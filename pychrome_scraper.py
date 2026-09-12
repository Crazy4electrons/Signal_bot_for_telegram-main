"""Capture a Pocket Option session from a manually logged-in Edge tab with pychrome."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import pychrome
from dotenv import load_dotenv

load_dotenv()


def save_to_env(key: str, value: str) -> None:
    env_path = Path(__file__).with_name(".env")
    lines = env_path.read_text().splitlines(keepends=True) if env_path.exists() else []
    replacement = f"{key}='{value}'\n"
    for index, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[index] = replacement
            break
    else:
        lines.append(replacement)
    env_path.write_text("".join(lines))


def extract_auth(payload: str) -> tuple[str, str, str] | None:
    payload = payload.strip()
    if not payload.startswith("42"):
        return None
    try:
        message = json.loads(payload[2:])
    except json.JSONDecodeError:
        return None
    if not isinstance(message, list) or len(message) < 2 or message[0] != "auth":
        return None
    details = message[1]
    if isinstance(details, str):
        try:
            details = json.loads(details)
        except json.JSONDecodeError:
            return None
    if not isinstance(details, dict):
        return None
    if not all(key in details for key in ("session", "isDemo", "uid")):
        return None
    return str(details["session"]), str(details["isDemo"]), str(details["uid"])


def find_tab(browser: pychrome.Browser) -> pychrome.Tab:
    for tab in browser.list_tab():
        target_url = str(tab._kwargs.get("url", ""))
        if tab.type == "page" and "pocketoption.com" in target_url:
            return tab
    raise RuntimeError("No Pocket Option tab found. Open the cabinet page in the CDP Edge profile.")


def launch_edge_if_needed() -> subprocess.Popen[bytes] | None:
    try:
        pychrome.Browser(url="http://127.0.0.1:9222").list_tab()
        return None
    except Exception:
        pass
    edge_binary = os.getenv("EDGE_BINARY") or shutil.which("microsoft-edge-stable") or shutil.which("microsoft-edge")
    if not edge_binary:
        raise RuntimeError("Microsoft Edge was not found; set EDGE_BINARY in .env")
    profile_path = os.getenv("POCKETOPTION_EDGE_PROFILE", os.path.expanduser("~/.pocketoption-edge"))
    process = subprocess.Popen(
        [edge_binary, "--remote-debugging-port=9222", f"--user-data-dir={profile_path}", "https://pocketoption.com/en/cabinet/"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            pychrome.Browser(url="http://127.0.0.1:9222").list_tab()
            return process
        except Exception:
            time.sleep(0.25)
    process.terminate()
    raise RuntimeError("Edge started but its CDP endpoint did not become available")


def collect(account_type: str, timeout_seconds: int, reload_page: bool, launch: bool) -> None:
    browser = pychrome.Browser(url="http://127.0.0.1:9222")
    edge_process = launch_edge_if_needed() if launch else None
    try:
        browser = pychrome.Browser(url="http://127.0.0.1:9222")
        tab = find_tab(browser)
    except Exception:
        if edge_process:
            edge_process.terminate()
        raise
    found = False

    def handle_frame(**event: Any) -> None:
        nonlocal found
        response = event.get("response", {})
        auth = extract_auth(response.get("payloadData", ""))
        if not auth:
            return
        session, is_demo, uid = auth
        if is_demo != ("1" if account_type.upper() == "DEMO" else "0"):
            return
        escaped_session = session.replace('"', '\\"')
        save_to_env(
            "ssid",
            f'42["auth",{{"session":"{escaped_session}","isDemo":{is_demo},"uid":{uid},"platform":2,"isFastHistory":true,"isOptimized":true}}]',
        )
        save_to_env("UID", uid)
        save_to_env("ACCOUNT_TYPE", account_type.upper())
        found = True
        print(f"Captured {account_type.upper()} Pocket Option session for UID {uid}")

    try:
        tab.start()
        tab.Network.enable()
        tab.Network.webSocketFrameReceived = handle_frame
        tab.Network.webSocketFrameSent = handle_frame
        if reload_page:
            tab.Page.enable()
            tab.Page.reload(ignoreCache=True)
        deadline = time.monotonic() + timeout_seconds
        while not found and time.monotonic() < deadline:
            tab.wait(1)
    finally:
        tab.stop()
        if edge_process:
            edge_process.terminate()

    if not found:
        raise RuntimeError(
            "No matching auth frame received. Keep the logged-in cabinet page open "
            "and retry with --reload."
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--account-type", choices=("DEMO", "REAL"), default="DEMO")
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--reload", action="store_true", help="Reload the cabinet to trigger a fresh auth exchange")
    parser.add_argument("--launch", action="store_true", help="Launch the dedicated Edge profile automatically")
    args = parser.parse_args()
    collect(args.account_type, args.timeout, args.reload, args.launch)


if __name__ == "__main__":
    main()
