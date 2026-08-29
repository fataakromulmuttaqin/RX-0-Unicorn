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

## [0.2.0] — Planned

### Phase 2: Core Indicator Engine
- Luminance Breakout Engine (Python port dari LuxAlgo)
- RSI Regime Filter
- BOS/CHoCH Structure Dashboard
- WaveTrend Oscillator

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
