# 📋 Changelog — RX-0 Unicorn

All notable changes to this project will be documented here.

Format: [Semantic Versioning](https://semver.org/)

---

## [0.1.0] — 2026-08-29

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

## [0.2.0] — 2026-08-29

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
- Confluence scoring resmi (0-4 + entry rules, position sizing 1.5x untuk A+ setup) dibangun di Phase 3 (lihat [0.3.0] di bawah) — `main.py scan` sekarang memakainya langsung

---

## [0.3.0] — 2026-08-29 (In Progress)

### Phase 3: Confluence Scorer

#### Added
- ✅ `confluence/scorer.py` — `score_confluence()`: skor 0-4 penuh per-bar dari 4 sinyal indikator Phase 2, plus grade (`skip`/`valid`/`A+`) dan `size_multiplier` (0.0/1.0/1.5) sesuai STRATEGY.md
- ✅ `latest_confluence()` — ringkasan bar terakhir (dict, tipe native Python) siap dikonsumsi CLI/alert
- ✅ Risk levels otomatis: `entry_price`, `stop_loss` (dari range breakout / swing structure), `take_profit_1` (1R), `take_profit_2` (2R), `risk_reward`
- ✅ Konstanta scoring baru di `src/config.py`: `CONFLUENCE_MIN_VALID`, `CONFLUENCE_A_PLUS`, `A_PLUS_SIZE_MULTIPLIER`, `VALID_SIZE_MULTIPLIER`, `SKIP_SIZE_MULTIPLIER`, `MIN_RISK_REWARD`
- ✅ `main.py scan` — sekarang pakai Confluence Scorer resmi (bukan preview sederhana lagi), tabel output menampilkan Grade/SL/TP1/TP2
- ✅ `tests/test_confluence.py` — 15 unit tests (score bounds, grade↔score consistency, risk level ordering long/short, edge case no-direction, native type check)

#### Changed
- 🔁 Logic confluence di `main.py cmd_scan` dipindah sepenuhnya ke modul `confluence/` — CLI kini jadi thin wrapper

#### Notes
- Scoring ini mekanis (berbasis 4 sinyal indikator, bukan discretionary "BOS + pullback ke demand zone" penuh dari STRATEGY.md) — cukup untuk backtest awal (Phase 5) dan alerting (Phase 4)
- Total test suite: **52 passed, 2 skipped** (skip = live-network test, butuh akses Binance)

---

## [0.4.0] — 2026-08-29

### Phase 4: Telegram Alert System

#### Added
- ✅ `alerts/telegram.py` — `TelegramBot` class (httpx-based, no python-telegram-bot overhead). `send_message(text)` returns bool. Graceful degradation: kalau `TELEGRAM_BOT_TOKEN` atau `TELEGRAM_CHAT_ID` kosong, log ke console & return False (no crash)
- ✅ `alerts/formatter.py` — `format_signal(latest_confluence_result)` — render dict ke string alert Telegram sesuai template Chastiefol-style. Skip grade → return None (tidak dikirim)
- ✅ `alerts/cooldown.py` — `CooldownManager` (SQLite-backed, table `alert_cooldown(pair TEXT PRIMARY KEY, last_alert_at INTEGER)`). Methods: `should_alert(pair)`, `mark_alerted(pair)`, `clear(pair=None)`, `cleanup_old(max_age_hours=24)`
- ✅ `main.py daemon` — loop forever, scan watchlist, hitung confluence, kirim top-N alert dengan cooldown per pair. Graceful shutdown via SIGINT/SIGTERM. Args: `--timeframe`, `--interval`, `--top-n`
- ✅ `main.py test-alert` — kirim sample alert (placeholder data) untuk verifikasi Telegram config. Print ke console kalau token kosong
- ✅ `main.py cooldown` — list/clear cooldown table. Subcommands: `--clear [PAIR]`, `--clear-all`
- ✅ Konstanta baru di `src/config.py`: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `ALERT_COOLDOWN_MINUTES` (15), `SCAN_INTERVAL_SECONDS` (300), `ALERT_TOP_N` (5), `A_PLUS_EMOJI` ⭐, `VALID_EMOJI` 🟢, `SKIP_EMOJI` ⚪
- ✅ `tests/test_alerts.py` — 35 unit tests (13 cooldown, 13 formatter, 9 telegram httpx mock)
- ✅ `httpx>=0.27.0` ditambahkan ke `requirements.txt`
- ✅ `.env.example` (sudah ada, diperkaya dengan dokumentasi lengkap)

#### Changed
- 🔁 `main.py` subparser sekarang punya 7 command: `status`, `fetch`, `cleanup`, `scan`, `daemon`, `test-alert`, `cooldown`
- 🔁 README Quick Start diperluas dengan step-by-step Telegram bot setup + daemon usage

#### Notes
- Cooldown default 15 menit per pair — override via `ALERT_COOLDOWN_MINUTES` di `.env`
- Daemon ranking: grade A+ diprioritaskan di atas valid; score sebagai tie-breaker
- Total test suite: **87 passed, 2 skipped** (skip = live-network test, butuh akses Binance)
- Sample alert (A+ setup, placeholder data, no token):
  ```
  ⭐ RX-0 SIGNAL — A+ 1H
  ━━━━━━━━━━━━━━━
  Pair:       BTC/USDT
  TF:         1H
  Score:      4/4
  Grade:      A+
  Direction:  LONG
  Entry:      $62,450.00
  SL:         $62,180.00 (-0.43%)
  TP1:        $62,990.00 (+0.86%)
  TP2:        $63,530.00 (+1.73%)
  R:R:        1:2.0 / 1:4.0
  Regime:     trending
  Confluence:
    ✓ Luminance breakout
    ✓ RSI regime aligned
    ✓ BOS confirm
    ✓ WaveTrend cross
  Time:       2026-08-29 06:07 UTC
  ```

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
