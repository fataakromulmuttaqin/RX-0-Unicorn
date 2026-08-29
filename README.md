# 🦄 RX-0 Unicorn

> **Crypto trading bot bertenaga AI dengan strategi LuxAlgo-grade — dibangun dari nol untuk profit konsisten.**

[![Status](https://img.shields.io/badge/status-Phase%204%20Complete-brightgreen)]()
[![Python](https://img.shields.io/badge/python-3.10+-blue)]()
[![License](https://img.shields.io/badge/license-Private-red)]()

---

## 🎯 Vision

RX-0 Unicorn adalah **crypto trading bot** yang mengimplementasikan strategi terbukti dari **LuxAlgo** dengan approach modern:

- **Confluence-based** — bukan single indicator, tapi 4-layer confirmation
- **Backtested** — setiap strategy harus lulus 6 metrics wajib
- **Adaptive** — belajar dari trade history (LLM-enhanced phase akhir)
- **Transparent** — semua signal, win/loss, dan metrics terekspos di Telegram

**Target pasar:** Crypto spot & futures (Binance, Bybit, OKX) — mulai dari Bitcoin & Ethereum, expand ke altcoin liquid.

---

## 🧠 Strategi Inti

Berdasarkan riset dari [LuxAlgo Library](https://www.luxalgo.com/library/), RX-0 Unicorn menggunakan **4-strategy confluence framework**:

### Core Strategy Stack

| Layer | Strategi | Fungsi | LuxAlgo PF | LuxAlgo WR |
|-------|----------|--------|------------|------------|
| **1. Trend Detection** | **Luminance Breakout Engine** | Identifikasi breakout dengan volume confirm | 2.33 | 71.6% |
| **2. Regime Filter** | **RSI Regime Filter** | Anti-fading runaway trend, validasi momentum | - | - |
| **3. Structure** | **BOS/CHoCH Dashboard** | Break of Structure + Change of Character confirm | - | - |
| **4. Exit Timing** | **WaveTrend Oscillator** | Momentum exit, timing TP yang presisi | 2.20 | 67% |

### Confluence Scoring

- **4/4 confluence** = A+ setup (size up 1.5x normal)
- **3/4 confluence** = Valid entry (full size)
- **2/4 atau kurang** = SKIP (no trade)

> **Kenapa confluence?** Single indicator = noise. Multi-confirmation = edge. Backtest LuxAlgo menunjukkan win rate 71%+ saat 3+ indicator aligned.

---

## 🏗️ Arsitektur

```
┌─────────────────────────────────────────────────────────┐
│                  RX-0 UNICORN SYSTEM                    │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  ┌──────────────┐    ┌──────────────┐                  │
│  │ Data Layer   │───▶│ Indicator    │                  │
│  │ (CCXT)       │    │ Engine       │                  │
│  └──────────────┘    └──────┬───────┘                  │
│                             │                           │
│                             ▼                           │
│                      ┌──────────────┐                   │
│                      │ Confluence   │                   │
│                      │ Scorer       │                   │
│                      └──────┬───────┘                   │
│                             │                           │
│         ┌───────────────────┼───────────────────┐      │
│         ▼                   ▼                   ▼      │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────┐│
│  │ Telegram     │   │ Backtest     │   │ Auto-Trade   ││
│  │ Alert        │   │ Engine       │   │ (Future)     ││
│  └──────────────┘   └──────────────┘   └──────────────┘│
│                                                         │
└─────────────────────────────────────────────────────────┘
```

---

## 🗺️ Roadmap 7 Fase

| Fase | Nama | Status | Output | Estimasi |
|------|------|--------|--------|----------|
| **1** | **Data Foundation** | ✅ Done | Candle puller + SQLite + watchlist | 1-2 hari |
| **2** | **Core Indicator Engine** | ✅ Done | Luminance + RSI Regime + BOS/CHoCH + WaveTrend (Python port) | 3-4 hari |
| **3** | **Confluence Scorer** | ✅ Done | 0-4 scoring logic, entry rules, position sizing | 1 hari |
| **4** | **Telegram Alert System** | ✅ Done | Alert format + cooldown + daemon + top-N ranking | 1-2 hari |
| 5 | Backtest Engine | ⏳ Pending | Historical replay + 6 metrics + equity curve | 2-3 hari |
| 6 | Paper Trading | ⏳ Pending | Dry-run 2-4 minggu, real-time win rate tracking | 2-4 minggu |
| 7 | Auto-Trade Layer | ⏳ Pending | CCXT live execution + risk guard + kill switch | 3-5 hari |

**Total timeline:** ~3-4 minggu sampai full auto-trade ready.

---

## 📦 Tech Stack

- **Language:** Python 3.10+
- **Data Source:** CCXT (Binance public endpoints initially)
- **Storage:** SQLite (local, no external DB)
- **Indicators:** Python port dari PineScript LuxAlgo
- **Alerting:** Telegram Bot API
- **Backtesting:** Custom engine + vectorbt (planned)
- **Execution:** CCXT (Binance, Bybit, OKX)
- **LLM Enhancement:** OpenAI/Groq (Phase 7+)

### Dependencies

```
ccxt>=4.0.0          # Exchange connectivity
pandas>=2.0.0        # Data manipulation
loguru>=0.7.0        # Logging
python-dotenv>=1.0.0 # Config management
pytest>=7.0.0        # Testing
```

---

## 🚀 Quick Start

### 1. Setup Environment

```bash
# Clone
git clone https://github.com/fataakromulm/RX-0-Unicorn.git
cd RX-0-Unicorn

# Virtualenv (Python 3.10+)
python3 -m venv .venv
source .venv/bin/activate

# Install deps
pip install -r requirements.txt

# (Optional) Copy .env template — needed only for real Telegram alerts
cp .env.example .env
# Edit .env dan isi TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID (lihat step 4)
```

### 2. Fetch Initial Data

```bash
# Tarik 500 candle 1H untuk semua watchlist (BTC, ETH, SOL, dll)
python main.py fetch --tier tier_1_major --timeframe 1h --limit 500

# Cek row count di DB
python main.py status
```

### 3. Run Scanner (Phase 2 + Phase 3 confluence)

```bash
# Scan semua watchlist, tampilkan Grade/SL/TP
python main.py scan --timeframe 1h

# Single symbol + filter minimum score
python main.py scan --symbol BTC/USDT --timeframe 1h --min-score 3
```

### 4. Setup Telegram Bot (Optional, untuk Phase 4)

1. Chat ke **@BotFather** di Telegram → `/newbot` → ikuti instruksi → copy token
2. Chat ke **@userinfobot** atau **@get_id_bot** → catat chat ID kamu
3. Edit `.env`:
   ```env
   TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ
   TELEGRAM_CHAT_ID=633709469
   ```
4. Test koneksi:
   ```bash
   python main.py test-alert
   # Harus muncul "Telegram send OK — check your chat!"
   ```
   Kalau token kosong, sample alert di-print ke console (tidak crash).

### 5. Start Alert Daemon (Phase 4)

```bash
# Loop forever: scan + kirim top-5 alert ke Telegram setiap 5 menit
python main.py daemon --timeframe 1h --interval 300

# Override top-N dan interval
python main.py daemon --timeframe 4h --interval 900 --top-n 3

# Stop dengan Ctrl+C — graceful shutdown
```

### 6. Manage Cooldown

```bash
# Lihat semua pair yang sedang cooldown
python main.py cooldown

# Clear cooldown untuk satu pair
python main.py cooldown --clear BTC/USDT

# Clear semua cooldown
python main.py cooldown --clear-all
```

> **Catatan:** Cooldown disimpan di SQLite table `alert_cooldown`. Default
> 15 menit per pair (override via `ALERT_COOLDOWN_MINUTES` di .env). Mencegah
> spam alert untuk pair yang sama.

---

## 📊 Project Structure

```
luxalgo-trader/
├── data/
│   ├── fetchers/
│   │   └── crypto_fetcher.py      # CCXT Binance public endpoints
│   ├── storage/
│   │   └── candle_db.py           # SQLite schema + CRUD
│   └── pairs/
│       └── watchlist.json         # 50+ crypto pairs (tiered)
├── src/
│   ├── config.py                  # Constants, paths, settings
│   └── logger.py                  # Loguru setup
├── indicators/                     # Phase 2 ✅
│   ├── _utils.py                   # OHLCV validation helper
│   ├── luminance.py                # Luminance Breakout Engine
│   ├── rsi_regime.py               # RSI Regime Filter (RSI + ADX)
│   ├── structure.py                # BOS/CHoCH Structure Dashboard
│   └── wavetrend.py                # WaveTrend Oscillator
├── confluence/                      # Phase 3 ✅
│   └── scorer.py                    # score_confluence() / latest_confluence()
├── alerts/                          # Phase 4 ✅
│   ├── telegram.py                  # TelegramBot (httpx-based)
│   ├── formatter.py                 # format_signal() — alert text template
│   └── cooldown.py                  # CooldownManager (SQLite-backed)
├── backtest/                       # Phase 5
│   ├── engine.py
│   └── metrics.py
├── execution/                      # Phase 7
│   ├── trader.py
│   └── risk_manager.py
├── tests/
├── main.py
├── requirements.txt
├── .gitignore
└── README.md
```

---

## 🎯 Watchlist Tier System

Bot scan 50+ pairs yang di-organize dalam 4 tier:

- **Tier 1 (Major):** BTC, ETH, SOL, BNB — selalu scan
- **Tier 2 (Large Cap):** XRP, ADA, AVAX, dll — high liquidity
- **Tier 3 (Mid Cap):** Mid-cap altcoin dengan volume kuat
- **Tier 4 (Development):** Emerging pairs, lower priority

Detail ada di `data/pairs/watchlist.json`.

---

## 📈 Performance Metrics (Target)

Saat backtest & paper trading jalan, RX-0 Unicorn diukur dengan **6 metrics wajib**:

1. **Win Rate** — target > 50% (dengan R:R 1:2 = profitable)
2. **Profit Factor** — target > 1.5 (gross profit / gross loss)
3. **Max Drawdown** — target < 20%
4. **Sharpe Ratio** — target > 1.5
5. **Avg R-Multiple** — target > 1.5R per trade
6. **Expectancy** — formula: (WR × avg_win) - ((1-WR) × avg_loss) — target > 0

---

## 🛡️ Risk Management (Phase 7)

- **Risk per trade:** 1-2% modal
- **R:R minimum:** 1:2
- **Max trades/day:** 3 (anti-overtrading)
- **Daily loss limit:** 5% → auto-stop
- **Correlation guard:** Max 2 posisi dalam pair yang berkorelasi tinggi
- **News filter:** Skip 30 menit sebelum/sesudah high-impact news

---

## 🔒 Security & Privacy

- **No API key di code** — semua via `.env` (gitignored)
- **Paper trading by default** — live mode butuh explicit enable
- **Kill switch** — emergency stop via Telegram command
- **Local-only data** — tidak ada data dikirim ke external service (kecuali Telegram alert)

---

## 📚 References

- [LuxAlgo Library](https://www.luxalgo.com/library/) — source strategi
- [LuxAlgo AI Backtesting](https://www.luxalgo.com/features/backtesting/)
- [CCXT Documentation](https://docs.ccxt.com/)
- [Binance API Docs](https://binance-docs.github.io/apidocs/)

---

## 📝 Development Log

### Phase 1 — Data Foundation (Current)
- [x] Project structure setup
- [x] CCXT fetcher implementation
- [x] SQLite storage layer
- [x] Watchlist (50+ pairs)
- [x] CLI entry point
- [x] Logger setup
- [ ] (Coming) Backfill historical data

### Phase 2 — Core Indicators (Current)
- [x] Luminance Breakout Engine (Python port)
- [x] RSI Regime Filter (RSI + ADX regime, anti-fade-trend guard)
- [x] BOS/CHoCH Structure (fractal swing detection)
- [x] WaveTrend Oscillator
- [x] `main.py scan` CLI preview + unit tests (32 tests, synthetic OHLCV)
- [ ] (Coming) Formal Confluence Scorer module (Phase 3)

### Phase 3 — Confluence Scorer ✅
- [x] `confluence/scorer.py` — skor 0-4 per bar, grade skip/valid/A+
- [x] Risk levels: entry, SL, TP1 (1R), TP2 (2R), risk_reward
- [x] `main.py scan` dipindah ke Confluence Scorer resmi
- [x] Unit tests (15 tests: score bounds, grade consistency, risk ordering)

### Phase 4 — Telegram Alert System ✅
- [x] `alerts/telegram.py` — `TelegramBot` (httpx, graceful degradation kalau token kosong)
- [x] `alerts/formatter.py` — `format_signal()` sesuai template Chastiefol-style
- [x] `alerts/cooldown.py` — `CooldownManager` (SQLite `alert_cooldown` table)
- [x] `main.py daemon` — loop forever scan + kirim top-N alert
- [x] `main.py test-alert` — verifikasi bot config
- [x] `main.py cooldown` — list/clear per-pair cooldown
- [x] `.env.example` — template TELEGRAM_BOT_TOKEN/CHAT_ID
- [x] Unit tests (35 tests: cooldown logic, formatter edge cases, httpx mock)
- [ ] (Coming) Daily digest

### Phase 5-7 (Planned)
- Backtest engine, paper trading, auto-trade

---

## 📄 License

Private project — All rights reserved.

---

**Built with 🦄 by Fataakromulm | Strategi powered by LuxAlgo**
