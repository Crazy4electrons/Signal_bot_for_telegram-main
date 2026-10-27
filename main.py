import asyncio
import contextlib
import json
import re
from random import random
import pytz
from fastapi import FastAPI, Request, HTTPException, status
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import AsyncIterator, Any
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from parse_data import parse_macrodroid_trade_data
from telegram_listener import (
    ChannelConfig,
    TelegramSignalListener,
    channel_to_dict,
    channels_file,
    coerce_channel,
    read_channel_entries,
    write_channels,
)
from state_store import SignalBotStateStore

# Multi-broker abstraction
from brokers.router import SignalRouter
from brokers.base import NormalizedSignal
import os
import secrets
from pydantic import BaseModel, Field
from starlette.requests import ClientDisconnect
from starlette.responses import Response

from rich.logging import RichHandler
import logging
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from typing import Callable

load_dotenv()

class TimestampFormatter(logging.Formatter):
    def __init__(self, delegate: logging.Formatter):
        super().__init__()
        self.delegate = delegate

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.fromtimestamp(record.created).astimezone().strftime("%Y-%m-%d %H:%M:%S")
        return f"{timestamp} {self.delegate.format(record)}"


def configure_terminal_logging() -> None:
    logging.basicConfig(level="INFO", handlers=[RichHandler(show_time=False)], force=True)
    seen_handlers = set()
    for logger_name in ("", "uvicorn", "uvicorn.error", "uvicorn.access"):
        target_logger = logging.getLogger(logger_name)
        for handler in target_logger.handlers:
            if id(handler) in seen_handlers:
                continue
            seen_handlers.add(id(handler))
            current_formatter = handler.formatter or logging.Formatter("%(message)s")
            if not isinstance(current_formatter, TimestampFormatter):
                handler.setFormatter(TimestampFormatter(current_formatter))


configure_terminal_logging()
logger = logging.getLogger("PO_Signal")

class RISK_MANAGEMENT(BaseModel):
    initial_amount: float = Field(default=1, gt=0)
    martingale_levels: int = Field(default=3, ge=0)
    martingale_multiplier: float = Field(default=2, ge=1)
    drawback_threshold: int = -16
    timeframe: int = Field(default=300, gt=0)
    local_timezone: str = 'Etc/GMT-2'
    martingale_enabled: bool = True
    max_trade_amount: float = Field(default=16, gt=0)
    max_sequence_exposure: float = Field(default=31, gt=0)
    max_open_trades: int = Field(default=5, ge=1)
    max_open_trades_per_asset: int = Field(default=1, ge=0)
    max_open_trades_per_provider: int = Field(default=0, ge=0)
    stale_sequence_seconds: int = Field(default=0, ge=0)
    min_balance_reserve: float = Field(default=0, ge=0)


class BROKER_RISK_OVERRIDE(BaseModel):
    initial_amount: float | None = Field(default=None, gt=0)
    martingale_levels: int | None = Field(default=None, ge=0)
    martingale_multiplier: float | None = Field(default=None, ge=1)
    drawback_threshold: int | None = Field(default=None, le=0)
    timeframe: int | None = Field(default=None, gt=0)
    martingale_enabled: bool | None = None
    max_trade_amount: float | None = Field(default=None, gt=0)
    max_sequence_exposure: float | None = Field(default=None, gt=0)
    max_open_trades: int | None = Field(default=None, ge=1)
    max_open_trades_per_asset: int | None = Field(default=None, ge=0)
    max_open_trades_per_provider: int | None = Field(default=None, ge=0)
    stale_sequence_seconds: int | None = Field(default=None, ge=0)
    min_balance_reserve: float | None = Field(default=None, ge=0)


class TRADE_FIELDS( BaseModel):
    signal_provider: str
    asset:str
    direction:str
    entry_time:datetime
    level:int
    open_price:float
    amount:float
    broker_id: str
    signal_id: str | None = None
class TRADE(BaseModel):
    trade_id:str
    trade_details:TRADE_FIELDS
class SIGNAL_FIELDS(BaseModel):
    signal_provider: str
    asset:str
    direction:str
    entry_time:datetime
    broker_id: str | None = None
    channel_key: str | None = None
class SIGNAL(BaseModel):
    # map Tsid to signal details
    signal_id:str
    signal_details:SIGNAL_FIELDS

def risk_settings_file() -> Path:
    return Path(os.getenv("RISK_SETTINGS_FILE", "risk_settings.json"))


def load_risk_management() -> RISK_MANAGEMENT:
    values: dict[str, Any] = {}
    environment_types = {
        "initial_amount": float,
        "martingale_levels": int,
        "martingale_multiplier": float,
        "drawback_threshold": int,
        "timeframe": int,
        "local_timezone": str,
        "martingale_enabled": lambda value: value.lower() in {"1", "true", "yes"},
        "max_trade_amount": float,
        "max_sequence_exposure": float,
        "max_open_trades": int,
        "max_open_trades_per_asset": int,
        "max_open_trades_per_provider": int,
        "stale_sequence_seconds": int,
        "min_balance_reserve": float,
    }
    for name, converter in environment_types.items():
        value = os.getenv(name.upper())
        if value is not None and value != "":
            values[name] = converter(value)
    return RISK_MANAGEMENT(**values)


risk_management:RISK_MANAGEMENT = load_risk_management()
risk_overrides: dict[str, dict[str, Any]] = {}
broker_pnl_today: dict[str, float] = {}
broker_lifespan: dict[str, float] = {}
broker_balances: dict[str, float] = {}


def load_risk_settings() -> None:
    """Load persisted global risk values and optional per-broker overrides."""
    global risk_management, risk_overrides
    path = risk_settings_file()
    if not path.is_file():
        return
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(settings, dict):
            raise ValueError("risk settings must be a JSON object")
        global_values = settings.get("global")
        overrides = settings.get("broker_overrides", {})
        if not isinstance(overrides, dict):
            raise ValueError("risk settings must contain an object of broker_overrides")
        unknown_brokers = set(overrides) - {"pocket_option", "quotex"}
        if unknown_brokers:
            raise ValueError(
                f"risk settings contain unsupported broker overrides: "
                f"{', '.join(sorted(unknown_brokers))}"
            )
        if global_values is not None:
            risk_management = RISK_MANAGEMENT.model_validate(global_values)
        risk_overrides = {
            broker_id: BROKER_RISK_OVERRIDE.model_validate(values)
            .model_dump(exclude_none=True)
            for broker_id, values in overrides.items()
        }
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise RuntimeError(f"Could not load risk settings from {path}: {exc}") from exc


def persist_risk_settings(
    global_values: RISK_MANAGEMENT | None = None,
    overrides: dict[str, dict[str, Any]] | None = None,
) -> None:
    path = risk_settings_file()
    temporary_path = path.with_name(f".{path.name}.tmp")
    payload = {
        "global": (global_values or risk_management).model_dump(),
        "broker_overrides": overrides if overrides is not None else risk_overrides,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        temporary_path.replace(path)
    except OSError:
        temporary_path.unlink(missing_ok=True)
        raise


load_risk_settings()
Signals:dict = {}
trade_details:dict = {}
closed_trades:dict = {}
runtime_state: SignalBotStateStore | None = None
runtime_state_loaded = False
resumed_trade_tasks: dict[str, asyncio.Task] = {}
telegram_listener: TelegramSignalListener | None = None
telegram_listener_task: asyncio.Task | None = None

broker_connected = False

# Multi-broker state. We keep Pocket behavior intact while we migrate.
broker_registry: dict[str, Any] = {}
broker_tasks: dict[str, asyncio.Task] = {}
connected_brokers: set[str] = set()
broker_connection_locks: dict[str, asyncio.Lock] = {}
signal_router: SignalRouter | None = None
BROKER_NAMES = {
    "pocket_option": "Pocket Option",
    "quotex": "Quotex",
}


def get_runtime_state() -> SignalBotStateStore:
    global runtime_state
    if runtime_state is None:
        runtime_state = SignalBotStateStore(
            os.getenv("SIGNAL_BOT_STATE_PATH", "signal_bot.sqlite3")
        )
    return runtime_state


def local_trading_date() -> date:
    return datetime.now(pytz.timezone(str(risk_management.local_timezone))).date()


def restore_runtime_state() -> None:
    global runtime_state_loaded
    if runtime_state_loaded:
        return
    store = get_runtime_state()
    interrupted = store.interrupt_unfinished_signals()
    Signals.clear()
    trade_details.clear()
    closed_trades.clear()
    for entry in store.load_active_trades():
        if entry["state"] != "monitoring":
            logger.warning(
                "Persisted trade %s is in %s state and requires manual review; it will not be replayed.",
                entry["trade_id"],
                entry["state"],
            )
            continue
        trade_details[entry["trade_id"]] = TRADE_FIELDS.model_validate(
            entry["trade_details"]
        )
    closed_trades.update(store.load_closed_trades())
    daily, lifetime = store.pnl_totals(local_trading_date())
    broker_pnl_today.clear()
    broker_pnl_today.update(daily)
    broker_lifespan.clear()
    broker_lifespan.update(lifetime)
    runtime_state_loaded = True
    if any(interrupted.values()):
        logger.warning(
            "Restart recovery: %s pending signal(s) interrupted, %s ambiguous signal(s), "
            "%s ambiguous trade(s) marked for review.",
            interrupted["interrupted"],
            interrupted["needs_review"],
            interrupted["ambiguous_trades"],
        )
    logger.info(
        "Restored runtime state: %s confirmed active trade(s), %s closed trade(s).",
        len(trade_details),
        len(closed_trades),
    )


def resume_active_trades(broker_id: str | None = None) -> None:
    for trade_id, details in list(trade_details.items()):
        if broker_id is not None and details.broker_id != broker_id:
            continue
        existing_task = resumed_trade_tasks.get(trade_id)
        if existing_task is not None and not existing_task.done():
            continue
        if not broker_is_connected(details.broker_id):
            continue
        trade = TRADE(trade_id=trade_id, trade_details=details)
        task = asyncio.create_task(
            manage_martingale(trade),
            name=f"resume-trade-{trade_id}",
        )
        resumed_trade_tasks[trade_id] = task

        def log_task_failure(done_task: asyncio.Task, current_id: str = trade_id) -> None:
            if resumed_trade_tasks.get(current_id) is done_task:
                resumed_trade_tasks.pop(current_id, None)
            try:
                done_task.result()
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("Recovered trade monitor failed for trade %s", current_id)

        task.add_done_callback(log_task_failure)


def normalise_broker_id(broker_id: str) -> str:
    """Return the canonical ID for a supported broker."""
    broker_id = broker_id.strip().lower().replace("-", "_")
    if broker_id not in BROKER_NAMES:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unsupported broker: {broker_id}",
        )
    return broker_id


def effective_risk_management(broker_id: str) -> RISK_MANAGEMENT:
    values = risk_management.model_dump()
    values.update(risk_overrides.get(broker_id, {}))
    return RISK_MANAGEMENT.model_validate(values)


def broker_pnl(broker_id: str) -> float:
    return float(broker_pnl_today.get(broker_id, 0.0))


def broker_lifetime_pnl(broker_id: str) -> float:
    return float(broker_lifespan.get(broker_id, 0.0))


def record_broker_pnl(
    broker_id: str, amount: float, event_id: str | None = None
) -> None:
    event_id = event_id or secrets.token_hex(16)
    if not get_runtime_state().record_pnl_event(
        event_id, broker_id, local_trading_date(), amount
    ):
        return
    broker_pnl_today[broker_id] = broker_pnl(broker_id) + amount
    broker_lifespan[broker_id] = broker_lifetime_pnl(broker_id) + amount


def finalize_trade(
    trade_id: str,
    payload: dict[str, Any],
    *,
    signal_id: str | None = None,
    signal_status: str = "completed",
) -> None:
    get_runtime_state().finish_trade(
        trade_id,
        payload,
        signal_id=signal_id,
        signal_status=signal_status,
    )
    closed_trades[trade_id] = payload
    trade_details.pop(trade_id, None)


def trade_history_payload(
    details: TRADE_FIELDS,
    result: str,
    from_server: dict[str, Any],
    *,
    amount: float | None = None,
    level: int | None = None,
) -> dict[str, Any]:
    return {
        "trade_details": {
            "direction": details.direction,
            "asset": details.asset,
            "amount": details.amount if amount is None else amount,
            "level": details.level if level is None else level,
            "signal_provider": details.signal_provider,
            "result": result,
            "entry_time": details.entry_time.strftime("%Y-%m-%d %H:%M:%S"),
            "open_price": details.open_price,
        },
        "broker_id": details.broker_id,
        "from_server": from_server,
    }


def broker_account_type(broker_id: str) -> str:
    if broker_id == "quotex":
        if os.getenv("QUOTEX_IS_DEMO") is not None:
            return "DEMO" if env_flag("QUOTEX_IS_DEMO") else "REAL"
        return os.getenv("QUOTEX_ACCOUNT_TYPE", "DEMO").strip().upper()
    return os.getenv("POCKETOPTION_ACCOUNT_TYPE", "DEMO").strip().upper()


def broker_is_connected(broker_id: str) -> bool:
    if broker_id not in connected_brokers:
        return False
    adapter = broker_registry.get(broker_id)
    adapter_connected = getattr(adapter, "is_connected", None) if adapter is not None else None
    return bool(adapter_connected) if adapter_connected is not None else True


def broker_status_payload() -> dict[str, Any]:
    """Expose connection readiness without returning session or credential data."""
    brokers = []
    for broker_id, name in BROKER_NAMES.items():
        prefix = "QUOTEX" if broker_id == "quotex" else "POCKETOPTION"
        configured = bool(os.getenv(f"{prefix}_SSID"))
        if broker_id == "quotex":
            configured = configured or env_flag("QUOTEX_BROWSER_SESSION", True)
        brokers.append({
            "id": broker_id,
            "name": name,
            "account_type": broker_account_type(broker_id),
            "configured": configured,
            "connected": broker_is_connected(broker_id),
        })
    return {"brokers": brokers}


def broker_routes_for_channels(entries: list[dict[str, Any]]) -> dict[str, str]:
    routes: dict[str, str] = {}
    for entry in entries:
        broker_id = entry.get("broker")
        channel = entry.get("channel")
        if broker_id in BROKER_NAMES and channel:
            routes[str(channel)] = broker_id
            if entry.get("name"):
                routes[str(entry["name"])] = broker_id
    return routes


def route_signal_to_broker(signal_data: SIGNAL_FIELDS) -> str:
    try:
        if signal_router is None:
            if not signal_data.broker_id:
                raise ValueError("Signal has no broker destination.")
            return normalise_broker_id(signal_data.broker_id)
        decision = signal_router.decide(
            NormalizedSignal(
                signal_id=signal_data.signal_provider,
                signal_provider=signal_data.signal_provider,
                asset=signal_data.asset,
                direction=signal_data.direction,
                entry_time=signal_data.entry_time,
                broker_hint=signal_data.broker_id,
            ),
            channel_key=signal_data.channel_key,
        )
        return normalise_broker_id(decision.broker_ids[0])
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Signal has no broker destination; configure its Telegram channel "
                "or include broker metadata."
            ),
        ) from exc


async def get_or_create_pocket_broker() -> Any:
    ssid = os.getenv("POCKETOPTION_SSID")
    if not ssid:
        raise RuntimeError("POCKETOPTION_SSID is not configured")
    broker = broker_registry.get("pocket_option")
    if broker is not None:
        return broker

    # Adapter used by router + future multi-broker.
    from brokers.pocket_option import PocketOptionBroker

    broker = PocketOptionBroker(broker_id="pocket_option", ssid=ssid)
    broker_registry["pocket_option"] = broker
    return broker


async def get_or_create_broker(broker_id: str) -> Any:
    if broker_id == "pocket_option":
        return await get_or_create_pocket_broker()

    broker = broker_registry.get(broker_id)
    if broker is not None:
        return broker

    if broker_id == "quotex":
        from brokers.quotex import QuotexBroker

        ssid = os.getenv("QUOTEX_SSID")
        default_demo = os.getenv("QUOTEX_ACCOUNT_TYPE", "DEMO").strip().upper() != "REAL"
        is_demo_raw = os.getenv("QUOTEX_IS_DEMO", str(default_demo)).lower().strip()
        is_demo = is_demo_raw in {"1", "true", "yes", "y"}
        regions_raw = os.getenv("QUOTEX_REGIONS", "").strip()
        regions = [r.strip() for r in regions_raw.split(",") if r.strip()] or None
        browser_session = os.getenv("QUOTEX_BROWSER_SESSION", "true").strip().lower() in {
            "1", "true", "yes", "on",
        }
        browser_headless = os.getenv("QUOTEX_BROWSER_HEADLESS", "false").strip().lower() in {
            "1", "true", "yes", "on",
        }
        browser_auth_timeout = int(os.getenv("QUOTEX_BROWSER_AUTH_TIMEOUT", "120"))

        broker = QuotexBroker(
            ssid=ssid or None,
            is_demo=is_demo,
            regions=regions,
            use_browser_session=browser_session,
            browser_headless=browser_headless,
            browser_auth_timeout=browser_auth_timeout,
        )
        broker_registry[broker_id] = broker
        return broker

    raise RuntimeError(f"Unsupported broker_id: {broker_id}")


async def ensure_broker_connected(broker_id: str) -> Any:
    global broker_connected
    broker = await get_or_create_broker(broker_id)
    lock = broker_connection_locks.setdefault(broker_id, asyncio.Lock())
    async with lock:
        if not broker_is_connected(broker_id):
            if broker_id in connected_brokers:
                logger.warning("Detected disconnected broker=%s; reconnecting adapter", broker_id)
                connected_brokers.discard(broker_id)
                broker_connected = any(broker_is_connected(candidate) for candidate in BROKER_NAMES)
                await broker.disconnect()
            if not await broker.connect():
                raise RuntimeError(f"Broker connection failed: {broker_id}")
            connected_brokers.add(broker_id)
    broker_connected = any(broker_is_connected(candidate) for candidate in BROKER_NAMES)
    return broker


async def ensure_broker_session(broker_id: str) -> None:
    """Leave Quotex authorization to the fork's native saved-session login flow."""
    if broker_id == "quotex":
        return

    prefix_by_broker = {
        "pocket_option": "POCKETOPTION",
        "quotex": "QUOTEX",
    }
    prefix = prefix_by_broker.get(broker_id)
    if prefix is None:
        raise RuntimeError(f"Unsupported broker_id: {broker_id}")
    if os.getenv(f"{prefix}_SSID"):
        return

    email = os.getenv(f"{prefix}_EMAIL")
    password = os.getenv(f"{prefix}_PASSWORD")
    if not email or not password:
        raise RuntimeError(
            f"{prefix}_SSID is missing; configure {prefix}_EMAIL and {prefix}_PASSWORD "
            "or run playwright_scraper.py interactively first."
        )

    from playwright_scraper import capture_session

    account_type = os.getenv(f"{prefix}_ACCOUNT_TYPE", "DEMO").upper()
    if broker_id == "quotex" and os.getenv("QUOTEX_IS_DEMO") is not None:
        account_type = "DEMO" if env_flag("QUOTEX_IS_DEMO") else "REAL"
    headless = env_flag(f"{prefix}_SCRAPER_HEADLESS", True)
    timeout_seconds = int(os.getenv(f"{prefix}_SCRAPER_TIMEOUT", "120"))
    await capture_session(
        broker_id,
        account_type,
        email=email,
        password=password,
        headless=headless,
        timeout_seconds=timeout_seconds,
    )

# Shared secret guarding POST /trade_signal. Required whenever the endpoint is
# reachable from the public internet (tunnel, ngrok, or a relay).
def load_webhook_secrets() -> list[str]:
    """Return accepted webhook secrets: current, plus an optional previous one.

    Supporting a previous secret allows rotating without a window of rejected
    signals while the sender (MacroDroid) is updated.
    """
    raw = os.getenv("WEBHOOK_SECRET", "")
    accepted = [candidate.strip() for candidate in raw.split(",") if candidate.strip()]
    previous = os.getenv("WEBHOOK_SECRET_PREVIOUS", "").strip()
    if previous and previous not in accepted:
        accepted.append(previous)
    return accepted


webhook_secrets = load_webhook_secrets()
webhook_stats: dict[str, Any] = {
    "last_received_at": None,
    "last_result": "never",
    "accepted": 0,
    "rejected": 0,
    "unauthorized": 0,
}


def env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_file_path() -> Path:
    """Location of the .env file to update (override with ENV_FILE)."""
    override = os.getenv("ENV_FILE", "").strip()
    return Path(override) if override else Path(__file__).with_name(".env")


def persist_env_value(key: str, value: str) -> None:
    """Atomically set ``KEY=value`` in .env, preserving every other line."""
    path = env_file_path()
    lines = path.read_text().splitlines(keepends=True) if path.exists() else []
    replacement = f"{key}={value}\n"
    for index, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[index] = replacement
            break
    else:
        lines.append(replacement)
    temp_path = path.with_name(f"{path.name}.tmp")
    temp_path.write_text("".join(lines))
    temp_path.chmod(0o600)
    temp_path.replace(path)


def ensure_webhook_secret() -> None:
    """Generate and persist a secret once, if enabled and none is configured.

    This deliberately runs only when no secret exists. Rotating on every start
    would invalidate the value stored in MacroDroid and reject every signal
    until the phone is updated by hand.
    """
    global webhook_secrets
    if webhook_secrets:
        return
    if not env_flag("WEBHOOK_SECRET_AUTOGENERATE", False):
        logger.warning(
            "WEBHOOK_SECRET is not set and WEBHOOK_SECRET_AUTOGENERATE is disabled; "
            "/trade_signal will accept unauthenticated requests."
        )
        return

    generated = secrets.token_urlsafe(32)
    persist_env_value("WEBHOOK_SECRET", generated)
    os.environ["WEBHOOK_SECRET"] = generated
    webhook_secrets = [generated]
    logger.warning(
        "Generated a new WEBHOOK_SECRET and saved it to .env (mode 0600). "
        "Copy this exact value into the MacroDroid variable and send it as the "
        "header x-webhook-secret: %s",
        generated,
    )


def webhook_auth_ok(request: Request) -> bool:
    """Validate the shared secret when one is configured.

    Fails closed when a secret is set. When no secret is configured the request
    is allowed so local development keeps working, but a warning is logged and
    /health reports webhook_auth_configured=false.
    """
    if not webhook_secrets:
        logger.warning(
            "WEBHOOK_SECRET is not set; /trade_signal is accepting unauthenticated "
            "requests. Set WEBHOOK_SECRET in .env before exposing this endpoint."
        )
        return True

    provided = request.headers.get("x-webhook-secret", "")
    if not provided:
        authorization = request.headers.get("authorization", "")
        if authorization.lower().startswith("bearer "):
            provided = authorization[7:].strip()
    return any(secrets.compare_digest(provided, accepted) for accepted in webhook_secrets)


def martingale_exposure(initial_amount: float, multiplier: float, levels: int) -> float:
    """Return the maximum amount risked across the initial trade and recoveries."""
    return sum(initial_amount * multiplier**level for level in range(levels + 1))


def to_float(value: Any, default: float = 0.0) -> float:
    """Coerce a broker response field to float.

    Pocket Option returns numeric fields such as ``profit``, ``amount`` and
    ``balance`` as strings (for example ``'0.92'``), so arithmetic must never
    assume they are already numeric.
    """
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        logger.warning("Could not convert broker value %r to float; using %s", value, default)
        return default


def risk_rejection(reason: str) -> None:
    logger.warning("Trade rejected by risk controls: %s", reason)


def active_sequences(
    exclude_trade_id: str | None = None,
    broker_id: str | None = None,
) -> list[TRADE_FIELDS]:
    """Currently open sequences, excluding one trade id when needed.

    A recovery leg must exclude its own parent, otherwise the parent would count
    against the per-asset limit and block its own martingale step.
    """
    return [
        details
        for trade_id, details in list(trade_details.items())
        if trade_id != exclude_trade_id
        and (broker_id is None or details.broker_id == broker_id)
    ]


def sequence_max_age_seconds(risk: RISK_MANAGEMENT) -> int:
    """Age after which a sequence is assumed dead and its slot reclaimed."""
    if risk.stale_sequence_seconds > 0:
        return risk.stale_sequence_seconds
    legs = risk.martingale_levels + 1 if risk.martingale_enabled else 1
    # Every leg can run for one timeframe, with slack for settlement lookups.
    return risk.timeframe * legs * 2 + 120


def purge_stale_sequences() -> int:
    """Drop sequences that outlived their expected duration.

    Without this, a result lookup that never returns would hold a slot against
    the open-trade limits indefinitely and silently block every later signal.
    """
    local_tz = pytz.timezone(str(risk_management.local_timezone))
    now = datetime.now(local_tz)
    removed = 0
    for trade_id, details in list(trade_details.items()):
        risk = effective_risk_management(details.broker_id)
        max_age = sequence_max_age_seconds(risk)
        entry_time = details.entry_time
        if entry_time.tzinfo is None:
            entry_time = local_tz.localize(entry_time)
        age = (now - entry_time).total_seconds()
        if age > max_age:
            logger.warning(
                "Reclaiming stale sequence %s (%s %s %s): age %.0fs exceeds %ss",
                trade_id, details.signal_provider, details.asset, details.direction, age, max_age,
            )
            trade_details.pop(trade_id, None)
            removed += 1
    return removed


def can_start_trade(
    amount: float,
    provider: str | None = None,
    asset: str | None = None,
    exclude_trade_id: str | None = None,
    is_recovery: bool = False,
    broker_id: str | None = None,
    risk: RISK_MANAGEMENT | None = None,
    balance: float | None = None,
) -> tuple[bool, str]:
    """Check the per-order limits before an order is sent to the broker.

    ``is_recovery`` marks a martingale leg that recovers an already-open
    sequence. Such a leg is exempt from ``min_balance_reserve``: the parent's
    loss is already booked, so refusing the recovery only locks the loss in and
    abandons the sequence. It still has to be affordable, so the stake may never
    exceed the current balance.
    """
    risk = risk or risk_management
    if balance is None:
        balance = broker_balances.get(broker_id) if broker_id else None
    if not risk.martingale_enabled and amount != risk.initial_amount:
        return False, "martingale is disabled"
    if amount > risk.max_trade_amount:
        return False, f"trade amount {amount} exceeds max_trade_amount"

    open_sequences = active_sequences(exclude_trade_id, broker_id)
    if len(open_sequences) >= risk.max_open_trades:
        summary = ", ".join(
            f"{d.signal_provider}/{d.asset}/{d.direction}" for d in open_sequences
        )
        return False, (
            f"maximum open trades reached ({len(open_sequences)}/{risk.max_open_trades}): {summary}"
        )

    if asset and risk.max_open_trades_per_asset:
        same_asset = [d for d in open_sequences if d.asset == asset]
        if len(same_asset) >= risk.max_open_trades_per_asset:
            return False, (
                f"already {len(same_asset)} open trade(s) on {asset} "
                f"(max_open_trades_per_asset={risk.max_open_trades_per_asset})"
            )

    if provider and risk.max_open_trades_per_provider:
        same_provider = [d for d in open_sequences if d.signal_provider == provider]
        if len(same_provider) >= risk.max_open_trades_per_provider:
            return False, (
                f"already {len(same_provider)} open trade(s) from provider {provider} "
                f"(max_open_trades_per_provider={risk.max_open_trades_per_provider})"
            )

    if is_recovery:
        if balance is not None and amount > balance:
            return False, (
                f"recovery stake {amount} exceeds available balance {balance}"
            )
        if balance is not None and (
            balance - amount < risk.min_balance_reserve
        ):
            logger.info(
                "Recovery stake $%s would leave $%.2f, below the $%s reserve; "
                "placing it anyway so the sequence can recover.",
                amount,
                balance - amount,
                risk.min_balance_reserve,
            )
        return True, ""

    if balance is not None and balance - amount < risk.min_balance_reserve:
        return False, "minimum balance reserve would be breached"
    return True, ""


def can_start_sequence(
    provider: str | None = None,
    asset: str | None = None,
    *,
    broker_id: str,
    risk: RISK_MANAGEMENT,
) -> tuple[bool, str]:
    purge_stale_sequences()
    exposure = martingale_exposure(
        risk.initial_amount,
        risk.martingale_multiplier if risk.martingale_enabled else 1,
        risk.martingale_levels if risk.martingale_enabled else 0,
    )
    if exposure > risk.max_sequence_exposure:
        return False, f"worst-case sequence exposure {exposure} exceeds max_sequence_exposure"
    return can_start_trade(
        risk.initial_amount,
        provider,
        asset,
        broker_id=broker_id,
        risk=risk,
    )


def normalize_trade_result(value: Any) -> str:
    """Normalize broker result labels without treating missing data as a win."""
    result = str(value or "").strip().upper()
    if result in {"WIN", "WON"}:
        return "WIN"
    if result in {"LOSS", "LOST"}:
        return "LOSS"
    if result in {"DRAW", "TIE", "EQUAL"}:
        return "DRAW"
    return "UNKNOWN"


async def handle_telegram_signal(
    text: str, source_id: str, channel: ChannelConfig | None = None
) -> None:
    """Route a new Telegram post through the same path as MacroDroid.

    ``channel`` carries the per-channel defaults configured in channels.json so
    each source can use its own timezone and provider label.
    """
    provider = (channel.provider if channel else None) or os.getenv(
        "TELEGRAM_SIGNAL_PROVIDER", "telegram"
    )
    timezone = (channel.timezone if channel else None) or os.getenv(
        "SIGNAL_TIMEZONE", risk_management.local_timezone
    )
    enriched_text = text
    if "signal_provider" not in text.lower():
        enriched_text += f'\nsignal_provider="{provider}"'
    if "timezone" not in text.lower():
        enriched_text += f'\ntimezone="{timezone}"'

    signal = await parse_signal(enriched_text, channel_config=channel)
    if signal is not None:
        logger.info("Accepted Telegram signal %s from %s", signal.signal_id, source_id)
        asyncio.create_task(take_trade(signal))
    else:
        logger.warning("Rejected Telegram signal from %s", source_id)


async def restart_telegram_listener() -> tuple[bool, str]:
    """Rebuild the Telegram listener so channel edits apply without a restart.

    Returns ``(reloaded, message)``. A failure here does not undo the file
    write: the saved list is still what the next start will read.
    """
    global telegram_listener, telegram_listener_task

    if telegram_listener is not None:
        try:
            await telegram_listener.stop()
        except Exception as exc:
            logger.warning("Stopping the Telegram listener failed: %s", exc)
    if telegram_listener_task is not None:
        telegram_listener_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await telegram_listener_task
    telegram_listener = None
    telegram_listener_task = None

    try:
        telegram_listener = TelegramSignalListener.from_environment(handle_telegram_signal)
    except Exception as exc:
        logger.error("Could not rebuild the Telegram listener: %s", exc, exc_info=True)
        return False, str(exc)
    if telegram_listener is None:
        return True, "Telegram listener is disabled in .env"

    telegram_listener_task = asyncio.create_task(telegram_listener.run())
    logger.info(
        "Telegram listener reloaded with %d channel(s)", len(telegram_listener.channels)
    )
    return True, f"Listening to {len(telegram_listener.channels)} channel(s)"


def timezone_choices() -> list[dict[str, str]]:
    """The Etc/GMT offsets the signal parser can handle, in human order."""
    choices: dict[int, dict[str, str]] = {}
    for name in pytz.all_timezones:
        match = re.fullmatch(r"Etc/GMT([+-])(\d{1,2})", name)
        if not match:
            continue
        hours = int(match.group(2))
        offset = hours if match.group(1) == "-" else -hours
        choices.setdefault(offset, {"value": name, "label": f"GMT{offset:+d} ({name})"})
    return [choices[offset] for offset in sorted(choices)]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global risk_management,telegram_listener,telegram_listener_task,broker_connected,signal_router
    ensure_webhook_secret()
    connected_brokers.clear()
    broker_connected = False
    restore_runtime_state()
    logger.info("FastAPI lifespan startup event: Initializing broker adapters.")
    # Risk settings come from the environment (see load_risk_management) or from
    # POST /set_risk_management. Startup must never block on stdin, otherwise a
    # service manager restart would hang forever.
    logger.info(
        "Active risk configuration: %s",
        risk_management.model_dump(),
    )
    if not risk_management.martingale_enabled:
        logger.warning("Martingale recovery is disabled; losing trades will not be recovered.")

    # Router maps each explicitly configured Telegram channel to its broker.
    broker_for_channel: dict[str, str] = {}
    try:
        entries = read_channel_entries(str(channels_file()))
        broker_for_channel = broker_routes_for_channels(entries)
    except Exception:
        broker_for_channel = {}

    signal_router = SignalRouter(broker_for_channel=broker_for_channel)

    async def connect_startup_broker(broker_id: str) -> None:
        try:
            await ensure_broker_session(broker_id)
            broker_adapter = await ensure_broker_connected(broker_id)
            broker_balances[broker_id] = to_float(await broker_adapter.balance())
            logger.info("Connected broker=%s for independent account and trade handling", broker_id)
            resume_active_trades(broker_id)
        except Exception as exc:
            logger.error("Could not initialize %s; other brokers remain available: %s", broker_id, exc)

    await asyncio.gather(*(connect_startup_broker(broker_id) for broker_id in BROKER_NAMES))
    broker_connected = any(broker_is_connected(candidate) for candidate in BROKER_NAMES)
    asyncio.create_task(reset_P_n_L_day()) 
    telegram_listener = TelegramSignalListener.from_environment(handle_telegram_signal)
    if telegram_listener:
        telegram_listener_task = asyncio.create_task(telegram_listener.run())
    yield
    # Disconnect
    if telegram_listener:
        await telegram_listener.stop()
    if telegram_listener_task:
        telegram_listener_task.cancel()
    # Disconnect adapters
    for b in broker_registry.values():
        with contextlib.suppress(Exception):
            await b.disconnect()
    connected_brokers.clear()
    broker_connected = False
    return

app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # allow all origins including 'null'
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class NoCacheUiMiddleware(BaseHTTPMiddleware):
    """Keep dashboard assets fresh so UI fixes appear without a hard reload."""

    async def dispatch(self, request: Request, call_next: Callable):
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/ui"):
            response.headers["Cache-Control"] = "no-store, max-age=0"
        return response


app.add_middleware(NoCacheUiMiddleware)


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "status": "ok",
            "broker_connected": any(broker_is_connected(candidate) for candidate in BROKER_NAMES),
            "telegram_listener_enabled": telegram_listener is not None,
            "telegram_listener_connected": bool(
                telegram_listener and telegram_listener.client and telegram_listener.client.is_connected()
            ),
            "telegram_channels": telegram_listener.status() if telegram_listener else [],
            "telegram_channel_problems": (
                telegram_listener.channel_problems if telegram_listener else []
            ),
            "webhook_auth_configured": bool(webhook_secrets),
            "webhook": webhook_stats,
        },
    )

@app.get("/broker_status", response_class=JSONResponse)
async def get_broker_status() -> JSONResponse:
    """Return independent broker readiness without exposing secrets."""
    return JSONResponse(status_code=status.HTTP_200_OK, content=broker_status_payload())


@app.post("/brokers/{broker_id}/connect", response_class=JSONResponse)
async def connect_broker(broker_id: str) -> JSONResponse:
    """Connect a configured broker for account reads; this endpoint never places orders."""
    broker_id = normalise_broker_id(broker_id)
    try:
        broker = await ensure_broker_connected(broker_id)
        balance = to_float(await broker.balance())
    except Exception as exc:
        logger.error("Could not connect %s from dashboard: %s", broker_id, exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Could not connect to {BROKER_NAMES[broker_id]}: {exc}",
        ) from exc
    broker_balances[broker_id] = balance
    resume_active_trades(broker_id)
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "broker_id": broker_id,
            "account_type": broker_account_type(broker_id),
            "balance": balance,
            "connected": True,
        },
    )

# Enable CORS so browser pages served from file:// (origin 'null') or other origins can reach the API.
# For local development it's fine to allow all origins; tighten this in production.

app.mount("/ui", StaticFiles(directory="ui", html=True), name="ui")
@app.get("/", response_class=HTMLResponse)
async def root_index():
    ui_dir = os.path.join(os.path.dirname(__file__), "ui")
    index_path = os.path.join(ui_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path, media_type="text/html")
    return HTMLResponse("<html><body><h1>Signal Bot</h1><p>UI not found. Visit /ui/</p></body></html>", status_code=200)

@app.get("/ui/script.js")
async def ui_script():
    ui_dir = os.path.join(os.path.dirname(__file__), "ui")
    script_path = os.path.join(ui_dir, "script.js")
    if os.path.exists(script_path):
        return FileResponse(script_path, media_type="application/javascript")
    raise HTTPException(status_code=404, detail="script.js not found")

@app.get("/ui/styles.css")
async def ui_styles():
    ui_dir = os.path.join(os.path.dirname(__file__), "ui")
    css_path = os.path.join(ui_dir, "styles.css")
    if os.path.exists(css_path):
        return FileResponse(css_path, media_type="text/css")
    raise HTTPException(status_code=404, detail="styles.css not found")

@app.get("/account_details", response_class=JSONResponse)
async def get_account_details(broker_id: str):
    broker_id = normalise_broker_id(broker_id)
    try:
        broker = await ensure_broker_connected(broker_id)
        balance = to_float(await broker.balance())
        broker_balances[broker_id] = balance
    except Exception as exc:
        logger.error("Could not read %s account details: %s", broker_id, exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Could not read {BROKER_NAMES[broker_id]} account details: {exc}",
        ) from exc
    P_n_L_day = broker_pnl(broker_id)
    lifespan = broker_lifetime_pnl(broker_id)
    jsonResponse = JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "broker_id": broker_id,
            "broker_name": BROKER_NAMES[broker_id],
            "account_type": broker_account_type(broker_id),
            "balance": balance,
            "P_n_L_day": P_n_L_day,
            "lifespan": lifespan,
        },
    )
    return jsonResponse

@app.get("/open_trades", response_class=JSONResponse)
async def get_open_trades(broker_id: str):
    broker_id = normalise_broker_id(broker_id)
    trades_list = [
        {
            "trade_id": trade_id,
            "broker_id": details.broker_id,
            "asset": details.asset,
            "amount": details.amount,
            "direction": details.direction,
            "profit": None,
            "openedTime": details.entry_time.isoformat(),
            "open_price": details.open_price,
            "current_price": None,
        }
        for trade_id, details in trade_details.items()
        if details.broker_id == broker_id
    ]
    return JSONResponse(status_code=status.HTTP_200_OK, content={"open_trades": trades_list})

@app.get("/closed_trades")
async def get_closed_trades(broker_id: str):
    broker_id = normalise_broker_id(broker_id)
    matching = [
        (trade_id, details)
        for trade_id, details in closed_trades.items()
        if details.get("broker_id") == broker_id
    ]
    trades = dict(matching)
    return JSONResponse(status_code=status.HTTP_200_OK, content={"closed_trades": trades})

@app.get("/current_signals", response_class=JSONResponse)
async def get_current_signals(broker_id: str):
    broker_id = normalise_broker_id(broker_id)
    signal_list = []
    for signal in get_runtime_state().recent_signals(limit=50):
        if signal.get("broker_id") != broker_id:
            continue
        signal_list.append(signal)
        if len(signal_list) == 20:
            break
        
    return JSONResponse(status_code=status.HTTP_200_OK, content={"signals": signal_list})


    #
    # return JSONResponse(status_code= status.HTTP_200_OK,content={f"message:{Signals.get_signal(returnAll=True)}"})
@app.post("/set_risk_management", response_class=JSONResponse)
async def set_risk_management(Risk: RISK_MANAGEMENT):
    global risk_management
    logger.info(f"Received risk management settings: {Risk}")
    try:
        persist_risk_settings(global_values=Risk)
    except OSError as exc:
        logger.error("Could not persist global risk settings: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not save global risk settings.",
        ) from exc
    risk_management = Risk
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"message": "Risk management values updated", "risk_values": risk_management.model_dump()},
    )


@app.put("/risk_management/brokers/{broker_id}", response_class=JSONResponse)
async def set_broker_risk_override(
    broker_id: str,
    override: BROKER_RISK_OVERRIDE,
) -> JSONResponse:
    broker_id = normalise_broker_id(broker_id)
    candidate = dict(risk_overrides)
    values = override.model_dump(exclude_none=True)
    if values:
        candidate[broker_id] = values
    else:
        candidate.pop(broker_id, None)
    try:
        persist_risk_settings(overrides=candidate)
    except OSError as exc:
        logger.error("Could not persist %s risk overrides: %s", broker_id, exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not save {BROKER_NAMES[broker_id]} risk overrides.",
        ) from exc
    risk_overrides.clear()
    risk_overrides.update(candidate)
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "message": f"{BROKER_NAMES[broker_id]} risk overrides saved",
            "broker_id": broker_id,
            "overrides": values,
            "effective_risk_values": effective_risk_management(broker_id).model_dump(),
        },
    )


@app.get("/get_risk_management", response_class=JSONResponse)
async def get_risk_management(broker_id: str | None = None):
    selected_broker = normalise_broker_id(broker_id) if broker_id else None
    effective = {
        supported_id: effective_risk_management(supported_id).model_dump()
        for supported_id in BROKER_NAMES
    }
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "risk_values": (
                effective[selected_broker]
                if selected_broker
                else risk_management.model_dump()
            ),
            "global_risk_values": risk_management.model_dump(),
            "broker_overrides": risk_overrides,
            "effective_risk_by_broker": effective,
            "selected_broker_id": selected_broker,
        },
    )

@app.get("/get_channels", response_class=JSONResponse)
async def get_channels() -> JSONResponse:
    """The channel file as it stands, disabled and invalid entries included.

    Invalid entries are returned rather than hidden so the dashboard can show
    them for correction. They are skipped by the listener, never traded.
    """
    global telegram_listener
    try:
        entries = read_channel_entries()
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "channels_file": str(channels_file()),
            "channels": entries,
            "timezone_choices": timezone_choices(),
            "listener_enabled": telegram_listener is not None,
            "listener_connected": bool(
                telegram_listener
                and telegram_listener.client
                and telegram_listener.client.is_connected()
            ),
            "listener_channels": telegram_listener.status() if telegram_listener else [],
        },
    )

@app.post("/set_channels", response_class=JSONResponse)
async def set_channels(request: Request) -> JSONResponse:
    """Replace the channel list, then reload the listener.

    The whole list is validated before anything is written, so a rejected save
    can never leave a half-edited file behind. The write itself is atomic.
    """
    global telegram_listener
    try:
        payload = await request.json()
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Expected a JSON body holding a 'channels' list.",
        )

    entries = payload.get("channels") if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The body needs a 'channels' list.",
        )
    if not entries:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Keep at least one entry: the listener needs a source to read.",
        )

    configs: list[ChannelConfig] = []
    problems: list[str] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        config, problem = coerce_channel(entry)
        label = f"Channel {index + 1}"
        if config is None:
            problems.append(f"{label}: {problem}")
            continue
        if config.channel in seen:
            problems.append(f"{label}: {config.channel!r} is already listed above")
            continue
        seen.add(config.channel)
        configs.append(config)
    if problems:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=" ".join(problems)
        )

    path = channels_file()
    try:
        write_channels(str(path), configs)
    except OSError as exc:
        logger.error("Could not write %s: %s", path, exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not write {path}: {exc}",
        )

    if signal_router is not None:
        signal_router.broker_for_channel = broker_routes_for_channels(
            [channel_to_dict(config) for config in configs]
        )
    reloaded, message = True, "Saved"
    if telegram_listener is not None:
        reloaded, message = await restart_telegram_listener()
    logger.info("Saved %d channel(s) to %s; listener: %s", len(configs), path, message)

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "message": message if reloaded else f"Saved, but the listener did not restart: {message}",
            "reloaded": reloaded,
            "channels": [channel_to_dict(config) for config in configs],
        },
    )

@app.post("/trade_signal")
async def trade_signal_webhook(request: Request):
    # Defensive read: MacroDroid can abort the POST before the body is
    # fully transferred, which makes request.body() raise ClientDisconnect.
    try:
        raw_body = await request.body()
    except ClientDisconnect:
        logger.warning("Client disconnected before signal body was received.")
        return Response(status_code=499)

    raw_data = raw_body.decode("utf-8", errors="replace")
    logger.info(f"Received raw data from notification: {raw_data}")

    if not webhook_auth_ok(request):
        webhook_stats["unauthorized"] += 1
        webhook_stats["last_result"] = "unauthorized"
        client_host = request.client.host if request.client else "unknown"
        logger.warning("Rejected /trade_signal request with invalid credentials from %s", client_host)
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"message": "Unauthorized: missing or invalid webhook secret."},
        )

    trade_data: SIGNAL | None = None
    try:
        trade_data = await parse_signal(text=raw_data)
        if trade_data is None:
            webhook_stats["rejected"] += 1
            webhook_stats["last_result"] = "invalid_signal"
            return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"message": "Invalid trade signal data."})
        signal_details = trade_data.signal_details
        broker_id = route_signal_to_broker(signal_details)
        risk = effective_risk_management(broker_id)
        broker = await ensure_broker_connected(broker_id)
        balance = to_float(await broker.balance())
        broker_balances[broker_id] = balance
        if broker_pnl(broker_id) <= risk.drawback_threshold:
            webhook_stats["rejected"] += 1
            webhook_stats["last_result"] = "drawdown_halt"
            logger.warning(
                "Daily P/L for %s is below its effective threshold. Signal rejected.",
                broker_id,
            )
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"message": "Trade signal processing halted due to P_n_L_day threshold."},
            )
        asyncio.create_task(take_trade(trade_data))
    except HTTPException:
        webhook_stats["rejected"] += 1
        webhook_stats["last_result"] = "invalid_signal_route"
        if trade_data is not None:
            Signals.pop(trade_data.signal_id, None)
        raise
    except (Exception,KeyboardInterrupt) as e:
        webhook_stats["rejected"] += 1
        webhook_stats["last_result"] = "broker_unavailable"
        logger.error(f"Error taking trade: {e}", exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"message": f"Broker unavailable or signal could not be routed: {e}"},
        )
    webhook_stats["accepted"] += 1
    webhook_stats["last_result"] = "accepted"
    return JSONResponse(status_code=status.HTTP_200_OK, content={"message": "Trade signal received and processed successfully."})
    
# Helper functions
async def parse_signal(
    text: str = "", channel_config: ChannelConfig | None = None
) -> SIGNAL | None:
    global risk_management,Signals
    #parse signal data
    parsed_data = parse_macrodroid_trade_data(text)
    logger.info(f"Parsed trade data: {parsed_data}")  
    timezone = (
        channel_config.timezone
        if channel_config is not None
        else parsed_data.get("timezone")
    )
    if not parsed_data.get("asset") or not parsed_data.get("direction") or not parsed_data.get("time") or not parsed_data.get("signal_provider") or not timezone:
        logger.error("Failed to parse essential trade data (asset, direction, entry time, signal provider, or configured timezone) from notification. Aborting trade attempt.")
        return None
    #assign parsed data to variables
    asset_name_for_po = str(parsed_data["asset"])
    direction = str(parsed_data["direction"])
    entryTime = str(parsed_data["time"])
    signal_provider = str(parsed_data["signal_provider"])
    timezone = str(timezone)
    broker_id = (
        channel_config.broker
        if channel_config is not None
        else parsed_data.get("broker")
    )
    channel_key = (
        channel_config.key
        if channel_config is not None
        else parsed_data.get("channel")
    )
    logger.info(f"\n\n -------Parsed trade data:----------\n--Asset: {asset_name_for_po}\n--Direction: {direction}\n--Entry Time: {entryTime}\n--Signal Provider: {signal_provider}\n--Timezone: {timezone}\n-----------------------------------\n\n ")
    # Validate direction
    if not direction.upper() in {"CALL", "PUT", "BUY", "SELL"}:
        return None
    # Convert entry time to local timezone
    LOCAL_TIMEZONE = pytz.timezone(str(risk_management.local_timezone))
    current_local_dt = datetime.now(LOCAL_TIMEZONE)
    SIGNAL_TIMEZONE = pytz.timezone(str(timezone))
    try:
        signal_time_obj = datetime.strptime(entryTime, "%H:%M").time()
        signal_dt_in_signal_tz = SIGNAL_TIMEZONE.localize(datetime(current_local_dt.year, current_local_dt.month, current_local_dt.day,signal_time_obj.hour, signal_time_obj.minute, 0))        
        # Check if local time is before 6 AM
        signal_tz_number = int(timezone[-2:])
        logger.info(f"signal_tz_number: {timezone}")
        local_tz_number = int(risk_management.local_timezone[-2:])
        minTimezone = min(signal_tz_number,local_tz_number)
        maxTimezone = max(signal_tz_number,local_tz_number)
        rangeTimezone = range(minTimezone,maxTimezone)
        logger.info(f"local_tz_number: {len(rangeTimezone)}")
        if current_local_dt.hour < len(rangeTimezone):
            signal_dt_in_signal_tz = signal_dt_in_signal_tz - timedelta(days=1)
        target_local_dt = signal_dt_in_signal_tz.astimezone(LOCAL_TIMEZONE)
    except (Exception, KeyboardInterrupt) as e:
        logger.error(f"Error parsing or converting signal entry time '{entryTime}': {e}", exc_info=True)
        return None
    logger.info(f"Signal entry time {signal_dt_in_signal_tz.tzinfo}: {entryTime}. Calculated local target entry time: {target_local_dt.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    
    data = {
        "signal_id":f"{signal_provider}|{entryTime}|{asset_name_for_po}|{broker_id or channel_key or ''}",
        "signal_details":{"signal_provider": signal_provider,
        "asset":asset_name_for_po,
        "direction":direction,
        "entry_time": target_local_dt,
        "broker_id": broker_id,
        "channel_key": channel_key
            }
        }
    
    signal_data = SIGNAL(**data)
    
    logger.info(f"New signal received:{asset_name_for_po} {direction}. Initiating a new trade sequence. Initial Amount: ${risk_management.initial_amount}")    
    try:
        if signal_data.signal_id not in Signals:
            await asyncio.sleep(random() * 10)  # Small delay to ensure proper logging order
            saved = get_runtime_state().save_signal(
                signal_data.signal_id,
                "pending",
                signal_data.signal_details.model_dump(mode="json"),
            )
            if not saved:
                logger.warning(
                    "Signal %s was already recorded and will not be placed again.",
                    signal_data.signal_id,
                )
                return None
            Signals[signal_data.signal_id] = signal_data.signal_details
        else:
            logger.warning(f"Signal for {signal_data.signal_details.asset} {signal_data.signal_details.direction} at {signal_data.signal_details.entry_time} from {signal_data.signal_details.signal_provider} already exists. Skipping duplicate signal.")
            return None
    except (Exception,KeyboardInterrupt) as e:
            logger.error(f"Error placing trade for {signal_data.signal_details.asset} {signal_data.signal_details.direction}: {e}", exc_info=True)
            del signal_data
    if current_local_dt > target_local_dt + timedelta(seconds=1): # Allow a small buffer for late signals, e.g., up to 5 seconds past target entry time.
            logger.warning(f"Signal for {asset_name_for_po} {direction} (Entry: {entryTime}) arrived late. "
                       f"Current local time: {current_local_dt.strftime('%d-%m-%Y %H:%M:%S')}, Target local time: {target_local_dt.strftime('%d-%m-%Y %H:%M:%S')}. "
                       f"Skipping trade.")
            Signals.pop(signal_data.signal_id)
            return None
    return signal_data
    
async def take_trade(signal:SIGNAL):
    global trade_details,Signals,signal_router
        # Place the initial trade
    current_local_dt = datetime.now(pytz.timezone(str(risk_management.local_timezone)))
    active_trade_id: str | None = None
    try:
        signal_data = signal.signal_details
        broker_id = route_signal_to_broker(signal_data)
        time_to_wait_seconds = (signal_data.entry_time - current_local_dt- timedelta(milliseconds=0)).total_seconds()
        if time_to_wait_seconds > 0:
            logger.info(f"Waiting {time_to_wait_seconds:.2f} seconds until target entry time: {signal_data.entry_time.strftime('%H:%M:%S')}")
            await asyncio.sleep(time_to_wait_seconds)
        else:
            logger.info(f"Signal arrived exactly at or slightly past target entry time ({current_local_dt.strftime('%H:%M:%S')} vs {signal_data.entry_time.strftime('%H:%M:%S')}). Placing trade immediately.")        
        try:
            direction_raw = signal_data.direction
            normalized_dir = "CALL" if direction_raw.upper() in {"BUY", "CALL"} else "PUT"

            broker_asset = (
                signal_data.asset
                if signal_data.asset.lower().endswith("_otc")
                else f"{signal_data.asset}_otc"
            )

            risk = effective_risk_management(broker_id)
            broker_adapter = await ensure_broker_connected(broker_id)
            balance = to_float(await broker_adapter.balance())
            broker_balances[broker_id] = balance
            if broker_pnl(broker_id) <= risk.drawback_threshold:
                risk_rejection(
                    f"{broker_id} daily P/L {broker_pnl(broker_id)} reached "
                    f"drawback threshold {risk.drawback_threshold}"
                )
                get_runtime_state().set_signal_status(signal.signal_id, "rejected")
                Signals.pop(signal.signal_id, None)
                return
            allowed, reason = can_start_sequence(
                signal_data.signal_provider,
                signal_data.asset,
                broker_id=broker_id,
                risk=risk,
            )
            if not allowed:
                risk_rejection(reason)
                get_runtime_state().set_signal_status(signal.signal_id, "rejected")
                Signals.pop(signal.signal_id, None)
                return

            get_runtime_state().set_signal_status(signal.signal_id, "placing")
            placement = await broker_adapter.place_call_put(
                asset=broker_asset,
                direction=normalized_dir,
                amount=risk.initial_amount,
                timeframe_seconds=risk.timeframe,
                check_win=False,
            )
            buy_id = placement.trade_id

            # Reconstruct Details shape to preserve existing downstream code.
            Details = {
                "asset": placement.asset,
                "openPrice": placement.open_price,
                "openTime": placement.entry_time.strftime("%Y-%m-%d %H:%M:%S"),
                "amount": placement.amount,
            }
        except (Exception,KeyboardInterrupt) as e:
            logger.error(
                "Error placing trade for %s at %s: %s",
                signal_data.asset, signal_data.entry_time, e, exc_info=True,
            )
            get_runtime_state().set_signal_status(signal.signal_id, "needs_review")
            Signals.pop(signal.signal_id, None)
            return
        
        logger.info(f"\n\n======Trade placed successfully.=======\n -Trade ID: {buy_id}\n-Details: {Details}\n\n")
        data = {
        "trade_id":buy_id,
        "trade_details":{"signal_provider": signal_data.signal_provider,
        "asset":Details["asset"],
        "direction":signal_data.direction,
        "entry_time": datetime.strptime(Details["openTime"], "%Y-%m-%d %H:%M:%S"),
        "level":0,
        "open_price":Details["openPrice"],
        "broker_id":broker_id,
        "signal_id":signal.signal_id,
        "amount":to_float(Details.get("amount"))
            }
        }
        
        trade = TRADE(**data)
        active_trade_id = trade.trade_id
        logger.info(f"trade details: {trade.trade_details}")
        trade_details[trade.trade_id] = trade.trade_details
        get_runtime_state().save_active_trade(
            trade.trade_id,
            trade.trade_details.model_dump(mode="json"),
            signal_id=signal.signal_id,
        )
        trade_results = await manage_martingale(trade=trade)
        if trade_results:
            logger.info(f"Signal for {trade.trade_details.signal_provider} at {signal_data.entry_time} was a success")
        else:
            logger.info(f"Signal for {trade.trade_details.signal_provider} at {signal_data.entry_time}  Failed")
        Signals.pop(signal.signal_id, None)
            
    except(Exception,KeyboardInterrupt) as e:
            logger.error("Trade sequence failed for %s: %s", signal.signal_id, e, exc_info=True)
            Signals.pop(signal.signal_id, None)
            if active_trade_id is None:
                with contextlib.suppress(KeyError):
                    get_runtime_state().set_signal_status(signal.signal_id, "failed")
            return

    
async def manage_martingale(trade:TRADE)-> bool:
    global trade_details,closed_trades,signal_router
    current_trade = trade.trade_details
    broker_id = current_trade.broker_id
    risk = effective_risk_management(broker_id)
    logger.info(f"waiting for trade to end: {trade.trade_id}")
    logger.info(f"current trade details: {current_trade}")
    try:
        broker_adapter = await ensure_broker_connected(broker_id)
        result_details = await broker_adapter.check_result(trade.trade_id)
        result = normalize_trade_result(
            result_details.get("result")
            or result_details.get("status")
        )
    except (Exception,KeyboardInterrupt) as e:
        logger.error(
            f"Error checking trade result for {trade.trade_id}: {e}",
            exc_info=True,
        )
        finalize_trade(
            trade.trade_id,
            trade_history_payload(current_trade, "unknown", {}),
            signal_id=current_trade.signal_id,
        )
        return False
    logger.info("Trade result for %s from %s: %s", trade.trade_id, broker_id, result_details)
    if result == "UNKNOWN":
        logger.error(
            "Broker returned an unknown result for %s; ending the sequence without "
            "recording a win or placing a recovery trade.",
            trade.trade_id,
        )
        finalize_trade(
            trade.trade_id,
            trade_history_payload(current_trade, "Unknown", result_details),
            signal_id=current_trade.signal_id,
        )
        return False
    if result == "LOSS":
        loss_amount = to_float(result_details.get("amount", current_trade.amount))
        record_broker_pnl(broker_id, -loss_amount, f"{trade.trade_id}:loss")
        if not risk.martingale_enabled:
            logger.info("Martingale is disabled; ending sequence after loss %s", trade.trade_id)
            finalize_trade(
                trade.trade_id,
                trade_history_payload(current_trade, "Loss", result_details),
                signal_id=current_trade.signal_id,
            )
            return False
        current_trade.level = current_trade.level + 1
        if current_trade.level > risk.martingale_levels:
            logger.warning(f"Max martingale levels reached for trade {trade.trade_id}. Ending martingale sequence.")
            finalize_trade(
                trade.trade_id,
                trade_history_payload(
                    current_trade,
                    "Loss",
                    result_details,
                    amount=current_trade.amount / risk.martingale_multiplier,
                    level=current_trade.level - 1,
                ),
                signal_id=current_trade.signal_id,
            )
            return False
        logger.info(
            f"Trade {trade.trade_id} lost. Initiating martingale sequence. level: {int(current_trade.level)}"
        )
        new_amount = current_trade.amount * risk.martingale_multiplier
        broker_adapter = await ensure_broker_connected(broker_id)
        balance = to_float(await broker_adapter.balance())
        broker_balances[broker_id] = balance
        allowed, reason = can_start_trade(
            new_amount,
            current_trade.signal_provider,
            current_trade.asset,
            exclude_trade_id=trade.trade_id,
            is_recovery=True,
            broker_id=broker_id,
            risk=risk,
            balance=balance,
        )
        if not allowed:
            risk_rejection(reason)
            logger.warning("Stopping martingale sequence for %s", trade.trade_id)
            finalize_trade(
                trade.trade_id,
                trade_history_payload(
                    current_trade,
                    "Loss",
                    result_details,
                    amount=current_trade.amount,
                    level=current_trade.level - 1,
                ),
                signal_id=current_trade.signal_id,
            )
            return False
        current_trade.amount = new_amount
        logger.info(f"Placing martingale trade level {current_trade.level} for amount: ${new_amount}")
        store = get_runtime_state()
        try:
            store.set_active_trade_state(
                trade.trade_id,
                "placing",
                current_trade.model_dump(mode="json"),
            )
        except KeyError:
            store.save_active_trade(
                trade.trade_id,
                current_trade.model_dump(mode="json"),
                state="placing",
                signal_id=current_trade.signal_id,
            )
        if current_trade.signal_id:
            store.set_signal_status(current_trade.signal_id, "placing")
        try:
            normalized_dir = "CALL" if current_trade.direction.upper() in {"BUY", "CALL"} else "PUT"
            placement = await broker_adapter.place_call_put(
                asset=current_trade.asset,
                direction=normalized_dir,
                amount=new_amount,
                timeframe_seconds=risk.timeframe,
                check_win=False,
            )
            buy_id = placement.trade_id
            Details = {
                "asset": placement.asset,
                "openPrice": placement.open_price,
                "openTime": placement.entry_time.strftime("%Y-%m-%d %H:%M:%S"),
                "amount": placement.amount,
            }
        except (Exception,KeyboardInterrupt) as e:
            logger.error(f"Error placing martingale trade for {current_trade.asset} {current_trade.direction}: {e}", exc_info=True)
            store.set_active_trade_state(
                trade.trade_id,
                "needs_review",
                current_trade.model_dump(mode="json"),
            )
            if current_trade.signal_id:
                store.set_signal_status(current_trade.signal_id, "needs_review")
            trade_details.pop(trade.trade_id, None)
            return False
        logger.info(
            f"\n\n======Martingale Trade placed successfully.=======\n -Trade ID: {buy_id}\n-Details: {Details}\n\n"
        )
        data = {
        "trade_id":buy_id,
        "trade_details":{"signal_provider": current_trade.signal_provider,
        "asset":Details["asset"],
        "direction":current_trade.direction,
        "entry_time": datetime.strptime(Details["openTime"], "%Y-%m-%d %H:%M:%S"),
        "level":current_trade.level,
        "open_price":Details["openPrice"],
        "broker_id":broker_id,
        "signal_id":current_trade.signal_id,
        "entry_id":buy_id,
        "amount":to_float(Details.get("amount"))
            }
        }
        next_trade = TRADE(**data)
        closed_payload = trade_history_payload(
            current_trade,
            "Loss",
            {},
            amount=current_trade.amount / risk.martingale_multiplier,
            level=current_trade.level - 1,
        )
        store.roll_active_trade(
            trade.trade_id,
            next_trade.trade_id,
            next_trade.trade_details.model_dump(mode="json"),
            closed_payload,
            signal_id=current_trade.signal_id,
        )
        closed_trades[trade.trade_id] = closed_payload
        trade_details.pop(trade.trade_id, None)
        trade = next_trade
        trade_details[trade.trade_id] = trade.trade_details
        status_results = await manage_martingale(trade=trade)
        return status_results
    else:
        profit = to_float(result_details.get("profit", result_details.get("pnl", 0)))
        result_label = "Draw" if result == "DRAW" else "Won"
        logger.info("Trade %s ended with %s; martingale sequence completed.", trade.trade_id, result_label)
        print(f"==trade result==\n -Asset:{current_trade.asset}\n -lastest amount: {current_trade.amount}\n -martingale level: {current_trade.level}\n -profit/loss: {profit}\n")
        record_broker_pnl(broker_id, profit, f"{trade.trade_id}:settlement")
        finalize_trade(
            trade.trade_id,
            trade_history_payload(current_trade, result_label, result_details),
            signal_id=current_trade.signal_id,
        )
        return True
    
async def reset_P_n_L_day():
    global broker_pnl_today
    while True:
        now = datetime.now(pytz.timezone(risk_management.local_timezone))
        next_reset = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        wait_seconds = (next_reset - now).total_seconds()
        await asyncio.sleep(wait_seconds)
        daily, _ = get_runtime_state().pnl_totals(local_trading_date())
        broker_pnl_today.clear()
        broker_pnl_today.update(daily)
