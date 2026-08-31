"""
Unit tests untuk Phase 6 — Paper Trading System.

Covers:
    - PaperJournal:    schema init, log_open, log_close, get_state, daily agg,
                       wipe_all, count, get_stats
    - PaperPortfolio:  initial balance, open_position, close_position w/ P/L,
                       equity calc, drawdown, daily loss limit, circuit
                       breaker, position sizing math
    - PaperTrader:     open_from_signal (A+ / valid / skip), close_trade,
                       check_one_position (SL / TP1 / TP2 / time-stop),
                       position sizing integration
    - Reporter:        generate_report text, phase7_readiness,
                       generate_equity_chart (PNG created), build_weekly_summary
    - Notifier:        5-tier message format, graceful degradation when no
                       Telegram token

Total: ~30 tests. All isolated (tmp_path for SQLite, MagicMock for Telegram).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from paper import (  # noqa: E402
    ALLOWED_DIRECTIONS,
    ALLOWED_GRADES,
    PaperJournal,
    PaperNotifier,
    PaperPortfolio,
    PaperTrader,
    TIER_DAILY,
    TIER_ENTRY,
    TIER_EXIT,
    TIER_RISK,
    TIER_WEEKLY,
    build_weekly_summary,
    generate_equity_chart,
    generate_report,
    make_trade_id,
    phase7_readiness,
)
from paper.notifier import PaperNotifier as _PaperNotifier  # noqa: E402


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def journal(tmp_path: Path) -> PaperJournal:
    """Fresh isolated SQLite journal in tmp_path."""
    j = PaperJournal(db_path=tmp_path / "paper.db")
    j.__enter__()
    yield j
    j.__exit__(None, None, None)


@pytest.fixture
def portfolio(journal: PaperJournal) -> PaperPortfolio:
    """Portfolio with $10k initial balance, no notifier."""
    p = PaperPortfolio(journal=journal, notifier=None)
    p.start()
    return p


@pytest.fixture
def trader(journal: PaperJournal) -> PaperTrader:
    """Trader wired to journal, no notifier."""
    return PaperTrader(journal=journal, notifier=None)


def _sample_long_trade(
    *,
    trade_id: str = "TEST-L-001",
    entry: float = 100.0,
    sl: float = 95.0,
    tp1: float = 105.0,
    tp2: float = 110.0,
    risk: float = 100.0,
) -> dict:
    return {
        "trade_id": trade_id,
        "symbol": "BTC/USDT",
        "direction": "long",
        "entry_time": int(time.time()),
        "entry_price": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "confluence_score": 3,
        "grade": "valid",
        "size_multiplier": 1.0,
        "position_size_units": risk / abs(entry - sl),  # = 20.0
        "risk_usd": risk,
        "signal_source": "scanner",
        "status": "open",
    }


def _open_long(portfolio: PaperPortfolio, journal: PaperJournal, **overrides):
    """Helper: open a long BTC/USDT position, return trade dict."""
    params = dict(
        trade_id="LONG-TEST-001",
        symbol="BTC/USDT",
        direction="long",
        entry_price=100.0,
        sl=95.0,
        tp1=105.0,
        tp2=110.0,
        confluence_score=3,
        grade="valid",
        size_multiplier=1.0,
        risk_per_trade=0.02,
        signal_source="scanner",
    )
    params.update(overrides)
    return portfolio.open_position(**params)


# =============================================================================
# PaperJournal tests
# =============================================================================


class TestPaperJournal:
    """SQLite persistence layer."""

    def test_schema_tables_created(self, journal: PaperJournal) -> None:
        rows = journal.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'paper_%'"
        ).fetchall()
        names = {r["name"] for r in rows}
        assert "paper_trades" in names
        assert "paper_daily" in names
        assert "paper_state" in names

    def test_log_open_position_returns_rowid(self, journal: PaperJournal) -> None:
        rid = journal.log_open_position(
            trade_id="T1",
            symbol="ETH/USDT",
            direction="long",
            entry_time=int(time.time()),
            entry_price=2000.0,
            sl=1950.0,
            tp1=2050.0,
            tp2=2100.0,
            confluence_score=3,
            grade="valid",
            size_multiplier=1.0,
            position_size_units=2.0,
            risk_usd=100.0,
        )
        assert rid >= 1
        assert journal.count_open_positions() == 1

    def test_log_open_rejects_bad_direction(self, journal: PaperJournal) -> None:
        with pytest.raises(ValueError, match="direction"):
            journal.log_open_position(
                trade_id="BAD1",
                symbol="X",
                direction="sideways",
                entry_time=int(time.time()),
                entry_price=1.0,
                sl=0.9,
                tp1=1.1,
                tp2=1.2,
                confluence_score=2,
                grade="valid",
                size_multiplier=1.0,
                position_size_units=1.0,
                risk_usd=1.0,
            )

    def test_log_open_rejects_bad_score(self, journal: PaperJournal) -> None:
        with pytest.raises(ValueError, match="confluence_score"):
            journal.log_open_position(
                trade_id="BAD2",
                symbol="X",
                direction="long",
                entry_time=int(time.time()),
                entry_price=1.0,
                sl=0.9,
                tp1=1.1,
                tp2=1.2,
                confluence_score=7,  # > 4
                grade="valid",
                size_multiplier=1.0,
                position_size_units=1.0,
                risk_usd=1.0,
            )

    def test_log_close_updates_status_and_pnl(
        self, journal: PaperJournal
    ) -> None:
        journal.log_open_position(
            trade_id="T-CLOSE",
            symbol="BTC/USDT",
            direction="long",
            entry_time=int(time.time()),
            entry_price=100.0,
            sl=95.0,
            tp1=105.0,
            tp2=110.0,
            confluence_score=3,
            grade="valid",
            size_multiplier=1.0,
            position_size_units=20.0,
            risk_usd=100.0,
        )
        journal.log_close_position(
            trade_id="T-CLOSE",
            exit_time=int(time.time()),
            exit_price=110.0,
            exit_reason="tp2",
            pnl_usd=200.0,
            pnl_r_multiple=2.0,
        )
        trade = journal.get_trade_by_id("T-CLOSE")
        assert trade is not None
        assert trade["status"] == "closed"
        assert trade["exit_reason"] == "tp2"
        assert float(trade["pnl_usd"]) == 200.0

    def test_log_close_rejects_already_closed(
        self, journal: PaperJournal
    ) -> None:
        journal.log_open_position(
            trade_id="T-DUP",
            symbol="BTC/USDT",
            direction="long",
            entry_time=int(time.time()),
            entry_price=100.0,
            sl=95.0,
            tp1=105.0,
            tp2=110.0,
            confluence_score=2,
            grade="valid",
            size_multiplier=1.0,
            position_size_units=20.0,
            risk_usd=100.0,
        )
        journal.log_close_position(
            trade_id="T-DUP",
            exit_time=int(time.time()),
            exit_price=110.0,
            exit_reason="tp2",
            pnl_usd=200.0,
            pnl_r_multiple=2.0,
        )
        with pytest.raises(ValueError, match="closed"):
            journal.log_close_position(
                trade_id="T-DUP",
                exit_time=int(time.time()),
                exit_price=120.0,
                exit_reason="tp2",
                pnl_usd=400.0,
                pnl_r_multiple=4.0,
            )

    def test_get_closed_trades_orders_recent_first(
        self, journal: PaperJournal
    ) -> None:
        now = int(time.time())
        # Open + close 2 trades
        for tid, t_exit, pnl in [
            ("OLD", now - 200, 50.0),
            ("NEW", now - 50, 80.0),
        ]:
            journal.log_open_position(
                trade_id=tid,
                symbol="X/USDT",
                direction="long",
                entry_time=now - 1000,
                entry_price=100.0,
                sl=95.0,
                tp1=105.0,
                tp2=110.0,
                confluence_score=3,
                grade="valid",
                size_multiplier=1.0,
                position_size_units=20.0,
                risk_usd=100.0,
            )
            journal.log_close_position(
                trade_id=tid,
                exit_time=t_exit,
                exit_price=110.0,
                exit_reason="tp2",
                pnl_usd=pnl,
                pnl_r_multiple=2.0,
            )
        closed = journal.get_closed_trades()
        assert closed[0]["trade_id"] == "NEW"
        assert closed[1]["trade_id"] == "OLD"

    def test_daily_aggregation_creates_row(
        self, journal: PaperJournal
    ) -> None:
        journal.log_open_position(
            trade_id="AGG-1",
            symbol="X/USDT",
            direction="long",
            entry_time=int(time.time()),
            entry_price=100.0,
            sl=95.0,
            tp1=105.0,
            tp2=110.0,
            confluence_score=3,
            grade="valid",
            size_multiplier=1.0,
            position_size_units=20.0,
            risk_usd=100.0,
        )
        journal.log_close_position(
            trade_id="AGG-1",
            exit_time=int(time.time()),
            exit_price=110.0,
            exit_reason="tp2",
            pnl_usd=200.0,
            pnl_r_multiple=2.0,
        )
        history = journal.get_daily_history()
        assert len(history) == 1
        row = history[0]
        assert int(row["trades_count"]) == 1
        assert int(row["wins"]) == 1
        assert int(row["losses"]) == 0
        assert float(row["daily_pnl"]) == 200.0
        assert abs(float(row["win_rate"]) - 1.0) < 1e-9

    def test_set_and_get_state_roundtrip(self, journal: PaperJournal) -> None:
        journal.set_state("balance", 12345.67)
        assert abs(float(journal.get_state("balance")) - 12345.67) < 1e-9
        # Default when missing
        assert journal.get_state("nonexistent", "DEFAULT") == "DEFAULT"
        # Dict roundtrip
        journal.set_state("nested", {"a": 1, "b": [1, 2, 3]})
        got = journal.get_state("nested")
        assert got == {"a": 1, "b": [1, 2, 3]}

    def test_wipe_all_clears_everything(self, journal: PaperJournal) -> None:
        journal.set_state("balance", 1000.0)
        journal.log_open_position(
            trade_id="WIPE",
            symbol="X",
            direction="long",
            entry_time=int(time.time()),
            entry_price=10.0,
            sl=9.0,
            tp1=11.0,
            tp2=12.0,
            confluence_score=3,
            grade="valid",
            size_multiplier=1.0,
            position_size_units=10.0,
            risk_usd=10.0,
        )
        journal.wipe_all()
        assert journal.get_state("balance") is None
        assert journal.count_open_positions() == 0
        assert journal.get_closed_trades() == []


# =============================================================================
# PaperPortfolio tests
# =============================================================================


class TestPaperPortfolio:
    """Virtual balance + open position manager."""

    def test_initial_balance_is_10k(self, portfolio: PaperPortfolio) -> None:
        assert abs(portfolio.get_balance() - 10_000.0) < 1e-9
        assert abs(portfolio.get_initial_balance() - 10_000.0) < 1e-9
        assert abs(portfolio.get_peak_equity() - 10_000.0) < 1e-9

    def test_start_is_idempotent(self, journal: PaperJournal) -> None:
        p = PaperPortfolio(journal=journal, notifier=None)
        p.start(initial_balance=10_000.0)
        p.start(initial_balance=99_999.0)  # should NOT reset
        assert abs(p.get_balance() - 10_000.0) < 1e-9

    def test_start_with_reset(self, journal: PaperJournal) -> None:
        p = PaperPortfolio(journal=journal, notifier=None)
        p.start(initial_balance=10_000.0)
        p.start(initial_balance=5000.0, reset=True)
        assert abs(p.get_balance() - 5000.0) < 1e-9

    def test_compute_position_size_math(self) -> None:
        # equity=10000, risk=0.02, mult=1.0 -> risk_usd=200
        # sl_dist=5 -> size=40
        size, risk = PaperPortfolio.compute_position_size(
            equity=10_000.0,
            risk_per_trade=0.02,
            entry_price=100.0,
            stop_loss=95.0,
            size_multiplier=1.0,
        )
        assert abs(size - 40.0) < 1e-9
        assert abs(risk - 200.0) < 1e-9

    def test_compute_position_size_with_multiplier(self) -> None:
        # A+ trade: 1.5x -> risk_usd=300, size=60
        size, risk = PaperPortfolio.compute_position_size(
            equity=10_000.0,
            risk_per_trade=0.02,
            entry_price=100.0,
            stop_loss=95.0,
            size_multiplier=1.5,
        )
        assert abs(size - 60.0) < 1e-9
        assert abs(risk - 300.0) < 1e-9

    def test_compute_position_size_validates_inputs(self) -> None:
        with pytest.raises(ValueError):
            PaperPortfolio.compute_position_size(
                equity=0,
                risk_per_trade=0.02,
                entry_price=100.0,
                stop_loss=95.0,
            )
        with pytest.raises(ValueError):
            PaperPortfolio.compute_position_size(
                equity=10_000.0,
                risk_per_trade=1.5,  # > 1
                entry_price=100.0,
                stop_loss=95.0,
            )
        with pytest.raises(ValueError):
            PaperPortfolio.compute_position_size(
                equity=10_000.0,
                risk_per_trade=0.02,
                entry_price=100.0,
                stop_loss=100.0,  # == entry
            )

    def test_compute_pnl_long_and_short(self) -> None:
        long_pnl = PaperPortfolio.compute_pnl(
            direction="long", entry_price=100.0,
            exit_price=110.0, size_units=2.0,
        )
        assert abs(long_pnl - 20.0) < 1e-9
        short_pnl = PaperPortfolio.compute_pnl(
            direction="short", entry_price=100.0,
            exit_price=90.0, size_units=2.0,
        )
        assert abs(short_pnl - 20.0) < 1e-9

    def test_open_position_returns_trade(
        self, portfolio: PaperPortfolio, journal: PaperJournal
    ) -> None:
        trade = _open_long(portfolio, journal)
        assert trade is not None
        assert trade["trade_id"] == "LONG-TEST-001"
        assert trade["status"] == "open"
        # equity unchanged (mark-price == entry-price fallback)
        assert abs(portfolio.get_equity() - portfolio.get_balance()) < 1e-9

    def test_close_position_adds_pnl_to_balance(
        self, portfolio: PaperPortfolio, journal: PaperJournal
    ) -> None:
        _open_long(portfolio, journal)
        # entry=100, exit=110, size=40 -> pnl=+400
        closed = portfolio.close_position(
            trade_id="LONG-TEST-001",
            exit_price=110.0,
            exit_reason="tp2",
        )
        assert closed["status"] == "closed"
        assert abs(float(closed["pnl_usd"]) - 400.0) < 1e-9
        assert abs(portfolio.get_balance() - 10_400.0) < 1e-9

    def test_close_position_realizes_loss(
        self, portfolio: PaperPortfolio, journal: PaperJournal
    ) -> None:
        _open_long(portfolio, journal)
        # entry=100, exit=95 (==SL), size=40 -> pnl=-200
        closed = portfolio.close_position(
            trade_id="LONG-TEST-001",
            exit_price=95.0,
            exit_reason="sl",
        )
        assert abs(float(closed["pnl_usd"]) - (-200.0)) < 1e-9
        assert abs(portfolio.get_balance() - 9800.0) < 1e-9

    def test_equity_includes_unrealized_pnl(
        self, portfolio: PaperPortfolio, journal: PaperJournal
    ) -> None:
        _open_long(portfolio, journal, trade_id="UPL-001")
        # mark price 105 -> unrealized = (105-100)*40 = +200
        eq = portfolio.get_equity(mark_prices={"BTC/USDT": 105.0})
        assert abs(eq - 10_200.0) < 1e-9

    def test_get_drawdown_pct_increases_after_loss(
        self, portfolio: PaperPortfolio, journal: PaperJournal
    ) -> None:
        _open_long(portfolio, journal, trade_id="DD-001")
        # close at SL: balance=9800, peak was 10000 -> dd=2%
        portfolio.close_position(
            trade_id="DD-001", exit_price=95.0, exit_reason="sl"
        )
        dd = portfolio.get_drawdown_pct()
        assert abs(dd - 0.02) < 1e-9

    def test_drawdown_circuit_trips_and_activates(
        self, journal: PaperJournal
    ) -> None:
        p = PaperPortfolio(journal=journal, notifier=None)
        p.start(initial_balance=10_000.0)
        # Manually set peak_equity high, then force drawdown >= 15%
        journal.set_state("peak_equity", 12_000.0)
        # Equity at 10000 -> dd = (12000-10000)/12000 = 0.1667
        assert p.get_drawdown_pct() > 0.15
        assert not p.is_drawdown_circuit_active()
        p.trip_drawdown_circuit(pause_seconds=3600)
        assert p.is_drawdown_circuit_active()

    def test_circuit_blocks_new_positions(
        self, journal: PaperJournal
    ) -> None:
        p = PaperPortfolio(journal=journal, notifier=None)
        p.start(initial_balance=10_000.0)
        p.trip_drawdown_circuit(pause_seconds=3600)
        with pytest.raises(RuntimeError, match="drawdown_circuit"):
            p.open_position(
                trade_id="BLOCKED-1",
                symbol="BTC/USDT",
                direction="long",
                entry_price=100.0,
                sl=95.0,
                tp1=105.0,
                tp2=110.0,
                confluence_score=3,
                grade="valid",
                size_multiplier=1.0,
                risk_per_trade=0.02,
            )

    def test_daily_loss_limit_blocks_new_trades(
        self, journal: PaperJournal
    ) -> None:
        p = PaperPortfolio(journal=journal, notifier=None)
        p.start(initial_balance=10_000.0)
        # 2 losing trades with size_multiplier=1.5 -> each loss ~$300
        # Total ~-$590 > 5% of equity (~$500) trips the limit.
        for i in range(2):
            tid = f"LOSS-{i}"
            p.open_position(
                trade_id=tid,
                symbol="BTC/USDT",
                direction="long",
                entry_price=100.0,
                sl=95.0,
                tp1=105.0,
                tp2=110.0,
                confluence_score=3,
                grade="valid",
                size_multiplier=1.5,
                risk_per_trade=0.02,
            )
            p.close_position(
                trade_id=tid, exit_price=95.0, exit_reason="sl"
            )
        assert p.is_daily_loss_limit_hit() is True
        allowed, reason = p.can_open_new_position()
        assert allowed is False
        assert reason == "daily_loss_limit_hit"

    def test_can_open_returns_ok_when_safe(
        self, portfolio: PaperPortfolio
    ) -> None:
        allowed, reason = portfolio.can_open_new_position()
        assert allowed is True
        assert reason == "ok"

    def test_close_all_clears_open_positions(
        self, portfolio: PaperPortfolio, journal: PaperJournal
    ) -> None:
        for i in range(2):
            _open_long(
                portfolio, journal, trade_id=f"MULTI-{i}",
                entry_price=100.0 + i,
            )
        assert journal.count_open_positions() == 2
        # close_all default reason is "manual_close_all" but journal
        # only accepts {tp1, tp2, sl, time_stop, manual, end_of_data,
        # cancelled} — use "manual" which is valid.
        count = portfolio.close_all(reason="manual")
        assert count == 2
        assert journal.count_open_positions() == 0

    def test_get_state_returns_full_dict(
        self, portfolio: PaperPortfolio
    ) -> None:
        s = portfolio.get_state()
        for key in (
            "balance", "peak_equity", "initial_balance",
            "started_at", "circuit_until", "last_daily_loss",
        ):
            assert key in s


# =============================================================================
# PaperTrader tests
# =============================================================================


class TestPaperTrader:
    """High-level orchestrator."""

    def test_open_from_signal_valid_long(
        self, trader: PaperTrader, journal: PaperJournal
    ) -> None:
        signal = {
            "score": 3, "grade": "valid", "direction": "long",
            "stop_loss": 95.0, "take_profit_1": 105.0, "take_profit_2": 110.0,
            "entry_price": 100.0,
        }
        trade = trader.open_from_signal(signal, symbol="BTC/USDT")
        assert trade is not None
        assert trade["direction"] == "long"
        assert trade["symbol"] == "BTC/USDT"

    def test_open_from_signal_a_plus_uses_1_5x_multiplier(
        self, trader: PaperTrader
    ) -> None:
        signal = {
            "score": 4, "grade": "a_plus", "direction": "long",
            "stop_loss": 95.0, "take_profit_1": 105.0, "take_profit_2": 110.0,
            "entry_price": 100.0,
        }
        trade = trader.open_from_signal(signal, symbol="ETH/USDT")
        assert trade is not None
        assert float(trade["size_multiplier"]) >= 1.5

    def test_open_from_signal_skips_low_score(
        self, trader: PaperTrader
    ) -> None:
        signal = {
            "score": 1,  # below CONFLUENCE_MIN_VALID
            "grade": "valid", "direction": "long",
            "stop_loss": 95.0, "take_profit_1": 105.0, "take_profit_2": 110.0,
            "entry_price": 100.0,
        }
        assert trader.open_from_signal(signal, symbol="X/USDT") is None

    def test_open_from_signal_skips_skip_grade(
        self, trader: PaperTrader
    ) -> None:
        signal = {
            "score": 3, "grade": "skip", "direction": "long",
            "stop_loss": 95.0, "take_profit_1": 105.0, "take_profit_2": 110.0,
            "entry_price": 100.0,
        }
        assert trader.open_from_signal(signal, symbol="X/USDT") is None

    def test_open_from_signal_missing_sl_tp_returns_none(
        self, trader: PaperTrader
    ) -> None:
        signal = {
            "score": 3, "grade": "valid", "direction": "long",
            "stop_loss": None, "take_profit_1": None, "take_profit_2": None,
            "entry_price": 100.0,
        }
        assert trader.open_from_signal(signal, symbol="X/USDT") is None

    def test_check_one_position_sl_hit(
        self, trader: PaperTrader, journal: PaperJournal
    ) -> None:
        trade = trader.open_from_signal(
            {
                "score": 3, "grade": "valid", "direction": "long",
                "stop_loss": 95.0, "take_profit_1": 105.0,
                "take_profit_2": 110.0, "entry_price": 100.0,
            },
            symbol="BTC/USDT",
        )
        closed = trader.check_one_position(trade, current_price=94.0)
        assert closed is not None
        assert closed["exit_reason"] == "sl"
        assert float(closed["pnl_usd"]) < 0

    def test_check_one_position_tp1_hit(
        self, trader: PaperTrader, journal: PaperJournal
    ) -> None:
        trade = trader.open_from_signal(
            {
                "score": 3, "grade": "valid", "direction": "long",
                "stop_loss": 95.0, "take_profit_1": 105.0,
                "take_profit_2": 110.0, "entry_price": 100.0,
            },
            symbol="BTC/USDT",
        )
        closed = trader.check_one_position(trade, current_price=105.5)
        assert closed is not None
        assert closed["exit_reason"] == "tp1"

    def test_check_one_position_tp2_hit(
        self, trader: PaperTrader, journal: PaperJournal
    ) -> None:
        trade = trader.open_from_signal(
            {
                "score": 3, "grade": "valid", "direction": "long",
                "stop_loss": 95.0, "take_profit_1": 105.0,
                "take_profit_2": 110.0, "entry_price": 100.0,
            },
            symbol="BTC/USDT",
        )
        closed = trader.check_one_position(trade, current_price=111.0)
        assert closed is not None
        assert closed["exit_reason"] == "tp2"

    def test_check_one_position_time_stop(
        self, trader: PaperTrader, journal: PaperJournal
    ) -> None:
        # Use a trade_id so we can backdate entry_time to force time-stop.
        trade = trader.open_from_signal(
            {
                "score": 3, "grade": "valid", "direction": "long",
                "stop_loss": 95.0, "take_profit_1": 105.0,
                "take_profit_2": 110.0, "entry_price": 100.0,
            },
            symbol="BTC/USDT",
        )
        # Backdate entry_time to > 4h ago (PAPER_TIME_STOP_SECONDS=14400)
        journal.conn.execute(
            "UPDATE paper_trades SET entry_time = ? WHERE trade_id = ?",
            (int(time.time()) - 20_000, trade["trade_id"]),
        )
        # Refresh trade dict and price between SL and TP1
        refreshed = journal.get_trade_by_id(trade["trade_id"])
        closed = trader.check_one_position(refreshed, current_price=102.0)
        assert closed is not None
        assert closed["exit_reason"] == "time_stop"

    def test_check_one_position_no_action_when_safe(
        self, trader: PaperTrader, journal: PaperJournal
    ) -> None:
        trade = trader.open_from_signal(
            {
                "score": 3, "grade": "valid", "direction": "long",
                "stop_loss": 95.0, "take_profit_1": 105.0,
                "take_profit_2": 110.0, "entry_price": 100.0,
            },
            symbol="BTC/USDT",
        )
        # price between entry and TP1, no time-stop yet
        result = trader.check_one_position(trade, current_price=102.0)
        assert result is None

    def test_short_sl_and_tp_logic(
        self, trader: PaperTrader, journal: PaperJournal
    ) -> None:
        trade = trader.open_from_signal(
            {
                "score": 3, "grade": "valid", "direction": "short",
                "stop_loss": 105.0, "take_profit_1": 95.0,
                "take_profit_2": 90.0, "entry_price": 100.0,
            },
            symbol="BTC/USDT",
        )
        # Short SL hit when price >= 105
        closed = trader.check_one_position(trade, current_price=106.0)
        assert closed["exit_reason"] == "sl"

    def test_close_trade_calls_portfolio(
        self, trader: PaperTrader, portfolio: PaperPortfolio,
        journal: PaperJournal,
    ) -> None:
        _open_long(portfolio, journal, trade_id="CT-001")
        before = portfolio.get_balance()
        result = trader.close_trade("CT-001", 110.0, "tp2")
        assert result is not None
        assert result["status"] == "closed"
        assert portfolio.get_balance() > before

    def test_close_trade_unknown_returns_none(
        self, trader: PaperTrader
    ) -> None:
        assert trader.close_trade("DOES-NOT-EXIST", 100.0, "manual") is None

    def test_make_trade_id_format(self) -> None:
        tid = make_trade_id("BTC/USDT", "long")
        assert tid.startswith("BTCUSDT-L-")
        assert len(tid) > len("BTCUSDT-L-")
        tid_short = make_trade_id("ETH/USDT", "short")
        assert tid_short.startswith("ETHUSDT-S-")


# =============================================================================
# Reporter tests
# =============================================================================


class TestReporter:
    """Text + chart report generation."""

    def test_generate_report_contains_key_fields(
        self, journal: PaperJournal, portfolio: PaperPortfolio
    ) -> None:
        _open_long(portfolio, journal, trade_id="RPT-001")
        portfolio.close_position(
            trade_id="RPT-001", exit_price=110.0, exit_reason="tp2"
        )
        text = generate_report(journal, days_back=7)
        assert "RX-0 Unicorn" in text
        assert "Paper Trading Report" in text
        assert "PERFORMANCE SUMMARY" in text
        assert "Win rate" in text
        assert "Total P/L" in text
        assert "PHASE 7" in text.upper() or "PHASE 7" in text

    def test_generate_report_handles_empty_db(
        self, journal: PaperJournal
    ) -> None:
        text = generate_report(journal, days_back=7)
        assert "Total trades     : 0" in text
        assert "(none)" in text  # open positions
        assert "DAILY EQUITY (no data yet" in text

    def test_phase7_readiness_logic(self) -> None:
        # Not enough trades
        r = phase7_readiness(
            metrics={
                "win_rate": 0.7, "profit_factor": 2.0,
                "max_drawdown_pct": 0.05,
            },
            total_trades=5,
        )
        assert r["ready"] is False
        assert r["min_trades_ok"] is False
        # Enough trades, all pass
        r2 = phase7_readiness(
            metrics={
                "win_rate": 0.6, "profit_factor": 1.5,
                "max_drawdown_pct": 0.10,
            },
            total_trades=50,
        )
        assert r2["ready"] is True
        assert r2["min_trades_ok"] is True
        assert r2["win_rate_ok"] is True
        assert r2["profit_factor_ok"] is True
        assert r2["drawdown_ok"] is True

    def test_generate_equity_chart_creates_png(
        self, journal: PaperJournal, portfolio: PaperPortfolio, tmp_path: Path
    ) -> None:
        # Seed one close so daily_equity row exists
        _open_long(portfolio, journal, trade_id="CHART-001")
        portfolio.close_position(
            trade_id="CHART-001", exit_price=110.0, exit_reason="tp2"
        )
        out = tmp_path / "equity.png"
        path = generate_equity_chart(
            journal, days_back=7, output_path=str(out)
        )
        assert path is not None
        assert Path(path).exists()
        assert Path(path).suffix == ".png"
        assert Path(path).stat().st_size > 0

    def test_generate_equity_chart_returns_none_on_empty(
        self, journal: PaperJournal, tmp_path: Path
    ) -> None:
        # No daily data — should return None gracefully
        out = tmp_path / "should_not_exist.png"
        result = generate_equity_chart(
            journal, days_back=7, output_path=str(out)
        )
        assert result is None

    def test_build_weekly_summary_shape(
        self, journal: PaperJournal, portfolio: PaperPortfolio
    ) -> None:
        # One winner + one loser to exercise both top lists.
        _open_long(portfolio, journal, trade_id="WS-WIN")
        portfolio.close_position(
            trade_id="WS-WIN", exit_price=110.0, exit_reason="tp2"
        )
        _open_long(
            portfolio, journal,
            trade_id="WS-LOSS", entry_price=100.0,
        )
        portfolio.close_position(
            trade_id="WS-LOSS", exit_price=95.0, exit_reason="sl"
        )
        summary = build_weekly_summary(journal, days_back=7)
        assert "period" in summary
        assert summary["total_trades"] == 2
        assert summary["wins"] == 1
        assert summary["losses"] == 1
        assert summary["profit_factor"] > 0.0
        # The reporter returns all closed trades sorted desc (top_winners)
        # and asc (top_losers), capped at top-3. With 2 trades, both
        # appear in each list. The first/last elements are the actual
        # winner/loser respectively.
        assert len(summary["top_winners"]) == 2
        assert len(summary["top_losers"]) == 2
        # First of top_winners = best (the WIN), last of top_losers = worst (the LOSS)
        assert summary["top_winners"][0]["trade_id"] == "WS-WIN"
        assert summary["top_losers"][0]["trade_id"] == "WS-LOSS"


# =============================================================================
# PaperNotifier tests (5 tiers + graceful degradation)
# =============================================================================


class TestPaperNotifier:
    """5-tier Telegram notifier."""

    def test_graceful_degradation_when_no_token(
        self, tmp_path: Path
    ) -> None:
        # Make sure env vars are empty
        import os

        old_token = os.environ.pop("TELEGRAM_BOT_TOKEN", None)
        old_chat = os.environ.pop("TELEGRAM_CHAT_ID", None)
        try:
            from alerts.telegram import TelegramBot

            bot = TelegramBot()
            assert bot.is_configured is False
            notifier = PaperNotifier(bot=bot)
            assert notifier._enabled is False
            # All tier calls should return False but not raise
            assert (
                notifier.notify_entry(_sample_long_trade()) is False
            )
            assert (
                notifier.notify_exit(_sample_long_trade()) is False
            )
            assert (
                notifier.notify_daily_digest({"balance": 10000}) is False
            )
            assert notifier.notify_weekly_report(
                {"period": "last 7d", "total_trades": 0}
            ) is False
            assert (
                notifier.notify_risk_breach("daily_loss_limit")
                is False
            )
        finally:
            if old_token is not None:
                os.environ["TELEGRAM_BOT_TOKEN"] = old_token
            if old_chat is not None:
                os.environ["TELEGRAM_CHAT_ID"] = old_chat

    def test_notify_entry_message_contains_fields(self) -> None:
        # Mock the bot so _send returns True
        mock_bot = MagicMock()
        mock_bot.is_configured = True
        mock_bot.send_message = MagicMock(return_value=True)
        notifier = PaperNotifier(bot=mock_bot)
        ok = notifier.notify_entry(_sample_long_trade())
        assert ok is True
        # Inspect the message body that was sent
        call = mock_bot.send_message.call_args
        body = call.args[0]
        assert "PAPER ENTRY" in body
        assert "Tier 1" in body
        assert "BTC/USDT" in body
        assert "LONG" in body
        assert "100.0000" in body  # entry price formatted
        assert "95.0000" in body  # SL
        assert "TEST-L-001" in body  # trade_id

    def test_notify_exit_message_contains_fields(self) -> None:
        mock_bot = MagicMock()
        mock_bot.is_configured = True
        mock_bot.send_message = MagicMock(return_value=True)
        notifier = PaperNotifier(bot=mock_bot)
        trade = _sample_long_trade()
        trade.update({
            "exit_price": 110.0, "pnl_usd": 200.0, "pnl_r_multiple": 2.0,
            "exit_reason": "tp2",
        })
        ok = notifier.notify_exit(trade)
        assert ok is True
        body = mock_bot.send_message.call_args.args[0]
        assert "PAPER EXIT" in body
        assert "Tier 2" in body
        assert "tp2" in body
        assert "$200.00" in body  # positive PnL shown without + prefix
        assert "+2.00R" in body

    def test_notify_daily_digest_message_contains_fields(self) -> None:
        mock_bot = MagicMock()
        mock_bot.is_configured = True
        mock_bot.send_message = MagicMock(return_value=True)
        notifier = PaperNotifier(bot=mock_bot)
        state = {
            "balance": 10_500.0, "equity": 10_500.0,
            "initial_balance": 10_000.0, "daily_pnl": 200.0,
            "trades_today": 3, "wins": 2, "losses": 1,
            "win_rate": 0.666, "drawdown_pct": 0.02, "open_count": 1,
        }
        ok = notifier.notify_daily_digest(state, date_str="2026-08-29")
        assert ok is True
        body = mock_bot.send_message.call_args.args[0]
        assert "DAILY DIGEST" in body
        assert "Tier 3" in body
        assert "2026-08-29" in body
        assert "3" in body  # trades_today

    def test_notify_weekly_report_message_contains_fields(self) -> None:
        mock_bot = MagicMock()
        mock_bot.is_configured = True
        mock_bot.send_message = MagicMock(return_value=True)
        notifier = PaperNotifier(bot=mock_bot)
        report = {
            "period": "last 7d", "total_trades": 10,
            "wins": 6, "losses": 4, "win_rate": 0.6,
            "profit_factor": 1.8, "total_pnl": 350.0,
            "max_drawdown_pct": 0.05, "avg_r_multiple": 1.4,
            "top_winners": [
                {"symbol": "BTC/USDT", "pnl_usd": 250.0,
                 "pnl_r_multiple": 2.5}
            ],
            "top_losers": [
                {"symbol": "ETH/USDT", "pnl_usd": -80.0,
                 "pnl_r_multiple": -1.0}
            ],
        }
        ok = notifier.notify_weekly_report(report, chart_path="/tmp/x.png")
        assert ok is True
        body = mock_bot.send_message.call_args.args[0]
        assert "WEEKLY REPORT" in body
        assert "Tier 4" in body
        assert "last 7d" in body
        assert "BTC/USDT" in body  # top winner
        assert "ETH/USDT" in body  # top loser
        assert "/tmp/x.png" in body  # chart path

    def test_notify_risk_breach_message_contains_fields(self) -> None:
        mock_bot = MagicMock()
        mock_bot.is_configured = True
        mock_bot.send_message = MagicMock(return_value=True)
        notifier = PaperNotifier(bot=mock_bot)
        ok = notifier.notify_risk_breach(
            "drawdown_circuit",
            {
                "drawdown_pct": 0.18, "equity": 8200.0,
                "paused_until": "2026-08-30T00:00:00+00:00",
            },
        )
        assert ok is True
        body = mock_bot.send_message.call_args.args[0]
        assert "RISK ALERT" in body
        assert "Tier 5" in body
        assert "drawdown_circuit" in body
        assert "18.00%" in body  # formatted drawdown

    def test_tier_constants(self) -> None:
        assert TIER_ENTRY == 1
        assert TIER_EXIT == 2
        assert TIER_DAILY == 3
        assert TIER_WEEKLY == 4
        assert TIER_RISK == 5
        # Module-level vs class import must agree
        assert _PaperNotifier is PaperNotifier


# --- MTF (Multi-Timeframe) tests — v1.1.0 Relaxed MTF Combo ---

class TestMTFFilter:
    """Test MTF filter logic di paper trader.

    Validated behavior (from /tmp/xauusd_mtf_tweaks_report.md):
      - PAPER_MTF_ENABLED=False: filter pass-through (backward compat)
      - PAPER_MTF_ENABLED=True + bias matches: allow
      - PAPER_MTF_ENABLED=True + bias mismatch: block
      - PAPER_MTF_ENABLED=True + bias None: block (safer)
    """

    def setup_method(self) -> None:
        """Reset cache before each test."""
        from paper.trader import _clear_daily_bias_cache
        _clear_daily_bias_cache()

    def test_mtf_disabled_always_allows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """PAPER_MTF_ENABLED=False → filter tidak aktif, selalu True."""
        import src.config as cfg
        monkeypatch.setattr(cfg, "PAPER_MTF_ENABLED", False)
        from paper.trader import check_mtf_filter
        # Even with bad direction, returns True (no filter active)
        assert check_mtf_filter("long", symbol="XAU/USD") is True
        assert check_mtf_filter("short", symbol="XAU/USD") is True

    def test_mtf_enabled_bias_match_allows(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Bias 'long' + signal 'long' → allow."""
        # Patch trader module directly (not just cfg) so the
        # already-imported constant reflects the test value.
        from paper import trader as t_mod
        monkeypatch.setattr(t_mod, "PAPER_MTF_ENABLED", True)
        monkeypatch.setattr(
            t_mod, "_fetch_daily_bias", lambda sym: ("long", 2)
        )
        assert t_mod.check_mtf_filter("long", symbol="XAU/USD") is True

    def test_mtf_enabled_bias_mismatch_blocks(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Bias 'long' + signal 'short' → block."""
        from paper import trader as t_mod
        monkeypatch.setattr(t_mod, "PAPER_MTF_ENABLED", True)
        monkeypatch.setattr(
            t_mod, "_fetch_daily_bias", lambda sym: ("long", 2)
        )
        assert t_mod.check_mtf_filter("short", symbol="XAU/USD") is False

    def test_mtf_enabled_no_bias_blocks(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Bias None (unclear market) → block (safer side)."""
        from paper import trader as t_mod
        monkeypatch.setattr(t_mod, "PAPER_MTF_ENABLED", True)
        monkeypatch.setattr(
            t_mod, "_fetch_daily_bias", lambda sym: (None, 0)
        )
        assert t_mod.check_mtf_filter("long", symbol="XAU/USD") is False

    def test_mtf_fetch_daily_bias_returns_tuple(self) -> None:
        """_fetch_daily_bias returns (direction, score) tuple."""
        from paper.trader import _fetch_daily_bias
        result = _fetch_daily_bias("XAU/USD")
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_mtf_config_defaults(self) -> None:
        """Config defaults: disabled, threshold1/2, XAU/USD symbol."""
        import src.config as cfg
        assert cfg.PAPER_MTF_ENABLED is False  # OFF by default
        assert cfg.PAPER_MTF_DAILY_MIN_SCORE == 1
        assert cfg.PAPER_MTF_15M_MIN_SCORE == 2
        assert cfg.PAPER_MTF_DAILY_SYMBOL == "XAU/USD"
        assert cfg.PAPER_MTF_BIAS_CACHE_TTL > 0
