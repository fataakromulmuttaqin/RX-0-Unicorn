"""
BOS/CHoCH Structure Dashboard — Python port.

Source strategi: https://www.luxalgo.com/library/indicator/market-structure-scatter-dashboard/
Fungsi: konfirmasi structural break, filter noise (lihat STRATEGY.md).

Definitions:
    BOS (Break of Structure)   : price breaks previous swing high/low,
                                  confirming trend continuation.
    CHoCH (Change of Character): first structural break AGAINST prevailing
                                  trend (potential reversal).

Logic:
    1. Deteksi swing high/low pakai fractal N-bar (default 2 kiri, 2 kanan).
    2. Track trend bias saat ini: "up" / "down" / None (belum ada bias).
    3. Bar break di atas swing high terakhir:
         - kalau bias sudah "up" atau None -> BOS bullish, bias jadi "up"
         - kalau bias "down" -> CHoCH bullish (reversal), bias jadi "up"
    4. Simetris untuk break di bawah swing low terakhir.

Output kolom:
    swing_high, swing_low       -> harga swing point terkonfirmasi (fractal), else NaN
    structure_bias              -> "up" | "down" | None, trend bias berjalan
    bos_bullish, bos_bearish    -> bool, break of structure searah trend
    choch_bullish, choch_bearish-> bool, change of character (reversal)
    structure_signal            -> 1 (long) / -1 (short) / 0 (none)
        BOS & CHoCH searah entry sama-sama dianggap sinyal valid untuk
        confluence (BOS = continuation, CHoCH = early reversal).
"""

from __future__ import annotations

import pandas as pd

from indicators._utils import ensure_sorted, require_ohlcv

DEFAULT_FRACTAL_LEFT: int = 2
DEFAULT_FRACTAL_RIGHT: int = 2


def _find_fractal_swings(
    high: pd.Series, low: pd.Series, left: int, right: int
) -> tuple[pd.Series, pd.Series]:
    """
    Deteksi fractal swing high/low.

    Bar i adalah swing high kalau high[i] adalah nilai TERTINGGI dalam
    window [i-left, i+right]. Simetris untuk swing low. Hasil disimpan
    pada indeks bar i (bukan digeser), karena konfirmasi baru terjadi
    `right` bar kemudian secara alami lewat rolling window yang center.
    """
    window = left + right + 1
    is_high = (
        high.rolling(window, center=True).max() == high
    ) & high.rolling(window, center=True).count().eq(window)
    is_low = (
        low.rolling(window, center=True).min() == low
    ) & low.rolling(window, center=True).count().eq(window)

    swing_high = high.where(is_high)
    swing_low = low.where(is_low)
    return swing_high, swing_low


def compute(
    df: pd.DataFrame,
    fractal_left: int = DEFAULT_FRACTAL_LEFT,
    fractal_right: int = DEFAULT_FRACTAL_RIGHT,
) -> pd.DataFrame:
    """
    Hitung BOS/CHoCH structure di atas OHLCV DataFrame.

    Args:
        df: OHLCV DataFrame.
        fractal_left: Jumlah bar di kiri untuk validasi fractal swing.
        fractal_right: Jumlah bar di kanan untuk validasi fractal swing
            (menentukan lag konfirmasi swing point).

    Returns:
        Copy df + kolom indikator (lihat docstring modul).
    """
    min_rows = fractal_left + fractal_right + 3
    require_ohlcv(df, min_rows=min_rows)
    out = ensure_sorted(df)

    swing_high, swing_low = _find_fractal_swings(
        out["high"], out["low"], fractal_left, fractal_right
    )
    out["swing_high"] = swing_high
    out["swing_low"] = swing_low

    # "Last known" swing level yang sudah terkonfirmasi sampai bar ini
    # (forward-fill, lalu shift 1 supaya bar breakout tidak membandingkan
    # dengan swing dari dirinya sendiri).
    last_swing_high = swing_high.ffill().shift(1)
    last_swing_low = swing_low.ffill().shift(1)

    bias: list[str | None] = [None] * len(out)
    bos_bull = [False] * len(out)
    bos_bear = [False] * len(out)
    choch_bull = [False] * len(out)
    choch_bear = [False] * len(out)

    current_bias: str | None = None
    closes = out["close"].to_numpy()
    lsh = last_swing_high.to_numpy()
    lsl = last_swing_low.to_numpy()

    for i in range(len(out)):
        broke_up = pd.notna(lsh[i]) and closes[i] > lsh[i]
        broke_down = pd.notna(lsl[i]) and closes[i] < lsl[i]

        # Kalau break dua arah sekaligus (range sangat lebar/data jarang),
        # prioritaskan arah yang berlawanan dengan bias saat ini sebagai CHoCH.
        if broke_up and broke_down:
            if current_bias == "up":
                broke_up, broke_down = False, True
            else:
                broke_up, broke_down = True, False

        if broke_up:
            if current_bias == "down":
                choch_bull[i] = True
            else:
                bos_bull[i] = True
            current_bias = "up"
        elif broke_down:
            if current_bias == "up":
                choch_bear[i] = True
            else:
                bos_bear[i] = True
            current_bias = "down"

        bias[i] = current_bias

    out["structure_bias"] = bias
    out["bos_bullish"] = bos_bull
    out["bos_bearish"] = bos_bear
    out["choch_bullish"] = choch_bull
    out["choch_bearish"] = choch_bear

    out["structure_signal"] = 0
    long_mask = out["bos_bullish"] | out["choch_bullish"]
    short_mask = out["bos_bearish"] | out["choch_bearish"]
    out.loc[long_mask, "structure_signal"] = 1
    out.loc[short_mask, "structure_signal"] = -1

    return out
