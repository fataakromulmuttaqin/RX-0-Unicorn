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

## [0.6.0] — 2026-08-29

### Phase 6: Paper Trading ✅

**Module: `paper/`** — simulates real-time trading with no real money.
Validates the confluence strategy in real time before greenlighting
Phase 7 (live trading).

#### Added
- ✅ `paper/journal.py` — SQLite persistence (`paper_trades`,
  `paper_daily`, `paper_state`) with WAL + daily aggregation
- ✅ `paper/portfolio.py` — virtual $10k balance, position-sizing
  math, drawdown tracking, 4 risk gates (drawdown circuit, daily
  loss limit, max open positions, max daily trades)
- ✅ `paper/trader.py` — high-level orchestrator
  (`open_from_signal`, `check_one_position`, `monitor_loop`,
  `ccxt_price_fetcher`)
- ✅ `paper/reporter.py` — text + PNG equity-curve report, weekly
  summary, `phase7_readiness()` greenlight check
- ✅ `paper/notifier.py` — **5-tier Telegram notification system**
  (entry / exit / daily / weekly / risk) with graceful degradation
  when `TELEGRAM_BOT_TOKEN` is empty
- ✅ `main.py paper` CLI: 10 subcommands
  (`start`, `status`, `scan-and-trade`, `monitor`, `close`,
  `close-all`, `report`, `journal`, `daily-digest`, `weekly-report`)
- ✅ `src/config.py` — 22 new `PAPER_*` constants (initial balance,
  risk per trade, drawdown circuit, TP1/TP2 ratios, time-stop,
  Phase 7 thresholds, reports dir)
- ✅ `docs/PAPER_TRADING.md` — full architecture + lifecycle +
  schema + sizing math documentation
- ✅ `tests/test_paper.py` — **55 unit tests** (journal, portfolio,
  trader, reporter, notifier; tmp_path isolated DB; MagicMock
  TelegramBot)

#### Config (new in `src/config.py`)
- `PAPER_INITIAL_BALANCE = 10_000`
- `PAPER_RISK_PER_TRADE = 0.02`
- `PAPER_MAX_DRAWDOWN_CIRCUIT = 0.15`
- `PAPER_DAILY_LOSS_LIMIT = 0.05`
- `PAPER_TP1_RR_RATIO = 1.0` · `PAPER_TP2_RR_RATIO = 2.0`
- `PAPER_TIME_STOP_SECONDS = 14_400` (4h)
- `PAPER_PHASE7_MIN_TRADES = 30` (greenlight threshold)
- See `docs/PAPER_TRADING.md` for the full table.

#### Tests
```
$ python -m pytest tests/test_paper.py -v
============================== 55 passed in 1.00s ==============================
```

---

## [0.7.1] — 2026-08-30

### Journal Export + Web Dashboard Upgrade + Vercel Deploy

#### Added
- **`export_journal.py`** — Export SQLite paper-trades → `journal.json` untuk dashboard
  - Skema-aware: kolom DB asli (`direction`, `pnl_r_multiple`, `status`) dipetakan ke JSON
  - Tambahan `state` (balance, initial_balance, peak_equity) & `daily` (per-day PnL aggregate)
  - Epoch timestamp → ISO 8601 otomatis (auto-detect detik/milidetik)
  - Default `--db data/storage/paper_trades.db` jadi tinggal `python export_journal.py`
  - Scan table opsional (silent skip kalau gak ada)
- **`rx0-unicorn.html`** — Dark cyber dashboard
  - 12-stat strip: Total Trade, WR, P/L USD, P/L %, Avg R, Max DD, Saldo Saat Ini, Peak Equity, Trade Aktif, Win Terbesar, Loss Terbesar, Profit Factor
  - **Performa per Symbol** bar chart (auto-sorted by PnL)
  - **Performa per Grade** cards (A+ / Valid / Skip → WR, P/L, Avg R)
  - **Daily P/L** 7-hari bars (dari `paper_daily`)
  - Bug fix: `normResult()` prioritas `status` dari DB
  - Bug fix: equity chart pakai `state.initial_balance` bukan hardcoded 10000
  - Tambah relative time ("Diperbarui 5 menit lalu") di header
  - Responsive grid (12→3 cols @ tablet, 2 @ mobile)
- **Cron job** `d3c7327530ec` — silent `*/5 * * * *` regenerasi `journal.json`, no Telegram notif kecuali error
- **Vercel deploy** — project `rx0-unicorn-dashboard` (ID `prj_eRO3JZL0MWRCFHENnWDWLfMz7HuN`)
  - Production URL: **https://rx0-unicorn-dashboard.vercel.app**
  - GitHub integration aktif — push ke `main` auto-deploy
  - SSO protection dimatikan (Teams plan butuh `ssoProtection: null`)
  - Repo `fataakromulmuttaqin/RX-0-Unicorn` linked via API (`POST /v1/projects/{id}/link`)

---

## [0.7.0] — 2026-08-30

### Multi-Timeframe + Correlation Guard + News/Sentiment

#### Added
- **`confluence/mtf.py`** — Multi-timeframe analysis module
  - `compute_htf_bias(df, timeframe)` — bias from EMA + market structure (4H/1D)
  - `compute_ltf_entry_signal(df)` — 15m entry precision (EMA 9/21 cross, RSI(7), volume)
  - `get_mtf_bias_and_confluence(pair)` — 3-way alignment check (1D + 4H + 1H)
  - `get_15m_entry_signal(pair, direction)` — LTF entry validation
  - 3-tier hierarchy: 1D (long-term bias) → 4H (medium) → 1H (confluence) → 15m (entry)
  - `strongly_aligned` flag for A+ grade eligibility

- **`paper/correlation_guard.py`** — 11 correlation groups + 17 cross-group rules
  - Groups: l1_majors, l1_alts, l2s, defi, memes, ai, privacy, gamefi, infra, rwa, exchange
  - 70+ pairs mapped to groups
  - `get_group(symbol)`, `are_correlated(s1, s2)`, `check_correlation_limit(...)`
  - `get_correlation_summary(open_positions)` for portfolio breakdown
  - Max 2 correlated positions per `STRATEGY.md` line 162

- **`data/fetchers/news.py`** — News aggregation from 3 RSS feeds
  - CoinDesk, Cointelegraph, The Block
  - Impact categorization (high/medium/low)
  - Currency tagging (BTC, ETH, SOL, etc)
  - `fetch_all_news()`, `get_today_news(currencies=...)`, `format_news_for_telegram(...)`

- **`data/fetchers/sentiment.py`** — Market + per-coin sentiment
  - CoinGecko batch `/coins/markets` (1 call = 250 coins)
  - Alternative.me Fear & Greed Index
  - Price-action implied sentiment (50 + 2×7d% change)
  - 1H cache + 10 req/min sliding window rate limiter
  - `get_market_sentiment_summary()`, `get_sentiment_for_symbol(...)`
  - Lazy fetching (on-demand only, no scheduled daemon calls)

#### Integration
- **`paper/portfolio.py`** — `can_open_new_position(symbol=...)` extended
  - Symbol-aware correlation check before opening
  - Telegram Tier 5 notification on correlation block
- **`paper/trader.py`** — passes `symbol=symbol` to risk gate
- **`paper/notifier.py`** — new `notify_risk_breach("correlation_limit", ...)` template
- **`src/config.py`** — `PAPER_MAX_CORRELATED_POSITIONS = 2`
- **`alerts/commands.py`** — new `/rx0 news` & `/rx0 sentiment` commands
- **`scripts/paper_daemon.py`** — removed scheduled news/sentiment fetch (lazy only)
- **`backtest/trade_generator.py`** — pre-loads 4H + 1D bias per pair, filters 1H signals against MTF

#### Performance
- Backtest with MTF enabled: **5 trades, WR 60%, PF 2.27, Sharpe 6.02, Max DD 2.23%**
- Compare to non-MTF: 28 trades, WR 39%, PF 1.25, Sharpe 1.49, Max DD 5.50%
- **MTF dramatically improves quality** — fewer trades, higher WR, better Sharpe, lower DD
- Verdict: 🟢 EXCELLENT (3/4 pillars pass)

#### API Optimization
- CoinGecko batch endpoint: 1 call = 250 coins (was 1 call per coin)
- Sliding window rate limiter: 10 req/min max
- Daily API calls: ~1,440 → ~24 (**-98%**)
- News + sentiment now lazy (on-demand only)

#### Tests
```
$ python -m pytest tests/ -q
217 passed, 2 skipped in 2.21s
```
- New `tests/test_correlation_guard.py` (27 tests)
- Existing tests still pass

---

## [1.0.0] — Planned

### Phase 7: Auto-Trade Layer
- CCXT live execution
- Risk manager (1-2% per trade, max 3 trades/day, correlation guard)
- Kill switch via Telegram
- News filter integration (now informational, may upgrade to blocking)
- LLM-enhanced pattern recognition (optional)

---

**Legend:** ✅ Done | 🟡 In Progress | ⏳ Pending | ❌ Cancelled
