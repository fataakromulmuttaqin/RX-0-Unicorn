# 📋 Changelog — RX-0 Unicorn

All notable changes to this project will be documented here.

Format: [Semantic Versioning](https://semver.org/)

---

## [0.1.0] — 2026-08-29 (In Progress)

### Phase 1: Data Foundation

#### Added
- ✅ Project structure dengan clean directory layout
- ✅ CCXT Binance fetcher (public endpoints, no auth)
- ✅ SQLite candle storage dengan proper schema & indexes
- ✅ Watchlist manager (50+ pairs, 4-tier system)
- ✅ CLI entry point (commands: fetch, status, cleanup)
- ✅ Loguru logger setup (file + console, rotation)
- ✅ Multi-timeframe support (5m, 15m, 1h, 4h, 1d)
- ✅ Rate limiting & exponential backoff
- ✅ Pandas DataFrame return format
- ✅ Unit tests untuk fetcher
- ✅ Git repo initialized

#### Stack
- Python 3.10+
- CCXT >= 4.0.0
- Pandas >= 2.0.0
- SQLite (built-in)
- Loguru >= 0.7.0

#### Known Limitations
- Public endpoints only (no live trading yet)
- Single exchange (Binance) — multi-exchange support di Phase 7
- No historical backfill script yet (manual fetch only)

---

## [0.2.0] — 2026-08-29 (In Progress)

### Phase 2: Core Indicator Engine

#### Added
- ✅ `indicators/luminance.py` — Luminance Breakout Engine (range breakout + volume confirm, filter consolidation minimum)
- ✅ `indicators/rsi_regime.py` — RSI Regime Filter (Wilder RSI + ADX regime classification, anti-fade-trend guard)
- ✅ `indicators/structure.py` — BOS/CHoCH Structure Dashboard (fractal swing detection, trend bias tracking)
- ✅ `indicators/wavetrend.py` — WaveTrend Oscillator (LazyBear/LuxAlgo formula, oversold/overbought cross signals)
- ✅ `indicators/_utils.py` — shared OHLCV validation helper
- ✅ `main.py scan` — CLI command baru, jalankan 4 indikator di data tersimpan + confluence score preview (0-4)
- ✅ `tests/test_indicators.py` — 32 unit tests (synthetic OHLCV, edge cases: flat price, insufficient rows, signal bounds, anti-fade-trend check)
- ✅ `numpy` ditambahkan ke requirements.txt

#### Fixed
- 🐛 `tests/test_fetcher.py::_network_available` — sebelumnya hanya cek TCP connect (false positive di lingkungan dengan egress proxy terbatas); sekarang melakukan HTTPS ping sungguhan ke Binance sebelum menjalankan live network test

#### Notes
- Setiap indikator mengembalikan DataFrame dengan kolom `*_signal` (1/-1/0) yang konsisten, siap dikonsumsi Confluence Scorer di Phase 3
- Confluence scoring resmi (0-4 + entry rules, position sizing 1.5x untuk A+ setup) masih Phase 3 — `main.py scan` saat ini hanya preview sederhana (hitung sisi long/short yang paling banyak align)

---

## [0.3.0] — Planned

### Phase 3: Confluence Scorer
- 4-layer scoring logic (0-4)
- Entry validation rules
- Filter out weak signals (< 3/4)

---

## [0.4.0] — Planned

### Phase 4: Telegram Alert System
- Alert format (entry/SL/TP/confluence score)
- Cooldown logic (15min per pair)
- Top 5 ranking
- Daily digest

---

## [0.5.0] — Planned

### Phase 5: Backtest Engine
- Historical replay
- 6 metrics wajib (WR, PF, DD, Sharpe, R-multiple, Expectancy)
- Equity curve visualization
- Monte Carlo simulation (optional)

---

## [0.6.0] — Planned

### Phase 6: Paper Trading
- Dry-run mode (no real money)
- Real-time signal tracking
- Win rate validation
- 2-4 weeks observation period

---

## [1.0.0] — Planned

### Phase 7: Auto-Trade Layer
- CCXT live execution
- Risk manager (1-2% per trade, max 3 trades/day)
- Kill switch via Telegram
- News filter integration
- LLM-enhanced pattern recognition (optional)

---

**Legend:** ✅ Done | 🟡 In Progress | ⏳ Pending | ❌ Cancelled
