import asyncio
from datetime import datetime

import main
import pytest
from brokers.base import TradePlacement
from state_store import SignalBotStateStore


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    store = SignalBotStateStore(tmp_path / "state.sqlite3")
    monkeypatch.setattr(main, "runtime_state", store)
    return store


def _trade(trade_id: str = "initial", signal_id: str | None = None) -> main.TRADE:
    return main.TRADE(
        trade_id=trade_id,
        trade_details=main.TRADE_FIELDS(
            signal_provider="test-signal",
            asset="EURUSD_otc",
            direction="CALL",
            entry_time=datetime(2026, 10, 20, 10, 0),
            level=0,
            open_price=1.1,
            amount=1.0,
            broker_id="quotex",
            signal_id=signal_id,
        ),
    )


def test_loss_places_multiplier_recovery_and_win_closes_sequence(isolated_state, monkeypatch):
    class FakeBroker:
        def __init__(self):
            self.results = {
                "initial": {"result": "loss", "amount": 1.0},
                "recovery": {"result": "win", "profit": 1.8},
            }
            self.placements = []

        async def check_result(self, trade_id):
            return self.results[trade_id]

        async def balance(self):
            return 100.0

        async def place_call_put(self, **kwargs):
            self.placements.append(kwargs)
            return TradePlacement(
                trade_id="recovery",
                asset=kwargs["asset"],
                direction=kwargs["direction"],
                entry_time=datetime(2026, 10, 20, 10, 1),
                level=1,
                open_price=1.2,
                amount=kwargs["amount"],
            )

    broker = FakeBroker()
    risk = main.RISK_MANAGEMENT(
        initial_amount=1.0,
        martingale_levels=1,
        martingale_multiplier=2.0,
        max_trade_amount=2.0,
        max_sequence_exposure=3.0,
        timeframe=60,
    )
    pnl = []
    monkeypatch.setattr(main, "effective_risk_management", lambda _broker_id: risk)
    monkeypatch.setattr(main, "ensure_broker_connected", lambda _broker_id: _async_value(broker))
    monkeypatch.setattr(
        main,
        "record_broker_pnl",
        lambda broker_id, amount, _event_id=None: pnl.append((broker_id, amount)),
    )
    trade = _trade(signal_id="signal-1")
    isolated_state.save_signal("signal-1", "pending", {"broker_id": "quotex"})
    isolated_state.save_active_trade(
        trade.trade_id,
        trade.trade_details.model_dump(mode="json"),
        signal_id="signal-1",
    )
    monkeypatch.setattr(main, "trade_details", {"initial": trade.trade_details})
    monkeypatch.setattr(main, "closed_trades", {})
    monkeypatch.setattr(main, "broker_balances", {})
    monkeypatch.setattr(main, "random", lambda: 0)
    monkeypatch.setattr(main.asyncio, "sleep", _async_sleep)

    result = asyncio.run(main.manage_martingale(trade))

    assert result is True
    assert [(placement["amount"], placement["direction"]) for placement in broker.placements] == [(2.0, "CALL")]
    assert main.closed_trades["initial"]["trade_details"]["result"] == "Loss"
    assert main.closed_trades["recovery"]["trade_details"]["result"] == "Won"
    assert main.closed_trades["recovery"]["trade_details"]["amount"] == 2.0
    assert main.trade_details == {}
    assert pnl == [("quotex", -1.0), ("quotex", 1.8)]
    assert isolated_state.recent_signals()[0]["status"] == "completed"


def test_unknown_result_does_not_report_win_or_place_recovery(isolated_state, monkeypatch):
    class FakeBroker:
        async def check_result(self, _trade_id):
            return {"result": "pending"}

        async def place_call_put(self, **_kwargs):
            raise AssertionError("Unknown outcomes must not start a recovery order")

    trade = _trade()
    risk = main.RISK_MANAGEMENT()
    monkeypatch.setattr(main, "effective_risk_management", lambda _broker_id: risk)
    monkeypatch.setattr(main, "ensure_broker_connected", lambda _broker_id: _async_value(FakeBroker()))
    monkeypatch.setattr(main, "trade_details", {"initial": trade.trade_details})
    monkeypatch.setattr(main, "closed_trades", {})
    monkeypatch.setattr(main, "record_broker_pnl", lambda *_args: pytest.fail("Unknown result must not change P/L"))

    result = asyncio.run(main.manage_martingale(trade))

    assert result is False
    assert main.closed_trades["initial"]["trade_details"]["result"] == "Unknown"
    assert main.trade_details == {}


def test_draw_is_not_treated_as_a_loss_or_win(isolated_state, monkeypatch):
    class FakeBroker:
        async def check_result(self, _trade_id):
            return {"result": "draw", "profit": 0}

        async def place_call_put(self, **_kwargs):
            raise AssertionError("A draw must end the sequence without a recovery")

    trade = _trade()
    monkeypatch.setattr(main, "effective_risk_management", lambda _broker_id: main.RISK_MANAGEMENT())
    monkeypatch.setattr(main, "ensure_broker_connected", lambda _broker_id: _async_value(FakeBroker()))
    monkeypatch.setattr(main, "trade_details", {"initial": trade.trade_details})
    monkeypatch.setattr(main, "closed_trades", {})
    monkeypatch.setattr(main, "record_broker_pnl", lambda *_args: None)
    monkeypatch.setattr(main, "random", lambda: 0)
    monkeypatch.setattr(main.asyncio, "sleep", _async_sleep)

    result = asyncio.run(main.manage_martingale(trade))

    assert result is True
    assert main.closed_trades["initial"]["trade_details"]["result"] == "Draw"


async def _async_value(value):
    return value


async def _async_sleep(_seconds):
    return None


def test_failed_recovery_placement_is_review_only(isolated_state, monkeypatch):
    class FakeBroker:
        async def check_result(self, _trade_id):
            return {"result": "loss", "amount": 1.0}

        async def balance(self):
            return 100.0

        async def place_call_put(self, **_kwargs):
            raise RuntimeError("placement response was lost")

    trade = _trade(signal_id="signal-1")
    isolated_state.save_signal("signal-1", "placed", {"broker_id": "quotex"})
    isolated_state.save_active_trade(
        trade.trade_id,
        trade.trade_details.model_dump(mode="json"),
        signal_id="signal-1",
    )
    risk = main.RISK_MANAGEMENT(
        initial_amount=1.0,
        martingale_levels=1,
        martingale_multiplier=2.0,
        max_trade_amount=2.0,
        max_sequence_exposure=3.0,
        timeframe=60,
    )
    monkeypatch.setattr(main, "effective_risk_management", lambda _broker_id: risk)
    monkeypatch.setattr(
        main, "ensure_broker_connected", lambda _broker_id: _async_value(FakeBroker())
    )
    monkeypatch.setattr(main, "trade_details", {trade.trade_id: trade.trade_details})
    monkeypatch.setattr(main, "closed_trades", {})
    monkeypatch.setattr(main, "broker_balances", {})
    monkeypatch.setattr(main, "random", lambda: 0)
    monkeypatch.setattr(main.asyncio, "sleep", _async_sleep)

    result = asyncio.run(main.manage_martingale(trade))

    assert result is False
    assert main.closed_trades == {}
    assert main.trade_details == {}
    assert isolated_state.load_active_trades() == [
        {
            "trade_id": "initial",
            "state": "needs_review",
            "trade_details": {
                **trade.trade_details.model_dump(mode="json"),
                "amount": 2.0,
                "level": 1,
            },
        }
    ]
    assert isolated_state.recent_signals()[0]["status"] == "needs_review"
