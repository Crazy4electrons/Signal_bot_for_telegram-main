import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import main
from brokers.pocket_option import PocketOptionBroker
from brokers.quotex import QuotexBroker
from telegram_listener import coerce_channel


def response_body(response):
    return json.loads(response.body)


def test_broker_status_reports_readiness_without_session_values(monkeypatch):
    monkeypatch.setenv("POCKETOPTION_SSID", "pocket-secret")
    monkeypatch.setenv("POCKETOPTION_ACCOUNT_TYPE", "DEMO")
    monkeypatch.setenv("QUOTEX_SSID", "quotex-secret")
    monkeypatch.setenv("QUOTEX_ACCOUNT_TYPE", "REAL")
    monkeypatch.delenv("QUOTEX_IS_DEMO", raising=False)
    monkeypatch.setattr(main, "connected_brokers", {"quotex"})
    monkeypatch.setattr(main, "broker_registry", {"quotex": SimpleNamespace(is_connected=True)})

    body = response_body(asyncio.run(main.get_broker_status()))

    assert body["brokers"] == [
        {
            "id": "pocket_option",
            "name": "Pocket Option",
            "account_type": "DEMO",
            "configured": True,
            "connected": False,
        },
        {
            "id": "quotex",
            "name": "Quotex",
            "account_type": "REAL",
            "configured": True,
            "connected": True,
        },
    ]
    assert "pocket-secret" not in json.dumps(body)
    assert "quotex-secret" not in json.dumps(body)


def test_broker_status_detects_disconnected_quotex_socket(monkeypatch):
    monkeypatch.setenv("QUOTEX_SSID", "quotex-secret")
    monkeypatch.setattr(main, "connected_brokers", {"quotex"})
    monkeypatch.setattr(main, "broker_registry", {"quotex": SimpleNamespace(is_connected=False)})

    body = response_body(asyncio.run(main.get_broker_status()))

    quotex = next(broker for broker in body["brokers"] if broker["id"] == "quotex")
    assert quotex["connected"] is False


def test_pocket_option_connection_status_reads_underlying_websocket(monkeypatch):
    class RawClient:
        connected = False

        def is_connected(self):
            return self.connected

    broker = PocketOptionBroker(broker_id="pocket_option", ssid="session")
    broker._client = SimpleNamespace(client=RawClient())
    monkeypatch.setattr(main, "connected_brokers", {"pocket_option"})

    assert broker.is_connected is False

    broker._client.client.connected = True
    assert broker.is_connected is True


def test_pocket_option_connect_establishes_socket_before_balance(monkeypatch):
    calls = []

    class FakeClient:
        client = SimpleNamespace(is_connected=lambda: True)

        def __init__(self, _ssid):
            pass

        async def connect(self):
            calls.append("connect")

        async def balance(self):
            calls.append("balance")
            return 42.5

    monkeypatch.setattr("brokers.pocket_option.PocketOptionAsync", FakeClient)
    broker = PocketOptionBroker(broker_id="pocket_option", ssid="session")

    assert asyncio.run(broker.connect()) is True
    assert calls == ["connect", "balance"]


def test_quotex_uses_existing_browser_profile_by_default(monkeypatch):
    monkeypatch.setattr(main, "broker_registry", {})
    monkeypatch.setenv("QUOTEX_IS_DEMO", "true")
    monkeypatch.delenv("QUOTEX_SSID", raising=False)
    monkeypatch.delenv("QUOTEX_BROWSER_SESSION", raising=False)
    monkeypatch.delenv("QUOTEX_BROWSER_HEADLESS", raising=False)
    monkeypatch.delenv("QUOTEX_BROWSER_AUTH_TIMEOUT", raising=False)

    broker = asyncio.run(main.get_or_create_broker("quotex"))

    assert broker.ssid is None
    assert broker.use_browser_session is True
    assert broker.browser_headless is False
    assert broker.browser_auth_timeout == 120


def test_quotex_can_opt_out_of_existing_browser_profile(monkeypatch):
    monkeypatch.setattr(main, "broker_registry", {})
    monkeypatch.setenv("QUOTEX_BROWSER_SESSION", "false")

    broker = asyncio.run(main.get_or_create_broker("quotex"))

    assert broker.use_browser_session is False


def test_quotex_startup_leaves_authentication_to_fork(monkeypatch):
    monkeypatch.delenv("QUOTEX_SSID", raising=False)
    monkeypatch.delenv("QUOTEX_EMAIL", raising=False)
    monkeypatch.delenv("QUOTEX_PASSWORD", raising=False)
    monkeypatch.delenv("QUOTEX_BROWSER_SESSION", raising=False)

    asyncio.run(main.ensure_broker_session("quotex"))


def test_quotex_fork_login_uses_configured_credentials(monkeypatch):
    import api_quotex.login

    expected_ssid = '42["authorization",{"session":"demo-session","isDemo":1,"tournamentId":0}]'
    calls = []

    async def fake_get_ssid(**kwargs):
        calls.append(kwargs)
        return True, {"ssid": expected_ssid}

    monkeypatch.delenv("QUOTEX_SSID", raising=False)
    monkeypatch.setenv("QUOTEX_EMAIL", "demo@example.test")
    monkeypatch.setenv("QUOTEX_PASSWORD", "test-password")
    monkeypatch.setenv("QUOTEX_EMAIL_PASS", "test-email-password")
    monkeypatch.setenv("QUOTEX_LANG", "en")
    monkeypatch.setattr(api_quotex.login, "get_ssid", fake_get_ssid)

    broker = QuotexBroker(is_demo=True, use_browser_session=False)
    ssid = asyncio.run(broker._get_fork_authorization())

    assert ssid == expected_ssid
    assert broker.ssid == expected_ssid
    assert calls == [
        {
            "email": "demo@example.test",
            "password": "test-password",
            "email_pass": "test-email-password",
            "lang": "en",
            "is_demo": True,
        }
    ]


def test_ensure_broker_connected_reconnects_stale_quotex_adapter(monkeypatch):
    class DisconnectedBroker:
        broker_id = "quotex"
        is_connected = False

        def __init__(self):
            self.disconnected = 0
            self.connected = 0

        async def disconnect(self):
            self.disconnected += 1

        async def connect(self):
            self.connected += 1
            self.is_connected = True
            return True

    broker = DisconnectedBroker()

    async def get_broker(broker_id):
        assert broker_id == "quotex"
        return broker

    monkeypatch.setattr(main, "get_or_create_broker", get_broker)
    monkeypatch.setattr(main, "broker_registry", {"quotex": broker})
    monkeypatch.setattr(main, "connected_brokers", {"quotex"})
    monkeypatch.setattr(main, "broker_connection_locks", {})

    result = asyncio.run(main.ensure_broker_connected("quotex"))

    assert result is broker
    assert broker.disconnected == 1
    assert broker.connected == 1
    assert "quotex" in main.connected_brokers


def test_connect_broker_reads_account_without_ordering(monkeypatch):
    class ReadOnlyBroker:
        async def balance(self):
            return 42.5

    async def ensure_connected(broker_id):
        assert broker_id == "quotex"
        return ReadOnlyBroker()

    monkeypatch.setattr(main, "ensure_broker_connected", ensure_connected)
    monkeypatch.setattr(main, "broker_balances", {})

    body = response_body(asyncio.run(main.connect_broker("quotex")))

    assert body == {
        "broker_id": "quotex",
        "account_type": "DEMO",
        "balance": 42.5,
        "connected": True,
    }
    assert main.broker_balances == {"quotex": 42.5}


def test_connect_broker_returns_explicit_service_error(monkeypatch):
    async def fail_connection(_broker_id):
        raise RuntimeError("session rejected")

    monkeypatch.setattr(main, "ensure_broker_connected", fail_connection)

    with pytest.raises(HTTPException) as error:
        asyncio.run(main.connect_broker("pocket_option"))

    assert error.value.status_code == 503
    assert "session rejected" in error.value.detail


def test_account_read_keeps_each_broker_balance_independent(monkeypatch):
    class ReadOnlyBroker:
        async def balance(self):
            return 86.25

    async def ensure_connected(broker_id):
        assert broker_id == "quotex"
        return ReadOnlyBroker()

    monkeypatch.setattr(main, "ensure_broker_connected", ensure_connected)
    monkeypatch.setattr(main, "broker_balances", {})
    monkeypatch.setattr(main, "broker_pnl_today", {})
    monkeypatch.setattr(main, "broker_lifespan", {})

    body = response_body(asyncio.run(main.get_account_details("quotex")))

    assert body["broker_id"] == "quotex"
    assert body["balance"] == 86.25
    assert main.broker_balances == {"quotex": 86.25}


def test_account_read_failure_is_not_returned_as_a_success(monkeypatch):
    async def fail_connection(_broker_id):
        raise RuntimeError("session expired")

    monkeypatch.setattr(main, "ensure_broker_connected", fail_connection)

    with pytest.raises(HTTPException) as error:
        asyncio.run(main.get_account_details("quotex"))

    assert error.value.status_code == 503
    assert "session expired" in error.value.detail


def test_trade_history_endpoints_filter_by_broker(monkeypatch):
    monkeypatch.setattr(
        main,
        "trade_details",
        {
            "po-order": main.TRADE_FIELDS(
                signal_provider="alpha",
                asset="EURUSD_otc",
                direction="CALL",
                entry_time=main.datetime.now(),
                level=0,
                open_price=1.0,
                amount=1.0,
                broker_id="pocket_option",
            ),
            "qx-order": main.TRADE_FIELDS(
                signal_provider="beta",
                asset="AUDCAD_otc",
                direction="PUT",
                entry_time=main.datetime.now(),
                level=0,
                open_price=1.0,
                amount=1.0,
                broker_id="quotex",
            ),
        },
    )
    monkeypatch.setattr(
        main,
        "closed_trades",
        {
            "po-closed": {"broker_id": "pocket_option", "trade_details": {}},
            "qx-closed": {"broker_id": "quotex", "trade_details": {}},
        },
    )

    open_body = response_body(asyncio.run(main.get_open_trades("quotex")))
    closed_body = response_body(asyncio.run(main.get_closed_trades("quotex")))

    assert [trade["trade_id"] for trade in open_body["open_trades"]] == ["qx-order"]
    assert list(closed_body["closed_trades"]) == ["qx-closed"]


def test_closed_trades_endpoint_returns_full_history(monkeypatch):
    monkeypatch.setattr(
        main,
        "closed_trades",
        {
            f"trade-{index}": {
                "broker_id": "quotex",
                "trade_details": {"result": "Won"},
            }
            for index in range(12)
        },
    )

    response = response_body(asyncio.run(main.get_closed_trades("quotex")))

    assert len(response["closed_trades"]) == 12


def test_current_signals_are_filtered_by_broker(tmp_path, monkeypatch):
    store = main.SignalBotStateStore(tmp_path / "state.sqlite3")
    monkeypatch.setattr(main, "runtime_state", store)
    store.save_signal(
        "po-signal",
        "pending",
        main.SIGNAL_FIELDS(
            signal_provider="alpha",
            asset="EURUSD",
            direction="CALL",
            entry_time=main.datetime.now(),
            broker_id="pocket_option",
        ).model_dump(mode="json"),
    )
    store.save_signal(
        "qx-signal",
        "pending",
        main.SIGNAL_FIELDS(
            signal_provider="beta",
            asset="AUDCAD",
            direction="PUT",
            entry_time=main.datetime.now(),
            broker_id="quotex",
        ).model_dump(mode="json"),
    )

    body = response_body(asyncio.run(main.get_current_signals("quotex")))

    assert [signal["signal_provider"] for signal in body["signals"]] == ["beta"]


def test_signal_without_explicit_broker_has_no_fallback(monkeypatch):
    monkeypatch.setattr(main, "signal_router", None)
    signal = main.SIGNAL_FIELDS(
        signal_provider="alpha",
        asset="EURUSD",
        direction="CALL",
        entry_time=main.datetime.now(),
    )

    with pytest.raises(HTTPException) as error:
        main.route_signal_to_broker(signal)

    assert error.value.status_code == 400
    assert "no broker destination" in error.value.detail


def test_channel_broker_is_normalized_and_validated():
    channel, error = coerce_channel(
        {
            "channel": "@signals",
            "broker": "Pocket-Option",
            "timezone": "Etc/GMT-2",
        }
    )
    invalid_channel, invalid_error = coerce_channel(
        {
            "channel": "@signals",
            "broker": "unknown",
            "timezone": "Etc/GMT-2",
        }
    )

    assert error is None
    assert channel is not None and channel.broker == "pocket_option"
    assert invalid_channel is None
    assert "pocket_option" in invalid_error


def test_channel_requires_explicit_timezone_and_broker():
    missing_timezone, timezone_error = coerce_channel(
        {"channel": "@signals", "broker": "quotex"}
    )
    missing_broker, broker_error = coerce_channel(
        {"channel": "@signals", "timezone": "Etc/GMT-2"}
    )

    assert missing_timezone is None
    assert timezone_error == "timezone must be configured for this channel"
    assert missing_broker is None
    assert broker_error == "broker must be selected for this channel"


def test_telegram_channel_settings_override_signal_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(
        main,
        "runtime_state",
        main.SignalBotStateStore(tmp_path / "signal_bot.sqlite3"),
    )
    timezone = main.risk_management.local_timezone
    entry_time = "12:10"

    class FixedDateTime(main.datetime):
        @classmethod
        def now(cls, tz=None):
            fixed = cls(2025, 1, 1, 12, 0)
            return tz.localize(fixed) if tz else fixed

    monkeypatch.setattr(main, "datetime", FixedDateTime)
    monkeypatch.setattr(
        main,
        "parse_macrodroid_trade_data",
        lambda _text: {
            "asset": "EURUSD",
            "direction": "CALL",
            "time": entry_time,
            "signal_provider": "telegram",
            "timezone": "Etc/GMT+8",
            "broker": "pocket_option",
            "channel": "untrusted-channel-hint",
        },
    )
    monkeypatch.setattr(main, "random", lambda: 0)
    monkeypatch.setattr(main, "Signals", {})
    channel = main.ChannelConfig(
        channel="@signals",
        name="configured-source",
        timezone=timezone,
        broker="quotex",
    )

    signal = asyncio.run(main.parse_signal("signal", channel_config=channel))

    assert signal is not None
    assert signal.signal_details.broker_id == "quotex"
    assert signal.signal_details.channel_key == "configured-source"
    assert signal.signal_details.entry_time.hour == 12
    assert signal.signal_details.entry_time.minute == 10


def test_channel_routes_are_keyed_by_id_and_label():
    routes = main.broker_routes_for_channels([
        {"channel": "-100123", "name": "morning", "broker": "quotex"},
        {"channel": "@other", "broker": None},
    ])

    assert routes == {"-100123": "quotex", "morning": "quotex"}


def test_per_broker_risk_inherits_unset_global_fields(monkeypatch):
    monkeypatch.setattr(
        main,
        "risk_management",
        main.RISK_MANAGEMENT(initial_amount=2, timeframe=45, max_trade_amount=10),
    )
    monkeypatch.setattr(
        main,
        "risk_overrides",
        {"quotex": {"initial_amount": 0.5, "martingale_enabled": False}},
    )

    effective = main.effective_risk_management("quotex")

    assert effective.initial_amount == 0.5
    assert effective.timeframe == 45
    assert effective.max_trade_amount == 10
    assert effective.martingale_enabled is False


def test_saving_empty_broker_override_clears_it_and_persists(tmp_path, monkeypatch):
    settings_path = tmp_path / "risk-settings.json"
    monkeypatch.setenv("RISK_SETTINGS_FILE", str(settings_path))
    monkeypatch.setattr(main, "risk_management", main.RISK_MANAGEMENT())
    monkeypatch.setattr(
        main,
        "risk_overrides",
        {"quotex": {"initial_amount": 0.5}},
    )

    response = asyncio.run(
        main.set_broker_risk_override("quotex", main.BROKER_RISK_OVERRIDE())
    )
    saved = json.loads(settings_path.read_text(encoding="utf-8"))

    assert response.status_code == 200
    assert "quotex" not in main.risk_overrides
    assert saved["broker_overrides"] == {}
    assert saved["global"]["initial_amount"] == 1.0


def test_loading_risk_settings_rejects_unknown_broker_override(tmp_path, monkeypatch):
    settings_path = tmp_path / "risk-settings.json"
    settings_path.write_text(
        json.dumps({
            "global": {"initial_amount": 2.0},
            "broker_overrides": {"unknown": {"initial_amount": 0.5}},
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("RISK_SETTINGS_FILE", str(settings_path))
    original_global = main.risk_management
    original_overrides = dict(main.risk_overrides)

    with pytest.raises(RuntimeError, match="unsupported broker overrides"):
        main.load_risk_settings()

    assert main.risk_management is original_global
    assert main.risk_overrides == original_overrides
