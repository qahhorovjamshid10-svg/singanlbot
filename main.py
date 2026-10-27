"""
XAUUSD (Gold) Telegram AI Signal Bot — Asosiy ishga tushirish fayli (main.py).

Ishlatish usullari:
  1. To'liq botni ishga tushirish (Telegram + Polling + Webhook):
       python main.py
  2. Bir martalik jonli bozor tekshiruvi:
       python main.py --mode once
  3. Tarixiy ma'lumotlarda backtest va paper-trading hisoboti:
       python main.py --mode backtest
  4. Tizim va ulanishlarni tekshirish:
       python main.py --mode self-check
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# Windows konsolida UTF-8 va emoji belgilarini to'g'ri chiqarish
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from src.backtest import format_backtest_report, run_backtest
from src.config import BotConfig
from src.engine import SignalBotEngine
from src.indicators import compute_latest_indicators
from src.market_data import (
    MarketDataProvider,
    fetch_swissquote_spot_xauusd,
    load_candles_from_csv,
)


def setup_logging(level_name: str = "INFO") -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def cmd_backtest(config: BotConfig, csv_path: str, timeframe: str, range_str: str) -> int:
    cfg = BotConfig(
        **{
            **config.__dict__,
            "timeframe": timeframe or config.timeframe,
        }
    )
    if csv_path and Path(csv_path).exists():
        candles = load_candles_from_csv(csv_path)
        source_label = f"Lokal CSV ({csv_path})"
    else:
        provider = MarketDataProvider(
            source=cfg.market_data_source,
            timeframe=cfg.timeframe,
            twelvedata_api_key=cfg.twelvedata_api_key,
        )
        candles = provider.fetch_candles(range_str=range_str)
        source_label = provider.last_used_source

    metrics = run_backtest(candles=candles, config=cfg, data_source_name=source_label)
    print(format_backtest_report(metrics))
    return 0


def cmd_once(config: BotConfig) -> int:
    engine = SignalBotEngine(config=config)
    try:
        candles = engine.market_provider.fetch_candles()
        closed = [c for c in candles if c.is_closed]
        snap = compute_latest_indicators(
            closed,
            ema_fast_period=config.ema_fast_period,
            ema_slow_period=config.ema_slow_period,
            rsi_period=config.rsi_period,
            atr_period=config.atr_period,
        )
        res = engine.run_market_cycle_once(candles_override=candles)
        spot = engine.market_provider.last_spot_quote or fetch_swissquote_spot_xauusd()

        print("============================================================")
        print("🔍 XAUUSD JONLI BOZOR VA INDIKATOR TEKSHIRUVI (--mode once)")
        print("============================================================")
        print(f"Ma'lumot manbasi    : {engine.market_provider.last_used_source}")
        if spot:
            print(
                f"Swissquote Spot XAU : Bid={spot['bid']:.2f} | Ask={spot['ask']:.2f} | Mid={spot['mid']:.2f}"
            )
        if closed:
            last_c = closed[-1]
            print(f"Oxirgi yopilgan sham: {last_c.iso_time} | Close={last_c.close:.2f}")
        if snap:
            trend_str = "BULLISH (Ko'tariluvchi)" if snap.ema_fast > snap.ema_slow else "BEARISH (Pasayuvchi)"
            print(f"Trend holati        : {trend_str}")
            print(f"EMA 50 / EMA 200    : {snap.ema_fast:.2f} / {snap.ema_slow:.2f}")
            print(f"RSI 14              : {snap.rsi:.2f}")
            print(f"ATR 14              : {snap.atr:.2f}")
        print(f"Sikl natijasi       : {res['status']}")
        if res.get("new_signal"):
            sig = res["new_signal"]
            print(
                f"YANGI SIGNAL!       : {sig.side} @ {sig.entry_price:.2f} "
                f"(SL={sig.stop_loss:.2f}, TP1={sig.tp1:.2f}, TP2={sig.tp2:.2f}, TP3={sig.tp3:.2f})"
            )
        else:
            print("Yangi signal        : Hozirgi yopilgan shamda yangi signal yo'q")
        print("============================================================")
        return 0
    finally:
        engine.stop()


def cmd_self_check(config: BotConfig) -> int:
    engine = SignalBotEngine(config=config)
    try:
        restored = engine.restore_state()
        candles = engine.market_provider.fetch_candles()
        closed = [c for c in candles if c.is_closed]
        spot = engine.market_provider.last_spot_quote or fetch_swissquote_spot_xauusd()
        report = {
            "config": config.masked_summary(),
            "restored_state": restored,
            "market_data": {
                "source": engine.market_provider.last_used_source,
                "total_candles": len(candles),
                "closed_candles": len(closed),
                "latest_closed_time": closed[-1].iso_time if closed else None,
                "latest_closed_price": closed[-1].close if closed else None,
                "swissquote_spot_xauusd": spot,
            },
        }
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    finally:
        engine.stop()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="XAUUSD (Gold) Telegram AI Signal Bot — Demo / Paper Trading"
    )
    parser.add_argument(
        "--mode",
        choices=["run", "once", "backtest", "self-check"],
        default="run",
        help="Ishga tushirish rejimi (standart: run)",
    )
    parser.add_argument(
        "--csv",
        default="",
        help="Backtest uchun lokal CSV fayl yo'li (ixtiyoriy)",
    )
    parser.add_argument(
        "--timeframe",
        default="",
        help="Taymfreym ustidan yozish (masalan: 15m yoki 1h)",
    )
    parser.add_argument(
        "--range",
        default="30d",
        help="Yahoo Finance tarixiy ma'lumot oralig'i (masalan: 10d, 30d, 60d)",
    )
    args = parser.parse_args()

    config = BotConfig.from_env()
    setup_logging(config.log_level)

    if args.mode == "backtest":
        return cmd_backtest(
            config=config,
            csv_path=args.csv,
            timeframe=args.timeframe,
            range_str=args.range,
        )
    if args.mode == "once":
        return cmd_once(config=config)
    if args.mode == "self-check":
        return cmd_self_check(config=config)

    engine = SignalBotEngine(config=config)
    engine.start_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
