import asyncio
from random import random
import pytz
from fastapi import FastAPI, Request, HTTPException, status
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional, AsyncIterator, Any
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from BinaryOptionsToolsV2.pocketoption import PocketOptionAsync
from parse_data import parse_macrodroid_trade_data
from telegram_listener import TelegramSignalListener
import os
import secrets
from pydantic import BaseModel, Field
from starlette.requests import ClientDisconnect
from starlette.responses import Response

from rich.logging import RichHandler
import logging
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from typing import Callable, Tuple

load_dotenv()

logging.basicConfig(level="DEBUG", handlers=[RichHandler()])
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

class ACCOUNT_DETAILS(BaseModel):
    balance: float=0.0
    P_n_L_day: float=0.0
    lifespan: float=0.0
    async def update_balance(self,api):
        self.balance = await api.balance()
        

class QueueMiddleware(BaseHTTPMiddleware):
    """Queue incoming HTTP requests and process them sequentially.

    - max_queue: 0 means unlimited queue size. Set >0 to limit queue length.
    """
    def __init__(self, app, max_queue: int = 0):
        super().__init__(app)
        self._queue: asyncio.Queue[Tuple[Callable, Request, asyncio.Future]] = asyncio.Queue(maxsize=max_queue)
        self._worker_task: asyncio.Task | None = None

    async def dispatch(self, request: Request, call_next: Callable):
        # start worker lazily on first request (safe for startup ordering)
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker())

        loop = asyncio.get_event_loop()
        response_future: asyncio.Future = loop.create_future()
        # enqueue the work item: (call_next coroutine factory, request, future to set result)
        await self._queue.put((call_next, request, response_future))
        # wait until worker sets the result (Response) or raises
        response = await response_future
        return response

    async def _worker(self):
        while True:
            call_next, request, response_future = await self._queue.get()
            try:
                # call_next(request) returns a coroutine that yields a Response when awaited
                response = await call_next(request)
                if not response_future.cancelled():
                    response_future.set_result(response)
            except Exception as e:
                if not response_future.cancelled():
                    response_future.set_exception(e)
            finally:
                self._queue.task_done()

class TRADE_FIELDS( BaseModel):
    signal_provider: str
    asset:str
    direction:str
    entry_time:datetime
    level:int
    open_price:float
    amount:float
class TRADE(BaseModel):
    trade_id:str
    trade_details:TRADE_FIELDS
class SIGNAL_FIELDS(BaseModel):
    signal_provider: str
    asset:str
    direction:str
    entry_time:datetime
class SIGNAL(BaseModel):
    # map Tsid to signal details
    signal_id:str
    signal_details:SIGNAL_FIELDS

account_details:ACCOUNT_DETAILS = ACCOUNT_DETAILS()


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
Signals:dict = {}
trade_details:dict = {}
closed_trades:dict = {}
telegram_listener: TelegramSignalListener | None = None
telegram_listener_task: asyncio.Task | None = None
api: PocketOptionAsync | None = None
broker_connected = False

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


def active_sequences(exclude_trade_id: str | None = None) -> list[TRADE_FIELDS]:
    """Currently open sequences, excluding one trade id when needed.

    A recovery leg must exclude its own parent, otherwise the parent would count
    against the per-asset limit and block its own martingale step.
    """
    return [
        details
        for trade_id, details in list(trade_details.items())
        if trade_id != exclude_trade_id
    ]


def sequence_max_age_seconds() -> int:
    """Age after which a sequence is assumed dead and its slot reclaimed."""
    if risk_management.stale_sequence_seconds > 0:
        return risk_management.stale_sequence_seconds
    legs = risk_management.martingale_levels + 1 if risk_management.martingale_enabled else 1
    # Every leg can run for one timeframe, with slack for settlement lookups.
    return risk_management.timeframe * legs * 2 + 120


def purge_stale_sequences() -> int:
    """Drop sequences that outlived their expected duration.

    Without this, a result lookup that never returns would hold a slot against
    the open-trade limits indefinitely and silently block every later signal.
    """
    local_tz = pytz.timezone(str(risk_management.local_timezone))
    now = datetime.now(local_tz)
    max_age = sequence_max_age_seconds()
    removed = 0
    for trade_id, details in list(trade_details.items()):
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
) -> tuple[bool, str]:
    if not risk_management.martingale_enabled and amount != risk_management.initial_amount:
        return False, "martingale is disabled"
    if amount > risk_management.max_trade_amount:
        return False, f"trade amount {amount} exceeds max_trade_amount"

    open_sequences = active_sequences(exclude_trade_id)
    if len(open_sequences) >= risk_management.max_open_trades:
        summary = ", ".join(
            f"{d.signal_provider}/{d.asset}/{d.direction}" for d in open_sequences
        )
        return False, (
            f"maximum open trades reached ({len(open_sequences)}/{risk_management.max_open_trades}): {summary}"
        )

    if asset and risk_management.max_open_trades_per_asset:
        same_asset = [d for d in open_sequences if d.asset == asset]
        if len(same_asset) >= risk_management.max_open_trades_per_asset:
            return False, (
                f"already {len(same_asset)} open trade(s) on {asset} "
                f"(max_open_trades_per_asset={risk_management.max_open_trades_per_asset})"
            )

    if provider and risk_management.max_open_trades_per_provider:
        same_provider = [d for d in open_sequences if d.signal_provider == provider]
        if len(same_provider) >= risk_management.max_open_trades_per_provider:
            return False, (
                f"already {len(same_provider)} open trade(s) from provider {provider} "
                f"(max_open_trades_per_provider={risk_management.max_open_trades_per_provider})"
            )

    if account_details.balance and account_details.balance - amount < risk_management.min_balance_reserve:
        return False, "minimum balance reserve would be breached"
    return True, ""


def can_start_sequence(provider: str | None = None, asset: str | None = None) -> tuple[bool, str]:
    purge_stale_sequences()
    exposure = martingale_exposure(
        risk_management.initial_amount,
        risk_management.martingale_multiplier if risk_management.martingale_enabled else 1,
        risk_management.martingale_levels if risk_management.martingale_enabled else 0,
    )
    if exposure > risk_management.max_sequence_exposure:
        return False, f"worst-case sequence exposure {exposure} exceeds max_sequence_exposure"
    return can_start_trade(risk_management.initial_amount, provider, asset)


async def handle_telegram_signal(text: str, source_id: str) -> None:
    """Route a new Telegram post through the same path as MacroDroid."""
    provider = os.getenv("TELEGRAM_SIGNAL_PROVIDER", "telegram")
    timezone = os.getenv("SIGNAL_TIMEZONE", risk_management.local_timezone)
    enriched_text = text
    if "signal_provider" not in text.lower():
        enriched_text += f'\nsignal_provider="{provider}"'
    if "timezone" not in text.lower():
        enriched_text += f'\ntimezone="{timezone}"'

    signal = await parse_signal(enriched_text)
    if signal:
        logger.info("Accepted Telegram signal %s from %s", signal.signal_id, source_id)
        await take_trade(signal)
    else:
        logger.warning("Rejected Telegram signal from %s", source_id)

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global api,account_details,risk_management,telegram_listener,telegram_listener_task,broker_connected
    ensure_webhook_secret()
    #connect client
    ssid = os.getenv("ssid")
    if not ssid:
        logger.critical("SSID not found in .env; starting in degraded mode with trading disabled.")
        yield
        return
    
    logger.info("FastAPI lifespan startup event: Initializing Pocket Option client.")
    # Risk settings come from the environment (see load_risk_management) or from
    # POST /set_risk_management. Startup must never block on stdin, otherwise a
    # service manager restart would hang forever.
    logger.info(
        "Active risk configuration: %s",
        risk_management.model_dump(),
    )
    if not risk_management.martingale_enabled:
        logger.warning("Martingale recovery is disabled; losing trades will not be recovered.")

    try:
        api = PocketOptionAsync(ssid) #type: ignore
        await asyncio.sleep(5)
        for _ in range(3):
            logger.info(f"${api}")
            balance = await api.balance()
            if balance:
                logger.info("FastAPI lifespan startup event: Connected to Pocket Option client.")
                broker_connected = True
                logger.info(f"Startup Balance: {balance}")
                logger.info(f"\n\n\n== Risk management values == \n - Initial entry amount: ${risk_management.initial_amount}\n - max martingale level: {risk_management.martingale_levels}\n - Martingale multiplier: {risk_management.martingale_multiplier}\n - drawback threshol: {risk_management.drawback_threshold}\n - Timeframe: {risk_management.timeframe}\n\n-----use POST : /set_risk_management to change settings \n\n") #type: ignore
                account_details = ACCOUNT_DETAILS(balance=balance,P_n_L_day= 0,lifespan=0)
                break
            else:
                logger.error("FastAPI lifespan startup event: Failed to connect to Pocket Option client.")
                logger.info("Attempting to reconnect...")
                if _ >= 3:
                    logger.error("Failed to reconnect to Pocket Option client after 3 attempts.")
                    return
                await api.reconnect() #type: ignore
            await asyncio.sleep(5)
    except Exception or KeyboardInterrupt as e:
        logger.error(f"Failed to connect to Pocket Option client: {e}", exc_info=True)
        broker_connected = False
        yield
        return   
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
    if api is not None:
        await api.disconnect()
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
app.add_middleware(QueueMiddleware, max_queue=0)


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
            "broker_connected": broker_connected,
            "telegram_listener_enabled": telegram_listener is not None,
            "telegram_listener_connected": bool(
                telegram_listener and telegram_listener.client and telegram_listener.client.is_connected()
            ),
            "webhook_auth_configured": bool(webhook_secrets),
            "webhook": webhook_stats,
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
async def get_account_details():
    global api,account_details
    balance = None
    try:
        balance = to_float(await api.balance())
        if balance <= 0:
            for retries in range(10):
                await api.reconnect()
                balance = to_float(await api.balance())
                if balance > 0:
                    break
                if retries == 9:
                    logger.error("Failed to reconnect and get a valid balance after 10 attempts.")
        account_details.balance = balance
    except Exception as e:
        balance = "fetch failed"
    P_n_L_day = account_details.P_n_L_day
    lifespan  = account_details.lifespan
    jsonResponse = JSONResponse(status_code= status.HTTP_200_OK,content={"balance": balance,"P_n_L_day": P_n_L_day, "lifespan": lifespan})
    return jsonResponse

@app.get("/open_trades", response_class=JSONResponse)
async def get_open_trades():
    global api,risk_management,trade_details
    # Ensure integer values are passed to get_candles (period and offset must be ints)
        
    try:
        async with asyncio.timeout(10):
            openTrades = await api.opened_deals()
        # print(f"Fetched open trades: {openTrades}\n\n\n")
        # # handle dict or list responses from the API
        trades_list = []
        for tid,data in openTrades.items(): #type: ignore
            current_price = None
            async with asyncio.timeout(10):  # Set a 3-second timeout
                subscription = await api.subscribe_symbol(data.get("asset"),)
                async for candle in subscription:
                    print(f" Close: {candle['close']}")
                    current_price=candle['close']
                    print(f"Current price for {data.get('asset')}: {current_price}")
                    break  # We only need the latest candle
            trades_list.append({
                "trade_id": data.get("id"),
                "asset": data.get("asset"),
                "amount": data.get("amount"),
                "direction": trade_details[data.get("id")].direction if data.get("id") in trade_details else "-",
                "profit": data.get("profit"),
                "openedTime": data.get("openTime"),
                "open_price": data.get("openPrice"),
                # current_price may be non-serializable depending on API; include as-is and let caller handle
                "current_price": current_price #type: ignore
            })
        # print(f"Compiled open trades list: {trades_list}")
        return JSONResponse(status_code=status.HTTP_200_OK, content={"open_trades": trades_list})
    except (Exception, KeyboardInterrupt) as e:
        logger.error(f"Error fetching open trades: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error fetching open trades: {e}")

@app.get("/closed_trades")
async def get_closed_trades():
    global closed_trades
    if len(closed_trades) >= 10:
        closed_trades = dict(list(closed_trades.items())[-10:])
    return JSONResponse(status_code=status.HTTP_200_OK, content={"closed_trades": closed_trades})

@app.get("/current_signals", response_class=JSONResponse)
async def get_current_signals():
    global Signals
    signals_get = Signals.copy()
    logger.info(f"current Signals {signals_get}")
    signal_list = []
    for signal_id, signal_details in signals_get.items(): #type: ignore
        signal_list.append({
            "signal_provider": signal_details.signal_provider,
            "entry_time": str(signal_details.entry_time),
            "direction": signal_details.direction,
            "asset": signal_details.asset
        })
        
    return JSONResponse(status_code=status.HTTP_200_OK, content={"signals": signal_list})


    #
    # return JSONResponse(status_code= status.HTTP_200_OK,content={f"message:{Signals.get_signal(returnAll=True)}"})
@app.post("/set_risk_management", response_class=JSONResponse  )
async def set_risk_management(Risk: RISK_MANAGEMENT):
    global risk_management
    logger.info(f"Received risk management settings: {Risk}")
    risk_management = Risk
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"message": "Risk management values updated", "risk_values": risk_management.model_dump()},
    )
@app.get("/get_risk_management", response_class=JSONResponse)
async def get_risk_management():
    global risk_management
    risk_management_values = {}
    for key, value in risk_management.model_dump().items():
        risk_management_values[key] = value        
    return JSONResponse(status_code= status.HTTP_200_OK,content={"risk_values":risk_management_values})

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

    if api is None:
        webhook_stats["rejected"] += 1
        webhook_stats["last_result"] = "broker_unavailable"
        logger.error("Rejecting signal: broker client is not connected.")
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"message": "Broker client is not connected; signal not processed."},
        )

    account_details.balance = to_float(await api.balance())
    if account_details.P_n_L_day <= risk_management.drawback_threshold:
        webhook_stats["rejected"] += 1
        webhook_stats["last_result"] = "drawdown_halt"
        logger.warning("P_n_L_day is below the threshold. Trade signal processing halted.")
        return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content={"message": "Trade signal processing halted due to P_n_L_day threshold."})
    try:
        trade_data = await parse_signal(text=raw_data)
        if not trade_data:
            #type: ignore
            webhook_stats["rejected"] += 1
            webhook_stats["last_result"] = "invalid_signal"
            return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"message": "Invalid trade signal data."})
        asyncio.create_task(take_trade(trade_data))#type: ignore
    except (Exception,KeyboardInterrupt) as e:
        webhook_stats["rejected"] += 1
        webhook_stats["last_result"] = "error"
        logger.error(f"Error taking trade: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error taking trade: {e}")
    webhook_stats["accepted"] += 1
    webhook_stats["last_result"] = "accepted"
    return JSONResponse(status_code=status.HTTP_200_OK, content={"message": "Trade signal received and processed successfully."})
    
# Helper functions
async def parse_signal(text:str = "")->SIGNAL|bool:
    global risk_management,Signals
    #parse signal data
    parsed_data = parse_macrodroid_trade_data(text)
    logger.info(f"Parsed trade data: {parsed_data}")  
    if not parsed_data.get("asset") or not parsed_data.get("direction") or not parsed_data.get("time") or not parsed_data.get("signal_provider") or not parsed_data.get("timezone"):
        logger.error("Failed to parse essential trade data (asset, direction, entry time, signal provider, or timezone) from notification. Aborting trade attempt.")
        return False
    #assign parsed data to variables
    asset_name_for_po = parsed_data["asset"]
    direction = parsed_data["direction"]
    entryTime = parsed_data["time"]
    signal_provider = parsed_data["signal_provider"]
    timezone = parsed_data["timezone"]
    logger.info(f"\n\n -------Parsed trade data:----------\n--Asset: {asset_name_for_po}\n--Direction: {direction}\n--Entry Time: {entryTime}\n--Signal Provider: {signal_provider}\n--Timezone: {timezone}\n-----------------------------------\n\n ")
    # Validate direction
    if not direction.upper() in {"CALL", "PUT", "BUY", "SELL"}:
        return False
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
        return False
    logger.info(f"Signal entry time {signal_dt_in_signal_tz.tzinfo}: {entryTime}. Calculated local target entry time: {target_local_dt.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    
    data = {
        "signal_id":f"{signal_provider}|{entryTime}|{asset_name_for_po}",
        "signal_details":{"signal_provider": signal_provider,
        "asset":asset_name_for_po,
        "direction":direction,
        "entry_time": target_local_dt
            }
        }
    
    signal_data = SIGNAL(**data)
    
    logger.info(f"New signal received:{asset_name_for_po} {direction}. Initiating a new trade sequence. Initial Amount: ${risk_management.initial_amount}")    
    try:
        if signal_data.signal_id not in Signals:
            await asyncio.sleep(random() * 10)  # Small delay to ensure proper logging order
            Signals[signal_data.signal_id] = signal_data.signal_details
        else:
            logger.warning(f"Signal for {signal_data.signal_details.asset} {signal_data.signal_details.direction} at {signal_data.signal_details.entry_time} from {signal_data.signal_details.signal_provider} already exists. Skipping duplicate signal.")
            return False
    except (Exception,KeyboardInterrupt) as e:
            logger.error(f"Error placing trade for {signal_data.signal_details.asset} {signal_data.signal_details.direction}: {e}", exc_info=True)
            del signal_data
    if current_local_dt > target_local_dt + timedelta(seconds=1): # Allow a small buffer for late signals, e.g., up to 5 seconds past target entry time.
            logger.warning(f"Signal for {asset_name_for_po} {direction} (Entry: {entryTime}) arrived late. "
                       f"Current local time: {current_local_dt.strftime('%d-%m-%Y %H:%M:%S')}, Target local time: {target_local_dt.strftime('%d-%m-%Y %H:%M:%S')}. "
                       f"Skipping trade.")
            Signals.pop(signal_data.signal_id)
            return False
    return signal_data
    
async def take_trade(signal:SIGNAL):
    global risk_management,api,trade_details,Signals
        # Place the initial trade
    current_local_dt = datetime.now(pytz.timezone(str(risk_management.local_timezone)))
    active_trade_id: str | None = None
    try:
        #check entry status of trade_data        
        signal_data = signal.signal_details
        allowed, reason = can_start_sequence(signal_data.signal_provider, signal_data.asset)
        if not allowed:
            risk_rejection(reason)
            Signals.pop(signal.signal_id, None)
            return
        time_to_wait_seconds = (signal_data.entry_time - current_local_dt- timedelta(milliseconds=0)).total_seconds()
        if time_to_wait_seconds > 0:
            logger.info(f"Waiting {time_to_wait_seconds:.2f} seconds until target entry time: {signal_data.entry_time.strftime('%H:%M:%S')}")
            await asyncio.sleep(time_to_wait_seconds)
        else:
            logger.info(f"Signal arrived exactly at or slightly past target entry time ({current_local_dt.strftime('%H:%M:%S')} vs {signal_data.entry_time.strftime('%H:%M:%S')}). Placing trade immediately.")        
        try:
            signal_direction = signal_data.direction
            broker_asset = signal_data.asset if signal_data.asset.lower().endswith("_otc") else f"{signal_data.asset}_otc"
            if signal_direction.upper() == "BUY" or signal_direction.upper() == "CALL": #type: ignore
                (buy_id, Details) = await api.buy(
                    asset=broker_asset,
                    amount= risk_management.initial_amount, 
                    time= risk_management.timeframe,
                    check_win=False )
            elif signal_direction.upper() == "SELL" or signal_direction.upper() == "PUT":
                (buy_id, Details) = await api.sell(
                    asset=broker_asset,
                    amount= risk_management.initial_amount, 
                    time= risk_management.timeframe, 
                    check_win=False )
            else:
                raise ValueError(f"Unsupported trade direction: {signal_direction!r}")
        except (Exception,KeyboardInterrupt) as e:
            logger.error(
                "Error placing %s trade for %s at %s: %s",
                signal_data.direction, broker_asset, signal_data.entry_time, e, exc_info=True,
            )
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
        "amount":to_float(Details.get("amount"))
            }
        }
        
        trade = TRADE(**data)
        active_trade_id = trade.trade_id
        await asyncio.sleep(random() * 10) 
        logger.info(f"trade details: {trade.trade_details}")
        trade_details[trade.trade_id] = trade.trade_details
        # try:
        trade_results = await manage_martingale(trade=trade)
        if trade_results:
            logger.info(f"Signal for {trade.trade_details.signal_provider} at {signal_data.entry_time} was a success")
            Signals.pop(signal.signal_id)
            del signal_data
            del signal
        else:
            logger.info(f"Signal for {trade.trade_details.signal_provider} at {signal_data.entry_time}  Failed")
            Signals.pop(signal.signal_id)
            del signal_data
            del signal
            
    except(Exception,KeyboardInterrupt) as e:
            logger.error("Trade sequence failed for %s: %s", signal.signal_id, e, exc_info=True)
            Signals.pop(signal.signal_id, None)
            if active_trade_id:
                trade_details.pop(active_trade_id, None)
            return

    
async def manage_martingale(trade:TRADE)-> bool:
    global api,risk_management,account_details,trade_details,closed_trades
    current_trade = trade.trade_details
    logger.info(f"waiting for trade to end: {trade.trade_id}")
    logger.info(f"current trade details: {current_trade}")
    try:
        status = await api.check_win(trade.trade_id)
        result = status["result"]
    except (Exception,KeyboardInterrupt) as e:
        logger.error(f"Error checking trade result for {trade.trade_id}: {e}", exc_info=True)
        server_details = await api.closed_deals()
        await asyncio.sleep(random() * 10) 
        closed_trades[trade.trade_id] = {
            "trade_details":{
            "direction":current_trade.direction,
            "asset":current_trade.asset,
            "amount":current_trade.amount,
            "level":current_trade.level,
            "signal_provider":current_trade.signal_provider,
            "result":"unknown",
            "entry_time":current_trade.entry_time.strftime("%Y-%m-%d %H:%M:%S"),
            "open_price":current_trade.open_price},
            "from_server":str(server_details.get(trade.trade_id, {})) #type: ignore
            }
        trade_details.pop(trade.trade_id)
        del current_trade
        del trade
        return False
    logger.info(status)
    if result.upper() == "LOSS":
        loss_amount = to_float(status.get("amount"))
        account_details.P_n_L_day -= loss_amount
        account_details.lifespan -= loss_amount
        if not risk_management.martingale_enabled:
            logger.info("Martingale is disabled; ending sequence after loss %s", trade.trade_id)
            trade_details.pop(trade.trade_id, None)
            return False
        current_trade.level = current_trade.level + 1
        if current_trade.level > risk_management.martingale_levels:
            logger.warning(f"Max martingale levels reached for trade {trade.trade_id}. Ending martingale sequence.")
            await asyncio.sleep(random() * 10) 
            server_details = await api.closed_deals()
            closed_trades[trade.trade_id] = {
            "trade_details":{
            "direction":current_trade.direction,
            "asset":current_trade.asset,
            "amount":current_trade.amount/risk_management.martingale_multiplier,
            "level":current_trade.level-1,
            "signal_provider":current_trade.signal_provider,
            "result":"Loss",
            "entry_time":current_trade.entry_time.strftime("%Y-%m-%d %H:%M:%S"),
            "open_price":current_trade.open_price},
            "from_server":str(server_details.get(trade.trade_id, {})) #type: ignore
            }
            trade_details.pop(trade.trade_id)
            del current_trade
            del trade
            return False
        logger.info(f"Trade {trade.trade_id} lost. Initiating martingale sequence. level: {int(current_trade.level)}")#type: ignore
        new_amount = current_trade.amount * risk_management.martingale_multiplier
        allowed, reason = can_start_trade(
            new_amount,
            current_trade.signal_provider,
            current_trade.asset,
            exclude_trade_id=trade.trade_id,
        )
        if not allowed:
            risk_rejection(reason)
            logger.warning("Stopping martingale sequence for %s", trade.trade_id)
            trade_details.pop(trade.trade_id, None)
            return False
        current_trade.amount = new_amount
        logger.info(f"Placing martingale trade level {current_trade.level} for amount: ${new_amount}")
        try:
            if current_trade.direction.upper() == "BUY" or current_trade.direction.upper() == "CALL": #type: ignore
                (buy_id, Details) = await api.buy(
                    asset=current_trade.asset, 
                    amount= new_amount, 
                    time= risk_management.timeframe, 
                    check_win=False )
            elif current_trade.direction.upper() == "SELL" or current_trade.direction.upper() == "PUT":
                (buy_id, Details) = await api.sell(
                    asset=current_trade.asset, 
                    amount= new_amount, 
                    time= risk_management.timeframe, 
                    check_win=False )
        except (Exception,KeyboardInterrupt) as e:
            logger.error(f"Error placing martingale trade for {current_trade.asset} {current_trade.direction}: {e}", exc_info=True)
            await asyncio.sleep(random() * 10) 
            server_details = await api.closed_deals()
            closed_trades[trade.trade_id] = {
            "trade_details":{
            "direction":current_trade.direction,
            "asset":current_trade.asset,
            "amount":current_trade.amount/risk_management.martingale_multiplier,
            "level":current_trade.level-1,
            "signal_provider":current_trade.signal_provider,
            "result":"Loss",
            "entry_time":current_trade.entry_time.strftime("%Y-%m-%d %H:%M:%S"),
            "open_price":current_trade.open_price},
            "from_server":str(server_details.get(trade.trade_id, {})) #type: ignore
            }
            account_details.P_n_L_day = float(account_details.P_n_L_day) - current_trade.amount
            account_details.lifespan = float(account_details.lifespan) - current_trade.amount
            trade_details.pop(trade.trade_id)
            del current_trade
            del trade
            return False
        logger.info(f"\n\n======Martingale Trade placed successfully.=======\n -Trade ID: {buy_id}\n-Details: {Details}\n\n")
        await asyncio.sleep(random() * 10) 
        server_details = await api.closed_deals()
        closed_trades[trade.trade_id] = {
            "trade_details":{
            "direction":current_trade.direction,
            "asset":current_trade.asset,
            "amount":current_trade.amount/risk_management.martingale_multiplier,
            "level":current_trade.level-1,
            "signal_provider":current_trade.signal_provider,
            "result":"Loss",
            "entry_time":current_trade.entry_time.strftime("%Y-%m-%d %H:%M:%S"),
            "open_price":current_trade.open_price},
            "from_server":str(server_details.get(trade.trade_id, {})) #type: ignore
            }
        data = {
        "trade_id":buy_id,
        "trade_details":{"signal_provider": current_trade.signal_provider,
        "asset":Details["asset"],
        "direction":current_trade.direction,
        "entry_time": datetime.strptime(Details["openTime"], "%Y-%m-%d %H:%M:%S"),
        "level":current_trade.level,
        "open_price":Details["openPrice"],
        "entry_id":buy_id,
        "amount":to_float(Details.get("amount"))
            }
        }
        trade_details.pop(trade.trade_id)
        trade = TRADE(**data)
        trade_details[trade.trade_id] = trade.trade_details
        status_results = await manage_martingale(trade=trade)
        return status_results
    else:
        profit = to_float(status.get("profit"))
        logger.info(f"Trade {trade.trade_id} won or tied. Martingale sequence completed.")
        print(f"==trade result==\n -Asset:{current_trade.asset}\n -lastest amount: {current_trade.amount}\n -martingale level: {current_trade.level}\n -profit/loss: {profit}\n")
        await asyncio.sleep(random() * 10) 
        server_details = await api.closed_deals()
        closed_trades[trade.trade_id] = {
            "trade_details":{
            "direction":current_trade.direction,
            "asset":current_trade.asset,
            "amount":current_trade.amount,
            "level":current_trade.level,
            "signal_provider":current_trade.signal_provider,
            "result":"Won",
            "entry_time":current_trade.entry_time.strftime("%Y-%m-%d %H:%M:%S"),
            "open_price":current_trade.open_price},
            "from_server":str(server_details.get(trade.trade_id, {})) #type: ignore
            }
        trade_details.pop(trade.trade_id)
        del current_trade
        del trade
        account_details.P_n_L_day += profit
        account_details.lifespan += profit
        return True
    
async def reset_P_n_L_day():
    
    global account_details
    while True:
        now = datetime.now(pytz.timezone(risk_management.local_timezone))
        next_reset = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        wait_seconds = (next_reset - now).total_seconds()
        await asyncio.sleep(wait_seconds)
        account_details.P_n_L_day = 0



