"""
Unit tests untuk Phase 2 — Core Indicator Engine.

Cakupan:
- Setiap indikator: kolom output ada, tipe benar, tidak error di data sintetis
- Signal kolom hanya berisi {-1, 0, 1}
- Validasi input (missing columns, terlalu sedikit baris) raise error yang benar
- Edge cases: flat price (no volatility), NaN handling
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from indicators import (  # noqa: E402
    compute_luminance,
    compute_rsi_regime,
    compute_structure,
    compute_wavetrend,
)
from indicators._utils import require_ohlcv  # noqa: E402


# --- Fixtures ---
def make_ohlcv(n: int = 300, seed: int = 42, trend: float = 0.0) -> pd.DataFrame:
    """Generate synthetic OHLCV data (random walk + optional drift)."""
    rng = np.random.default_rng(seed)
    steps = rng.normal(loc=trend, scale=1.0, size=n)
    close = 100 + np.cumsum(steps)
    close = np.maximum(close, 1.0)  # keep positive

    open_ = np.empty(n)
    open_[0] = close[0]
    open_[1:] = close[:-1]

    high = np.maximum(open_, close) + rng.uniform(0.1, 1.5, size=n)
    low = np.minimum(open_, close) - rng.uniform(0.1, 1.5, size=n)
    low = np.maximum(low, 0.01)
    volume = rng.uniform(100, 1000, size=n)
    # Sprinkle a few volume spikes to exercise breakout logic
    spike_idx = rng.choice(n, size=max(1, n // 20), replace=False)
    volume[spike_idx] *= rng.uniform(2.0, 4.0, size=len(spike_idx))

    timestamp = 1_700_000_000_000 + np.arange(n) * 3_600_000  # 1h candles, ms

    return pd.DataFrame(
        {
            "timestamp": timestamp,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )


@pytest.fixture()
def ohlcv() -> pd.DataFrame:
    return make_ohlcv(n=300, seed=1)


@pytest.fixture()
def ohlcv_uptrend() -> pd.DataFrame:
    return make_ohlcv(n=300, seed=2, trend=0.35)


@pytest.fixture()
def ohlcv_flat() -> pd.DataFrame:
    """Perfectly flat price series (edge case: zero volatility)."""
    n = 100
    timestamp = 1_700_000_000_000 + np.arange(n) * 3_600_000
    return pd.DataFrame(
        {
            "timestamp": timestamp,
            "open": [100.0] * n,
            "high": [100.0] * n,
            "low": [100.0] * n,
            "close": [100.0] * n,
            "volume": [500.0] * n,
        }
    )


# --- _utils.require_ohlcv ---
class TestRequireOhlcv:
    def test_valid_df_passes(self, ohlcv: pd.DataFrame) -> None:
        require_ohlcv(ohlcv, min_rows=10)  # should not raise

    def test_not_a_dataframe_raises_typeerror(self) -> None:
        with pytest.raises(TypeError):
            require_ohlcv([1, 2, 3])  # type: ignore[arg-type]

    def test_missing_columns_raises_valueerror(self) -> None:
        df = pd.DataFrame({"timestamp": [1, 2], "close": [1.0, 2.0]})
        with pytest.raises(ValueError):
            require_ohlcv(df)

    def test_too_few_rows_raises_valueerror(self, ohlcv: pd.DataFrame) -> None:
        with pytest.raises(ValueError):
            require_ohlcv(ohlcv.head(2), min_rows=50)


# --- Luminance Breakout Engine ---
class TestLuminance:
    EXPECTED_COLS = {
        "range_high",
        "range_low",
        "vol_avg",
        "is_consolidating",
        "bars_in_consolidation",
        "luminance_breakout_up",
        "luminance_breakout_down",
        "luminance_signal",
    }

    def test_runs_without_error_and_has_columns(self, ohlcv: pd.DataFrame) -> None:
        result = compute_luminance(ohlcv)
        assert self.EXPECTED_COLS.issubset(result.columns)
        assert len(result) == len(ohlcv)

    def test_signal_values_are_valid(self, ohlcv: pd.DataFrame) -> None:
        result = compute_luminance(ohlcv)
        assert set(result["luminance_signal"].unique()).issubset({-1, 0, 1})

    def test_breakout_flags_match_signal(self, ohlcv: pd.DataFrame) -> None:
        result = compute_luminance(ohlcv)
        up_rows = result[result["luminance_breakout_up"]]
        down_rows = result[result["luminance_breakout_down"]]
        assert (up_rows["luminance_signal"] == 1).all()
        assert (down_rows["luminance_signal"] == -1).all()

    def test_flat_price_no_crash_no_breakout(self, ohlcv_flat: pd.DataFrame) -> None:
        result = compute_luminance(ohlcv_flat, range_lookback=10)
        assert result["luminance_signal"].abs().sum() == 0

    def test_too_few_rows_raises(self) -> None:
        tiny = make_ohlcv(n=5)
        with pytest.raises(ValueError):
            compute_luminance(tiny)


# --- RSI Regime Filter ---
class TestRsiRegime:
    EXPECTED_COLS = {
        "rsi",
        "adx",
        "plus_di",
        "minus_di",
        "regime",
        "rsi_regime_signal",
    }

    def test_runs_without_error_and_has_columns(self, ohlcv: pd.DataFrame) -> None:
        result = compute_rsi_regime(ohlcv)
        assert self.EXPECTED_COLS.issubset(result.columns)
        assert len(result) == len(ohlcv)

    def test_rsi_bounded_0_100(self, ohlcv: pd.DataFrame) -> None:
        result = compute_rsi_regime(ohlcv)
        rsi_valid = result["rsi"].dropna()
        assert (rsi_valid >= 0).all() and (rsi_valid <= 100).all()

    def test_regime_only_two_categories(self, ohlcv: pd.DataFrame) -> None:
        result = compute_rsi_regime(ohlcv)
        assert set(result["regime"].unique()).issubset({"trending", "ranging"})

    def test_signal_values_are_valid(self, ohlcv: pd.DataFrame) -> None:
        result = compute_rsi_regime(ohlcv)
        assert set(result["rsi_regime_signal"].unique()).issubset({-1, 0, 1})

    def test_no_short_signal_fading_uptrend(self, ohlcv_uptrend: pd.DataFrame) -> None:
        """Anti-pattern check: jangan fade strong uptrend di regime trending."""
        result = compute_rsi_regime(ohlcv_uptrend)
        bad = result[
            (result["regime"] == "trending")
            & (result["plus_di"] > result["minus_di"])
            & (result["rsi_regime_signal"] == -1)
        ]
        assert bad.empty

    def test_flat_price_rsi_is_neutral(self, ohlcv_flat: pd.DataFrame) -> None:
        result = compute_rsi_regime(ohlcv_flat, rsi_period=5, adx_period=5)
        rsi_valid = result["rsi"].dropna()
        assert np.allclose(rsi_valid, 50.0)


# --- BOS/CHoCH Structure ---
class TestStructure:
    EXPECTED_COLS = {
        "swing_high",
        "swing_low",
        "structure_bias",
        "bos_bullish",
        "bos_bearish",
        "choch_bullish",
        "choch_bearish",
        "structure_signal",
    }

    def test_runs_without_error_and_has_columns(self, ohlcv: pd.DataFrame) -> None:
        result = compute_structure(ohlcv)
        assert self.EXPECTED_COLS.issubset(result.columns)
        assert len(result) == len(ohlcv)

    def test_signal_values_are_valid(self, ohlcv: pd.DataFrame) -> None:
        result = compute_structure(ohlcv)
        assert set(result["structure_signal"].unique()).issubset({-1, 0, 1})

    def test_bos_and_choch_mutually_exclusive_per_direction(
        self, ohlcv: pd.DataFrame
    ) -> None:
        result = compute_structure(ohlcv)
        assert not (result["bos_bullish"] & result["choch_bullish"]).any()
        assert not (result["bos_bearish"] & result["choch_bearish"]).any()

    def test_bias_only_valid_values(self, ohlcv: pd.DataFrame) -> None:
        result = compute_structure(ohlcv)
        assert set(result["structure_bias"].dropna().unique()).issubset(
            {"up", "down"}
        )

    def test_too_few_rows_raises(self) -> None:
        tiny = make_ohlcv(n=3)
        with pytest.raises(ValueError):
            compute_structure(tiny)


# --- WaveTrend Oscillator ---
class TestWaveTrend:
    EXPECTED_COLS = {
        "wt1",
        "wt2",
        "wt_cross_up",
        "wt_cross_down",
        "wavetrend_signal",
    }

    def test_runs_without_error_and_has_columns(self, ohlcv: pd.DataFrame) -> None:
        result = compute_wavetrend(ohlcv)
        assert self.EXPECTED_COLS.issubset(result.columns)
        assert len(result) == len(ohlcv)

    def test_signal_values_are_valid(self, ohlcv: pd.DataFrame) -> None:
        result = compute_wavetrend(ohlcv)
        assert set(result["wavetrend_signal"].unique()).issubset({-1, 0, 1})

    def test_long_signal_only_in_oversold_zone(self, ohlcv: pd.DataFrame) -> None:
        result = compute_wavetrend(ohlcv, oversold=-60.0)
        longs = result[result["wavetrend_signal"] == 1]
        assert (longs["wt1"] <= -60.0).all()

    def test_short_signal_only_in_overbought_zone(self, ohlcv: pd.DataFrame) -> None:
        result = compute_wavetrend(ohlcv, overbought=60.0)
        shorts = result[result["wavetrend_signal"] == -1]
        assert (shorts["wt1"] >= 60.0).all()

    def test_flat_price_no_crash(self, ohlcv_flat: pd.DataFrame) -> None:
        result = compute_wavetrend(
            ohlcv_flat, channel_len=5, avg_len=5, ma_len=3
        )
        # d == 0 sepanjang seri flat -> wt1/wt2 harus NaN, bukan crash/inf
        assert not np.isinf(result["wt1"].dropna()).any()
        assert not np.isinf(result["wt2"].dropna()).any()

    def test_too_few_rows_raises(self) -> None:
        tiny = make_ohlcv(n=5)
        with pytest.raises(ValueError):
            compute_wavetrend(tiny)


# --- Integration: semua indikator jalan bareng di data yang sama ---
def test_all_indicators_together(ohlcv: pd.DataFrame) -> None:
    lum = compute_luminance(ohlcv)
    rsi = compute_rsi_regime(ohlcv)
    struct = compute_structure(ohlcv)
    wt = compute_wavetrend(ohlcv)

    for result in (lum, rsi, struct, wt):
        assert len(result) == len(ohlcv)
        assert result["timestamp"].equals(ohlcv["timestamp"])
