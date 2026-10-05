"""Direct Telegram channel ingestion for the signal bot.

The listener uses a Telegram user session because bots cannot read arbitrary
channels that the account merely subscribes to. Authenticate once on the
server, then keep the generated session file private.

Channels live in a JSON file (``channels.json`` by default, override with
``TELEGRAM_CHANNELS_FILE``) so the same list can be shared across hosts
without copying ``.env``::

    {
      "channels": [
        {"name": "primary", "channel": "-1003559202666",
         "timezone": "Etc/GMT-2", "provider": "telegram", "enabled": true},
        {"channel": "@other_channel", "timezone": "Etc/GMT+4"}
      ]
    }

Each entry may be an object (as above) or a bare channel string. ``timezone``
and ``provider`` are optional and default to ``SIGNAL_TIMEZONE`` and
``TELEGRAM_SIGNAL_PROVIDER``. When the file is absent the legacy
``TELEGRAM_SOURCE_CHANNEL`` variable is used instead.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytz

logger = logging.getLogger(__name__)

DEFAULT_CHANNELS_FILE = "channels.json"
DEFAULT_CHANNELS_COMMENT = (
    "Telegram source channels. Safe to commit and share between hosts (no secrets here). "
    "Override the path with TELEGRAM_CHANNELS_FILE in .env."
)
# Only Etc/GMT offsets are usable: main.py reads the numeric offset from the
# last characters of the string, so a named zone would raise on every signal.
ETC_TIMEZONE = re.compile(r"^Etc/GMT([+-])(\d{1,2})$")


@dataclass(frozen=True)
class ChannelConfig:
    """One Telegram source channel plus its signal defaults."""

    channel: str
    timezone: str | None = None
    provider: str | None = None
    name: str | None = None
    enabled: bool = True

    @property
    def key(self) -> str:
        """Stable identifier used for logging, deduplication and status."""
        return self.name or self.channel


@dataclass
class ChannelSet:
    """Every entry that parsed, plus notes about the ones that did not."""

    channels: list[ChannelConfig] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def enabled(self) -> list[ChannelConfig]:
        """The channels the listener should actually read."""
        return [config for config in self.channels if config.enabled]


SignalCallback = Callable[[str, str, ChannelConfig], Awaitable[None]]

try:
    from telethon import TelegramClient, events
except ImportError:  # Keep the rest of the app importable until the dependency is installed.
    TelegramClient = None  # type: ignore[assignment,misc]
    events = None  # type: ignore[assignment]


def channels_file(path: str | None = None) -> Path:
    """Resolved path of the channel file."""
    return Path(path or os.getenv("TELEGRAM_CHANNELS_FILE", DEFAULT_CHANNELS_FILE))


def timezone_problem(timezone: str) -> str | None:
    """Explain why a timezone cannot be used here, or return None when it is fine.

    Only ``Etc/GMT`` offsets are accepted, because main.py derives the numeric
    offset from the last characters of the string. A named zone such as
    "Europe/Berlin" would raise on every signal it touched.
    """
    if not ETC_TIMEZONE.match(timezone):
        return (
            f"timezone {timezone!r} is not an Etc/GMT offset; write 'Etc/GMT-2' for GMT+2 "
            "(the sign is flipped)"
        )
    try:
        pytz.timezone(timezone)
    except Exception:
        return (
            f"timezone {timezone!r} is out of range; use Etc/GMT-14 (GMT+14) "
            "through Etc/GMT+12 (GMT-12)"
        )
    return None


def coerce_channel(entry: Any) -> tuple[ChannelConfig | None, str | None]:
    """Build a ChannelConfig from one file entry, or explain why it is invalid."""
    if isinstance(entry, str):
        entry = {"channel": entry}
    if not isinstance(entry, dict):
        return None, "entry is not an object or a string"
    channel = str(entry.get("channel") or entry.get("id") or "").strip()
    if not channel:
        return None, "entry has no 'channel' value"
    timezone = str(entry["timezone"]).strip() if entry.get("timezone") else None
    if timezone:
        problem = timezone_problem(timezone)
        if problem:
            return None, problem
    return (
        ChannelConfig(
            channel=channel,
            timezone=timezone,
            provider=str(entry["provider"]).strip() if entry.get("provider") else None,
            name=str(entry["name"]).strip() if entry.get("name") else None,
            enabled=bool(entry.get("enabled", True)),
        ),
        None,
    )


def channel_to_dict(config: ChannelConfig) -> dict[str, Any]:
    """One channel in the shape it is written to the file."""
    return {
        "name": config.name,
        "channel": config.channel,
        "timezone": config.timezone,
        "provider": config.provider,
        "enabled": config.enabled,
    }


def read_channel_entries(path: str | None = None) -> list[dict[str, Any]]:
    """Every entry in the file, normalised, each with a ``problem`` note.

    Invalid entries come back too: the dashboard shows them so a typo can be
    corrected, rather than them vanishing on the next save.
    """
    file = channels_file(path)
    if file.is_file():
        try:
            document = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Could not read channel configuration {file}: {exc}") from exc
        raw_entries = document.get("channels") if isinstance(document, dict) else document
        if not isinstance(raw_entries, list):
            raise RuntimeError(
                f"Channel configuration {file} must be a list or a {{'channels': [...]}} object"
            )
    else:
        legacy = os.getenv("TELEGRAM_SOURCE_CHANNEL", "").strip()
        logger.info("No channels file at %s; using legacy TELEGRAM_SOURCE_CHANNEL", file)
        raw_entries = (
            [
                {
                    "channel": legacy,
                    "timezone": os.getenv("SIGNAL_TIMEZONE"),
                    "provider": os.getenv("TELEGRAM_SIGNAL_PROVIDER"),
                }
            ]
            if legacy
            else []
        )

    entries: list[dict[str, Any]] = []
    for raw in raw_entries:
        config, problem = coerce_channel(raw)
        if config is not None:
            entry = channel_to_dict(config)
        elif isinstance(raw, dict):
            entry = {
                "name": raw.get("name") or None,
                "channel": str(raw.get("channel") or raw.get("id") or "").strip(),
                "timezone": raw.get("timezone") or None,
                "provider": raw.get("provider") or None,
                "enabled": bool(raw.get("enabled", True)),
            }
        else:
            entry = {
                "name": None,
                "channel": str(raw).strip(),
                "timezone": None,
                "provider": None,
                "enabled": True,
            }
        entry["problem"] = problem
        entries.append(entry)
    return entries


def load_channel_set(path: str | None = None) -> ChannelSet:
    """Load the configured channels and report entries that had to be skipped.

    Bad entries are reported rather than raised: a typo in one channel must not
    take the whole bot offline. Skipped channels never trade, so a wrong value
    can never cause a trade at the wrong time.
    """
    channels: list[ChannelConfig] = []
    problems: list[str] = []
    seen: set[str] = set()
    for entry in read_channel_entries(path):
        if entry.get("problem"):
            label = entry.get("name") or entry.get("channel") or "entry"
            problems.append(f"{label}: {entry['problem']}")
            continue
        config = ChannelConfig(
            channel=entry["channel"],
            timezone=entry.get("timezone") or None,
            provider=entry.get("provider") or None,
            name=entry.get("name") or None,
            enabled=bool(entry.get("enabled", True)),
        )
        if config.channel in seen:
            problems.append(
                f"channel {config.channel!r} is listed more than once; keeping the first"
            )
            continue
        seen.add(config.channel)
        channels.append(config)
    if channels:
        logger.info("Loaded %d channel(s): %s", len(channels), ", ".join(c.key for c in channels))
    return ChannelSet(channels=channels, problems=problems)


def load_channels(path: str | None = None) -> list[ChannelConfig]:
    """Enabled channels, with skipped entries logged as errors."""
    channel_set = load_channel_set(path)
    for problem in channel_set.problems:
        logger.error("Ignoring channel configuration: %s", problem)
    return channel_set.enabled


def write_channels(path: str, channels: list[ChannelConfig]) -> None:
    """Persist channels as JSON, preserving any other top-level keys.

    The write is atomic: the bot reads this file while it runs, and a half
    written file would leave the listener with nothing to read.
    """
    file = Path(path)
    document: dict[str, Any] = {}
    if file.is_file():
        try:
            loaded = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("Replacing unreadable channel file %s", file)
        else:
            if isinstance(loaded, dict):
                document = loaded

    payload: dict[str, Any] = {"$comment": document.get("$comment") or DEFAULT_CHANNELS_COMMENT}
    payload.update(
        {key: value for key, value in document.items() if key not in {"$comment", "channels"}}
    )
    payload["channels"] = [channel_to_dict(config) for config in channels]

    file.parent.mkdir(parents=True, exist_ok=True)
    temporary = file.parent / f"{file.name}.tmp"
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    os.replace(temporary, file)
    logger.info("Wrote %d channel(s) to %s", len(channels), file)


class TelegramSignalListener:
    """Read new posts from one or more configured Telegram channels."""

    def __init__(
        self,
        callback: SignalCallback,
        api_id: int,
        api_hash: str,
        channels: list[ChannelConfig],
        session_path: str,
        state_path: str = "signal_bot.sqlite3",
        problems: list[str] | None = None,
    ) -> None:
        self.callback = callback
        self.api_id = api_id
        self.api_hash = api_hash
        self.channels = list(channels)
        self.channel_problems = list(problems or [])
        self.session_path = session_path
        self.state_path = state_path
        self.client: Any = None
        self.last_message_ids: dict[str, int] = {}
        self.last_error: str | None = None
        self._initialize_state()

    @property
    def channel(self) -> str:
        """First configured channel; kept for backwards compatibility."""
        return self.channels[0].channel if self.channels else ""

    @classmethod
    def from_environment(cls, callback: SignalCallback) -> TelegramSignalListener | None:
        """Build a listener only when direct Telegram ingestion is enabled."""
        if os.getenv("TELEGRAM_LISTENER_ENABLED", "false").lower() not in {"1", "true", "yes"}:
            return None

        api_id = os.getenv("TELEGRAM_API_ID")
        api_hash = os.getenv("TELEGRAM_API_HASH")
        channel_set = load_channel_set()
        for problem in channel_set.problems:
            logger.error("Ignoring channel configuration: %s", problem)
        channels = channel_set.enabled
        if not api_id or not api_hash or not channels:
            raise RuntimeError(
                "TELEGRAM_LISTENER_ENABLED is true but TELEGRAM_API_ID, TELEGRAM_API_HASH, "
                "or the channel list (TELEGRAM_CHANNELS_FILE / channels.json) is missing"
            )
        return cls(
            callback=callback,
            api_id=int(api_id),
            api_hash=api_hash,
            channels=channels,
            session_path=os.getenv("TELEGRAM_SESSION_PATH", "data/telegram_signal_bot"),
            state_path=os.getenv("SIGNAL_BOT_STATE_PATH", "signal_bot.sqlite3"),
            problems=channel_set.problems,
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

    def _claim_message(self, channel_key: str, message_id: int) -> bool:
        with sqlite3.connect(self.state_path) as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO telegram_messages(channel, message_id) VALUES (?, ?)",
                (channel_key, message_id),
            )
            return cursor.rowcount == 1

    @staticmethod
    def _chat_filter(channel: str) -> str | int:
        """Telethon wants a numeric id for -100... channels, a username otherwise."""
        body = channel[1:] if channel.startswith("-") else channel
        return int(channel) if body.isdigit() else channel

    def _make_handler(self, config: ChannelConfig):
        async def handle_message(event: Any) -> None:
            message_id = int(event.message.id)
            if not self._claim_message(config.channel, message_id):
                logger.info(
                    "Ignoring already processed Telegram message %s from %s",
                    message_id,
                    config.key,
                )
                return
            text = event.message.message or ""
            self.last_message_ids[config.channel] = message_id
            await self.callback(text, f"telegram:{config.key}:{message_id}", config)

        return handle_message

    def status(self) -> list[dict[str, Any]]:
        """Per-channel state, surfaced by the /health endpoint."""
        return [
            {
                "name": config.name,
                "channel": config.channel,
                "timezone": config.timezone,
                "provider": config.provider,
                "last_message_id": self.last_message_ids.get(config.channel),
            }
            for config in self.channels
        ]

    async def run(self) -> None:
        """Connect and process new posts until cancelled."""
        if TelegramClient is None or events is None:
            raise RuntimeError("Direct Telegram support requires the 'telethon' package")

        self.client = TelegramClient(self.session_path, self.api_id, self.api_hash)
        for config in self.channels:
            self.client.on(events.NewMessage(chats=self._chat_filter(config.channel)))(
                self._make_handler(config)
            )

        try:
            await self.client.start()
            logger.info(
                "Telegram listener connected to %d channel(s): %s",
                len(self.channels),
                ", ".join(config.key for config in self.channels),
            )
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
