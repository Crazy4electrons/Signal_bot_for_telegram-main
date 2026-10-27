from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from api_quotex.client import AsyncQuotexClient
from api_quotex.models import OrderDirection

from .base import Broker, TradePlacement


@dataclass
class QuotexBroker(Broker):
    broker_id: str = "quotex"
    # Use the persistent browser profile for authorization; fork login is optional.
    ssid: str | None = None
    is_demo: bool = True
    regions: list[str] | None = None
    use_browser_session: bool = True
    browser_headless: bool = False
    browser_auth_timeout: int = 120

    _client: AsyncQuotexClient | None = field(default=None, init=False, repr=False)
    _browser_session: Any | None = field(default=None, init=False, repr=False)

    @property
    def is_connected(self) -> bool:
        return bool(self._client and self._client.is_connected)

    async def connect(self) -> bool:
        if self._client is not None:
            await self._client.disconnect()

        authorization_refresh = None
        if self.use_browser_session:
            from playwright_scraper import QuotexBrowserSession
            from api_quotex.login import _infer_is_demo_from_ssid

            if self._browser_session is None:
                self._browser_session = QuotexBrowserSession(
                    is_demo=self.is_demo,
                    headless=self.browser_headless,
                    timeout_seconds=self.browser_auth_timeout,
                    email=os.getenv("QUOTEX_EMAIL"),
                    password=os.getenv("QUOTEX_PASSWORD"),
                )
            saved_ssid = self.ssid or os.getenv("QUOTEX_SSID")
            if saved_ssid and _infer_is_demo_from_ssid(
                saved_ssid, default_demo=not self.is_demo
            ) == self.is_demo:
                ssid = saved_ssid
                self.ssid = ssid
            else:
                if self._browser_session.is_started:
                    ssid = await self._browser_session.refresh_authorization()
                else:
                    ssid = await self._browser_session.start()
                self.ssid = ssid
            authorization_refresh = self._refresh_browser_authorization
        else:
            ssid = await self._get_fork_authorization()
            authorization_refresh = self._refresh_fork_authorization

        self._client = AsyncQuotexClient(
            ssid=ssid,
            is_demo=self.is_demo,
            authorization_refresh=authorization_refresh,
        )
        ok = await self._client.connect(regions=self.regions, persistent=True)
        if not ok:
            keep_alive = self._client._keep_alive_manager
            websocket = getattr(keep_alive, "_websocket", None)
            auth_error = getattr(websocket, "_last_auth_error", None)
            if auth_error or (self.use_browser_session and self.ssid):
                account_type = "DEMO" if self.is_demo else "REAL"
                if self.use_browser_session:
                    await self._client.disconnect()
                    ssid = await self._refresh_browser_authorization()
                    self.ssid = ssid
                    self._client = AsyncQuotexClient(
                        ssid=ssid,
                        is_demo=self.is_demo,
                        authorization_refresh=authorization_refresh,
                    )
                    ok = await self._client.connect(regions=self.regions, persistent=True)
                    if ok:
                        return True
                    raise RuntimeError(
                        f"Quotex rejected the refreshed browser {account_type} authorization."
                    )
                if authorization_refresh is not None:
                    await self._client.disconnect()
                    ssid = await authorization_refresh()
                    self._client = AsyncQuotexClient(
                        ssid=ssid,
                        is_demo=self.is_demo,
                        authorization_refresh=authorization_refresh,
                    )
                    ok = await self._client.connect(regions=self.regions, persistent=True)
                    if ok:
                        return True
                raise RuntimeError(
                    f"Quotex rejected the saved {account_type} authorization after the fork login refresh."
                )
        return bool(ok)

    async def _refresh_browser_authorization(self) -> str:
        if self._browser_session is None:
            raise RuntimeError("Quotex browser session is not running")
        ssid = await self._browser_session.refresh_authorization()
        self.ssid = ssid
        return ssid

    async def _refresh_fork_authorization(self) -> str:
        return await self._get_fork_authorization(force_refresh=True)

    async def _get_fork_authorization(self, *, force_refresh: bool = False) -> str:
        from api_quotex.login import _infer_is_demo_from_ssid, get_ssid

        ssid = self.ssid or os.getenv("QUOTEX_SSID")
        if ssid and _infer_is_demo_from_ssid(ssid, default_demo=not self.is_demo) != self.is_demo:
            raise RuntimeError(
                f"Saved Quotex authorization is not for the configured "
                f"{'DEMO' if self.is_demo else 'LIVE'} account."
            )
        if ssid and not force_refresh:
            self.ssid = ssid
            return ssid

        success, session_data = await get_ssid(
            email=os.getenv("QUOTEX_EMAIL"),
            password=os.getenv("QUOTEX_PASSWORD"),
            email_pass=os.getenv("QUOTEX_EMAIL_PASS"),
            lang=os.getenv("QUOTEX_LANG", "en"),
            is_demo=self.is_demo,
        )
        if not success or not session_data.get("ssid"):
            raise RuntimeError(
                f"The Quotex fork could not obtain a "
                f"{'DEMO' if self.is_demo else 'LIVE'} authorization."
            )

        refreshed_ssid = str(session_data["ssid"])
        if _infer_is_demo_from_ssid(
            refreshed_ssid,
            default_demo=not self.is_demo,
        ) != self.is_demo:
            raise RuntimeError("The Quotex fork returned authorization for the wrong account mode.")
        self.ssid = refreshed_ssid
        return refreshed_ssid

    async def disconnect(self) -> None:
        if self._client is not None:
            await self._client.disconnect()
            self._client = None
        if self._browser_session is not None:
            await self._browser_session.close()
            self._browser_session = None

    async def balance(self) -> float:
        assert self._client is not None
        b = await self._client.get_balance()
        return float(b.balance)

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

        direction_norm = direction.upper()
        if direction_norm in {"CALL", "BUY"}:
            odir = OrderDirection.CALL
        elif direction_norm in {"PUT", "SELL"}:
            odir = OrderDirection.PUT
        else:
            raise ValueError(f"Unsupported direction for Quotex: {direction!r}")

        # API-Quotex expects duration (seconds) in `place_order`.
        res = await self._client.place_order(
            asset=asset,
            amount=float(amount),
            direction=odir,
            duration=int(timeframe_seconds),
        )

        # place_order returns OrderResult-like with order_id and open_price.
        trade_id = str(getattr(res, "order_id", None) or getattr(res, "id", None) or res)
        open_price = float(getattr(res, "open_price", 0.0) or 0.0)
        entry_time = getattr(res, "placed_at", None) or datetime.now(timezone.utc).replace(tzinfo=None)

        return TradePlacement(
            trade_id=trade_id,
            asset=asset,
            direction=direction_norm if direction_norm in {"CALL", "PUT"} else ("CALL" if odir == OrderDirection.CALL else "PUT"),
            entry_time=entry_time,
            level=0,
            open_price=open_price,
            amount=float(amount),
        )

    async def check_result(self, trade_id: str) -> dict[str, Any]:
        assert self._client is not None
        pnl, status = await self._client.check_win(trade_id)
        return {"result": status, "pnl": pnl}
