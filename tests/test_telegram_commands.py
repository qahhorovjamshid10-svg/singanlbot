"""Telegram bot buyruqlari (/start, /status, /signals, /stats, /pause, /resume) va xabar formatlari testlari."""

from __future__ import annotations

from src.config import BotConfig
from src.database import SignalDatabase
from src.models import LevelHitEvent, Signal
from src.telegram_bot import (
    TelegramBotService,
    format_level_hit_message,
    format_new_signal_message,
)


def test_telegram_commands_and_notifications() -> None:
    db = SignalDatabase(":memory:")
    sent_messages: list[tuple[str, str]] = []
    bot = TelegramBotService(
        db=db,
        config=BotConfig(),
        send_callback=lambda chat_id, text: (sent_messages.append((chat_id, text)) or True),
    )

    # 1. /start buyrug'i
    start_reply = bot.handle_command("/start", chat_id="777", username="gold_user", first_name="Sardor")
    assert "Sardor" in start_reply
    assert "XAUUSD" in start_reply
    assert "foyda kafolati berilmaydi" in start_reply.lower()
    assert "777" in db.get_active_subscribers()

    # 2. /pause va /resume buyruqlari
    pause_reply = bot.handle_command("/pause", chat_id="777")
    assert "to'xtatildi" in pause_reply.lower()
    assert db.is_paused() is True

    status_paused = bot.handle_command("/status", chat_id="777")
    assert "PAUSED" in status_paused

    resume_reply = bot.handle_command("/resume", chat_id="777")
    assert "faollashtirildi" in resume_reply.lower()
    assert db.is_paused() is False

    # 3. Yangi signal qo'shish va bildirishnoma yuborish
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
        reason="EMA50 bounce",
    )
    db.save_signal(sig)
    bot.notify_new_signal(sig)
    assert len(sent_messages) == 1
    assert "4000.00" in sent_messages[-1][1]
    assert "TP1" in sent_messages[-1][1]
    assert "TP2" in sent_messages[-1][1]
    assert "TP3" in sent_messages[-1][1]

    # 4. /status, /signals, /stats buyruqlari
    status_active = bot.handle_command("/status", chat_id="777")
    assert "4000.00" in status_active

    signals_reply = bot.handle_command("/signals", chat_id="777")
    assert "XAUUSD BUY" in signals_reply

    stats_reply = bot.handle_command("/stats", chat_id="777")
    assert "10,000.00" in stats_reply
    assert "Win Rate" in stats_reply

    # 5. TP1 / SL hodisa xabari formati
    ev = LevelHitEvent(
        signal_id=1,
        signal_key=sig.signal_key,
        symbol="XAUUSD",
        side="BUY",
        level="TP1",
        level_price=4015.0,
        trigger_price=4015.0,
        candle_timestamp=1780000900,
        tranche_r=0.34,
        tranche_pnl_usd=34.0,
        total_realized_r=0.34,
        total_realized_pnl_usd=34.0,
        signal_status="ACTIVE",
        virtual_balance_after=10034.0,
    )
    bot.notify_level_hit(ev)
    assert len(sent_messages) == 2
    assert "TP1 DARAJASIGA YETDI" in sent_messages[-1][1]
    assert format_new_signal_message(sig)
    assert format_level_hit_message(ev)
    db.close()


def test_multiple_chat_ids_in_default_config() -> None:
    db = SignalDatabase(":memory:")
    sent_to: list[str] = []
    cfg = BotConfig(telegram_default_chat_id="111111, 222222")
    bot = TelegramBotService(
        db=db,
        config=cfg,
        send_callback=lambda chat_id, text: (sent_to.append(chat_id) or True),
    )
    subs = db.get_active_subscribers()
    assert "111111" in subs
    assert "222222" in subs

    count = bot.broadcast("Test xabar ikkita chatga")
    assert count == 2
    assert sent_to == ["111111", "222222"]
    db.close()
