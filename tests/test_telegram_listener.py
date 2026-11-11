import asyncio
import sqlite3
from types import SimpleNamespace

import pytest

import telegram_listener


def test_listener_catches_up_recent_messages_without_reprocessing_claimed_ones(tmp_path):
    database = tmp_path / "signals.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE telegram_messages ("
            "channel TEXT NOT NULL, message_id INTEGER NOT NULL, "
            "received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "PRIMARY KEY(channel, message_id))"
        )
        connection.executemany(
            "INSERT INTO telegram_messages(channel, message_id) VALUES (?, ?)",
            [("-100123", 100), ("-100123", 102)],
        )

    received = []

    async def callback(text, source, channel):
        received.append((text, source, channel.key))

    listener = telegram_listener.TelegramSignalListener(
        callback,
        api_id=1,
        api_hash="hash",
        channels=[
            telegram_listener.ChannelConfig(
                channel="-100123",
                timezone="Etc/GMT+3",
                broker="quotex",
                name="Magic Trader",
            )
        ],
        session_path=str(tmp_path / "telegram"),
        state_path=str(database),
    )

    class FakeClient:
        async def iter_messages(self, _channel, *, min_id, reverse):
            assert min_id == 2
            assert reverse is True
            for message_id in (100, 101, 102, 103):
                yield SimpleNamespace(id=message_id, message=f"post {message_id}")

    async def run_test():
        listener.client = FakeClient()
        await listener._catch_up_recent_messages()

    asyncio.run(run_test())

    assert received == [
        ("post 101", "telegram:Magic Trader:101", "Magic Trader"),
        ("post 103", "telegram:Magic Trader:103", "Magic Trader"),
    ]
    assert listener.last_message_ids["-100123"] == 103


def test_listener_retries_after_telegram_disconnect(monkeypatch, tmp_path):
    clients = []
    retry_delays = []

    class FakeClient:
        def __init__(self, *_args):
            clients.append(self)

        def on(self, _event):
            return lambda _handler: None

        async def start(self):
            return None

        async def run_until_disconnected(self):
            raise ConnectionError("network unavailable")

        async def disconnect(self):
            return None

    async def fake_sleep(delay):
        retry_delays.append(delay)
        if len(retry_delays) == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(telegram_listener, "TelegramClient", FakeClient)
    monkeypatch.setattr(
        telegram_listener,
        "events",
        SimpleNamespace(NewMessage=lambda **_kwargs: object()),
    )
    monkeypatch.setattr(telegram_listener.asyncio, "sleep", fake_sleep)
    listener = telegram_listener.TelegramSignalListener(
        callback=lambda *_args: asyncio.sleep(0),
        api_id=1,
        api_hash="hash",
        channels=[
            telegram_listener.ChannelConfig(
                channel="-100123",
                timezone="Etc/GMT+3",
                broker="quotex",
            )
        ],
        session_path=str(tmp_path / "telegram"),
        state_path=str(tmp_path / "signals.sqlite3"),
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(listener.run())

    assert len(clients) == 2
    assert retry_delays == [5, 10]
    assert listener.last_error == "network unavailable"
