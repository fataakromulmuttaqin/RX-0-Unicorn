"""Helper internal untuk modul indicators.* — bukan public API."""

from __future__ import annotations

import pandas as pd

REQUIRED_OHLCV_COLS: tuple[str, ...] = (
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "volume",
)


def require_ohlcv(df: pd.DataFrame, min_rows: int = 1) -> None:
    """
    Validasi DataFrame OHLCV sebelum dipakai indikator.

    Raises:
        TypeError: kalau df bukan pandas DataFrame.
        ValueError: kalau kolom wajib hilang atau baris kurang dari min_rows.
    """
    if not isinstance(df, pd.DataFrame):
        raise TypeError(f"df harus pandas.DataFrame, dapat: {type(df)}")
    missing = set(REQUIRED_OHLCV_COLS) - set(df.columns)
    if missing:
        raise ValueError(f"DataFrame OHLCV kehilangan kolom: {sorted(missing)}")
    if len(df) < min_rows:
        raise ValueError(
            f"Butuh minimal {min_rows} baris, dapat {len(df)} baris"
        )


def ensure_sorted(df: pd.DataFrame) -> pd.DataFrame:
    """Return copy DataFrame terurut ascending by timestamp, index di-reset."""
    return df.sort_values("timestamp").reset_index(drop=True).copy()
