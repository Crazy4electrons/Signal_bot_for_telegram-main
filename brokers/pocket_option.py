from __future__ import annotations

from dataclasses import dataclass
import asyncio
import logging
import os
from typing import Any
from datetime import datetime, timezone

from BinaryOptionsToolsV2.pocketoption import PocketOptionAsync

from .base import Broker, TradePlacement

BALANCE_TIMEOUT_SECONDS = 15
logger = logging.getLogger(__name__)


@dataclass
class PocketOptionBroker(Broker):
    """Adapter for the existing Pocket Option workflow.

    NOTE: This adapter is intentionally minimal and keeps compatibility
    with the current martingale logic in main.py.
    """

    broker_id: str = "pocket_option"
    _ssid: str | None = None
    _client: PocketOptionAsync | None = None
    _trade_timeframes: dict[str, int] | None = None

    def __init__(self, *, broker_id: str, ssid: str | None) -> None:
        self.broker_id = broker_id
        self._ssid = ssid
        self._client = None
        self._trade_timeframes = {}

    @property
    def is_connected(self) -> bool:
        if self._client is None:
            return False
        return bool(self._client.client.is_connected())

    async def connect(self) -> bool:
        ssid = self._ssid or os.getenv("POCKETOPTION_SSID")
        if ssid:
            try:
                return await self._connect_with_ssid(ssid)
            except Exception as exc:
                logger.warning(
                    "Saved Pocket Option authorization could not be verified; "
                    "attempting browser sign-in (%s)",
                    type(exc).__name__,
                )

        from playwright_scraper import capture_session

        account_type = os.getenv("POCKETOPTION_ACCOUNT_TYPE", "DEMO").strip().upper()
        session = await capture_session(
            "pocket_option",
            account_type,
            email=os.getenv("POCKETOPTION_EMAIL"),
            password=os.getenv("POCKETOPTION_PASSWORD"),
            headless=os.getenv("POCKETOPTION_SCRAPER_HEADLESS", "false").strip().lower()
            in {"1", "true", "yes", "on"},
            timeout_seconds=int(os.getenv("POCKETOPTION_SCRAPER_TIMEOUT", "120")),
        )
        self._ssid = str(session["ssid"])
        return await self._connect_with_ssid(self._ssid)

    async def _connect_with_ssid(self, ssid: str) -> bool:
        client = PocketOptionAsync(ssid)
        self._client = client
        try:
            await client.connect()
            await asyncio.wait_for(
                client.balance(), timeout=BALANCE_TIMEOUT_SECONDS
            )
        except Exception:
            self._client = None
            await client.disconnect()
            raise
        return True

    async def disconnect(self) -> None:
        if self._client is not None:
            client, self._client = self._client, None
            await client.disconnect()

    async def balance(self) -> float:
        assert self._client is not None
        return float(
            await asyncio.wait_for(
                self._client.balance(), timeout=BALANCE_TIMEOUT_SECONDS
            )
        )

    async def place_call_put(
        self,
        *,
        asset: str,
        direction: str,
        amount: float,
        timeframe_seconds: int,
        check_win: bool = False,
    ) -> TradePlacement:
        assert self._client is not None
        # PocketOptionAsync expects buy/sell via api.buy/api.sell.
        # main.py currently maps CALL->buy and PUT->sell.
        if direction.upper() == "CALL":
            buy_id, details = await self._client.buy(asset, amount, timeframe_seconds)
        elif direction.upper() == "PUT":
            buy_id, details = await self._client.sell(asset, amount, timeframe_seconds)
        else:
            raise ValueError(f"Unsupported direction {direction!r} for pocket")

        # Pocket details model varies; preserve minimal fields.
        # main.py already tracks entry/open_price/amount.
        trade_id = str(buy_id)
        if isinstance(details, dict):
            entry_time = details.get("open_time") or details.get("openTime")
            open_price = details.get("price") or details.get("open_price") or details.get("openPrice") or 0.0
        else:
            entry_time = None
            open_price = 0.0
        if isinstance(entry_time, str):
            try:
                entry_time = datetime.fromisoformat(entry_time)
            except ValueError:
                entry_time = None
        if self._trade_timeframes is not None:
            self._trade_timeframes[trade_id] = int(timeframe_seconds)
        return TradePlacement(
            trade_id=trade_id,
            asset=asset,
            direction=direction.upper(),
            entry_time=entry_time or datetime.now(timezone.utc).replace(tzinfo=None),
            level=0,
            open_price=float(open_price),
            amount=float(amount),
        )

    async def check_result(self, trade_id: str) -> dict[str, Any]:
        assert self._client is not None
        timeframe = (self._trade_timeframes or {}).get(trade_id, 300)
        result = await self._client.check_win(
            trade_id,
            timeout_seconds=max(timeframe + 45, 90),
        )
        # Preserve existing main.py expectation: api.check_win(...) returns dict with 'result'
        return result
