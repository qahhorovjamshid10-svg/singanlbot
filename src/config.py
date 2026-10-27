"""Loyiha konfiguratsiyasi va .env muhit o'zgaruvchilarini xavfsiz yuklash moduli."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None


def _load_env_file(env_path: Optional[Path] = None) -> None:
    """Load variables from .env file safely without overwriting existing env vars."""
    path = env_path or Path(".env")
    if load_dotenv is not None and path.exists():
        load_dotenv(dotenv_path=path, override=False)
        return
    if path.exists():
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip("'").strip('"')
            if key and key not in os.environ:
                os.environ[key] = val


def _parse_bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class BotConfig:
    """XAUUSD Telegram AI Signal Bot konfiguratsiyasi (faqat Demo / Paper Trading)."""

    # Telegram
    telegram_bot_token: str = ""
    telegram_default_chat_id: str = ""

    # Bozor va algoritm
    symbol: str = "XAUUSD"
    timeframe: str = "15m"
    market_data_source: str = "auto"
    twelvedata_api_key: str = ""

    ema_fast_period: int = 50
    ema_slow_period: int = 200
    rsi_period: int = 14
    atr_period: int = 14

    atr_sl_multiplier: float = 1.5
    tp1_rr_ratio: float = 1.0
    tp2_rr_ratio: float = 2.0
    tp3_rr_ratio: float = 3.0
    signal_cooldown_candles: int = 3

    # Demo / Paper Trading (Real pul ishlatilmaydi, broker funksiyasi yo'q)
    paper_trading_only: bool = True
    initial_virtual_balance: float = 10000.0
    risk_per_trade_pct: float = 1.0
    move_sl_to_be_on_tp1: bool = False

    # Polling va Webhook
    poll_interval_seconds: int = 60
    webhook_enabled: bool = True
    webhook_host: str = "0.0.0.0"
    port: int = 8000
    webhook_secret_token: str = ""
    webhook_max_age_seconds: int = 300

    # Baza va loglar
    database_path: str = "data/xauusd_signals.db"
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, env_path: Optional[Path] = None) -> "BotConfig":
        """Load configuration from environment variables and optional .env file."""
        _load_env_file(env_path)
        return cls(
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
            telegram_default_chat_id=os.getenv("TELEGRAM_DEFAULT_CHAT_ID", "").strip(),
            symbol=os.getenv("SYMBOL", "XAUUSD").strip().upper(),
            timeframe=os.getenv("TIMEFRAME", "15m").strip(),
            market_data_source=os.getenv("MARKET_DATA_SOURCE", "auto").strip().lower(),
            twelvedata_api_key=os.getenv("TWELVEDATA_API_KEY", "").strip(),
            ema_fast_period=int(os.getenv("EMA_FAST_PERIOD", "50")),
            ema_slow_period=int(os.getenv("EMA_SLOW_PERIOD", "200")),
            rsi_period=int(os.getenv("RSI_PERIOD", "14")),
            atr_period=int(os.getenv("ATR_PERIOD", "14")),
            atr_sl_multiplier=float(os.getenv("ATR_SL_MULTIPLIER", "1.5")),
            tp1_rr_ratio=float(os.getenv("TP1_RR_RATIO", "1.0")),
            tp2_rr_ratio=float(os.getenv("TP2_RR_RATIO", "2.0")),
            tp3_rr_ratio=float(os.getenv("TP3_RR_RATIO", "3.0")),
            signal_cooldown_candles=int(os.getenv("SIGNAL_COOLDOWN_CANDLES", "3")),
            paper_trading_only=True,  # Qat'iy faqat demo/paper trading
            initial_virtual_balance=float(os.getenv("INITIAL_VIRTUAL_BALANCE", "10000.0")),
            risk_per_trade_pct=float(os.getenv("RISK_PER_TRADE_PCT", "1.0")),
            move_sl_to_be_on_tp1=_parse_bool(os.getenv("MOVE_SL_TO_BE_ON_TP1", "false")),
            poll_interval_seconds=int(os.getenv("POLL_INTERVAL_SECONDS", "60")),
            webhook_enabled=_parse_bool(os.getenv("WEBHOOK_ENABLED", "true"), True),
            webhook_host=os.getenv("WEBHOOK_HOST", "0.0.0.0").strip(),
            port=int(os.getenv("PORT", "8000")),
            webhook_secret_token=os.getenv("WEBHOOK_SECRET_TOKEN", "").strip(),
            webhook_max_age_seconds=int(os.getenv("WEBHOOK_MAX_AGE_SECONDS", "300")),
            database_path=os.getenv("DATABASE_PATH", "data/xauusd_signals.db").strip(),
            log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
        )

    def has_valid_telegram_token(self) -> bool:
        """Check whether a real BotFather token is configured."""
        token = self.telegram_bot_token
        if not token or token == "YOUR_BOTFATHER_TOKEN_HERE":
            return False
        parts = token.split(":")
        return len(parts) == 2 and parts[0].isdigit() and len(parts[1]) >= 20

    def has_valid_webhook_secret(self) -> bool:
        """Check whether a non-placeholder webhook secret token is configured."""
        secret = self.webhook_secret_token
        if not secret or secret == "CHANGE_ME_TO_A_STRONG_RANDOM_SECRET_32_CHARS":
            return False
        return len(secret) >= 12

    def masked_summary(self) -> dict:
        """Return safe dictionary without exposing secret tokens."""
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "market_data_source": self.market_data_source,
            "paper_trading_only": self.paper_trading_only,
            "initial_virtual_balance": self.initial_virtual_balance,
            "risk_per_trade_pct": self.risk_per_trade_pct,
            "telegram_configured": self.has_valid_telegram_token(),
            "webhook_secret_configured": self.has_valid_webhook_secret(),
            "database_path": self.database_path,
        }
