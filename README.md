**Project Layout**
- **`main.py`**: FastAPI app and primary logic (endpoints, trade lifecycle, PocketOption client integration).
- **`scraper.py`**: scripts used to fetch or store credentials (e.g., SSID) required by the PocketOption client.
- **`parse_data.py`**: parsing helper for MacroDroid notification payloads (parses asset/time/direction/provider/timezone).
- **`measure_latency.py`**, **`test.py`**: misc utilities and test harnesses.
- **`ui/`**: simple static UI served at `/ui` (contains `index.html`, `script.js`, `styles.css`).
- **`Macrodroid/MacroDroid.mdr`**: MacroDroid export file (contains macros, variables, and custom widgets). Import into MacroDroid.
- **`drivers/`**: download edge browser driver and insert in this file if driver is outdated.

**Purpose**
- This project receives trading signals (via MacroDroid -> webhook), parses them, and places trades on Pocket Option using an async API client. The FastAPI server exposes a small set of endpoints for status and webhook intake.

**Endpoints & Functions (what exists)**
- `GET /` : serves UI index (redirects to `/ui/` when `ui/index.html` exists).
- `GET /ui/script.js` and `GET /ui/styles.css` : serve static UI files.
- `GET /account_details` : returns the current Pocket Option balance and basic account PnL info.
- `GET /open_trades` : lists currently opened trades (tries to query the PO client).
- `GET /current_signals` : returns signals currently held in memory.
- `POST /set_risk_management` : set martingale/size/timeframe settings (expects the `RISK_MANAGEMENT` schema).
- `POST /get_risk_management` : returns current risk settings (currently implemented as POST in `main.py`).
- `POST /trade_signal` : webhook endpoint MacroDroid should post to; parses incoming payload, validates, and schedules trade execution.

**Endpoints & Features That Still Need Implementation / Improvement (TODOs)**
- **Authentication/Validation for webhooks**: currently `POST /trade_signal` trusts incoming payloads. Add a simple secret token or signature check (recommended).
- **`/closed_trades` endpoint**: commented out in `main.py`. Re-enable and return historical/closed trade data.
- **Persisting state**: Signals, trade_details and closed_trades are in-memory. Add persistence (SQLite/JSON/Redis) to survive restarts.
- **Better error handling & retries around PocketOption API**: some reconnect logic exists but should be hardened and logged more granularly.
- **Unit tests / CI**: add tests for `parse_data.py`, `parse_signal()` and critical endpoints.
- **Dockerfile**: create a Dockerfile for easier deployment.
- **closed_trades endpoint**: implement an endpoint to retrieve closed trades for auditing and debugging and `ui/script.js` to display them  in the ui.
- **set risk_management**: update ui to implement setting risk management.
- **improve ui**: enhance the web UI to show more stats, trade history, and allow manual signal posting for testing.

**MacroDroid Setup (import & variables)**
- Import file: open MacroDroid and import the `Macrodroid/MacroDroid.mdr` file from this repo.
- How to import (MacroDroid):
  - Open MacroDroid app on the Android device.
  - Skip the intro if it shows up, then press the Home button in the bottom-left.
  - Choose **Import** and navigate to the `Macrodroid/MacroDroid.mdr` file (copy it to your device first or access via a shared folder).
  - Import the file and grant any prompts.
  - After import, open **Variables** in MacroDroid and update the following global variables:
	 - `ngrok_url` : the public Ngrok forwarding URL (see below) plus the `trade_signal` path if needed (example: `https://<your-id>.ngrok.io/trade_signal`).
	- `signal_provider` : (optional) default provider name this device will report as.
	- `timezone` : must be in pytz format (example: `Etc/GMT-2` for GMT+2). This is used when MacroDroid posts the signal so the server can convert entry times correctly.
   - more info in `Macrodroid/README.md`.

**MacroDroid Custom Widgets**
- This MacroDroid export includes custom widgets you can add to your Home screen: `test signal`, `go to web ui`, and `quick view`.
- `test signal` : sends a test notification / webhook to make sure your server receives and parses a signal correctly.
- `go to web ui` : opens the web UI served by this app (useful to monitor signals and trades).
- `quick view` : a compact widget that shows key account values (balance, PnL) by calling the local `/account_details` endpoint.

**Environment & Setup (Windows / PowerShell)**
1. Install dependencies (choose one),Create & activate a Python virtual environment (example using `venv`):
	- Create venv (if not present): `uv sync` then Activate in PowerShell or your console: `./.venv/Scripts/Activate.ps1`
	  - If you are using a different environment manager, activate accordingly.
        - usualy its `python -m venv .venv` to create
        - then `.\.venv\Scripts\Activate.ps1` to activate in powershell or `source .venv/bin/activate` in bash
        -install dependencies with `pip install -r requirements.txt`
2. Ensure required env values are present (example `.env`):
	- `ssid` : required by the PocketOption client (used inside `main.py` lifespan to connect). You can create it via `scraper.py` or set it manually.
3. Run the app with Uvicorn (replace PORT):
	- `uvicorn main:app --port <PORT>`

**Ngrok (webhook) setup**
- Start ngrok on the same machine and forward the port you run the app on, e.g.: `ngrok http <PORT>`.
- Copy the public forwarding URL (e.g. `https://<id>.ngrok.io`) and paste into MacroDroid variable `ngrok_url`. If MacroDroid expects a path, append `/trade_signal`.
- Use the `test signal` MacroDroid widget to send a test webhook and verify the FastAPI logs show the incoming request.

**Test signal**
 From MacroDroid use `test signal` widget to verify webhook reception.

**Notes & Recommendations**
- Secure the webhook: add a simple header token or signature to MacroDroid posts and validate in `POST /trade_signal`.
- Add persistence for `closed_trades` and `Signals` to help debugging and record keeping.
---
File included for MacroDroid import: `Macrodroid/MacroDroid.mdr`
UI available at `http://localhost:<PORT>/ui/` after starting `uvicorn`.

**license: MIT**

