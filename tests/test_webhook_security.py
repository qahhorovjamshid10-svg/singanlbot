"""Webhook xavfsizlik, autentifikatsiya, replay himoyasi va deduplikatsiya testlari."""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from src.config import BotConfig
from src.database import SignalDatabase
import urllib.request
from src.webhook_server import WebhookHTTPServer, WebhookProcessor, WebhookValidationError


def test_webhook_auth_deduplication_and_validation() -> None:
    db = SignalDatabase(":memory:")
    secret = "SUPER_SECRET_WEBHOOK_KEY_1234567890"
    cfg = BotConfig(webhook_secret_token=secret, webhook_max_age_seconds=300)
    emitted = []
    proc = WebhookProcessor(
        db=db,
        config=cfg,
        on_new_signal=lambda s: emitted.append(s),
    )

    now_ts = 1780000000
    valid_payload = {
        "secret": secret,
        "symbol": "XAUUSD",
        "timeframe": "15m",
        "side": "BUY",
        "is_closed": True,
        "candle_timestamp": now_ts,
        "entry_price": 4010.0,
        "atr": 8.0,
        "ema50": 4005.0,
        "ema200": 3980.0,
        "rsi": 59.5,
    }
    raw_valid = json.dumps(valid_payload).encode("utf-8")

    # 1. To'g'ri webhook qabul qilinishi shart
    res1 = proc.process_request(raw_body=raw_valid, now_ts=now_ts)
    assert res1["status"] == "accepted"
    assert len(emitted) == 1
    assert emitted[0].side == "BUY"

    # 2. Aynan shu webhook takroran kelsa -> 'duplicate' qaytishi va signal qayta yuborilmasligi shart
    res2 = proc.process_request(raw_body=raw_valid, now_ts=now_ts)
    assert res2["status"] == "duplicate"
    assert len(emitted) == 1

    # 3. Noto'g'ri token bilan kelsa -> 401 xato
    bad_auth = dict(valid_payload, secret="WRONG_TOKEN", candle_timestamp=now_ts + 900)
    with pytest.raises(WebhookValidationError) as exc_info:
        proc.process_request(raw_body=json.dumps(bad_auth).encode("utf-8"), now_ts=now_ts + 900)
    assert exc_info.value.status_code == 401

    # 4. Sham yopilmagan bo'lsa (is_closed=False) -> 422 xato
    unclosed = dict(valid_payload, is_closed=False, candle_timestamp=now_ts + 1800)
    with pytest.raises(WebhookValidationError) as exc_info2:
        proc.process_request(raw_body=json.dumps(unclosed).encode("utf-8"), now_ts=now_ts + 1800)
    assert exc_info2.value.status_code == 422

    # 5. Replay hujum (timestamp juda eski) -> 422 xato
    old_payload = dict(valid_payload, timestamp=now_ts - 9999, candle_timestamp=now_ts - 9999)
    with pytest.raises(WebhookValidationError) as exc_info3:
        proc.process_request(raw_body=json.dumps(old_payload).encode("utf-8"), now_ts=now_ts)
    assert exc_info3.value.status_code == 422

    # 6. HMAC-SHA256 header imzosi bilan autentifikatsiya
    hmac_payload = {
        "symbol": "XAUUSD",
        "timeframe": "15m",
        "side": "SELL",
        "is_closed": True,
        "candle_timestamp": now_ts + 2700,
        "entry_price": 3995.0,
        "atr": 10.0,
        "rsi": 41.0,
    }
    raw_hmac = json.dumps(hmac_payload).encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), raw_hmac, hashlib.sha256).hexdigest()
    res_hmac = proc.process_request(
        raw_body=raw_hmac,
        headers={"X-Webhook-Signature": f"sha256={sig}"},
        now_ts=now_ts + 2700,
    )
    assert res_hmac["status"] == "accepted"
    assert len(emitted) == 2
    db.close()


def test_webhook_http_server_live_endpoints() -> None:
    db = SignalDatabase(":memory:")
    secret = "LIVE_HTTP_SECRET_TOKEN_9876543210"
    cfg = BotConfig(webhook_secret_token=secret, webhook_max_age_seconds=0)
    proc = WebhookProcessor(db=db, config=cfg)

    server = WebhookHTTPServer(processor=proc, host="127.0.0.1", port=0)
    server.start_background()
    try:
        assert server._server is not None
        actual_port = server._server.server_address[1]

        # 1. GET /health
        with urllib.request.urlopen(f"http://127.0.0.1:{actual_port}/health", timeout=5) as resp:
            assert resp.status == 200
            health = json.loads(resp.read().decode("utf-8"))
            assert health["ok"] is True
            assert health["symbol"] == "XAUUSD"

        # 2. POST /webhook/tradingview
        body = json.dumps(
            {
                "secret": secret,
                "symbol": "XAUUSD",
                "timeframe": "15m",
                "side": "BUY",
                "is_closed": True,
                "candle_timestamp": 1780000000,
                "entry_price": 4012.5,
                "atr": 6.0,
                "ema50": 4005.0,
                "ema200": 3985.0,
                "rsi": 58.0,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{actual_port}/webhook/tradingview",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp2:
            assert resp2.status == 200
            res_json = json.loads(resp2.read().decode("utf-8"))
            assert res_json["ok"] is True
            assert res_json["status"] == "accepted"
    finally:
        server.stop()
        db.close()
