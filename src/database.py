"""SQLite ma'lumotlar bazasi qatlami: signallar, hodisalar, webhook deduplikatsiya va holatni tiklash."""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from src.models import LevelHitEvent, Signal


class SignalDatabase:
    """Thread-safe SQLite storage for XAUUSD signals, paper-trading stats, and bot state."""

    def __init__(self, db_path: str = "data/xauusd_signals.db") -> None:
        self.db_path = db_path
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.executescript(
                """
                PRAGMA journal_mode=WAL;

                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_key TEXT NOT NULL UNIQUE,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    side TEXT NOT NULL,
                    candle_timestamp INTEGER NOT NULL,
                    entry_price REAL NOT NULL,
                    stop_loss REAL NOT NULL,
                    tp1 REAL NOT NULL,
                    tp2 REAL NOT NULL,
                    tp3 REAL NOT NULL,
                    sl_distance REAL NOT NULL,
                    rr_tp1 REAL NOT NULL,
                    rr_tp2 REAL NOT NULL,
                    rr_tp3 REAL NOT NULL,
                    ema_fast REAL NOT NULL,
                    ema_slow REAL NOT NULL,
                    rsi REAL NOT NULL,
                    atr REAL NOT NULL,
                    reason TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'polling',
                    status TEXT NOT NULL DEFAULT 'ACTIVE',
                    tp1_hit INTEGER NOT NULL DEFAULT 0,
                    tp2_hit INTEGER NOT NULL DEFAULT 0,
                    tp3_hit INTEGER NOT NULL DEFAULT 0,
                    sl_hit INTEGER NOT NULL DEFAULT 0,
                    realized_r REAL NOT NULL DEFAULT 0.0,
                    realized_pnl_usd REAL NOT NULL DEFAULT 0.0,
                    risk_usd REAL NOT NULL DEFAULT 100.0,
                    remaining_weight REAL NOT NULL DEFAULT 1.0,
                    created_at TEXT NOT NULL,
                    closed_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status);
                CREATE INDEX IF NOT EXISTS idx_signals_candle_ts ON signals(candle_timestamp DESC);

                CREATE TABLE IF NOT EXISTS signal_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_id INTEGER,
                    signal_key TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    level TEXT NOT NULL,
                    level_price REAL NOT NULL,
                    trigger_price REAL NOT NULL,
                    candle_timestamp INTEGER NOT NULL,
                    tranche_r REAL NOT NULL,
                    tranche_pnl_usd REAL NOT NULL,
                    total_realized_r REAL NOT NULL,
                    total_realized_pnl_usd REAL NOT NULL,
                    signal_status TEXT NOT NULL,
                    virtual_balance_after REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(signal_key, level)
                );

                CREATE TABLE IF NOT EXISTS webhook_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    webhook_key TEXT NOT NULL UNIQUE,
                    payload_hash TEXT NOT NULL,
                    received_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS bot_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS subscribers (
                    chat_id TEXT PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    joined_at TEXT NOT NULL
                );
                """
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------
    # Bot State Persistence (Restart Recovery)
    # ------------------------------------------------------------------
    def set_state(self, key: str, value: Any) -> None:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO bot_state (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, str(value), now),
            )

    def get_state(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM bot_state WHERE key = ?", (key,)
            ).fetchone()
            return row["value"] if row else default

    def is_paused(self) -> bool:
        val = self.get_state("is_paused", "0")
        return val == "1"

    def set_paused(self, paused: bool) -> None:
        self.set_state("is_paused", "1" if paused else "0")

    def get_virtual_balance(self, default_balance: float = 10000.0) -> float:
        val = self.get_state("virtual_balance")
        if val is None:
            self.set_virtual_balance(default_balance)
            return default_balance
        return float(val)

    def set_virtual_balance(self, balance: float) -> None:
        self.set_state("virtual_balance", f"{balance:.2f}")

    # ------------------------------------------------------------------
    # Subscribers Management
    # ------------------------------------------------------------------
    def add_subscriber(
        self, chat_id: str, username: str = "", first_name: str = ""
    ) -> None:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO subscribers (chat_id, username, first_name, is_active, joined_at)
                VALUES (?, ?, ?, 1, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    username = excluded.username,
                    first_name = excluded.first_name,
                    is_active = 1
                """,
                (str(chat_id), username or "", first_name or "", now),
            )

    def get_active_subscribers(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT chat_id FROM subscribers WHERE is_active = 1"
            ).fetchall()
            return [str(r["chat_id"]) for r in rows]

    # ------------------------------------------------------------------
    # Webhook Deduplication
    # ------------------------------------------------------------------
    def register_webhook_event(self, webhook_key: str, payload_hash: str) -> bool:
        """
        Attempt to record a webhook event idempotency key.
        Returns True if new, False if duplicate.
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        with self._lock:
            try:
                with self._conn:
                    self._conn.execute(
                        """
                        INSERT INTO webhook_events (webhook_key, payload_hash, received_at)
                        VALUES (?, ?, ?)
                        """,
                        (webhook_key, payload_hash, now),
                    )
                return True
            except sqlite3.IntegrityError:
                return False

    # ------------------------------------------------------------------
    # Signal Storage & Retrieval
    # ------------------------------------------------------------------
    def save_signal(self, signal: Signal) -> Optional[int]:
        """
        Insert a new signal into SQLite.
        Returns the new signal row ID, or None if a duplicate `signal_key` already exists.
        """
        with self._lock:
            try:
                with self._conn:
                    cursor = self._conn.execute(
                        """
                        INSERT INTO signals (
                            signal_key, symbol, timeframe, side, candle_timestamp,
                            entry_price, stop_loss, tp1, tp2, tp3, sl_distance,
                            rr_tp1, rr_tp2, rr_tp3, ema_fast, ema_slow, rsi, atr,
                            reason, source, status, tp1_hit, tp2_hit, tp3_hit, sl_hit,
                            realized_r, realized_pnl_usd, risk_usd, remaining_weight,
                            created_at, closed_at
                        ) VALUES (
                            ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?,
                            ?, ?
                        )
                        """,
                        (
                            signal.signal_key,
                            signal.symbol,
                            signal.timeframe,
                            signal.side,
                            signal.candle_timestamp,
                            signal.entry_price,
                            signal.stop_loss,
                            signal.tp1,
                            signal.tp2,
                            signal.tp3,
                            signal.sl_distance,
                            signal.rr_tp1,
                            signal.rr_tp2,
                            signal.rr_tp3,
                            signal.ema_fast,
                            signal.ema_slow,
                            signal.rsi,
                            signal.atr,
                            signal.reason,
                            signal.source,
                            signal.status,
                            int(signal.tp1_hit),
                            int(signal.tp2_hit),
                            int(signal.tp3_hit),
                            int(signal.sl_hit),
                            signal.realized_r,
                            signal.realized_pnl_usd,
                            signal.risk_usd,
                            signal.remaining_weight,
                            signal.created_at,
                            signal.closed_at,
                        ),
                    )
                    signal.id = int(cursor.lastrowid)
                    return signal.id
            except sqlite3.IntegrityError:
                return None

    def update_signal(self, signal: Signal) -> None:
        """Update an existing signal's TP/SL hits, PnL, and status."""
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE signals SET
                    stop_loss = ?,
                    status = ?,
                    tp1_hit = ?,
                    tp2_hit = ?,
                    tp3_hit = ?,
                    sl_hit = ?,
                    realized_r = ?,
                    realized_pnl_usd = ?,
                    remaining_weight = ?,
                    closed_at = ?
                WHERE signal_key = ?
                """,
                (
                    signal.stop_loss,
                    signal.status,
                    int(signal.tp1_hit),
                    int(signal.tp2_hit),
                    int(signal.tp3_hit),
                    int(signal.sl_hit),
                    signal.realized_r,
                    signal.realized_pnl_usd,
                    signal.remaining_weight,
                    signal.closed_at,
                    signal.signal_key,
                ),
            )

    def record_level_event(self, event: LevelHitEvent) -> bool:
        """Save a TP1/TP2/TP3/SL hit event idempotently."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        with self._lock:
            try:
                with self._conn:
                    self._conn.execute(
                        """
                        INSERT INTO signal_events (
                            signal_id, signal_key, symbol, side, level,
                            level_price, trigger_price, candle_timestamp,
                            tranche_r, tranche_pnl_usd, total_realized_r,
                            total_realized_pnl_usd, signal_status,
                            virtual_balance_after, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            event.signal_id,
                            event.signal_key,
                            event.symbol,
                            event.side,
                            event.level,
                            event.level_price,
                            event.trigger_price,
                            event.candle_timestamp,
                            event.tranche_r,
                            event.tranche_pnl_usd,
                            event.total_realized_r,
                            event.total_realized_pnl_usd,
                            event.signal_status,
                            event.virtual_balance_after,
                            now,
                        ),
                    )
                return True
            except sqlite3.IntegrityError:
                return False

    def _row_to_signal(self, row: sqlite3.Row) -> Signal:
        return Signal(
            id=row["id"],
            signal_key=row["signal_key"],
            symbol=row["symbol"],
            timeframe=row["timeframe"],
            side=row["side"],
            candle_timestamp=row["candle_timestamp"],
            entry_price=row["entry_price"],
            stop_loss=row["stop_loss"],
            tp1=row["tp1"],
            tp2=row["tp2"],
            tp3=row["tp3"],
            sl_distance=row["sl_distance"],
            rr_tp1=row["rr_tp1"],
            rr_tp2=row["rr_tp2"],
            rr_tp3=row["rr_tp3"],
            ema_fast=row["ema_fast"],
            ema_slow=row["ema_slow"],
            rsi=row["rsi"],
            atr=row["atr"],
            reason=row["reason"],
            source=row["source"],
            status=row["status"],
            tp1_hit=bool(row["tp1_hit"]),
            tp2_hit=bool(row["tp2_hit"]),
            tp3_hit=bool(row["tp3_hit"]),
            sl_hit=bool(row["sl_hit"]),
            realized_r=row["realized_r"],
            realized_pnl_usd=row["realized_pnl_usd"],
            risk_usd=row["risk_usd"],
            remaining_weight=row["remaining_weight"],
            created_at=row["created_at"],
            closed_at=row["closed_at"],
        )

    def get_active_signals(self) -> list[Signal]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM signals WHERE status = 'ACTIVE' ORDER BY candle_timestamp ASC"
            ).fetchall()
            return [self._row_to_signal(r) for r in rows]

    def get_recent_signals(self, limit: int = 10) -> list[Signal]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM signals ORDER BY candle_timestamp DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [self._row_to_signal(r) for r in rows]

    def get_all_signal_keys(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute("SELECT signal_key FROM signals").fetchall()
            return [str(r["signal_key"]) for r in rows]

    def get_stats_summary(self, initial_balance: float = 10000.0) -> dict[str, Any]:
        """Compute comprehensive demo/paper-trading statistics from stored signals."""
        with self._lock:
            rows = self._conn.execute("SELECT * FROM signals").fetchall()
            signals = [self._row_to_signal(r) for r in rows]

        current_balance = self.get_virtual_balance(initial_balance)
        total_signals = len(signals)
        active_signals = sum(1 for s in signals if s.status == "ACTIVE")
        closed_signals = total_signals - active_signals

        tp1_hits = sum(1 for s in signals if s.tp1_hit)
        tp2_hits = sum(1 for s in signals if s.tp2_hit)
        tp3_hits = sum(1 for s in signals if s.tp3_hit)
        sl_hits = sum(1 for s in signals if s.sl_hit)

        # Signal wins if realized_pnl_usd > 0 (e.g., hit at least TP1 with positive net R or TP3)
        evaluated = [s for s in signals if s.status != "ACTIVE" or s.realized_pnl_usd != 0]
        wins = sum(1 for s in evaluated if s.realized_pnl_usd > 0)
        losses = sum(1 for s in evaluated if s.realized_pnl_usd < 0)
        win_rate = (wins / len(evaluated) * 100.0) if evaluated else 0.0

        total_pnl_usd = round(sum(s.realized_pnl_usd for s in signals), 2)
        total_r = round(sum(s.realized_r for s in signals), 2)
        return_pct = round(((current_balance - initial_balance) / initial_balance) * 100.0, 2)

        return {
            "initial_balance": round(initial_balance, 2),
            "current_balance": round(current_balance, 2),
            "total_pnl_usd": total_pnl_usd,
            "return_pct": return_pct,
            "total_r": total_r,
            "total_signals": total_signals,
            "active_signals": active_signals,
            "closed_signals": closed_signals,
            "wins": wins,
            "losses": losses,
            "win_rate_pct": round(win_rate, 2),
            "tp1_hits": tp1_hits,
            "tp2_hits": tp2_hits,
            "tp3_hits": tp3_hits,
            "sl_hits": sl_hits,
        }
