"""XAUUSD Trading Algoritmi: EMA 50/200 Trend, RSI 14 Momentum, ATR 14 SL/TP."""

from __future__ import annotations

from typing import Optional, Sequence

from src.config import BotConfig
from src.indicators import (
    compute_atr_series,
    compute_ema_series,
    compute_rsi_series,
)
from src.models import Candle, Signal


def build_signal_key(symbol: str, timeframe: str, candle_timestamp: int, side: str) -> str:
    """Create a deterministic idempotency key for a signal."""
    return f"{symbol.upper()}:{timeframe}:{int(candle_timestamp)}:{side.upper()}"


def calculate_levels(
    side: str,
    entry_price: float,
    atr_value: float,
    atr_sl_multiplier: float = 1.5,
    tp1_rr: float = 1.0,
    tp2_rr: float = 2.0,
    tp3_rr: float = 3.0,
) -> dict[str, float]:
    """
    Calculate Entry, SL, TP1, TP2, TP3 and Risk/Reward distances from ATR 14.
    """
    side_norm = side.upper()
    if side_norm not in {"BUY", "SELL"}:
        raise ValueError(f"Unsupported signal side: {side}")
    if entry_price <= 0:
        raise ValueError("Entry price must be positive")
    if atr_value <= 0:
        raise ValueError("ATR value must be positive")

    sl_distance = max(round(atr_value * atr_sl_multiplier, 2), 0.10)
    entry = round(entry_price, 2)

    if side_norm == "BUY":
        sl = round(entry - sl_distance, 2)
        tp1 = round(entry + sl_distance * tp1_rr, 2)
        tp2 = round(entry + sl_distance * tp2_rr, 2)
        tp3 = round(entry + sl_distance * tp3_rr, 2)
    else:
        sl = round(entry + sl_distance, 2)
        tp1 = round(entry - sl_distance * tp1_rr, 2)
        tp2 = round(entry - sl_distance * tp2_rr, 2)
        tp3 = round(entry - sl_distance * tp3_rr, 2)

    return {
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "sl_distance": sl_distance,
        "rr_tp1": round(tp1_rr, 2),
        "rr_tp2": round(tp2_rr, 2),
        "rr_tp3": round(tp3_rr, 2),
    }


class XAUUSDStrategy:
    """
    XAUUSD (Gold) Trend + Momentum + Volatility Signal Strategy.

    Qoidalar:
    1. Faqat yopilgan shamlarda (`candle.is_closed == True`) signal tasdiqlanadi.
    2. Trend: EMA 50 va EMA 200 orqali aniqlanadi.
    3. Momentum: RSI 14 orqali tekshiriladi (BUY: 50..70 va o'suvchi; SELL: 30..50 va pasayuvchi).
    4. Stop-Loss: ATR 14 * ATR_SL_MULTIPLIER masofasida belgilanadi.
    5. Take-Profit: TP1 (1:1 R:R), TP2 (1:2 R:R), TP3 (1:3 R:R).
    6. Bir xil signal kaliti (`signal_key`) hech qachon takroran yuborilmaydi.
    """

    def __init__(self, config: Optional[BotConfig] = None) -> None:
        self.config = config or BotConfig()
        self._emitted_keys: set[str] = set()

    def register_existing_keys(self, keys: Sequence[str]) -> None:
        """Pre-populate already emitted signal keys (e.g. from SQLite on startup)."""
        self._emitted_keys.update(keys)

    def has_emitted(self, signal_key: str) -> bool:
        return signal_key in self._emitted_keys

    def evaluate_candle_index(
        self,
        closed_candles: Sequence[Candle],
        idx: int,
        ema_fast_series: Sequence[Optional[float]],
        ema_slow_series: Sequence[Optional[float]],
        rsi_series: Sequence[Optional[float]],
        atr_series: Sequence[Optional[float]],
        risk_usd: float = 100.0,
        source: str = "polling",
    ) -> Optional[Signal]:
        """Evaluate strategy rules at a specific index `idx` of `closed_candles`."""
        if idx < 1 or idx >= len(closed_candles):
            return None

        curr = closed_candles[idx]
        prev = closed_candles[idx - 1]

        # Qat'iy qoida: faqat yopilgan shamda signal berish
        if not curr.is_closed:
            return None

        ef = ema_fast_series[idx]
        es = ema_slow_series[idx]
        rsi = rsi_series[idx]
        atr = atr_series[idx]

        prev_ef = ema_fast_series[idx - 1]
        prev_es = ema_slow_series[idx - 1]
        prev_rsi = rsi_series[idx - 1]

        if (
            ef is None
            or es is None
            or rsi is None
            or atr is None
            or prev_ef is None
            or prev_es is None
            or prev_rsi is None
            or atr <= 0
        ):
            return None

        side: Optional[str] = None
        reason: str = ""

        # --- BUY SHARTLARI ---
        # Trend: EMA 50 > EMA 200 (yoki M1 da narx ikkala EMA ustiga yorib chiqqan va EMA50 o'smoqda)
        candle_body = abs(curr.close - curr.open)
        trend_separation = abs(ef - es) >= 0.25 * atr
        classic_uptrend = ef > es and ef > prev_ef and curr.close > ef and trend_separation
        ema_breakout_up = (
            curr.close > ef
            and curr.close > es
            and ef > prev_ef
            and prev.close <= max(prev_ef, prev_es)
        )
        bullish_trend = classic_uptrend or ema_breakout_up
        # Momentum: RSI 14 [50, 72] oralig'ida va o'smoqda
        bullish_momentum = 50.0 <= rsi <= 72.0 and (rsi - prev_rsi) >= 1.2

        if bullish_trend and bullish_momentum and curr.close > curr.open and candle_body >= 0.20 * atr:
            golden_cross = prev_ef <= prev_es and ef > es
            rsi_cross_up = prev_rsi < 50.0 and rsi >= 50.0
            rsi_impulse_up = prev_rsi <= 58.0 and (rsi - prev_rsi) >= 2.5
            ema_pullback_bounce = (
                min(prev.low, curr.low) <= ef + 0.5 * atr
                and curr.close > ef
                and (curr.close - ef) <= 1.8 * atr
            )
            if golden_cross:
                side = "BUY"
                reason = "EMA50/EMA200 Golden Cross + RSI14 bullish momentum"
            elif ema_breakout_up:
                side = "BUY"
                reason = "EMA50 va EMA200 yuqoriga yorib o'tish (Breakout) + RSI14 tasdiq"
            elif ema_pullback_bounce:
                side = "BUY"
                reason = "Uptrend (EMA50 tepada EMA200 dan) + EMA50 pullback bounce + RSI14 tasdiq"
            elif (rsi_cross_up or rsi_impulse_up) and (curr.close - ef) <= 1.8 * atr:
                side = "BUY"
                reason = "Uptrend (EMA50 tepada EMA200 dan) + RSI14 bullish impuls tasdig'i"

        # --- SELL SHARTLARI ---
        # Trend: EMA 50 < EMA 200 (yoki M1 da narx ikkala EMA ostiga yorib tushgan va EMA50 pasaymoqda)
        classic_downtrend = ef < es and ef < prev_ef and curr.close < ef and trend_separation
        ema_breakdown = (
            curr.close < ef
            and curr.close < es
            and ef < prev_ef
            and prev.close >= min(prev_ef, prev_es)
        )
        bearish_trend = classic_downtrend or ema_breakdown
        # Momentum: RSI 14 [28, 50] oralig'ida va pasaymoqda
        bearish_momentum = 28.0 <= rsi <= 50.0 and (prev_rsi - rsi) >= 1.2

        if side is None and bearish_trend and bearish_momentum and curr.close < curr.open and candle_body >= 0.20 * atr:
            death_cross = prev_ef >= prev_es and ef < es
            rsi_cross_down = prev_rsi > 50.0 and rsi <= 50.0
            rsi_impulse_down = prev_rsi >= 42.0 and (prev_rsi - rsi) >= 2.5
            ema_pullback_reject = (
                max(prev.high, curr.high) >= ef - 0.5 * atr
                and curr.close < ef
                and (ef - curr.close) <= 1.8 * atr
            )
            if death_cross:
                side = "SELL"
                reason = "EMA50/EMA200 Death Cross + RSI14 bearish momentum"
            elif ema_breakdown:
                side = "SELL"
                reason = "EMA50 va EMA200 pastga yorib o'tish (Breakdown) + RSI14 tasdiq"
            elif ema_pullback_reject:
                side = "SELL"
                reason = "Downtrend (EMA50 pastda EMA200 dan) + EMA50 zona pullback rejection + RSI14 tasdiq"
            elif (rsi_cross_down or rsi_impulse_down) and (ef - curr.close) <= 1.8 * atr:
                side = "SELL"
                reason = "Downtrend (EMA50 pastda EMA200 dan) + RSI14 bearish impuls tasdig'i"

        if side is None:
            return None

        signal_key = build_signal_key(
            self.config.symbol, self.config.timeframe, curr.timestamp, side
        )
        if signal_key in self._emitted_keys:
            return None

        levels = calculate_levels(
            side=side,
            entry_price=curr.close,
            atr_value=atr,
            atr_sl_multiplier=self.config.atr_sl_multiplier,
            tp1_rr=self.config.tp1_rr_ratio,
            tp2_rr=self.config.tp2_rr_ratio,
            tp3_rr=self.config.tp3_rr_ratio,
        )

        return Signal(
            signal_key=signal_key,
            symbol=self.config.symbol,
            timeframe=self.config.timeframe,
            side=side,
            candle_timestamp=curr.timestamp,
            entry_price=levels["entry"],
            stop_loss=levels["sl"],
            tp1=levels["tp1"],
            tp2=levels["tp2"],
            tp3=levels["tp3"],
            sl_distance=levels["sl_distance"],
            rr_tp1=levels["rr_tp1"],
            rr_tp2=levels["rr_tp2"],
            rr_tp3=levels["rr_tp3"],
            ema_fast=round(ef, 2),
            ema_slow=round(es, 2),
            rsi=round(rsi, 2),
            atr=round(atr, 2),
            reason=reason,
            source=source,
            risk_usd=round(risk_usd, 2),
        )

    def evaluate_latest(
        self,
        candles: Sequence[Candle],
        risk_usd: float = 100.0,
        mark_emitted: bool = True,
    ) -> Optional[Signal]:
        """
        Evaluate only the latest CLOSED candle in `candles`.
        Any unclosed/forming candle at the tail of `candles` is ignored.
        """
        closed_candles = [c for c in candles if c.is_closed]
        min_len = (
            max(
                self.config.ema_fast_period,
                self.config.ema_slow_period,
                self.config.rsi_period + 1,
                self.config.atr_period,
            )
            + 1
        )
        if len(closed_candles) < min_len:
            return None

        closes = [c.close for c in closed_candles]
        ema_fast = compute_ema_series(closes, self.config.ema_fast_period)
        ema_slow = compute_ema_series(closes, self.config.ema_slow_period)
        rsi = compute_rsi_series(closes, self.config.rsi_period)
        atr = compute_atr_series(closed_candles, self.config.atr_period)

        signal = self.evaluate_candle_index(
            closed_candles=closed_candles,
            idx=len(closed_candles) - 1,
            ema_fast_series=ema_fast,
            ema_slow_series=ema_slow,
            rsi_series=rsi,
            atr_series=atr,
            risk_usd=risk_usd,
            source="polling",
        )
        if signal is not None and mark_emitted:
            self._emitted_keys.add(signal.signal_key)
        return signal
