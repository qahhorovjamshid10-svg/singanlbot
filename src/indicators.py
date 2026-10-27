"""Texnik indikatorlar: EMA (50, 200), Wilder RSI (14) va Wilder ATR (14)."""

from __future__ import annotations

from typing import Optional, Sequence

from src.models import Candle, IndicatorSnapshot


def compute_ema_series(values: Sequence[float], period: int) -> list[Optional[float]]:
    """
    Calculate Exponential Moving Average (EMA) series matching TradingView `ta.ema`.

    First valid value at index `period - 1` is the Simple Moving Average (SMA)
    of the first `period` values; subsequent values use multiplier 2 / (period + 1).
    """
    if period <= 0:
        raise ValueError("EMA period must be positive")
    n = len(values)
    result: list[Optional[float]] = [None] * n
    if n < period:
        return result

    sma = sum(values[:period]) / period
    result[period - 1] = sma
    alpha = 2.0 / (period + 1.0)
    prev_ema = sma

    for i in range(period, n):
        ema = (values[i] - prev_ema) * alpha + prev_ema
        result[i] = ema
        prev_ema = ema

    return result


def compute_rsi_series(closes: Sequence[float], period: int = 14) -> list[Optional[float]]:
    """
    Calculate Wilder's Relative Strength Index (RSI) matching TradingView `ta.rsi`.

    Uses Wilder's smoothing (alpha = 1 / period) after initial average gain/loss
    over the first `period` price changes.
    """
    if period <= 0:
        raise ValueError("RSI period must be positive")
    n = len(closes)
    result: list[Optional[float]] = [None] * n
    if n <= period:
        return result

    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, period + 1):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0.0:
        result[period] = 100.0 if avg_gain > 0 else 50.0
    else:
        rs = avg_gain / avg_loss
        result[period] = 100.0 - (100.0 / (1.0 + rs))

    for i in range(period + 1, n):
        delta = closes[i] - closes[i - 1]
        gain = max(delta, 0.0)
        loss = max(-delta, 0.0)
        avg_gain = ((avg_gain * (period - 1)) + gain) / period
        avg_loss = ((avg_loss * (period - 1)) + loss) / period

        if avg_loss == 0.0:
            result[i] = 100.0 if avg_gain > 0 else 50.0
        else:
            rs = avg_gain / avg_loss
            result[i] = 100.0 - (100.0 / (1.0 + rs))

    return result


def compute_atr_series(candles: Sequence[Candle], period: int = 14) -> list[Optional[float]]:
    """
    Calculate Wilder's Average True Range (ATR) matching TradingView `ta.atr`.

    True Range = max(high - low, |high - prev_close|, |low - prev_close|).
    First ATR at index `period - 1` is the average of the first `period` True Ranges,
    then smoothed using Wilder's RMA (alpha = 1 / period).
    """
    if period <= 0:
        raise ValueError("ATR period must be positive")
    n = len(candles)
    result: list[Optional[float]] = [None] * n
    if n < period:
        return result

    true_ranges: list[float] = []
    for i, c in enumerate(candles):
        if i == 0:
            tr = c.high - c.low
        else:
            prev_close = candles[i - 1].close
            tr = max(
                c.high - c.low,
                abs(c.high - prev_close),
                abs(c.low - prev_close),
            )
        true_ranges.append(tr)

    initial_atr = sum(true_ranges[:period]) / period
    result[period - 1] = initial_atr
    prev_atr = initial_atr

    for i in range(period, n):
        atr = ((prev_atr * (period - 1)) + true_ranges[i]) / period
        result[i] = atr
        prev_atr = atr

    return result


def compute_latest_indicators(
    candles: Sequence[Candle],
    ema_fast_period: int = 50,
    ema_slow_period: int = 200,
    rsi_period: int = 14,
    atr_period: int = 14,
) -> Optional[IndicatorSnapshot]:
    """
    Compute EMA50, EMA200, RSI14, and ATR14 for the last closed candle in `candles`.
    Returns None if there are not enough closed candles.
    """
    closed = [c for c in candles if c.is_closed]
    min_required = max(ema_fast_period, ema_slow_period, rsi_period + 1, atr_period) + 1
    if len(closed) < min_required:
        return None

    closes = [c.close for c in closed]
    ema_fast = compute_ema_series(closes, ema_fast_period)
    ema_slow = compute_ema_series(closes, ema_slow_period)
    rsi = compute_rsi_series(closes, rsi_period)
    atr = compute_atr_series(closed, atr_period)

    curr_ef = ema_fast[-1]
    curr_es = ema_slow[-1]
    curr_rsi = rsi[-1]
    curr_atr = atr[-1]

    if curr_ef is None or curr_es is None or curr_rsi is None or curr_atr is None:
        return None

    return IndicatorSnapshot(
        ema_fast=curr_ef,
        ema_slow=curr_es,
        rsi=curr_rsi,
        atr=curr_atr,
        prev_ema_fast=ema_fast[-2],
        prev_ema_slow=ema_slow[-2],
        prev_rsi=rsi[-2],
    )
