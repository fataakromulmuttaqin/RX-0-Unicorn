"""
RX-0 Unicorn — Phase 1 CLI entry point.

Subcommands:
    fetch   Tarik candle untuk watchlist dan simpan ke SQLite.
    status  Tampilkan statistik database.
    cleanup Hapus candle lama sesuai retention policy.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Iterable

# Pastikan root project ada di sys.path agar import src.* & data.* konsisten
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from data.fetchers.crypto_fetcher import CryptoFetcher
from data.storage.candle_db import CandleDB
from src.config import (
    DEFAULT_LIMIT,
    DEFAULT_TIMEFRAME,
    VALID_TIMEFRAMES,
    WATCHLIST_PATH,
    WATCHLIST_TIERS,
)
from src.logger import logger


def load_watchlist(path: Path = WATCHLIST_PATH) -> dict[str, list[str]]:
    """Load watchlist JSON. Raise kalau file rusak."""
    if not path.exists():
        raise FileNotFoundError(
            f"Watchlist tidak ditemukan di {path}. "
            f"Pastikan data/pairs/watchlist.json ada."
        )
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Watchlist harus object/dict, dapat: {type(data)}")
    return data


def resolve_symbols(
    watchlist: dict[str, list[str]],
    tier: str | None,
) -> list[str]:
    """
    Pilih simbol dari watchlist. Jika tier=None, gabungkan semua.
    """
    if tier is None:
        out: list[str] = []
        for t, syms in watchlist.items():
            if not isinstance(syms, list):
                logger.warning(f"Tier {t} bukan list, di-skip")
                continue
            out.extend(syms)
        # Dedup sambil pertahankan urutan
        seen = set()
        deduped = []
        for s in out:
            if s not in seen:
                seen.add(s)
                deduped.append(s)
        return deduped

    if tier not in watchlist:
        raise ValueError(
            f"Tier '{tier}' tidak ada. Pilihan: {list(watchlist.keys())}"
        )
    syms = watchlist[tier]
    if not isinstance(syms, list):
        raise ValueError(f"Tier {tier} harus berisi list")
    return list(syms)


def _format_stats(stats: dict) -> str:
    """Format statistik DB jadi string rapi untuk dicetak."""
    lines = [
        "=" * 60,
        "RX-0 Unicorn — Database Status",
        "=" * 60,
        f"Path             : {stats.get('db_path')}",
        f"Schema version   : {stats.get('schema_version')}",
        f"Size             : {stats.get('size_bytes', 0):,} bytes",
        f"Total rows       : {stats.get('total_rows', 0):,}",
        f"Distinct pairs   : {stats.get('distinct_pairs', 0):,}",
        f"Distinct TFs     : {stats.get('distinct_timeframes', 0):,}",
        f"First timestamp  : {stats.get('first_timestamp_iso') or 'N/A'}",
        f"Last  timestamp  : {stats.get('last_timestamp_iso') or 'N/A'}",
        "-" * 60,
        "Rows per timeframe:",
    ]
    per_tf = stats.get("rows_per_timeframe") or {}
    if per_tf:
        for tf in sorted(per_tf.keys()):
            lines.append(f"  {tf:>4} : {per_tf[tf]:>8,} rows")
    else:
        lines.append("  (no data yet)")
    lines.append("=" * 60)
    return "\n".join(lines)


# --- Subcommand handlers ---
def cmd_status(_args: argparse.Namespace) -> int:
    """Tampilkan statistik database."""
    with CandleDB() as db:
        stats = db.get_stats()
    print(_format_stats(stats))
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    """
    Tarik candle untuk watchlist dan simpan ke DB.

    Default: seluruh watchlist, timeframe dari --timeframe, limit dari --limit.
    """
    watchlist = load_watchlist()
    symbols = resolve_symbols(watchlist, args.tier)
    if not symbols:
        logger.error("Watchlist kosong setelah filter")
        return 1
    logger.info(
        f"Fetch plan: {len(symbols)} symbols, "
        f"tf={args.timeframe}, limit={args.limit}"
        + (f", tier={args.tier}" if args.tier else ", all tiers")
    )

    fetcher = CryptoFetcher(exchange_id="binance")
    start = time.time()
    try:
        results = fetcher.fetch_multiple(
            symbols=symbols,
            timeframe=args.timeframe,
            limit=args.limit,
        )
    finally:
        fetcher.close()

    inserted_total = 0
    failures: list[str] = []
    with CandleDB() as db:
        for sym, df in results.items():
            if df is None or df.empty:
                failures.append(sym)
                continue
            try:
                inserted_total += db.insert_candles(
                    df=df, pair=sym, timeframe=args.timeframe
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(f"DB insert failed for {sym}: {exc}")
                failures.append(sym)

    elapsed = time.time() - start
    logger.success(
        f"Done: {len(symbols) - len(failures)}/{len(symbols)} ok, "
        f"inserted={inserted_total} new rows, "
        f"failed={len(failures)}, elapsed={elapsed:.1f}s"
    )
    if failures:
        logger.warning(f"Failed symbols: {failures}")
    return 0


def cmd_cleanup(_args: argparse.Namespace) -> int:
    """Hapus candle lama sesuai retention policy."""
    with CandleDB() as db:
        deleted = db.cleanup_old()
    logger.success(f"Cleanup done: {deleted} rows deleted")
    return 0


# --- Argparse ---
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rx0-unicorn",
        description=(
            "RX-0 Unicorn — Phase 1 data foundation CLI. "
            "Tarik candle dari Binance, simpan ke SQLite, lihat status DB."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # status
    sub.add_parser(
        "status",
        help="Tampilkan statistik database (row count, distinct pairs, dll).",
    ).set_defaults(func=cmd_status)

    # fetch
    p_fetch = sub.add_parser(
        "fetch",
        help="Tarik OHLCV untuk watchlist dan simpan ke SQLite.",
    )
    p_fetch.add_argument(
        "--tier",
        choices=WATCHLIST_TIERS,
        default=None,
        help="Filter ke satu tier watchlist (default: semua).",
    )
    p_fetch.add_argument(
        "--timeframe",
        "-t",
        choices=VALID_TIMEFRAMES,
        default=DEFAULT_TIMEFRAME,
        help=f"Timeframe candle (default: {DEFAULT_TIMEFRAME}).",
    )
    p_fetch.add_argument(
        "--limit",
        "-l",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"Jumlah candle per simbol (default: {DEFAULT_LIMIT}).",
    )
    p_fetch.set_defaults(func=cmd_fetch)

    # cleanup
    sub.add_parser(
        "cleanup",
        help="Hapus candle lama sesuai retention policy (90d intraday, daily forever).",
    ).set_defaults(func=cmd_cleanup)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        logger.warning("Interrupted by user")
        return 130
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"Fatal: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
