"""
XAUUSD (Gold) ochiq bozor ma'lumotlari provayderi.

TradingView Free (Basic) tarifida Webhook Alerts funksiyasi mavjud emasligi sababli
(Webhook faqat pullik Essential/Plus/Premium tariflarda ishlaydi), ushbu modul
100% bepul va qonuniy ochiq bozor manbalaridan haqiqiy XAUUSD ma'lumotlarini oladi:

1. Yahoo Finance (`GC=F` — COMEX Gold Futures OHLCV shamlari, API kalitsiz).
2. Swissquote Bank Public Feed (`XAU/USD` — real-vaqt Spot XAU/USD bid/ask kotirovkasi, API kalitsiz).
3. TwelveData API (`XAU/USD` — ixtiyoriy bepul API kalit kiritilganda to'g'ridan-to'g'ri Spot XAU/USD shamlari).
4. Binance Public API (`PAXGUSDT` — 1:1 fizik oltin bilan ta'minlangan PAX Gold, zaxira sifatida).
5. Lokal CSV (`data/xauusd_historical_sample.csv` — oflayn backtest va testlar uchun).
"""

from __future__ import annotations

import csv
import json
import logging
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.models import Candle

logger = logging.getLogger(__name__)

TIMEFRAME_SECONDS: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}


def timeframe_to_seconds(timeframe: str) -> int:
    return TIMEFRAME_SECONDS.get(timeframe.lower(), 900)


def fetch_swissquote_spot_xauusd(timeout: float = 8.0) -> Optional[dict[str, float]]:
    """
    Fetch real-time institutional Spot XAU/USD bid/ask/mid price from Swissquote Bank.
    Returns {'bid': float, 'ask': float, 'mid': float} or None on error.
    """
    url = "https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/XAU/USD"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        if isinstance(payload, list) and payload:
            profiles = payload[0].get("spreadProfilePrices", [])
            if profiles:
                bid = float(profiles[0]["bid"])
                ask = float(profiles[0]["ask"])
                return {
                    "bid": round(bid, 2),
                    "ask": round(ask, 2),
                    "mid": round((bid + ask) / 2.0, 2),
                }
    except Exception as exc:
        logger.debug("Swissquote XAU/USD quote fetch failed: %s", exc)
    return None


def fetch_yahoo_gold_candles(
    timeframe: str = "15m",
    range_str: str = "10d",
    now_ts: Optional[int] = None,
    timeout: float = 10.0,
) -> list[Candle]:
    """
    Fetch real Gold (`GC=F`) OHLCV candles from Yahoo Finance v8 chart endpoint.
    Automatically marks forming/incomplete candles with `is_closed=False`.
    """
    tf_sec = timeframe_to_seconds(timeframe)
    yf_interval = timeframe.lower()
    if yf_interval == "4h":
        yf_interval = "1h"

    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/GC=F"
        f"?interval={urllib.parse.quote(yf_interval)}&range={urllib.parse.quote(range_str)}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    result = data["chart"]["result"][0]
    timestamps = result.get("timestamp", [])
    quote = result["indicators"]["quote"][0]
    opens = quote.get("open", [])
    highs = quote.get("high", [])
    lows = quote.get("low", [])
    closes = quote.get("close", [])
    volumes = quote.get("volume", [])

    current_ts = int(now_ts if now_ts is not None else time.time())
    candles_by_ts: dict[int, Candle] = {}

    for i, ts in enumerate(timestamps):
        o = opens[i] if i < len(opens) else None
        h = highs[i] if i < len(highs) else None
        l = lows[i] if i < len(lows) else None
        c = closes[i] if i < len(closes) else None
        v = volumes[i] if i < len(volumes) and volumes[i] is not None else 0.0

        if o is None or h is None or l is None or c is None:
            continue

        ts_int = int(ts)
        # Yahoo appends a real-time tick at the end whose timestamp is not aligned to interval
        aligned_ts = (ts_int // tf_sec) * tf_sec
        is_closed = (ts_int % tf_sec == 0) and ((aligned_ts + tf_sec) <= current_ts)

        if aligned_ts in candles_by_ts and not is_closed:
            # Merge live tick into the currently forming candle
            prev_c = candles_by_ts[aligned_ts]
            candles_by_ts[aligned_ts] = Candle(
                timestamp=aligned_ts,
                open=prev_c.open,
                high=round(max(prev_c.high, float(h)), 2),
                low=round(min(prev_c.low, float(l)), 2),
                close=round(float(c), 2),
                volume=float(prev_c.volume + float(v)),
                is_closed=False,
            )
        else:
            candles_by_ts[aligned_ts] = Candle(
                timestamp=aligned_ts,
                open=round(float(o), 2),
                high=round(float(h), 2),
                low=round(float(l), 2),
                close=round(float(c), 2),
                volume=float(v),
                is_closed=is_closed,
            )

    return [candles_by_ts[k] for k in sorted(candles_by_ts.keys())]


def fetch_twelvedata_xauusd_candles(
    api_key: str,
    timeframe: str = "15m",
    outputsize: int = 300,
    now_ts: Optional[int] = None,
    timeout: float = 10.0,
) -> list[Candle]:
    """Fetch Spot XAU/USD candles from TwelveData API when a free API key is provided."""
    if not api_key:
        raise ValueError("TwelveData API key is required")

    tf_map = {
        "1m": "1min",
        "5m": "5min",
        "15m": "15min",
        "30m": "30min",
        "1h": "1h",
        "4h": "4h",
        "1d": "1day",
    }
    interval = tf_map.get(timeframe.lower(), "15min")
    tf_sec = timeframe_to_seconds(timeframe)
    url = (
        f"https://api.twelvedata.com/time_series?symbol=XAU/USD"
        f"&interval={interval}&outputsize={outputsize}&timezone=UTC&apikey={urllib.parse.quote(api_key)}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    values = data.get("values")
    if not values:
        raise RuntimeError(f"TwelveData error: {data.get('message', 'no values')}")

    current_ts = int(now_ts if now_ts is not None else time.time())
    candles: list[Candle] = []
    for item in reversed(values):
        dt = datetime.strptime(item["datetime"], "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        )
        ts_int = int(dt.timestamp())
        is_closed = (ts_int + tf_sec) <= current_ts
        candles.append(
            Candle(
                timestamp=ts_int,
                open=round(float(item["open"]), 2),
                high=round(float(item["high"]), 2),
                low=round(float(item["low"]), 2),
                close=round(float(item["close"]), 2),
                volume=0.0,
                is_closed=is_closed,
            )
        )
    return candles


def fetch_binance_paxg_candles(
    timeframe: str = "15m",
    limit: int = 500,
    now_ts: Optional[int] = None,
    timeout: float = 10.0,
) -> list[Candle]:
    """
    Fetch PAX Gold (`PAXGUSDT`, 1:1 physical gold troy ounce) OHLCV klines from Binance public API.
    Used as a high-availability fallback.
    """
    tf_sec = timeframe_to_seconds(timeframe)
    url = (
        f"https://api.binance.com/api/v3/klines"
        f"?symbol=PAXGUSDT&interval={urllib.parse.quote(timeframe.lower())}&limit={int(limit)}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        rows = json.loads(resp.read().decode("utf-8"))

    current_ts = int(now_ts if now_ts is not None else time.time())
    candles: list[Candle] = []
    for row in rows:
        open_ts = int(row[0]) // 1000
        close_ts = int(row[6]) // 1000
        is_closed = close_ts <= current_ts and (open_ts + tf_sec) <= current_ts
        candles.append(
            Candle(
                timestamp=open_ts,
                open=round(float(row[1]), 2),
                high=round(float(row[2]), 2),
                low=round(float(row[3]), 2),
                close=round(float(row[4]), 2),
                volume=round(float(row[5]), 4),
                is_closed=is_closed,
            )
        )
    return candles


def save_candles_to_csv(candles: list[Candle], csv_path: str | Path) -> None:
    """Save candles to CSV for offline backtesting and auditability."""
    path = Path(csv_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume", "is_closed"])
        for c in candles:
            writer.writerow(
                [
                    c.timestamp,
                    f"{c.open:.2f}",
                    f"{c.high:.2f}",
                    f"{c.low:.2f}",
                    f"{c.close:.2f}",
                    f"{c.volume:.4f}",
                    int(c.is_closed),
                ]
            )


def load_candles_from_csv(csv_path: str | Path) -> list[Candle]:
    """Load historical XAUUSD candles from a local CSV file."""
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"CSV file not found: {path}")
    candles: list[Candle] = []
    with path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            candles.append(
                Candle(
                    timestamp=int(row["timestamp"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("volume", 0.0) or 0.0),
                    is_closed=bool(int(row.get("is_closed", 1))),
                )
            )
    return candles


class MarketDataProvider:
    """
    Unified market data provider for XAUUSD with automatic fallback and source tracking.
    """

    def __init__(
        self,
        source: str = "auto",
        timeframe: str = "15m",
        twelvedata_api_key: str = "",
        csv_fallback_path: str = "data/xauusd_historical_sample.csv",
    ) -> None:
        self.source = source.lower()
        self.timeframe = timeframe
        self.twelvedata_api_key = twelvedata_api_key
        self.csv_fallback_path = csv_fallback_path
        self.last_used_source: str = "unknown"
        self.last_spot_quote: Optional[dict[str, float]] = None

    def fetch_candles(self, range_str: str = "10d", limit: int = 500) -> list[Candle]:
        """Fetch XAUUSD OHLCV candles from configured source or automatic fallback chain."""
        # Refresh Swissquote live spot quote in background/best-effort for reference
        if self.source in {"auto", "yahoo", "twelvedata"}:
            self.last_spot_quote = fetch_swissquote_spot_xauusd(timeout=4.0)

        if self.source == "csv":
            self.last_used_source = f"csv ({self.csv_fallback_path})"
            return load_candles_from_csv(self.csv_fallback_path)

        if self.source in {"twelvedata", "auto"} and self.twelvedata_api_key:
            try:
                candles = fetch_twelvedata_xauusd_candles(
                    api_key=self.twelvedata_api_key,
                    timeframe=self.timeframe,
                    outputsize=limit,
                )
                if len(candles) >= 210:
                    self.last_used_source = "TwelveData (XAU/USD Spot)"
                    return candles
            except Exception as exc:
                logger.warning("TwelveData fetch failed, falling back: %s", exc)
                if self.source == "twelvedata":
                    raise

        if self.source in {"yahoo", "auto"}:
            try:
                candles = fetch_yahoo_gold_candles(
                    timeframe=self.timeframe, range_str=range_str
                )
                if len(candles) >= 210:
                    self.last_used_source = "Yahoo Finance (GC=F COMEX Gold)"
                    return candles
            except Exception as exc:
                logger.warning("Yahoo Finance GC=F fetch failed, falling back: %s", exc)
                if self.source == "yahoo":
                    raise

        if self.source in {"binance_paxg", "auto"}:
            try:
                candles = fetch_binance_paxg_candles(
                    timeframe=self.timeframe, limit=limit
                )
                if len(candles) >= 210:
                    self.last_used_source = "Binance Public API (PAXGUSDT 1:1 Gold)"
                    return candles
            except Exception as exc:
                logger.warning("Binance PAXGUSDT fetch failed: %s", exc)
                if self.source == "binance_paxg":
                    raise

        if Path(self.csv_fallback_path).exists():
            self.last_used_source = f"Local CSV Fallback ({self.csv_fallback_path})"
            return load_candles_from_csv(self.csv_fallback_path)

        raise RuntimeError("Barcha XAUUSD ma'lumot manbalariga ulanishda xatolik yuz berdi.")
