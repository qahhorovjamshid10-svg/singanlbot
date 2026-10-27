"""SQLite ma'lumotlar bazasi va server qayta ishga tushganda (restart) holatni tiklash testlari."""

from __future__ import annotations

from pathlib import Path

from src.config import BotConfig
from src.database import SignalDatabase
from src.engine import SignalBotEngine
from src.models import Signal


def _make_sample_signal(ts: int = 1780000900, side: str = "BUY") -> Signal:
    return Signal(
        signal_key=f"XAUUSD:15m:{ts}:{side}",
        symbol="XAUUSD",
        timeframe="15m",
        side=side,
        candle_timestamp=ts,
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
        reason="Test signal",
    )


def test_sqlite_signal_persistence_and_restart_recovery(tmp_path: Path) -> None:
    db_file = str(tmp_path / "restart_test.db")
    cfg = BotConfig(database_path=db_file, initial_virtual_balance=10000.0)

    # 1-sessiya: signal saqlash, balansni yangilash, pauza holatini yoqish
    engine1 = SignalBotEngine(config=cfg)
    sig = _make_sample_signal()
    saved_id = engine1.db.save_signal(sig)
    assert saved_id is not None
    # Takroriy signal saqlanmasligi shart
    assert engine1.db.save_signal(sig) is None

    engine1.db.set_paused(True)
    engine1.db.set_virtual_balance(10250.50)
    engine1.db.add_subscriber("123456789", username="trader_uz", first_name="Ali")
    engine1.stop()

    # 2-sessiya (Server qayta ishga tushdi / Restart): barcha holat SQLite'dan tiklanishi shart
    engine2 = SignalBotEngine(config=cfg)
    restored = engine2.restore_state()

    assert restored["restored_signal_keys"] == 1
    assert restored["active_signals_count"] == 1
    assert restored["is_paused"] is True
    assert restored["virtual_balance"] == 10250.50
    assert restored["subscribers_count"] == 1
    assert engine2.strategy.has_emitted(sig.signal_key) is True
    engine2.stop()
