"""Virtual balans va Demo/Paper-Trading pozitsiyalarni (TP1, TP2, TP3, SL) kuzatish dvigateli."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Sequence

from src.config import BotConfig
from src.database import SignalDatabase
from src.models import Candle, LevelHitEvent, Signal

TP1_WEIGHT = 0.34
TP2_WEIGHT = 0.33
TP3_WEIGHT = 0.33


class PaperTradingEngine:
    """
    Monitors active demo signals against market candles/prices and records
    TP1, TP2, TP3, and SL hits without ever placing real broker orders.
    """

    def __init__(
        self,
        db: SignalDatabase,
        config: Optional[BotConfig] = None,
    ) -> None:
        self.db = db
        self.config = config or BotConfig()
        # Ensure initial virtual balance is initialized in DB if not present
        self.db.get_virtual_balance(self.config.initial_virtual_balance)

    def current_risk_usd(self) -> float:
        """Calculate virtual dollar risk for the next trade based on current virtual balance."""
        balance = self.db.get_virtual_balance(self.config.initial_virtual_balance)
        return round(balance * (self.config.risk_per_trade_pct / 100.0), 2)

    def evaluate_signal_on_candle(
        self,
        signal: Signal,
        candle: Candle,
    ) -> list[LevelHitEvent]:
        """
        Check whether `candle` triggers SL, TP1, TP2, or TP3 on an active `signal`.
        Updates `signal`, virtual balance, and SQLite database atomically.
        """
        if signal.status != "ACTIVE":
            return []
        # Do not trigger TP/SL on the exact same candle that generated the entry signal
        if candle.timestamp <= signal.candle_timestamp:
            return []

        events: list[LevelHitEvent] = []
        balance = self.db.get_virtual_balance(self.config.initial_virtual_balance)
        now_str = datetime.fromtimestamp(candle.timestamp, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )

        is_buy = signal.side.upper() == "BUY"
        sl_triggered = (
            candle.low <= signal.stop_loss if is_buy else candle.high >= signal.stop_loss
        )

        # Conservative rule: if SL is hit on this candle before all TPs are done,
        # check whether SL was hit first.
        if sl_triggered:
            # If stop_loss was moved to break-even (entry_price), R loss is 0.0; else -1.0
            if abs(signal.stop_loss - signal.entry_price) < 1e-6:
                unit_r = 0.0
            else:
                unit_r = -1.0

            tranche_r = round(signal.remaining_weight * unit_r, 4)
            tranche_pnl = round(signal.risk_usd * tranche_r, 2)

            signal.sl_hit = True
            signal.status = "CLOSED_SL"
            signal.remaining_weight = 0.0
            signal.realized_r = round(signal.realized_r + tranche_r, 4)
            signal.realized_pnl_usd = round(signal.realized_pnl_usd + tranche_pnl, 2)
            signal.closed_at = now_str
            balance = round(balance + tranche_pnl, 2)

            self.db.set_virtual_balance(balance)
            self.db.update_signal(signal)

            ev = LevelHitEvent(
                signal_id=signal.id,
                signal_key=signal.signal_key,
                symbol=signal.symbol,
                side=signal.side,
                level="SL",
                level_price=signal.stop_loss,
                trigger_price=signal.stop_loss,
                candle_timestamp=candle.timestamp,
                tranche_r=tranche_r,
                tranche_pnl_usd=tranche_pnl,
                total_realized_r=signal.realized_r,
                total_realized_pnl_usd=signal.realized_pnl_usd,
                signal_status=signal.status,
                virtual_balance_after=balance,
            )
            if self.db.record_level_event(ev):
                events.append(ev)
            return events

        # Check TP1 -> TP2 -> TP3 progression
        tp_checks = [
            ("TP1", signal.tp1, signal.rr_tp1, TP1_WEIGHT, "tp1_hit"),
            ("TP2", signal.tp2, signal.rr_tp2, TP2_WEIGHT, "tp2_hit"),
            ("TP3", signal.tp3, signal.rr_tp3, TP3_WEIGHT, "tp3_hit"),
        ]

        for level_name, level_price, rr_val, weight, attr_name in tp_checks:
            already_hit = getattr(signal, attr_name)
            if already_hit:
                continue

            hit = candle.high >= level_price if is_buy else candle.low <= level_price
            if not hit:
                continue

            setattr(signal, attr_name, True)
            tranche_r = round(weight * rr_val, 4)
            tranche_pnl = round(signal.risk_usd * tranche_r, 2)

            signal.remaining_weight = max(0.0, round(signal.remaining_weight - weight, 4))
            signal.realized_r = round(signal.realized_r + tranche_r, 4)
            signal.realized_pnl_usd = round(signal.realized_pnl_usd + tranche_pnl, 2)
            balance = round(balance + tranche_pnl, 2)

            if level_name == "TP1" and self.config.move_sl_to_be_on_tp1:
                signal.stop_loss = signal.entry_price

            if level_name == "TP3":
                signal.status = "CLOSED_TP3"
                signal.remaining_weight = 0.0
                signal.closed_at = now_str

            self.db.set_virtual_balance(balance)
            self.db.update_signal(signal)

            ev = LevelHitEvent(
                signal_id=signal.id,
                signal_key=signal.signal_key,
                symbol=signal.symbol,
                side=signal.side,
                level=level_name,
                level_price=level_price,
                trigger_price=level_price,
                candle_timestamp=candle.timestamp,
                tranche_r=tranche_r,
                tranche_pnl_usd=tranche_pnl,
                total_realized_r=signal.realized_r,
                total_realized_pnl_usd=signal.realized_pnl_usd,
                signal_status=signal.status,
                virtual_balance_after=balance,
            )
            if self.db.record_level_event(ev):
                events.append(ev)

        return events

    def process_active_signals(self, candles: Sequence[Candle]) -> list[LevelHitEvent]:
        """Check all currently active signals against recent candles."""
        active_signals = self.db.get_active_signals()
        if not active_signals or not candles:
            return []

        all_events: list[LevelHitEvent] = []
        for sig in active_signals:
            for candle in candles:
                if candle.timestamp <= sig.candle_timestamp:
                    continue
                evs = self.evaluate_signal_on_candle(sig, candle)
                all_events.extend(evs)
                if sig.status != "ACTIVE":
                    break
        return all_events
