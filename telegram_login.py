"""Interactive Telegram user-session login, shared by the setup scripts.

Run this file directly, or let ``fetch_channels.py`` call :func:`ensure_login`
when no authorized session exists yet. The generated session file must stay
private.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient

load_dotenv()

# Seconds to wait for the initial Telegram connection before giving up.
CONNECT_TIMEOUT = float(os.getenv("TELEGRAM_CONNECT_TIMEOUT", "30"))


def _info(message: str) -> None:
    """Report progress on stderr so stdout stays clean for piped/JSON output."""
    print(message, file=sys.stderr, flush=True)


def telegram_credentials() -> tuple[int, str]:
    """Return ``(api_id, api_hash)`` from the environment, with guidance on failure."""
    api_id = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")
    if not api_id or not api_hash:
        raise RuntimeError(
            "Missing TELEGRAM_API_ID or TELEGRAM_API_HASH. Add both values to .env; "
            "create them at https://my.telegram.org under API development tools."
        )
    return int(api_id), api_hash


def session_path() -> str:
    """Return the configured session path, without the ``.session`` suffix."""
    return os.getenv("TELEGRAM_SESSION_PATH", "data/telegram_signal_bot")


def session_file() -> Path:
    """Return the SQLite session file backed by :func:`session_path`."""
    return Path(f"{session_path()}.session")


def build_client() -> TelegramClient:
    """Create a TelegramClient using ``TELEGRAM_SESSION_PATH``."""
    api_id, api_hash = telegram_credentials()
    path = Path(session_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    return TelegramClient(str(path), api_id, api_hash)


async def ensure_login(client: TelegramClient | None = None) -> TelegramClient:
    """Return an authorized client, running the interactive login when needed.

    Telethon prompts on stdin for the phone number, the login code, and the 2FA
    password (if set), so a first-time login must run in an interactive terminal.
    """
    interactive = sys.stdin.isatty()
    if not session_file().exists() and not interactive:
        raise RuntimeError(
            "No Telegram session file and stdin is not a terminal. "
            "Run `python telegram_login.py` interactively first."
        )

    client = client or build_client()
    _info("Connecting to Telegram...")
    try:
        await asyncio.wait_for(client.connect(), timeout=CONNECT_TIMEOUT)
    except (asyncio.TimeoutError, TimeoutError) as exc:
        await client.disconnect()
        raise RuntimeError(
            f"Timed out after {CONNECT_TIMEOUT:.0f}s connecting to Telegram; "
            "check the network connection and retry."
        ) from exc

    if await client.is_user_authorized():
        return client

    if not interactive:
        await client.disconnect()
        raise RuntimeError(
            "No authorized Telegram session and stdin is not a terminal. "
            "Run `python telegram_login.py` interactively first."
        )

    _info("No authorized Telegram session found - starting login.")
    _info("Telethon will ask for your phone number, the login code, and your 2FA password.")
    await client.start()
    if not await client.is_user_authorized():
        raise RuntimeError(
            "Telegram login did not complete; run `python telegram_login.py` and try again."
        )
    me = await client.get_me()
    _info(f"Logged in as {getattr(me, 'username', None) or me.id}")
    return client


async def main() -> None:
    client = await ensure_login()
    me = await client.get_me()
    print(f"Telegram session ready for user {getattr(me, 'username', None) or me.id}")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
