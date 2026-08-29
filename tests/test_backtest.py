"""
Unit tests untuk Phase 5 — Backtest Engine.

Cakupan (~30 tests):
- data_loader: cache hit, cache miss, fetch from CCXT (mocked), required_candles
- engine: trade lifecycle (TP1/SL/time-stop), position sizing, no-look-ahead,
  multiple signals, run_backtest full integration
- metrics: 6 mandatory metrics, edge cases (0/1/all-wins/all-losses), target_check
- report: format_report keys, to_json roundtrip, to_equity_curve_chart creates file
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtest import (  # noqa: E402
    BacktestResult,
    Trade,
    calculate_metrics,
    empty_metrics,
    ensure_data,
    format_report,
    run_backtest,
    simulate_trade,
    to_equity_curve_chart,
    to_json,
)
from backtest.data_loader import last_n_days, required_candles  # noqa: E402
from backtest.metrics import PROFIT_FACTOR_CAP, target_check  # noqa: E402
from confluence.scorer import score_confluence  # noqa: E402
from data.storage.candle_db import CandleDB  # noqa: E402
from src.config import (  # noqa: E402
    BACKTEST_INITIAL_CAPITAL,
    BACKTEST_MAX_BARS_HOLD,
    BACKTEST_RISK_PER_TRADE,
    TARGET_AVG_R_MULTIPLE,
    TARGET_MAX_DRAWDOWN,
    TARGET_PROFIT_FACTOR,
    TARGET_SHARPE,
    TARGET_WIN_RATE,
)
from tests.test_indicators import make_ohlcv  # noqa: E402


# --- Fixtures ---------------------------------------------------------------
@pytest.fixture
def ohlcv_500() -> pd.DataFrame:
    """500 bar synthetic 1h OHLCV, 1h spacing, ~20 hari."""
    return make_ohlcv(n=500, seed=11)


@pytest.fixture
def ohlcv_200() -> pd.DataFrame:
    """200 bar synthetic 1h OHLCV."""
    return make_ohlcv(n=200, seed=12)


@pytest.fixture
def ohlcv_uptrend_500() -> pd.DataFrame:
    """500 bar uptrending 1h OHLCV."""
    return make_ohlcv(n=500, seed=13, trend=0.5)


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    """SQLite DB di tmp path."""
    return tmp_path / "test_backtest.db"


# =========================================================================
# Tests: data_loader
# =========================================================================
class TestDataLoaderHelpers:
    def test_required_candles_1h_30days(self) -> None:
        # 30 days * 24 candles/day = 720, plus 10% buffer = 792
        n = required_candles(30, "1h")
        assert n == int(30 * 24 * 1.1)

    def test_required_candles_15m_7days(self) -> None:
        # 7 * 96 * 1.1 = 739.2 -> 739
        n = required_candles(7, "15m")
        assert n == int(7 * 96 * 1.1)

    def test_required_candles_invalid_days(self) -> None:
        with pytest.raises(ValueError):
            required_candles(0, "1h")
        with pytest.raises(ValueError):
            required_candles(-1, "1h")

    def test_required_candles_invalid_timeframe(self) -> None:
        with pytest.raises(ValueError):
            required_candles(30, "99h")

    def test_last_n_days_trims_correctly(self, ohlcv_500: pd.DataFrame) -> None:
        # 500 bar @ 1h = ~20.8 hari
        out = last_n_days(ohlcv_500, days=5)
        assert len(out) < len(ohlcv_500)
        # 5 hari = 120 bar
        assert len(out) == 120 or len(out) == 121  # boundary tolerance

    def test_last_n_days_empty_input(self) -> None:
        empty = pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        assert last_n_days(empty, 10).empty


class TestEnsureData:
    def test_cache_hit_returns_from_db(
        self, tmp_db: Path, ohlcv_200: pd.DataFrame
    ) -> None:
        # Pre-populate DB
        with CandleDB(db_path=tmp_db) as db:
            db.insert_candles(ohlcv_200, pair="BTC/USDT", timeframe="1h")

        with CandleDB(db_path=tmp_db) as db:
            # days_back=3 -> only need ~79 rows (3*24*1.1)
            df = ensure_data(
                symbol="BTC/USDT",
                timeframe="1h",
                days_back=3,
                db=db,
                force_refresh=False,
            )
        assert not df.empty
        # Should return last `required_candles(3, "1h")` rows
        expected = required_candles(3, "1h")
        assert len(df) == expected

    def test_cache_miss_falls_back_to_exchange(
        self, tmp_db: Path, ohlcv_200: pd.DataFrame, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # DB kosong -> harus fetch. Mock fetch_from_exchange agar tidak network.
        from backtest import data_loader

        def fake_fetch(symbol, timeframe, limit):
            return ohlcv_200.copy()

        monkeypatch.setattr(data_loader, "fetch_from_exchange", fake_fetch)

        with CandleDB(db_path=tmp_db) as db:
            df = ensure_data(
                symbol="BTC/USDT",
                timeframe="1h",
                days_back=3,
                db=db,
                force_refresh=False,
            )
        assert not df.empty
        assert len(df) == len(ohlcv_200)
        # Verify ditulis ke DB
        with CandleDB(db_path=tmp_db) as db:
            stored = db.get_candles("BTC/USDT", "1h")
        assert len(stored) == len(ohlcv_200)

    def test_force_refresh_bypasses_cache(
        self, tmp_db: Path, ohlcv_200: pd.DataFrame, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from backtest import data_loader

        def fake_fetch(symbol, timeframe, limit):
            return ohlcv_200.copy()

        monkeypatch.setattr(data_loader, "fetch_from_exchange", fake_fetch)

        # Pre-populate DB
        with CandleDB(db_path=tmp_db) as db:
            db.insert_candles(ohlcv_200, pair="BTC/USDT", timeframe="1h")

        with CandleDB(db_path=tmp_db) as db:
            df = ensure_data(
                symbol="BTC/USDT",
                timeframe="1h",
                days_back=3,
                db=db,
                force_refresh=True,
            )
        assert not df.empty
        # Force refresh artinya fetch dipanggil (kita assert via monkeypatch
        # dan jumlah bar di DB = jumlah bar fetched).
        assert len(df) == len(ohlcv_200)

    def test_normalize_symbol_no_slash(self) -> None:
        from backtest.data_loader import _normalize_symbol

        assert _normalize_symbol("BTCUSDT") == "BTC/USDT"
        assert _normalize_symbol("btcusdt") == "BTC/USDT"
        assert _normalize_symbol("ETH/USDT") == "ETH/USDT"
        assert _normalize_symbol("ETHBTC") == "ETH/BTC"


# =========================================================================
# Tests: engine
# =========================================================================
class TestPositionSizing:
    def test_position_size_units_basic(self) -> None:
        from backtest.engine import _position_size_units

        # equity=10_000, risk=0.02, size_mult=1.0 -> risk_dollar = 200
        # entry=100, stop=90 (diff=10) -> units = 200/10 = 20
        units, risk_dollar = _position_size_units(
            equity=10_000.0, risk_per_trade=0.02, size_multiplier=1.0,
            entry=100.0, stop=90.0,
        )
        assert units == pytest.approx(20.0)
        assert risk_dollar == pytest.approx(200.0)

    def test_position_size_a_plus_multiplier(self) -> None:
        from backtest.engine import _position_size_units

        # A+ -> size_mult=1.5
        units, risk_dollar = _position_size_units(
            equity=10_000.0, risk_per_trade=0.02, size_multiplier=1.5,
            entry=100.0, stop=90.0,
        )
        assert units == pytest.approx(30.0)
        assert risk_dollar == pytest.approx(300.0)

    def test_position_size_zero_diff_returns_zero(self) -> None:
        from backtest.engine import _position_size_units

        units, risk_dollar = _position_size_units(
            equity=10_000.0, risk_per_trade=0.02, size_multiplier=1.0,
            entry=100.0, stop=100.0,
        )
        assert units == 0.0
        assert risk_dollar == 0.0


class TestSimulateTrade:
    def _make_scored(
        self,
        n: int = 20,
        *,
        signal_idx: int = 5,
        direction: str = "long",
        grade: str = "A+",
        sl: float = 95.0,
        tp1: float = 110.0,
        tp2: float = 120.0,
        entry_open: float = 100.0,
        size_mult: float = 1.5,
        future_highs: list[float] | None = None,
        future_lows: list[float] | None = None,
        future_closes: list[float] | None = None,
    ) -> pd.DataFrame:
        """
        Bikin DataFrame 'scored' sintetis dengan 1 sinyal di signal_idx.
        Kolom lengkap hasil score_confluence, tapi nilai-nilainya di-stub.

        future_highs/lows/closes: list of bar values setelah signal_idx+1
            (yaitu bar entry dan seterusnya). Panjang = n - (signal_idx + 1).
        """
        rng = np.random.default_rng(7)
        rows = []
        # Bars sebelum signal: netral
        for i in range(signal_idx):
            rows.append(
                {
                    "timestamp": 1_700_000_000_000 + i * 3_600_000,
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.0,
                    "volume": 500.0,
                    "luminance_signal": 0,
                    "rsi_regime_signal": 0,
                    "structure_signal": 0,
                    "wavetrend_signal": 0,
                    "regime": "ranging",
                    "confluence_direction": "long",
                    "confluence_score": 1,
                    "confluence_grade": "skip",
                    "size_multiplier": 0.0,
                    "entry_price": 100.0,
                    "stop_loss": 95.0,
                    "take_profit_1": 110.0,
                    "take_profit_2": 120.0,
                    "risk_reward": 2.0,
                }
            )
        # Bar signal: setup the trade
        rows.append(
            {
                "timestamp": 1_700_000_000_000 + signal_idx * 3_600_000,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "volume": 500.0,
                "luminance_signal": 1,
                "rsi_regime_signal": 1,
                "structure_signal": 1,
                "wavetrend_signal": 1,
                "regime": "trending",
                "confluence_direction": direction,
                "confluence_score": 4,
                "confluence_grade": grade,
                "size_multiplier": size_mult,
                "entry_price": entry_open,
                "stop_loss": sl,
                "take_profit_1": tp1,
                "take_profit_2": tp2,
                "risk_reward": 2.0,
            }
        )
        # Bar entry (signal_idx + 1) dan seterusnya
        post_count = n - (signal_idx + 1)
        if future_highs is None:
            future_highs = [101.0] * post_count
        if future_lows is None:
            future_lows = [99.0] * post_count
        if future_closes is None:
            future_closes = [100.0] * post_count
        assert len(future_highs) >= post_count
        assert len(future_lows) >= post_count
        assert len(future_closes) >= post_count
        for j in range(post_count):
            rows.append(
                {
                    "timestamp": 1_700_000_000_000
                    + (signal_idx + 1 + j) * 3_600_000,
                    "open": entry_open if j == 0 else 100.0,
                    "high": future_highs[j],
                    "low": future_lows[j],
                    "close": future_closes[j],
                    "volume": 500.0,
                    "luminance_signal": 0,
                    "rsi_regime_signal": 0,
                    "structure_signal": 0,
                    "wavetrend_signal": 0,
                    "regime": "ranging",
                    "confluence_direction": None,
                    "confluence_score": 0,
                    "confluence_grade": "skip",
                    "size_multiplier": 0.0,
                    "entry_price": 100.0,
                    "stop_loss": 95.0,
                    "take_profit_1": 110.0,
                    "take_profit_2": 120.0,
                    "risk_reward": 2.0,
                }
            )
        return pd.DataFrame(rows)

    def test_tp1_hit_long(self) -> None:
        """Bar setelah entry: high >= tp1 -> exit di TP1."""
        # entry bar open=100, bar 1 high=120 (>>tp1=110) -> hit_tp
        scored = self._make_scored(
            n=20, signal_idx=5, direction="long",
            entry_open=100.0, sl=95.0, tp1=110.0, tp2=120.0,
            future_highs=[120.0, 105.0, 105.0, 105.0, 105.0, 105.0, 105.0, 105.0, 105.0, 105.0, 105.0, 105.0, 105.0, 105.0],
            future_lows=[100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0],
            future_closes=[110.0, 105.0, 105.0, 105.0, 105.0, 105.0, 105.0, 105.0, 105.0, 105.0, 105.0, 105.0, 105.0, 105.0],
        )
        trade = simulate_trade(scored, signal_idx=5, max_bars_hold=20)
        assert trade is not None
        assert trade.exit_reason == "tp1"
        assert trade.exit_price == pytest.approx(110.0)
        assert trade.pnl > 0
        # R-multiple: pnl / risk_dollar. risk = 10000*0.02*1.5 = 300. units = 300/5 = 60. pnl = (110-100)*60 = 600. r = 2.0
        assert trade.r_multiple == pytest.approx(2.0, rel=1e-6)

    def test_sl_hit_long(self) -> None:
        """Bar setelah entry: low <= sl -> exit di SL."""
        scored = self._make_scored(
            n=20, signal_idx=5, direction="long",
            entry_open=100.0, sl=95.0, tp1=110.0, tp2=120.0,
            future_highs=[100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0],
            future_lows=[80.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0],
            future_closes=[80.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0],
        )
        trade = simulate_trade(scored, signal_idx=5, max_bars_hold=20)
        assert trade is not None
        assert trade.exit_reason == "sl"
        assert trade.exit_price == pytest.approx(95.0)
        assert trade.pnl < 0
        # pnl = (95-100)*60 = -300. r = -300/300 = -1.0
        assert trade.r_multiple == pytest.approx(-1.0, rel=1e-6)

    def test_sl_before_tp_same_bar(self) -> None:
        """Dalam 1 bar, low <= sl DAN high >= tp1 -> SL dulu (pessimistic)."""
        scored = self._make_scored(
            n=20, signal_idx=5, direction="long",
            entry_open=100.0, sl=95.0, tp1=110.0, tp2=120.0,
            future_highs=[120.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0],
            future_lows=[80.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0],
            future_closes=[100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0, 100.0],
        )
        trade = simulate_trade(scored, signal_idx=5, max_bars_hold=20)
        assert trade is not None
        # Pessimistic: SL dulu meskipun high juga kena TP
        assert trade.exit_reason == "sl"
        assert trade.exit_price == pytest.approx(95.0)

    def test_short_tp_hit(self) -> None:
        """Short: low <= tp1 (tanpa hit SL) -> exit di TP1."""
        scored = self._make_scored(
            n=20, signal_idx=5, direction="short",
            entry_open=100.0, sl=105.0, tp1=90.0, tp2=80.0,
            # Bar entry: high=95 (di bawah sl=105, jadi TIDAK hit SL),
            #            low=80 (di bawah tp1=90 -> hit TP1 untuk short).
            # Bar setelah entry: kembali ke netral (tidak ada TP/SL kedua).
            future_highs=[95.0, 95.0, 95.0, 95.0, 95.0, 95.0, 95.0, 95.0, 95.0, 95.0, 95.0, 95.0, 95.0, 95.0],
            future_lows=[80.0, 90.0, 90.0, 90.0, 90.0, 90.0, 90.0, 90.0, 90.0, 90.0, 90.0, 90.0, 90.0, 90.0],
            future_closes=[85.0, 90.0, 90.0, 90.0, 90.0, 90.0, 90.0, 90.0, 90.0, 90.0, 90.0, 90.0, 90.0, 90.0],
        )
        trade = simulate_trade(scored, signal_idx=5, max_bars_hold=20)
        assert trade is not None
        assert trade.exit_reason == "tp1"
        assert trade.exit_price == pytest.approx(90.0)
        # Short profit: (100-90)*units > 0
        assert trade.pnl > 0

    def test_time_stop(self) -> None:
        """max_bars_hold=3, no SL/TP hit -> exit at close after 3 bars."""
        scored = self._make_scored(
            n=20, signal_idx=5, direction="long",
            entry_open=100.0, sl=95.0, tp1=110.0, tp2=120.0,
            future_highs=[106.0] * 14,
            future_lows=[100.0] * 14,
            future_closes=[105.0] * 14,
        )
        trade = simulate_trade(scored, signal_idx=5, max_bars_hold=3)
        assert trade is not None
        assert trade.exit_reason == "time_stop"
        assert trade.bars_held == 3

    def test_no_lookahead_entry_at_next_open(self) -> None:
        """Entry price harus == open of bar signal+1, BUKAN close of signal."""
        scored = self._make_scored(
            n=20, signal_idx=5, direction="long",
            entry_open=100.0, sl=95.0, tp1=110.0, tp2=120.0,
            future_highs=[120.0] * 14,
            future_lows=[100.0] * 14,
            future_closes=[110.0] * 14,
        )
        trade = simulate_trade(scored, signal_idx=5, max_bars_hold=5)
        assert trade is not None
        expected_entry = float(scored.iloc[6]["open"])  # signal+1
        assert trade.entry_price == pytest.approx(expected_entry)

    def test_invalid_signal_no_direction_returns_none(self) -> None:
        """Signal dengan direction None -> None."""
        scored = self._make_scored(n=20, signal_idx=5)
        scored.loc[5, "confluence_direction"] = None
        trade = simulate_trade(scored, signal_idx=5, max_bars_hold=5)
        assert trade is None

    def test_invalid_signal_skip_grade_returns_none(self) -> None:
        scored = self._make_scored(n=20, signal_idx=5, grade="A+")
        scored.loc[5, "confluence_grade"] = "skip"
        trade = simulate_trade(scored, signal_idx=5, max_bars_hold=5)
        assert trade is None

    def test_invalid_signal_no_sl_returns_none(self) -> None:
        scored = self._make_scored(n=20, signal_idx=5)
        scored.loc[5, "stop_loss"] = float("nan")
        trade = simulate_trade(scored, signal_idx=5, max_bars_hold=5)
        assert trade is None

    def test_position_sizing_math(self) -> None:
        """Risk dollar = equity * risk_per_trade * size_multiplier."""
        scored = self._make_scored(
            n=20, signal_idx=5, direction="long", size_mult=1.5,
            entry_open=100.0, sl=95.0, tp1=110.0,
        )
        trade = simulate_trade(
            scored, signal_idx=5, max_bars_hold=20,
            initial_capital=10_000.0, risk_per_trade=0.02,
        )
        assert trade is not None
        # risk_dollar = 10_000 * 0.02 * 1.5 = 300
        assert trade.risk_per_trade_dollar == pytest.approx(300.0, rel=1e-6)
        # units = 300 / 5 = 60
        assert trade.size_units == pytest.approx(60.0, rel=1e-6)


def _find_first_valid_signal(scored: pd.DataFrame, direction: str | None = None) -> int | None:
    """
    Cari bar pertama dengan grade valid (A+/valid) dan risk levels tersedia.
    direction filter opsional.
    """
    n = len(scored)
    for i in range(60, n - 1):
        row = scored.iloc[i]
        grade = row.get("confluence_grade")
        if grade not in ("A+", "valid"):
            continue
        if direction is not None and row.get("confluence_direction") != direction:
            continue
        sl = row.get("stop_loss")
        tp1 = row.get("take_profit_1")
        if pd.isna(sl) or pd.isna(tp1):
            continue
        return i
    return None


class TestRunBacktest:
    def test_run_backtest_on_synthetic_data(self, ohlcv_500: pd.DataFrame) -> None:
        result = run_backtest(
            df=ohlcv_500,
            symbol="BTC/USDT",
            timeframe="1h",
            initial_capital=10_000.0,
            risk_per_trade=0.02,
            max_bars_hold=20,
        )
        assert isinstance(result, BacktestResult)
        assert result.bars_processed == len(ohlcv_500)
        # trades adalah list (mungkin kosong di data sintetis)
        assert isinstance(result.trades, list)
        for t in result.trades:
            assert isinstance(t, Trade)
            assert t.pnl != 0  # trade ada = pnl terhitung

    def test_run_backtest_empty_data(self) -> None:
        empty = pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
        result = run_backtest(df=empty, symbol="X", timeframe="1h")
        assert result.bars_processed == 0
        assert result.trades == []

    def test_run_backtest_too_few_bars(self) -> None:
        tiny = make_ohlcv(n=50, seed=99)
        result = run_backtest(df=tiny, symbol="X", timeframe="1h")
        # < skip_warmup + 10 -> early return, 0 trades
        assert result.trades == []
        assert result.bars_processed == 50

    def test_run_backtest_result_to_dict(
        self, ohlcv_500: pd.DataFrame
    ) -> None:
        result = run_backtest(df=ohlcv_500, symbol="BTC/USDT", timeframe="1h")
        d = result.to_dict()
        assert d["symbol"] == "BTC/USDT"
        assert d["timeframe"] == "1h"
        assert d["initial_capital"] == BACKTEST_INITIAL_CAPITAL
        assert "trades" in d
        assert isinstance(d["trades"], list)


# =========================================================================
# Tests: metrics
# =========================================================================
class TestEmptyMetrics:
    def test_empty_metrics_has_all_keys(self) -> None:
        m = empty_metrics()
        for k in (
            "total_trades", "wins", "losses", "win_rate", "profit_factor",
            "max_drawdown_pct", "sharpe_ratio", "avg_r_multiple",
            "expectancy", "total_pnl", "equity_curve", "equity_final",
        ):
            assert k in m
        assert m["total_trades"] == 0
        assert m["wins"] == 0
        assert m["losses"] == 0
        assert m["equity_curve"] == []


class TestCalculateMetricsBasics:
    def test_two_wins_one_loss(self) -> None:
        trades = [
            {"pnl": 100.0, "r_multiple": 0.5},
            {"pnl": 200.0, "r_multiple": 1.0},
            {"pnl": -50.0, "r_multiple": -0.25},
        ]
        m = calculate_metrics(trades, initial_capital=10_000.0, risk_per_trade=0.02)
        assert m["total_trades"] == 3
        assert m["wins"] == 2
        assert m["losses"] == 1
        assert m["win_rate"] == pytest.approx(2 / 3, rel=1e-6)
        # gross_profit=300, gross_loss=-50, PF = 300/50 = 6.0
        assert m["profit_factor"] == pytest.approx(6.0, rel=1e-6)
        assert m["total_pnl"] == pytest.approx(250.0, rel=1e-6)
        # avg_r = (0.5 + 1.0 - 0.25) / 3 = 0.41666...
        assert m["avg_r_multiple"] == pytest.approx((0.5 + 1.0 - 0.25) / 3, rel=1e-6)

    def test_all_wins_profit_factor_capped(self) -> None:
        trades = [{"pnl": 50.0, "r_multiple": 0.25} for _ in range(5)]
        m = calculate_metrics(trades)
        assert m["profit_factor"] == PROFIT_FACTOR_CAP
        assert m["losses"] == 0
        assert m["win_rate"] == 1.0

    def test_all_losses_profit_factor_zero(self) -> None:
        trades = [{"pnl": -50.0, "r_multiple": -0.25} for _ in range(4)]
        m = calculate_metrics(trades)
        assert m["profit_factor"] == 0.0
        assert m["wins"] == 0
        assert m["win_rate"] == 0.0
        assert m["expectancy"] < 0

    def test_single_trade_sharpe_zero(self) -> None:
        # n < 2 -> sharpe 0
        trades = [{"pnl": 100.0, "r_multiple": 0.5}]
        m = calculate_metrics(trades)
        assert m["sharpe_ratio"] == 0.0
        assert m["total_trades"] == 1

    def test_expectancy_formula(self) -> None:
        # 60% WR, avg win 200, avg loss 100
        # Expectancy = 0.6*200 - 0.4*100 = 80
        trades = [{"pnl": 200.0, "r_multiple": 1.0}] * 6 + [{"pnl": -100.0, "r_multiple": -0.5}] * 4
        m = calculate_metrics(trades)
        assert m["expectancy"] == pytest.approx(80.0, rel=1e-6)

    def test_max_drawdown_calculation(self) -> None:
        # Equity: 10k -> 9k -> 8k -> 11k -> 10.5k
        # Running peak after each: 10k, 10k, 10k, 11k, 11k
        # Drawdowns: 0, -10%, -20%, 0, -4.5%
        # Max DD = 20%
        pnls = [-1000.0, -1000.0, 3000.0, -500.0]
        trades = [{"pnl": p, "r_multiple": p / 200.0} for p in pnls]
        m = calculate_metrics(trades, initial_capital=10_000.0)
        assert m["max_drawdown_pct"] == pytest.approx(20.0, rel=1e-6)

    def test_equity_curve_shape(self) -> None:
        trades = [{"pnl": 50.0, "r_multiple": 0.25}] * 4
        m = calculate_metrics(trades, initial_capital=10_000.0)
        assert len(m["equity_curve"]) == 4
        assert m["equity_curve"] == [50.0, 100.0, 150.0, 200.0]
        assert m["equity_final"] == pytest.approx(10_200.0, rel=1e-6)

    def test_zero_trades_returns_empty_metrics(self) -> None:
        m = calculate_metrics([])
        assert m["total_trades"] == 0
        assert m["equity_curve"] == []
        assert m["expectancy"] == 0.0

    def test_r_multiple_fallback(self) -> None:
        """Trade tanpa r_multiple -> hitung dari pnl / risk_dollar."""
        # pnl=200, risk_per_trade=0.02, capital=10k -> risk_dollar=200 -> r=1.0
        trades = [{"pnl": 200.0}]  # no r_multiple key
        m = calculate_metrics(trades, initial_capital=10_000.0, risk_per_trade=0.02)
        assert m["avg_r_multiple"] == pytest.approx(1.0, rel=1e-6)


class TestTargetCheck:
    def test_all_pass(self) -> None:
        m = {
            "win_rate": 0.7,
            "profit_factor": 2.0,
            "max_drawdown_pct": 10.0,
            "sharpe_ratio": 2.0,
            "avg_r_multiple": 2.0,
            "expectancy": 50.0,
        }
        t = target_check(m)
        assert all(t.values())

    def test_all_fail(self) -> None:
        m = {
            "win_rate": 0.3,
            "profit_factor": 0.5,
            "max_drawdown_pct": 50.0,
            "sharpe_ratio": 0.5,
            "avg_r_multiple": 0.5,
            "expectancy": -10.0,
        }
        t = target_check(m)
        assert not any(t.values())

    def test_target_values_match_config(self) -> None:
        # Sanity: target_check thresholds harus match config constants.
        m = {
            "win_rate": TARGET_WIN_RATE + 0.01,
            "profit_factor": TARGET_PROFIT_FACTOR + 0.1,
            "max_drawdown_pct": TARGET_MAX_DRAWDOWN * 100 - 0.5,
            "sharpe_ratio": TARGET_SHARPE + 0.1,
            "avg_r_multiple": TARGET_AVG_R_MULTIPLE + 0.1,
            "expectancy": 0.01,
        }
        t = target_check(m)
        assert all(t.values()), f"Expected all pass with margin: {t}"


# =========================================================================
# Tests: report
# =========================================================================
class TestFormatReport:
    def test_format_report_contains_key_fields(self) -> None:
        metrics = calculate_metrics(
            [{"pnl": 50.0, "r_multiple": 0.25} for _ in range(5)]
        )
        trades = [{"pnl": 50.0, "r_multiple": 0.25, "entry_time": 1_700_000_000_000,
                   "exit_time": 1_700_003_600_000, "direction": "long",
                   "entry_price": 100.0, "exit_price": 105.0, "exit_reason": "tp1"} for _ in range(5)]
        text = format_report(
            symbol="BTC/USDT",
            timeframe="1h",
            metrics=metrics,
            trades=trades,
            period=(1_700_000_000_000, 1_700_003_600_000),
            initial_capital=10_000.0,
            risk_per_trade=0.02,
        )
        assert "RX-0 Unicorn" in text
        assert "BTC/USDT" in text
        assert "Win Rate" in text
        assert "Profit Factor" in text
        assert "Max Drawdown" in text
        assert "Sharpe Ratio" in text
        assert "Avg R-Multiple" in text
        assert "Expectancy" in text
        assert "VERDICT" in text

    def test_format_report_empty_trades(self) -> None:
        m = empty_metrics()
        text = format_report("X/USDT", "1h", m, [], period=(0, 0))
        assert "Total trades   : 0" in text
        # max_drawdown=0% passes <20% target even with 0 trades, so verdict
        # is at least 1/6 (the others all fail with 0 trades).
        assert "VERDICT:" in text
        assert "1/6" in text

    def test_format_report_top5_wins_and_worst5(self) -> None:
        trades = [
            {"pnl": float(i), "r_multiple": i / 100.0, "entry_time": 1_700_000_000_000 + i * 1_000_000,
             "exit_time": 1_700_000_000_000 + (i + 1) * 1_000_000, "direction": "long",
             "entry_price": 100.0, "exit_price": 100.0 + i, "exit_reason": "tp1"}
            for i in range(-5, 15)  # -5 to 14
        ]
        m = calculate_metrics(trades)
        text = format_report("X/USDT", "1h", m, trades, period=(0, 0))
        assert "TOP 5 WINS" in text
        assert "WORST 5 LOSSES" in text


class TestToJson:
    def test_to_json_writes_file(self, tmp_path: Path) -> None:
        m = calculate_metrics(
            [{"pnl": 100.0, "r_multiple": 0.5}, {"pnl": -50.0, "r_multiple": -0.25}]
        )
        out = tmp_path / "bt.json"
        result_path = to_json(m, out, metadata={"symbol": "BTC/USDT"}, trades=[])
        assert result_path.exists()
        loaded = json.loads(out.read_text())
        assert loaded["metadata"]["symbol"] == "BTC/USDT"
        assert "metrics" in loaded
        assert loaded["metrics"]["total_trades"] == 2

    def test_to_json_includes_trades(self, tmp_path: Path) -> None:
        trades = [{"pnl": 100.0, "r_multiple": 0.5}]
        m = calculate_metrics(trades)
        out = tmp_path / "bt2.json"
        to_json(m, out, trades=trades)
        loaded = json.loads(out.read_text())
        assert loaded["trades"] == trades

    def test_to_json_empty_metrics(self, tmp_path: Path) -> None:
        out = tmp_path / "bt3.json"
        to_json(empty_metrics(), out)
        loaded = json.loads(out.read_text())
        assert loaded["metrics"]["total_trades"] == 0


class TestEquityChart:
    def test_chart_creates_png(self, tmp_path: Path) -> None:
        m = calculate_metrics(
            [{"pnl": 50.0, "r_multiple": 0.25} for _ in range(10)]
        )
        out = tmp_path / "eq.png"
        result = to_equity_curve_chart(m, out, initial_capital=10_000.0)
        assert result.exists()
        assert result.stat().st_size > 1000  # PNG non-empty

    def test_chart_with_no_trades(self, tmp_path: Path) -> None:
        m = empty_metrics()
        out = tmp_path / "eq_empty.png"
        result = to_equity_curve_chart(m, out)
        assert result.exists()
