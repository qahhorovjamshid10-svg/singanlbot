"""Ma'lumot modellari: Candle, Signal, LevelHitEvent va BacktestMetrics."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


DISCLAIMER_UZ = (
    "⚠️ Eslatma: Ushbu signal faqat ta'lim va demo/paper-trading maqsadida "
    "avtomatik algoritm tomonidan yaratildi. Hech qanday foyda kafolati berilmaydi "
    "va bu moliyaviy maslahat emas. Real pul bilan savdo qilmang."
)


@dataclass(frozen=True)
class Candle:
    """Bitta OHLCV sham ma'lumoti."""

    timestamp: int  # UTC epoch seconds (candle open time)
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    is_closed: bool = True

    @property
    def iso_time(self) -> str:
        return datetime.fromtimestamp(self.timestamp, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC"
        )


@dataclass(frozen=True)
class IndicatorSnapshot:
    """Yopilgan shamdagi indikator qiymatlari."""

    ema_fast: float
    ema_slow: float
    rsi: float
    atr: float
    prev_ema_fast: Optional[float] = None
    prev_ema_slow: Optional[float] = None
    prev_rsi: Optional[float] = None


@dataclass
class Signal:
    """XAUUSD BUY yoki SELL savdo signali (faqat Demo / Paper Trading)."""

    signal_key: str  # Unique key: e.g. XAUUSD:15m:1793091600:BUY
    symbol: str
    timeframe: str
    side: str  # "BUY" or "SELL"
    candle_timestamp: int
    entry_price: float
    stop_loss: float
    tp1: float
    tp2: float
    tp3: float
    sl_distance: float
    rr_tp1: float
    rr_tp2: float
    rr_tp3: float
    ema_fast: float
    ema_slow: float
    rsi: float
    atr: float
    reason: str
    source: str = "polling"  # "polling", "webhook", "backtest"
    status: str = "ACTIVE"  # "ACTIVE", "CLOSED_TP3", "CLOSED_SL"
    tp1_hit: bool = False
    tp2_hit: bool = False
    tp3_hit: bool = False
    sl_hit: bool = False
    realized_r: float = 0.0
    realized_pnl_usd: float = 0.0
    risk_usd: float = 100.0
    remaining_weight: float = 1.0
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    )
    closed_at: Optional[str] = None
    id: Optional[int] = None

    @property
    def rr_display(self) -> str:
        return f"1:{self.rr_tp1:.1f} (TP1) | 1:{self.rr_tp2:.1f} (TP2) | 1:{self.rr_tp3:.1f} (TP3)"

    @property
    def candle_iso_time(self) -> str:
        return datetime.fromtimestamp(self.candle_timestamp, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC"
        )


@dataclass(frozen=True)
class LevelHitEvent:
    """TP1, TP2, TP3 yoki SL darajasi urilganda hosil bo'ladigan hodisa."""

    signal_id: Optional[int]
    signal_key: str
    symbol: str
    side: str
    level: str  # "TP1", "TP2", "TP3", "SL"
    level_price: float
    trigger_price: float
    candle_timestamp: int
    tranche_r: float
    tranche_pnl_usd: float
    total_realized_r: float
    total_realized_pnl_usd: float
    signal_status: str
    virtual_balance_after: float


@dataclass
class BacktestMetrics:
    """Tarixiy ma'lumotlarda backtest natijalari."""

    symbol: str
    timeframe: str
    data_source: str
    total_candles: int
    start_time: str
    end_time: str
    initial_balance: float
    final_balance: float
    net_profit_usd: float
    net_return_pct: float
    total_signals: int
    closed_signals: int
    active_signals: int
    winning_signals: int
    losing_signals: int
    win_rate_pct: float
    tp1_hits: int
    tp2_hits: int
    tp3_hits: int
    sl_hits: int
    total_r: float
    profit_factor: float
    max_drawdown_pct: float
    signals: list[Signal] = field(default_factory=list)
    events: list[LevelHitEvent] = field(default_factory=list)
    disclaimer: str = DISCLAIMER_UZ
