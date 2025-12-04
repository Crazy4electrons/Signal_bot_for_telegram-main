import asyncio
import pytz
from fastapi import FastAPI, Request, HTTPException, status
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from datetime import date, datetime, timedelta
from typing import Optional, AsyncIterator, Any
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from BinaryOptionsToolsV2.pocketoption import PocketOptionAsync
from parse_data import parse_macrodroid_trade_data
import os
from pydantic import BaseModel, Field

from rich.logging import RichHandler
import logging
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from typing import Callable, Tuple

load_dotenv()

logging.basicConfig(level="DEBUG", handlers=[RichHandler()])
logger = logging.getLogger("PO_Signal")

class RISK_MANAGEMENT(BaseModel):
    initial_amount: float = 1
    martingale_levels: int = 3
    martingale_multiplier: int = 2
    drawback_threshold: int = -16
    timeframe:  int = 300
    local_timezone: str = 'Etc/GMT-2'

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
risk_management:RISK_MANAGEMENT = RISK_MANAGEMENT()
Signals:dict = {}
trade_details:dict = {}
# closed_trades:dict = {}

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global api,account_details,risk_management
    #connect client
    ssid = os.getenv("ssid")
    if not ssid:
        logger.critical("SSID not found in .env. Please ensure run scraper usin ./run_scaper.ps1 in in powershell, uv run scraper.py, pyhton scraper.py, or ensure .env is correctly set.")
        return
    
    logger.info("FastAPI lifespan startup event: Initializing Pocket Option client.")
    # risk_management:object|None = None
    #App startup values
    set_risk_management = input("Do you want to set risk managment values? (y/n): ").strip().lower()
    if set_risk_management == "y" or set_risk_management == "yes":
        for _ in range(3):
            intial_amount = input("Enter initial amount: ").strip()
            martingale_levels = input("Enter martingale levels: ").strip()
            martingale_multiplier = input("Enter martingale multiplier: ").strip()
            timeframe = input("Enter timeframe: ").strip()
            drawback_threshold = input("Enter drawback threshold: ").strip()
            if intial_amount and martingale_levels and martingale_multiplier and timeframe and drawback_threshold:
                risk_management = RISK_MANAGEMENT(
                    initial_amount = float(intial_amount),
                    martingale_levels=int(martingale_levels),
                    martingale_multiplier=int(martingale_multiplier),
                    drawback_threshold=int(drawback_threshold),
                    timeframe= int(timeframe)
                    )
                break
            else:
                logger.warning("Please enter all the required details.")
                if _ >= 3:
                    logger.info("not all values have been set for the app, will use default values for missing values..")
                    risk_management = RISK_MANAGEMENT()
            await asyncio.sleep(5)
    
    try:
        api = PocketOptionAsync(ssid) #type: ignore
        await asyncio.sleep(5)
        for _ in range(3):
            logger.info(f"${api}")
            balance = await api.balance()
            if balance:
                logger.info("FastAPI lifespan startup event: Connected to Pocket Option client.")
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
        return   
    asyncio.create_task(reset_P_n_L_day()) 
    yield
    # Disconnect
    await api.disconnect()
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
        balance = await api.balance()
        if balance <= 0:
            for retries in range(10):
                await api.reconnect()
                balance =await api.balance()
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
    period = int(risk_management.timeframe) // (int(risk_management.timeframe)//10)
    if period <= 0:
        period = 1
    offset = period * 5
    
        
    try:
        async with asyncio.timeout(10):
            openTrades = await api.opened_deals()
        # print(f"Fetched open trades: {openTrades}\n\n\n")
        # # handle dict or list responses from the API
        trades_list = []
        for tid,data in openTrades.items(): #type: ignore
            async with asyncio.timeout(10):  # Set a 3-second timeout
                current_price = await api.get_candles(data.get("asset"), period, offset)
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

# @app.get("/closed_trades")
# async def get_closed_trades():
#     global closed_trades
#     close_list = {}
#     for Tsid in closed_trades:
#         close_list[Tsid] = closed_trades[Tsid]
#     return JSONResponse(status_code=status.HTTP_200_OK, content={"closed_trades": close_list})

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
    if Risk.initial_amount and Risk.martingale_levels and Risk.martingale_multiplier and Risk.drawback_threshold and Risk.timeframe:
        risk_management.initial_amount = Risk.initial_amount
        risk_management.martingale_levels = Risk.martingale_levels
        risk_management.martingale_multiplier = Risk.martingale_multiplier
        risk_management.drawback_threshold = Risk.drawback_threshold
        risk_management.timeframe = Risk.timeframe
        return JSONResponse(status_code= status.HTTP_200_OK,content={f"message": "Risk managment values successfully set to: {risk_management}"})
    else:
        return JSONResponse(status_code= status.HTTP_400_BAD_REQUEST,content={f"message": "Risk managment values not set.Please ensure schema : {initial_amount,martingale_levels,martingale_multiplier,drawback_threshold,timeframe}"})
@app.post("/get_risk_management", response_class=JSONResponse)
async def get_risk_management():
    global risk_management
    return JSONResponse(status_code= status.HTTP_200_OK,content={f"message": f"Risk managment values: {risk_management}"})

@app.post("/trade_signal")
async def trade_signal_webhook(request: Request)->JSONResponse:
    global api,risk_management,account_details
    account_details.balance = await api.balance()
    raw_data = (await request.body()).decode('utf-8')
    logger.info(f"\n\nReceived raw data from notification: {raw_data}\n\n")
    if account_details.P_n_L_day <= risk_management.drawback_threshold:
        logger.warning("P_n_L_day is below the threshold. Trade signal processing halted.")
        return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content={"message": "Trade signal processing halted due to P_n_L_day threshold."})
    try:
        trade_data = parse_signal(text=raw_data)
        if not trade_data:
            #type: ignore
            return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"message": "Invalid trade signal data."})
        asyncio.create_task(take_trade(trade_data))#type: ignore
    except (Exception,KeyboardInterrupt) as e:
        logger.error(f"Error taking trade: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error taking trade: {e}")
    return JSONResponse(status_code=status.HTTP_200_OK, content={"message": "Trade signal received and processed successfully."})
    
# Helper functions
def parse_signal(text:str = "")->SIGNAL|bool:
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
            Signals[signal_data.signal_id] = signal_data.signal_details
        else:
            logger.warning(f"Signal for {asset_name_for_po} {direction} at {entryTime} from {signal_provider} already exists. Skipping duplicate signal.")
            return False
    except (Exception,KeyboardInterrupt) as e:
        logger.error(f"Error placing trade for {asset_name_for_po} {direction}: {e}", exc_info=True)
        del signal_data
        return False
    
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
    try:
        #check entry status of trade_data        
        signal_data = signal.signal_details
        time_to_wait_seconds = (signal_data.entry_time - current_local_dt- timedelta(milliseconds=0)).total_seconds()
        if time_to_wait_seconds > 0:
            logger.info(f"Waiting {time_to_wait_seconds:.2f} seconds until target entry time: {signal_data.entry_time.strftime('%H:%M:%S')}")
            await asyncio.sleep(time_to_wait_seconds)
        else:
            logger.info(f"Signal arrived exactly at or slightly past target entry time ({current_local_dt.strftime('%H:%M:%S')} vs {signal_data.entry_time.strftime('%H:%M:%S')}). Placing trade immediately.")        
        try:
            signal_direction = signal_data.direction
            if signal_direction.upper() == "BUY" or signal_direction.upper() == "CALL": #type: ignore
                (buy_id, Details) = await api.buy(
                    asset=signal_data.asset+"_otc", 
                    amount= risk_management.initial_amount, 
                    time= risk_management.timeframe,
                    check_win=False )
            elif signal_direction.upper() == "SELL" or signal_direction.upper() == "PUT":
                (buy_id, Details) = await api.sell(
                    asset=signal_data.asset+"_otc",  
                    amount= risk_management.initial_amount, 
                    time= risk_management.timeframe, 
                    check_win=False )
        except (Exception,KeyboardInterrupt) as e:
            logger.error(f"Error placing trade for {signal_data.asset+"_otc", } {signal_data.direction}: {e}", exc_info=True)
            del signal_data
            del signal
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
        "amount":float(Details["amount"])
            }
        }
        trade = TRADE(**data)
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
            logger.error(f"Error placing trade for {signal_data.asset+"_otc", } {signal_data.direction}: {e}", exc_info=True)
            Signals.pop(signal.signal_id)
            del trade
            del signal_data
            del signal
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
        # closed_trades[trade.trade_id] = {"trade_details":trade.trade_details,"result":"LOSS","from_server":None}
        trade_details.pop(trade.trade_id)
        del current_trade
        del trade
        return False
    logger.info(status)
    if result.upper() == "LOSS":
        # closed_trades[trade.trade_id] = {"trade_details":trade.trade_details,"result":"LOSS","from_server":status}
        account_details.P_n_L_day = account_details.P_n_L_day - status["amount"]
        account_details.lifespan = account_details.lifespan - status["amount"]
        current_trade.level = current_trade.level + 1
        if current_trade.level > risk_management.martingale_levels:
            logger.warning(f"Max martingale levels reached for trade {trade.trade_id}. Ending martingale sequence.")
            # closed_trades[trade.trade_id] = {"trade_details":trade.trade_details,"result":"LOSS","from_server":status}
            trade_details.pop(trade.trade_id)
            del current_trade
            del trade
            return False
        logger.info(f"Trade {trade.trade_id} lost. Initiating martingale sequence. level: {int(current_trade.level)}")#type: ignore
        new_amount = current_trade.amount * risk_management.martingale_multiplier
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
            # closed_trades[trade.trade_id] = {"trade_details":trade.trade_details,"result":"LOSS","from_server":status}
            account_details.P_n_L_day = float(account_details.P_n_L_day) - current_trade.amount
            account_details.lifespan = float(account_details.lifespan) - current_trade.amount
            trade_details.pop(trade.trade_id)
            del current_trade
            del trade
            return False
        logger.info(f"\n\n======Martingale Trade placed successfully.=======\n -Trade ID: {buy_id}\n-Details: {Details}\n\n")
        data = {
        "trade_id":buy_id,
        "trade_details":{"signal_provider": current_trade.signal_provider,
        "asset":Details["asset"],
        "direction":current_trade.direction,
        "entry_time": datetime.strptime(Details["openTime"], "%Y-%m-%d %H:%M:%S"),
        "level":current_trade.level,
        "open_price":Details["openPrice"],
        "entry_id":buy_id,
        "amount":float(Details["amount"])
            }
        }
        trade_details.pop(trade.trade_id)
        trade = TRADE(**data)
        trade_details[trade.trade_id] = trade.trade_details
        status_results = await manage_martingale(trade=trade)
        return status_results
    else:
        logger.info(f"Trade {trade.trade_id} won or tied. Martingale sequence completed.")
        print(f"==trade result==\n -Asset:{current_trade.asset}\n -lastest amount: {current_trade.amount}\n -martingale level: {current_trade.level}\n -profit/loss: {status["profit"]}\n")
        # closed_trades[trade.trade_id] = {"trade_details":trade.trade_details,"result":"WON","from_server":status}
        trade_details.pop(trade.trade_id)
        del current_trade
        del trade
        account_details.P_n_L_day = account_details.P_n_L_day + status["profit"]
        account_details.lifespan = account_details.lifespan + status["profit"]
        return True
    
async def reset_P_n_L_day():
    
    global account_details
    while True:
        now = datetime.now(pytz.timezone(risk_management.local_timezone))
        next_reset = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        wait_seconds = (next_reset - now).total_seconds()
        await asyncio.sleep(wait_seconds)
        account_details.P_n_L_day = 0
        
        
        
