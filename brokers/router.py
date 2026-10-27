from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Sequence

from .base import NormalizedSignal


@dataclass(frozen=True)
class RouterDecision:
    broker_ids: Sequence[str]


class SignalRouter:
    """Routes a normalized signal to one or more brokers.

    Currently supports Option A: `channels.json[].broker`.
    """

    def __init__(
        self,
        broker_for_channel: Dict[str, str] | None = None,
    ) -> None:
        self.broker_for_channel = broker_for_channel or {}

    def decide(self, signal: NormalizedSignal, *, channel_key: str | None = None) -> RouterDecision:
        # Priority 1: explicit mapping from channel_key -> broker_id
        if channel_key:
            if channel_key in self.broker_for_channel:
                return RouterDecision(broker_ids=[self.broker_for_channel[channel_key]])
        # Webhook signals must carry an explicit broker when not mapped to a channel.
        if signal.broker_hint:
            return RouterDecision(broker_ids=[signal.broker_hint])
        raise ValueError("Signal has no broker destination; configure a channel broker or include broker metadata.")
