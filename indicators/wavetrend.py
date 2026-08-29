"""
WaveTrend Oscillator — Python port (LazyBear origin, dipakai LuxAlgo).

Source strategi: https://www.luxalgo.com/library/
Backtest LuxAlgo (referensi): PF 2.20, WR 67% (ETH/USD 15m)
Fungsi: momentum exit timing, precision TP (lihat STRATEGY.md).

Logic:
    ap  = (high + low + close) / 3                      # average price
    esa = EMA(ap, channel_len)
    d   = EMA(|ap - esa|, channel_len)
    ci  = (ap - esa) / (0.015 * d)
    wt1 = EMA(ci, avg_len)                               # fast line
    wt2 = SMA(wt1, ma_len)                                # slow line

Signals:
    - Cross wt1 di atas wt2 saat wt1 <= oversold  -> potential long
    - Cross wt1 di bawah wt2 saat wt1 >= overbought -> potential short
    - wt1 melewati zero line -> momentum shift (bullish/bearish)

Output kolom:
    wt1, wt2                -> fast / slow WaveTrend line
    wt_cross_up, wt_cross_down -> bool, cross event (tanpa filter zona)
    wavetrend_signal         -> 1 (long) / -1 (short) / 0 (none)
        Sinyal HANYA valid kalau cross terjadi di zona oversold/overbought
        (entry trigger), sesuai role-nya di confluence framework.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from indicators._utils import ensure_sorted, require_ohlcv

DEFAULT_CHANNEL_LEN: int = 10
DEFAULT_AVG_LEN: int = 21
DEFAULT_MA_LEN: int = 4
DEFAULT_OVERBOUGHT: float = 60.0
DEFAULT_OVERSOLD: float = -60.0


def compute(
    df: pd.DataFrame,
    channel_len: int = DEFAULT_CHANNEL_LEN,
    avg_len: int = DEFAULT_AVG_LEN,
    ma_len: int = DEFAULT_MA_LEN,
    overbought: float = DEFAULT_OVERBOUGHT,
    oversold: float = DEFAULT_OVERSOLD,
) -> pd.DataFrame:
    """
    Hitung WaveTrend Oscillator di atas OHLCV DataFrame.

    Args:
        df: OHLCV DataFrame.
        channel_len: Periode EMA untuk esa & d (default 10, "channel length").
        avg_len: Periode EMA untuk wt1 (default 21, "average length").
        ma_len: Periode SMA untuk wt2 (default 4).
        overbought / oversold: Ambang zona ekstrem WaveTrend (default +-60).

    Returns:
        Copy df + kolom indikator (lihat docstring modul).
    """
    min_rows = channel_len + avg_len + ma_len + 2
    require_ohlcv(df, min_rows=min_rows)
    out = ensure_sorted(df)

    ap = (out["high"] + out["low"] + out["close"]) / 3.0
    esa = ap.ewm(span=channel_len, adjust=False, min_periods=channel_len).mean()
    d = (ap - esa).abs().ewm(
        span=channel_len, adjust=False, min_periods=channel_len
    ).mean()
    d_safe = d.replace(0.0, np.nan)
    ci = (ap - esa) / (0.015 * d_safe)

    wt1 = ci.ewm(span=avg_len, adjust=False, min_periods=avg_len).mean()
    wt2 = wt1.rolling(ma_len).mean()

    out["wt1"] = wt1
    out["wt2"] = wt2

    diff = wt1 - wt2
    diff_prev = diff.shift(1)
    cross_up = (diff_prev <= 0) & (diff > 0)
    cross_down = (diff_prev >= 0) & (diff < 0)

    out["wt_cross_up"] = cross_up.fillna(False)
    out["wt_cross_down"] = cross_down.fillna(False)

    long_signal = (out["wt_cross_up"] & (wt1 <= oversold)).fillna(False)
    short_signal = (out["wt_cross_down"] & (wt1 >= overbought)).fillna(False)

    out["wavetrend_signal"] = 0
    out.loc[long_signal, "wavetrend_signal"] = 1
    out.loc[short_signal, "wavetrend_signal"] = -1

    return out
