"""Send one explicitly confirmed test signal to a configured DEMO broker."""

import argparse
import json
import os
import re
from datetime import datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from dotenv import load_dotenv

load_dotenv()

BROKERS = ("pocket_option", "quotex")
SIGNAL_TEMPLATE = """{asset} OTC
Expiration 5M
Entry at {entry_time}
{direction}
signal_provider="{signal_provider}"
timezone="{timezone}"
broker="{broker_id}"
"""


def fetch_json(base_url: str, path: str) -> dict:
    response = requests.get(f"{base_url.rstrip('/')}{path}", timeout=10)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError(f"Expected an object from {path}")
    return payload


def validate_demo_account(base_url: str, broker_id: str) -> tuple[dict, dict]:
    status_payload = fetch_json(base_url, "/broker_status")
    broker = next(
        (item for item in status_payload.get("brokers", []) if item.get("id") == broker_id),
        None,
    )
    if broker is None:
        raise RuntimeError(f"Server did not report broker {broker_id!r}")
    if broker.get("account_type") != "DEMO":
        raise RuntimeError(
            f"Refusing to send: {broker_id} is configured as "
            f"{broker.get('account_type')!r}, not DEMO."
        )
    if not broker.get("configured"):
        raise RuntimeError(f"{broker_id} has no configured session on the server.")

    risk_payload = fetch_json(base_url, f"/get_risk_management?broker_id={broker_id}")
    risk = risk_payload.get("risk_values")
    if not isinstance(risk, dict):
        raise ValueError("Server did not return effective broker risk settings.")
    return broker, risk


def default_entry_time(timezone: str) -> str:
    try:
        now = datetime.now(ZoneInfo(timezone))
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone: {timezone}") from exc
    return (now + timedelta(minutes=2)).strftime("%H:%M")


def format_signal(
    asset: str,
    direction: str,
    entry_time: str,
    timezone: str,
    broker_id: str,
    signal_provider: str,
) -> str:
    if not re.fullmatch(r"[A-Z]{3}/[A-Z]{3}", asset):
        raise ValueError("Asset must use the format EUR/USD.")
    if direction not in {"CALL", "PUT"}:
        raise ValueError("Direction must be CALL or PUT.")
    if not re.fullmatch(r"(?:[01]?\d|2[0-3]):[0-5]\d", entry_time):
        raise ValueError("Entry time must use 24-hour HH:MM format.")
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone: {timezone}") from exc
    return SIGNAL_TEMPLATE.format(
        asset=asset,
        direction=direction,
        entry_time=entry_time,
        timezone=timezone,
        broker_id=broker_id,
        signal_provider=signal_provider,
    ).strip()


def send_test_signal(
    base_url: str,
    broker_id: str,
    broker: dict,
    risk: dict,
    asset: str,
    direction: str,
    entry_time: str,
) -> None:
    timezone = str(risk.get("local_timezone", "Etc/GMT-2"))
    signal_provider = f"test-signal-{uuid4().hex[:8]}"
    signal_text = format_signal(
        asset, direction, entry_time, timezone, broker_id, signal_provider
    )

    initial_amount = float(risk["initial_amount"])
    levels = int(risk["martingale_levels"]) if risk["martingale_enabled"] else 0
    multiplier = float(risk["martingale_multiplier"]) if levels else 1.0
    exposure = sum(initial_amount * multiplier**level for level in range(levels + 1))

    print(f"Broker: {broker_id} ({broker['account_type']}), configured={broker['configured']}")
    print(f"Initial stake: {initial_amount:g}; martingale levels: {levels}; max sequence exposure: {exposure:g}")
    print("\nSignal that will be submitted:\n")
    print(signal_text)
    print(
        "\nThis webhook can place a real DEMO order and may automatically place "
        "martingale recovery orders according to the displayed settings."
    )
    confirmation = input(f'Type "PLACE DEMO TRADE" to continue: ').strip()
    if confirmation != "PLACE DEMO TRADE":
        print("Cancelled; no signal was sent.")
        return

    headers = {"Content-Type": "text/plain"}
    secret = os.getenv("WEBHOOK_SECRET", "").strip()
    if secret:
        headers["x-webhook-secret"] = secret

    response = requests.post(
        f"{base_url.rstrip('/')}/trade_signal",
        data=signal_text,
        headers=headers,
        timeout=15,
    )
    try:
        response_body = json.dumps(response.json(), indent=2)
    except requests.exceptions.JSONDecodeError:
        response_body = response.text
    print(f"HTTP {response.status_code}\n{response_body}")
    response.raise_for_status()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Send a test signal to a broker the server reports as DEMO."
    )
    parser.add_argument("--url", default="http://127.0.0.1:9634", help="Base URL of the running bot.")
    parser.add_argument("--broker", choices=BROKERS, required=True)
    parser.add_argument("--asset", default="EUR/USD", help="Pair in BASE/QUOTE format.")
    parser.add_argument("--direction", choices=("CALL", "PUT"), required=True)
    parser.add_argument(
        "--entry-time",
        help="24-hour HH:MM in the broker's configured local timezone (default: two minutes from now).",
    )
    args = parser.parse_args()

    try:
        broker, risk = validate_demo_account(args.url, args.broker)
        timezone = str(risk.get("local_timezone", "Etc/GMT-2"))
        entry_time = args.entry_time or default_entry_time(timezone)
        print(f"Verified configured account: {args.broker} {broker['account_type']}")
        send_test_signal(
            args.url,
            args.broker,
            broker,
            risk,
            args.asset.upper(),
            args.direction,
            entry_time,
        )
    except (requests.RequestException, RuntimeError, ValueError, KeyError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
