"""
PaperJournal — SQLite persistence layer untuk paper trades (Phase 6).

Schema:
  paper_trades (id PK, trade_id UNIQUE, symbol, direction, entry/exit
                prices & times, SL/TP, exit_reason, pnl_usd, r_multiple,
                confluence metadata)
  paper_daily  (date PK, total_equity, daily_pnl, trades_count, wins,
                losses, win_rate, cumulative_pnl)
  paper_state  (key PK, value) — singleton key-value store untuk
                menyimpan peak_equity, last_init_ts, dll.

DB path: src.config.PAPER_DB_PATH (default data/storage/paper_trades.db).
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from src.config import PAPER_DB_PATH
from src.logger import logger


# --- Schema ---
SCHEMA_SQL: str = """
CREATE TABLE IF NOT EXISTS paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id TEXT UNIQUE NOT NULL,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry_time INTEGER NOT NULL,
    entry_price REAL NOT NULL,
    exit_time INTEGER,
    exit_price REAL,
    sl REAL NOT NULL,
    tp1 REAL NOT NULL,
    tp2 REAL NOT NULL,
    exit_reason TEXT,
    pnl_usd REAL,
    pnl_r_multiple REAL,
    confluence_score INTEGER,
    grade TEXT,
    size_multiplier REAL,
    signal_source TEXT DEFAULT 'scanner',
    position_size_units REAL,
    risk_usd REAL,
    status TEXT DEFAULT 'open',
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_paper_trades_symbol
    ON paper_trades(symbol);
CREATE INDEX IF NOT EXISTS idx_paper_trades_status
    ON paper_trades(status);
CREATE INDEX IF NOT EXISTS idx_paper_trades_entry_time
    ON paper_trades(entry_time);
CREATE INDEX IF NOT EXISTS idx_paper_trades_exit_time
    ON paper_trades(exit_time);

CREATE TABLE IF NOT EXISTS paper_daily (
    date TEXT PRIMARY KEY,
    total_equity REAL NOT NULL,
    daily_pnl REAL NOT NULL DEFAULT 0,
    trades_count INTEGER NOT NULL DEFAULT 0,
    wins INTEGER NOT NULL DEFAULT 0,
    losses INTEGER NOT NULL DEFAULT 0,
    win_rate REAL NOT NULL DEFAULT 0,
    cumulative_pnl REAL NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);
"""


# Allowed values for enum-like columns — kept here to avoid hard-coding
# throughout the codebase. Validation happens in PaperTrader.
ALLOWED_DIRECTIONS: tuple[str, ...] = ("long", "short")
ALLOWED_GRADES: tuple[str, ...] = ("a_plus", "valid", "skip")
ALLOWED_SIGNAL_SOURCES: tuple[str, ...] = ("scanner", "manual", "telegram")
ALLOWED_STATUSES: tuple[str, ...] = ("open", "closed", "cancelled")
ALLOWED_EXIT_REASONS: tuple[str, ...] = (
    "tp1", "tp2", "sl", "time_stop", "manual", "end_of_data", "cancelled"
)


def _now_ts() -> int:
    return int(time.time())


def _to_iso(ts: int | None) -> str:
    if ts is None:
        return "N/A"
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


class PaperJournal:
    """
    Thin SQLite wrapper untuk paper_trades / paper_daily / paper_state.

    Usage:
        with PaperJournal() as journal:
            journal.log_open_position(...)
            journal.log_close_position(...)
            trades = journal.get_closed_trades(limit=20)
    """

    def __init__(self, db_path: str | Path = PAPER_DB_PATH) -> None:
        self.db_path: Path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None

    # --- Context manager ---
    def __enter__(self) -> "PaperJournal":
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        if self._conn is not None:
            try:
                if exc_type is None:
                    self._conn.commit()
                else:
                    self._conn.rollback()
            finally:
                self._conn.close()
                self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError(
                "PaperJournal harus dipakai sebagai context manager: "
                "'with PaperJournal() as j: ...'"
            )
        return self._conn

    def _init_schema(self) -> None:
        self.conn.executescript(SCHEMA_SQL)

    # --- Open / close trade ---
    def log_open_position(
        self,
        *,
        trade_id: str,
        symbol: str,
        direction: str,
        entry_time: int,
        entry_price: float,
        sl: float,
        tp1: float,
        tp2: float,
        confluence_score: int,
        grade: str,
        size_multiplier: float,
        position_size_units: float,
        risk_usd: float,
        signal_source: str = "scanner",
        notes: str | None = None,
    ) -> int:
        """
        Insert a new open position. Returns row id.
        """
        if direction not in ALLOWED_DIRECTIONS:
            raise ValueError(
                f"direction '{direction}' not in {ALLOWED_DIRECTIONS}"
            )
        if grade not in ALLOWED_GRADES:
            raise ValueError(f"grade '{grade}' not in {ALLOWED_GRADES}")
        if signal_source not in ALLOWED_SIGNAL_SOURCES:
            raise ValueError(
                f"signal_source '{signal_source}' not in {ALLOWED_SIGNAL_SOURCES}"
            )
        if not (0 <= confluence_score <= 4):
            raise ValueError(
                f"confluence_score must be 0..4, got {confluence_score}"
            )

        cur = self.conn.execute(
            """
            INSERT INTO paper_trades (
                trade_id, symbol, direction, entry_time, entry_price,
                sl, tp1, tp2, confluence_score, grade, size_multiplier,
                signal_source, position_size_units, risk_usd, status, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)
            """,
            (
                trade_id,
                symbol,
                direction,
                int(entry_time),
                float(entry_price),
                float(sl),
                float(tp1),
                float(tp2),
                int(confluence_score),
                grade,
                float(size_multiplier),
                signal_source,
                float(position_size_units),
                float(risk_usd),
                notes,
            ),
        )
        logger.info(
            f"[journal] open {direction.upper()} {symbol} @ {entry_price:.4f} "
            f"(score={confluence_score}/4 grade={grade} id={trade_id})"
        )
        return int(cur.lastrowid or 0)

    def log_close_position(
        self,
        *,
        trade_id: str,
        exit_time: int,
        exit_price: float,
        exit_reason: str,
        pnl_usd: float,
        pnl_r_multiple: float,
    ) -> None:
        """Update an open position to closed. Computes daily aggregation."""
        if exit_reason not in ALLOWED_EXIT_REASONS:
            raise ValueError(
                f"exit_reason '{exit_reason}' not in {ALLOWED_EXIT_REASONS}"
            )

        # Verify trade is currently open
        row = self.conn.execute(
            "SELECT status, exit_time FROM paper_trades WHERE trade_id = ?",
            (trade_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"trade_id '{trade_id}' not found")
        if row["status"] != "open":
            raise ValueError(
                f"trade_id '{trade_id}' already {row['status']} "
                f"(exit_time={row['exit_time']})"
            )

        self.conn.execute(
            """
            UPDATE paper_trades
            SET exit_time = ?, exit_price = ?, exit_reason = ?,
                pnl_usd = ?, pnl_r_multiple = ?, status = 'closed'
            WHERE trade_id = ?
            """,
            (
                int(exit_time),
                float(exit_price),
                exit_reason,
                float(pnl_usd),
                float(pnl_r_multiple),
                trade_id,
            ),
        )
        # Aggregate into paper_daily
        entry_row = self.conn.execute(
            "SELECT entry_time FROM paper_trades WHERE trade_id = ?",
            (trade_id,),
        ).fetchone()
        if entry_row is not None:
            date_str = datetime.fromtimestamp(
                int(entry_row["entry_time"]), tz=timezone.utc
            ).strftime("%Y-%m-%d")
            self._aggregate_day(date_str)

        logger.info(
            f"[journal] close {trade_id} @ {exit_price:.4f} "
            f"reason={exit_reason} pnl=${pnl_usd:+.2f} ({pnl_r_multiple:+.2f}R)"
        )

    def cancel_position(self, trade_id: str, reason: str = "cancelled") -> None:
        """Cancel an open position (no P/L realized — entry offset)."""
        row = self.conn.execute(
            "SELECT status FROM paper_trades WHERE trade_id = ?",
            (trade_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"trade_id '{trade_id}' not found")
        if row["status"] != "open":
            raise ValueError(
                f"trade_id '{trade_id}' is {row['status']}, not open"
            )
        self.conn.execute(
            """
            UPDATE paper_trades
            SET status = 'cancelled', exit_reason = ?, exit_time = ?
            WHERE trade_id = ?
            """,
            (reason, _now_ts(), trade_id),
        )
        logger.info(f"[journal] cancelled {trade_id} reason={reason}")

    # --- Read ---
    def get_open_positions(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT * FROM paper_trades WHERE status = 'open'
            ORDER BY entry_time ASC
            """
        ).fetchall()
        return [dict(r) for r in rows]

    def get_closed_trades(
        self, *, limit: int | None = None, days_back: int | None = None
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM paper_trades WHERE status = 'closed'"
        params: list[Any] = []
        if days_back is not None and days_back > 0:
            cutoff = _now_ts() - (days_back * 86400)
            sql += " AND exit_time >= ?"
            params.append(cutoff)
        sql += " ORDER BY exit_time DESC"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        rows = self.conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def get_all_trades(
        self, *, limit: int | None = None
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM paper_trades ORDER BY entry_time DESC"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        return [dict(r) for r in self.conn.execute(sql).fetchall()]

    def get_trade_by_id(self, trade_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM paper_trades WHERE trade_id = ?", (trade_id,)
        ).fetchone()
        return dict(row) if row else None

    def count_open_positions(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM paper_trades WHERE status = 'open'"
        ).fetchone()
        return int(row["c"] if row else 0)

    def count_trades_today(self) -> int:
        """Count entries placed today (UTC)."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        # entry_time is unix ts — convert
        cutoff = int(
            datetime.strptime(today, "%Y-%m-%d")
            .replace(tzinfo=timezone.utc)
            .timestamp()
        )
        row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM paper_trades WHERE entry_time >= ?",
            (cutoff,),
        ).fetchone()
        return int(row["c"] if row else 0)

    def daily_pnl_today(self) -> float:
        """Sum P/L of trades closed today (UTC)."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        cutoff = int(
            datetime.strptime(today, "%Y-%m-%d")
            .replace(tzinfo=timezone.utc)
            .timestamp()
        )
        row = self.conn.execute(
            """
            SELECT COALESCE(SUM(pnl_usd), 0) AS s
            FROM paper_trades
            WHERE status = 'closed' AND exit_time >= ?
            """,
            (cutoff,),
        ).fetchone()
        return float(row["s"] if row else 0.0)

    # --- Daily aggregation ---
    def _aggregate_day(self, date_str: str) -> None:
        """Recompute paper_daily for `date_str` from closed trades."""
        # entry_time for trades closed today falls into a UTC date based
        # on entry time. We aggregate by entry date to make the report
        # stable (a trade booked today stays on today's row).
        cutoff_start = int(
            datetime.strptime(date_str, "%Y-%m-%d")
            .replace(tzinfo=timezone.utc)
            .timestamp()
        )
        cutoff_end = cutoff_start + 86400
        row = self.conn.execute(
            """
            SELECT
                COUNT(*) AS cnt,
                COALESCE(SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END), 0) AS wins,
                COALESCE(SUM(CASE WHEN pnl_usd < 0 THEN 1 ELSE 0 END), 0) AS losses,
                COALESCE(SUM(pnl_usd), 0) AS pnl
            FROM paper_trades
            WHERE status = 'closed'
              AND entry_time >= ? AND entry_time < ?
            """,
            (cutoff_start, cutoff_end),
        ).fetchone()
        cnt = int(row["cnt"] or 0)
        wins = int(row["wins"] or 0)
        losses = int(row["losses"] or 0)
        pnl = float(row["pnl"] or 0.0)
        win_rate = (wins / cnt) if cnt > 0 else 0.0
        # total_equity / cumulative_pnl will be updated by reporter/portfolio
        self.conn.execute(
            """
            INSERT INTO paper_daily (
                date, total_equity, daily_pnl, trades_count, wins,
                losses, win_rate, cumulative_pnl, updated_at
            ) VALUES (?, 0, ?, ?, ?, ?, ?, 0, ?)
            ON CONFLICT(date) DO UPDATE SET
                daily_pnl = excluded.daily_pnl,
                trades_count = excluded.trades_count,
                wins = excluded.wins,
                losses = excluded.losses,
                win_rate = excluded.win_rate,
                updated_at = excluded.updated_at
            """,
            (date_str, pnl, cnt, wins, losses, win_rate, _now_ts()),
        )

    def update_daily_equity(
        self, date_str: str, total_equity: float, cumulative_pnl: float
    ) -> None:
        """Write equity snapshot into paper_daily row (idempotent)."""
        # Ensure row exists
        self.conn.execute(
            "INSERT OR IGNORE INTO paper_daily (date, total_equity, daily_pnl, "
            "trades_count, wins, losses, win_rate, cumulative_pnl, updated_at) "
            "VALUES (?, ?, 0, 0, 0, 0, 0, ?, ?)",
            (date_str, total_equity, cumulative_pnl, _now_ts()),
        )
        self.conn.execute(
            """
            UPDATE paper_daily
            SET total_equity = ?, cumulative_pnl = ?, updated_at = ?
            WHERE date = ?
            """,
            (total_equity, cumulative_pnl, _now_ts(), date_str),
        )

    def get_daily_history(
        self, days_back: int | None = None
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM paper_daily ORDER BY date ASC"
        rows = self.conn.execute(sql).fetchall()
        out = [dict(r) for r in rows]
        if days_back is not None and days_back > 0 and out:
            return out[-int(days_back):]
        return out

    # --- State (singleton key/value) ---
    def set_state(self, key: str, value: Any) -> None:
        """Persist a JSON-encodable value under key."""
        encoded = json.dumps(value)
        self.conn.execute(
            """
            INSERT INTO paper_state (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, encoded, _now_ts()),
        )

    def get_state(self, key: str, default: Any = None) -> Any:
        row = self.conn.execute(
            "SELECT value FROM paper_state WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            return default

    # --- Aggregates for reporter ---
    def aggregate_performance(
        self, *, days_back: int | None = None
    ) -> dict[str, Any]:
        """
        Return summary metrics across closed trades.
        Same shape as backtest.metrics.calculate_metrics() (subset).
        """
        trades = self.get_closed_trades(days_back=days_back)
        return _summarize_trades(trades)

    # --- Maintenance ---
    def wipe_all(self) -> None:
        """DANGEROUS: delete every paper trade + daily + state row.
        Used by tests and by `paper start --reset`."""
        self.conn.executescript(
            "DELETE FROM paper_trades; DELETE FROM paper_daily; "
            "DELETE FROM paper_state;"
        )
        logger.warning("[journal] wiped all paper_trades / paper_daily / paper_state")

    def get_stats(self) -> dict[str, Any]:
        """Lightweight summary for `paper status`."""
        open_count = self.count_open_positions()
        closed_count_row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM paper_trades WHERE status = 'closed'"
        ).fetchone()
        closed_count = int(closed_count_row["c"] if closed_count_row else 0)
        total_count_row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM paper_trades"
        ).fetchone()
        total_count = int(total_count_row["c"] if total_count_row else 0)
        size_bytes = (
            self.db_path.stat().st_size if self.db_path.exists() else 0
        )
        return {
            "db_path": str(self.db_path),
            "size_bytes": size_bytes,
            "total_trades": total_count,
            "open_trades": open_count,
            "closed_trades": closed_count,
        }


def _summarize_trades(trades: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """
    Build summary metrics over a list of closed trade dicts.
    Mirrors the subset of fields produced by backtest.metrics.calculate_metrics
    so the reporter can use the same shape.
    """
    pnls: list[float] = []
    rs: list[float] = []
    wins = 0
    losses = 0
    total_win = 0.0
    total_loss = 0.0
    largest_win = 0.0
    largest_loss = 0.0
    for t in trades:
        pnl = t.get("pnl_usd")
        if pnl is None:
            continue
        pnl = float(pnl)
        pnls.append(pnl)
        r = t.get("pnl_r_multiple")
        if r is not None:
            rs.append(float(r))
        if pnl > 0:
            wins += 1
            total_win += pnl
            if pnl > largest_win:
                largest_win = pnl
        elif pnl < 0:
            losses += 1
            total_loss += pnl  # negative
            if pnl < largest_loss:
                largest_loss = pnl
    total = len(pnls)
    if total == 0:
        return {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "max_drawdown_pct": 0.0,
            "sharpe_ratio": 0.0,
            "avg_r_multiple": 0.0,
            "expectancy": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "largest_win": 0.0,
            "largest_loss": 0.0,
            "total_pnl": 0.0,
        }
    import math

    win_rate = wins / total
    profit_factor = (
        (total_win / abs(total_loss)) if total_loss < 0 else 999.0
    )
    avg_win = total_win / wins if wins else 0.0
    avg_loss = total_loss / losses if losses else 0.0  # negative
    expectancy = (win_rate * avg_win) + ((1 - win_rate) * avg_loss)
    avg_r = sum(rs) / len(rs) if rs else 0.0
    # Sharpe: mean(pnl)/std(pnl); use sample std (ddof=1) if n>=2
    if len(pnls) >= 2:
        mean = sum(pnls) / len(pnls)
        var = sum((x - mean) ** 2 for x in pnls) / (len(pnls) - 1)
        std = math.sqrt(var) if var > 0 else 0.0
        sharpe = (mean / std) if std > 0 else 0.0
    else:
        sharpe = 0.0
    # Max drawdown: peak-to-trough of cumulative pnl
    peak = 0.0
    equity = 0.0
    max_dd = 0.0
    for p in pnls:  # sorted by exit_time DESC — reverse for chronological
        equity += p
        if equity > peak:
            peak = equity
        dd = (peak - equity) / (peak if peak != 0 else 1.0)
        if dd > max_dd:
            max_dd = dd
    # NOTE: pnls are DESC, so iterate reversed
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in reversed(pnls):
        equity += p
        if equity > peak:
            peak = equity
        dd = (peak - equity) / (peak if peak != 0 else 1.0)
        if dd > max_dd:
            max_dd = dd
    return {
        "total_trades": total,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "max_drawdown_pct": max_dd,
        "sharpe_ratio": sharpe,
        "avg_r_multiple": avg_r,
        "expectancy": expectancy,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "largest_win": largest_win,
        "largest_loss": largest_loss,
        "total_pnl": sum(pnls),
    }


# --- Module exports ---
__all__ = [
    "PaperJournal",
    "SCHEMA_SQL",
    "ALLOWED_DIRECTIONS",
    "ALLOWED_GRADES",
    "ALLOWED_SIGNAL_SOURCES",
    "ALLOWED_STATUSES",
    "ALLOWED_EXIT_REASONS",
    "_summarize_trades",
]
