"""
Xavfsiz Webhook Server va Kiruvchi Ma'lumotlar Tekshiruvchisi:
- Autentifikatsiya: constant-time Secret Token yoki HMAC-SHA256 imzo tekshiruvi.
- Deduplikatsiya: SQLite `webhook_events` va `signals` orqali takroriy webhooklarni filtrlash.
- Validatsiya: XAUUSD symbol, BUY/SELL side, musbat narx/ATR, va faqat yopilgan sham (`is_closed=True`).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Optional

from src.config import BotConfig
from src.database import SignalDatabase
from src.models import Signal
from src.strategy import build_signal_key, calculate_levels

logger = logging.getLogger(__name__)

MAX_WEBHOOK_BODY_BYTES = 16_384
ALLOWED_GOLD_SYMBOLS = {"XAUUSD", "XAU/USD", "GOLD", "GC=F", "OANDA:XAUUSD", "FX:XAUUSD", "PAXGUSDT"}


class WebhookValidationError(Exception):
    """Raised when an incoming webhook payload fails validation or authentication."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class WebhookProcessor:
    """
    Validates, authenticates, deduplicates, and processes TradingView / external
    webhook payloads into demo `Signal` records.
    """

    def __init__(
        self,
        db: SignalDatabase,
        config: Optional[BotConfig] = None,
        on_new_signal: Optional[Callable[[Signal], None]] = None,
        risk_usd_provider: Optional[Callable[[], float]] = None,
    ) -> None:
        self.db = db
        self.config = config or BotConfig()
        self.on_new_signal = on_new_signal
        self.risk_usd_provider = risk_usd_provider

    def verify_authentication(
        self,
        raw_body: bytes,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> None:
        """
        Verify webhook secret via:
        1. `X-Webhook-Signature` (HMAC-SHA256 hex digest of raw_body), OR
        2. `X-Webhook-Token` header, OR
        3. `secret` / `token` field inside JSON payload (used by TradingView alerts).
        """
        expected_secret = self.config.webhook_secret_token
        if not self.config.has_valid_webhook_secret():
            raise WebhookValidationError(
                503,
                "Webhook secret token sozlanmagan (.env da WEBHOOK_SECRET_TOKEN kiriting).",
            )

        norm_headers = {k.lower(): v.strip() for k, v in (headers or {}).items()}
        sig_header = norm_headers.get("x-webhook-signature", "")
        if sig_header:
            if sig_header.startswith("sha256="):
                sig_header = sig_header.split("=", 1)[1]
            expected_hmac = hmac.new(
                expected_secret.encode("utf-8"), raw_body, hashlib.sha256
            ).hexdigest()
            if hmac.compare_digest(sig_header.lower(), expected_hmac.lower()):
                return
            raise WebhookValidationError(401, "Noto'g'ri HMAC imzo (Invalid signature).")

        provided_token = (
            norm_headers.get("x-webhook-token")
            or str(payload.get("secret") or payload.get("token") or "").strip()
        )
        if not provided_token or not hmac.compare_digest(provided_token, expected_secret):
            raise WebhookValidationError(401, "Webhook autentifikatsiya xatosi (Invalid token).")

    def process_request(
        self,
        raw_body: bytes,
        headers: Optional[dict[str, str]] = None,
        now_ts: Optional[int] = None,
    ) -> dict[str, Any]:
        """
        Validate, authenticate, deduplicate, and store an incoming webhook request.
        Returns a structured response dict or raises `WebhookValidationError`.
        """
        if not raw_body:
            raise WebhookValidationError(400, "Bo'sh webhook so'rovi.")
        if len(raw_body) > MAX_WEBHOOK_BODY_BYTES:
            raise WebhookValidationError(413, "Webhook hajmi ruxsat etilgan me'yordan katta.")

        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except Exception as exc:
            raise WebhookValidationError(400, f"Noto'g'ri JSON format: {exc}") from exc

        if not isinstance(payload, dict):
            raise WebhookValidationError(400, "JSON obyekt bo'lishi shart.")

        # 1. Authenticate
        self.verify_authentication(raw_body, headers or {}, payload)

        # 2. Check pause state
        if self.db.is_paused():
            return {
                "status": "ignored",
                "reason": "Bot monitoringi pauza (/pause) holatida.",
            }

        # 3. Validate symbol
        raw_symbol = str(payload.get("symbol", self.config.symbol)).strip().upper()
        if raw_symbol not in ALLOWED_GOLD_SYMBOLS:
            raise WebhookValidationError(
                422, f"Faqat XAUUSD (Gold) signallari qabul qilinadi (kelgan: {raw_symbol})."
            )

        # 4. Validate closed candle requirement
        is_closed = payload.get("is_closed", payload.get("bar_closed", True))
        if isinstance(is_closed, str):
            is_closed = is_closed.strip().lower() in {"true", "1", "yes"}
        if not bool(is_closed):
            raise WebhookValidationError(
                422,
                "Signal rad etildi: sham hali yopilmagan (is_closed=false). "
                "Faqat yopilgan shamda signal tasdiqlanadi.",
            )

        # 5. Validate side (BUY / SELL)
        side = str(payload.get("side") or payload.get("action") or "").strip().upper()
        if side not in {"BUY", "SELL"}:
            raise WebhookValidationError(422, "Signal yo'nalishi 'BUY' yoki 'SELL' bo'lishi shart.")

        # 6. Validate timestamp & replay age
        current_ts = int(now_ts if now_ts is not None else time.time())
        try:
            candle_ts = int(payload.get("candle_timestamp") or payload.get("timestamp") or current_ts)
            if candle_ts > 10_000_000_000:  # Milliseconds to seconds
                candle_ts = candle_ts // 1000
        except (TypeError, ValueError) as exc:
            raise WebhookValidationError(422, "Noto'g'ri timestamp qiymati.") from exc

        if self.config.webhook_max_age_seconds > 0 and "timestamp" in payload:
            age = abs(current_ts - candle_ts)
            if age > self.config.webhook_max_age_seconds:
                raise WebhookValidationError(
                    422,
                    f"Eski (replay) webhook rad etildi (yoshi: {age}s > {self.config.webhook_max_age_seconds}s).",
                )

        # 7. Validate numeric prices and indicators
        try:
            entry_price = float(payload.get("entry_price") or payload.get("close") or payload.get("price"))
            atr_val = float(payload.get("atr", 5.0))
            ema_fast = float(payload.get("ema_fast", payload.get("ema50", entry_price)))
            ema_slow = float(payload.get("ema_slow", payload.get("ema200", entry_price)))
            rsi_val = float(payload.get("rsi", 55.0 if side == "BUY" else 45.0))
        except (TypeError, ValueError) as exc:
            raise WebhookValidationError(422, "Narx yoki indikator qiymatlari son bo'lishi shart.") from exc

        if entry_price <= 0 or atr_val <= 0:
            raise WebhookValidationError(422, "Entry narxi va ATR musbat bo'lishi shart.")
        if not (0.0 <= rsi_val <= 100.0):
            raise WebhookValidationError(422, "RSI qiymati 0 va 100 oralig'ida bo'lishi shart.")

        timeframe = str(payload.get("timeframe", self.config.timeframe)).strip()
        signal_key = build_signal_key(self.config.symbol, timeframe, candle_ts, side)
        webhook_key = str(payload.get("webhook_id") or signal_key)
        payload_hash = hashlib.sha256(raw_body).hexdigest()

        # 8. Deduplicate webhook & signal
        if not self.db.register_webhook_event(webhook_key, payload_hash):
            return {
                "status": "duplicate",
                "signal_key": signal_key,
                "message": "Takroriy webhook e'tiborsiz qoldirildi (idempotent).",
            }

        levels = calculate_levels(
            side=side,
            entry_price=entry_price,
            atr_value=atr_val,
            atr_sl_multiplier=self.config.atr_sl_multiplier,
            tp1_rr=self.config.tp1_rr_ratio,
            tp2_rr=self.config.tp2_rr_ratio,
            tp3_rr=self.config.tp3_rr_ratio,
        )

        risk_usd = self.risk_usd_provider() if self.risk_usd_provider else 100.0
        signal = Signal(
            signal_key=signal_key,
            symbol=self.config.symbol,
            timeframe=timeframe,
            side=side,
            candle_timestamp=candle_ts,
            entry_price=levels["entry"],
            stop_loss=levels["sl"],
            tp1=levels["tp1"],
            tp2=levels["tp2"],
            tp3=levels["tp3"],
            sl_distance=levels["sl_distance"],
            rr_tp1=levels["rr_tp1"],
            rr_tp2=levels["rr_tp2"],
            rr_tp3=levels["rr_tp3"],
            ema_fast=round(ema_fast, 2),
            ema_slow=round(ema_slow, 2),
            rsi=round(rsi_val, 2),
            atr=round(atr_val, 2),
            reason=str(payload.get("reason", "TradingView Webhook closed-candle alert")),
            source="webhook",
            risk_usd=round(risk_usd, 2),
        )

        saved_id = self.db.save_signal(signal)
        if saved_id is None:
            return {
                "status": "duplicate",
                "signal_key": signal_key,
                "message": "Ushbu sham uchun signal allaqachon saqlangan.",
            }

        if self.on_new_signal is not None:
            self.on_new_signal(signal)

        return {
            "status": "accepted",
            "signal_id": saved_id,
            "signal_key": signal_key,
            "entry": signal.entry_price,
            "sl": signal.stop_loss,
            "tp1": signal.tp1,
            "tp2": signal.tp2,
            "tp3": signal.tp3,
        }


class WebhookHTTPServer:
    """Lightweight multithreaded HTTP server for `/health` and `/webhook/tradingview`."""

    def __init__(
        self,
        processor: WebhookProcessor,
        host: str = "0.0.0.0",
        port: int = 8000,
    ) -> None:
        self.processor = processor
        self.host = host
        self.port = port
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start_background(self) -> None:
        proc = self.processor

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                logger.debug("HTTP %s - " + format, self.address_string(), *args)

            def _send_json(self, status_code: int, data: dict[str, Any]) -> None:
                body = json.dumps(data, ensure_ascii=False).encode("utf-8")
                self.send_response(status_code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                if self.path in {"/", "/health", "/status"}:
                    stats = proc.db.get_stats_summary(proc.config.initial_virtual_balance)
                    self._send_json(
                        200,
                        {
                            "ok": True,
                            "service": "XAUUSD Telegram AI Signal Bot (Demo Only)",
                            "symbol": proc.config.symbol,
                            "timeframe": proc.config.timeframe,
                            "is_paused": proc.db.is_paused(),
                            "active_signals": stats["active_signals"],
                            "virtual_balance": stats["current_balance"],
                        },
                    )
                else:
                    self._send_json(404, {"ok": False, "error": "Topilmadi (Not Found)"})

            def do_POST(self) -> None:  # noqa: N802
                if self.path not in {"/webhook", "/webhook/tradingview"}:
                    self._send_json(404, {"ok": False, "error": "Webhook manzili topilmadi"})
                    return
                content_length = int(self.headers.get("Content-Length", "0") or "0")
                raw_body = self.rfile.read(min(content_length, MAX_WEBHOOK_BODY_BYTES + 1))
                headers_dict = {k: v for k, v in self.headers.items()}
                try:
                    result = proc.process_request(raw_body=raw_body, headers=headers_dict)
                    self._send_json(200, {"ok": True, **result})
                except WebhookValidationError as exc:
                    self._send_json(exc.status_code, {"ok": False, "error": exc.message})
                except Exception as exc:
                    logger.exception("Unhandled error in webhook POST")
                    self._send_json(500, {"ok": False, "error": f"Server xatosi: {exc}"})

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="WebhookHTTPServer",
            daemon=True,
        )
        self._thread.start()
        logger.info("Webhook va Health server ishga tushdi: http://%s:%d", self.host, self.port)

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
