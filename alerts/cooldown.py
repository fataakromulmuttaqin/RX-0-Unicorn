"""
Per-pair alert cooldown — SQLite-backed, Phase 4 RX-0 Unicorn.

Anti-spam guard: kalau pair yang sama sudah di-alert dalam
`cooldown_minutes` terakhir, skip. Cooldown di-reset setiap kali
`mark_alerted()` dipanggil (timestamp baru).

Schema:
    alert_cooldown(
        pair          TEXT PRIMARY KEY,
        last_alert_at INTEGER NOT NULL  -- Unix epoch seconds
    )

Menggunakan DB yang sama dengan candle storage (data/storage/candles.db)
untuk konsolidasi — tabel terpisah, tidak mengganggu schema candle.
Bisa di-instantiate tanpa context manager (otomatis buka/tutup connection
per method), atau dipakai sebagai context manager untuk batch operations.
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from src.config import DB_PATH, ensure_dirs
from src.logger import logger


class CooldownManager:
    """
    SQLite-backed per-pair alert cooldown.

    Args:
        cooldown_minutes: Minimum jeda antar alert untuk pair yang sama.
        db_path: Path ke SQLite file. Default = DB_PATH dari config.
        auto_init: Bikin tabel kalau belum ada (default True).
    """

    def __init__(
        self,
        cooldown_minutes: int = 15,
        db_path: Path | str | None = None,
        auto_init: bool = True,
    ) -> None:
        self.cooldown_minutes: int = max(0, int(cooldown_minutes))
        self.cooldown_seconds: int = self.cooldown_minutes * 60
        self.db_path: Path = Path(db_path) if db_path is not None else DB_PATH
        self._owns_conn: bool = False  # set True kalau pakai context manager
        self._conn: sqlite3.Connection | None = None
        if auto_init:
            self._ensure_table()
            logger.debug(
                f"CooldownManager ready (db={self.db_path}, "
                f"cooldown={self.cooldown_minutes}m)"
            )

    # -- low-level connection handling -----------------------------------

    def _ensure_table(self) -> None:
        ensure_dirs()
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS alert_cooldown (
                    pair          TEXT PRIMARY KEY,
                    last_alert_at INTEGER NOT NULL
                )
                """
            )
            conn.commit()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Buka koneksi singkat, auto-close. Untuk 1-shot operation."""
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        try:
            yield conn
        finally:
            conn.close()

    def _get_conn(self) -> sqlite3.Connection:
        """Buka koneksi persisten (untuk context manager usage)."""
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass
            self._conn = None

    def __enter__(self) -> "CooldownManager":
        self._owns_conn = True
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
        self._owns_conn = False

    # -- public API -------------------------------------------------------

    def should_alert(self, pair: str) -> bool:
        """
        Return True kalau `pair` Boleh di-alert sekarang (di luar cooldown
        window atau belum pernah di-alert).
        """
        if self.cooldown_seconds == 0:
            return True
        now = int(time.time())
        with self._connect() as conn:
            row = conn.execute(
                "SELECT last_alert_at FROM alert_cooldown WHERE pair = ?",
                (pair,),
            ).fetchone()
        if row is None:
            return True
        last = int(row[0])
        return (now - last) >= self.cooldown_seconds

    def mark_alerted(self, pair: str, ts: int | None = None) -> None:
        """Catat bahwa `pair` baru saja di-alert di timestamp `ts` (atau now)."""
        when = int(ts) if ts is not None else int(time.time())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO alert_cooldown (pair, last_alert_at)
                VALUES (?, ?)
                ON CONFLICT(pair) DO UPDATE SET last_alert_at = excluded.last_alert_at
                """,
                (pair, when),
            )
            conn.commit()
        logger.debug(f"Cooldown set: {pair} @ {when}")

    def clear(self, pair: str | None = None) -> int:
        """
        Hapus cooldown. Kalau `pair` None -> hapus semua. Return jumlah row
        yang terhapus.
        """
        with self._connect() as conn:
            if pair is None:
                cur = conn.execute("DELETE FROM alert_cooldown")
            else:
                cur = conn.execute(
                    "DELETE FROM alert_cooldown WHERE pair = ?", (pair,)
                )
            conn.commit()
            deleted = cur.rowcount
        logger.info(f"Cooldown cleared: {deleted} row(s)" + (f" ({pair})" if pair else ""))
        return deleted

    def cleanup_old(self, max_age_hours: int = 24) -> int:
        """
        Hapus entry cooldown yang lebih tua dari `max_age_hours`. Mencegah
        tabel membengkak untuk pair yang sudah lama tidak muncul.
        Return jumlah row yang terhapus.
        """
        cutoff = int(time.time()) - (max_age_hours * 3600)
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM alert_cooldown WHERE last_alert_at < ?", (cutoff,)
            )
            conn.commit()
            deleted = cur.rowcount
        if deleted:
            logger.debug(f"Cooldown cleanup: removed {deleted} stale rows")
        return deleted

    def get_last_alert_at(self, pair: str) -> int | None:
        """Return timestamp terakhir pair di-alert, atau None."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT last_alert_at FROM alert_cooldown WHERE pair = ?",
                (pair,),
            ).fetchone()
        return int(row[0]) if row else None

    def all_pairs(self) -> dict[str, int]:
        """Return dict {pair: last_alert_at} untuk semua entry (debug)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT pair, last_alert_at FROM alert_cooldown"
            ).fetchall()
        return {p: int(t) for p, t in rows}
