# XAUUSD (Gold) Telegram AI Signal Bot — Demo / Paper Trading

Ushbu loyiha **XAUUSD (Oltin)** bozori uchun **EMA 50 + EMA 200 (Trend)**, **RSI 14 (Momentum)** va **ATR 14 (Stop-Loss / Take-Profit)** algoritmi asosida ishlovchi, **faqat Demo / Paper Trading** uchun mo‘ljallangan to‘liq funksional Python Telegram Signal Botdir.

> ⚠️ **Qat’iy eslatma:** Ushbu dastur faqat ta’lim va demo/paper-trading maqsadida yaratilgan. Hech qanday foyda kafolati berilmaydi va hech qaysi brokerga real buyurtma yuborilmaydi.

---

## 1. TradingView Free Tarif Cheklovlari va Yechim

**Haqiqiy holat (Tekshirilgan):**
1. **Webhook cheklovi:** TradingView **Free (Basic)** tarifida **Webhook URL notifications** funksiyasi **mavjud emas** (Webhook faqat pullik *Essential / Plus / Premium* tariflarida ishlaydi).
2. **Indikator limiti:** TradingView Free tarifida bitta grafikda **maksimum 2 ta indikator** qo‘shish mumkin.

**Loyihadagi qonuniy va 100% bepul yechim:**
- **Avtonom Python Polling Dvigateli (`src/market_data.py`):** Bot TradingView webhookiga bog‘lanib qolmasdan, ochiq va qonuniy bozor ma’lumotlari manbalaridan haqiqiy XAUUSD shamlarini o‘zi oladi:
  1. **Yahoo Finance (`GC=F` — COMEX Gold Futures):** 15m va 1h OHLCV shamlari (API kalitsiz, bepul).
  2. **Swissquote Bank Institutional Feed (`XAU/USD`):** Jonli Spot XAU/USD Bid/Ask kotirovkasi (API kalitsiz, bepul).
  3. **TwelveData API (`XAU/USD`):** Ixtiyoriy bepul API kalit kiritilganda Spot XAU/USD shamlari.
  4. **Binance Public API (`PAXGUSDT`):** 1:1 fizik oltin bilan ta’minlangan PAX Gold (uzluksiz zaxira manba sifatida).
- **4-in-1 Pine Script v5 (`pine/xauusd_ema_rsi_atr.pine`):** TradingView Free tarifida grafikda ko‘rish uchun EMA 50, EMA 200, RSI 14, ATR 14 va Entry/SL/TP1/TP2/TP3 hisob-kitoblarini **bitta indikator slotiga** birlashtiradi.
- **Himoyalangan Webhook Server (`src/webhook_server.py`):** Agar kelajakda pullik TradingView tarifiga o‘tsangiz yoki Email-to-Webhook ko‘prigidan foydalansangiz, `POST /webhook/tradingview` manzili tayyor.

---

## 2. Trading Algoritmi Qoidalari

- **Faqat yopilgan shamda tasdiqlash (`is_closed = True`):** Hali shakllanayotgan (ochiq) shamda hech qachon signal berilmaydi.
- **Trend (EMA 50 va EMA 200):**
  - **BUY:** `EMA 50 > EMA 200`, ikkala EMA ko‘tarilmoqda va sham yopilish narxi `EMA 50` dan yuqorida.
  - **SELL:** `EMA 50 < EMA 200`, ikkala EMA pasaymoqda va sham yopilish narxi `EMA 50` dan pastda.
- **Momentum (RSI 14):**
  - **BUY:** `50 <= RSI 14 <= 70` va RSI o‘smoqda.
  - **SELL:** `30 <= RSI 14 <= 50` va RSI pasaymoqda.
- **SL va TP1 / TP2 / TP3 (ATR 14 asosida):**
  - `SL masofasi = ATR 14 × 1.5`
  - `TP1 = 1:1.0 R:R` (34% virtual pozitsiya)
  - `TP2 = 1:2.0 R:R` (33% virtual pozitsiya)
  - `TP3 = 1:3.0 R:R` (33% virtual pozitsiya)

---

## 3. Telegram Bot Buyruqlari

- `/start` — Botni boshlash va xabarlarga obuna bo‘lish
- `/status` — Faol signallar, virtual balans va monitoring holati
- `/signals` — Oxirgi signallar tarixi
- `/stats` — Demo savdo statistikasi (Win Rate, PnL $, R-multiple, TP1/TP2/TP3/SL soni)
- `/pause` — Signal monitoringini vaqtincha to‘xtatish
- `/resume` — Signal monitoringini qayta davom ettirish

---

## 4. Ishga Tushirish Yo‘riqnomasi

### 1-qadam: Kutubxonalarni o‘rnatish
```bash
pip install -r requirements.txt
```

### 2-qadam: `.env` faylini sozlash
`.env.example` faylidan `.env` nusxasini yarating va `@BotFather` dan olingan tokenni kiriting:
```env
TELEGRAM_BOT_TOKEN=123456789:AAH_your_real_token_from_botfather
```

### 3-qadam: Tekshiruv, Backtest va Ishga tushirish
```bash
# 1. Tizim va jonli XAUUSD ulanishini tekshirish
python main.py --mode self-check

# 2. Bir martalik jonli bozor tahlili
python main.py --mode once

# 3. Tarixiy XAUUSD ma'lumotlarida backtest (15m va 1h)
python main.py --mode backtest --csv data/xauusd_historical_sample.csv --timeframe 15m
python main.py --mode backtest --csv data/xauusd_1h_sample.csv --timeframe 1h

# 4. Avtomatik testlarni ishga tushirish
pytest -v

# 5. Botni to'liq ishga tushirish
python main.py --mode run
```
