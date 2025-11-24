from math import log
import os
import json
import time
import asyncio
import logging
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
# from BinaryOptionsToolsV2 import validate_asset
# Assuming parse_data.py is correctly implemented and available
from parse_data import parse_macrodroid_trade_data
from measure_latency import measure_one

load_dotenv()

from pydantic import BaseModel, Field




logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class TRADE_DETAILS(BaseModel):
    Tsid: str
    amount: float
    level: int = 0
    entryTime: datetime 
    direction: str
    full_details: dict

class OPEN_TRADES(BaseModel):
    # store open trade details in a list
    trades: dict = Field(default_factory=dict)

class SIGNAL_DETAILS(BaseModel):
    # map Tsid to signal details
    Signals: dict = Field(default_factory=dict)
    def add_new_signal(self,signal_provider:str,entry_time:datetime,direction:str)->dict:
        Tsid = str(f"{signal_provider}{entry_time}")
        if Tsid in self.Signals:
            return {"error":"Signal already exists"}
        if signal_provider and entry_time and direction:
            self.Signals[Tsid] = {"signal_provider":signal_provider,"entry_time":entry_time,"direction":direction}
            return {"Tsid":Tsid,"signal":self.Signals[Tsid]}
        else:
            return {"error":"Missing required fields"}
    def get_signal(self,Tsid:str="",signal_provider:str="",entry_time:datetime|None=None)->dict|None:
        if Tsid == "":
            Tsid = str(f"{signal_provider}{entry_time}")
        if Tsid not in self.Signals:
            return {"error":"Signal not found"}
        return {"Tsid":Tsid,"signal":self.Signals[Tsid]}
    def remove_signal(self,Tsid:str="",signal_provider:str="",entry_time:datetime=datetime.now())->dict|None:
        if Tsid == "":
            Tsid = str(f"{signal_provider}{entry_time}")
        try:
            return self.Signals.pop(Tsid)
        except:
            return {"error":"Signal not found"}
    
class RISK_MANAGEMENT(BaseModel):
    initial_amount: float = 1
    martingale_levels: int = 3
    martingale_multiplier: int = 2
    buffer_time: float = 0.5
    timeframe:  int = 300
    local_timezone: str = 'Etc/GMT-2'
        


class ACCOUNT_DETAILS(BaseModel):
    balance: float|None = None
    P_n_L_day: float|None = None
    lifespan: float|None = None
    

account_details = ACCOUNT_DETAILS()
risk_managment = RISK_MANAGEMENT()
Signals = SIGNAL_DETAILS()
open_trades = OPEN_TRADES()
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global api,account_details,risk_managment
    #connect client
    ssid = os.getenv("ssid")
    if not ssid:
        logger.critical("SSID not found in .env. Please ensure run scraper usin ./run_scaper.ps1 in in powershell, uv run scraper.py, pyhton scraper.py, or ensure .env is correctly set.")
        return
    
    logger.info("FastAPI lifespan startup event: Initializing Pocket Option client.")
    # risk_managment:object|None = None
    #App startup values
    set_risk_managment = input("Do you want to set risk managment values? (y/n): ").strip().lower()
    if set_risk_managment == "y":
        for _ in range(3):
            intial_amount = input("Enter initial amount: ").strip()
            martingale_levels = input("Enter martingale levels: ").strip()
            martingale_multiplier = input("Enter martingale multiplier: ").strip()
            timeframe = input("Enter timeframe: ").strip()
            buffer_time = input("Enter buffer time: ").strip()
            if intial_amount and martingale_levels and martingale_multiplier and timeframe and buffer_time:
                risk_managment = RISK_MANAGEMENT(
                    initial_amount = float(intial_amount),
                    martingale_levels=int(martingale_levels),
                    martingale_multiplier=int(martingale_multiplier),
                    buffer_time=float(buffer_time),
                    timeframe= int(timeframe)
                    )
                break
            else:
                logger.warning("Please enter all the required details.")
                if _ >= 3:
                    logger.info("not all values have been set for the app, will use default values for missing values..")
                    risk_managment = RISK_MANAGEMENT()
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
                print(f"\n\n\n== Risk management values == \n - Initial entry amount: ${risk_managment.initial_amount}\n - max martingale level: {risk_managment.martingale_levels}\n - Martingale multiplier: {risk_managment.martingale_multiplier}\n - Buffer time: {risk_managment.buffer_time}\n - Timeframe: {risk_managment.timeframe}\n\n-----use POST : /set_risk_managment to change settings \n\n") #type: ignore
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

# Enable CORS so browser pages served from file:// (origin 'null') or other origins can reach the API.
# For local development it's fine to allow all origins; tighten this in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # allow all origins including 'null'
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve the UI folder via HTTP so you can open http://localhost:8000/ui/ instead of loading files via file://
app.mount("/ui", StaticFiles(directory="ui", html=True), name="ui")

# Replace previous root handlers with this: serve ui/index.html if present
@app.get("/", response_class=HTMLResponse)
async def root_index():
    ui_dir = os.path.join(os.path.dirname(__file__), "ui")
    index_path = os.path.join(ui_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path, media_type="text/html")
    return HTMLResponse("<html><body><h1>Signal Bot</h1><p>UI not found. Visit /ui/</p></body></html>", status_code=200)

# Explicit endpoints to return JS and CSS (useful when not using StaticFiles or for direct linking)
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
    balance = await api.balance()
    account_details.balance = balance
    P_n_L_day = account_details.P_n_L_day
    lifespan  = account_details.lifespan
    jsonResponse = JSONResponse(status_code= status.HTTP_200_OK,content={"balance": balance,"P_n_L_day": P_n_L_day, "lifespan": lifespan})
    return jsonResponse

@app.get("/open_trades", response_class=JSONResponse)
async def get_open_trades():
    global api,open_trades,risk_managment
    # Ensure integer values are passed to get_candles (period and offset must be ints)
    period = int(risk_managment.timeframe) // 60
    if period <= 0:
        period = 1
    offset = period * 10
        
    try:
        openTrades = await api.opened_deals()
        trades_list = []
        # handle dict or list responses from the API
        if isinstance(openTrades, dict):
            for tid, data in openTrades.items():
                if not isinstance(data, dict):
                    continue
                # print(open_trades.trades[data.get("id")])
                trades_list.append({
                    "trade_id": data.get("id"),
                    "asset": data.get("asset"),
                    "amount": data.get("amount"),
                    "direction": open_trades.trades.get(data.get("id"), {}).get("direction"),
                    "profit": data.get("profit"),
                    "openedTime": data.get("openTime"),
                    "open_price": data.get("openPrice"),
                    # current_price may be non-serializable depending on API; include as-is and let caller handle
                    "current_price": await api.get_candles(data.get("asset"), period, offset) #type: ignore
                })
        elif isinstance(openTrades, list):
            for data in openTrades:
                if not isinstance(data, dict):
                    continue
                print(open_trades.trades[data.get("id")])
                trades_list.append({
                    "trade_id": data.get("id"),
                    "asset": data.get("asset"),
                    "amount": data.get("amount"),
                    "direction": open_trades.trades.get(data.get("id"), {}).get("direction"),
                    "profit": data.get("profit"),
                    "openedTime": data.get("openTime"),
                    # "closedTime": data.get("closedTime"),
                    "current_price": await api.get_candles(data.get("asset"), period, offset) #type: ignore
                })
        else:
            # fallback: return raw string representation inside a list so content is JSON-serializable
            trades_list.append({"raw": str(openTrades)})

        # Return a dict mapping key -> list (avoid using {openTrades} which creates a set)
        return JSONResponse(status_code=status.HTTP_200_OK, content={"open_trades": trades_list})
    except (Exception, KeyboardInterrupt) as e:
        logger.error(f"Error fetching open trades: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error fetching open trades: {e}")

@app.get("/current_signals", response_class=JSONResponse)
async def get_current_signals():
    global Signals
    return JSONResponse(status_code= status.HTTP_200_OK,content={f"message": f"Current signals: {Signals}"})

@app.post("/set_risk_managment", response_class=JSONResponse  )
async def set_risk_managment(Risk: RISK_MANAGEMENT):
    global risk_managment
    if Risk.initial_amount and Risk.martingale_levels and Risk.martingale_multiplier and Risk.buffer_time and Risk.timeframe:
        risk_managment.initial_amount = Risk.initial_amount
        risk_managment.martingale_levels = Risk.martingale_levels
        risk_managment.martingale_multiplier = Risk.martingale_multiplier
        risk_managment.buffer_time = Risk.buffer_time
        risk_managment.timeframe = Risk.timeframe
        return JSONResponse(status_code= status.HTTP_200_OK,content={f"message": "Risk managment values successfully set to: {risk_managment}"})
    else:
        return JSONResponse(status_code= status.HTTP_400_BAD_REQUEST,content={f"message": "Risk managment values not set.Please ensure schema : {initial_amount,martingale_levels,martingale_multiplier,buffer_time,timeframe}"})

@app.post("/get_risk_managment", response_class=JSONResponse)
async def get_risk_managment():
    global risk_managment
    return JSONResponse(status_code= status.HTTP_200_OK,content={f"message": f"Risk managment values: {risk_managment}"})

@app.post("/trade_signal")
async def trade_signal_webhook(request: Request)->JSONResponse:
    global api,risk_managment,account_details
    account_details.balance = await api.balance()
    raw_data = (await request.body()).decode('utf-8')
    logger.info(f"\n\nReceived raw data from notification: {raw_data}\n\n")
    parsed_data = parse_macrodroid_trade_data(raw_data)
    print(f"Parsed trade data: {parsed_data}")  
    if not parsed_data.get("asset") or not parsed_data.get("direction") or not parsed_data.get("time") or not parsed_data.get("signal_provider") or not parsed_data.get("timezone"):
        logger.error("Failed to parse essential trade data (asset, direction, entry time, signal provider, or timezone) from notification. Aborting trade attempt.")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Failed to parse essential trade data from notification.")

    asset_name_for_po = parsed_data["asset"]
    direction = parsed_data["direction"]
    entryTime = parsed_data["time"]
    signal_provider = parsed_data["signal_provider"]
    timezone = parsed_data["timezone"]
    logger.info(f"\n\n -------Parsed trade data:----------\n--Asset: {asset_name_for_po}\n--Direction: {direction}\n--Entry Time: {entryTime}\n--Signal Provider: {signal_provider}\n--Timezone: {timezone}\n-----------------------------------\n\n ")
    
    if not direction.upper() in {"CALL", "PUT", "BUY", "SELL"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid direction value. Expected 'CALL', 'PUT', 'BUY', or 'SELL'.")
    
    LOCAL_TIMEZONE = pytz.timezone(str(risk_managment.local_timezone))
    current_local_dt = datetime.now(LOCAL_TIMEZONE)
    SIGNAL_TIMEZONE = pytz.timezone(str(timezone))
        
    try:
        signal_time_obj = datetime.strptime(entryTime, "%H:%M").time()
        signal_dt_in_signal_tz = SIGNAL_TIMEZONE.localize(datetime(current_local_dt.year, current_local_dt.month, current_local_dt.day,signal_time_obj.hour, signal_time_obj.minute, 0))        
        # Check if local time is before 6 AM
        signal_tz_number = int(timezone[-1])
        local_tz_number = int(risk_managment.local_timezone[-1])
        if current_local_dt.hour <= (local_tz_number + signal_tz_number):
            signal_dt_in_signal_tz = signal_dt_in_signal_tz - timedelta(days=1)
        target_local_dt = signal_dt_in_signal_tz.astimezone(LOCAL_TIMEZONE)
    except (Exception, KeyboardInterrupt) as e:
        logger.error(f"Error parsing or converting signal entry time '{entryTime}': {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid signal entry time format: {e}")

    logger.info(f"Signal entry time (GMT-4): {entryTime}. Calculated local target entry time: {target_local_dt.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    # Allow a small buffer for late signals, e.g., up to 5 seconds past target entry time.
    if current_local_dt > target_local_dt + timedelta(seconds=5):
        logger.warning(f"Signal for {asset_name_for_po} {direction} (Entry: {entryTime}) arrived late. "
                       f"Current local time: {current_local_dt.strftime('%H:%M:%S')}, Target local time: {target_local_dt.strftime('%H:%M:%S')}. "
                       f"Skipping trade.")
        return JSONResponse(status_code=status.HTTP_200_OK, content={"status": "skipped", "message": "Signal arrived too late, trade skipped."})
    
    logger.info(f"New signal received:{asset_name_for_po} {direction}. Initiating a new trade sequence. Initial Amount: ${risk_managment.initial_amount}")
    
    try:
        if Signals.get_signal(signal_provider=signal_provider,entry_time=target_local_dt) == {"error":"Signal not found"}:
            new_signal = Signals.add_new_signal(signal_provider=signal_provider,entry_time=target_local_dt,direction=direction.upper())
        else:
            logger.warning(f"Signal for {asset_name_for_po} {direction} at {entryTime} from {signal_provider} already exists. Skipping duplicate signal.")
            return JSONResponse(status_code=status.HTTP_200_OK, content={"status": "skipped", "message": "Signal already exists, trade skipped."})
    except (Exception,KeyboardInterrupt) as e:
        logger.error(f"Error placing trade for {asset_name_for_po} {direction}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error placing trade: {e}")
    
    
    time_to_wait_seconds = (target_local_dt - datetime.now(LOCAL_TIMEZONE)- timedelta(milliseconds=0)).total_seconds()

    if time_to_wait_seconds > 0:
        logger.info(f"Waiting {time_to_wait_seconds:.2f} seconds until target entry time: {target_local_dt.strftime('%H:%M:%S')}")
        await asyncio.sleep(time_to_wait_seconds)
        logger.info(f"Reached target entry time. Proceeding with trade for {asset_name_for_po} {direction}.")
    else:
        logger.info(f"Signal arrived exactly at or slightly past target entry time ({current_local_dt.strftime('%H:%M:%S')} vs {target_local_dt.strftime('%H:%M:%S')}). Placing trade immediately.")
        
    # Place the initial trade
   
    try:
        signal_direction = Signals.get_signal(signal_provider=signal_provider,entry_time=target_local_dt)["signal"]["direction"] #type: ignore
        if signal_direction.upper() == "BUY" or signal_direction.upper() == "CALL": #type: ignore
            (buy_id, Details) = await api.buy(
                asset=asset_name_for_po+"_otc", 
                amount= risk_managment.initial_amount, 
                time= risk_managment.timeframe, 
                check_win=False )
        elif signal_direction.upper() == "SELL" or signal_direction.upper() == "PUT":
            (buy_id, Details) = await api.sell(
                asset=asset_name_for_po+"_otc", 
                amount= risk_managment.initial_amount, 
                time= risk_managment.timeframe, 
                check_win=False )
    except (Exception,KeyboardInterrupt) as e:
        logger.error(f"Error placing trade for {asset_name_for_po} {direction}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail={"message": f"Error placing trade: {e}"})
        # return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content={"message": f"Error placing trade: {e}"})
    
    logger.info(f"\n\n======Trade placed successfully.=======\n -Trade ID: {buy_id}\n-Details: {Details}\n\n")
    
    trade_details = TRADE_DETAILS(
        Tsid=new_signal["Tsid"],
        amount=risk_managment.initial_amount,
        entryTime=target_local_dt,
        direction=direction.upper(),
        full_details=Details
    )
    open_trades.trades[buy_id] = {
        "direction": direction.upper(),
        "Full_details": Details
    }
    try:
        asyncio.create_task(manage_martingale(trade_details=trade_details))
    except (Exception,KeyboardInterrupt) as e:
        logger.error(f"Error managing martingale for trade {buy_id}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail={"message": f"Error managing martingale: {e}"})
    return JSONResponse(status_code=status.HTTP_200_OK, content={"message": "Trade signal received and processed successfully."})
async def manage_martingale(trade_details: TRADE_DETAILS):
    
    global api,risk_managment,account_details
    account_details.balance = await api.balance()
    trade_id = trade_details.full_details["id"] 
    logger.info(f"waiting for trade to end: {trade_id}")
    try:
        status = await api.check_win(trade_id)
        print(status)
        result = status["result"]
    except (Exception,KeyboardInterrupt) as e:
        logger.error(f"Error checking trade result for {trade_id}: {e}", exc_info=True)
        open_trades.trades.pop(trade_id)
        raise e
    print(status)
    if result.upper() == "LOSS":
        account_details.P_n_L_day = account_details.P_n_L_day-status["amount"]
        account_details.lifespan = account_details.lifespan -status["amount"]
        trade_details.level = trade_details.level + 1
        if trade_details.level > risk_managment.martingale_levels:
            logger.warning(f"Max martingale levels reached for trade {trade_id}. Ending martingale sequence.")
            Signals.remove_signal(Tsid=trade_details.Tsid)
            open_trades.trades.pop(trade_id)
            trade_details.level = 0
            del trade_details
            return False
        logger.info(f"Trade {trade_id} lost. Initiating martingale sequence. level: {int(trade_details.level)}")#type: ignore
        new_amount = trade_details.amount * risk_managment.martingale_multiplier
        trade_details.amount = new_amount
        logger.info(f"Placing martingale trade level {trade_details.level} for amount: ${new_amount}")
        try:
            if trade_details.direction.upper() == "BUY" or trade_details.direction.upper() == "CALL": #type: ignore
                (buy_id, Details) = await api.buy(
                    asset=trade_details.full_details["asset"], 
                    amount= new_amount, 
                    time= risk_managment.timeframe, 
                    check_win=False )
            elif trade_details.direction.upper() == "SELL" or trade_details.direction.upper() == "PUT":
                (buy_id, Details) = await api.sell(
                    asset=trade_details.full_details["asset"], 
                    amount= new_amount, 
                    time= risk_managment.timeframe, 
                    check_win=False )
            trade_details.full_details = Details
            open_trades.trades[buy_id]=    open_trades.trades[buy_id] = {
                "direction":open_trades.trades[trade_id]["direction"].upper(),
                "Full_details": Details
                }
            open_trades.trades.pop(trade_id)
                
        except (Exception,KeyboardInterrupt) as e:
            logger.error(f"Error placing martingale trade for {trade_details.full_details['asset']} {trade_details.direction}: {e}", exc_info=True)
            open_trades.trades.pop(trade_id)
            raise e
        logger.info(f"\n\n======Martingale Trade placed successfully.=======\n -Trade ID: {buy_id}\n-Details: {Details}\n\n")
        asyncio.create_task(manage_martingale(trade_details=trade_details))
    else:
        logger.info(f"Trade {trade_id} won or tied. Martingale sequence completed.")
        print(f"==trade result==\n -lastest amount: {trade_details.amount}\n -martingale level: {trade_details.level}\n -profit/loss: {status["profit"]}\n")
        trade_details.level = 0
        account_details.balance = await api.balance()
        account_details.P_n_L_day = account_details.P_n_L_day + status["profit"]
        account_details.lifespan = account_details.lifespan + status["profit"]
        Signals.remove_signal(Tsid=trade_details.Tsid)
        open_trades.trades.pop(trade_id)
        del trade_details
        return True
    
    
async def reset_P_n_L_day():
    global account_details
    while True:
        now = datetime.now(pytz.timezone(risk_managment.local_timezone))
        next_reset = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        wait_seconds = (next_reset - now).total_seconds()
        await asyncio.sleep(wait_seconds)
        account_details.P_n_L_day = 0