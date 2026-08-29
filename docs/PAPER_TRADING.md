# Paper Trading — Phase 6

> **Status:** ✅ Implemented (55 unit tests passing).
> **Mode:** Default. Every signal that would have been live-traded is
> mirrored against a virtual $10,000 portfolio in real time. The same
> confluence engine, the same SL/TP plan, the same risk math — just no
> real money on the wire.

---

## Why this phase exists

Backtest tells you "the strategy worked on 2023 BTC 1h". It does not
tell you "the strategy will work on the BTC 1h candle that closes in 12
minutes, with the slippage, latency, and partial-fill behaviour of the
exchange you're about to point it at".

Phase 6 closes that gap by running the **full** confluence + sizing
pipeline against a paper portfolio for at least **2 weeks / 30
trades**, comparing:

| Metric               | Backtest target | Paper greenlight |
|----------------------|-----------------|------------------|
| Win rate             | ≥ 50%           | ≥ 50% ± 10% tol  |
| Profit factor        | ≥ 1.5           | ≥ 1.0            |
| Max drawdown         | ≤ 20%           | ≤ 20%            |
| Avg R-multiple       | ≥ 1.5R          | ≥ 0R (positive)  |
| Total trades         | (any)           | ≥ 30             |

If the paper run clears every row, Phase 7 (live trading) is
greenlit. If not, we have a real signal that the backtest is
overfit to history, and we go back to tune the confluence weights.

---

## Architecture

```
                  ┌──────────────────┐
                  │   main.py paper  │  CLI entrypoint
                  │  (10 subcommands)│
                  └────────┬─────────┘
                           │
            ┌──────────────┼──────────────┐
            │              │              │
   ┌────────▼─────┐ ┌──────▼──────┐ ┌─────▼──────┐
   │ PaperJournal │ │PaperPortfolio│ │PaperTrader │
   │   (SQLite)   │ │ (sizing/PnL) │ │(orchestrate)│
   └──────────────┘ └──────┬──────┘ └─────┬──────┘
                           │              │
                  ┌────────▼──────────────▼─────┐
                  │   PaperNotifier (5 tiers)    │
                  │   alerts/telegram.py backend │
                  └──────────────────────────────┘
```

### Module map

| File                | Responsibility                                              |
|---------------------|-------------------------------------------------------------|
| `paper/__init__.py` | Public API re-exports                                       |
| `paper/journal.py`  | SQLite persistence: `paper_trades`, `paper_daily`, `paper_state` |
| `paper/portfolio.py`| Virtual balance, position math, drawdown, circuit breaker   |
| `paper/trader.py`   | High-level: `open_from_signal()`, `check_one_position()`, `monitor_loop()` |
| `paper/reporter.py` | Text report, equity-chart PNG, `phase7_readiness()`         |
| `paper/notifier.py` | 5-tier Telegram alerts (entry / exit / daily / weekly / risk) |

---

## CLI

All commands are namespaced under `python main.py paper ...`.

| Command                | What it does                                                |
|------------------------|-------------------------------------------------------------|
| `paper start [--reset]`| Initialize / reset the $10k paper portfolio (idempotent)    |
| `paper status`         | Show balance, equity, drawdown, open positions, risk gates  |
| `paper scan-and-trade` | One-shot: scan → score → open paper positions from confluence |
| `paper monitor`        | Long-running poll loop (CCXT price feed → SL/TP1/TP2 checks) |
| `paper close --id <TID>`| Manually close a specific position by trade_id             |
| `paper close-all`      | Emergency close every open position                         |
| `paper report --days N` | Generate the text + chart report for the last N days       |
| `paper journal [--limit N]`| Inspect raw paper_trades rows                             |
| `paper daily-digest`   | Fire Tier 3 daily summary to Telegram (manual trigger)      |
| `paper weekly-report`  | Fire Tier 4 weekly report + chart to Telegram                |

---

## Position lifecycle

```
   confluence signal
   (score, grade, sl, tp1, tp2)
          │
          ▼
   ┌──────────────────┐
   │  open_from_signal│   ← PaperTrader checks risk gates
   └────────┬─────────┘
            │  log_open_position
            ▼
   ┌──────────────────┐
   │ paper_trades     │  status = 'open'
   │  (SQLite row)    │
   └────────┬─────────┘
            │  monitor_loop polls every N seconds
            ▼
   ┌──────────────────┐
   │ check_one_position│  price vs SL / TP1 / TP2 / time-stop
   └────────┬─────────┘
            │  on trigger → close_trade
            ▼
   ┌──────────────────┐
   │ paper_trades     │  status = 'closed', pnl_usd, pnl_r_multiple
   │  + paper_daily   │  daily aggregation updated
   └──────────────────┘
```

### Exit priority (matches backtest engine)

1. **SL hit + TP1 hit in same bar** → assume SL (pessimistic, like
   the backtest engine — preserved ordering matters for honest PnL).
2. **SL hit** → `exit_reason='sl'`
3. **TP2 hit** → `exit_reason='tp2'`, full close
4. **TP1 hit** → `exit_reason='tp1'`, full close at TP1 (SL moved to
   breakeven if `PAPER_TP1_HIT_BREAKEVEN=True`, but the trade is
   already flat in this paper implementation — TP1 = full close)
5. **Time-stop** (position open > `PAPER_TIME_STOP_SECONDS`,
   default 4h) → `exit_reason='time_stop'`

### Risk gates (enforced before every new entry)

| Gate                | Config                       | Default |
|---------------------|------------------------------|---------|
| Drawdown circuit    | `PAPER_MAX_DRAWDOWN_CIRCUIT` | 15%     |
| Daily loss limit    | `PAPER_DAILY_LOSS_LIMIT`     | 5%      |
| Max open positions  | `PAPER_MAX_OPEN_POSITIONS`   | 3       |
| Max daily trades    | `PAPER_MAX_DAILY_TRADES`     | 3       |

Any gate breach → Tier 5 alert + position refused.

---

## Position sizing math

```
risk_usd   = equity × risk_per_trade × size_multiplier
size_units = risk_usd / |entry - sl|
```

- `risk_per_trade` = 0.02 (2% of equity, default)
- `size_multiplier` = 1.0 for "valid" grade, **1.5 for "A+" grade**

At equity = $10,000, risk = 0.02, A+ signal with SL 5% away:
- `risk_usd = 10000 × 0.02 × 1.5 = $300`
- `size_units = 300 / 5 = 60 units`

---

## 5-tier Telegram notifications

All five tiers share a single `PaperNotifier` that gracefully degrades
to console-log if `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` are
unset (so the daemon can run on a dev box without breaking).

| Tier | Trigger                              | Includes                                |
|------|--------------------------------------|-----------------------------------------|
| 1    | New entry (`open_from_signal`)       | symbol, direction, entry/SL/TP1/TP2, size, score, grade |
| 2    | Exit (`close_trade` with any reason) | P/L $, P/L %, R-multiple, exit reason   |
| 3    | Daily digest (00:05 UTC)             | equity, day P/L, trades, win rate, DD, open count |
| 4    | Weekly report (Sun 23:59 UTC)        | full metrics, top winners/losers, chart |
| 5    | Risk gate breach                     | alert_type + details (DD%, equity, paused_until) |

Tiers 3 and 4 fire automatically from `paper monitor` when the
scheduled time arrives. All five can be triggered manually via the
`paper daily-digest` and `paper weekly-report` subcommands.

---

## Database schema

```sql
CREATE TABLE paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id TEXT UNIQUE NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,         -- 'long' | 'short'
    entry_time INTEGER NOT NULL,
    entry_price REAL NOT NULL,
    exit_time INTEGER,
    exit_price REAL,
    sl REAL NOT NULL,
    tp1 REAL NOT NULL,
    tp2 REAL NOT NULL,
    exit_reason TEXT,                -- tp1 | tp2 | sl | time_stop | manual | end_of_data | cancelled
    pnl_usd REAL,
    pnl_r_multiple REAL,
    confluence_score INTEGER,        -- 0..4
    grade TEXT,                      -- a_plus | valid | skip
    size_multiplier REAL,
    signal_source TEXT DEFAULT 'scanner',
    position_size_units REAL,
    risk_usd REAL,
    status TEXT DEFAULT 'open',      -- open | closed | cancelled
    notes TEXT
);

CREATE TABLE paper_daily (
    date TEXT PRIMARY KEY,           -- YYYY-MM-DD (UTC)
    total_equity REAL NOT NULL,
    daily_pnl REAL NOT NULL DEFAULT 0,
    trades_count INTEGER NOT NULL DEFAULT 0,
    wins INTEGER NOT NULL DEFAULT 0,
    losses INTEGER NOT NULL DEFAULT 0,
    win_rate REAL NOT NULL DEFAULT 0,
    cumulative_pnl REAL NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL
);

CREATE TABLE paper_state (           -- singleton key/value
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,             -- JSON
    updated_at INTEGER NOT NULL
);
```

DB path: `data/storage/paper_trades.db` (overridable via
`src.config.PAPER_DB_PATH`).

---

## Phase 7 readiness check

`phase7_readiness(metrics, total_trades)` returns:

```python
{
    "ready": bool,             # ALL four must be True
    "min_trades_ok": bool,     # total_trades >= PAPER_PHASE7_MIN_TRADES (30)
    "win_rate_ok":   bool,     # win_rate >= 0.40 (target 50% - 10% tol)
    "profit_factor_ok": bool,  # profit_factor >= 1.0
    "drawdown_ok":   bool,     # max_drawdown_pct <= 0.20
    "min_trades":    30,
    "total_trades":  ...,
    "win_rate":      ...,
    "profit_factor": ...,
    "max_drawdown_pct": ...,
}
```

The `paper report` command prints a `🟢 READY` or `🔴 NOT READY` line
at the bottom so the operator can copy-paste the verdict straight
into the project log.

---

## Quickstart

```bash
# 1. Initialize the paper portfolio
python main.py paper start

# 2. Check status
python main.py paper status

# 3. One-shot scan + open paper positions from confluence
python main.py paper scan-and-trade

# 4. Long-running monitor (CCXT price feed, SL/TP polling)
python main.py paper monitor

# 5. Inspect the report after some trades have closed
python main.py paper report --days 7

# 6. (Optional) Manually fire a Telegram summary
python main.py paper daily-digest
python main.py paper weekly-report
```

---

## Tests

55 unit tests live in `tests/test_paper.py`. Run with:

```bash
python -m pytest tests/test_paper.py -v
```

Coverage:
- `PaperJournal` (10 tests): schema, open/close, validation, daily
  aggregation, state roundtrip, wipe_all
- `PaperPortfolio` (17 tests): initial balance, idempotent start,
  position sizing math (with/without multiplier, input validation),
  long/short PnL, open/close, equity, drawdown, circuit breaker,
  daily loss limit, `close_all`, `get_state`
- `PaperTrader` (13 tests): `open_from_signal` for valid / A+ /
  low-score / skip-grade / missing-SL-TP, `check_one_position` for
  SL / TP1 / TP2 / time-stop / no-action / short direction,
  `close_trade`, `make_trade_id` format
- `Reporter` (6 tests): report text on populated + empty DB, Phase 7
  readiness (pass / fail), PNG equity chart created, empty-chart
  graceful return, weekly summary shape
- `Notifier` (7 tests): 5-tier message format (entry / exit /
  daily / weekly / risk), graceful degradation when no Telegram
  token, tier constants

---

## Known limitations

- **TP1 partial close** is recorded as a full close at TP1 in the
  paper implementation. The TP1-hit-breakeven mechanic is wired
  (config flag, journal column) but the partial-fill accounting is
  left to the live-trading layer in Phase 7.
- **No live price feed** in `paper status` — mark-to-market uses the
  entry price for open positions unless you pass `mark_prices=`.
- **Same-bar SL + TP1** is resolved pessimistically (assume SL hit)
  to match the backtest engine. The real exchange may resolve
  differently, so expect paper SL hit rate to be slightly higher
  than live.
