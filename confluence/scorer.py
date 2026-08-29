"""
Confluence Scorer — Phase 3.

Menggabungkan sinyal dari 4 indikator Phase 2 menjadi satu skor 0-4 per bar,
sesuai framework di STRATEGY.md:

    ★☆☆☆☆ (1/4) -> SKIP
    ★★☆☆☆ (2/4) -> SKIP
    ★★★☆☆ (3/4) -> VALID ENTRY (normal size)
    ★★★★☆ (4/4) -> A+ SETUP (size up 1.5x)

Untuk tiap bar:
    1. Kumpulkan sinyal dari luminance, rsi_regime, structure, wavetrend
       (masing-masing -1/0/1).
    2. Arah confluence = sisi (long/short) dengan jumlah sinyal align
       terbanyak. Kalau count long == count short (termasuk 0==0), arah
       dianggap None (no bias, otomatis SKIP).
    3. Skor = jumlah indikator yang align ke arah tersebut.
    4. Grade: skor >= CONFLUENCE_A_PLUS -> "A+", skor >= CONFLUENCE_MIN_VALID
       -> "valid", selain itu -> "skip".
    5. Risk levels (SL/TP1/TP2/R:R) dihitung dari struktur harga:
         - SL: level luminance range (breakout boundary) kalau tersedia,
           fallback ke swing terakhir dari structure indicator.
         - TP1 = entry +/- 1R, TP2 = entry +/- 2R, R = |entry - SL|.
       Baris tanpa SL valid (data belum cukup) -> risk kolom NaN.

Catatan penting: ini scoring MEKANIS berbasis sinyal 4 indikator yang sudah
dihitung Phase 2 — bukan discretionary entry rule penuh (mis. "BOS + pullback
ke demand zone" di STRATEGY.md butuh price action lanjutan yang belum
di-encode). Cukup untuk backtest awal (Phase 5) dan alerting (Phase 4).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from indicators import (
    compute_luminance,
    compute_rsi_regime,
    compute_structure,
    compute_wavetrend,
)
from indicators._utils import require_ohlcv
from src.config import (
    A_PLUS_SIZE_MULTIPLIER,
    CONFLUENCE_A_PLUS,
    CONFLUENCE_MIN_VALID,
    SKIP_SIZE_MULTIPLIER,
    VALID_SIZE_MULTIPLIER,
)

SIGNAL_COLUMNS: tuple[str, ...] = (
    "luminance_signal",
    "rsi_regime_signal",
    "structure_signal",
    "wavetrend_signal",
)

GRADE_SKIP: str = "skip"
GRADE_VALID: str = "valid"
GRADE_A_PLUS: str = "A+"


def merge_indicators(
    df: pd.DataFrame,
    luminance_kwargs: dict | None = None,
    rsi_regime_kwargs: dict | None = None,
    structure_kwargs: dict | None = None,
    wavetrend_kwargs: dict | None = None,
) -> pd.DataFrame:
    """
    Jalankan 4 indikator Phase 2 di atas OHLCV yang sama dan gabungkan
    kolom-kolom pentingnya jadi satu DataFrame (indexed selaras dengan `df`).

    Args:
        df: OHLCV DataFrame.
        *_kwargs: Override parameter opsional untuk masing-masing indikator.

    Returns:
        DataFrame dengan kolom OHLCV asli + semua kolom sinyal indikator +
        beberapa kolom pendukung (range_high/low, swing_high/low, regime).
    """
    lum = compute_luminance(df, **(luminance_kwargs or {}))
    rsi = compute_rsi_regime(df, **(rsi_regime_kwargs or {}))
    struct = compute_structure(df, **(structure_kwargs or {}))
    wt = compute_wavetrend(df, **(wavetrend_kwargs or {}))

    merged = df.copy()
    merged["luminance_signal"] = lum["luminance_signal"]
    merged["range_high"] = lum["range_high"]
    merged["range_low"] = lum["range_low"]

    merged["rsi_regime_signal"] = rsi["rsi_regime_signal"]
    merged["rsi"] = rsi["rsi"]
    merged["adx"] = rsi["adx"]
    merged["regime"] = rsi["regime"]

    merged["structure_signal"] = struct["structure_signal"]
    merged["structure_bias"] = struct["structure_bias"]
    merged["swing_high"] = struct["swing_high"].ffill()
    merged["swing_low"] = struct["swing_low"].ffill()

    merged["wavetrend_signal"] = wt["wavetrend_signal"]
    merged["wt1"] = wt["wt1"]
    merged["wt2"] = wt["wt2"]

    return merged


def _score_direction(merged: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Hitung arah confluence (long/short/None) dan skornya (0-4) per bar."""
    signals = merged[list(SIGNAL_COLUMNS)]
    long_count = (signals == 1).sum(axis=1)
    short_count = (signals == -1).sum(axis=1)

    direction = pd.Series(
        np.where(
            long_count > short_count,
            "long",
            np.where(short_count > long_count, "short", None),
        ),
        index=merged.index,
    )
    score = np.where(
        direction == "long", long_count, np.where(direction == "short", short_count, 0)
    )
    return direction, pd.Series(score, index=merged.index).astype(int)


def _grade_from_score(score: pd.Series) -> pd.Series:
    return pd.Series(
        np.select(
            [score >= CONFLUENCE_A_PLUS, score >= CONFLUENCE_MIN_VALID],
            [GRADE_A_PLUS, GRADE_VALID],
            default=GRADE_SKIP,
        ),
        index=score.index,
    )


def _size_multiplier_from_grade(grade: pd.Series) -> pd.Series:
    return pd.Series(
        np.select(
            [grade == GRADE_A_PLUS, grade == GRADE_VALID],
            [A_PLUS_SIZE_MULTIPLIER, VALID_SIZE_MULTIPLIER],
            default=SKIP_SIZE_MULTIPLIER,
        ),
        index=grade.index,
    )


def _compute_risk_levels(
    merged: pd.DataFrame, direction: pd.Series
) -> pd.DataFrame:
    """
    Hitung entry/SL/TP1/TP2/R:R per bar sesuai STRATEGY.md:
        - SL beyond range boundary (opposite side), fallback ke swing struktur.
        - TP1 = 1R, TP2 = 2R, R = |entry - SL|.

    Baris tanpa level SL yang valid akan berisi NaN (bukan error).
    """
    entry = merged["close"]

    # Long: SL dari batas bawah (range_low kalau ada, else swing_low).
    long_sl = merged["range_low"].where(
        merged["range_low"].notna(), merged["swing_low"]
    )
    # Short: SL dari batas atas.
    short_sl = merged["range_high"].where(
        merged["range_high"].notna(), merged["swing_high"]
    )

    stop_loss = pd.Series(np.nan, index=merged.index, dtype="float64")
    stop_loss = stop_loss.where(direction != "long", long_sl)
    stop_loss = stop_loss.where(direction != "short", short_sl)

    # SL harus di sisi yang benar relatif ke entry, atau invalid -> NaN.
    invalid_long = (direction == "long") & (stop_loss >= entry)
    invalid_short = (direction == "short") & (stop_loss <= entry)
    stop_loss = stop_loss.mask(invalid_long | invalid_short)

    risk = (entry - stop_loss).abs()
    tp1 = pd.Series(np.nan, index=merged.index, dtype="float64")
    tp2 = pd.Series(np.nan, index=merged.index, dtype="float64")

    is_long = direction == "long"
    is_short = direction == "short"
    tp1 = tp1.mask(is_long & risk.notna(), entry + risk)
    tp1 = tp1.mask(is_short & risk.notna(), entry - risk)
    tp2 = tp2.mask(is_long & risk.notna(), entry + 2 * risk)
    tp2 = tp2.mask(is_short & risk.notna(), entry - 2 * risk)

    risk_reward = pd.Series(np.where(risk.notna() & (risk > 0), 2.0, np.nan), index=merged.index)

    return pd.DataFrame(
        {
            "entry_price": entry,
            "stop_loss": stop_loss,
            "take_profit_1": tp1,
            "take_profit_2": tp2,
            "risk_reward": risk_reward,
        }
    )


def score_confluence(
    df: pd.DataFrame,
    luminance_kwargs: dict | None = None,
    rsi_regime_kwargs: dict | None = None,
    structure_kwargs: dict | None = None,
    wavetrend_kwargs: dict | None = None,
) -> pd.DataFrame:
    """
    Hitung confluence score penuh (indikator + skor + entry rules) untuk
    seluruh bar di `df`.

    Args:
        df: OHLCV DataFrame (timestamp, open, high, low, close, volume).
        *_kwargs: Override parameter opsional per indikator.

    Returns:
        DataFrame OHLCV + semua kolom indikator + kolom confluence:
            confluence_direction ("long"/"short"/None)
            confluence_score (0-4)
            confluence_grade ("skip"/"valid"/"A+")
            size_multiplier (0.0 / 1.0 / 1.5)
            entry_price, stop_loss, take_profit_1, take_profit_2, risk_reward
    """
    require_ohlcv(df, min_rows=1)
    merged = merge_indicators(
        df,
        luminance_kwargs=luminance_kwargs,
        rsi_regime_kwargs=rsi_regime_kwargs,
        structure_kwargs=structure_kwargs,
        wavetrend_kwargs=wavetrend_kwargs,
    )

    direction, score = _score_direction(merged)
    grade = _grade_from_score(score)
    size_mult = _size_multiplier_from_grade(grade)

    merged["confluence_direction"] = direction
    merged["confluence_score"] = score
    merged["confluence_grade"] = grade
    merged["size_multiplier"] = size_mult

    risk_df = _compute_risk_levels(merged, direction)
    merged = pd.concat([merged, risk_df], axis=1)

    return merged


def latest_confluence(df: pd.DataFrame, **kwargs) -> dict:
    """
    Convenience helper: hitung confluence untuk seluruh `df` lalu kembalikan
    ringkasan bar TERAKHIR sebagai dict (dipakai `main.py scan`).
    """
    result = score_confluence(df, **kwargs)
    last = result.iloc[-1]

    def _clean(val):
        if isinstance(val, float) and np.isnan(val):
            return None
        return val

    return {
        "close": float(last["close"]),
        "regime": _clean(last.get("regime")),
        "direction": _clean(last.get("confluence_direction")),
        "score": int(last["confluence_score"]),
        "grade": str(last["confluence_grade"]),
        "size_multiplier": float(last["size_multiplier"]),
        "entry_price": _clean(last.get("entry_price")),
        "stop_loss": _clean(last.get("stop_loss")),
        "take_profit_1": _clean(last.get("take_profit_1")),
        "take_profit_2": _clean(last.get("take_profit_2")),
        "risk_reward": _clean(last.get("risk_reward")),
        "signals": {
            "luminance": int(last["luminance_signal"]),
            "rsi_regime": int(last["rsi_regime_signal"]),
            "structure": int(last["structure_signal"]),
            "wavetrend": int(last["wavetrend_signal"]),
        },
    }
