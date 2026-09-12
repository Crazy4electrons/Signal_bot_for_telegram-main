"""Direct Telegram channel ingestion for the signal bot.

The listener uses a Telegram user session because bots cannot read arbitrary
channels that the account merely subscribes to. Authenticate once on the
server, then keep the generated session file private.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
SignalCallback = Callable[[str, str], Awaitable[None]]

try:
    from telethon import TelegramClient, events
except ImportError:  # Keep the rest of the app importable until the dependency is installed.
    TelegramClient = None  # type: ignore[assignment,misc]
    events = None  # type: ignore[assignment]


class TelegramSignalListener:
    """Read new posts from one configured Telegram channel."""

    def __init__(
        self,
        callback: SignalCallback,
        api_id: int,
        api_hash: str,
        channel: str,
        session_path: str,
        state_path: str = "signal_bot.sqlite3",
    ) -> None:
        self.callback = callback
        self.api_id = api_id
        self.api_hash = api_hash
        self.channel = channel
        self.session_path = session_path
        self.state_path = state_path
        self.client: Any = None
        self.last_message_id: int | None = None
        self.last_error: str | None = None
        self._initialize_state()

    @classmethod
    def from_environment(cls, callback: SignalCallback) -> TelegramSignalListener | None:
        """Build a listener only when direct Telegram ingestion is enabled."""
        if os.getenv("TELEGRAM_LISTENER_ENABLED", "false").lower() not in {"1", "true", "yes"}:
            return None

        api_id = os.getenv("TELEGRAM_API_ID")
        api_hash = os.getenv("TELEGRAM_API_HASH")
        channel = os.getenv("TELEGRAM_SOURCE_CHANNEL")
        if not api_id or not api_hash or not channel:
            raise RuntimeError(
                "TELEGRAM_LISTENER_ENABLED is true but TELEGRAM_API_ID, "
                "TELEGRAM_API_HASH, or TELEGRAM_SOURCE_CHANNEL is missing"
            )
        return cls(
            callback=callback,
            api_id=int(api_id),
            api_hash=api_hash,
            channel=channel,
            session_path=os.getenv("TELEGRAM_SESSION_PATH", "data/telegram_signal_bot"),
            state_path=os.getenv("SIGNAL_BOT_STATE_PATH", "signal_bot.sqlite3"),
        )

    def _initialize_state(self) -> None:
        Path(self.state_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.state_path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS telegram_messages ("
                "channel TEXT NOT NULL, message_id INTEGER NOT NULL, "
                "received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                "PRIMARY KEY(channel, message_id))"
            )

    def _claim_message(self, message_id: int) -> bool:
        with sqlite3.connect(self.state_path) as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO telegram_messages(channel, message_id) VALUES (?, ?)",
                (self.channel, message_id),
            )
            return cursor.rowcount == 1

    async def run(self) -> None:
        """Connect and process new posts until cancelled."""
        if TelegramClient is None or events is None:
            raise RuntimeError("Direct Telegram support requires the 'telethon' package")

        self.client = TelegramClient(self.session_path, self.api_id, self.api_hash)

        @self.client.on(events.NewMessage(chats=self.channel))
        async def handle_message(event: Any) -> None:
            message_id = int(event.message.id)
            if not self._claim_message(message_id):
                logger.info("Ignoring already processed Telegram message %s", message_id)
                return
            text = event.message.message or ""
            self.last_message_id = message_id
            await self.callback(text, f"telegram:{self.channel}:{message_id}")

        try:
            await self.client.start()
            logger.info("Telegram listener connected to %s", self.channel)
            await self.client.run_until_disconnected()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.last_error = str(exc)
            logger.exception("Telegram listener stopped")
            raise
        finally:
            if self.client:
                await self.client.disconnect()

    async def stop(self) -> None:
        if self.client:
            await self.client.disconnect()
