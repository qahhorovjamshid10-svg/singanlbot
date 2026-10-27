"""Tarixiy ma'lumotlarda algoritmni backtest va paper-trading tekshiruvidan o'tkazish moduli."""

from __future__ import annotations

from typing import Optional, Sequence

from src.config import BotConfig
from src.database import SignalDatabase
from src.indicators import (
    compute_atr_series,
    compute_ema_series,
    compute_rsi_series,
)
from src.models import BacktestMetrics, Candle, DISCLAIMER_UZ, LevelHitEvent, Signal
from src.paper_trader import PaperTradingEngine
from src.strategy import XAUUSDStrategy


def run_backtest(
    candles: Sequence[Candle],
    config: Optional[BotConfig] = None,
    data_source_name: str = "Historical XAUUSD",
) -> BacktestMetrics:
    """
    Run a bar-by-bar historical backtest on closed candles using an isolated
    in-memory SQLite database and PaperTradingEngine.
    """
    cfg = config or BotConfig()
    closed_candles = [c for c in candles if c.is_closed]
    if len(closed_candles) < max(cfg.ema_slow_period + 5, 205):
        raise ValueError(
            f"Backtest uchun kamida {cfg.ema_slow_period + 5} ta yopilgan sham kerak "
            f"(mavjud: {len(closed_candles)})."
        )

    db = SignalDatabase(":memory:")
    db.set_virtual_balance(cfg.initial_virtual_balance)
    paper_engine = PaperTradingEngine(db=db, config=cfg)
    strategy = XAUUSDStrategy(config=cfg)

    closes = [c.close for c in closed_candles]
    ema_fast = compute_ema_series(closes, cfg.ema_fast_period)
    ema_slow = compute_ema_series(closes, cfg.ema_slow_period)
    rsi = compute_rsi_series(closes, cfg.rsi_period)
    atr = compute_atr_series(closed_candles, cfg.atr_period)

    all_events: list[LevelHitEvent] = []
    peak_balance = cfg.initial_virtual_balance
    max_drawdown_pct = 0.0
    last_signal_idx_by_side: dict[str, int] = {}

    start_idx = max(cfg.ema_fast_period, cfg.ema_slow_period, cfg.rsi_period + 1, cfg.atr_period)

    for idx in range(start_idx, len(closed_candles)):
        candle = closed_candles[idx]

        # 1. Avval ochiq (ACTIVE) signallarning TP1, TP2, TP3 yoki SL darajalarini tekshiramiz
        active_signals = db.get_active_signals()
        for active_sig in active_signals:
            evs = paper_engine.evaluate_signal_on_candle(active_sig, candle)
            if evs:
                all_events.extend(evs)
                curr_bal = db.get_virtual_balance(cfg.initial_virtual_balance)
                if curr_bal > peak_balance:
                    peak_balance = curr_bal
                dd_pct = ((peak_balance - curr_bal) / peak_balance) * 100.0 if peak_balance > 0 else 0.0
                if dd_pct > max_drawdown_pct:
                    max_drawdown_pct = dd_pct

        # 2. Yangi BUY/SELL signalini faqat sham yopilganda tekshiramiz
        risk_usd = paper_engine.current_risk_usd()
        candidate = strategy.evaluate_candle_index(
            closed_candles=closed_candles,
            idx=idx,
            ema_fast_series=ema_fast,
            ema_slow_series=ema_slow,
            rsi_series=rsi,
            atr_series=atr,
            risk_usd=risk_usd,
            source="backtest",
        )
        if candidate is None:
            continue

        # Cooldown va bir xil yo'nalishda ochiq signal filtri
        prev_idx = last_signal_idx_by_side.get(candidate.side)
        if prev_idx is not None and (idx - prev_idx) < cfg.signal_cooldown_candles:
            continue

        still_active_same_side = any(
            s.side == candidate.side and s.status == "ACTIVE"
            for s in db.get_active_signals()
        )
        if still_active_same_side:
            continue

        saved_id = db.save_signal(candidate)
        if saved_id is not None:
            strategy.register_existing_keys([candidate.signal_key])
            last_signal_idx_by_side[candidate.side] = idx

    summary = db.get_stats_summary(cfg.initial_virtual_balance)
    all_signals = list(reversed(db.get_recent_signals(limit=10000)))

    gross_profit = sum(s.realized_pnl_usd for s in all_signals if s.realized_pnl_usd > 0)
    gross_loss = abs(sum(s.realized_pnl_usd for s in all_signals if s.realized_pnl_usd < 0))
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (99.99 if gross_profit > 0 else 0.0)

    db.close()

    return BacktestMetrics(
        symbol=cfg.symbol,
        timeframe=cfg.timeframe,
        data_source=data_source_name,
        total_candles=len(closed_candles),
        start_time=closed_candles[0].iso_time,
        end_time=closed_candles[-1].iso_time,
        initial_balance=summary["initial_balance"],
        final_balance=summary["current_balance"],
        net_profit_usd=summary["total_pnl_usd"],
        net_return_pct=summary["return_pct"],
        total_signals=summary["total_signals"],
        closed_signals=summary["closed_signals"],
        active_signals=summary["active_signals"],
        winning_signals=summary["wins"],
        losing_signals=summary["losses"],
        win_rate_pct=summary["win_rate_pct"],
        tp1_hits=summary["tp1_hits"],
        tp2_hits=summary["tp2_hits"],
        tp3_hits=summary["tp3_hits"],
        sl_hits=summary["sl_hits"],
        total_r=summary["total_r"],
        profit_factor=profit_factor,
        max_drawdown_pct=round(max_drawdown_pct, 2),
        signals=all_signals,
        events=all_events,
        disclaimer=DISCLAIMER_UZ,
    )


def format_backtest_report(metrics: BacktestMetrics) -> str:
    """Format backtest metrics into a human-readable Uzbek report."""
    lines = [
        "============================================================",
        f"📊 XAUUSD ({metrics.timeframe}) BACKTEST VA PAPER-TRADING HISOBOTI",
        "============================================================",
        f"Manba             : {metrics.data_source}",
        f"Davr              : {metrics.start_time} -> {metrics.end_time}",
        f"Yopilgan shamlar  : {metrics.total_candles} ta",
        "------------------------------------------------------------",
        f"Boshlang'ich demo : ${metrics.initial_balance:,.2f}",
        f"Yakuniy demo      : ${metrics.final_balance:,.2f}",
        f"Sof natija (PnL)  : ${metrics.net_profit_usd:+,.2f} ({metrics.net_return_pct:+.2f}%)",
        f"Jami R-multiple   : {metrics.total_r:+.2f}R",
        f"Profit Factor     : {metrics.profit_factor:.2f}",
        f"Max Drawdown      : {metrics.max_drawdown_pct:.2f}%",
        "------------------------------------------------------------",
        f"Jami signallar    : {metrics.total_signals} ta (Yopilgan: {metrics.closed_signals}, Faol: {metrics.active_signals})",
        f"Yutuqli / Zararli : {metrics.winning_signals} / {metrics.losing_signals} (Win Rate: {metrics.win_rate_pct:.2f}%)",
        f"TP1 urilgan       : {metrics.tp1_hits} marta",
        f"TP2 urilgan       : {metrics.tp2_hits} marta",
        f"TP3 urilgan       : {metrics.tp3_hits} marta",
        f"SL urilgan        : {metrics.sl_hits} marta",
        "------------------------------------------------------------",
        metrics.disclaimer,
        "============================================================",
    ]
    return "\n".join(lines)
