# 🦄 RX-0 Unicorn

> **Crypto trading bot bertenaga AI dengan strategi LuxAlgo-grade — dibangun dari nol untuk profit konsisten.**

[![Status](https://img.shields.io/badge/status-Phase%205%20Complete-brightgreen)]()
[![Python](https://img.shields.io/badge/python-3.10+-blue)]()
[![Tests](https://img.shields.io/badge/tests-90%2B%20passing-brightgreen)]()
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
| **5** | **Backtest Engine** | ✅ Done | Walk-forward + 6 metrics + equity curve + JSON export | 2-3 hari |
| **5b** | **TradingView Pine Scripts** | ✅ Done | 2 indikator (.pine) untuk visual chart + alert | bonus |
| 6 | Paper Trading | ✅ Done | Virtual $10k portfolio, SL/TP polling, 5-tier Telegram, 55 unit tests | 2-4 minggu |
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

### 7. Run Backtest (Phase 5 — validasi strategi)

```bash
# Backtest 90 hari BTC/USDT 1H, modal $10k, risk 2% per trade
python main.py backtest --symbol BTC/USDT --timeframe 1h --days 90

# Output ke JSON + chart PNG
python main.py backtest --symbol ETH/USDT --timeframe 4h --days 180 \
  --output backtest/results/eth_180d.json \
  --chart backtest/results/eth_equity.png

# Multi-symbol loop (shell)
for s in BTC/USDT ETH/USDT SOL/USDT; do
  python main.py backtest --symbol $s --timeframe 1h --days 90
done
```

**6 metrics yang diukur** (target per `STRATEGY.md`):
- Win Rate > 50% · Profit Factor > 1.5 · Max Drawdown < 20%
- Sharpe Ratio > 1.5 · Avg R-Multiple > 1.5R · Expectancy > 0

Report menampilkan verdict (X/6 metrics lulus target) + equity curve chart.

### 8. Paper Trading (Phase 6 — dry-run live)

Validate strategi real-time tanpa uang sungguhan. Default mode — semua
confluence signal otomatis di-mirror ke virtual $10k portfolio.

```bash
# Initialize (idempotent) — buat paper DB + state awal
python main.py paper start

# Cek status portfolio, open positions, drawdown
python main.py paper status

# Generate text + PNG equity report (default 7 hari terakhir)
python main.py paper report --days 7

# Long-running monitor (poll harga via CCXT, fire SL/TP/time-stop)
python main.py paper monitor
```

**5-tier Telegram alerts** (fire otomatis kalau `TELEGRAM_BOT_TOKEN` di-set):

| Tier | Trigger              | Format                                     |
|------|----------------------|--------------------------------------------|
| 1    | New entry            | symbol, direction, entry/SL/TP1/TP2, grade |
| 2    | Exit (any reason)    | P/L $, P/L %, R-multiple                   |
| 3    | Daily digest         | equity, day P/L, trades, win rate, DD      |
| 4    | Weekly report        | full metrics + equity chart PNG            |
| 5    | Risk gate breach     | alert_type + DD%/equity/paused_until       |

**Phase 7 readiness** tercetak di akhir `paper report` —
🟢 READY = win rate ≥ 40%, profit factor ≥ 1.0, drawdown ≤ 20%,
total trades ≥ 30. Lihat [`docs/PAPER_TRADING.md`](docs/PAPER_TRADING.md)
untuk detail lengkap.

### 9. TradingView Visualization (Optional)

Visualisasi strategi di chart TradingView — cocok untuk konfirmasi manual.

**File di `tradingview/`:**
- `rx0-confluence.pine` — overlay (Luminance + Structure + Confluence table)
- `rx0-momentum.pine` — pane bawah (RSI + WaveTrend + regime background)

**Cara install** (lengkap di `tradingview/INSTALL.md`):
1. Buka TradingView → buka chart pair apapun (mis. BTCUSDT)
2. Klik tab **Pine Editor** di bagian bawah
3. New blank script → paste `rx0-confluence.pine` → Save → **Add to chart**
4. Ulangi untuk `rx0-momentum.pine` (pilih "lower pane")
5. Setup alert: klik indicator → **...** → **Add Alert** → pilih condition

> **Catatan:** TradingView Free plan = max 2 indikator/chart. 2 file ini udah optimal untuk limit itu.

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
├── backtest/                       # Phase 5 ✅
│   ├── engine.py
│   └── metrics.py
├── paper/                          # Phase 6 ✅
│   ├── journal.py                  # SQLite: paper_trades / paper_daily / paper_state
│   ├── portfolio.py                # virtual balance, sizing, drawdown, circuit breaker
│   ├── trader.py                   # open_from_signal / check_one_position / monitor_loop
│   ├── reporter.py                 # text + chart report, phase7_readiness
│   └── notifier.py                 # 5-tier Telegram (entry/exit/daily/weekly/risk)
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

## 🧪 Backtest Methodology

**Pendekatan:** walk-forward simulation, no look-ahead.

| Aspek | Implementasi |
|---|---|
| **Entry** | Saat confluence score ≥ 3 (A+ atau Valid). Fill price = next candle **open** (bukan close sekarang) |
| **Exit** | 3 mode: TP1 hit, TP2 hit, SL hit, atau time-stop (max 50 candle) |
| **Sizing** | 1-2% modal per trade (configurable). A+ dapat 1.5x multiplier |
| **No look-ahead** | Indikator + signal dihitung di bar `t`, entry/track di `t+1` dst |
- **Slippage** | Dimodelkan konservatif lewat pessimistic SL/TP ordering di Phase 5 + divalidasi lagi di Phase 6 paper trading |
| **Sample size** | Minimum 30 trade untuk verdict valid. < 30 = warning |

**6 Metrics Wajib + Target:**
- Win Rate > 50% · Profit Factor > 1.5 · Max Drawdown < 20%
- Sharpe Ratio > 1.5 · Avg R-Multiple > 1.5R · Expectancy > 0

Detail formula & edge case: lihat `backtest/metrics.py` docstring.

**Interpretasi:**
- **6/6 PASS** → strategi layak live trade (setelah paper trade validation)
- **4-5/6** → perlu tuning, jangan live dulu
- **< 4/6** → strategy tidak viable, rework

---

## 📺 TradingView Visualization

Visualisasi strategi langsung di chart TradingView. Cocok untuk:
- ✅ Konfirmasi manual sebelum entry
- ✅ Lihat BOS/CHoCH + Luminance breakout bareng
- ✅ Setup alert ke webhook → forward ke RX-0 daemon

**File Pine Script di `tradingview/`:**

| File | Tipe | Isi |
|---|---|---|
| `rx0-confluence.pine` | Overlay (chart utama) | Luminance range box + breakout arrows + BOS/CHoCH labels + Confluence score table |
| `rx0-momentum.pine` | Lower pane | RSI Wilder + ADX regime + WaveTrend LazyBear + zone highlight |

**Limitasi:** TradingView Free = max 2 indikator/chart. 2 file ini optimal untuk limit itu.

**Detail lengkap:** baca `tradingview/README.md` + `tradingview/INSTALL.md`.

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

### Phase 5-6 (Done)
- [x] **Phase 5** — Backtest engine: walk-forward, 6 metrics, equity curve, JSON export
- [x] **Phase 6** — Paper trading: virtual portfolio + SL/TP polling + 5-tier Telegram notifier + Phase 7 readiness check (55 unit tests)
- [ ] **Phase 7** — Auto-trade layer (CCXT live execution + risk guard + kill switch)
- Backtest engine, paper trading, auto-trade

---

## 📄 License

Private project — All rights reserved.

---

**Built with 🦄 by Fataakromulm | Strategi powered by LuxAlgo**
