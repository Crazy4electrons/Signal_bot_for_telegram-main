import asyncio
from datetime import datetime

from starlette.requests import Request
from starlette.responses import Response

import main


class FakeBroker:
    def __init__(self):
        self.connected = False
        self.disconnected = False

    def is_connected(self):
        return self.connected

    async def balance(self):
        if not self.connected:
            raise ConnectionError("connection closed")
        return 32.92

    async def disconnect(self):
        self.disconnected = True


def test_ensure_broker_connection_replaces_dead_client(monkeypatch):
    old_broker = FakeBroker()
    replacement = FakeBroker()
    replacement.connected = True
    monkeypatch.setattr(main, "api", old_broker)
    monkeypatch.setattr(main, "PocketOptionAsync", lambda ssid: replacement)
    monkeypatch.setenv("ssid", "test-session")
    monkeypatch.setattr(main, "broker_connected", False)

    connected = asyncio.run(main.ensure_broker_connection())

    assert connected is True
    assert main.api is replacement
    assert old_broker.disconnected is True
    assert main.broker_connected is True
    assert main.account_details.balance == 32.92


def test_ensure_broker_connection_reports_failed_replacement(monkeypatch):
    old_broker = FakeBroker()

    def fail_to_create_client(_ssid):
        raise ConnectionError("broker unavailable")

    monkeypatch.setattr(main, "api", old_broker)
    monkeypatch.setattr(main, "PocketOptionAsync", fail_to_create_client)
    monkeypatch.setenv("ssid", "test-session")
    monkeypatch.setattr(main, "broker_connected", False)

    connected = asyncio.run(main.ensure_broker_connection())

    assert connected is False
    assert main.broker_connected is False


def test_parse_signal_ignores_posts_without_trade_fields(monkeypatch):
    monkeypatch.setattr(
        main,
        "parse_macrodroid_trade_data",
        lambda _text: {
            "asset": None,
            "time": None,
            "direction": None,
            "signal_provider": "Lorenzo",
            "timezone": "Etc/GMT-2",
        },
    )

    signal = asyncio.run(main.parse_signal("Lorenzo announcement"))

    assert signal is None


def test_parse_signal_warns_on_partial_trade_fields(monkeypatch):
    monkeypatch.setattr(
        main,
        "parse_macrodroid_trade_data",
        lambda _text: {
            "asset": "EURUSD_otc",
            "time": None,
            "direction": None,
            "signal_provider": "Lorenzo",
            "timezone": "Etc/GMT-2",
        },
    )

    signal = asyncio.run(main.parse_signal("Incomplete signal"))

    assert signal is False


def test_parse_signal_uses_signal_timezone_date_near_midnight(monkeypatch):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            local = cls(2026, 11, 17, 1, 0)
            return tz.localize(local) if tz is not None else local

    monkeypatch.setattr(main, "datetime", FrozenDateTime)
    monkeypatch.setattr(main, "random", lambda: 0)
    monkeypatch.setattr(main, "Signals", {})
    monkeypatch.setattr(
        main,
        "parse_macrodroid_trade_data",
        lambda _text: {
            "asset": "EURUSD_otc",
            "time": "18:05",
            "direction": "CALL",
            "signal_provider": "Lorenzo",
            "timezone": "Etc/GMT+5",
        },
    )

    signal = asyncio.run(main.parse_signal("valid signal"))

    assert signal
    assert signal.signal_details.entry_time.strftime("%Y-%m-%d %H:%M") == (
        "2026-11-17 01:05"
    )


def test_health_request_bypasses_the_serial_request_queue():
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/health",
        "raw_path": b"/health",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 1),
        "server": ("127.0.0.1", 80),
    }
    request = Request(scope)
    middleware = main.QueueMiddleware(lambda _scope, _receive, _send: None)
    called = False

    async def call_next(_request):
        nonlocal called
        called = True
        return Response("ok")

    response = asyncio.run(middleware.dispatch(request, call_next))

    assert called is True
    assert response.body == b"ok"
