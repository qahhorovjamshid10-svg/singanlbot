"""Paper-Trading dvigateli: TP1, TP2, TP3 va SL bosqichma-bosqich urilishi va virtual balans testlari."""

from __future__ import annotations

import pytest

from src.config import BotConfig
from src.database import SignalDatabase
from src.models import Candle, Signal
from src.paper_trader import PaperTradingEngine


def test_buy_signal_hits_tp1_tp2_tp3_sequentially() -> None:
    db = SignalDatabase(":memory:")
    cfg = BotConfig(initial_virtual_balance=10000.0, risk_per_trade_pct=1.0)
    trader = PaperTradingEngine(db=db, config=cfg)

    sig = Signal(
        signal_key="XAUUSD:15m:1780000000:BUY",
        symbol="XAUUSD",
        timeframe="15m",
        side="BUY",
        candle_timestamp=1780000000,
        entry_price=4000.0,
        stop_loss=3985.0,
        tp1=4015.0,
        tp2=4030.0,
        tp3=4045.0,
        sl_distance=15.0,
        rr_tp1=1.0,
        rr_tp2=2.0,
        rr_tp3=3.0,
        ema_fast=3995.0,
        ema_slow=3970.0,
        rsi=58.0,
        atr=10.0,
        reason="Test BUY",
        risk_usd=100.0,
    )
    db.save_signal(sig)

    # 1-sham: TP1 (4015.0) ga yetadi
    c1 = Candle(timestamp=1780000900, open=4002.0, high=4016.0, low=3999.0, close=4014.0)
    ev1 = trader.evaluate_signal_on_candle(sig, c1)
    assert len(ev1) == 1
    assert ev1[0].level == "TP1"
    assert sig.tp1_hit is True
    assert sig.status == "ACTIVE"
    assert db.get_virtual_balance() == pytest.approx(10034.0)  # +0.34 * $100

    # 2-sham: TP2 (4030.0) ga yetadi
    c2 = Candle(timestamp=1780001800, open=4014.0, high=4032.0, low=4012.0, close=4029.0)
    ev2 = trader.evaluate_signal_on_candle(sig, c2)
    assert len(ev2) == 1
    assert ev2[0].level == "TP2"
    assert sig.tp2_hit is True
    assert sig.status == "ACTIVE"
    assert db.get_virtual_balance() == pytest.approx(10100.0)  # +0.34*100 + 0.33*200 = +$100

    # 3-sham: TP3 (4045.0) ga yetadi va signal to'liq yopiladi
    c3 = Candle(timestamp=1780002700, open=4029.0, high=4048.0, low=4028.0, close=4046.0)
    ev3 = trader.evaluate_signal_on_candle(sig, c3)
    assert len(ev3) == 1
    assert ev3[0].level == "TP3"
    assert sig.tp3_hit is True
    assert sig.status == "CLOSED_TP3"
    assert db.get_virtual_balance() == pytest.approx(10199.0)  # +$34 + $66 + $99 = +$199
    db.close()


def test_sell_signal_hits_stop_loss() -> None:
    db = SignalDatabase(":memory:")
    cfg = BotConfig(initial_virtual_balance=10000.0, risk_per_trade_pct=1.0)
    trader = PaperTradingEngine(db=db, config=cfg)

    sig = Signal(
        signal_key="XAUUSD:15m:1780000000:SELL",
        symbol="XAUUSD",
        timeframe="15m",
        side="SELL",
        candle_timestamp=1780000000,
        entry_price=4000.0,
        stop_loss=4015.0,
        tp1=3985.0,
        tp2=3970.0,
        tp3=3955.0,
        sl_distance=15.0,
        rr_tp1=1.0,
        rr_tp2=2.0,
        rr_tp3=3.0,
        ema_fast=4005.0,
        ema_slow=4025.0,
        rsi=42.0,
        atr=10.0,
        reason="Test SELL",
        risk_usd=100.0,
    )
    db.save_signal(sig)

    c_sl = Candle(timestamp=1780000900, open=4001.0, high=4017.0, low=3998.0, close=4016.0)
    evs = trader.evaluate_signal_on_candle(sig, c_sl)
    assert len(evs) == 1
    assert evs[0].level == "SL"
    assert sig.sl_hit is True
    assert sig.status == "CLOSED_SL"
    assert db.get_virtual_balance() == pytest.approx(9900.0)  # -$100 (-1.0R)
    db.close()
