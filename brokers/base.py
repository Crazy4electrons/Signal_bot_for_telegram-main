from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class NormalizedSignal:
    """Broker-agnostic normalized signal input."""

    signal_id: str
    signal_provider: str
    asset: str
    direction: str  # CALL/PUT (Pocket style)
    entry_time: datetime
    broker_hint: str | None = None


@dataclass(frozen=True)
class TradePlacement:
    trade_id: str
    asset: str
    direction: str
    entry_time: datetime
    level: int
    open_price: float
    amount: float


@runtime_checkable
class Broker(Protocol):
    """Minimal broker interface to support existing martingale flow."""

    broker_id: str

    async def connect(self) -> bool: ...
    async def disconnect(self) -> None: ...
    async def balance(self) -> float: ...

    async def place_call_put(
        self,
        *,
        asset: str,
        direction: str,  # CALL/PUT
        amount: float,
        timeframe_seconds: int,
        check_win: bool = False,
    ) -> TradePlacement: ...

    async def check_result(self, trade_id: str) -> dict[str, Any]: ...
