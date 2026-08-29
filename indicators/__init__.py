"""
Indicator Engine — Phase 2 RX-0 Unicorn.

Python port dari 4 strategi LuxAlgo yang jadi pondasi confluence framework:
    1. Luminance Breakout Engine  -> indicators.luminance
    2. RSI Regime Filter          -> indicators.rsi_regime
    3. BOS/CHoCH Structure        -> indicators.structure
    4. WaveTrend Oscillator       -> indicators.wavetrend

Setiap modul mengekspos fungsi `compute(df, ...) -> pd.DataFrame` yang
menerima OHLCV DataFrame (kolom: timestamp, open, high, low, close, volume)
dan mengembalikan DataFrame yang sama ditambah kolom indikator + kolom
`*_signal` (1=long, -1=short, 0=none).
"""

from indicators.luminance import compute as compute_luminance
from indicators.rsi_regime import compute as compute_rsi_regime
from indicators.structure import compute as compute_structure
from indicators.wavetrend import compute as compute_wavetrend

__all__ = [
    "compute_luminance",
    "compute_rsi_regime",
    "compute_structure",
    "compute_wavetrend",
]
