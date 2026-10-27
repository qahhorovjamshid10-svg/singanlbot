"""Tarixiy XAUUSD ma'lumotlarida backtest va paper-trading tekshiruvi testlari."""

from __future__ import annotations

from pathlib import Path

from src.backtest import format_backtest_report, run_backtest
from src.config import BotConfig
from src.market_data import load_candles_from_csv


def test_backtest_on_real_xauusd_15m_dataset() -> None:
    csv_path = Path("data/xauusd_historical_sample.csv")
    assert csv_path.exists()
    candles = load_candles_from_csv(csv_path)
    assert len(candles) >= 2000

    metrics = run_backtest(
        candles=candles,
        config=BotConfig(timeframe="15m"),
        data_source_name="Real XAUUSD 15m Sample",
    )
    assert metrics.total_candles == len(candles)
    assert metrics.total_signals > 0
    assert metrics.tp1_hits >= 0
    assert metrics.sl_hits >= 0
    # Verify no duplicate signal keys were generated during backtest
    keys = [s.signal_key for s in metrics.signals]
    assert len(keys) == len(set(keys))

    report = format_backtest_report(metrics)
    assert "BACKTEST VA PAPER-TRADING HISOBOTI" in report
    assert "foyda kafolati berilmaydi" in report.lower()


def test_backtest_on_real_xauusd_1h_dataset() -> None:
    csv_path = Path("data/xauusd_1h_sample.csv")
    assert csv_path.exists()
    candles = load_candles_from_csv(csv_path)
    assert len(candles) >= 1500

    metrics = run_backtest(
        candles=candles,
        config=BotConfig(timeframe="1h"),
        data_source_name="Real XAUUSD 1h Sample",
    )
    assert metrics.total_signals > 0
    assert metrics.final_balance > 0
