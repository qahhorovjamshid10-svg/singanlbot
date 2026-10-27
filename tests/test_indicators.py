"""EMA 50, EMA 200, RSI 14 va ATR 14 indikatorlarining matematik aniqlik testlari."""

from __future__ import annotations

import pytest

from src.indicators import (
    compute_atr_series,
    compute_ema_series,
    compute_latest_indicators,
    compute_rsi_series,
)
from src.models import Candle


def test_compute_ema_series_exact_values() -> None:
    values = [10.0, 12.0, 14.0, 16.0, 18.0]
    ema3 = compute_ema_series(values, period=3)
    assert ema3[0] is None
    assert ema3[1] is None
    # First EMA at index 2 is SMA(10, 12, 14) = 12.0
    assert ema3[2] == pytest.approx(12.0)
    # Alpha = 2 / (3 + 1) = 0.5 -> (16 - 12)*0.5 + 12 = 14.0
    assert ema3[3] == pytest.approx(14.0)
    # Next -> (18 - 14)*0.5 + 14 = 16.0
    assert ema3[4] == pytest.approx(16.0)


def test_compute_rsi_series_bounds_and_direction() -> None:
    rising = [4000.0 + i * 2.5 for i in range(30)]
    rsi_up = compute_rsi_series(rising, period=14)
    assert all(x is None for x in rsi_up[:14])
    assert rsi_up[14] == pytest.approx(100.0)
    assert rsi_up[-1] == pytest.approx(100.0)

    falling = [4100.0 - i * 2.5 for i in range(30)]
    rsi_down = compute_rsi_series(falling, period=14)
    assert rsi_down[14] == pytest.approx(0.0)
    assert rsi_down[-1] == pytest.approx(0.0)


def test_compute_atr_series_constant_range() -> None:
    candles = [
        Candle(
            timestamp=1700000000 + i * 900,
            open=4000.0,
            high=4005.0,
            low=3995.0,
            close=4000.0,
            is_closed=True,
        )
        for i in range(25)
    ]
    atr14 = compute_atr_series(candles, period=14)
    assert all(x is None for x in atr14[:13])
    # Each candle has High - Low = 10.0 and prev_close = 4000.0 -> TR = 10.0
    assert atr14[13] == pytest.approx(10.0)
    assert atr14[-1] == pytest.approx(10.0)


def test_compute_latest_indicators_ignores_unclosed_candle() -> None:
    closed_candles = [
        Candle(
            timestamp=1700000000 + i * 900,
            open=4000.0 + i * 0.5,
            high=4003.0 + i * 0.5,
            low=3998.0 + i * 0.5,
            close=4001.0 + i * 0.5,
            is_closed=True,
        )
        for i in range(210)
    ]
    snap_closed = compute_latest_indicators(closed_candles)
    assert snap_closed is not None

    # Append a wild unclosed (forming) candle with an extreme price spike
    forming_candle = Candle(
        timestamp=1700000000 + 210 * 900,
        open=4105.0,
        high=5000.0,
        low=3000.0,
        close=4999.0,
        is_closed=False,
    )
    snap_with_forming = compute_latest_indicators(closed_candles + [forming_candle])
    assert snap_with_forming is not None
    assert snap_with_forming.ema_fast == pytest.approx(snap_closed.ema_fast)
    assert snap_with_forming.ema_slow == pytest.approx(snap_closed.ema_slow)
    assert snap_with_forming.rsi == pytest.approx(snap_closed.rsi)
    assert snap_with_forming.atr == pytest.approx(snap_closed.atr)
