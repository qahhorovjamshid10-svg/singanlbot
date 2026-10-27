"""Trading algoritmi, SL/TP1/TP2/TP3, closed-candle qoidasi va deduplikatsiya testlari."""

from __future__ import annotations

import pytest

from src.config import BotConfig
from src.models import Candle
from src.strategy import XAUUSDStrategy, calculate_levels


def test_calculate_levels_buy_and_sell() -> None:
    buy_lv = calculate_levels(
        side="BUY",
        entry_price=4000.0,
        atr_value=10.0,
        atr_sl_multiplier=1.5,
        tp1_rr=1.0,
        tp2_rr=2.0,
        tp3_rr=3.0,
    )
    assert buy_lv["entry"] == pytest.approx(4000.0)
    assert buy_lv["sl_distance"] == pytest.approx(15.0)
    assert buy_lv["sl"] == pytest.approx(3985.0)
    assert buy_lv["tp1"] == pytest.approx(4015.0)
    assert buy_lv["tp2"] == pytest.approx(4030.0)
    assert buy_lv["tp3"] == pytest.approx(4045.0)

    sell_lv = calculate_levels(
        side="SELL",
        entry_price=4000.0,
        atr_value=10.0,
        atr_sl_multiplier=1.5,
        tp1_rr=1.0,
        tp2_rr=2.0,
        tp3_rr=3.0,
    )
    assert sell_lv["entry"] == pytest.approx(4000.0)
    assert sell_lv["sl_distance"] == pytest.approx(15.0)
    assert sell_lv["sl"] == pytest.approx(4015.0)
    assert sell_lv["tp1"] == pytest.approx(3985.0)
    assert sell_lv["tp2"] == pytest.approx(3970.0)
    assert sell_lv["tp3"] == pytest.approx(3955.0)


def _build_bullish_pullback_candles() -> list[Candle]:
    """Construct a deterministic 210-candle bullish trend with a clean EMA50 pullback bounce at the end."""
    candles: list[Candle] = []
    base_ts = 1780000000
    price = 3900.0
    for i in range(208):
        delta = 1.8 if i % 2 == 0 else -1.2
        price = round(price + delta, 2)
        open_p = round(price - (0.6 if delta > 0 else -0.6), 2)
        candles.append(
            Candle(
                timestamp=base_ts + i * 900,
                open=open_p,
                high=round(max(price, open_p) + 2.0, 2),
                low=round(min(price, open_p) - 2.0, 2),
                close=price,
                is_closed=True,
            )
        )

    # Candle 208: pullback touching EMA50 so RSI cools to ~49
    c208_close = round(candles[-1].close - 8.0, 2)
    candles.append(
        Candle(
            timestamp=base_ts + 208 * 900,
            open=candles[-1].close,
            high=round(candles[-1].close + 0.5, 2),
            low=round(c208_close - 2.0, 2),
            close=c208_close,
            is_closed=True,
        )
    )

    # Candle 209: closed bullish bounce candle closing back above EMA50 with RSI ~57
    c209_open = c208_close
    c209_close = round(c208_close + 6.0, 2)
    candles.append(
        Candle(
            timestamp=base_ts + 209 * 900,
            open=c209_open,
            high=round(c209_close + 1.0, 2),
            low=round(c209_open - 0.5, 2),
            close=c209_close,
            is_closed=True,
        )
    )
    return candles


def test_strategy_emits_buy_on_closed_candle_and_deduplicates() -> None:
    candles = _build_bullish_pullback_candles()
    strategy = XAUUSDStrategy(config=BotConfig())

    sig1 = strategy.evaluate_latest(candles, risk_usd=100.0, mark_emitted=True)
    assert sig1 is not None
    assert sig1.side == "BUY"
    assert sig1.entry_price == pytest.approx(candles[-1].close)
    assert sig1.stop_loss < sig1.entry_price
    assert sig1.tp1 > sig1.entry_price
    assert sig1.tp2 > sig1.tp1
    assert sig1.tp3 > sig1.tp2
    assert "1:1.0" in sig1.rr_display

    # Evaluating the exact same closed candle a second time must return None (no duplicate signal)
    sig2 = strategy.evaluate_latest(candles, risk_usd=100.0, mark_emitted=True)
    assert sig2 is None


def test_strategy_never_confirms_unclosed_candle() -> None:
    candles = _build_bullish_pullback_candles()
    # Mark the trigger candle at the end as NOT closed (forming candle)
    unclosed_tail = Candle(
        timestamp=candles[-1].timestamp,
        open=candles[-1].open,
        high=candles[-1].high,
        low=candles[-1].low,
        close=candles[-1].close,
        is_closed=False,
    )
    candles_with_unclosed = candles[:-1] + [unclosed_tail]

    strategy = XAUUSDStrategy(config=BotConfig())
    sig = strategy.evaluate_latest(candles_with_unclosed, risk_usd=100.0, mark_emitted=True)
    # Because the trigger candle is unclosed, it must NOT be confirmed!
    assert sig is None
