"""
Luminance Breakout Engine — Python port.

Source strategi: https://www.luxalgo.com/library/indicator/luminance-breakout-engine/
Backtest LuxAlgo (referensi): PF 2.33, WR 71.6% (BTCUSDT 1H, 102 trades)

Logic (lihat STRATEGY.md):
    1. Identifikasi range/consolidation zone (rolling high/low N bar).
    2. Wait for breakout candle: close menembus batas range.
    3. Confirm dengan volume spike >= volume_threshold x rolling average volume.
    4. Filter opsional: minimal jumlah bar konsolidasi sebelum breakout valid.

Output kolom:
    range_high, range_low          -> rolling boundary (lookback bar sebelumnya)
    vol_avg                        -> rolling average volume
    is_consolidating               -> bool, range saat ini "sempit" (< threshold)
    bars_in_consolidation          -> jumlah bar consolidating berturut-turut
    luminance_breakout_up          -> bool, breakout ke atas + volume confirm
    luminance_breakout_down        -> bool, breakout ke bawah + volume confirm
    luminance_signal                -> 1 (long) / -1 (short) / 0 (none)
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from indicators._utils import ensure_sorted, require_ohlcv

# --- Default parameters (lihat STRATEGY.md) ---
DEFAULT_RANGE_LOOKBACK: int = 20
DEFAULT_VOLUME_THRESHOLD: float = 1.5
DEFAULT_MIN_CONSOLIDATION_BARS: int = 5
# Range dianggap "consolidating" kalau lebar range <= X% dari harga close.
DEFAULT_CONSOLIDATION_WIDTH_PCT: float = 0.03


def compute(
    df: pd.DataFrame,
    range_lookback: int = DEFAULT_RANGE_LOOKBACK,
    volume_threshold: float = DEFAULT_VOLUME_THRESHOLD,
    min_consolidation_bars: int = DEFAULT_MIN_CONSOLIDATION_BARS,
    consolidation_width_pct: float = DEFAULT_CONSOLIDATION_WIDTH_PCT,
) -> pd.DataFrame:
    """
    Hitung Luminance Breakout Engine di atas OHLCV DataFrame.

    Args:
        df: OHLCV DataFrame (timestamp, open, high, low, close, volume).
        range_lookback: Jumlah bar untuk hitung rolling range boundary.
        volume_threshold: Kelipatan minimal volume vs rolling average
            supaya breakout dianggap valid (default 1.5x).
        min_consolidation_bars: Minimal bar consolidating sebelum breakout
            dianggap valid (filter anti-false-breakout).
        consolidation_width_pct: Ambang lebar range (relatif ke close) untuk
            dianggap "consolidating".

    Returns:
        Copy df + kolom indikator (lihat docstring modul).
    """
    require_ohlcv(df, min_rows=range_lookback + 2)
    out = ensure_sorted(df)

    # Range boundary dihitung dari N bar SEBELUM bar saat ini (shift 1) agar
    # tidak look-ahead: breakout candle sendiri tidak termasuk range-nya.
    out["range_high"] = out["high"].rolling(range_lookback).max().shift(1)
    out["range_low"] = out["low"].rolling(range_lookback).min().shift(1)
    out["vol_avg"] = out["volume"].rolling(range_lookback).mean().shift(1)

    range_width = out["range_high"] - out["range_low"]
    # Hindari division by zero -> inf; replace inf dengan NaN secara eksplisit
    # (pandas 3.x tidak lagi punya opsi 'mode.use_inf_as_na').
    width_pct = (range_width / out["close"]).abs()
    width_pct = width_pct.replace([np.inf, -np.inf], np.nan)
    out["is_consolidating"] = width_pct <= consolidation_width_pct

    # Hitung berapa lama consolidating berturut-turut (reset saat False).
    consolidating = out["is_consolidating"].fillna(False)
    grp = (~consolidating).cumsum()
    out["bars_in_consolidation"] = (
        consolidating.groupby(grp).cumsum().astype(int)
    )

    vol_confirm = out["volume"] >= (out["vol_avg"] * volume_threshold)
    had_enough_consolidation = (
        out["bars_in_consolidation"].shift(1).fillna(0) >= min_consolidation_bars
    )

    breakout_up = (
        (out["close"] > out["range_high"])
        & vol_confirm
        & had_enough_consolidation
    )
    breakout_down = (
        (out["close"] < out["range_low"])
        & vol_confirm
        & had_enough_consolidation
    )

    out["luminance_breakout_up"] = breakout_up.fillna(False)
    out["luminance_breakout_down"] = breakout_down.fillna(False)

    out["luminance_signal"] = 0
    out.loc[out["luminance_breakout_up"], "luminance_signal"] = 1
    out.loc[out["luminance_breakout_down"], "luminance_signal"] = -1

    return out
