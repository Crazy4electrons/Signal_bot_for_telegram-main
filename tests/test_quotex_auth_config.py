import asyncio
from io import StringIO
import json
import ssl
import time
from pathlib import Path

import pytest

from api_quotex.client import AsyncQuotexClient
from api_quotex.connection_keep_alive import ConnectionKeepAlive, REGIONS as QUOTEX_REGIONS
from api_quotex.exceptions import AuthenticationError, OrderError, WebSocketError
from api_quotex import login as quotex_login
from api_quotex.models import Order, OrderDirection
from api_quotex.websocket_client import AsyncWebSocketClient, logger as quotex_logger
from playwright_scraper import (
    BROKER_CONFIG,
    QuotexBrowserSession,
    _quotex_profile_path,
    _extract_csrf_form,
    _has_quotex_browser_auth,
    _is_quotex_trade_page,
    _listen_for_quotex_authorization,
    _quotex_authorization_ssid,
    _quotex_socket_event,
    _quotex_trade_url,
    capture_session,
)


def test_quotex_browser_uses_official_sign_in_page() -> None:
    assert BROKER_CONFIG["quotex"]["url"] == "https://www.quotex.com/en/sign-in/"


def test_quotex_trade_url_matches_requested_account_mode() -> None:
    assert _quotex_trade_url(is_demo=True) == "https://www.quotex.com/en/demo-trade/"
    assert _quotex_trade_url(is_demo=False) == "https://www.quotex.com/en/trade/"


def test_quotex_profile_path_is_stable_when_launched_from_another_directory(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.delenv("QUOTEX_PLAYWRIGHT_PROFILE", raising=False)
    monkeypatch.chdir(tmp_path)

    assert _quotex_profile_path() == Path(__file__).resolve().parents[1] / "data/quotex-playwright"


def test_quotex_trade_page_guard_rejects_wrong_account_route() -> None:
    assert _is_quotex_trade_page("https://quotex.com/en/demo-trade/", is_demo=True)
    assert not _is_quotex_trade_page("https://quotex.com/en/trade/", is_demo=True)
    assert _is_quotex_trade_page("https://quotex.com/en/trade/", is_demo=False)
    assert not _is_quotex_trade_page("https://quotex.com/en/demo-trade/", is_demo=False)


def test_quotex_authorization_parser_preserves_canonical_demo_frame() -> None:
    payload = '42["authorization",{"session":"session-value","isDemo":1,"tournamentId":0,"isFastHistory":true}]'

    assert _quotex_authorization_ssid(payload, is_demo=True) == payload


def test_quotex_authorization_parser_rejects_wrong_account_mode() -> None:
    payload = '42["authorization",{"session":"session-value","isDemo":0,"tournamentId":0}]'

    assert _quotex_authorization_ssid(payload, is_demo=True) is None


def test_quotex_socket_event_recognizes_server_authorization_success() -> None:
    assert _quotex_socket_event('42["s_authorization",{"isDemo":1}]') == (
        "s_authorization",
        {"isDemo": 1},
    )


def test_validate_ssid_waits_for_server_authorization(monkeypatch) -> None:
    class FakeWebSocket:
        def __init__(self) -> None:
            self.handlers = {}
            self.is_authenticated = False
            self._last_auth_error = None
            self.disconnected = False

        def add_event_handler(self, event, handler) -> None:
            self.handlers[event] = handler

        def remove_event_handler(self, event, handler) -> None:
            assert self.handlers[event] is handler
            del self.handlers[event]

        async def connect(self, _urls, _ssid) -> bool:
            return True

        async def disconnect(self) -> None:
            self.disconnected = True

    client = FakeWebSocket()
    monkeypatch.setattr(
        "api_quotex.websocket_client.AsyncWebSocketClient",
        lambda: client,
    )
    monkeypatch.setattr(
        quotex_login.REGIONS,
        "get_all_regions",
        lambda: {"DEMO": "wss://demo.example/socket"},
    )
    monkeypatch.setattr(
        quotex_login.REGIONS,
        "get_demo_regions",
        lambda: ["wss://demo.example/socket"],
    )
    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setitem(quotex_login.CONNECTION_SETTINGS, "handshake_timeout", 0.001)

    assert not asyncio.run(quotex_login.validate_ssid("demo-frame"))
    assert client.disconnected
    assert client.handlers == {}


def test_validate_ssid_accepts_server_auth_after_pending_handshake(monkeypatch) -> None:
    class FakeWebSocket:
        def __init__(self) -> None:
            self.handlers = {}
            self.is_authenticated = False
            self._last_auth_error = None

        def add_event_handler(self, event, handler) -> None:
            self.handlers[event] = handler

        def remove_event_handler(self, event, handler) -> None:
            del self.handlers[event]

        async def connect(self, _urls, _ssid) -> bool:
            await self.handlers["authenticated"]({})
            return True

        async def disconnect(self) -> None:
            pass

    client = FakeWebSocket()
    monkeypatch.setattr(
        "api_quotex.websocket_client.AsyncWebSocketClient",
        lambda: client,
    )
    monkeypatch.setattr(
        quotex_login.REGIONS,
        "get_all_regions",
        lambda: {"DEMO": "wss://demo.example/socket"},
    )
    monkeypatch.setattr(
        quotex_login.REGIONS,
        "get_demo_regions",
        lambda: ["wss://demo.example/socket"],
    )
    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda *_args, **_kwargs: [],
    )

    assert asyncio.run(quotex_login.validate_ssid("demo-frame"))


def test_get_ssid_falls_back_when_cloudscraper_authorization_is_rejected(monkeypatch) -> None:
    class Response:
        status_code = 200
        headers = {}
        text = "trade page"

    class Cookies:
        def get_dict(self):
            return {}

    class FakeScraper:
        cookies = Cookies()
        headers = {"User-Agent": "test-agent"}

        def get(self, *_args, **_kwargs):
            return Response()

        def post(self, *_args, **_kwargs):
            return Response()

    saved_sessions = []
    validated_ssids = []

    async def validate(ssid):
        validated_ssids.append(ssid)
        return ssid == '42["authorization",{"session":"browser-token","isDemo":1,"tournamentId":0}]'

    async def browser_login(*_args, **_kwargs):
        return True, {
            "ssid": '42["authorization",{"session":"browser-token","isDemo":1,"tournamentId":0}]',
            "is_demo": True,
        }

    monkeypatch.delenv("QUOTEX_SSID", raising=False)
    monkeypatch.setattr(quotex_login, "load_session", lambda: {})
    monkeypatch.setattr(quotex_login, "load_config", lambda: {})
    monkeypatch.setattr(quotex_login, "save_config", lambda _config: None)
    monkeypatch.setattr(quotex_login, "save_session", saved_sessions.append)
    monkeypatch.setattr(quotex_login, "validate_ssid", validate)
    monkeypatch.setattr(quotex_login, "_playwright_login_and_capture", browser_login)
    monkeypatch.setattr(quotex_login, "_extract_csrf_from_signin", lambda _html: "csrf")
    monkeypatch.setattr(quotex_login, "_extract_token_from_trade", lambda _html: "cloud-token")
    monkeypatch.setattr(quotex_login, "_login_urls", lambda *_args: ("login", "trade"))
    monkeypatch.setattr(quotex_login.cloudscraper, "create_scraper", FakeScraper)

    success, session = asyncio.run(
        quotex_login.get_ssid(
            email="demo@example.test",
            password="test-password",
            is_demo=True,
        )
    )

    assert success
    assert session["ssid"].endswith('"browser-token","isDemo":1,"tournamentId":0}]')
    assert len(validated_ssids) == 2
    assert validated_ssids[0].endswith('"cloud-token","isDemo":1,"tournamentId":0}]')
    assert saved_sessions == [session]


def test_get_ssid_rejects_unvalidated_playwright_session(monkeypatch) -> None:
    class Response:
        status_code = 200
        headers = {}
        text = "trade page"

    class Cookies:
        def get_dict(self):
            return {}

    class FakeScraper:
        cookies = Cookies()
        headers = {}

        def get(self, *_args, **_kwargs):
            return Response()

        def post(self, *_args, **_kwargs):
            return Response()

    async def browser_login(*_args, **_kwargs):
        return True, {"ssid": "rejected-browser-frame", "is_demo": True}

    async def record_error(**_kwargs) -> None:
        pass

    saved_sessions = []
    monkeypatch.delenv("QUOTEX_SSID", raising=False)
    monkeypatch.setattr(quotex_login, "load_session", lambda: {})
    monkeypatch.setattr(quotex_login, "load_config", lambda: {})
    monkeypatch.setattr(quotex_login, "save_config", lambda _config: None)
    monkeypatch.setattr(quotex_login, "save_session", saved_sessions.append)
    monkeypatch.setattr(quotex_login, "validate_ssid", lambda _ssid: asyncio.sleep(0, result=False))
    monkeypatch.setattr(quotex_login, "_playwright_login_and_capture", browser_login)
    monkeypatch.setattr(quotex_login, "_extract_csrf_from_signin", lambda _html: "csrf")
    monkeypatch.setattr(quotex_login, "_extract_token_from_trade", lambda _html: None)
    monkeypatch.setattr(quotex_login, "_login_urls", lambda *_args: ("login", "trade"))
    monkeypatch.setattr(quotex_login.cloudscraper, "create_scraper", FakeScraper)
    monkeypatch.setattr(quotex_login.error_monitor, "record_error", record_error)

    success, session = asyncio.run(
        quotex_login.get_ssid(
            email="demo@example.test",
            password="test-password",
            is_demo=True,
        )
    )

    assert not success
    assert session == {}
    assert saved_sessions == []


def test_quotex_websocket_uses_verified_tls(monkeypatch) -> None:
    class FakeSocket:
        async def close(self) -> None:
            pass

    client = AsyncWebSocketClient()
    captured = {}

    async def fake_connect(**kwargs):
        captured.update(kwargs)
        return FakeSocket()

    async def no_op(*_args, **_kwargs):
        pass

    monkeypatch.setattr("api_quotex.websocket_client.websockets.connect", fake_connect)
    monkeypatch.setattr(client, "_send_handshake", no_op)
    monkeypatch.setattr(client, "_start_background_tasks", no_op)

    assert asyncio.run(client.connect(["wss://quotex.example/socket.io/"], "test-frame"))
    assert captured["ssl"].verify_mode == ssl.CERT_REQUIRED
    assert captured["ssl"].check_hostname is True
    assert captured["ping_interval"] is None
    assert captured["ping_timeout"] is None
    asyncio.run(client.disconnect())


def test_quotex_persistence_fallback_requires_browser_auth_confirmation() -> None:
    assert _has_quotex_browser_auth({"browser_authenticated": True})
    assert not _has_quotex_browser_auth({"browser_authenticated": False})
    assert not _has_quotex_browser_auth({})


def test_browser_authorization_listener_ignores_previous_socket_generation() -> None:
    class FakePage:
        def __init__(self) -> None:
            self.handlers = {}

        def on(self, event, handler) -> None:
            self.handlers[event] = handler

    class FakeWebSocket:
        def __init__(self) -> None:
            self.handlers = {}

        def on(self, event, handler) -> None:
            self.handlers[event] = handler

        def send(self, payload) -> None:
            self.handlers["framesent"](payload)

        def receive(self, payload) -> None:
            self.handlers["framereceived"](payload)

    async def run_test() -> None:
        page = FakePage()
        generation = 1
        current_future = asyncio.get_running_loop().create_future()
        _listen_for_quotex_authorization(
            page,
            True,
            lambda: current_future,
            lambda: generation,
        )

        old_socket = FakeWebSocket()
        page.handlers["websocket"](old_socket)
        generation += 1
        auth_frame = (
            '42["authorization",{"session":"demo-session","isDemo":1,"tournamentId":0}]'
        )
        old_socket.send(auth_frame)
        old_socket.receive('42["s_authorization",{"isDemo":1}]')
        assert not current_future.done()

        new_socket = FakeWebSocket()
        page.handlers["websocket"](new_socket)
        new_socket.send(auth_frame)
        new_socket.receive('42["s_authorization",{"isDemo":1}]')
        assert await current_future == auth_frame

    asyncio.run(run_test())


def test_browser_refresh_navigates_back_to_trade_page_and_captures_auth() -> None:
    class FakePage:
        url = "https://www.quotex.com/en/sign-in/"

        def on(self, _event, _handler) -> None:
            pass

        async def goto(self, url, **_kwargs) -> None:
            self.url = url
            if not session._auth_future.done():
                session._auth_future.set_result(
                    '42["authorization",{"session":"demo-session","isDemo":1}]'
                )

    async def run_test() -> None:
        nonlocal session
        session = QuotexBrowserSession(is_demo=True)
        page = FakePage()
        class FakeContext:
            async def close(self) -> None:
                pass

        session._context = FakeContext()
        session._page = page

        auth_frame = await session.refresh_authorization()

        assert page.url == "https://www.quotex.com/en/demo-trade/"
        assert auth_frame == '42["authorization",{"session":"demo-session","isDemo":1}]'
        await session.close()

    session = None
    asyncio.run(run_test())


def test_browser_auth_timeout_keeps_visible_profile_open(monkeypatch) -> None:
    class FakePage:
        url = ""

        def on(self, _event, _handler) -> None:
            pass

        async def goto(self, url, **_kwargs) -> None:
            self.url = url

    class FakeContext:
        def __init__(self) -> None:
            self.pages = [FakePage()]
            self.closed = False

        async def close(self) -> None:
            self.closed = True

    class FakeBrowser:
        def __init__(self, context) -> None:
            self.context = context
            self.chromium = self
            self.launch_args = None

        async def launch_persistent_context(self, *args, **_kwargs):
            self.launch_args = args
            return self.context

        async def stop(self) -> None:
            pass

    class FakePlaywrightManager:
        def __init__(self, browser) -> None:
            self.browser = browser

        async def start(self):
            return self.browser

    async def run_test() -> None:
        context = FakeContext()
        browser = FakeBrowser(context)
        manager = QuotexBrowserSession(is_demo=True, timeout_seconds=0.001)
        monkeypatch.setattr(
            "playwright_scraper.async_playwright",
            lambda: FakePlaywrightManager(browser),
        )
        monkeypatch.setattr("playwright_scraper.resolve_edge_binary", lambda: "/edge")

        with pytest.raises(RuntimeError, match="browser remains open"):
            await manager.start()

        assert manager.is_started
        assert browser.launch_args[0] == str(_quotex_profile_path())
        assert not context.closed
        await manager.close()
        assert context.closed

    asyncio.run(run_test())


def test_quotex_capture_always_initializes_persistent_browser_profile(monkeypatch) -> None:
    cloudscraper = object()
    browser_capture = {
        "ssid": '42["authorization",{"session":"browser-session","isDemo":1}]',
        "is_demo": True,
        "browser_authenticated": True,
    }
    captured = {}

    def cloud_login(*_args):
        return cloudscraper, {"ssid": "temporary-http-frame", "is_demo": True}

    async def browser_login(
        _account_type, _timeout, _headless, scraper, _email, _password
    ):
        captured["scraper"] = scraper
        return browser_capture

    async def validate(_session, _is_demo):
        return True

    def save(_broker, session):
        captured["saved_session"] = session

    monkeypatch.setattr("playwright_scraper._cloud_login", cloud_login)
    monkeypatch.setattr("playwright_scraper._capture_quotex_browser", browser_login)
    monkeypatch.setattr("playwright_scraper._validate_quotex_session", validate)
    monkeypatch.setattr("playwright_scraper._save_broker_session", save)

    result = asyncio.run(
        capture_session(
            "quotex",
            "DEMO",
            email="demo@example.test",
            password="not-a-real-password",
        )
    )

    assert captured["scraper"] is cloudscraper
    assert result == browser_capture
    assert captured["saved_session"] == browser_capture


def test_quotex_csrf_parser_selects_login_not_registration_form() -> None:
        html = '''
        <form action="https://quotex.com/en/sign-up/" method="post">
            <input type="hidden" name="_token" value="signup-token">
            <input type="email" name="email">
            <input type="password" name="password">
        </form>
        <form action="https://quotex.com/en/sign-in/" method="post">
            <input type="hidden" name="_token" value="signin-token">
            <input type="email" name="email">
            <input type="password" name="password">
            <input type="checkbox" name="remember">
        </form>
        '''

        form = _extract_csrf_form(html, "https://quotex.com/en/sign-in/")

        assert form is not None
        action, fields = form
        assert action == "https://quotex.com/en/sign-in/"
        assert fields["_token"] == "signin-token"


def test_quotex_list_style_successopenorder_marks_order_active() -> None:
    client = AsyncQuotexClient("session-value", is_demo=True)
    payload = [
        "successopenOrder",
        {
            "id": "broker-order-42",
            "requestId": "1234",
            "asset": "EURUSD",
            "amount": 1.0,
            "command": 0,
            "duration": 60,
            "openTime": 1700000000,
            "openPrice": 1.1234,
            "uid": 99,
        },
    ]

    asyncio.run(client._on_json_data(payload))

    assert client._request_id_to_server_id["1234"] == "broker-order-42"
    assert client._active_orders["1234"].broker_order_id == "broker-order-42"
    assert client._active_orders["1234"].status.name == "OPEN"


def test_quotex_open_ack_plus_payload_is_processed() -> None:
    client = AsyncQuotexClient("session-value", is_demo=True)
    payload = [
        "OPEN",
        {
            "id": "broker-order-99",
            "requestId": "4321",
            "asset": "EURUSD",
            "amount": 2.0,
            "command": 1,
            "duration": 60,
            "openTime": 1700000000,
            "openPrice": 1.2345,
            "uid": 77,
        },
    ]

    asyncio.run(client._on_order_opened(payload))

    assert client._request_id_to_server_id["4321"] == "broker-order-99"
    assert client._active_orders["4321"].broker_order_id == "broker-order-99"
    assert client._active_orders["4321"].direction.name == "PUT"


def test_order_open_ack_emits_pending_created_once_with_order_result() -> None:
    client = AsyncQuotexClient("session-value", is_demo=True)
    pending_order = Order(
        asset="EURUSD",
        amount=1.0,
        direction=OrderDirection.CALL,
        duration=60,
        request_id="5678",
    )
    client._pending_order_requests["5678"] = pending_order
    created = []
    client.add_event_callback("pending/created", created.append)
    payload = {
        "id": "broker-order-5678",
        "requestId": "5678",
        "asset": "EURUSD",
        "amount": 1.0,
        "command": 0,
        "duration": 60,
        "openTime": 1700000000,
        "openPrice": 1.1234,
    }

    async def process_duplicate_acknowledgments():
        await client._on_order_opened(payload)
        await client._on_order_opened(payload)

    asyncio.run(process_duplicate_acknowledgments())

    assert len(created) == 1
    assert created[0] == client._active_orders["5678"]
    assert created[0].order_id == "5678"
    assert created[0].broker_order_id == "broker-order-5678"
    assert "5678" not in client._pending_order_requests


def test_unmatched_order_open_ack_does_not_emit_pending_created() -> None:
    client = AsyncQuotexClient("session-value", is_demo=True)
    created = []
    client.add_event_callback("pending/created", created.append)

    asyncio.run(client._on_order_opened({
        "id": "broker-order-unmatched",
        "requestId": "unmatched",
        "asset": "EURUSD",
        "amount": 1.0,
        "command": 0,
        "duration": 60,
    }))

    assert created == []


def test_opened_order_list_emits_pending_created_once() -> None:
    client = AsyncQuotexClient("session-value", is_demo=True)
    client._pending_order_requests["6789"] = Order(
        asset="EURUSD",
        amount=1.0,
        direction=OrderDirection.CALL,
        duration=60,
        request_id="6789",
    )
    created = []
    client.add_event_callback("pending/created", created.append)
    payload = {
        "id": "broker-order-6789",
        "requestId": "6789",
        "asset": "EURUSD",
        "amount": 1.0,
        "command": 0,
        "duration": 60,
        "openTime": 1700000000,
    }

    async def process_list_and_duplicate_ack():
        await client._on_orders_opened_list([payload])
        await client._on_order_opened(payload)

    asyncio.run(process_list_and_duplicate_ack())

    assert len(created) == 1
    assert created[0].order_id == "6789"
    assert created[0].broker_order_id == "broker-order-6789"
    assert "6789" not in client._pending_order_requests


def test_text_order_open_parser_forwards_list_payload() -> None:
    client = AsyncQuotexClient("session-value", is_demo=True)
    pending_order = Order(
        asset="EURUSD",
        amount=1.0,
        direction=OrderDirection.CALL,
        duration=60,
        request_id="1234",
    )
    client._pending_order_requests["1234"] = pending_order

    asyncio.run(client._websocket._handle_auth_message(
        '42["s_orders/open",["OPEN",{"id":"broker-order-1","requestId":"1234",'
        '"asset":"EURUSD","amount":1.0,"command":0,"duration":60,"openPrice":1.12}]]'
    ))

    assert client._request_id_to_server_id["1234"] == "broker-order-1"
    assert client._active_orders["1234"].broker_order_id == "broker-order-1"
    result = asyncio.run(client._wait_for_order_result("1234", pending_order, timeout=0.1))

    assert result.order_id == "1234"
    assert "1234" not in client._pending_order_requests


def test_binary_order_open_response_correlates_browser_request_id() -> None:
    client = AsyncQuotexClient("session-value", is_demo=True)
    pending_order = Order(
        asset="AUDCAD",
        amount=1.0,
        direction=OrderDirection.CALL,
        duration=60,
        request_id="1792066006",
    )
    client._pending_order_requests[pending_order.request_id] = pending_order
    created = []
    client.add_event_callback("pending/created", created.append)
    client._websocket._pending_binary_event = "s_orders/open"

    asyncio.run(client._websocket._handle_candle_message(
        '\x04[{"requestId":1792066006,"id":"c28a1c10-4ea3-4720-9ba2-d2ae5e04e1f2",'
        '"asset":"AUDCAD","amount":1,"command":0,"isDemo":1,"profit":0.77}]'
    ))
    result = asyncio.run(client._wait_for_order_result(pending_order.request_id, pending_order, timeout=0.1))

    assert result.broker_order_id == "c28a1c10-4ea3-4720-9ba2-d2ae5e04e1f2"
    assert result.asset == "AUDCAD"
    assert pending_order.request_id not in client._pending_order_requests
    assert created == [result]


def test_binary_order_close_parser_forwards_ticket_payload() -> None:
    websocket = AsyncWebSocketClient()
    closed = []
    websocket.add_event_handler("order_closed", closed.append)
    websocket._pending_binary_event = "s_orders/close"

    asyncio.run(websocket._handle_candle_message(
        '\x04{"ticket":{"id":"broker-order-2","profit":1.5}}'
    ))

    assert closed == [{"id": "broker-order-2", "profit": 1.5}]


def test_send_message_and_flush_waits_for_websocket_write() -> None:
    class FakeWebSocket:
        state = type("State", (), {"name": "OPEN"})()

        def __init__(self):
            self.sent = []

        async def send(self, message):
            self.sent.append(message)

    websocket = AsyncWebSocketClient()
    fake_socket = FakeWebSocket()
    websocket.websocket = fake_socket

    async def run():
        await websocket.send_message('42["tick"]')
        await websocket.send_message_and_flush('42["orders/open",{}]')

    asyncio.run(run())

    assert fake_socket.sent == ['42["tick"]', '42["orders/open",{}]']


def test_send_message_and_flush_propagates_write_failure() -> None:
    class FakeWebSocket:
        state = type("State", (), {"name": "OPEN"})()

        async def send(self, message):
            raise RuntimeError("socket write failed")

    websocket = AsyncWebSocketClient()
    websocket.websocket = FakeWebSocket()

    with pytest.raises(WebSocketError, match="Failed to flush"):
        asyncio.run(websocket.send_message_and_flush('42["orders/open",{}]'))


def test_order_send_failure_is_not_silently_ignored() -> None:
    client = AsyncQuotexClient("session-value", is_demo=True)
    sent = []

    async def fake_send_message(message, *, wait_for_flush=False):
        sent.append((message, wait_for_flush))
        if message == '42["tick"]':
            client._quote_update_events["EURUSD"].set()
        return True

    async def fake_send_message_immediate(message):
        sent.append((message, True))
        raise WebSocketError("socket write failed")

    client.send_message = fake_send_message
    client.send_message_immediate = fake_send_message_immediate
    order = Order(
        asset="EURUSD",
        amount=1.0,
        direction=OrderDirection.CALL,
        duration=60,
        request_id="1234",
    )

    with pytest.raises(OrderError, match="Failed to send order request"):
        asyncio.run(client._send_order(order))

    assert sent[-1][1] is True


@pytest.mark.parametrize("duration", [60, 300])
def test_order_frame_matches_browser_time_and_option_type(duration: int) -> None:
    client = AsyncQuotexClient("session-value", is_demo=True)
    sent = []

    async def fake_send_message(message, *, wait_for_flush=False):
        sent.append((message, wait_for_flush))
        if message == '42["tick"]':
            client._quote_update_events["AUDCAD"].set()
        return True

    async def fake_send_message_immediate(message):
        sent.append((message, True))

    client.send_message = fake_send_message
    client.send_message_immediate = fake_send_message_immediate
    order = Order(
        asset="AUDCAD",
        amount=1.0,
        direction=OrderDirection.CALL,
        duration=duration,
        request_id="1792066006",
    )

    asyncio.run(client._send_order(order))

    message, wait_for_flush = sent[-1]
    assert sent[3][0] == '42["tick"]'
    update_event, update_payload = json.loads(sent[0][0][2:])
    assert update_event == "instruments/update"
    assert update_payload == {"asset": "AUDCAD", "period": 60}
    event, payload = json.loads(message[2:])
    assert event == "orders/open"
    assert wait_for_flush
    assert payload["requestId"] == 1792066006
    assert payload["asset"] == "AUDCAD"
    assert payload["amount"] == 1.0
    assert payload["action"] == "call"
    assert payload["time"] == duration
    assert payload["optionType"] == 100
    assert payload["isDemo"] == 1


def test_order_request_ids_use_epoch_seconds_and_stay_unique(monkeypatch) -> None:
    client = AsyncQuotexClient("session-value", is_demo=True)
    monkeypatch.setattr("api_quotex.client.time.time", lambda: 1792066006.8)

    assert client._new_order_request_id() == "1792066006"
    assert client._new_order_request_id() == "1792066007"


def test_opened_order_list_derives_duration_from_open_and_close_timestamps() -> None:
    client = AsyncQuotexClient("session-value", is_demo=True)
    payload = [{
        "id": "broker-order-42",
        "asset": "AUDCAD",
        "amount": 1.0,
        "command": 0,
        "openTime": "2026-10-19 06:21:22",
        "closeTime": "2026-10-19 06:26:22",
        "openTimestamp": 1792390882,
        "closeTimestamp": 1792391182,
        "uid": 99,
    }]

    asyncio.run(client._on_orders_opened_list(payload))

    opened = client._active_orders["broker-order-42"]
    assert opened.duration == 300
    assert opened.expires_at is not None
    assert opened.placed_at is not None
    assert opened.placed_at.timestamp() == pytest.approx(1792390882)
    assert opened.expires_at.timestamp() == pytest.approx(1792391182)


def test_closed_order_list_derives_duration_and_uses_epoch_timestamps() -> None:
    client = AsyncQuotexClient("session-value", is_demo=True)
    payload = {
        "id": "broker-order-43",
        "asset": "AUDCAD",
        "amount": 1.0,
        "command": 0,
        "openTime": "2026-10-19 06:21:22",
        "closeTime": "2026-10-19 06:22:22",
        "openTimestamp": 1792390882,
        "closeTimestamp": 1792390942,
        "profit": 0.8,
        "uid": 99,
    }

    asyncio.run(client._on_order_closed(payload))

    closed = client._order_results["broker-order-43"]
    assert closed.duration == 60
    assert closed.placed_at is not None
    assert closed.expires_at is not None
    assert closed.placed_at.timestamp() == pytest.approx(1792390882)
    assert closed.expires_at.timestamp() == pytest.approx(1792390942)


def test_instruments_header_does_not_mark_connection_authenticated() -> None:
    class FakeWebSocket:
        def __init__(self):
            self.messages = iter(["40", '451-["instruments/list",{"_placeholder":true,"num":0}]'])

        async def recv(self):
            return next(self.messages)

    websocket = AsyncWebSocketClient()
    websocket.websocket = FakeWebSocket()
    authenticated = []
    sent = []
    websocket.add_event_handler("authenticated", authenticated.append)

    async def fake_send_message(message):
        sent.append(message)

    websocket.send_message = fake_send_message
    asyncio.run(websocket._send_handshake('42["authorization",{}]'))

    assert not websocket.is_authenticated
    assert authenticated == []
    assert sent == ['42["authorization",{}]']


def test_authentication_debug_logs_do_not_expose_session() -> None:
    class FakeWebSocket:
        def __init__(self):
            self.messages = iter(["40", '42["s_authorization",{}]'])

        async def recv(self):
            return next(self.messages)

    secret = "sensitive-session-marker"
    websocket = AsyncWebSocketClient()
    websocket.websocket = FakeWebSocket()
    captured_logs = StringIO()
    sink_id = quotex_logger.add(captured_logs, level="DEBUG")

    async def fake_send_message(message):
        return None

    websocket.send_message = fake_send_message
    try:
        asyncio.run(websocket._send_handshake(f'42["authorization",{{"session":"{secret}"}}]'))
    finally:
        quotex_logger.remove(sink_id)

    assert secret not in captured_logs.getvalue()
    assert "Sent Quotex authorization frame" in captured_logs.getvalue()


def test_s_authorization_marks_connection_authenticated() -> None:
    websocket = AsyncWebSocketClient()

    asyncio.run(websocket._handle_auth_message('42["s_authorization",{}]'))

    assert websocket.is_authenticated
    assert websocket.websocket_is_connected


def test_authorization_reject_is_reported_and_not_authenticated() -> None:
    websocket = AsyncWebSocketClient()
    websocket.is_authenticated = True
    auth_errors = []
    websocket.add_event_handler("auth_error", auth_errors.append)

    asyncio.run(websocket._handle_auth_message('42["authorization/reject",{}]'))

    assert not websocket.is_authenticated
    assert not websocket.websocket_is_connected
    assert websocket._last_auth_error == {"message": "Authorization rejected by server"}
    assert auth_errors == [{"message": "Authorization rejected by server"}]


def test_wait_for_authentication_fails_immediately_after_server_reject() -> None:
    client = AsyncQuotexClient("session-value", is_demo=True)
    client._websocket._last_auth_error = {"message": "Authorization rejected by server"}

    with pytest.raises(AuthenticationError, match="Authorization rejected"):
        asyncio.run(client._wait_for_authentication(timeout=1.0))


def test_persistent_connection_auth_wait_rejects_stale_session() -> None:
    manager = ConnectionKeepAlive("session-value", is_demo=True)
    manager._websocket = AsyncWebSocketClient()
    manager._websocket._last_auth_error = {"message": "Authorization rejected by server"}

    with pytest.raises(ConnectionError, match="Authorization rejected"):
        asyncio.run(manager._wait_for_authorization())


def test_persistent_connection_stops_region_retries_after_auth_rejection(monkeypatch) -> None:
    class FakeWebSocket:
        _last_auth_error = {"message": "Authorization rejected by server"}

        def add_event_handler(self, *_args) -> None:
            pass

        async def connect(self, *_args) -> bool:
            return True

    manager = ConnectionKeepAlive("session-value", is_demo=True)
    fake_websocket = FakeWebSocket()
    manager._websocket = fake_websocket
    attempted = []
    monkeypatch.setattr(QUOTEX_REGIONS, "get_region", lambda name: f"wss://{name}.example")

    async def reject_authentication():
        attempted.append("DEMO")
        raise ConnectionError("Authorization rejected")

    manager._wait_for_authorization = reject_authentication

    connected = asyncio.run(manager.connect_with_keep_alive(regions=["DEMO", "LIVE"]))

    assert connected is False
    assert attempted == ["DEMO"]


def test_persistent_connection_registers_auth_error_handler_once(monkeypatch) -> None:
    class FakeKeepAlive:
        def __init__(self, *_args, **_kwargs) -> None:
            self.handlers = {}

        def add_event_handler(self, event, handler) -> None:
            self.handlers.setdefault(event, []).append(handler)

        async def connect_with_keep_alive(self, _regions) -> bool:
            return True

    monkeypatch.setattr(
        "api_quotex.connection_keep_alive.ConnectionKeepAlive",
        FakeKeepAlive,
    )
    client = AsyncQuotexClient("session-value", is_demo=True)

    assert asyncio.run(client._start_persistent_connection())
    assert client._keep_alive_manager.handlers["auth_error"] == [client._on_auth_error]


def test_persistent_auth_error_delegates_refresh_to_configured_provider(monkeypatch) -> None:
    client = AsyncQuotexClient(
        "session-value",
        is_demo=True,
        persistent_connection=True,
        authorization_refresh=lambda: asyncio.sleep(0, result="fresh-session"),
    )
    client._config.load_config = lambda: {
        "email": "demo@example.test",
        "password": "test-password",
    }
    refresh_calls = []

    async def refresh_with_login(*_args, **_kwargs):
        refresh_calls.append(True)
        return True, {"ssid": "unexpected-session"}

    async def ignore_error(**_kwargs) -> None:
        pass

    monkeypatch.setattr("api_quotex.client.get_ssid", refresh_with_login)
    monkeypatch.setattr(client._error_monitor, "record_error", ignore_error)

    asyncio.run(client._on_auth_error({"message": "Authorization rejected by server"}))

    assert refresh_calls == []


def test_reconnect_replaces_websocket_and_rebinds_forwarders(monkeypatch) -> None:
    class FakeWebSocket:
        def __init__(self) -> None:
            self.handlers = {}
            self.disconnected = False

        def add_event_handler(self, event, handler) -> None:
            self.handlers[event] = handler

        async def disconnect(self) -> None:
            self.disconnected = True

    manager = ConnectionKeepAlive("session-value", is_demo=True)
    stale_websocket = FakeWebSocket()
    manager._websocket = stale_websocket
    monkeypatch.setattr(manager, "_websocket_client_class", FakeWebSocket)
    manager._connection_stats["last_ping_time"] = 123.0
    manager._connection_stats["last_pong_time"] = 124.0

    asyncio.run(manager._reset_websocket())

    assert stale_websocket.disconnected
    assert manager._websocket is not stale_websocket
    assert "authenticated" in manager._websocket.handlers
    assert "orders_opened_list" in manager._websocket.handlers
    assert manager._connection_stats["last_ping_time"] is None
    assert manager._connection_stats["last_pong_time"] is None


def test_engine_pong_refreshes_keepalive_health_timestamp() -> None:
    manager = ConnectionKeepAlive("session-value", is_demo=True)
    manager._create_websocket()

    sent = []

    async def capture_send(message: str) -> None:
        sent.append(message)

    manager._websocket.send_message = capture_send
    asyncio.run(manager._websocket._process_message("2"))

    assert sent == ["3"]
    assert manager._connection_stats["last_pong_time"] is not None
    assert time.time() - manager._connection_stats["last_pong_time"] < 1


def test_persistent_keepalive_sends_engine_ping(monkeypatch) -> None:
    manager = ConnectionKeepAlive("session-value", is_demo=True)
    manager.is_connected = True
    sent = []

    class FakeWebSocket:
        async def send_message(self, message: str) -> None:
            sent.append(message)

    manager._websocket = FakeWebSocket()

    async def stop_after_first_ping(_seconds: float) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr("api_quotex.connection_keep_alive.asyncio.sleep", stop_after_first_ping)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(manager._ping_loop())

    assert sent == ["2"]
    assert manager._connection_stats["last_ping_time"] is not None


def test_reconnect_start_does_not_cancel_the_monitor_that_started_it() -> None:
    manager = ConnectionKeepAlive("session-value", is_demo=True)

    async def wait_forever():
        await asyncio.Event().wait()

    manager._health_monitor_loop = wait_forever
    manager._assets_request_loop = wait_forever

    async def start_tasks_from_monitor():
        current_task = asyncio.current_task()
        manager._reconnect_task = current_task
        manager._start_keep_alive_tasks()
        assert manager._reconnect_task is current_task
        assert not current_task.cancelling()
        tasks = (manager._health_task, manager._assets_request_task)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(start_tasks_from_monitor())


def test_reconnect_refreshes_rejected_auth_from_browser_provider(monkeypatch) -> None:
    class FakeWebSocket:
        def __init__(self, auth_error=None) -> None:
            self._last_auth_error = auth_error
            self.is_connected = False
            self.sent = []

        async def send_message(self, message) -> None:
            self.sent.append(message)

    async def run_test() -> None:
        refresh_calls = 0

        async def refresh_authorization() -> str:
            nonlocal refresh_calls
            refresh_calls += 1
            return "fresh-frame"

        manager = ConnectionKeepAlive(
            "expired-frame",
            is_demo=True,
            authorization_refresh=refresh_authorization,
        )
        manager.is_connected = False
        manager._websocket = FakeWebSocket({"message": "rejected"})

        async def reset_websocket() -> None:
            manager._websocket = FakeWebSocket()

        async def reconnect():
            assert manager.ssid == "fresh-frame"
            manager.is_connected = True
            manager._websocket.is_connected = True
            return True

        async def trigger_event(_event, data) -> None:
            assert data == {}

        manager._reset_websocket = reset_websocket
        manager.connect_with_keep_alive = reconnect
        manager._trigger_event_async = trigger_event
        sleep_calls = 0

        async def stop_after_reconnect(_seconds) -> None:
            nonlocal sleep_calls
            sleep_calls += 1
            if sleep_calls > 1:
                raise asyncio.CancelledError

        monkeypatch.setattr(
            "api_quotex.connection_keep_alive.asyncio.sleep",
            stop_after_reconnect,
        )
        with pytest.raises(asyncio.CancelledError):
            await manager._reconnection_monitor()

        assert refresh_calls == 1
        assert manager._websocket.sent == [
            '451-["instruments/list",{"_placeholder":true,"num":0}]'
        ]

    asyncio.run(run_test())


def test_health_monitor_marks_connection_down_when_heartbeat_stops(monkeypatch) -> None:
    class FakeWebSocket:
        is_connected = True

    manager = ConnectionKeepAlive("session-value", is_demo=True)
    manager.is_connected = True
    manager._websocket = FakeWebSocket()
    manager._connection_stats["last_ping_time"] = time.time()
    manager._connection_stats["last_pong_time"] = time.time() - 61
    sleep_calls = 0

    async def stop_after_health_check(_seconds) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls > 1:
            raise asyncio.CancelledError

    async def ignore_monitoring_error(**_kwargs) -> None:
        pass

    monkeypatch.setattr("api_quotex.connection_keep_alive.asyncio.sleep", stop_after_health_check)
    monkeypatch.setattr(
        "api_quotex.connection_keep_alive.error_monitor.record_error",
        ignore_monitoring_error,
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(manager._health_monitor_loop())

    assert not manager.is_connected


def test_persistent_adapter_forwards_opened_and_closed_order_lists() -> None:
    manager = ConnectionKeepAlive("session-value", is_demo=True)
    received = []
    manager.add_event_handler("orders_opened_list", lambda data: received.append(("opened", data)))
    manager.add_event_handler("orders_closed_list", lambda data: received.append(("closed", data)))

    async def forward_lists():
        await manager._forward_orders_opened_list([{"id": "open-1"}])
        await manager._forward_orders_closed_list([{"id": "closed-1"}])

    asyncio.run(forward_lists())

    assert received == [
        ("opened", [{"id": "open-1"}]),
        ("closed", [{"id": "closed-1"}]),
    ]


def test_binary_opened_order_list_populates_client_active_orders() -> None:
    client = AsyncQuotexClient("session-value", is_demo=True)
    client._pending_order_requests["1234"] = Order(
        asset="EURUSD",
        amount=1.0,
        direction=OrderDirection.CALL,
        duration=60,
        request_id="1234",
    )
    client._websocket._pending_binary_event = "orders/opened/list"

    asyncio.run(client._websocket._handle_candle_message(
        '\x04[{"id":"broker-order-1","requestId":"1234","asset":"EURUSD",'
        '"amount":1.0,"command":0,"duration":60,"openTime":1700000000}]'
    ))

    assert client._active_orders["1234"].broker_order_id == "broker-order-1"
    assert client._request_id_to_server_id["1234"] == "broker-order-1"
