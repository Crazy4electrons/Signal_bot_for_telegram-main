"""One-time interactive Telegram user-session login for the VPS."""

import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient

load_dotenv()


async def main() -> None:
    api_id = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")
    if not api_id or not api_hash:
        raise RuntimeError(
            "Missing TELEGRAM_API_ID or TELEGRAM_API_HASH. Add both values to .env; "
            "create them at https://my.telegram.org under API development tools."
        )
    session_path = os.getenv("TELEGRAM_SESSION_PATH", "data/telegram_signal_bot")
    Path(session_path).parent.mkdir(parents=True, exist_ok=True)

    client = TelegramClient(session_path, int(api_id), api_hash)
    await client.start()
    me = await client.get_me()
    print(f"Telegram session created for user {getattr(me, 'username', None) or me.id}")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
