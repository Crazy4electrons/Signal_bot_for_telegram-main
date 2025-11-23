# test.py
import requests
import json
import time
from datetime import datetime, timedelta

# --- Configuration ---
# IMPORTANT: Replace this with your actual Ngrok HTTPS URL
# ngrok_url = None
WEBHOOK_URL = "http://localhost:3000/trade_signal" # e.g., "https://abcdef12345.ngrok-free.app/trade_signal"
# --- End Configuration ---

# Base notification template
NOTIFICATION_TEMPLATE = """
{asset_emoji} {asset_pair} {country_emoji} OTC
🕘 Expiration 5M
⏺ Entry at {entry_time}
{direction_emoji} {direction_text}
signal_provider="{signal_provider}"
timezone="{timezone}"
"""

def get_asset_emojis(asset_pair:str):
    """Simple mapping for common asset emojis."""
    
    emojis = {
        "EUR/USD": ("🇪🇺", "🇺🇸"),
        "GBP/JPY": ("🇬🇧", "🇯🇵"),
        "USD/JPY": ("🇺🇸", "🇯🇵"),
        "AUD/USD": ("🇦🇺", "🇺🇸"),
        "USD/CHF": ("🇺🇸", "🇨🇭"),
        "GBPCHF": ("🇬🇧", "🇨🇭"), # For assets without slash
        # Add more mappings as needed
    }
    # Handle assets given without a slash (e.g., GBPCHF)
    if '/' not in asset_pair and len(asset_pair) == 6:
        base_pair = asset_pair[:3] + '/' + asset_pair[3:]
        return emojis.get(base_pair, ("❓", "❓"))
    
    return emojis.get(asset_pair, ("❓", "❓"))

def get_direction_emoji(direction_text):
    """Simple mapping for direction emojis."""
    if direction_text.upper() == "BUY":
        return "🟩"
    elif direction_text.upper() == "SELL":
        return "🟥"
    return "❓"

def get_next_5min_interval_time(current_time_str=None):
    """
    Calculates the next 5-minute interval time (HH:MM) based on current local time.
    Useful for simulating subsequent Martingale entries.
    """
    if current_time_str:
        # If a specific time is provided, parse it
        try:
            now = datetime.strptime(current_time_str, "%H:%M")
        except ValueError:
            print("Invalid time format. Using current time.")
            now = datetime.now()
    else:
        now = datetime.now()

    # Add 5 minutes to current time
    next_time = now + timedelta(minutes=5)
    
    # Round down to the nearest 5-minute interval
    # This ensures it aligns with 00, 05, 10, etc. minutes
    # However, your requirement is "enter exactly at 19:27", so we'll just add 5 mins.
    # If you wanted to snap to the next 5-min mark, it would be:
    # next_time_minute = (next_time.minute // 5) * 5
    # next_time = next_time.replace(minute=next_time_minute, second=0, microsecond=0)
    
    return next_time.strftime("%H:%M")


def send_test_signal(asset_pair:str,direction_text:str|None = None):
    """
    Prompts user for signal details, constructs notification, and sends it.
    """
    print("\n--- Send New Test Signal ---")
    
    
    direction_text = input("Enter Direction (BUY or SELL): ").strip().upper() if not direction_text else direction_text.upper()
    
    # Suggest next entry time
    suggested_time = get_next_5min_interval_time()
    entry_time = input(f"Enter Entry Time (HH:MM, e.g., {suggested_time}): ").strip()

    asset_emoji1, asset_emoji2 = get_asset_emojis(asset_pair)
    direction_emoji = get_direction_emoji(direction_text)

    notification_content = NOTIFICATION_TEMPLATE.format(
        asset_emoji=asset_emoji1,
        asset_pair=asset_pair,
        country_emoji=asset_emoji2,
        entry_time=entry_time,
        direction_emoji=direction_emoji,
        direction_text=direction_text,
        signal_provider="john_doe",
        timezone="Etc/GMT+4"
    ).strip()

    print("\n--- Generated Notification Content ---")
    print(notification_content)
    print("------------------------------------")

    try:
        print(f"Sending POST request to: {WEBHOOK_URL}")
        response = requests.post(WEBHOOK_URL, data=notification_content, headers={'Content-Type': 'text/plain'})

        print("\n--- Webhook Response ---")
        print(f"Status Code: {response.status_code}")
        try:
            print(f"Response Body: {json.dumps(response.json(), indent=2)}")
        except json.JSONDecodeError:
            print(f"Response Body (raw): {response.text}")
        print("------------------------")

    except requests.exceptions.ConnectionError as e:
        print(f"\nERROR: Could not connect to the webhook URL. Is Ngrok running and URL correct?")
        print(f"Details: {e}")
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}")

if __name__ == "__main__":
    print("Welcome to the Signal Test Sender!")
    print(f"Signals will be sent to: {WEBHOOK_URL}")
    print("Make sure your `trader.py` and Ngrok are running.")
    asset_pair = input("Enter Asset Pair (e.g., EUR/USD): ").strip().upper()
    direction_text = input("Enter Direction (BUY or SELL): ").strip().upper()
    send_test_signal(asset_pair=asset_pair,direction_text=direction_text)
    while True:
        
        choice = input("\nSend same signal with different time? (y/n): ").strip().lower()
        if choice != 'y':
            break
        send_test_signal(asset_pair=asset_pair,direction_text=direction_text)

    print("Exiting test sender.")