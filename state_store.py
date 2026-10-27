"""Durable runtime state for signals, trades, and broker P/L."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


_SCHEMA_VERSION = 1


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    raise TypeError(f"Cannot persist {type(value).__name__} as JSON")


class SignalBotStateStore:
    """SQLite store sharing the configured database with Telegram deduplication."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _migrate(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS signal_bot_state_schema "
                "(version INTEGER NOT NULL)"
            )
            row = connection.execute(
                "SELECT version FROM signal_bot_state_schema LIMIT 1"
            ).fetchone()
            version = int(row["version"]) if row else 0
            if version > _SCHEMA_VERSION:
                raise RuntimeError(
                    f"State database schema {version} is newer than supported "
                    f"schema {_SCHEMA_VERSION}"
                )
            if version == 0:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS runtime_signals (
                        signal_id TEXT PRIMARY KEY,
                        status TEXT NOT NULL,
                        payload TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS runtime_signals_created
                        ON runtime_signals(created_at DESC);

                    CREATE TABLE IF NOT EXISTS active_trades (
                        trade_id TEXT PRIMARY KEY,
                        state TEXT NOT NULL DEFAULT 'monitoring',
                        payload TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS active_trades_state
                        ON active_trades(state);

                    CREATE TABLE IF NOT EXISTS closed_trades (
                        trade_id TEXT PRIMARY KEY,
                        payload TEXT NOT NULL,
                        closed_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS closed_trades_recent
                        ON closed_trades(closed_at DESC);

                    CREATE TABLE IF NOT EXISTS broker_pnl_events (
                        event_id TEXT PRIMARY KEY,
                        broker_id TEXT NOT NULL,
                        local_date TEXT NOT NULL,
                        amount REAL NOT NULL,
                        recorded_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS broker_pnl_by_date
                        ON broker_pnl_events(broker_id, local_date);
                    """
                )
                connection.execute(
                    "INSERT INTO signal_bot_state_schema(version) VALUES (?)",
                    (_SCHEMA_VERSION,),
                )
            elif row:
                connection.execute(
                    "UPDATE signal_bot_state_schema SET version = ?",
                    (_SCHEMA_VERSION,),
                )

    @staticmethod
    def _dump(payload: Any) -> str:
        return json.dumps(payload, separators=(",", ":"), default=_json_default)

    @staticmethod
    def _load(payload: str) -> Any:
        return json.loads(payload)

    def save_signal(self, signal_id: str, status: str, payload: dict[str, Any]) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO runtime_signals
                    (signal_id, status, payload, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (signal_id, status, self._dump(payload), now, now),
            )
            return cursor.rowcount == 1

    def set_signal_status(self, signal_id: str, status: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE runtime_signals SET status = ?, updated_at = ? WHERE signal_id = ?",
                (status, datetime.now(timezone.utc).isoformat(), signal_id),
            )

    def recent_signals(self, limit: int = 20) -> list[dict[str, Any]]:
        if limit < 1:
            raise ValueError("limit must be greater than zero")
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT signal_id, status, payload
                FROM runtime_signals
                ORDER BY created_at DESC, signal_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "signal_id": row["signal_id"],
                "status": row["status"],
                **self._load(row["payload"]),
            }
            for row in rows
        ]

    def interrupt_unfinished_signals(self) -> dict[str, int]:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as connection:
            pending = connection.execute(
                """
                UPDATE runtime_signals SET status = 'interrupted', updated_at = ?
                WHERE status = 'pending'
                """,
                (now,),
            ).rowcount
            ambiguous = connection.execute(
                """
                UPDATE runtime_signals SET status = 'needs_review', updated_at = ?
                WHERE status = 'placing'
                """,
                (now,),
            ).rowcount
            ambiguous_trades = connection.execute(
                """
                UPDATE active_trades SET state = 'needs_review', updated_at = ?
                WHERE state = 'placing'
                """,
                (now,),
            ).rowcount
        return {
            "interrupted": pending,
            "needs_review": ambiguous,
            "ambiguous_trades": ambiguous_trades,
        }

    def save_active_trade(
        self,
        trade_id: str,
        payload: dict[str, Any],
        *,
        state: str = "monitoring",
        signal_id: str | None = None,
    ) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO active_trades(trade_id, state, payload, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(trade_id) DO UPDATE SET
                    state = excluded.state,
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (
                    trade_id,
                    state,
                    self._dump(payload),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            if signal_id:
                connection.execute(
                    "UPDATE runtime_signals SET status = 'placed', updated_at = ? WHERE signal_id = ?",
                    (datetime.now(timezone.utc).isoformat(), signal_id),
                )

    def set_active_trade_state(
        self,
        trade_id: str,
        state: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        with self._lock, self._connect() as connection:
            if payload is None:
                cursor = connection.execute(
                    "UPDATE active_trades SET state = ?, updated_at = ? WHERE trade_id = ?",
                    (state, datetime.now(timezone.utc).isoformat(), trade_id),
                )
            else:
                cursor = connection.execute(
                    """
                    UPDATE active_trades SET state = ?, payload = ?, updated_at = ?
                    WHERE trade_id = ?
                    """,
                    (
                        state,
                        self._dump(payload),
                        datetime.now(timezone.utc).isoformat(),
                        trade_id,
                    ),
                )
            if cursor.rowcount != 1:
                raise KeyError(f"Active trade {trade_id!r} is not stored")

    def remove_active_trade(self, trade_id: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM active_trades WHERE trade_id = ?", (trade_id,))

    def load_active_trades(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT trade_id, state, payload FROM active_trades ORDER BY updated_at"
            ).fetchall()
        return [
            {
                "trade_id": row["trade_id"],
                "state": row["state"],
                "trade_details": self._load(row["payload"]),
            }
            for row in rows
        ]

    def finish_trade(
        self,
        trade_id: str,
        payload: dict[str, Any],
        *,
        pnl_event: tuple[str, str, date, float] | None = None,
        signal_id: str | None = None,
        signal_status: str = "completed",
    ) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO closed_trades(trade_id, payload, closed_at)
                VALUES (?, ?, ?)
                ON CONFLICT(trade_id) DO UPDATE SET
                    payload = excluded.payload,
                    closed_at = excluded.closed_at
                """,
                (trade_id, self._dump(payload), now),
            )
            connection.execute("DELETE FROM active_trades WHERE trade_id = ?", (trade_id,))
            if signal_id:
                connection.execute(
                    "UPDATE runtime_signals SET status = ?, updated_at = ? WHERE signal_id = ?",
                    (signal_status, now, signal_id),
                )
            if pnl_event is None:
                return False
            event_id, broker_id, local_date, amount = pnl_event
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO broker_pnl_events
                    (event_id, broker_id, local_date, amount, recorded_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (event_id, broker_id, local_date.isoformat(), amount, now),
            )
            return cursor.rowcount == 1

    def roll_active_trade(
        self,
        previous_trade_id: str,
        next_trade_id: str,
        next_payload: dict[str, Any],
        closed_payload: dict[str, Any],
        *,
        signal_id: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO closed_trades(trade_id, payload, closed_at)
                VALUES (?, ?, ?)
                ON CONFLICT(trade_id) DO UPDATE SET
                    payload = excluded.payload,
                    closed_at = excluded.closed_at
                """,
                (previous_trade_id, self._dump(closed_payload), now),
            )
            connection.execute("DELETE FROM active_trades WHERE trade_id = ?", (previous_trade_id,))
            connection.execute(
                """
                INSERT INTO active_trades(trade_id, state, payload, updated_at)
                VALUES (?, 'monitoring', ?, ?)
                ON CONFLICT(trade_id) DO UPDATE SET
                    state = 'monitoring',
                    payload = excluded.payload,
                    updated_at = excluded.updated_at
                """,
                (next_trade_id, self._dump(next_payload), now),
            )
            if signal_id:
                connection.execute(
                    "UPDATE runtime_signals SET status = 'placed', updated_at = ? WHERE signal_id = ?",
                    (now, signal_id),
                )

    def recent_closed_trades(self, limit: int = 50) -> list[tuple[str, dict[str, Any]]]:
        if limit < 1:
            raise ValueError("limit must be greater than zero")
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT trade_id, payload FROM closed_trades
                ORDER BY closed_at DESC, trade_id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [(row["trade_id"], self._load(row["payload"])) for row in rows]

    def load_closed_trades(self) -> list[tuple[str, dict[str, Any]]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT trade_id, payload FROM closed_trades ORDER BY closed_at, trade_id"
            ).fetchall()
        return [(row["trade_id"], self._load(row["payload"])) for row in rows]

    def pnl_totals(self, local_date: date) -> tuple[dict[str, float], dict[str, float]]:
        with self._lock, self._connect() as connection:
            lifetime_rows = connection.execute(
                """
                SELECT broker_id, SUM(amount) AS total
                FROM broker_pnl_events GROUP BY broker_id
                """
            ).fetchall()
            daily_rows = connection.execute(
                """
                SELECT broker_id, SUM(amount) AS total
                FROM broker_pnl_events WHERE local_date = ?
                GROUP BY broker_id
                """,
                (local_date.isoformat(),),
            ).fetchall()
        lifetime = {row["broker_id"]: float(row["total"] or 0) for row in lifetime_rows}
        daily = {row["broker_id"]: float(row["total"] or 0) for row in daily_rows}
        return daily, lifetime

    def record_pnl_event(
        self,
        event_id: str,
        broker_id: str,
        local_date: date,
        amount: float,
    ) -> bool:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO broker_pnl_events
                    (event_id, broker_id, local_date, amount, recorded_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    broker_id,
                    local_date.isoformat(),
                    amount,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            return cursor.rowcount == 1
