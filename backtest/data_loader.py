"""
Backtest data loader — DB-first, fallback ke CCXT.

Logika:
    1. Buka SQLite lewat CandleDB.
    2. Hitung berapa candle yang kita butuhkan untuk `days_back × candles_per_day`
       (timeframe-specific). Misal 1h @ 30 hari -> 720 candle.
    3. Kalau DB sudah punya >= yang dibutuhkan untuk simbol+timeframe -> pakai DB.
    4. Else, fetch dari Binance via CryptoFetcher (dengan limit besar yang
       menutupi gap), simpan ke DB, dan kembalikan DataFrame.
    5. Sort ascending by timestamp, drop duplikat.

Output selalu DataFrame dengan kolom: timestamp, open, high, low, close, volume.
"""

from __future__ import annotations

import time
from typing import Any

import pandas as pd

from data.fetchers.crypto_fetcher import CryptoFetcher
from data.storage.candle_db import CandleDB
from src.config import TIMEFRAMES
from src.logger import logger


def _candles_per_day(timeframe: str) -> int:
    """Berapa candle 1 hari untuk timeframe tertentu."""
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"Timeframe tidak dikenal: {timeframe}")
    return int(86_400 // TIMEFRAMES[timeframe]["seconds"])


def required_candles(days_back: int, timeframe: str, buffer: float = 1.1) -> int:
    """
    Berapa candle minimum yang dibutuhkan untuk `days_back` hari pada `timeframe`.

    Buffer 10% default untuk memastikan warm-up indikator (RSI/ADX/WaveTrend)
    punya cukup data meskipun kita cut trailing days.
    """
    if days_back <= 0:
        raise ValueError(f"days_back harus > 0, dapat {days_back}")
    per_day = _candles_per_day(timeframe)
    return max(1, int(days_back * per_day * buffer))


def _normalize_symbol(symbol: str) -> str:
    """Samakan bentuk simbol (e.g. BTCUSDT -> BTC/USDT) untuk query DB."""
    s = symbol.strip().upper()
    if "/" in s:
        return s
    for q in ("USDT", "USDC", "BUSD", "FDUSD", "DAI", "BTC", "ETH"):
        if s.endswith(q) and len(s) > len(q):
            return f"{s[: -len(q)]}/{q}"
    return s


def load_from_db(
    db: CandleDB, symbol: str, timeframe: str, min_required: int
) -> pd.DataFrame:
    """
    Ambil candle dari SQLite. Return DataFrame kosong bila tidak cukup data.

    Tidak ada filter start_ts/end_ts — kita ambil `min_required` candle
    terakhir lalu biarkan engine memotong berdasarkan `days_back`.
    Caller bisa memfilter sendiri via .tail() / .iloc.
    """
    return db.get_candles(pair=symbol, timeframe=timeframe, limit=min_required)


def fetch_from_exchange(
    symbol: str, timeframe: str, limit: int
) -> pd.DataFrame:
    """Tarik candle langsung dari Binance via CCXT. Tutup exchange setelahnya."""
    fetcher = CryptoFetcher(exchange_id="binance")
    try:
        df = fetcher.fetch_ohlcv(symbol=symbol, timeframe=timeframe, limit=limit)
    finally:
        fetcher.close()
    return df


def ensure_data(
    symbol: str,
    timeframe: str,
    days_back: int,
    *,
    db: CandleDB | None = None,
    fetcher: CryptoFetcher | None = None,
    fetch_buffer: float = 1.1,
    force_refresh: bool = False,
    extra_fetch_multiplier: float = 1.5,
) -> pd.DataFrame:
    """
    Load data untuk backtest, DB-first dengan fallback ke CCXT.

    Args:
        symbol: Trading pair (e.g. 'BTC/USDT' atau 'BTCUSDT').
        timeframe: '5m' / '15m' / '1h' / '4h' / '1d'.
        days_back: Berapa hari ke belakang yang dibutuhkan.
        db: Optional CandleDB instance (akan dibuat jika None).
        fetcher: Optional CryptoFetcher instance (untuk testing). Jika None,
            akan dibuat dan ditutup di akhir. Catatan: argumen ini diabaikan
            bila data sudah cukup di DB.
        fetch_buffer: Buffer 10% untuk warm-up indikator.
        force_refresh: True -> selalu fetch dari exchange (lewati DB).
        extra_fetch_multiplier: Saat fetch, minta limit = required * multiplier
            (default 1.5) supaya pagination tidak under-fetch.

    Returns:
        DataFrame sorted ascending by timestamp, kolom
        timestamp/open/high/low/close/volume. Bisa kosong kalau fetch gagal.
    """
    norm = _normalize_symbol(symbol)
    needed = required_candles(days_back, timeframe, buffer=fetch_buffer)

    if not force_refresh:
        db_instance = db if db is not None else CandleDB()
        owns_db = db is None
        try:
            if owns_db:
                db_instance.open()
            df = load_from_db(db_instance, norm, timeframe, needed)
        finally:
            if owns_db:
                db_instance.close()
        if not df.empty and len(df) >= needed:
            logger.info(
                f"[data_loader] DB hit: {norm} {timeframe} "
                f"({len(df)} rows, needed {needed})"
            )
            return df.sort_values("timestamp").reset_index(drop=True)
        logger.info(
            f"[data_loader] DB miss: {norm} {timeframe} punya "
            f"{len(df)} rows, butuh {needed}. Akan fetch."
        )
    else:
        logger.info(
            f"[data_loader] force_refresh=True, skip DB untuk {norm} {timeframe}"
        )

    # Fetch from exchange
    fetch_limit = max(needed, int(needed * extra_fetch_multiplier))
    if fetcher is not None:
        df = fetcher.fetch_ohlcv(symbol=norm, timeframe=timeframe, limit=fetch_limit)
    else:
        df = fetch_from_exchange(norm, timeframe, fetch_limit)
    if df.empty:
        logger.error(
            f"[data_loader] Fetch kosong untuk {norm} {timeframe} — "
            f"backtest akan kosong."
        )
        return df

    # Simpan ke DB (best-effort). Tulis sekali, kita tidak peduli kalau
    # sebagian sudah ada (INSERT OR IGNORE di CandleDB).
    db_instance = db if db is not None else CandleDB()
    owns_db = db is None
    try:
        if owns_db:
            db_instance.open()
        db_instance.insert_candles(df=df, pair=norm, timeframe=timeframe)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[data_loader] Gagal insert ke DB: {exc}")
    finally:
        if owns_db:
            db_instance.close()

    return df.sort_values("timestamp").reset_index(drop=True)


__all__ = [
    "ensure_data",
    "fetch_from_exchange",
    "load_from_db",
    "required_candles",
]


# --- Tiny helper used by tests ------------------------------------------------
def last_n_days(df: pd.DataFrame, days: int) -> pd.DataFrame:
    """
    Helper: potong df ke N hari terakhir (berdasarkan timestamp ms).
    Berguna agar backtest engine dan tests pakai referensi waktu yang sama.
    """
    if df is None or df.empty or days <= 0:
        return df
    max_ts = df["timestamp"].max()
    # pandas Series.max() returns numpy scalar; coerce via Python int()
    last_ts = int(max_ts)  # type: ignore[arg-type]
    cutoff_ms = last_ts - days * 86_400_000
    mask = df["timestamp"] >= cutoff_ms
    out: pd.DataFrame = df[mask]  # type: ignore[assignment]
    return out.reset_index(drop=True)
