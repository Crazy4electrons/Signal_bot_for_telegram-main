import sqlite3
from datetime import date, datetime

import main
from state_store import SignalBotStateStore


def _trade_details(**overrides):
    details = {
        "signal_provider": "test-provider",
        "asset": "EURUSD_otc",
        "direction": "CALL",
        "entry_time": datetime(2026, 10, 20, 10, 0).isoformat(),
        "level": 0,
        "open_price": 1.1,
        "amount": 1.0,
        "broker_id": "quotex",
        "signal_id": "test-signal",
    }
    return {**details, **overrides}


def test_migration_preserves_existing_telegram_table(tmp_path):
    database = tmp_path / "state.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE telegram_seen_messages (message_id TEXT PRIMARY KEY)")
        connection.execute(
            "INSERT INTO telegram_seen_messages(message_id) VALUES ('already-seen')"
        )

    store = SignalBotStateStore(database)

    assert store.load_active_trades() == []
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT message_id FROM telegram_seen_messages"
        ).fetchone() == ("already-seen",)


def test_signal_statuses_and_duplicate_signals_are_persistent(tmp_path):
    database = tmp_path / "state.sqlite3"
    store = SignalBotStateStore(database)
    payload = _trade_details()

    assert store.save_signal("signal-1", "pending", payload) is True
    assert store.save_signal("signal-1", "pending", payload) is False
    store.set_signal_status("signal-1", "placing")

    restarted_store = SignalBotStateStore(database)
    assert restarted_store.recent_signals() == [
        {"signal_id": "signal-1", "status": "placing", **payload}
    ]


def test_restart_interrupts_unfinished_work_without_replaying_it(tmp_path, monkeypatch):
    store = SignalBotStateStore(tmp_path / "state.sqlite3")
    pending = _trade_details(signal_id="pending-signal")
    placing = _trade_details(signal_id="placing-signal")
    store.save_signal("pending-signal", "pending", pending)
    store.save_signal("placing-signal", "placing", placing)
    store.save_active_trade(
        "confirmed-trade",
        _trade_details(signal_id="confirmed-signal"),
        state="monitoring",
    )
    store.save_active_trade(
        "ambiguous-trade",
        _trade_details(signal_id="placing-signal"),
        state="placing",
    )
    monkeypatch.setattr(main, "runtime_state", store)
    monkeypatch.setattr(main, "runtime_state_loaded", False)
    monkeypatch.setattr(main, "Signals", {})
    monkeypatch.setattr(main, "trade_details", {})
    monkeypatch.setattr(main, "closed_trades", {})

    main.restore_runtime_state()

    assert set(main.trade_details) == {"confirmed-trade"}
    persisted_signals = {
        item["signal_id"]: item["status"] for item in store.recent_signals()
    }
    assert persisted_signals == {
        "placing-signal": "needs_review",
        "pending-signal": "interrupted",
    }
    assert {
        item["trade_id"]: item["state"] for item in store.load_active_trades()
    } == {
        "confirmed-trade": "monitoring",
        "ambiguous-trade": "needs_review",
    }


def test_trade_rollover_and_closed_history_survive_restart(tmp_path):
    database = tmp_path / "state.sqlite3"
    store = SignalBotStateStore(database)
    store.save_active_trade("first", _trade_details())
    store.roll_active_trade(
        "first",
        "recovery",
        _trade_details(level=1, amount=2.0),
        {"broker_id": "quotex", "trade_details": {"result": "Loss"}},
        signal_id="test-signal",
    )

    restarted_store = SignalBotStateStore(database)

    assert restarted_store.load_active_trades() == [
        {
            "trade_id": "recovery",
            "state": "monitoring",
            "trade_details": _trade_details(level=1, amount=2.0),
        }
    ]
    assert restarted_store.load_closed_trades() == [
        ("first", {"broker_id": "quotex", "trade_details": {"result": "Loss"}})
    ]


def test_pnl_event_ids_prevent_duplicate_totals(tmp_path):
    store = SignalBotStateStore(tmp_path / "state.sqlite3")
    trading_date = date(2026, 10, 20)

    assert store.record_pnl_event("trade-1:loss", "quotex", trading_date, -1.0)
    assert not store.record_pnl_event("trade-1:loss", "quotex", trading_date, -1.0)
    assert store.record_pnl_event("trade-1:settlement", "quotex", trading_date, 1.8)

    daily, lifetime = store.pnl_totals(trading_date)
    assert daily == {"quotex": 0.8}
    assert lifetime == {"quotex": 0.8}
