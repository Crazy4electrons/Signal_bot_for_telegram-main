# Pocket Option Trading Bot

Automated trading bot for Pocket Option that executes trades based on Telegram signals using FastAPI, Selenium, and async trading logic with Martingale support. The bot uses MacroDroid on Android to forward Telegram signals to the trading server through ngrok.

## Features

- **Automated SSID/UID Scraper:** Uses Selenium to log in and extract Pocket Option session credentials.
- **FastAPI Trading Server:** Receives trade signals via webhook and executes trades using the `pocketoptionapi-async` library.
- **Martingale Strategy:** Supports up to 2 Martingale levels for trade recovery.
- **Timezone Handling:** Converts signal times from New York (GMT-4) to local time (Africa/Windhoek).
- **Test Signal Sender:** Easily test your webhook endpoint with custom signals.

## Project Structure

```
├── .env                    # Environment variables and credentials
├── drivers/               # WebDriver executables directory
├── shared_data/          # Shared data storage
├── Macrodroid/           # MacroDroid macro files
│   └── MacroDroid.mdr    # Importable macro for signal forwarding
├── scraper.py            # Session credential scraper
├── scraper_debug.py      # Debug version of scraper with verbose logging
├── run_scraper.ps1       # PowerShell script to run scraper with credentials
├── trader.py             # Trading logic implementation
├── main.py              # FastAPI server and Martingale strategy
├── parse_data.py        # Signal parsing utilities
├── test.py              # Signal testing utility
├── requirements.txt     # Python package dependencies
└── pyproject.toml       # Project configuration
```

## Prerequisites

1. **Install Python 3.13+**  
   Make sure you have Python 3.13 or higher installed.

2. **Install ngrok**
   - Download ngrok from [ngrok.com](https://ngrok.com)
   - Sign up for an account
   - Add your authentication token:
     ```sh
     ngrok config add-authtoken YOUR_TOKEN
     ```

3. **Set up MacroDroid**
   - Install MacroDroid on your Android device
   - Import the provided macro from the `Macrodroid/MacroDroid.mdr` file
   - Configure the Notification Trigger to filter your signal channel/group
   - Edit the HTTP Action block with your ngrok webhook URL
   - Ensure MacroDroid has notification access permissions

## Setup

2. **Install Dependencies**  
   Use [uv](https://github.com/astral-sh/uv) or pip:
   ```sh
   uv pip install -r requirements.txt
   # or
   pip install -r requirements.txt
   ```

3. **Configure Edge WebDriver**  
   Download [msedgedriver.exe](https://developer.microsoft.com/en-us/microsoft-edge/tools/webdriver/) and place it in the `drivers/` folder.  
   Update the path in `scraper.py` and `scraper_debug.py` if needed.

4. **Set Up Environment Variables**  
   - Copy `.env` and fill in your credentials if needed.
   - Or use `run_scraper.ps1` to set `PO_EMAIL` and `PO_PASSWORD` for scraping.

5. **Scrape SSID/UID**  
   Run the scraper to obtain valid session credentials:
   ```sh
   uv run scraper.py
   ```
   Follow the prompts to log in and select account type (DEMO/REAL).  
   The script will update `.env` with `SSID` and `UID`.

6. **Start Trading Server**  
   Run the trading API (choose one):
   ```sh
   uvicorn trader:app --reload
   # or
   uvicorn main:app --reload
   ```
   On startup, select DEMO or REAL account.

7. **Send Test Signals**  
   Use `test.py` to send simulated signals:
   ```sh
   python test.py
   ```
   Make sure to update the webhook URL in `test.py` to match your server (e.g., via Ngrok).

## Usage

- **Webhook Endpoint:**  
  POST plain text signals to `/trade_signal` (see `test.py` for format).
- **Signal Format:**
  ```
  🇪🇺 EUR/USD 🇺🇸 OTC
  🕘 Expiration 5M
  ⏺ Entry at 19:27
  🟩 BUY
  ```
  other formats might work cause the regex is only serching for the currency pair,entry time and trade direction.
- **Martingale:**  
  The bot will automatically re-enter trades up to 2 times if the previous trade is predicted to lose, based on candle analysis.

## Running the Bot

1. **Start the Scraper First**
   ```sh
   uv run scraper.py
   # or
   python scraper.py
   ```
   Note: You'll need to complete the reCAPTCHA manually a few times initially. After that, the browser will be marked as "human" and reCAPTCHA should appear less frequently.

2. **Start ngrok** (in a new terminal)
   ```sh
   .\\ngrok http 8000
   ```
   Follow the web interface link and copy the generated URL (e.g., `https://XXX.ngrok-free.app`) and update it in your MacroDroid HTTP Action. Note: The URL changes every time you restart ngrok unless you have a paid license.

3. **Start the Trading Server** (in another terminal)
   ```sh
   uvicorn main:app --reload
   ```

4. **Configure MacroDroid**
   - Open MacroDroid on your phone
   - Load the imported macro
   - Update the webhook URL with your ngrok URL
   - Enable the macro to start forwarding Telegram signals

## Notes

- **Edge WebDriver:**  
  Only Microsoft Edge is supported for scraping. Make sure the driver version matches your browser.
- **Session Refresh:**  
  The scraper will refresh SSID/UID every 12 hours by default.
- **Account Type:**  
  Always select the same account type (DEMO/REAL) in both the scraper and trading server.
- **Multiple Terminals:**
  You need three separate terminals running simultaneously:
  1. Scraper (when refreshing session)
  2. ngrok (for webhook tunnel)
  3. Trading server (FastAPI)
- **Security:**
  - Never share your SSID/UID or other credentials
  - Use a demo account first to test your setup
  - Monitor the bot's performance before using real funds
- **Debugging:**
  - Use `scraper_debug.py` for verbose logging
  - Check terminal outputs for any errors
  - Verify webhook URLs in both ngrok and MacroDroid

## License

MIT License

---

**Disclaimer:** This project is for educational purposes. Use at your own risk.
