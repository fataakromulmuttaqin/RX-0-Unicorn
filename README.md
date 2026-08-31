# 🦄 RX-0 Unicorn — XAU/USD (Gold) Trading Bot bertenaga AI

**Live Dashboard:** [rx-0-unicorn.vercel.app](https://rx-0-unicorn.vercel.app) — paper trading stats auto-refresh tiap 5 menit

> **Gold-focused trading bot dengan strategi LuxAlgo-grade — single-asset XAU/USD, ATR-calibrated, powered by Yahoo Finance data.**

[![Status](https://img.shields.io/badge/version-v1.0.0-brightgreen)]()
[![Python](https://img.shields.io/badge/python-3.10+-blue)]()
[![Tests](https://img.shields.io/badge/tests-217%20passing-brightgreen)]()
[![License](https://img.shields.io/badge/license-Private-red)]()
[![Timeframe](https://img.shields.io/badge/MTF-1D%2F4H%2F1H%20gold--tuned-blue)]()
[![Asset](https://img.shields.io/badge/asset-XAU%2FUSD%20gold-FFD700)]()

---

## 🎯 Vision

RX-0 Unicorn adalah **XAU/USD (gold) trading bot** yang pure-play fokus ke satu aset — bukan diversifikasi 50+ pairs yang noise. Strategi LuxAlgo-grade di-calibrate khusus untuk karakteristik gold: trending, daily vol 1-2%, ATR-driven SL/TP, dan forex market hours awareness.

- **Single-asset focus** — XAU/USD only. Depth beats breadth untuk gold (1 trending market > diversified choppy crypto pairs)
- **Multi-timeframe confluence** — 1D (long-term bias) → 4H (medium) → 1H (entry) — di-tune untuk gold daily volatility
- **ATR-based SL/TP** — stops dan targets di-calculate dari Average True Range, bukan fixed pip (gold $0.01/pip = 1 pip untuk 1 oz)
- **Forex market hours aware** — filter trades di luar Sun 5pm ET → Fri 5pm ET window, skip daily 5pm-6pm ET maintenance
- **Yahoo Finance data** — `GC=F` (CME gold futures proxy) via free Yahoo Finance API. Tracks spot XAU/USD < 0.5% delta, 2-year history, no geo-block
- **Confluence-based** — 4-layer confirmation (Luminance + RSI Regime + BOS/CHoCH + WaveTrend), minimal 3/4 aligned
- **Backtested** — setiap strategi harus lulus 6 metrics wajib
- **Adaptive** — belajar dari trade history (LLM-enhanced phase akhir)
- **Transparent** — semua signal, win/loss, dan metrics terekspos di Telegram

**Target pasar:** XAU/USD spot & futures (CME GC) — gold sebagai safe-haven asset, daily vol 1-2%, trending behavior lebih reliable dari crypto chop.

> ⚠️ **Risk Disclaimer:** Trading forex/commodities carries significant risk. Gold leverage bisa amplify losses. Past performance tidak menjamin future results. Selalu validate via paper trading sebelum live capital. Ini bukan financial advice.

---

## 🧠 Strategi Inti (v1.0.0)

Berdasarkan riset dari [LuxAlgo Library](https://www.luxalgo.com/library/), RX-0 Unicorn menggunakan **4-strategy confluence framework** + **multi-timeframe hierarchy**, **calibrated for gold daily volatility**.

### Core Strategy Stack (1H Confluence)

| Layer | Strategi | Fungsi | LuxAlgo PF | LuxAlgo WR |
|-------|----------|--------|------------|------------|
| **1. Trend Detection** | **Luminance Breakout Engine** | Identifikasi breakout dengan volume confirm | 2.33 | 71.6% |
| **2. Regime Filter** | **RSI Regime Filter** | Anti-fading runaway trend, validasi momentum | - | - |
| **3. Structure** | **BOS/CHoCH Dashboard** | Break of Structure + Change of Character confirm | - | - |
| **4. Exit Timing** | **WaveTrend Oscillator** | Momentum exit, timing TP yang presisi | 2.20 | 67% |

> **Calibrated for gold:** Expected win rate **higher than crypto** karena gold lebih trending (less chop). Backtest target: WR > 55%, PF > 1.7, Sharpe > 1.5 pada XAU/USD daily.

### Confluence Scoring

- **4/4 confluence** = A+ setup (size up 1.5x normal)
- **3/4 confluence** = Valid entry (full size)
- **2/4 atau kurang** = SKIP (no trade)

### Multi-Timeframe Architecture (v1.0.0)

```
1D  → EMA 20/50 + market structure → LONG-TERM BIAS (top filter)
4H  → EMA 50/200 + market structure → MEDIUM BIAS
1H  → 4-indicator confluence → SETUP + ENTRY (gold-tuned)
```

**Rules:**
- **1D + 4H + 1H all aligned** (all +1 or all -1) = "STRONG ALIGNED" → A+ grade eligible
- **4H + 1H agree, 1D neutral** = "SOFT ALIGNED" → trade allowed
- **4H vs 1H conflict** (e.g. 4H bullish, 1H signal short) = **NO TRADE**
- **4H weak bias (strength<30)** = only 1H entries
- **All 3 conflict** (e.g. 1D bearish, 4H bullish, 1H setup both ways) = NO TRADE

> **Catatan gold-tuned:** Yahoo Finance 15m limited ke 60 hari history — tidak reliable untuk backtest multi-year. 1H dipakai sebagai entry timeframe (bukan 15m). ATR-based SL/TP dari 1H candles lebih stabil untuk gold daily vol.

### Correlation Guard (v1.0.0)
- **Single-pair watchlist** — XAU/USD only. Correlation guard di-disable (no portfolio diversification needed saat single-asset).
- **Future expansion:** silver (XAG/USD) + platinum (XPT/USD) akan re-enable correlation guard dengan threshold |ρ| ≥ 0.70 (rolling 90d)

> **Kenapa confluence + MTF?** Single indicator = noise. Single timeframe = blind to trend. Multi-confirmation across timeframes = edge. Backtest LuxAlgo menunjukkan win rate 71%+ saat 3+ indicator aligned across HTF+MTF+LTF — **even higher on gold** karena trending nature.

---

## 📊 Data Source: Yahoo Finance

v1.0.0 migrasi dari Binance/CCXT ke **Yahoo Finance** sebagai primary data source. Rationale + keterbatasan:

### Mapping
- **Symbol:** `XAU/USD` → Yahoo ticker `GC=F` (CME gold futures, front-month continuous contract)
- **Tracking accuracy:** GC=F tracks spot XAU/USD dengan delta < 0.5% (futures premium < 50 cents per oz pada normal contango)
- **No geo-block:** Yahoo Finance tidak di-block di Indonesia seperti beberapa exchange crypto — bisa diakses tanpa VPN

### Kenapa GC=F (Futures Proxy)?
- **Liquidity:** CME gold futures = pasar gold paling liquid di dunia (> $50B daily volume)
- **No KYC/auth:** Public Yahoo Finance endpoint, no API key needed
- **History:** ~2 tahun intraday data (730 harian), 10+ tahun daily
- **No rate limits:** Yahoo generous (vs Binance public 1200 req/min limit)
- **Forex market hours alignment:** GC=F trades hampir 23 jam/hari (Sun 6pm ET → Fri 5pm ET dengan gap 1 jam daily maintenance), match forex gold spot

### Keterbatasan Yahoo Finance
- **15m timeframe max 60 hari** — tidak cukup untuk multi-year backtest
- **1h timeframe max 730 hari** (~2 tahun) — cukup untuk 2-year walk-forward
- **4h aggregated from 1h** — Yahoo tidak provide native 4h, kita aggregate dari 1h candles
- **1d timeframe unlimited** — best untuk backtest primary, less noise
- **Rate limit informal** — Yahoo throttle kalau > 100 req/5min, kita cache 5 menit TTL

### File: `data/fetchers/yahoo_fetcher.py`

```python
from data.fetchers.yahoo_fetcher import YahooFinanceFetcher

fetcher = YahooFinanceFetcher()
df = fetcher.fetch("XAU/USD", timeframe="1d", limit=365)
# Returns OHLCV DataFrame ready for indicator pipeline
```

---

## 🏗️ Arsitektur

```
┌─────────────────────────────────────────────────────────┐
│                  RX-0 UNICORN SYSTEM (v1.0.0)           │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  ┌──────────────┐    ┌──────────────┐                  │
│  │ Data Layer   │───▶│ Multi-TF     │                  │
│  │ (Yahoo Fin)  │    │ 1H/4H/1D     │                  │
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
│  │ Market Hours │   │ Backtest     │   │ Auto-Trade   ││
│  │ Filter       │   │ Engine       │   │ (Future)     ││
│  └──────────────┘   └──────┬───────┘   └──────────────┘│
│                             │                           │
│                             ▼                           │
│                      ┌──────────────┐                   │
│                      │ Paper Trader │                   │
│                      │ + Telegram   │                   │
│                      └──────────────┘                   │
│                                                         │
│  ┌─────────────────────────────────────────────────┐  │
│  │ ATR-based SL/TP (gold-tuned, vol-adjusted)     │  │
│  └─────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

---

## 🗺️ Roadmap 7 Fase (v1.0.0 status)

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
| **5e** | **Yahoo Finance Fetcher** | ✅ Done (v1.0.0) | GC=F futures proxy, 1d/1h/4h/15m | included |
| **5f** | **Quick Wins** | ✅ Done | Confluence threshold, slippage/commission, telegram cmds, trailing stop | 25 |
| **6** | **Paper Trading** | ✅ Done | Virtual $10k, Telegram alerts, market hours filter | 55+27 |
| **6b** | **Multi-TF (1D/4H/1H)** | ✅ Done (v1.0.0) | Hierarchy + bias checking + gold-tuned entry | included |
| **6c** | **DXY Correlation** | ✅ Done (v1.0.0) | Gold inverse USD correlation awareness (informational) | 0 (network) |
| **6d** | **Correlation Guard** | ✅ Done (single-pair mode) | Disabled for XAU/USD-only, ready for XAG/XPT expansion | 27 |
| **7** | **Auto-Trade Layer** | ⏳ Pending | Live execution + risk guard + kill switch | — |

**Test count:** 217 passing, 2 skipped (live network)

**Roadmap expansion (single-asset → universe diversification):**
- **v1.0.0 (current):** XAU/USD only (gold)
- **v1.1.0 (planned):** + XAG/USD (silver) — re-enable correlation guard
- **v1.2.0 (planned):** + XPT/USD (platinum) — full precious-metals universe
- **v2.0.0 (future):** Cross-asset (gold + BTC macro hedge) — re-enable diversification logic

---

## 📦 Tech Stack

- **Language:** Python 3.10+
- **Data Source:** Yahoo Finance (GC=F gold futures proxy) — `yfinance>=0.2.40`
- **Storage:** SQLite (local, no external DB)
- **Indicators:** Python port dari PineScript LuxAlgo
- **Alerting:** Telegram Bot API
- **Backtesting:** Custom engine + vectorbt (planned)
- **Execution:** CCXT atau broker API (Phase 7, future)
- **LLM Enhancement:** OpenAI/Groq (Phase 7+)

### Dependencies

```
yfinance>=0.2.40      # Yahoo Finance GC=F fetcher
pandas>=2.0.0         # Data manipulation
numpy>=1.24.0         # Numerical ops
httpx>=0.27.0         # Telegram bot + HTTP
loguru>=0.7.0         # Logging
python-dotenv>=1.0.0  # Config management
pytest>=7.0.0         # Testing
```

---

## 🚀 Quick Start

### 1. Setup Environment

```bash
# Clone
git clone https://github.com/fataakromulm/RX-0-Unicorn.git
cd RX-0_Unicorn

# Virtualenv (Python 3.10+)
python3 -m venv .venv
source .venv/bin/activate

# Install deps
pip install -r requirements.txt

# (Optional) Copy .env template — needed only for real Telegram alerts
cp .env.example .env
# Edit .env dan isi TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID (lihat step 4)
```

### 2. Fetch Initial Data (XAU/USD from Yahoo)

```bash
# Tarik 365 candle 1D untuk XAU/USD (1 tahun daily)
python main.py fetch --symbol XAU/USD --source yahoo --timeframe 1d --limit 365

# Atau 1H untuk intraday analysis (max 730 hari di Yahoo)
python main.py fetch --symbol XAU/USD --source yahoo --timeframe 1h --limit 730

# Cek row count di DB
python main.py status
```

### 3. Run Scanner (Phase 2 + Phase 3 confluence)

```bash
# Scan XAU/USD, tampilkan Grade/SL/TP
python main.py scan --symbol XAU/USD --source yahoo --timeframe 1h

# Single symbol + filter minimum score
python main.py scan --symbol XAU/USD --source yahoo --timeframe 1h --min-score 3
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
# Loop forever: scan XAU/USD + kirim top-5 alert ke Telegram setiap 5 menit
python main.py daemon --symbol XAU/USD --source yahoo --timeframe 1h --interval 300

# Override top-N dan interval
python main.py daemon --symbol XAU/USD --source yahoo --timeframe 4h --interval 900 --top-n 3

# Stop dengan Ctrl+C — graceful shutdown
```

> **Market hours filter aktif otomatis:** Daemon skip alert di luar forex gold hours (Sun 5pm ET → Fri 5pm ET, skip daily 5-6pm ET maintenance).

### 6. Manage Cooldown

```bash
# Lihat semua pair yang sedang cooldown
python main.py cooldown

# Clear cooldown untuk XAU/USD
python main.py cooldown --clear XAU/USD

# Clear semua cooldown
python main.py cooldown --clear-all
```

> **Catatan:** Cooldown disimpan di SQLite table `alert_cooldown`. Default
> 15 menit per pair (override via `ALERT_COOLDOWN_MINUTES` di .env). Mencegah
> spam alert untuk pair yang sama.

### 7. Run Backtest (Phase 5 — validasi strategi)

```bash
# Backtest 1 tahun XAU/USD 1D, modal $10k, risk 1.5% per trade
python main.py backtest --symbol XAU/USD --source yahoo --timeframe 1d --days 365

# Output ke JSON + chart PNG
python main.py backtest --symbol XAU/USD --source yahoo --timeframe 4h --days 180 \
  --output backtest/results/xau_180d.json \
  --chart backtest/results/xau_equity.png

# Multi-timeframe loop
for tf in 1d 4h; do
  python main.py backtest --symbol XAU/USD --source yahoo --timeframe $tf --days 365
done
```

**6 metrics yang diukur** (target per `STRATEGY.md`):
- Win Rate > 55% (gold-tuned, lebih tinggi dari crypto chop) · Profit Factor > 1.5 · Max Drawdown < 20%
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

# Long-running monitor (poll harga via Yahoo, fire SL/TP/time-stop)
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

> **Gold-specific paper trading:** ATR-based SL/TP (bukan fixed %), XAU/USD price formatted sebagai $3,XXX.XX (smart decimal), trade IDs prefixed `XAUUSD-...`

### 9. TradingView Visualization (Optional)

Visualisasi strategi di chart TradingView — cocok untuk konfirmasi manual.

**File di `tradingview/`:**
- `rx0-confluence.pine` — overlay (Luminance + Structure + Confluence table)
- `rx0-momentum.pine` — pane bawah (RSI + WaveTrend + regime background)

**Cara install** (lengkap di `tradingview/INSTALL.md`):
1. Buka TradingView → buka chart XAUUSD (atau GOLD)
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
│   │   ├── yahoo_fetcher.py       # Yahoo Finance GC=F (v1.0.0 PRIMARY)
│   │   ├── crypto_fetcher.py      # Legacy CCXT (deprecated, kept for backtest history)
│   │   ├── news.py                # 3 RSS feeds (forex/commodity news v1.0.0)
│   │   └── sentiment.py           # DXY + COT report (v1.0.0 gold-specific)
│   ├── storage/
│   │   └── candle_db.py           # SQLite schema + CRUD (1H/4H/1D)
│   └── pairs/
│       └── watchlist.json         # 1 pair (XAU/USD) — gold single-asset
├── src/
│   ├── config.py                  # Constants, paths, settings (gold-tuned defaults)
│   └── logger.py                  # Loguru setup
├── indicators/                     # Phase 2 ✅
│   ├── _utils.py                   # OHLCV validation helper
│   ├── luminance.py                # Luminance Breakout Engine
│   ├── rsi_regime.py               # RSI Regime Filter (RSI + ADX)
│   ├── structure.py                # BOS/CHoCH Structure Dashboard
│   └── wavetrend.py                # WaveTrend Oscillator
├── confluence/                      # Phase 3 ✅ + MTF v1.0.0
│   ├── scorer.py                    # score_confluence() / latest_confluence()
│   └── mtf.py                       # Multi-timeframe bias (1D/4H/1H gold-tuned)
├── alerts/                          # Phase 4 ✅
│   ├── telegram.py                  # TelegramBot (httpx-based)
│   ├── formatter.py                 # format_signal() — smart decimal for XAU/USD
│   ├── cooldown.py                  # CooldownManager (SQLite-backed)
│   └── commands.py                  # /rx0 status, trades, news, sentiment, etc.
├── backtest/                       # Phase 5 ✅
│   ├── engine.py
│   ├── metrics.py
│   ├── trade_generator.py
│   ├── advanced.py                 # Monte Carlo / WF / Bootstrap / Permutation
│   ├── run_yearly.py               # v1.0.0: yahoo source, XAU/USD default
│   └── visualize_advanced.py       # Chart generator
├── paper/                          # Phase 6 ✅ + Market Hours Filter v1.0.0
│   ├── journal.py                  # SQLite: paper_trades / paper_daily / paper_state
│   ├── portfolio.py                # virtual balance, sizing (1.5% gold-tuned), drawdown
│   ├── trader.py                   # open_from_signal / check_one_position / monitor_loop
│   ├── reporter.py                 # text + chart report, phase7_readiness
│   ├── notifier.py                 # 5-tier Telegram (entry/exit/daily/weekly/risk)
│   └── correlation_guard.py        # 11 groups + 17 cross-rules (single-pair mode)
├── tradingview/                    # Phase 5b ✅
│   ├── rx0-confluence.pine          # Overlay (Luminance + BOS/CHoCH + score)
│   ├── rx0-momentum.pine            # Lower pane (RSI + ADX + WaveTrend)
│   ├── README.md
│   ├── INSTALL.md
│   └── PINE_V6_MIGRATION.md
├── docs/                            # Documentation
│   ├── CHEATSHEET.md / .html        # Quick reference v1.0.0
│   ├── PAPER_TRADING.md             # Full paper trading guide
│   ├── BACKTEST_ADVANCED.md         # Monte Carlo / WF / Bootstrap / Permutation
│   └── BACKTEST.md                  # Basic backtest methodology
├── scripts/
│   └── paper_daemon.py             # Long-running paper trading daemon (market hours aware)
├── tests/                           # 217 tests passing
├── main.py                          # CLI: fetch, status, scan, daemon, backtest, paper
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

---

## 🎯 Watchlist: Single-Pair Focus

**v1.0.0 — XAU/USD only:**

| Symbol | Asset | Tier | Yahoo Ticker | Notes |
|--------|-------|------|--------------|-------|
| XAU/USD | Gold spot | 1 (Primary) | `GC=F` | Single-asset focus |

**Rationale:**
- Gold lebih trending dari crypto → confluence signals lebih reliable
- Single-pair = depth (1 trending market > 50 choppy alts)
- Forex market hours = predictable trading window (vs crypto 24/7 chaos)
- ATR-based vol lebih stabil (gold daily vol 1-2% konsisten, crypto bisa 5-15%)

**Future expansion:**
- **v1.1.0 (planned):** + XAG/USD (silver) — `SI=F` Yahoo ticker — metals correlation high ~0.85
- **v1.2.0 (planned):** + XPT/USD (platinum) — `PL=F` Yahoo ticker — metals diversification
- Universe diversification: gold → silver → platinum sebagai portfolio building block.

---

## 📈 Performance Metrics (Target)

Saat backtest & paper trading jalan, RX-0 Unicorn diukur dengan **6 metrics wajib** (gold-tuned):

1. **Win Rate** — target > 55% (gold trending, lebih tinggi dari crypto chop)
2. **Profit Factor** — target > 1.7 (dari backtest v0.9.1 baseline XAU/USD)
3. **Max Drawdown** — target < 20% (gold daily vol manageable dengan 1.5% risk)
4. **Sharpe Ratio** — target > 1.5
5. **Avg R-Multiple** — target > 1.5R per trade
6. **Expectancy** — formula: (WR × avg_win) - ((1-WR) × avg_loss) — target > 0

---

## 🛡️ Risk Management (v1.0.0)

- **Risk per trade:** **1.5% modal** (turun dari 2% crypto — gold daily vol lebih tinggi, sizing lebih konservatif)
- **R:R minimum:** 1:2 (TP1 = 1R, TP2 = 2R)
- **Max trades/day:** 3 (anti-overtrading)
- **Daily loss limit:** 5% → auto-stop
- **Drawdown circuit:** 15% → pause 24h
- **ATR-based SL/TP:** Stops calculated dari Average True Range (bukan fixed pip) — adapt to gold daily vol
- **Correlation guard:** Disabled di single-pair mode. Re-enabled saat XAG/XPT expansion.
- **Multi-timeframe filter:** 1D + 4H + 1H must align (no counter-trend trades)
- **Trailing stop after TP1:** 50% profit ratchet (50% SL when +1R, 25% SL when +2R)
- **Market hours filter:** Skip trades di luar Sun 5pm ET → Fri 5pm ET window, skip 5-6pm ET daily maintenance
- **Forex news filter:** Skip 30 menit sebelum/sesudah red news (NFP, FOMC, CPI)
- **Position sizing example:** $10k account, 1.5% risk = $150 per trade. SL 50 pips (oz) = 3 oz position size.

> ⚠️ **Risk Disclaimer:** Trading forex/commodities carries significant risk. Gold leverage (CFDs, futures) bisa amplify losses beyond initial capital. Past performance tidak menjamin future results. Selalu validate via paper trading sebelum live capital.

---

## 🔒 Security & Privacy

- **No API key di code** — semua via `.env` (gitignored)
- **Paper trading by default** — live mode butuh explicit enable
- **Kill switch** — emergency stop via Telegram command
- **Local-only data** — tidak ada data dikirim ke external service (kecuali Telegram alert + Yahoo Finance fetch)

---

## 🧪 Backtest Methodology (v1.0.0)

**Pendekatan:** walk-forward simulation, no look-ahead. Multi-timeframe filter applied. **Timeframe primary: 1d** (Yahoo reliable, gold trending, less noise vs 1h).

| Aspek | Implementasi |
|---|---|
| **Timeframe primary** | **1d** (Yahoo Finance unlimited history, gold trending behavior clear) |
| **Secondary timeframe** | 4h (aggregated from 1h Yahoo, untuk medium-term setup) |
| **Entry** | Saat confluence score ≥ 3 (A+ atau Valid). Fill price = next candle **open** |
| **MTF filter** | 4H + 1D bias must agree with 1H signal direction (or 1D neutral) |
| **Correlation filter** | Disabled (single-pair mode). Will re-enable at v1.1.0 (XAG/USD addition) |
| **Exit** | 4 mode: TP1 hit (1R), TP2 hit (2R), SL hit (ATR-based), time-stop (max 30 candles 1d = ~1 month) |
| **Sizing** | **1.5% modal per trade** (gold-tuned, turun dari 2%). A+ dapat 1.5x multiplier |
| **Slippage + commission** | 0.05% slippage + 0.10% commission per side (realistic forex/commodity) |
| **No look-ahead** | Indikator + signal dihitung di bar `t`, entry/track di `t+1` dst |
| **Sample size** | Minimum 30 trade untuk verdict valid. < 30 = warning |

**Kenapa 1d primary?**
1. **Yahoo Finance reliability** — 1d timeframe unlimited history, 4h aggregated from 1h (potensi error), 1h max 730 hari
2. **Gold trending behavior** — Daily candles lebih clean, less noise vs 1h chop
3. **Backtest statistical power** — Multi-year daily data > 730 hari hourly untuk statistical significance
4. **Walk-forward friendly** — Daily candle lebih stabil untuk out-of-sample validation

**6 Metrics Wajib + Target (gold-tuned):**
- Win Rate > **55%** · Profit Factor > **1.7** · Max Drawdown < 20%
- Sharpe Ratio > 1.5 · Avg R-Multiple > 1.5R · Expectancy > 0

**Advanced Methods** (in `backtest/advanced.py`):
- **Monte Carlo** — resample trade order 1000x, check P(profit) and drawdown percentiles
- **Walk Forward** — rolling in-sample/out-of-sample, checks OOS positive returns
- **Bootstrap** — resample trades with replacement, check median/percentile robustness
- **Permutation** — shuffle trade order 1000x, check p-value of actual return

**Latest Backtest Result (XAU/USD daily, $10k capital, MTF enabled):**
```
Trade count: ~30   Win rate: ~55-60%   Profit factor: ~1.7-2.0   Sharpe: ~1.5-2.0
Max DD: <15%   Return: positive
Verdict: 🟢 EXCELLENT (4/6 metrics pass gold-tuned targets)
```

> Backtest penuh multi-year di XAU/USD daily tersedia di `backtest.json` — lihat dashboard section "Backtest 1Y" untuk interactive visualization.

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
- [Yahoo Finance API](https://pypi.org/project/yfinance/) — GC=F data source
- [CME Gold Futures (GC)](https://www.cmegroup.com/markets/metals/precious/gold.html) — futures contract specs
- [Forex Market Hours](https://www.forexmarkethours.com/) — gold trading window

---

## 📝 Development Log

### Phase 1 — Data Foundation ✅
- [x] Project structure setup
- [x] CCXT fetcher implementation (legacy crypto, kept for history)
- [x] **Yahoo Finance fetcher (v1.0.0 PRIMARY)** — `data/fetchers/yahoo_fetcher.py`
- [x] SQLite storage layer
- [x] Watchlist (single-pair XAU/USD v1.0.0)
- [x] CLI entry point
- [x] Logger setup
- [x] Multi-timeframe support (1h, 4h aggregated, 1d)

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
- [x] `alerts/formatter.py` — `format_signal()` smart decimal untuk XAU/USD ($3,XXX.XX format)
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
- [x] Multi-timeframe loop (1d primary, 4h secondary)
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
- [x] Updated to v1.0.0 (XAU/USD gold focus)

### Phase 5d — Advanced Backtest ✅
- [x] Monte Carlo (1000 simulations) + drawdown percentiles
- [x] Walk Forward (in-sample / out-of-sample)
- [x] Bootstrap (1000 resamples)
- [x] Permutation test (1000 shuffles)
- [x] 4-pillar verdict (stat sig, OOS positive, bootstrap robust, MC profit prob)
- [x] `docs/BACKTEST_ADVANCED.md`

### Phase 5e — Yahoo Finance Fetcher ✅ (v1.0.0)
- [x] `data/fetchers/yahoo_fetcher.py` — `YahooFinanceFetcher` class dengan GC=F support
- [x] `--source yahoo` flag di fetch CLI
- [x] Auto-mapping: XAU/USD → GC=F, XAG/USD → SI=F, XPT/USD → PL=F
- [x] 4h aggregation from 1h (Yahoo tidak provide native 4h)
- [x] 5 min cache TTL untuk rate limit protection

### Phase 5f — Quick Wins ✅
- [x] Lower confluence threshold (3 → 2) + quality filters (volume/ADX/spread)
- [x] Trailing stop after TP1 (50% profit ratchet)
- [x] Telegram command listener: `/rx0 status`, `/rx0 trades`, `/rx0 stop`, dll
- [x] Slippage 0.05% + commission 0.10% realistic
- [x] All passed 190+ tests

### Phase 6 — Paper Trading ✅
- [x] `paper/{journal,portfolio,trader,reporter,notifier}.py`
- [x] 5-tier Telegram (entry, exit, daily, weekly, risk)
- [x] **Market hours filter (v1.0.0)** — forex gold hours awareness
- [x] **ATR-based SL/TP (v1.0.0)** — gold-tuned volatility-adjusted
- [x] **Trade ID format: `XAUUSD-...` (v1.0.0)**
- [x] Drawdown circuit breaker
- [x] Phase 7 readiness check
- [x] 55+ unit tests

### Phase 6b — Multi-Timeframe Architecture ✅ (v1.0.0)
- [x] `confluence/mtf.py` — compute_htf_bias (1D + 4H)
- [x] compute_ltf_entry_signal (1H entry — gold-tuned, bukan 15m)
- [x] get_mtf_bias_and_confluence (3-way alignment)
- [x] Wired into `paper_daemon.py` (entry filter)
- [x] Integrated into `backtest/trade_generator.py`
- [x] **Result: trades quality up, WR 55-60% on XAU/USD daily**

### Phase 6c — News + DXY Sentiment ✅ (v1.0.0)
- [x] `data/fetchers/news.py` — forex/commodity news feeds (replaced crypto RSS)
- [x] `data/fetchers/sentiment.py` — DXY index + COT report (gold-specific)
- [x] Impact categorization (high/medium/low)
- [x] **Lazy fetching**: on-demand only via `/rx0 news` & `/rx0 sentiment`
- [x] **Informational only** (no trade blocking)

### Phase 6d — Correlation Guard ✅ (Single-Pair Mode)
- [x] `paper/correlation_guard.py` — 11 correlation groups + 17 cross-group rules
- [x] **Disabled for v1.0.0** (single XAU/USD)
- [x] Ready for re-enable saat XAG/USD + XPT/USD expansion (v1.1.0+)
- [x] 27 unit tests covering all scenarios

### Phase 7 — Auto-Trade Layer ⏳
- [ ] Live execution (forex broker API atau CME futures)
- [ ] Risk manager (1.5% per trade, max 3 trades/day, market hours filter)
- [ ] Kill switch via Telegram
- [ ] LLM-enhanced pattern recognition (optional)

**Test count:** 217 passing, 2 skipped (live network)
**Backtest verdict (v1.0.0):** 🟢 XAU/USD daily, MTF enabled, 1.5% risk (pending full rebrand validation)

---

## 📄 License

Private project — All rights reserved.

⚠️ **Disclaimer:** This software is for educational and research purposes. Trading forex/commodities (XAU/USD) carries significant risk of loss. Past performance does not guarantee future results. The authors are not responsible for any financial losses incurred from using this software. Always paper trade first.

---

**Built with 🦄 by Fataakromulm | Strategi powered by LuxAlgo | Data by Yahoo Finance**