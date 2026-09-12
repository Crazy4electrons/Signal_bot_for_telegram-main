"""Capture a Pocket Option session from an Edge tab via CDP.

The first run may require manual login in the launched Edge profile. Later runs
reuse that profile without requesting or storing a Pocket Option password.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.request import urlopen

import websockets
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


def find_pocket_option_tab() -> dict[str, Any]:
    with urlopen("http://127.0.0.1:9222/json/list", timeout=5) as response:
        targets = json.load(response)
    for target in targets:
        if target.get("type") == "page" and "pocketoption.com" in target.get("url", ""):
            return target
    raise RuntimeError("No Pocket Option tab found in the remote-debugging Edge session")


def extract_auth(payload: str) -> tuple[str, str, str] | None:
    if not payload.startswith("42"):
        return None
    try:
        message = json.loads(payload[2:])
    except json.JSONDecodeError:
        return None
    if not isinstance(message, list) or len(message) < 2 or message[0] != "auth":
        return None
    details = message[1]
    if not isinstance(details, dict) or not all(key in details for key in ("session", "isDemo", "uid")):
        return None
    return str(details["session"]), str(details["isDemo"]), str(details["uid"])


def browser_debugger_url() -> str:
    with urlopen("http://127.0.0.1:9222/json/version", timeout=5) as response:
        return json.load(response)["webSocketDebuggerUrl"]


def launch_edge_if_needed() -> subprocess.Popen[bytes] | None:
    try:
        browser_debugger_url()
        return None
    except Exception:
        pass

    edge_binary = os.getenv("EDGE_BINARY") or shutil.which("microsoft-edge-stable") or shutil.which("microsoft-edge")
    if not edge_binary:
        raise RuntimeError("Microsoft Edge was not found; set EDGE_BINARY in .env")
    profile_path = os.getenv("POCKETOPTION_EDGE_PROFILE", os.path.expanduser("~/.pocketoption-edge"))
    process = subprocess.Popen(
        [
            edge_binary,
            "--remote-debugging-port=9222",
            f"--user-data-dir={profile_path}",
            "https://pocketoption.com/en/cabinet/",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            browser_debugger_url()
            return process
        except Exception:
            time.sleep(0.25)
    process.terminate()
    raise RuntimeError("Edge started but its CDP endpoint did not become available")


async def collect(account_type: str, timeout_seconds: int, launch: bool) -> None:
    edge_process = launch_edge_if_needed() if launch else None
    target = find_pocket_option_tab()
    socket_url = browser_debugger_url()
    if not socket_url:
        raise RuntimeError("Pocket Option tab has no CDP WebSocket endpoint")

    try:
        async with websockets.connect(socket_url, max_size=None) as socket:
            command_id = 0

            async def command(method: str, params: dict[str, Any] | None = None, session_id: str | None = None) -> dict[str, Any]:
                nonlocal command_id
                command_id += 1
                message: dict[str, Any] = {"id": command_id, "method": method, "params": params or {}}
                if session_id:
                    message["sessionId"] = session_id
                await socket.send(json.dumps(message))
                while True:
                    response = json.loads(await socket.recv())
                    if response.get("id") == command_id:
                        return response

            attached = await command("Target.attachToTarget", {"targetId": target["id"], "flatten": True})
            session_id = attached.get("result", {}).get("sessionId")
            if not session_id:
                raise RuntimeError("Could not attach to the Pocket Option browser tab")
            await command("Network.enable", session_id=session_id)
            await command("Page.enable", session_id=session_id)
            await command("Page.reload", {"ignoreCache": True}, session_id=session_id)

            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout_seconds
            while loop.time() < deadline:
                remaining = max(0.1, deadline - loop.time())
                try:
                    raw_message = await asyncio.wait_for(socket.recv(), timeout=remaining)
                except asyncio.TimeoutError:
                    break
                message = json.loads(raw_message)
                if message.get("sessionId") != session_id:
                    continue
                if message.get("method") not in {"Network.webSocketFrameReceived", "Network.webSocketFrameSent"}:
                    continue
                payload = message.get("params", {}).get("response", {}).get("payloadData", "")
                auth = extract_auth(payload)
                if not auth:
                    continue
                session, is_demo, uid = auth
                expected_demo = "1" if account_type.upper() == "DEMO" else "0"
                if is_demo != expected_demo:
                    continue
                escaped_session = session.replace('"', '\\"')
                save_to_env("ssid", f'42["auth",{{"session":"{escaped_session}","isDemo":{is_demo},"uid":{uid},"platform":2,"isFastHistory":true,"isOptimized":true}}]')
                save_to_env("UID", uid)
                save_to_env("ACCOUNT_TYPE", account_type.upper())
                print(f"Captured {account_type.upper()} Pocket Option session for UID {uid}")
                return
    finally:
        if edge_process:
            edge_process.terminate()

    raise RuntimeError(
        "No matching Pocket Option auth frame received. Confirm the tab is logged in "
        "and retry while the cabinet page is open."
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--account-type", choices=("DEMO", "REAL"), default="DEMO")
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--launch", action="store_true", help="Launch the dedicated Edge profile automatically")
    args = parser.parse_args()
    asyncio.run(collect(args.account_type, args.timeout, args.launch))


if __name__ == "__main__":
    main()
