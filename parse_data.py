import re
import logging

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

def parse_macrodroid_trade_data(text: str)->dict:
    """
    Extracts trading signal details from raw text, ignoring emojis and extra lines.
    """
    # 1. Define Regex Patterns
    # Asset: Looks for 3 letters, forward slash, 3 letters (e.g., EUR/USD)
    # The \b ensures we don't match inside other words, but ignores emojis.
    asset_pattern = r'\b([a-zA-Z]{3}/[a-zA-Z]{3})\b'
    
    # Time: Looks for HH:MM (e.g., 19:55)
    time_pattern = r'\b(\d{1,2}:\d{2})\b'
    
    # Direction: buy/sell/call/put (case insensitive)
    direction_pattern = r'\b(buy|sell|call|put)\b'
    
    # Provider: Adjusted to match 'signal_provider="value"' based on your example
    provider_pattern = r'signal_provider="(.+?)"'
    
    timezone_pattern = r'timezone="(Etc/GMT[+-]\d{1,2})"'

    # 2. Execute Searches
    asset_match = re.search(asset_pattern, text)
    time_match = re.search(time_pattern, text)
    direction_match = re.search(direction_pattern, text, re.IGNORECASE)
    provider_match = re.search(provider_pattern, text)
    
    timezone_match = re.search(timezone_pattern, text)

    # 3. Construct Dictionary
    result = {
        "asset": asset_match.group(1).upper().replace("/","") if asset_match else None,
        "time": time_match.group(1) if time_match else None,
        "direction": direction_match.group(1).lower() if direction_match else None,
        "signal_provider": provider_match.group(1) if provider_match else None,
        "timezone": timezone_match.group(1) if timezone_match else None
    }

    return result

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