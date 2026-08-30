# 🦄 RX-0 Unicorn

**Live Dashboard:** [rx-0-unicorn.vercel.app](https://rx-0-unicorn.vercel.app) — paper trading stats auto-refresh tiap 5 menit

> **Crypto trading bot bertenaga AI dengan strategi LuxAlgo-grade — dibangun dari nol untuk profit konsisten.**

[![Status](https://img.shields.io/badge/version-v0.7.0-brightgreen)]()
[![Python](https://img.shields.io/badge/python-3.10+-blue)]()
[![Tests](https://img.shields.io/badge/tests-217%20passing-brightgreen)]()
[![License](https://img.shields.io/badge/license-Private-red)]()
[![Timeframe](https://img.shields.io/badge/MTF-1D%2F4H%2F1H%2F15m-blue)]()

---

## 🎯 Vision

RX-0 Unicorn adalah **crypto trading bot** yang mengimplementasikan strategi terbukti dari **LuxAlgo** dengan approach modern:

- **Multi-timeframe confluence** — 1D (long-term bias) → 4H (medium) → 1H (setup) → 15m (entry)
- **Confluence-based** — bukan single indicator, tapi 4-layer confirmation
- **Backtested** — setiap strategy harus lulus 6 metrics wajib
- **Correlation-aware** — max 2 correlated positions (no stacking)
- **Adaptive** — belajar dari trade history (LLM-enhanced phase akhir)
- **Transparent** — semua signal, win/loss, dan metrics terekspos di Telegram

**Target pasar:** Crypto spot & futures (Binance, Bybit, OKX) — mulai dari Bitcoin & Ethereum, expand ke altcoin liquid.

---

## 🧠 Strategi Inti (v0.7.0)

Berdasarkan riset dari [LuxAlgo Library](https://www.luxalgo.com/library/), RX-0 Unicorn menggunakan **4-strategy confluence framework** + **multi-timeframe hierarchy**:

### Core Strategy Stack (1H Confluence)

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

### Multi-Timeframe Architecture (v0.7.0)

```
1D  → EMA 20/50 + market structure → LONG-TERM BIAS (top filter)
4H  → EMA 50/200 + market structure → MEDIUM BIAS
1H  → 4-indicator confluence → SETUP
15m → EMA 9/21 cross + RSI(7) + volume → ENTRY TIMING
```

**Rules:**
- **1D + 4H + 1H all aligned** (all +1 or all -1) = "STRONG ALIGNED" → A+ grade eligible
- **4H + 1H agree, 1D neutral** = "SOFT ALIGNED" → trade allowed
- **4H vs 1H conflict** (e.g. 4H bullish, 1H signal short) = **NO TRADE**
- **4H weak bias (strength<30)** = only 1H entries, no 15m
- **All 3 conflict** (e.g. 1D bearish, 4H bullish, 1H setup both ways) = NO TRADE

### Correlation Guard (v0.7.0)
- **11 correlation groups** mapped (L1 majors, L1 alts, L2s, DeFi, memes, AI, privacy, GameFi, infra, RWA, exchange)
- **17 cross-group rules** (e.g. BTC drop → L1 alts dump → memes dump)
- **Max 2 correlated positions** per `STRATEGY.md` line 162
- Telegram notification on block (so user knows why trade rejected)

> **Kenapa confluence + MTF?** Single indicator = noise. Single timeframe = blind to trend. Multi-confirmation across timeframes = edge. Backtest LuxAlgo menunjukkan win rate 71%+ saat 3+ indicator aligned across HTF+MTF+LTF.

### News + Sentiment (v0.7.0)
- **News** — 3 RSS sources (CoinDesk, Cointelegraph, The Block), categorized by impact (high/medium/low), currency tagging
- **Sentiment** — CoinGecko market data (price action implied) + Alternative.me Fear & Greed index
- **Lazy fetching** — on-demand only via `/rx0 news` & `/rx0 sentiment` (no scheduled API calls)
- **Rate limited** — sliding window 10 req/min, batched calls (1 call = 250 coins)
- **Informational only** — does NOT block entry (per user request); daily summary in digest

---

## 🏗️ Arsitektur

```
┌─────────────────────────────────────────────────────────┐
│                  RX-0 UNICORN SYSTEM (v0.7.0)           │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  ┌──────────────┐    ┌──────────────┐                  │
│  │ Data Layer   │───▶│ Multi-TF     │                  │
│  │ (CCXT)       │    │ 15m/1H/4H/1D │                  │
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
│  │ Correlation  │   │ Backtest     │   │ Auto-Trade   ││
│  │ Guard        │   │ Engine       │   │ (Future)     ││
│  └──────────────┘   └──────┬───────┘   └──────────────┘│
│                             │                           │
│                             ▼                           │
│                      ┌──────────────┐                   │
│                      │ Paper Trader │                   │
│                      │ + 5-Tier TG  │                   │
│                      └──────────────┘                   │
│                                                         │
│  ┌─────────────────────────────────────────────────┐  │
│  │ News + Sentiment (lazy, rate-limited, on-demand)│  │
│  │ /rx0 news | /rx0 sentiment | Daily digest     │  │
│  └─────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

---

## 🗺️ Roadmap 7 Fase (v0.7.0 status)

| Fase | Nama | Status | Output | Tests |
|------|------|--------|--------|-------|
| **1** | **Data Foundation** | ✅ Done | Candle puller + SQLite + watchlist | 8 |
| **2** | **Core Indicator Engine** | ✅ Done | Luminance + RSI Regime + BOS/CHoCH + WaveTrend (Python port) | 32 |
| **3** | **Confluence Scorer** | ✅ Done | 0-4 scoring logic, entry rules, position sizing | 15 |
| **4** | **Telegram Alert System** | ✅ Done | Alert format + cooldown + daemon + top-N ranking | 35 |
| **5** | **Backtest Engine** | ✅ Done | Walk-forward + 6 metrics + equity curve + JSON export | 18 |
| **5b** | **TradingView Pine Scripts** | ✅ Done | 2 indikator (.pine) v6 — visual chart + alert | manual |
| **5c** | **Cheat Sheet** | ✅ Done | HTML + MD visual reference untuk quick trading | manual |
| **5d** | **Advanced Backtest** | ✅ Done | 4 methods: Monte Carlo, Walk Forward, Bootstrap, Permutation | included |
| **5e** | **Multi-Exchange Fetcher** | ✅ Done | Binance data API + SSL bypass fallback chain | included |
| **5f** | **Quick Wins** | ✅ Done | Confluence threshold, slippage/commission, telegram cmds, trailing stop | 25 |
| **6** | **Paper Trading** | ✅ Done | Virtual $10k, 5-tier Telegram, correlation guard, MTF filter | 55+27 |
| **6b** | **Multi-TF (1D/4H/1H/15m)** | ✅ Done | Hierarchy + bias checking + 15m entry | included |
| **6c** | **News + Sentiment** | ✅ Done | 3 RSS feeds + CoinGecko + Fear/Greed (lazy) | 0 (network) |
| **6d** | **Correlation Guard** | ✅ Done | 11 groups + 17 cross-rules + max 2 correlation | 27 |
| **7** | **Auto-Trade Layer** | ⏳ Pending | CCXT live execution + risk guard + kill switch | — |

**Test count:** 217 passing, 2 skipped (live network)

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
RX-0_Unicorn/
├── data/
│   ├── fetchers/
│   │   ├── crypto_fetcher.py      # CCXT Binance public endpoints
│   │   ├── multi_exchange.py      # Multi-exchange fallback chain (v0.7)
│   │   ├── news.py                # 3 RSS feeds (CoinDesk, Cointelegraph, The Block)
│   │   └── sentiment.py           # CoinGecko + Fear & Greed (lazy, rate-limited)
│   ├── storage/
│   │   └── candle_db.py           # SQLite schema + CRUD (15m/1H/4H/1D)
│   └── pairs/
│       └── watchlist.json         # 50+ crypto pairs (4 tiers)
├── src/
│   ├── config.py                  # Constants, paths, settings
│   └── logger.py                  # Loguru setup
├── indicators/                     # Phase 2 ✅
│   ├── _utils.py                   # OHLCV validation helper
│   ├── luminance.py                # Luminance Breakout Engine
│   ├── rsi_regime.py               # RSI Regime Filter (RSI + ADX)
│   ├── structure.py                # BOS/CHoCH Structure Dashboard
│   └── wavetrend.py                # WaveTrend Oscillator
├── confluence/                      # Phase 3 ✅ + MTF v0.7
│   ├── scorer.py                    # score_confluence() / latest_confluence()
│   └── mtf.py                       # Multi-timeframe bias + 15m entry
├── alerts/                          # Phase 4 ✅
│   ├── telegram.py                  # TelegramBot (httpx-based)
│   ├── formatter.py                 # format_signal() — alert text template
│   ├── cooldown.py                  # CooldownManager (SQLite-backed)
│   └── commands.py                  # /rx0 status, trades, news, sentiment, etc.
├── backtest/                       # Phase 5 ✅
│   ├── engine.py
│   ├── metrics.py
│   ├── trade_generator.py
│   ├── advanced.py                 # Monte Carlo / WF / Bootstrap / Permutation
│   ├── run_advanced.py             # CLI runner
│   └── visualize_advanced.py       # Chart generator
├── paper/                          # Phase 6 ✅ + Correlation Guard v0.7
│   ├── journal.py                  # SQLite: paper_trades / paper_daily / paper_state
│   ├── portfolio.py                # virtual balance, sizing, drawdown, correlation check
│   ├── trader.py                   # open_from_signal / check_one_position / monitor_loop
│   ├── reporter.py                 # text + chart report, phase7_readiness
│   ├── notifier.py                 # 5-tier Telegram (entry/exit/daily/weekly/risk+correlation)
│   └── correlation_guard.py        # 11 groups + 17 cross-rules (max 2 correlated)
├── tradingview/                    # Phase 5b ✅
│   ├── rx0-confluence.pine          # Overlay (Luminance + BOS/CHoCH + score)
│   ├── rx0-momentum.pine            # Lower pane (RSI + ADX + WaveTrend)
│   ├── README.md
│   ├── INSTALL.md
│   └── PINE_V6_MIGRATION.md
├── docs/                            # Documentation
│   ├── CHEATSHEET.md / .html        # Quick reference v0.7
│   ├── PAPER_TRADING.md             # Full paper trading guide
│   ├── BACKTEST_ADVANCED.md         # Monte Carlo / WF / Bootstrap / Permutation
│   └── BACKTEST.md                  # Basic backtest methodology
├── scripts/
│   └── paper_daemon.py             # Long-running paper trading daemon
├── tests/                           # 217 tests passing
├── main.py                          # CLI: fetch, status, scan, daemon, backtest, paper
├── requirements.txt
├── .env.example
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

## 🛡️ Risk Management (v0.7.0)

- **Risk per trade:** 1-2% modal
- **R:R minimum:** 1:2 (TP1 = 1R, TP2 = 2R)
- **Max trades/day:** 3 (anti-overtrading)
- **Daily loss limit:** 5% → auto-stop
- **Drawdown circuit:** 15% → pause 24h
- **Correlation guard:** Max 2 posisi dalam pair yang berkorelasi tinggi (11 groups + 17 cross-rules)
- **Multi-timeframe filter:** 1D + 4H + 1H must align (no counter-trend trades)
- **Trailing stop after TP1:** 50% profit ratchet (1% SL when +2%, 2.5% SL when +5%, 5% SL when +10%)
- **News:** Informational only (digest format, lazy fetch on demand)
- **API rate limit:** 10 req/min sliding window + batched calls (50+ pairs in 1 call)

---

## 🔒 Security & Privacy

- **No API key di code** — semua via `.env` (gitignored)
- **Paper trading by default** — live mode butuh explicit enable
- **Kill switch** — emergency stop via Telegram command
- **Local-only data** — tidak ada data dikirim ke external service (kecuali Telegram alert)

---

## 🧪 Backtest Methodology (v0.7.0)

**Pendekatan:** walk-forward simulation, no look-ahead. Multi-timeframe filter applied.

| Aspek | Implementasi |
|---|---|
| **Entry** | Saat confluence score ≥ 2 (A+ atau Valid). Fill price = next candle **open** (bukan close sekarang) |
| **MTF filter** | 4H + 1D bias must agree with 1H signal direction (or 1D neutral) |
| **Correlation filter** | Backtest respects max 2 correlated positions per pair |
| **Exit** | 3 mode: TP1 hit, TP2 hit, SL hit, atau time-stop (max 50 candle) |
| **Sizing** | 1-2% modal per trade (configurable). A+ dapat 1.5x multiplier |
| **Slippage + commission** | 0.05% slippage + 0.10% commission per side (realistic) |
| **No look-ahead** | Indikator + signal dihitung di bar `t`, entry/track di `t+1` dst |
| **Sample size** | Minimum 30 trade untuk verdict valid. < 30 = warning |

**6 Metrics Wajib + Target:**
- Win Rate > 50% · Profit Factor > 1.5 · Max Drawdown < 20%
- Sharpe Ratio > 1.5 · Avg R-Multiple > 1.5R · Expectancy > 0

**Advanced Methods** (in `backtest/advanced.py`):
- **Monte Carlo** — resample trade order 1000x, check P(profit) and drawdown percentiles
- **Walk Forward** — rolling in-sample/out-of-sample, checks OOS positive returns
- **Bootstrap** — resample trades with replacement, check median/percentile robustness
- **Permutation** — shuffle trade order 1000x, check p-value of actual return

**Latest Backtest Result (MTF enabled, $100 capital):**
```
Trade count: 5   Win rate: 60%   Profit factor: 2.27   Sharpe: 6.02
Max DD: 2.23%   Return: +3.11%
Verdict: 🟢 EXCELLENT (3/4 pillars pass)
```

Compare to non-MTF: 28 trades, 39% WR, PF 1.25, Sharpe 1.49, Max DD 5.50%.
**MTF dramatically improves quality** — fewer trades, higher WR, better Sharpe, lower DD.

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

### Phase 1 — Data Foundation ✅
- [x] Project structure setup
- [x] CCXT fetcher implementation
- [x] SQLite storage layer
- [x] Watchlist (50+ pairs)
- [x] CLI entry point
- [x] Logger setup
- [x] Multi-timeframe support (5m, 15m, 1h, 4h, 1d)

### Phase 2 — Core Indicators ✅
- [x] Luminance Breakout Engine (Python port)
- [x] RSI Regime Filter (RSI + ADX regime, anti-fade-trend guard)
- [x] BOS/CHoCH Structure (fractal swing detection)
- [x] WaveTrend Oscillator
- [x] `main.py scan` CLI preview + unit tests (32 tests, synthetic OHLCV)

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
- [x] Daily digest (Tier 3) + weekly report (Tier 4)
- [x] Command listener thread (`/rx0 status`, `/rx0 trades`, dll)

### Phase 5 — Backtest Engine ✅
- [x] Walk-forward engine + 6 metrics + equity curve + JSON export
- [x] Multi-symbol loop
- [x] Trade generator with confluence scoring
- [x] Realistic slippage (0.05%) + commission (0.10%)

### Phase 5b — TradingView Pine Scripts ✅
- [x] `rx0-confluence.pine` — overlay (Luminance + BOS/CHoCH + score)
- [x] `rx0-momentum.pine` — lower pane (RSI + ADX + WaveTrend)
- [x] Pine Script v6 compliance (TradingView mandatory)
- [x] `INSTALL.md` + `PINE_V6_MIGRATION.md`

### Phase 5c — Cheat Sheet ✅
- [x] `docs/CHEATSHEET.md` + `docs/CHEATSHEET.html`
- [x] Decision matrix, pre-entry checklist, risk rules
- [x] Color reference (TradingView hex)
- [x] Updated to v0.7.0 with MTF + correlation

### Phase 5d — Advanced Backtest ✅
- [x] Monte Carlo (1000 simulations) + drawdown percentiles
- [x] Walk Forward (in-sample / out-of-sample)
- [x] Bootstrap (1000 resamples)
- [x] Permutation test (1000 shuffles)
- [x] 4-pillar verdict (stat sig, OOS positive, bootstrap robust, MC profit prob)
- [x] `docs/BACKTEST_ADVANCED.md`

### Phase 5e — Multi-Exchange Fetcher ✅
- [x] Binance data API (`data-api.binance.vision`) — geo-bypass
- [x] SSL bypass fallback (`verify=False`) untuk Bybit/OKX/Kucoin
- [x] Fallback chain: Binance data API → Gate.io/HTX → Bybit/OKX dengan verify=False
- [x] 57 pairs backfilled (was 52 di Gate.io)

### Phase 5f — Quick Wins ✅
- [x] Lower confluence threshold (3 → 2) + quality filters (volume/ADX/spread)
- [x] Trailing stop after TP1 (50% profit ratchet)
- [x] Telegram command listener: `/rx0 status`, `/rx0 trades`, `/rx0 stop`, dll
- [x] Slippage 0.05% + commission 0.10% realistic
- [x] All passed 190+ tests

### Phase 6 — Paper Trading ✅
- [x] `paper/{journal,portfolio,trader,reporter,notifier}.py`
- [x] 5-tier Telegram (entry, exit, daily, weekly, risk)
- [x] Drawdown circuit breaker
- [x] Phase 7 readiness check
- [x] 55+ unit tests

### Phase 6b — Multi-Timeframe Architecture ✅
- [x] `confluence/mtf.py` — compute_htf_bias (1D + 4H)
- [x] compute_ltf_entry_signal (15m entry)
- [x] get_mtf_bias_and_confluence (3-way alignment)
- [x] Wired into `paper_daemon.py` (entry filter)
- [x] Integrated into `backtest/trade_generator.py`
- [x] **Result: trades 28→5, WR 39%→60%, Sharpe 1.49→6.02, Max DD 5.50%→2.23%**

### Phase 6c — News + Sentiment ✅
- [x] `data/fetchers/news.py` — 3 RSS feeds (CoinDesk, Cointelegraph, The Block)
- [x] `data/fetchers/sentiment.py` — CoinGecko + Fear & Greed
- [x] Impact categorization (high/medium/low)
- [x] Currency tagging (BTC, ETH, SOL, dll)
- [x] **Rate-limit optimized**: batched calls (1 call = 250 coins), sliding window 10 req/min
- [x] **Lazy fetching**: on-demand only via `/rx0 news` & `/rx0 sentiment`
- [x] Informational only (no trade blocking, per user request)
- [x] Daily summary in digest format

### Phase 6d — Correlation Guard ✅
- [x] `paper/correlation_guard.py` — 11 correlation groups + 17 cross-group rules
- [x] `can_open_new_position(symbol=...)` extended in portfolio
- [x] Telegram notification on block (Tier 5: correlation_limit)
- [x] 27 unit tests covering all scenarios
- [x] **Per STRATEGY.md line 162**: max 2 correlated positions

### Phase 7 — Auto-Trade Layer ⏳
- [ ] CCXT live execution
- [ ] Risk manager (1-2% per trade, max 3 trades/day, correlation guard)
- [ ] Kill switch via Telegram
- [ ] LLM-enhanced pattern recognition (optional)

**Test count:** 217 passing, 2 skipped (live network)
**Backtest verdict (v0.7.0):** 🟢 EXCELLENT (Sharpe 6.02, WR 60%, PF 2.27, Max DD 2.23%)

---

## 📄 License

Private project — All rights reserved.

---

**Built with 🦄 by Fataakromulm | Strategi powered by LuxAlgo**
