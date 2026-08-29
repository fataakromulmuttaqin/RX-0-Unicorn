"""
Unit tests untuk Phase 3 — Confluence Scorer.

Cakupan:
- score_confluence: kolom output lengkap, score 0-4, grade konsisten dengan score
- Risk levels (SL/TP1/TP2/R:R) konsisten secara arah (long SL < entry < TP, dst)
- latest_confluence: ringkasan bar terakhir, tipe native Python (bukan numpy)
- Edge cases: direction None -> skip & risk kolom NaN
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

from confluence import (  # noqa: E402
    GRADE_A_PLUS,
    GRADE_SKIP,
    GRADE_VALID,
    latest_confluence,
    score_confluence,
)
from src.config import CONFLUENCE_A_PLUS, CONFLUENCE_MIN_VALID  # noqa: E402
from tests.test_indicators import make_ohlcv  # noqa: E402


@pytest.fixture()
def ohlcv() -> pd.DataFrame:
    return make_ohlcv(n=300, seed=1)


@pytest.fixture()
def ohlcv_uptrend() -> pd.DataFrame:
    return make_ohlcv(n=300, seed=2, trend=0.35)


class TestScoreConfluence:
    EXPECTED_COLS = {
        "confluence_direction",
        "confluence_score",
        "confluence_grade",
        "size_multiplier",
        "entry_price",
        "stop_loss",
        "take_profit_1",
        "take_profit_2",
        "risk_reward",
        "luminance_signal",
        "rsi_regime_signal",
        "structure_signal",
        "wavetrend_signal",
    }

    def test_runs_without_error_and_has_columns(self, ohlcv: pd.DataFrame) -> None:
        result = score_confluence(ohlcv)
        assert self.EXPECTED_COLS.issubset(result.columns)
        assert len(result) == len(ohlcv)

    def test_score_bounded_0_to_4(self, ohlcv: pd.DataFrame) -> None:
        result = score_confluence(ohlcv)
        assert result["confluence_score"].min() >= 0
        assert result["confluence_score"].max() <= 4

    def test_grade_matches_score_thresholds(self, ohlcv: pd.DataFrame) -> None:
        result = score_confluence(ohlcv)
        for _, row in result.iterrows():
            score = row["confluence_score"]
            grade = row["confluence_grade"]
            if score >= CONFLUENCE_A_PLUS:
                assert grade == GRADE_A_PLUS
            elif score >= CONFLUENCE_MIN_VALID:
                assert grade == GRADE_VALID
            else:
                assert grade == GRADE_SKIP

    def test_size_multiplier_matches_grade(self, ohlcv: pd.DataFrame) -> None:
        result = score_confluence(ohlcv)
        a_plus = result[result["confluence_grade"] == GRADE_A_PLUS]
        valid = result[result["confluence_grade"] == GRADE_VALID]
        skip = result[result["confluence_grade"] == GRADE_SKIP]
        if not a_plus.empty:
            assert (a_plus["size_multiplier"] == 1.5).all()
        if not valid.empty:
            assert (valid["size_multiplier"] == 1.0).all()
        if not skip.empty:
            assert (skip["size_multiplier"] == 0.0).all()

    def test_direction_none_when_tied_or_no_signal(self, ohlcv: pd.DataFrame) -> None:
        result = score_confluence(ohlcv)
        no_dir = result[result["confluence_direction"].isna()]
        # Kalau tidak ada arah, score harus 0 dan grade skip.
        assert (no_dir["confluence_score"] == 0).all()
        assert (no_dir["confluence_grade"] == GRADE_SKIP).all()

    def test_long_risk_levels_ordered_correctly(self, ohlcv: pd.DataFrame) -> None:
        result = score_confluence(ohlcv)
        longs = result[
            (result["confluence_direction"] == "long") & result["stop_loss"].notna()
        ]
        if not longs.empty:
            assert (longs["stop_loss"] < longs["entry_price"]).all()
            assert (longs["take_profit_1"] > longs["entry_price"]).all()
            assert (longs["take_profit_2"] > longs["take_profit_1"]).all()

    def test_short_risk_levels_ordered_correctly(self, ohlcv: pd.DataFrame) -> None:
        result = score_confluence(ohlcv)
        shorts = result[
            (result["confluence_direction"] == "short") & result["stop_loss"].notna()
        ]
        if not shorts.empty:
            assert (shorts["stop_loss"] > shorts["entry_price"]).all()
            assert (shorts["take_profit_1"] < shorts["entry_price"]).all()
            assert (shorts["take_profit_2"] < shorts["take_profit_1"]).all()

    def test_risk_reward_is_two_when_present(self, ohlcv: pd.DataFrame) -> None:
        result = score_confluence(ohlcv)
        valid_rr = result["risk_reward"].dropna()
        if not valid_rr.empty:
            assert np.allclose(valid_rr, 2.0)

    def test_rows_without_stop_loss_have_nan_targets(self, ohlcv: pd.DataFrame) -> None:
        result = score_confluence(ohlcv)
        no_sl = result[result["stop_loss"].isna()]
        assert no_sl["take_profit_1"].isna().all()
        assert no_sl["take_profit_2"].isna().all()
        assert no_sl["risk_reward"].isna().all()

    def test_too_few_rows_raises(self) -> None:
        tiny = make_ohlcv(n=5)
        with pytest.raises(ValueError):
            score_confluence(tiny)


class TestLatestConfluence:
    def test_returns_expected_keys(self, ohlcv: pd.DataFrame) -> None:
        summary = latest_confluence(ohlcv)
        expected_keys = {
            "close",
            "regime",
            "direction",
            "score",
            "grade",
            "size_multiplier",
            "entry_price",
            "stop_loss",
            "take_profit_1",
            "take_profit_2",
            "risk_reward",
            "signals",
        }
        assert expected_keys.issubset(summary.keys())

    def test_values_are_native_python_types(self, ohlcv: pd.DataFrame) -> None:
        summary = latest_confluence(ohlcv)
        assert isinstance(summary["score"], int)
        assert isinstance(summary["grade"], str)
        assert isinstance(summary["close"], float)
        assert isinstance(summary["signals"], dict)
        for v in summary["signals"].values():
            assert isinstance(v, int)

    def test_nan_becomes_none(self, ohlcv: pd.DataFrame) -> None:
        summary = latest_confluence(ohlcv)
        # Kalau tidak ada arah/stop_loss, harus None bukan NaN float.
        if summary["direction"] is None:
            assert summary["stop_loss"] is None
            assert summary["risk_reward"] is None

    def test_score_consistent_with_signals(self, ohlcv: pd.DataFrame) -> None:
        summary = latest_confluence(ohlcv)
        signals = summary["signals"]
        if summary["direction"] == "long":
            expected = sum(1 for v in signals.values() if v == 1)
        elif summary["direction"] == "short":
            expected = sum(1 for v in signals.values() if v == -1)
        else:
            expected = 0
        assert summary["score"] == expected


def test_confluence_end_to_end_uptrend_smoke(ohlcv_uptrend: pd.DataFrame) -> None:
    """Smoke test: pastikan pipeline lengkap jalan mulus di data trending."""
    result = score_confluence(ohlcv_uptrend)
    assert not result.empty
    assert result["confluence_score"].notna().all()
    summary = latest_confluence(ohlcv_uptrend)
    assert summary["grade"] in {GRADE_SKIP, GRADE_VALID, GRADE_A_PLUS}
