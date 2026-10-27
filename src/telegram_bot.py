"""
Telegram Bot moduli:
Buyruqlar: /start, /status, /signals, /stats, /pause, /resume
Bildirishnomalar: Yangi BUY/SELL signali hamda TP1, TP2, TP3, SL urilishi.
"""

from __future__ import annotations

import html
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Optional

from src.config import BotConfig
from src.database import SignalDatabase
from src.models import DISCLAIMER_UZ, LevelHitEvent, Signal

logger = logging.getLogger(__name__)


def format_new_signal_message(signal: Signal) -> str:
    """Format a new closed-candle BUY/SELL signal message for Telegram."""
    emoji = "🟢" if signal.side.upper() == "BUY" else "🔴"
    direction_uz = "XARID (BUY)" if signal.side.upper() == "BUY" else "SOTUV (SELL)"
    lines = [
        f"{emoji} <b>YANGI DEMO SIGNAL: {signal.symbol} — {direction_uz}</b>",
        f"⏱ <b>Taymfreym:</b> {signal.timeframe} (Sham yopildi: {signal.candle_iso_time})",
        "━━━━━━━━━━━━━━━━━━━━",
        f"🎯 <b>Entry (Kirish):</b> <code>{signal.entry_price:.2f}</code>",
        f"🛑 <b>Stop-Loss (SL):</b> <code>{signal.stop_loss:.2f}</code> (ATR masofa: {signal.sl_distance:.2f})",
        f"✅ <b>TP1 (34%):</b> <code>{signal.tp1:.2f}</code> (R:R 1:{signal.rr_tp1:.1f})",
        f"✅ <b>TP2 (33%):</b> <code>{signal.tp2:.2f}</code> (R:R 1:{signal.rr_tp2:.1f})",
        f"🏆 <b>TP3 (33%):</b> <code>{signal.tp3:.2f}</code> (R:R 1:{signal.rr_tp3:.1f})",
        f"⚖️ <b>Risk/Reward:</b> {signal.rr_display}",
        "━━━━━━━━━━━━━━━━━━━━",
        "📊 <b>Indikator ko'rsatkichlari:</b>",
        f"• EMA 50: <code>{signal.ema_fast:.2f}</code> | EMA 200: <code>{signal.ema_slow:.2f}</code>",
        f"• RSI 14: <code>{signal.rsi:.2f}</code> | ATR 14: <code>{signal.atr:.2f}</code>",
        f"• Tasdiq: {html.escape(signal.reason)}",
        f"• Virtual xavf (Risk): <code>${signal.risk_usd:.2f}</code>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"<i>{DISCLAIMER_UZ}</i>",
    ]
    return "\n".join(lines)


def format_level_hit_message(event: LevelHitEvent) -> str:
    """Format a TP1, TP2, TP3, or SL hit notification message for Telegram."""
    level = event.level.upper()
    if level == "TP1":
        header = "🎯 <b>TP1 DARAJASIGA YETDI (1/3 qism yopildi)</b>"
    elif level == "TP2":
        header = "🎯🎯 <b>TP2 DARAJASIGA YETDI (2/3 qism yopildi)</b>"
    elif level == "TP3":
        header = "🏆 <b>TP3 DARAJASIGA YETDI (Signal to'liq foyda bilan yopildi!)</b>"
    else:
        header = "🛑 <b>STOP-LOSS (SL) DARAJASIGA YETDI (Signal yopildi)</b>"

    lines = [
        header,
        f"📌 <b>Signal:</b> {event.symbol} ({event.side}) | Kalit: <code>{event.signal_key}</code>",
        f"💵 <b>Daraja narxi:</b> <code>{event.level_price:.2f}</code>",
        f"📈 <b>Bosqich natijasi:</b> <code>${event.tranche_pnl_usd:+.2f}</code> ({event.tranche_r:+.2f}R)",
        f"💰 <b>Signal jami PnL:</b> <code>${event.total_realized_pnl_usd:+.2f}</code> ({event.total_realized_r:+.2f}R)",
        f"🏦 <b>Virtual balans:</b> <code>${event.virtual_balance_after:,.2f}</code>",
        f"🔄 <b>Signal holati:</b> <code>{event.signal_status}</code>",
        "━━━━━━━━━━━━━━━━━━━━",
        f"<i>{DISCLAIMER_UZ}</i>",
    ]
    return "\n".join(lines)


class TelegramBotService:
    """
    Handles Telegram bot commands (/start, /status, /signals, /stats, /pause, /resume)
    and broadcasts signal / TP / SL notifications to subscribed chats.
    """

    def __init__(
        self,
        db: SignalDatabase,
        config: Optional[BotConfig] = None,
        send_callback: Optional[Callable[[str, str], bool]] = None,
        scan_callback: Optional[Callable[[], str]] = None,
    ) -> None:
        self.db = db
        self.config = config or BotConfig()
        self._send_callback = send_callback
        self._scan_callback = scan_callback
        self._last_update_id: int = 0
        self._webhook_cleared: bool = False

        for cid in self.config.get_default_chat_ids():
            self.db.add_subscriber(
                cid,
                username="default",
                first_name="DefaultChat",
            )

    # ------------------------------------------------------------------
    # Command Handlers
    # ------------------------------------------------------------------
    def handle_command(
        self,
        command_text: str,
        chat_id: str,
        username: str = "",
        first_name: str = "",
    ) -> str:
        """Process an incoming Telegram command and return the response text."""
        cmd_raw = (command_text or "").strip().split()[0].lower()
        # Strip @bot_username suffix if present (e.g. /start@my_xauusd_bot)
        cmd = cmd_raw.split("@")[0]

        if cmd == "/start":
            self.db.add_subscriber(chat_id=chat_id, username=username, first_name=first_name)
            return self._cmd_start(first_name)
        if cmd == "/status":
            return self._cmd_status()
        if cmd == "/scan":
            return self._cmd_scan()
        if cmd == "/signals":
            return self._cmd_signals()
        if cmd == "/stats":
            return self._cmd_stats()
        if cmd == "/pause":
            return self._cmd_pause()
        if cmd == "/resume":
            return self._cmd_resume()

        return (
            "🤖 Noma'lum buyruq. Mavjud buyruqlar:\n"
            "/start — Botni boshlash\n"
            "/scan — Hozirgi jonli bozor va indikatorlar tahlili\n"
            "/status — Faol signallar va monitoring holati\n"
            "/signals — Signal tarixi\n"
            "/stats — Demo savdo natijalari\n"
            "/pause — Signal monitoringini to'xtatish\n"
            "/resume — Signal monitoringini davom ettirish"
        )

    def _cmd_scan(self) -> str:
        if self._scan_callback is not None:
            try:
                return self._scan_callback()
            except Exception as exc:
                return f"⚠️ Jonli tahlilda xatolik: {exc}"
        return self._cmd_status()

    def _cmd_start(self, first_name: str = "") -> str:
        greeting = f"Assalomu alaykum, {first_name}!" if first_name else "Assalomu alaykum!"
        paused_str = "⏸ PAUZA" if self.db.is_paused() else "▶️ FAOL"
        balance = self.db.get_virtual_balance(self.config.initial_virtual_balance)
        active = self.db.get_active_signals()
        active_block = ""
        if active:
            s = active[-1]
            active_block = (
                "\n🔥 <b>HOZIRGI FAOL SIGNAL:</b>\n"
                f"• <b>{s.symbol} {s.side}</b> ({s.candle_iso_time})\n"
                f"• Entry: <code>{s.entry_price:.2f}</code> | SL: <code>{s.stop_loss:.2f}</code>\n"
                f"• TP1: <code>{s.tp1:.2f}</code> | TP2: <code>{s.tp2:.2f}</code> | TP3: <code>{s.tp3:.2f}</code>\n"
            )
        return (
            f"👋 <b>{greeting} XAUUSD (Gold) AI Signal Botga xush kelibsiz!</b>\n\n"
            f"📌 <b>Bozor:</b> {self.config.symbol} ({self.config.timeframe})\n"
            f"📐 <b>Algoritm:</b> EMA 50/200 (Trend) + RSI 14 (Momentum) + ATR 14 (SL/TP)\n"
            f"🕯 <b>Tasdiqlash:</b> Faqat sham yopilgandan keyin (Closed Candle)\n"
            f"💼 <b>Rejim:</b> Faqat Demo / Paper Trading (Virtual balans: <code>${balance:,.2f}</code>)\n"
            f"📡 <b>Monitoring holati:</b> {paused_str}\n"
            f"{active_block}\n"
            "📋 <b>Buyruqlar ro'yxati:</b>\n"
            "• /scan — Jonli XAUUSD narxi va indikatorlar tahlili\n"
            "• /status — Faol signallar va bot holati\n"
            "• /signals — Oxirgi signallar tarixi\n"
            "• /stats — Demo savdo statistikasi\n"
            "• /pause — Monitoringni vaqtincha to'xtatish\n"
            "• /resume — Monitoringni qayta yoqish\n\n"
            f"<i>{DISCLAIMER_UZ}</i>"
        )

    def _cmd_status(self) -> str:
        is_paused = self.db.is_paused()
        state_badge = "⏸ TO'XTATILGAN (PAUSED)" if is_paused else "🟢 FAOL (ACTIVE)"
        balance = self.db.get_virtual_balance(self.config.initial_virtual_balance)
        last_check = self.db.get_state("last_check_time", "Hali tekshirilmagan")
        data_source = self.db.get_state("last_data_source", self.config.market_data_source)
        active_signals = self.db.get_active_signals()

        lines = [
            "📡 <b>XAUUSD BOT JORIY HOLATI (/status)</b>",
            f"• <b>Monitoring:</b> {state_badge}",
            f"• <b>Bozor / Taymfreym:</b> {self.config.symbol} ({self.config.timeframe})",
            f"• <b>Ma'lumot manbasi:</b> {data_source}",
            f"• <b>Oxirgi tekshiruv:</b> {last_check}",
            f"• <b>Virtual balans:</b> <code>${balance:,.2f}</code>",
            f"• <b>Faol signallar soni:</b> {len(active_signals)} ta",
            "━━━━━━━━━━━━━━━━━━━━",
        ]

        if not active_signals:
            lines.append("ℹ️ Hozirda ochiq (ACTIVE) signallar yo'q.")
        else:
            for i, s in enumerate(active_signals, 1):
                tp_progress = (
                    f"TP1:{'✅' if s.tp1_hit else '⏳'} "
                    f"TP2:{'✅' if s.tp2_hit else '⏳'} "
                    f"TP3:{'✅' if s.tp3_hit else '⏳'}"
                )
                lines.append(
                    f"<b>{i}. {s.symbol} {s.side}</b> ({s.candle_iso_time})\n"
                    f"   Entry: <code>{s.entry_price:.2f}</code> | SL: <code>{s.stop_loss:.2f}</code>\n"
                    f"   TP1: <code>{s.tp1:.2f}</code> | TP2: <code>{s.tp2:.2f}</code> | TP3: <code>{s.tp3:.2f}</code>\n"
                    f"   Holat: {tp_progress} | Joriy PnL: <code>${s.realized_pnl_usd:+.2f}</code>"
                )

        lines.append(f"\n<i>{DISCLAIMER_UZ}</i>")
        return "\n".join(lines)

    def _cmd_signals(self, limit: int = 8) -> str:
        recent = self.db.get_recent_signals(limit=limit)
        if not recent:
            return (
                "📜 <b>SIGNAL TARIXI (/signals)</b>\n\n"
                "Hozircha bazada signallar mavjud emas.\n\n"
                f"<i>{DISCLAIMER_UZ}</i>"
            )

        lines = [f"📜 <b>OXIRGI {len(recent)} TA SIGNAL TARIXI (/signals)</b>", "━━━━━━━━━━━━━━━━━━━━"]
        for i, s in enumerate(recent, 1):
            icon = "🟢" if s.side == "BUY" else "🔴"
            lines.append(
                f"<b>{i}. {icon} {s.symbol} {s.side}</b> [{s.status}]\n"
                f"   Vaqt: {s.candle_iso_time}\n"
                f"   Entry: <code>{s.entry_price:.2f}</code> | SL: <code>{s.stop_loss:.2f}</code>\n"
                f"   TP1: <code>{s.tp1:.2f}</code> | TP2: <code>{s.tp2:.2f}</code> | TP3: <code>{s.tp3:.2f}</code>\n"
                f"   Natija: <code>${s.realized_pnl_usd:+.2f}</code> ({s.realized_r:+.2f}R)"
            )
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"<i>{DISCLAIMER_UZ}</i>")
        return "\n".join(lines)

    def _cmd_stats(self) -> str:
        st = self.db.get_stats_summary(self.config.initial_virtual_balance)
        lines = [
            "📊 <b>DEMO / PAPER TRADING STATISTIKASI (/stats)</b>",
            "━━━━━━━━━━━━━━━━━━━━",
            f"🏦 <b>Boshlang'ich balans:</b> <code>${st['initial_balance']:,.2f}</code>",
            f"💰 <b>Joriy virtual balans:</b> <code>${st['current_balance']:,.2f}</code>",
            f"📈 <b>Sof virtual PnL:</b> <code>${st['total_pnl_usd']:+,.2f}</code> ({st['return_pct']:+.2f}%)",
            f"⚖️ <b>Jami R-multiple:</b> <code>{st['total_r']:+.2f}R</code>",
            "━━━━━━━━━━━━━━━━━━━━",
            f"🔢 <b>Jami signallar:</b> {st['total_signals']} ta (Faol: {st['active_signals']}, Yopilgan: {st['closed_signals']})",
            f"🏆 <b>Yutuqli / Zararli:</b> {st['wins']} / {st['losses']} (Win Rate: <code>{st['win_rate_pct']:.2f}%</code>)",
            f"✅ <b>TP1 urilgan:</b> {st['tp1_hits']} marta",
            f"✅ <b>TP2 urilgan:</b> {st['tp2_hits']} marta",
            f"🏆 <b>TP3 urilgan:</b> {st['tp3_hits']} marta",
            f"🛑 <b>SL urilgan:</b> {st['sl_hits']} marta",
            "━━━━━━━━━━━━━━━━━━━━",
            f"<i>{DISCLAIMER_UZ}</i>",
        ]
        return "\n".join(lines)

    def _cmd_pause(self) -> str:
        self.db.set_paused(True)
        return (
            "⏸ <b>Signal monitoringi vaqtincha to'xtatildi (/pause).</b>\n"
            "Yangi signallar qidirish pauza holatida. Qayta yoqish uchun /resume buyrug'ini yuboring."
        )

    def _cmd_resume(self) -> str:
        self.db.set_paused(False)
        return (
            "▶️ <b>Signal monitoringi qayta faollashtirildi (/resume).</b>\n"
            "Bot XAUUSD yopilgan shamlarini kuzatishda davom etmoqda."
        )

    # ------------------------------------------------------------------
    # Outgoing Notifications & Telegram HTTP API
    # ------------------------------------------------------------------
    def send_message_to_chat(self, chat_id: str, text: str) -> bool:
        """Send an HTML-formatted message to a single Telegram chat with plain-text fallback."""
        if self._send_callback is not None:
            return self._send_callback(str(chat_id), text)

        if not self.config.has_valid_telegram_token():
            logger.info("[TELEGRAM DRY-RUN -> chat=%s]:\n%s", chat_id, text)
            return False

        url = f"https://api.telegram.org/bot{self.config.telegram_bot_token}/sendMessage"
        payload = json.dumps(
            {
                "chat_id": str(chat_id),
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
        ).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10.0) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                return bool(body.get("ok"))
        except Exception as exc:
            logger.warning("HTML sendMessage failed for %s (%s), retrying plain text", chat_id, exc)
            plain_text = html.unescape(re.sub(r"<[^>]+>", "", text))
            fallback_payload = json.dumps(
                {
                    "chat_id": str(chat_id),
                    "text": plain_text,
                    "disable_web_page_preview": True,
                }
            ).encode("utf-8")
            fallback_req = urllib.request.Request(
                url,
                data=fallback_payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(fallback_req, timeout=10.0) as resp2:
                    body2 = json.loads(resp2.read().decode("utf-8"))
                    return bool(body2.get("ok"))
            except Exception as exc2:
                logger.error("Failed to send Telegram message to %s: %s", chat_id, exc2)
                return False

    def _ensure_polling_ready(self) -> None:
        """Clear any stale Telegram webhook and register bot menu commands once."""
        if self._webhook_cleared or not self.config.has_valid_telegram_token():
            return
        self._webhook_cleared = True
        base = f"https://api.telegram.org/bot{self.config.telegram_bot_token}"
        try:
            urllib.request.urlopen(f"{base}/deleteWebhook?drop_pending_updates=false", timeout=6.0)
        except Exception as exc:
            logger.debug("deleteWebhook notice: %s", exc)
        try:
            cmds = [
                {"command": "start", "description": "Botni boshlash va obuna bo'lish"},
                {"command": "scan", "description": "Jonli XAUUSD narxi va indikator tahlili"},
                {"command": "status", "description": "Faol signallar va bot holati"},
                {"command": "signals", "description": "Oxirgi signallar tarixi"},
                {"command": "stats", "description": "Demo savdo statistikasi"},
                {"command": "pause", "description": "Monitoringni vaqtincha to'xtatish"},
                {"command": "resume", "description": "Monitoringni qayta yoqish"},
            ]
            req = urllib.request.Request(
                f"{base}/setMyCommands",
                data=json.dumps({"commands": cmds}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=6.0)
        except Exception as exc:
            logger.debug("setMyCommands notice: %s", exc)

    def broadcast(self, text: str) -> int:
        """Broadcast a message to all active subscribers."""
        subscribers = self.db.get_active_subscribers()
        if not subscribers:
            subscribers = self.config.get_default_chat_ids()

        sent_count = 0
        for chat_id in subscribers:
            if self.send_message_to_chat(chat_id, text):
                sent_count += 1
        return sent_count

    def notify_new_signal(self, signal: Signal) -> int:
        """Format and broadcast a newly confirmed BUY/SELL signal."""
        msg = format_new_signal_message(signal)
        return self.broadcast(msg)

    def notify_level_hit(self, event: LevelHitEvent) -> int:
        """Format and broadcast a TP1, TP2, TP3, or SL hit event."""
        msg = format_level_hit_message(event)
        return self.broadcast(msg)

    def poll_updates_once(self, timeout: int = 1) -> int:
        """
        Poll Telegram `getUpdates` once and respond to any user commands.
        Returns the number of processed command updates.
        """
        if not self.config.has_valid_telegram_token():
            return 0
        self._ensure_polling_ready()

        params = urllib.parse.urlencode(
            {
                "offset": self._last_update_id + 1,
                "timeout": timeout,
                "allowed_updates": json.dumps(["message"]),
            }
        )
        url = f"https://api.telegram.org/bot{self.config.telegram_bot_token}/getUpdates?{params}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "XAUUSDSignalBot/1.0"})
            with urllib.request.urlopen(req, timeout=timeout + 5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            logger.debug("Telegram getUpdates polling error: %s", exc)
            return 0

        if not data.get("ok"):
            return 0

        processed = 0
        for upd in data.get("result", []):
            upd_id = int(upd.get("update_id", 0))
            if upd_id > self._last_update_id:
                self._last_update_id = upd_id

            msg = upd.get("message") or {}
            text = (msg.get("text") or "").strip()
            if not text.startswith("/"):
                continue

            chat = msg.get("chat") or {}
            chat_id = str(chat.get("id", ""))
            if not chat_id:
                continue

            user = msg.get("from") or {}
            reply = self.handle_command(
                command_text=text,
                chat_id=chat_id,
                username=str(user.get("username", "")),
                first_name=str(user.get("first_name", "")),
            )
            self.send_message_to_chat(chat_id, reply)
            if text.split()[0].split("@")[0].lower() == "/start":
                active_sigs = self.db.get_active_signals()
                if active_sigs:
                    self.send_message_to_chat(chat_id, format_new_signal_message(active_sigs[-1]))
            processed += 1

        return processed
