import logging
import re

logger = logging.getLogger(__name__)

# def parse_macrodroid_trade_data(notification_text: str) -> dict:
#     """
#     Parses trade data from a MacroDroid notification text.

#     Args:
#         notification_text: The full text of the MacroDroid notification.

#     Returns:
#         A dictionary containing parsed trade data (asset_name_for_po, direction, entryTime).
#         Returns an empty dictionary or a dictionary with None values for fields that couldn't be parsed.
#     """
#     parsed_data = {}

#     # --- 1. Parse Asset Name (Currency Pair) ---
#     # Matches patterns like "GBP/AUD", "EUR/USD", including country flags
#     # Example: 🇬🇧 GBP/AUD 🇦🇺 OTC or EUR/USD
#     asset_match = re.search(r'(?:[A-Z]{2,3}\s*\/[A-Z]{2,3}|[A-Z]{6})(?=\s*OTC)?', notification_text, re.IGNORECASE)
#     if asset_match:
#         asset = asset_match.group(0).replace('/', '').strip().upper() # Remove slash and spaces
#         # Check if OTC is mentioned in the full notification text
#         if re.search(r'OTC', notification_text, re.IGNORECASE):
#             asset_for_po = f"{asset}_otc"
#             logger.info(f"Detected Asset: {asset_match.group(0)} -> PO API Name: {asset_for_po}")
#         else:
#             asset_for_po = asset
#             logger.info(f"Detected Asset: {asset_match.group(0)} -> PO API Name: {asset_for_po} (Non-OTC)")
#         parsed_data['asset_name_for_po'] = asset_for_po
#     else:
#         logger.warning("Could not detect Asset from notification text.")

#     # --- 2. Parse Direction (BUY/SELL) ---
#     # Matches 'BUY' or 'SELL', possibly with leading/trailing symbols like '🟩'
#     direction_match = re.search(r'(?:🟩\s*BUY|🟥\s*SELL|BUY|SELL)', notification_text, re.IGNORECASE)
#     if direction_match:
#         direction_raw = direction_match.group(0).replace('🟩', '').replace('🟥', '').strip().upper()
#         if 'BUY' in direction_raw:
#             direction_for_po = 'CALL'
#             logger.info(f"Detected Direction: {direction_raw} -> {direction_for_po}")
#         elif 'SELL' in direction_raw:
#             direction_for_po = 'PUT'
#             logger.info(f"Detected Direction: {direction_raw} -> {direction_for_po}")
#         else:
#             direction_for_po = None
#             logger.warning(f"Detected direction '{direction_raw}' but could not map to CALL/PUT.")
#         parsed_data['direction'] = direction_for_po
#     else:
#         logger.warning("Could not detect Direction from notification text.")

#     # --- 3. Parse Entry Time ---
#     # Matches patterns like 'Entry at 04:25' or just '04:25' if it's clearly a time.
#     # Prioritize 'Entry at HH:MM' or 'Expiration HH:MM'
#     entry_time_match = re.search(r'(?:Entry at|Expiration)\s*(\d{2}:\d{2})', notification_text)
#     if entry_time_match:
#         entry_time = entry_time_match.group(1)
#         logger.info(f"Detected Entry Time: {entry_time}")
#         parsed_data['entryTime'] = entry_time
#     else:
#         # Fallback for just HH:MM if not explicitly an "Entry at" or "Expiration" time
#         # This is less robust and might pick up other times, but handles simpler formats
#         time_only_match = re.search(r'\b(\d{2}:\d{2})\b', notification_text)
#         if time_only_match:
#             entry_time = time_only_match.group(1)
#             logger.info(f"Detected (fallback) Entry Time: {entry_time}")
#             parsed_data['entryTime'] = entry_time
#         else:
#             logger.warning("Could not detect Entry Time from notification text.")

#     if not parsed_data.get("asset_name_for_po") or not parsed_data.get("direction") or not parsed_data.get("entryTime"):
#         logger.warning("No essential trade data could be parsed from the notification. Check signal format and regex patterns.")
        
#     logger.info(f"Successfully parsed raw notification into: {parsed_data}")
#     return parsed_data

def parse_macrodroid_trade_data(text: str) -> dict[str, str | None]:
    """Parse the common signal format from MacroDroid or Telegram.

    Telegram posts often omit the explicit ``signal_provider`` and ``timezone``
    fields, so those values can be filled from environment configuration.
    Missing trading directions and entry times are never guessed.
    """
    asset_match = re.search(r"\b([A-Za-z]{3})\s*/\s*([A-Za-z]{3})(?:\s+(OTC))?\b", text)
    time_match = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", text)
    direction_match = re.search(r"\b(buy|sell|call|put)\b", text, re.IGNORECASE)
    provider_match = re.search(r"signal_provider\s*=\s*[\"']([^\"']+)[\"']", text, re.IGNORECASE)
    timezone_match = re.search(r"timezone\s*=\s*[\"']([^\"']+)[\"']", text, re.IGNORECASE)

    asset = None
    if asset_match:
        asset = f"{asset_match.group(1)}{asset_match.group(2)}".upper()
        if asset_match.group(3) or re.search(r"\bOTC\b", text, re.IGNORECASE):
            asset = f"{asset}_otc"

    direction = direction_match.group(1).upper() if direction_match else None
    if direction == "BUY":
        direction = "CALL"
    elif direction == "SELL":
        direction = "PUT"

    return {
        "asset": asset,
        "time": f"{time_match.group(1)}:{time_match.group(2)}" if time_match else None,
        "direction": direction,
        "signal_provider": provider_match.group(1).strip() if provider_match else None,
        "timezone": timezone_match.group(1).strip() if timezone_match else None,
    }

# --- Testing with your Example ---

# raw_text = """
# 🇪🇺 EUR/USD 🇺🇸 OTC
# 🕘 Expiration 5M
# ⏺ Entry at 19:55
# 🟩 BUY
# signal_provider="john_doe"
# timezone="Etc/GMT+4"
# """

# parsed_data = parse_trading_signal(raw_text)
# print(parsed_data)