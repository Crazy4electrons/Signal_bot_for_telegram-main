"""Capture a Pocket Option session with a headed persistent Edge profile.

The first run requires manual login in the opened browser. This script does
not submit credentials or solve CAPTCHA challenges.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from playwright.async_api import async_playwright

load_dotenv()


def save_to_env(key: str, value: str) -> None:
    env_path = Path(__file__).with_name(".env")
    lines = env_path.read_text().splitlines(keepends=True) if env_path.exists() else []
    replacement = f"{key}='{value}'\n"
    for index, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[index] = replacement
            break
    else:
        lines.append(replacement)
    env_path.write_text("".join(lines))


def extract_auth(payload: str | bytes) -> tuple[str, str, str] | None:
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", errors="replace")
    payload = payload.strip()
    if not payload.startswith("42"):
        return None
    try:
        message = json.loads(payload[2:])
    except json.JSONDecodeError:
        return None
    if not isinstance(message, list) or len(message) < 2 or message[0] != "auth":
        return None
    details: Any = message[1]
    if isinstance(details, str):
        try:
            details = json.loads(details)
        except json.JSONDecodeError:
            return None
    if not isinstance(details, dict) or not all(key in details for key in ("session", "isDemo", "uid")):
        return None
    return str(details["session"]), str(details["isDemo"]), str(details["uid"])


async def collect(account_type: str, timeout_seconds: int) -> None:
    edge_binary = os.getenv("EDGE_BINARY") or shutil.which("microsoft-edge-stable") or shutil.which("microsoft-edge")
    if not edge_binary:
        raise RuntimeError("Microsoft Edge was not found; set EDGE_BINARY in .env")

    profile_path = Path(os.getenv("POCKETOPTION_PLAYWRIGHT_PROFILE", "data/pocketoption-playwright"))
    profile_path.mkdir(parents=True, exist_ok=True)
    found = asyncio.Event()
    expected_demo = "1" if account_type.upper() == "DEMO" else "0"

    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            str(profile_path),
            executable_path=edge_binary,
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            viewport=None,
        )

        async def handle_payload(payload: str) -> None:
            auth = extract_auth(payload)
            if not auth:
                return
            session, is_demo, uid = auth
            if is_demo != expected_demo:
                return
            escaped_session = session.replace('"', '\\"')
            save_to_env(
                "ssid",
                f'42["auth",{{"session":"{escaped_session}","isDemo":{is_demo},"uid":{uid},"platform":2,"isFastHistory":true,"isOptimized":true}}]',
            )
            save_to_env("UID", uid)
            save_to_env("ACCOUNT_TYPE", account_type.upper())
            print(f"Captured {account_type.upper()} Pocket Option session for UID {uid}")
            found.set()

        def handle_websocket(websocket: Any) -> None:
            websocket.on("framereceived", lambda payload: asyncio.create_task(handle_payload(payload)))
            websocket.on("framesent", lambda payload: asyncio.create_task(handle_payload(payload)))

        for page in context.pages:
            page.on("websocket", handle_websocket)
        context.on("page", lambda page: page.on("websocket", handle_websocket))

        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto("https://pocketoption.com/en/cabinet/", wait_until="domcontentloaded")
        print("Edge is open. Log in manually if needed; waiting for Pocket Option authentication...")
        try:
            await asyncio.wait_for(found.wait(), timeout=timeout_seconds)
        except asyncio.TimeoutError as exc:
            raise RuntimeError(
                "No matching auth frame received. Leave the logged-in cabinet page open and retry."
            ) from exc
        finally:
            await context.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--account-type", choices=("DEMO", "REAL"), default="DEMO")
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    asyncio.run(collect(args.account_type, args.timeout))


if __name__ == "__main__":
    main()
