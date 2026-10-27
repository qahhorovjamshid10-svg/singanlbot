"""Asosiy Bot Orkestratori: holatni tiklash, polling sikli, paper-trading va Telegram/Webhook integratsiyasi."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

from src.config import BotConfig
from src.database import SignalDatabase
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
            db=self.db, config=self.config
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

        # 1. Monitor active signals for TP1/TP2/TP3/SL hits
        level_events: list[LevelHitEvent] = self.paper_trader.process_active_signals(candles)
        for ev in level_events:
            self.telegram_bot.notify_level_hit(ev)

        # 2. Evaluate latest closed candle for new BUY/SELL signal
        closed_candles = [c for c in candles if c.is_closed]
        if not closed_candles:
            return {"status": "no_closed_candles", "new_signal": None, "level_events": level_events}

        latest_closed = closed_candles[-1]
        self.db.set_state("last_processed_candle_ts", str(latest_closed.timestamp))

        risk_usd = self.paper_trader.current_risk_usd()
        candidate = self.strategy.evaluate_latest(
            candles=closed_candles, risk_usd=risk_usd, mark_emitted=False
        )

        new_signal: Optional[Signal] = None
        if candidate is not None:
            # Check if an active signal in the same direction already exists
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
