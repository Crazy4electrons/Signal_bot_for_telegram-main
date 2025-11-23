# scraper.py - Automated Scraper for Pocket Option SSID and UID

import os
import json
import time
import re
import logging
import urllib.parse
from typing import cast, List, Dict, Any, Optional

from selenium import webdriver
from selenium.webdriver.edge.service import Service
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from dotenv import load_dotenv # Import dotenv

# Load environment variables from .env file
load_dotenv()

# --- MANUAL EDGE DRIVER PATH ---
# This path must point to your msedgedriver.exe
MANUAL_EDGEDRIVER_PATH = r".\\Drivers\\msedgedriver.exe"
# -------------------------------

# Configure logging for this script.
logging.basicConfig(
    level=logging.INFO, # Changed to INFO for general output
    format='{"timestamp": "%(asctime)s", "level": "%(levelname)s", "module": "%(name)s", "message": "%(message)s"}',
)
logger = logging.getLogger(__name__)


def save_to_env(key: str, value: str):
    """
    Saves or updates a key-value pair in the .env file.
    If the key already exists, its value is updated. Otherwise, the new key-value pair is added.
    Ensures value is enclosed in single quotes.
    """
    env_path = os.path.join(os.getcwd(), ".env")
    lines = []
    found = False

    if os.path.exists(env_path):
        
        with open(env_path, "r") as f:
            for line in f:
                if line.strip().startswith(f"{key}="):
                    lines.append(f"{key}='{value}'\n") # Use single quotes
                    found = True
                else:
                    lines.append(line)

    if not found:
        lines.append(f"{key}='{value}'\n") # Use single quotes

    with open(env_path, "w") as f:
        f.writelines(lines)
    logger.info(f"Successfully saved {key} to .env file.")


def get_pocketoption_session_data(email: str, password: str, account_type: str) -> dict[str, Optional[str]]:
    """
    Automates the process of logging into PocketOption using Microsoft Edge,
    navigating to a specific cabinet page (real or demo), and then scraping
    WebSocket traffic to extract the session ID (SSID) and User ID (UID).
    Returns a dictionary containing 'ssid' and 'uid'.
    """
    logger.info(f"Starting Microsoft Edge browser instance for automated login to {account_type} account...")
    
    edge_options = EdgeOptions()
    edge_options.add_argument("--no-sandbox")
    edge_options.add_argument("--disable-dev-shm-usage")
    edge_options.add_argument("--disable-gpu")
    edge_options.add_argument("--window-size=1280,800")
    edge_options.add_argument("--start-maximized")
    edge_options.add_argument("--log-level=0") # Set Edge's internal logging to verbose
    edge_options.add_argument("--remote-debugging-port=9222")
    edge_options.add_argument("--disable-features=RendererCodeIntegrity")
    edge_options.add_argument("--disable-extensions")
    edge_options.add_argument("--disable-background-networking")

    # Enable performance logging for Edge (CRITICAL for capturing WebSocket traffic)
    edge_options.set_capability("ms:loggingPrefs", {"performance": "ALL"})

    driver = None
    session_data = {"ssid": str(None), "uid": None}

    try:
        service = Service(MANUAL_EDGEDRIVER_PATH)
        driver = webdriver.Edge(service=service, options=edge_options)
        logger.info("Microsoft Edge WebDriver initialized successfully.")

        login_url = "https://pocketoption.com/en/login/"
        
        # Determine target cabinet URL based on selected account type
        if account_type.upper() == "DEMO":
            target_cabinet_url = "https://pocketoption.com/en/cabinet/demo-quick-high-low/"
            expected_is_demo_value = 1 # Expected isDemo value in SSID for demo account
        elif account_type.upper() == "REAL":
            target_cabinet_url = "https://pocketoption.com/en/cabinet/" # General cabinet for real
            expected_is_demo_value = 0 # Expected isDemo value in SSID for real account
        else:
            raise ValueError("Invalid account_type. Must be 'DEMO' or 'REAL'.")

        # Regex to capture the session string (SSID) and the uid from the "auth" message.
        # It handles escaped quotes within the session string and verifies isDemo.
        # Group 1: Full "auth" payload (for SSID env var)
        # Group 2: Session string (raw value for AsyncPocketOptionClient)
        # Group 3: isDemo value (int)
        # Group 4: UID value (int)
        ssid_uid_pattern = re.compile(
            r'42\["auth",\{"session":"((?:\\.|[^"\\])*)",'  # Group 1: Full session string with escapes
            r'"isDemo":(\d),'                               # Group 2: isDemo (0 or 1)
            r'"uid":(\d+),'                                 # Group 3: UID
            r'"platform":\d+,'
            r'"isFastHistory":(?:true|false),'
            r'"isOptimized":(?:true|false)\}\]'
        )
        
        logger.info(f"Navigating to login page: {login_url}")
        driver.get(login_url)

        WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.NAME, "email")))
        WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.NAME, "password")))
        WebDriverWait(driver, 30).until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button[type='submit']")))

        email_field = driver.find_element(By.NAME, "email")
        password_field = driver.find_element(By.NAME, "password")
        login_button = driver.find_element(By.CSS_SELECTOR, "button[type='submit']")

        email_field.send_keys(email)
        password_field.send_keys(password)
        login_button.click()
        logger.info("Login credentials entered and login button clicked.")

        # Wait for successful login and redirection to cabinet/dashboard
        WebDriverWait(driver, 60).until(
            EC.url_contains("cabinet") or EC.url_contains("dashboard") or 
            EC.presence_of_element_located((By.CSS_SELECTOR, ".header-user__name"))
        )
        logger.info("Successfully logged in to Pocket Option website.")

        # Now navigate to the specific target URL within the cabinet to ensure all WebSocket connections are made.
        logger.info(f"Navigating to target cabinet page: {target_cabinet_url}")
        driver.get(target_cabinet_url)

        WebDriverWait(driver, 60).until(EC.url_contains(target_cabinet_url))
        logger.info("Successfully navigated to the target cabinet page.")

        # Give the page some time to load all WebSocket connections and messages.
        time.sleep(20) # Increased sleep to ensure more logs are captured

        performance_logs = cast(List[Dict[str, Any]], driver.get_log("performance"))
        logger.info(f"Collected {len(performance_logs)} performance log entries. Analyzing for SSID and UID...")

        found_full_ssid_string = None
        found_uid = None
        
        # Iterate through the performance logs to find WebSocket frames.
        for i, entry in enumerate(performance_logs):
            try:
                message = json.loads(entry["message"])
                log_method = message["message"]["method"]
                
                # Log all WebSocket frame messages in detail (DEBUG level)
                if log_method == "Network.webSocketFrameReceived" or log_method == "Network.webSocketFrameSent":
                    payload_data = message["message"]["params"]["response"]["payloadData"]
                    logger.debug(f"--- WebSocket Frame ({log_method}) Entry {i} ---")
                    logger.debug(f"Timestamp: {entry['timestamp']}")
                    logger.debug(f"Frame Type: {'Received' if log_method == 'Network.webSocketFrameReceived' else 'Sent'}")
                    logger.debug(f"Payload Data: {payload_data}")
                    logger.debug("-----------------------------------")

                    # Attempt to find the full SSID and UID string using the defined regex pattern.
                    match = ssid_uid_pattern.search(payload_data)
                    if match:
                        extracted_session = match.group(1).replace('\\"', '"') # Unescape quotes
                        extracted_is_demo = int(match.group(2))
                        extracted_uid_str = match.group(3)
                        
                        if extracted_is_demo == expected_is_demo_value:
                            # Construct the full string including the 42 prefix and the JSON structure
                            full_payload_for_env = f'42["auth",{{"session":"{extracted_session.replace("\"", "\\\"")}","isDemo":{extracted_is_demo},"uid":{extracted_uid_str},"platform":2,"isFastHistory":true,"isOptimized":true}}]'
                            
                            found_full_ssid_string = full_payload_for_env
                            found_uid = extracted_uid_str
                            
                            logger.info(
                                f"FOUND SSID and UID IN LOGS FOR {account_type} ACCOUNT."
                                f"SSID: {found_full_ssid_string[:50]}... UID: {found_uid}"
                            )
                            # Do NOT break here. Continue logging other messages to see the full loop.
                            # We will save the *last* found valid SSID and UID.
                        else:
                            logger.warning(f"Found SSID but 'isDemo' ({extracted_is_demo}) did not match expected ({expected_is_demo_value}). Skipping.")


            except json.JSONDecodeError:
                logger.debug(f"Skipping non-JSON log entry {i}.")
            except KeyError as ke:
                logger.debug(f"Skipping log entry {i} due to missing key: {ke}. Entry: {entry}")
            except Exception as e:
                logger.error(f"Error processing log entry {i}: {e}", exc_info=True)


        if found_full_ssid_string and found_uid:
            session_data["ssid"] = found_full_ssid_string
            session_data["uid"] = found_uid
            save_to_env("SSID", found_full_ssid_string)
            save_to_env("UID", found_uid)
            save_to_env("ACCOUNT_TYPE", account_type.upper())
            save_to_env("PO_EMAIL", email)
            save_to_env("PO_PASSWORD", password)
            logger.info(f"Full SSID and UID for {account_type} account successfully extracted and saved to .env.")
        else:
            logger.warning(
                f"Full SSID string and/or UID pattern for {account_type} account not found in WebSocket logs after login."
            )

    except Exception as e:
        logger.error(f"An error occurred during Edge automation: {e}", exc_info=True)
    finally:
        if driver:
            driver.quit()
            logger.info("WebDriver closed.")
    
    return session_data


if __name__ == "__main__":
    email = os.getenv('PO_EMAIL')
    password = os.getenv('PO_PASSWORD') 
    print(f"env_path: {os.path.join(os.getcwd(), ".env")}") 
    print(f"email: {email}, password: {'******' if password else None}")
    refresh_interval_minutes = int(os.getenv('SSID_REFRESH_INTERVAL_MINUTES',24*60*60))
    refresh_interval_seconds = refresh_interval_minutes /60
    
    if not email or not password:
        try:
            i = 0
            while True:
                email = input("PO_EMAIL: ")
                password  = input("PO_PASSWORD: ")
                if email and password:
                    break
                i += 1
                if i == 3:
                    raise Exception("Missing PO_EMAIL or PO_PASSWORD after 3 attempts.")
        except KeyboardInterrupt or exception:
            logger.error("PO_EMAIL and PO_PASSWORD environment variables must be set for the scraper.")
            exit(1)
            
    # --- User Prompt for Account Type ---
    while True:
        user_choice = input("Enter account type to scrape (DEMO/REAL): ").strip().upper()
        if user_choice in ["DEMO", "REAL"]:
            break
        else:
            print("Invalid input. Please enter 'DEMO' or 'REAL'.")
    # --- End User Prompt ---

    while True:
        logger.info(f"Attempting to refresh SSID and UID for {user_choice} account. Next refresh in {refresh_interval_minutes} minutes.")
        session_info = get_pocketoption_session_data(email, password, user_choice) # Pass account_type to the function
        if session_info["ssid"] and session_info["uid"]:
            logger.info(f"SSID and UID extraction completed for {user_choice} account.")
        else:
            logger.error(f"Failed to extract SSID and/or UID for {user_choice} account.")
        if refresh_interval_minutes >60:
            logger.info(f"Waiting {refresh_interval_minutes/60} hours and {refresh_interval_minutes%60} minutes before next SSID refresh attempt.")
        else:
            logger.info(f"Waiting {refresh_interval_minutes} minutes before next SSID refresh attempt.")
            
        time.sleep(refresh_interval_seconds)