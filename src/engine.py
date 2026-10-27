"""Asosiy Bot Orkestratori: holatni tiklash, polling sikli, paper-trading va Telegram/Webhook integratsiyasi."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

from src.config import BotConfig
from src.database import SignalDatabase
from src.indicators import (
    compute_atr_series,
    compute_ema_series,
    compute_latest_indicators,
    compute_rsi_series,
)
from src.market_data import MarketDataProvider
from src.models import Candle, LevelHitEvent, Signal
from src.paper_trader import PaperTradingEngine
from src.strategy import XAUUSDStrategy
from src.telegram_bot import TelegramBotService
from src.webhook_server import WebhookHTTPServer, WebhookProcessor

logger = logging.getLogger(__name__)


class SignalBotEngine:
    """
    Coordinates XAUUSD market polling, closed-candle strategy evaluation,
    paper-trading TP/SL monitoring, SQLite persistence, state recovery on restart,
    Telegram bot commands, and webhook ingestion.
    """

    def __init__(
        self,
        config: Optional[BotConfig] = None,
        db: Optional[SignalDatabase] = None,
        market_provider: Optional[MarketDataProvider] = None,
        telegram_bot: Optional[TelegramBotService] = None,
    ) -> None:
        self.config = config or BotConfig.from_env()
        self.db = db or SignalDatabase(self.config.database_path)
        self.paper_trader = PaperTradingEngine(db=self.db, config=self.config)
        self.strategy = XAUUSDStrategy(config=self.config)
        self.market_provider = market_provider or MarketDataProvider(
            source=self.config.market_data_source,
            timeframe=self.config.timeframe,
            twelvedata_api_key=self.config.twelvedata_api_key,
        )
        self.telegram_bot = telegram_bot or TelegramBotService(
            db=self.db,
            config=self.config,
            scan_callback=self.build_live_scan_report,
        )
        self.webhook_processor = WebhookProcessor(
            db=self.db,
            config=self.config,
            on_new_signal=self._on_webhook_signal,
            risk_usd_provider=self.paper_trader.current_risk_usd,
        )
        self.webhook_server: Optional[WebhookHTTPServer] = None
        self._stop_requested = False

        # Server qayta ishga tushganda (restart) holatni SQLite bazadan tiklash
        self.restore_state()

    def restore_state(self) -> dict[str, Any]:
        """
        Restore bot state from SQLite upon startup/restart:
        - Pre-registers all historical signal keys to prevent duplicate emissions.
        - Restores virtual balance, pause status, and active signals.
        """
        existing_keys = self.db.get_all_signal_keys()
        self.strategy.register_existing_keys(existing_keys)
        active_signals = self.db.get_active_signals()
        is_paused = self.db.is_paused()
        balance = self.db.get_virtual_balance(self.config.initial_virtual_balance)

        state_info = {
            "restored_signal_keys": len(existing_keys),
            "active_signals_count": len(active_signals),
            "is_paused": is_paused,
            "virtual_balance": balance,
            "subscribers_count": len(self.db.get_active_subscribers()),
        }
        logger.info("Server holati SQLite bazadan tiklandi: %s", state_info)
        return state_info

    def _on_webhook_signal(self, signal: Signal) -> None:
        """Callback triggered when a valid new signal arrives via webhook."""
        self.strategy.register_existing_keys([signal.signal_key])
        self.telegram_bot.notify_new_signal(signal)

    def build_live_scan_report(self) -> str:
        """Fetch latest market data and return a live technical analysis report for /scan."""
        candles = self.market_provider.fetch_candles()
        closed = [c for c in candles if c.is_closed]
        if not closed:
            return "⚠️ Yopilgan sham ma'lumotlari topilmadi."

        snap = compute_latest_indicators(
            closed,
            ema_fast_period=self.config.ema_fast_period,
            ema_slow_period=self.config.ema_slow_period,
            rsi_period=self.config.rsi_period,
            atr_period=self.config.atr_period,
        )
        last_c = closed[-1]
        spot = self.market_provider.last_spot_quote
        spot_line = (
            f"• <b>Swissquote Spot XAU/USD:</b> <code>{spot['mid']:.2f}</code> "
            f"(Bid: {spot['bid']:.2f} / Ask: {spot['ask']:.2f})\n"
            if spot
            else ""
        )
        if snap is None:
            return "⚠️ Indikatorlarni hisoblash uchun yetarli shamlar yo'q."

        trend = (
            "🟢 KO'TARILUVCHI (BULLISH: EMA50 > EMA200)"
            if snap.ema_fast > snap.ema_slow
            else "🔴 PASAYUVCHI (BEARISH: EMA50 < EMA200)"
        )
        return (
            "🔎 <b>XAUUSD JONLI BOZOR VA INDIKATOR TAHLILI (/scan)</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>Manba:</b> {self.market_provider.last_used_source}\n"
            f"{spot_line}"
            f"• <b>Oxirgi yopilgan sham ({self.config.timeframe}):</b> {last_c.iso_time}\n"
            f"• <b>Yopilish narxi (Close):</b> <code>{last_c.close:.2f}</code> "
            f"(H: {last_c.high:.2f} / L: {last_c.low:.2f})\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>Trend:</b> {trend}\n"
            f"• <b>EMA 50:</b> <code>{snap.ema_fast:.2f}</code> | <b>EMA 200:</b> <code>{snap.ema_slow:.2f}</code>\n"
            f"• <b>RSI 14:</b> <code>{snap.rsi:.2f}</code> (Oldingi: <code>{(snap.prev_rsi or snap.rsi):.2f}</code>)\n"
            f"• <b>ATR 14:</b> <code>{snap.atr:.2f}</code> (SL masofa: <code>{snap.atr * self.config.atr_sl_multiplier:.2f}</code>)\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "ℹ️ <i>Bot har 60 soniyada yangi yopilgan shamni avtomatik tekshiradi va signal chiqishi bilan xabar yuboradi.</i>"
        )

    def _warmup_recent_candles_if_empty(self, closed_candles: list[Candle], lookback: int = 12) -> Optional[Signal]:
        """
        If SQLite database is completely empty (e.g. fresh deploy on Render),
        scan the last `lookback` closed candles so any recent signal from the
        current session is immediately captured and monitored.
        """
        if self.db.get_all_signal_keys():
            return None

        min_len = max(
            self.config.ema_fast_period,
            self.config.ema_slow_period,
            self.config.rsi_period + 1,
            self.config.atr_period,
        ) + 1
        if len(closed_candles) < min_len:
            return None

        closes = [c.close for c in closed_candles]
        ema_fast = compute_ema_series(closes, self.config.ema_fast_period)
        ema_slow = compute_ema_series(closes, self.config.ema_slow_period)
        rsi = compute_rsi_series(closes, self.config.rsi_period)
        atr = compute_atr_series(closed_candles, self.config.atr_period)

        start_idx = max(min_len - 1, len(closed_candles) - lookback)
        last_new_signal: Optional[Signal] = None

        for idx in range(start_idx, len(closed_candles)):
            candle = closed_candles[idx]
            for active_sig in self.db.get_active_signals():
                self.paper_trader.evaluate_signal_on_candle(active_sig, candle)

            risk_usd = self.paper_trader.current_risk_usd()
            cand = self.strategy.evaluate_candle_index(
                closed_candles=closed_candles,
                idx=idx,
                ema_fast_series=ema_fast,
                ema_slow_series=ema_slow,
                rsi_series=rsi,
                atr_series=atr,
                risk_usd=risk_usd,
                source="polling",
            )
            if cand is None:
                continue
            active_same = any(
                s.side == cand.side and s.status == "ACTIVE"
                for s in self.db.get_active_signals()
            )
            if active_same:
                continue
            if self.db.save_signal(cand) is not None:
                self.strategy.register_existing_keys([cand.signal_key])
                last_new_signal = cand

        # Run TP/SL checks up to the final closed candle
        for active_sig in self.db.get_active_signals():
            for idx in range(start_idx, len(closed_candles)):
                self.paper_trader.evaluate_signal_on_candle(active_sig, closed_candles[idx])

        if last_new_signal is not None:
            self.telegram_bot.notify_new_signal(last_new_signal)
        return last_new_signal

    def run_market_cycle_once(
        self, candles_override: Optional[list[Candle]] = None
    ) -> dict[str, Any]:
        """
        Execute one monitoring cycle:
        1. If paused, skip cycle.
        2. Fetch latest XAUUSD candles (or use `candles_override`).
        3. Check active signals for TP1, TP2, TP3, or SL hits and notify Telegram.
        4. Check latest CLOSED candle for a new BUY/SELL signal and notify Telegram.
        """
        if self.db.is_paused():
            return {"status": "paused", "new_signal": None, "level_events": []}

        candles = (
            candles_override
            if candles_override is not None
            else self.market_provider.fetch_candles()
        )
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        self.db.set_state("last_check_time", now_str)
        self.db.set_state("last_data_source", self.market_provider.last_used_source)

        closed_candles = [c for c in candles if c.is_closed]
        if not closed_candles:
            return {"status": "no_closed_candles", "new_signal": None, "level_events": []}

        # Warm-up recent candles if DB is empty on fresh deploy (only on real market polling)
        if candles_override is None and not self.db.get_all_signal_keys():
            warm_sig = self._warmup_recent_candles_if_empty(closed_candles, lookback=12)
            latest_closed = closed_candles[-1]
            self.db.set_state("last_processed_candle_ts", str(latest_closed.timestamp))
            return {
                "status": "ok",
                "latest_closed_candle": latest_closed.iso_time,
                "latest_close_price": latest_closed.close,
                "new_signal": warm_sig,
                "level_events": [],
            }

        # 1. Monitor active signals for TP1/TP2/TP3/SL hits
        level_events: list[LevelHitEvent] = self.paper_trader.process_active_signals(candles)
        for ev in level_events:
            self.telegram_bot.notify_level_hit(ev)

        # 2. Evaluate latest closed candle for new BUY/SELL signal
        latest_closed = closed_candles[-1]
        self.db.set_state("last_processed_candle_ts", str(latest_closed.timestamp))

        risk_usd = self.paper_trader.current_risk_usd()
        candidate = self.strategy.evaluate_latest(
            candles=closed_candles, risk_usd=risk_usd, mark_emitted=False
        )

        new_signal: Optional[Signal] = None
        if candidate is not None:
            active_same_side = any(
                s.side == candidate.side and s.status == "ACTIVE"
                for s in self.db.get_active_signals()
            )
            if not active_same_side:
                saved_id = self.db.save_signal(candidate)
                if saved_id is not None:
                    self.strategy.register_existing_keys([candidate.signal_key])
                    self.telegram_bot.notify_new_signal(candidate)
                    new_signal = candidate

        return {
            "status": "ok",
            "latest_closed_candle": latest_closed.iso_time,
            "latest_close_price": latest_closed.close,
            "new_signal": new_signal,
            "level_events": level_events,
        }

    def start_forever(self) -> None:
        """Start webhook server (if enabled) and run continuous polling loop."""
        if self.config.webhook_enabled:
            self.webhook_server = WebhookHTTPServer(
                processor=self.webhook_processor,
                host=self.config.webhook_host,
                port=self.config.port,
            )
            self.webhook_server.start_background()

        logger.info(
            "XAUUSD AI Signal Bot ishga tushdi (Symbol=%s, Timeframe=%s, Poll=%ds)",
            self.config.symbol,
            self.config.timeframe,
            self.config.poll_interval_seconds,
        )

        last_market_poll = 0.0
        try:
            while not self._stop_requested:
                # 1. Poll Telegram commands frequently (every ~2 seconds)
                try:
                    self.telegram_bot.poll_updates_once(timeout=1)
                except Exception as exc:
                    logger.warning("Telegram update polling xatosi: %s", exc)

                # 2. Poll market data on configured interval
                now = time.time()
                if (now - last_market_poll) >= self.config.poll_interval_seconds:
                    last_market_poll = now
                    try:
                        res = self.run_market_cycle_once()
                        logger.info("Bozor tekshiruvi yakunlandi: %s", res.get("status"))
                    except Exception as exc:
                        logger.error("Bozor siklida xatolik: %s", exc)

                time.sleep(1.5)
        except KeyboardInterrupt:
            logger.info("To'xtatish signali qabul qilindi (KeyboardInterrupt).")
        finally:
            self.stop()

    def stop(self) -> None:
        self._stop_requested = True
        if self.webhook_server is not None:
            self.webhook_server.stop()
        self.db.close()
