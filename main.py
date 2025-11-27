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
from parse_data import parse_macrodroid_trade_data
from measure_latency import measure_one

load_dotenv()

from pydantic import BaseModel, Field




logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
class RISK_MANAGEMENT(BaseModel):
    initial_amount: float = 1
    martingale_levels: int = 3
    martingale_multiplier: int = 2
    buffer_time: float = 0.5
    timeframe:  int = 300
    local_timezone: str = 'Etc/GMT-2'

class TRADE_DETAILS(BaseModel):
    trades:dict = {}
    def add_trade(self,trade_id:str,details:dict,direction:str,Tsid:str)->dict|None:
            detail= {"Tsid":Tsid ,
                     "direction":direction,
                     "amount":details["amount"],
                     "level":0,
                     "full_details":details,
                     "entryTime":details["openTimestamp"],
                     "asset":details["asset"]
                     }
            for field in detail:
                if detail.get(field) is None:
                    raise Exception("Missing trade detail field")
            self.trades[trade_id]= detail
    def get_trade(self,trade_id:str,returnAll:bool=False)->dict:
        try:
            if returnAll:
                return self.trades
            return self.trades[trade_id]
        except:
            return {}
    def remove_trade(self,trade_id:str)->dict|None:
        try:
            return self.trades.pop(trade_id)
        except:
            return None
    async def take_trade(self,trade_data:dict)->JSONResponse:
        global risk_management,api
            # Place the initial trade
        try:
            if(trade_data["status"]== "skipped"):
                logger.info(f"Trade skipped: {trade_data.get('message')}")
                return JSONResponse(status_code=status.HTTP_200_OK, content={"message": "Trade skipped."})
            elif(trade_data["status"] == "wait"):
                wait_time = (trade_data["entry_time"] - datetime.now(pytz.timezone(risk_management.local_timezone))).total_seconds()
                logger.info(f"Waiting for {wait_time:.2f} seconds before placing trade for {trade_data.get('asset')} {trade_data.get('direction')}.")
                await asyncio.sleep(wait_time)
            try:
                signal_direction = trade_data["direction"]
                if signal_direction.upper() == "BUY" or signal_direction.upper() == "CALL": #type: ignore
                    (buy_id, Details) = await api.buy(
                        asset=trade_data["asset"]+"_otc", 
                        amount= risk_management.initial_amount, 
                        time= risk_management.timeframe,
                        check_win=False )
                elif signal_direction.upper() == "SELL" or signal_direction.upper() == "PUT":
                    (buy_id, Details) = await api.sell(
                        asset=trade_data["asset"]+"_otc", 
                        amount= risk_management.initial_amount, 
                        time= risk_management.timeframe, 
                        check_win=False )
            except (Exception,KeyboardInterrupt) as e:
                logger.error(f"Error placing trade for {trade_data["asset"]} {trade_data["direction"]}: {e}", exc_info=True)
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail={"message": f"Error placing trade: {e}"})
                # return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content={"message": f"Error placing trade: {e}"})

            logger.info(f"\n\n======Trade placed successfully.=======\n -Trade ID: {buy_id}\n-Details: {Details}\n\n")

            self.add_trade(trade_id=buy_id,details=Details,Tsid=trade_data["Tsid"],direction=trade_data["direction"])
            try:
                trade_results = await manage_martingale(trade_id=buy_id)
                if trade_results["status"] == "success":
                    Signals.remove_signal(Tsid=trade_results["Tsid"])
                    self.remove_trade(trade_id=trade_results["id"])
                    return JSONResponse(status_code=status.HTTP_200_OK, content={"message": "Trade and martingale sequence completed successfully."})
                else:
                    Signals.remove_signal(Tsid=trade_results["Tsid"])
                    self.remove_trade(trade_id=trade_results["id"])
                    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail={"message": trade_results["message"]}) 
            except (Exception,KeyboardInterrupt) as e:
                logger.error(f"Error managing martingale for trade {buy_id}: {e}", exc_info=True)
                Signals.remove_signal(Tsid=trade_data["Tsid"])
                self.remove_trade(trade_id=buy_id)
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail={"message": f"Error managing martingale: {e}"})
        except(Exception,KeyboardInterrupt) as e:
            raise e

    def update_trade(self,old_trade_id:str,trade_id:str,details:dict)->dict|None:
        if old_trade_id not in self.trades:
            raise Exception("Old trade ID not found")
        detail= {"Tsid":self.trades[old_trade_id],
                 "direction":self.trades[old_trade_id]["direction"],
                 "amount": details["amount"],
                 "level": details['level'],
                 "full_details": details,
                 "entryTime": details["openTimestamp"],
                 "asset": details["asset"]
                 }
        for field in detail:
            if detail.get(field) is None:
                raise Exception("Missing trade detail field")
        try:
            if self.remove_trade(old_trade_id):
                self.trades[trade_id]= detail
            return self.trades[trade_id]
        except:
            return None
class SIGNAL_DETAILS(BaseModel):
    # map Tsid to signal details
    Signals: dict = Field(default_factory=dict)

    def parse_signal(self,text:str = "")->dict:
        global risk_management
        parsed_data = parse_macrodroid_trade_data(text)
        logger.info(f"Parsed trade data: {parsed_data}")  
        if not parsed_data.get("asset") or not parsed_data.get("direction") or not parsed_data.get("time") or not parsed_data.get("signal_provider") or not parsed_data.get("timezone"):
            logger.error("Failed to parse essential trade data (asset, direction, entry time, signal provider, or timezone) from notification. Aborting trade attempt.")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Failed to parse essential trade data (asset, direction, entry time, signal provider, or timezone) from notification.")

        asset_name_for_po = parsed_data["asset"]
        direction = parsed_data["direction"]
        entryTime = parsed_data["time"]
        signal_provider = parsed_data["signal_provider"]
        timezone = parsed_data["timezone"]
        logger.info(f"\n\n -------Parsed trade data:----------\n--Asset: {asset_name_for_po}\n--Direction: {direction}\n--Entry Time: {entryTime}\n--Signal Provider: {signal_provider}\n--Timezone: {timezone}\n-----------------------------------\n\n ")

        if not direction.upper() in {"CALL", "PUT", "BUY", "SELL"}:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid direction value. Expected 'CALL', 'PUT', 'BUY', or 'SELL'.")

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
            raise e

        logger.info(f"Signal entry time {signal_dt_in_signal_tz.tzinfo}: {entryTime}. Calculated local target entry time: {target_local_dt.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        # Allow a small buffer for late signals, e.g., up to 5 seconds past target entry time.
        if current_local_dt > target_local_dt + timedelta(seconds=5):
            logger.warning(f"Signal for {asset_name_for_po} {direction} (Entry: {entryTime}) arrived late. "
                           f"Current local time: {current_local_dt.strftime('%d-%m-%Y %H:%M:%S')}, Target local time: {target_local_dt.strftime('%d-%m-%Y %H:%M:%S')}. "
                           f"Skipping trade.")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail= {"message": "Signal arrived too late, trade skipped."})

        logger.info(f"New signal received:{asset_name_for_po} {direction}. Initiating a new trade sequence. Initial Amount: ${risk_management.initial_amount}")

        try:
            if self.get_signal(signal_provider=signal_provider,entry_time=target_local_dt) == {"error":"Signal not found"}:
                new_signal = self.add_new_signal(signal_provider=signal_provider,entry_time=target_local_dt,direction=direction.upper(),asset=asset_name_for_po)
            else:
                logger.warning(f"Signal for {asset_name_for_po} {direction} at {entryTime} from {signal_provider} already exists. Skipping duplicate signal.")
                return {"status": "skipped", "message": "Signal already exists, trade skipped."}
        except (Exception,KeyboardInterrupt) as e:
            logger.error(f"Error placing trade for {asset_name_for_po} {direction}: {e}", exc_info=True)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error placing trade: {e}")

        # entry block
        time_to_wait_seconds = (target_local_dt - datetime.now(LOCAL_TIMEZONE)- timedelta(milliseconds=0)).total_seconds()

        if time_to_wait_seconds > 0:
            logger.info(f"Waiting {time_to_wait_seconds:.2f} seconds until target entry time: {target_local_dt.strftime('%H:%M:%S')}")
            return {"status":"wait","message":f"Waiting {time_to_wait_seconds:.2f} seconds until target entry time: {target_local_dt.strftime('%H:%M:%S')}","entry_time":target_local_dt, "provider": signal_provider, "asset": asset_name_for_po, "direction": direction.upper(),"Tsid": new_signal["Tsid"]}
        else:
            logger.info(f"Signal arrived exactly at or slightly past target entry time ({current_local_dt.strftime('%H:%M:%S')} vs {target_local_dt.strftime('%H:%M:%S')}). Placing trade immediately.")
            return {"status":"enter","message":f"Signal arrived exactly at or slightly past target entry time ({current_local_dt.strftime('%H:%M:%S')} vs {target_local_dt.strftime('%H:%M:%S')}). Placing trade immediately.","target_entry_time":target_local_dt, "signal_provider": signal_provider, "asset": asset_name_for_po, "direction": direction.upper()}
        
    def add_new_signal(self,signal_provider:str,entry_time:datetime,direction:str,asset:str)->dict:
        Tsid = str(f"{signal_provider}{entry_time}")
        if Tsid in self.Signals:
            return {"error":"Signal already exists"}
        if signal_provider and entry_time and direction:
            self.Signals[Tsid] = {"signal_provider":signal_provider,"entry_time":entry_time,"direction":direction,"asset":asset}
            return {"Tsid":Tsid,"signal":self.Signals[Tsid]}
        else:
            return {"error":"Missing required fields"}
    
    def get_signal(self,Tsid:str="",signal_provider:str="",entry_time:datetime|None=None, returnAll:bool=False)->dict|None:
        if returnAll:
            return self.Signals
        if Tsid == "":
            Tsid = str(f"{signal_provider}{entry_time}")
        if Tsid not in self.Signals:
            return {"error":"Signal not found"}
        return {"Tsid":Tsid,"signal":self.Signals[Tsid]}
    
    def remove_signal(self,Tsid:str="",signal_provider:str="",entry_time:datetime=datetime.now())->dict|None:
        if Tsid == "":
            Tsid = str(f"{signal_provider}{entry_time}")
            if Tsid not in self.Signals:
                return {"error":"Signal not found"}
        if not self.get_signal(Tsid=Tsid) :
            return {"error":"Signal not found"}
        try:
            return self.Signals.pop(Tsid)
        except Exception as e:
            raise e


class ACCOUNT_DETAILS(BaseModel):
    balance: float|None = None
    P_n_L_day: float|None = None
    lifespan: float|None = None
    async def update_balance(self,api):
        self.balance = await api.balance()

account_details = ACCOUNT_DETAILS()
risk_management = RISK_MANAGEMENT()
Signals = SIGNAL_DETAILS()
trade_details = TRADE_DETAILS()
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
    if set_risk_management == "y":
        for _ in range(3):
            intial_amount = input("Enter initial amount: ").strip()
            martingale_levels = input("Enter martingale levels: ").strip()
            martingale_multiplier = input("Enter martingale multiplier: ").strip()
            timeframe = input("Enter timeframe: ").strip()
            buffer_time = input("Enter buffer time: ").strip()
            if intial_amount and martingale_levels and martingale_multiplier and timeframe and buffer_time:
                risk_management = RISK_MANAGEMENT(
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
                logger.info(f"\n\n\n== Risk management values == \n - Initial entry amount: ${risk_management.initial_amount}\n - max martingale level: {risk_management.martingale_levels}\n - Martingale multiplier: {risk_management.martingale_multiplier}\n - Buffer time: {risk_management.buffer_time}\n - Timeframe: {risk_management.timeframe}\n\n-----use POST : /set_risk_management to change settings \n\n") #type: ignore
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
# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=["*"],  # allow all origins including 'null'
#     allow_credentials=True,
#     allow_methods=["*"],
#     allow_headers=["*"],
# )

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
    global api,open_trades,risk_management,trade_details
    # Ensure integer values are passed to get_candles (period and offset must be ints)
    period = int(risk_management.timeframe) // (int(risk_management.timeframe)//10)
    if period <= 0:
        period = 1
    offset = period * 5
        
    try:
        openTrades = await api.opened_deals()
        # print(f"Fetched open trades: {openTrades}\n\n\n")
        # # handle dict or list responses from the API
        trades_list = []
        for tid,data in openTrades.items(): #type: ignore
            trade_detail = trade_details.get_trade(trade_id=data.get("id"))
            
            logger.info(openTrades[data.get("id")])
            trades_list.append({
                "trade_id": data.get("id"),
                "asset": data.get("asset"),
                "amount": data.get("amount"),
                "direction": trade_detail.get("direction"),
                "profit": data.get("profit"),
                "openedTime": data.get("openTime"),
                "open_price": data.get("openPrice"),
                # current_price may be non-serializable depending on API; include as-is and let caller handle
                "current_price": await api.get_candles(data.get("asset"), period, offset) #type: ignore
            })
        # print(f"Compiled open trades list: {trades_list}")
        return JSONResponse(status_code=status.HTTP_200_OK, content={"open_trades": trades_list})
    except (Exception, KeyboardInterrupt) as e:
        logger.error(f"Error fetching open trades: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error fetching open trades: {e}")

@app.get("/current_signals", response_class=JSONResponse)
async def get_current_signals():
    global Signals
    signals_get = Signals.get_signal(returnAll=True)
    signal_list = []
    for Tsid,data in signals_get.items(): #type: ignore
        logger.info(data)
        signal_list.append({
            "signal_provider":data["signal_provider"],
            "entry_time":str(data["entry_time"].strftime("%Y-%m-%d %H:%M:%S")),
            "direction":data["direction"],
            "asset":data["asset"]
        })
        
    return JSONResponse(status_code=status.HTTP_200_OK, content={"signals":signal_list})


    #
    # return JSONResponse(status_code= status.HTTP_200_OK,content={f"message:{Signals.get_signal(returnAll=True)}"})
@app.post("/set_risk_management", response_class=JSONResponse  )
async def set_risk_management(Risk: RISK_MANAGEMENT):
    global risk_management
    if Risk.initial_amount and Risk.martingale_levels and Risk.martingale_multiplier and Risk.buffer_time and Risk.timeframe:
        risk_management.initial_amount = Risk.initial_amount
        risk_management.martingale_levels = Risk.martingale_levels
        risk_management.martingale_multiplier = Risk.martingale_multiplier
        risk_management.buffer_time = Risk.buffer_time
        risk_management.timeframe = Risk.timeframe
        return JSONResponse(status_code= status.HTTP_200_OK,content={f"message": "Risk managment values successfully set to: {risk_management}"})
    else:
        return JSONResponse(status_code= status.HTTP_400_BAD_REQUEST,content={f"message": "Risk managment values not set.Please ensure schema : {initial_amount,martingale_levels,martingale_multiplier,buffer_time,timeframe}"})
@app.post("/get_risk_management", response_class=JSONResponse)
async def get_risk_management():
    global risk_management
    return JSONResponse(status_code= status.HTTP_200_OK,content={f"message": f"Risk managment values: {risk_management}"})
@app.post("/trade_signal")
async def trade_signal_webhook(request: Request)->JSONResponse:
    global api,risk_management,account_details,Signals,trade_datails
    account_details.balance = await api.balance()
    raw_data = (await request.body()).decode('utf-8')
    logger.info(f"\n\nReceived raw data from notification: {raw_data}\n\n")
    try:
        trade_data = Signals.parse_signal(text=raw_data)
    except (Exception,KeyboardInterrupt) as e:
        logger.error(f"Error parsing trade signal: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error parsing trade signal: {e}")
    try:
        asyncio.create_task(trade_details.take_trade(trade_data=trade_data))
    except (Exception,KeyboardInterrupt) as e:
        logger.error(f"Error taking trade: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Error taking trade: {e}")
    return JSONResponse(status_code=status.HTTP_200_OK, content={"message": "Trade signal received and processed successfully."})
    
async def manage_martingale(trade_id:str):
    global api,risk_management,account_details,trade_details,Signals
    logger.info(f"waiting for trade to end: {trade_id}")
    current_trade = trade_details.get_trade(trade_id=trade_id)
    logger.info(f"current trade details: {current_trade}")
    try:
        status = await api.check_win(trade_id)
        result = status["result"]
    except (Exception,KeyboardInterrupt) as e:
        logger.error(f"Error checking trade result for {trade_id}: {e}", exc_info=True)
        return {"status":"error", "message": f"Error checking trade result: {e}"}
    logger.info(status)
    if result.upper() == "LOSS":
        account_details.P_n_L_day = account_details.P_n_L_day-status["amount"]
        account_details.lifespan = account_details.lifespan -status["amount"]
        current_trade["level"] = current_trade["level"] + 1
        if current_trade["level"] > risk_management.martingale_levels:
            logger.warning(f"Max martingale levels reached for trade {trade_id}. Ending martingale sequence.")
            Signals.remove_signal(Tsid=current_trade["Tsid"])
            trade_details.remove_trade(trade_id)
            return {"status":"error", "message": "Max martingale levels reached.", "id": trade_id, "Tsid": current_trade["Tsid"]}
        logger.info(f"Trade {trade_id} lost. Initiating martingale sequence. level: {int(current_trade["level"])}")#type: ignore
        new_amount = current_trade["amount"] * risk_management.martingale_multiplier
        current_trade["amount"] = new_amount
        logger.info(f"Placing martingale trade level {current_trade["level"]} for amount: ${new_amount}")
        try:
            if current_trade["direction"].upper() == "BUY" or current_trade["direction"].upper() == "CALL": #type: ignore
                (buy_id, Details) = await api.buy(
                    asset=current_trade["full_details"]['asset'], 
                    amount= new_amount, 
                    time= risk_management.timeframe, 
                    check_win=False )
            elif current_trade["direction"].upper() == "SELL" or current_trade["direction"].upper() == "PUT":
                (buy_id, Details) = await api.sell(
                    asset=current_trade["full_details"]['asset'], 
                    amount= new_amount, 
                    time= risk_management.timeframe, 
                    check_win=False )
            
                
        except (Exception,KeyboardInterrupt) as e:
            logger.error(f"Error placing martingale trade for {current_trade['asset']} {current_trade["direction"]}: {e}", exc_info=True)
            return {"status":"error", "message": f"Error placing martingale trade: {e}","id": current_trade["id"], "Tsid": current_trade["Tsid"]}
        logger.info(f"\n\n======Martingale Trade placed successfully.=======\n -Trade ID: {buy_id}\n-Details: {Details}\n\n")
        Details['level'] = current_trade["level"]
        Details['direction'] = current_trade["direction"]
        Details['Tsid'] = current_trade["Tsid"]
        Details["full_details"] = Details
        trade_details.update_trade(old_trade_id=trade_id,trade_id=buy_id,details=Details)
        status_results = await manage_martingale(trade_id=buy_id)
        return status_results
    else:
        logger.info(f"Trade {trade_id} won or tied. Martingale sequence completed.")
        print(f"==trade result==\n -Asset:{current_trade["full_details"]['asset']}\n -lastest amount: {current_trade["amount"]}\n -martingale level: {current_trade["level"]}\n -profit/loss: {status["profit"]}\n")
        account_details.P_n_L_day = account_details.P_n_L_day + status["profit"]
        account_details.lifespan = account_details.lifespan + status["profit"]
        trade_details.remove_trade(trade_id)
        Signals.remove_signal(Tsid=current_trade["Tsid"])
        return {"status":"success", "message": f"Trade {trade_id} won or tied.", "id": trade_id, "Tsid": current_trade["Tsid"]} 
    


    
async def reset_P_n_L_day():
    global account_details
    while True:
        now = datetime.now(pytz.timezone(risk_management.local_timezone))
        next_reset = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        wait_seconds = (next_reset - now).total_seconds()
        await asyncio.sleep(wait_seconds)
        account_details.P_n_L_day = 0