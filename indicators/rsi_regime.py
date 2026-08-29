"""
RSI Regime Filter — Python port.

Source strategi: https://www.luxalgo.com/library/indicator/rsi-regime-filter/
Fungsi: validasi momentum, anti-fading runaway trend (lihat STRATEGY.md).

Logic:
    1. Hitung RSI(period) via Wilder smoothing.
    2. Hitung ADX(period) untuk klasifikasi regime: trending vs ranging.
    3. Regime = "trending" kalau ADX > adx_threshold, else "ranging".
    4. Signal:
         - Ranging + RSI <= oversold  -> long (mean reversion)
         - Ranging + RSI >= overbought -> short (mean reversion)
         - Trending + RSI baru saja keluar dari oversold sambil trend naik
           -> long continuation (bukan fade)
         - Trending + RSI baru saja keluar dari overbought sambil trend turun
           -> short continuation
       Anti-pattern dari STRATEGY.md ditegakkan: TIDAK fade strong trend
       dengan RSI oversold/overbought saat regime == trending.

Output kolom:
    rsi                 -> RSI(period)
    adx                 -> ADX(period)
    plus_di, minus_di   -> +DI / -DI (dipakai untuk arah trend)
    regime              -> "trending" | "ranging"
    rsi_regime_signal   -> 1 (long) / -1 (short) / 0 (none)
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from indicators._utils import ensure_sorted, require_ohlcv

DEFAULT_RSI_PERIOD: int = 14
DEFAULT_ADX_PERIOD: int = 14
DEFAULT_ADX_THRESHOLD: float = 25.0
DEFAULT_OVERBOUGHT: float = 70.0
DEFAULT_OVERSOLD: float = 30.0


def _wilder_rma(series: pd.Series, period: int) -> pd.Series:
    """Wilder's smoothed moving average (dipakai RSI & ADX klasik)."""
    return series.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def _rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = _wilder_rma(gain, period)
    avg_loss = _wilder_rma(loss, period)
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    # Kalau avg_loss == 0 dan avg_gain > 0 -> RSI = 100
    rsi = rsi.where(~((avg_loss == 0) & (avg_gain > 0)), 100.0)
    # Kalau keduanya 0 (flat market) -> RSI = 50 (netral)
    rsi = rsi.where(~((avg_loss == 0) & (avg_gain == 0)), 50.0)
    return rsi


def _adx(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int
) -> tuple[pd.Series, pd.Series, pd.Series]:
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
        index=high.index,
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
        index=high.index,
    )

    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    atr = _wilder_rma(tr, period)
    plus_di = 100.0 * _wilder_rma(plus_dm, period) / atr.replace(0.0, np.nan)
    minus_di = 100.0 * _wilder_rma(minus_dm, period) / atr.replace(0.0, np.nan)

    dx = (
        100.0
        * (plus_di - minus_di).abs()
        / (plus_di + minus_di).replace(0.0, np.nan)
    )
    adx = _wilder_rma(dx, period)
    return adx, plus_di, minus_di


def compute(
    df: pd.DataFrame,
    rsi_period: int = DEFAULT_RSI_PERIOD,
    adx_period: int = DEFAULT_ADX_PERIOD,
    adx_threshold: float = DEFAULT_ADX_THRESHOLD,
    overbought: float = DEFAULT_OVERBOUGHT,
    oversold: float = DEFAULT_OVERSOLD,
) -> pd.DataFrame:
    """
    Hitung RSI Regime Filter di atas OHLCV DataFrame.

    Args:
        df: OHLCV DataFrame.
        rsi_period: Periode RSI (default 14).
        adx_period: Periode ADX untuk deteksi regime (default 14).
        adx_threshold: ADX di atas nilai ini = regime trending.
        overbought / oversold: Ambang RSI klasik.

    Returns:
        Copy df + kolom indikator (lihat docstring modul).
    """
    min_rows = max(rsi_period, adx_period) * 2 + 2
    require_ohlcv(df, min_rows=min_rows)
    out = ensure_sorted(df)

    out["rsi"] = _rsi(out["close"], rsi_period)
    adx, plus_di, minus_di = _adx(out["high"], out["low"], out["close"], adx_period)
    out["adx"] = adx
    out["plus_di"] = plus_di
    out["minus_di"] = minus_di

    out["regime"] = np.where(out["adx"] > adx_threshold, "trending", "ranging")
    trending = out["regime"] == "trending"
    ranging = ~trending
    trend_up = out["plus_di"] > out["minus_di"]
    trend_down = out["minus_di"] > out["plus_di"]

    rsi_prev = out["rsi"].shift(1)
    rsi_leaving_oversold = (rsi_prev <= oversold) & (out["rsi"] > oversold)
    rsi_leaving_overbought = (rsi_prev >= overbought) & (out["rsi"] < overbought)

    # Ranging market: mean-reversion di ekstrem RSI.
    long_ranging = ranging & (out["rsi"] <= oversold)
    short_ranging = ranging & (out["rsi"] >= overbought)

    # Trending market: HANYA continuation, jangan fade (anti-pattern di STRATEGY.md).
    long_trending = trending & trend_up & rsi_leaving_oversold
    short_trending = trending & trend_down & rsi_leaving_overbought

    long_signal = (long_ranging | long_trending).fillna(False)
    short_signal = (short_ranging | short_trending).fillna(False)

    out["rsi_regime_signal"] = 0
    out.loc[long_signal, "rsi_regime_signal"] = 1
    out.loc[short_signal, "rsi_regime_signal"] = -1

    return out
