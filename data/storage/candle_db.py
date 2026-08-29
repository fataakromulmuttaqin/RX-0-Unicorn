"""
SQLite storage untuk candle data RX-0 Unicorn.

Schema:
    candles(id, pair, timeframe, timestamp, open, high, low, close, volume,
            source, created_at)
    - UNIQUE(pair, timeframe, timestamp)
    - INDEX(pair, timeframe), INDEX(timestamp)

Retention policy:
    - Intraday (< 1d): keep last 90 days
    - 1d: keep forever
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import pandas as pd

from src.config import (
    DAILY_RETENTION_DAYS,
    DB_PATH,
    INTRADAY_RETENTION_DAYS,
    VALID_TIMEFRAMES,
    ensure_dirs,
)
from src.logger import logger


# --- Schema constants ---
SCHEMA_VERSION: int = 1

CREATE_META_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS _meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

CREATE_CANDLES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS candles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pair TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL,
    source TEXT NOT NULL DEFAULT 'binance',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(pair, timeframe, timestamp)
);
"""

CREATE_INDEX_PAIR_TF_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_candles_pair_tf "
    "ON candles(pair, timeframe);"
)
CREATE_INDEX_TIMESTAMP_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_candles_timestamp "
    "ON candles(timestamp);"
)


def _ts_to_iso(ts_ms: int) -> str:
    """Konversi millisecond timestamp ke ISO string (UTC)."""
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat()


def _now_ms() -> int:
    """Current UTC timestamp in milliseconds."""
    return int(time.time() * 1000)


def _days_ago_ms(days: int) -> int:
    """Timestamp in ms untuk N hari yang lalu."""
    return int((time.time() - days * 86400) * 1000)


class CandleDB:
    """
    SQLite-backed storage untuk candle data.

    Gunakan sebagai context manager:
        with CandleDB() as db:
            db.insert_candles(df, "BTC/USDT", "1h")
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        ensure_dirs()
        self.db_path = Path(db_path) if db_path else DB_PATH
        # Pastikan parent dir ada
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        logger.debug(f"CandleDB path: {self.db_path}")

    # --- Context manager ---
    def __enter__(self) -> "CandleDB":
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def open(self) -> None:
        """Buka koneksi dan inisialisasi schema."""
        if self._conn is not None:
            return
        self._conn = sqlite3.connect(
            str(self.db_path),
            detect_types=sqlite3.PARSE_DECLTYPES,
            isolation_level=None,  # autocommit; kita kontrol transaksi manual
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._init_schema()
        logger.debug("CandleDB connection opened & schema initialized")

    def close(self) -> None:
        """Tutup koneksi jika terbuka."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None
            logger.debug("CandleDB connection closed")

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        """Context manager untuk transaksi dengan rollback on error."""
        if self._conn is None:
            raise RuntimeError("CandleDB connection not open")
        try:
            self._conn.execute("BEGIN")
            yield self._conn
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def _require_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError(
                "CandleDB belum dibuka. Gunakan 'with CandleDB() as db:'"
            )
        return self._conn

    def _init_schema(self) -> None:
        """Buat tabel dan index kalau belum ada."""
        conn = self._require_conn()
        conn.execute(CREATE_META_TABLE_SQL)
        conn.execute(CREATE_CANDLES_TABLE_SQL)
        conn.execute(CREATE_INDEX_PAIR_TF_SQL)
        conn.execute(CREATE_INDEX_TIMESTAMP_SQL)
        # Set / bump schema version
        conn.execute(
            "INSERT INTO _meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(SCHEMA_VERSION),),
        )

    # --- CRUD ---
    def insert_candles(
        self,
        df: pd.DataFrame,
        pair: str,
        timeframe: str,
        source: str = "binance",
    ) -> int:
        """
        Insert candle rows dari DataFrame.

        Args:
            df: DataFrame dengan kolom timestamp, open, high, low, close, volume.
            pair: Simbol trading (e.g. 'BTC/USDT').
            timeframe: Salah satu VALID_TIMEFRAMES.
            source: Identifier asal data.

        Returns:
            Jumlah baris yang benar-benar di-insert (duplikat di-skip via
            INSERT OR IGNORE).
        """
        if df is None or df.empty:
            logger.debug(f"insert_candles: empty df for {pair} {timeframe}")
            return 0
        if timeframe not in VALID_TIMEFRAMES:
            raise ValueError(f"Invalid timeframe: {timeframe}")

        required = {"timestamp", "open", "high", "low", "close", "volume"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"DataFrame missing columns: {missing}")

        pair_norm = pair.strip().upper()
        rows = [
            (
                pair_norm,
                timeframe,
                int(row["timestamp"]),
                float(row["open"]),
                float(row["high"]),
                float(row["low"]),
                float(row["close"]),
                float(row["volume"]),
                source,
            )
            for _, row in df.iterrows()
        ]

        conn = self._require_conn()
        with self._tx():
            before = conn.execute(
                "SELECT COUNT(*) AS c FROM candles WHERE pair=? AND timeframe=?",
                (pair_norm, timeframe),
            ).fetchone()["c"]
            conn.executemany(
                "INSERT OR IGNORE INTO candles("
                "pair, timeframe, timestamp, open, high, low, close, volume, source"
                ") VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            after = conn.execute(
                "SELECT COUNT(*) AS c FROM candles WHERE pair=? AND timeframe=?",
                (pair_norm, timeframe),
            ).fetchone()["c"]
        inserted = after - before
        logger.info(
            f"insert_candles {pair_norm} {timeframe}: "
            f"{inserted} new, {len(rows) - inserted} dup"
        )
        return inserted

    def get_candles(
        self,
        pair: str,
        timeframe: str,
        start_ts: int | None = None,
        end_ts: int | None = None,
        limit: int | None = None,
    ) -> pd.DataFrame:
        """
        Ambil candle rows sebagai DataFrame, urut ascending by timestamp.

        Args:
            pair: Simbol trading.
            timeframe: Timeframe.
            start_ts: Filter timestamp >= start_ts (ms).
            end_ts: Filter timestamp <= end_ts (ms).
            limit: Maks baris yang dikembalikan (None = semua).
        """
        pair_norm = pair.strip().upper()
        query = (
            "SELECT timestamp, open, high, low, close, volume "
            "FROM candles WHERE pair=? AND timeframe=?"
        )
        params: list = [pair_norm, timeframe]
        if start_ts is not None:
            query += " AND timestamp >= ?"
            params.append(int(start_ts))
        if end_ts is not None:
            query += " AND timestamp <= ?"
            params.append(int(end_ts))
        query += " ORDER BY timestamp ASC"
        if limit is not None:
            query += " LIMIT ?"
            params.append(int(limit))

        conn = self._require_conn()
        rows = conn.execute(query, params).fetchall()
        if not rows:
            return pd.DataFrame(
                columns=["timestamp", "open", "high", "low", "close", "volume"]
            )
        df = pd.DataFrame(
            [dict(r) for r in rows],
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        )
        df["timestamp"] = df["timestamp"].astype("int64")
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = df[col].astype("float64")
        return df

    def get_latest(
        self, pair: str, timeframe: str, count: int = 1
    ) -> pd.DataFrame:
        """Ambil N candle terakhir untuk pair/timeframe."""
        pair_norm = pair.strip().upper()
        conn = self._require_conn()
        rows = conn.execute(
            "SELECT timestamp, open, high, low, close, volume "
            "FROM candles WHERE pair=? AND timeframe=? "
            "ORDER BY timestamp DESC LIMIT ?",
            (pair_norm, timeframe, int(count)),
        ).fetchall()
        if not rows:
            return pd.DataFrame(
                columns=["timestamp", "open", "high", "low", "close", "volume"]
            )
        df = pd.DataFrame(
            [dict(r) for r in rows],
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        )
        # Urutkan ascending
        return df.sort_values("timestamp").reset_index(drop=True)

    def cleanup_old(
        self,
        intraday_retention_days: int = INTRADAY_RETENTION_DAYS,
        daily_retention_days: int = DAILY_RETENTION_DAYS,
    ) -> int:
        """
        Hapus candle lama sesuai retention policy.

        - Intraday timeframes (5m/15m/1h/4h): keep last N days.
        - Daily (1d): keep forever (or up to daily_retention_days jika diset).

        Returns:
            Total baris yang dihapus.
        """
        conn = self._require_conn()
        intraday_cutoff = _days_ago_ms(intraday_retention_days)
        daily_cutoff = _days_ago_ms(daily_retention_days)
        intraday_tfs = tuple(t for t in VALID_TIMEFRAMES if t != "1d")

        deleted_total = 0
        # Intraday
        with self._tx():
            cur = conn.execute(
                "DELETE FROM candles "
                "WHERE timeframe IN (?, ?, ?, ?) AND timestamp < ?",
                (*intraday_tfs, intraday_cutoff),
            )
            deleted_intraday = cur.rowcount
            deleted_total += deleted_intraday

            # Daily
            cur = conn.execute(
                "DELETE FROM candles WHERE timeframe='1d' AND timestamp < ?",
                (daily_cutoff,),
            )
            deleted_daily = cur.rowcount
            deleted_total += deleted_daily

        logger.info(
            f"cleanup_old: intraday deleted={deleted_intraday} "
            f"(cutoff={_ts_to_iso(intraday_cutoff)}), "
            f"daily deleted={deleted_daily}"
        )
        return deleted_total

    # --- Stats / introspection ---
    def get_stats(self) -> dict:
        """Return statistik database: total rows, distinct pairs/tfs, dll."""
        conn = self._require_conn()
        total = conn.execute("SELECT COUNT(*) AS c FROM candles").fetchone()["c"]
        pairs = conn.execute(
            "SELECT COUNT(DISTINCT pair) AS c FROM candles"
        ).fetchone()["c"]
        timeframes = conn.execute(
            "SELECT COUNT(DISTINCT timeframe) AS c FROM candles"
        ).fetchone()["c"]
        first_ts = conn.execute(
            "SELECT MIN(timestamp) AS t FROM candles"
        ).fetchone()["t"]
        last_ts = conn.execute(
            "SELECT MAX(timestamp) AS t FROM candles"
        ).fetchone()["t"]
        per_tf_rows = conn.execute(
            "SELECT timeframe, COUNT(*) AS c FROM candles "
            "GROUP BY timeframe ORDER BY timeframe"
        ).fetchall()
        per_tf = {row["timeframe"]: row["c"] for row in per_tf_rows}
        schema_v = conn.execute(
            "SELECT value FROM _meta WHERE key='schema_version'"
        ).fetchone()
        return {
            "db_path": str(self.db_path),
            "total_rows": total,
            "distinct_pairs": pairs,
            "distinct_timeframes": timeframes,
            "first_timestamp_ms": first_ts,
            "last_timestamp_ms": last_ts,
            "first_timestamp_iso": _ts_to_iso(first_ts) if first_ts else None,
            "last_timestamp_iso": _ts_to_iso(last_ts) if last_ts else None,
            "rows_per_timeframe": per_tf,
            "schema_version": schema_v["value"] if schema_v else None,
            "size_bytes": self.db_path.stat().st_size if self.db_path.exists() else 0,
        }
