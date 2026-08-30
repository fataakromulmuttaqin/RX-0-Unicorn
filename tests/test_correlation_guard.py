"""
Tests for correlation_guard module (v0.8.0 rolling correlation engine).

Covers:
- Static fallback (no DB / insufficient data) uses _STATIC_GROUPS
- Rolling mode: regime-aware correlation from real candles
- Group lookup
- Correlation detection
- Limit enforcement
- Cache freshness + refresh
- Backward compatibility with v0.7.0 callers

Strategy: tests must pass in BOTH modes (rolling OR static). They assert
on observable behavior (e.g., "BTC correlated with ETH", "limit blocks
3rd correlated"), not on internal group labels.
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import paper.correlation_guard as cg
from paper.correlation_guard import (
    get_group,
    are_correlated,
    check_correlation_limit,
    get_correlation_summary,
    get_pair_correlation,
    refresh_cache,
    RHO_THRESHOLD,
)


def _ensure_cache_built():
    """Refresh cache (or build static fallback) so tests don't depend on TTL."""
    return refresh_cache()


# ─────────────────────────────────────────────────────────────────────────────
# Group lookup
# ─────────────────────────────────────────────────────────────────────────────

class TestGetGroup:
    def test_btc_and_eth_share_group_in_both_modes(self):
        """Both rolling + static put BTC and ETH in the same group."""
        _ensure_cache_built()
        # Normalize: any common group is fine; just assert they're equal
        # AND not "independent" (both modes guarantee they're together).
        g_btc = get_group("BTC/USDT")
        g_eth = get_group("ETH/USDT")
        assert g_btc == g_eth
        assert g_btc != "independent"

    def test_normalize_usdc_to_usdt(self):
        _ensure_cache_built()
        # BTC/USDC should normalize to BTC/USDT for lookup
        g1 = get_group("BTC/USDC")
        g2 = get_group("BTC/USDT")
        assert g1 == g2

    def test_unknown_symbol_independent(self):
        _ensure_cache_built()
        assert get_group("UNKNOWN_PAIR_XYZ/USDT") == "independent"

    def test_static_mode_uses_static_groups(self):
        """When DB is missing, static fallback groups BTC in l1_majors."""
        # Force static-no-db by pointing at a non-existent path
        refresh_cache(db_path=Path("/nonexistent/candles.db"))
        assert get_group("BTC/USDT") == "l1_majors"
        assert get_group("ETH/USDT") == "l1_majors"
        assert get_group("SOL/USDT") == "l1_alts"
        assert get_group("DOGE/USDT") == "memes"
        assert get_group("UNKNOWN/USDT") == "independent"
        # Restore normal cache for subsequent tests
        refresh_cache()


# ─────────────────────────────────────────────────────────────────────────────
# are_correlated
# ─────────────────────────────────────────────────────────────────────────────

class TestAreCorrelated:
    def test_same_symbol_not_correlated(self):
        _ensure_cache_built()
        corr, reason = are_correlated("BTC/USDT", "BTC/USDT")
        assert corr is False
        assert reason == ""

    def test_btc_eth_correlated(self):
        _ensure_cache_built()
        # Real 1d data: BTC-ETH ρ ≈ 0.88. Either mode marks them correlated.
        corr, reason = are_correlated("BTC/USDT", "ETH/USDT")
        assert corr is True
        assert reason != ""

    def test_independent_pair_never_correlated(self):
        _ensure_cache_built()
        # UNKNOWN_PAIR has no entry in either rolling or static → always
        # "independent" → never correlated with anyone.
        corr, reason = are_correlated("UNKNOWN_PAIR/USDT", "BTC/USDT")
        assert corr is False

    def test_trx_independent_from_btc_on_real_data(self):
        """Real 1d data shows BTC-TRX ρ ≈ 0.5 — not correlated by threshold."""
        _ensure_cache_built()
        rho = get_pair_correlation("BTC/USDT", "TRX/USDT")
        # If we have rolling data and ρ is computed, assert it's below threshold
        if rho is not None:
            assert abs(rho) < RHO_THRESHOLD, (
                f"BTC-TRX rolling ρ={rho:.3f} unexpectedly ≥ {RHO_THRESHOLD}; "
                f"regime has shifted — verify manually"
            )
            corr, reason = are_correlated("BTC/USDT", "TRX/USDT")
            assert corr is False

    def test_static_mode_ltc_correlated_with_btc(self):
        """Static fallback treats BTC, ETH, LTC all as l1_majors → correlated."""
        refresh_cache(db_path=Path("/nonexistent/candles.db"))
        corr, reason = are_correlated("BTC/USDT", "LTC/USDT")
        assert corr is True
        assert "l1_majors" in reason
        refresh_cache()


# ─────────────────────────────────────────────────────────────────────────────
# check_correlation_limit
# ─────────────────────────────────────────────────────────────────────────────

class TestCheckCorrelationLimit:
    def test_no_open_positions_allowed(self):
        _ensure_cache_built()
        allowed, reason = check_correlation_limit("BTC/USDT", [])
        assert allowed is True
        assert reason == "no_open_positions"

    def test_independent_symbol_always_allowed(self):
        _ensure_cache_built()
        open_pos = [{"symbol": "BTC/USDT"}, {"symbol": "ETH/USDT"}]
        allowed, reason = check_correlation_limit("UNKNOWN_PAIR_XYZ/USDT", open_pos)
        assert allowed is True
        assert reason == "independent_symbol"

    def test_btc_blocks_ltc_when_both_open(self):
        """Open BTC + ETH (correlated). Adding LTC must be blocked (3rd)."""
        _ensure_cache_built()
        open_pos = [{"symbol": "BTC/USDT"}, {"symbol": "ETH/USDT"}]
        allowed, reason = check_correlation_limit("LTC/USDT", open_pos)
        assert allowed is False
        assert "correlation_limit" in reason

    def test_btc_allows_trx_on_real_data(self):
        """Open BTC. TRX should be allowed (independent at ρ≈0.5)."""
        _ensure_cache_built()
        rho = get_pair_correlation("BTC/USDT", "TRX/USDT")
        if rho is not None:
            allowed, reason = check_correlation_limit("TRX/USDT", [{"symbol": "BTC/USDT"}])
            # Either allowed (independent) OR blocked (if rolling detected high
            # correlation due to recent regime shift). Verify the reason
            # aligns with the actual rolling matrix.
            if abs(rho) < RHO_THRESHOLD:
                assert allowed is True, (
                    f"BTC-TRX ρ={rho:.3f} < threshold but check failed: {reason}"
                )
            else:
                assert allowed is False

    def test_max_correlated_param(self):
        _ensure_cache_built()
        open_pos = [{"symbol": "BTC/USDT"}]
        # max_correlated=1: any new correlated position is blocked
        allowed, reason = check_correlation_limit("ETH/USDT", open_pos, max_correlated=1)
        # BTC and ETH correlated; with max=1, ETH would be 2nd → blocked
        assert allowed is False
        assert "correlation_limit" in reason

    def test_two_btc_eth_open_blocks_ltc_in_static_mode(self):
        """In static mode, BTC+ETH+LTC are all l1_majors → adding LTC is 3rd → blocked."""
        refresh_cache(db_path=Path("/nonexistent/candles.db"))
        open_pos = [{"symbol": "BTC/USDT"}, {"symbol": "ETH/USDT"}]
        allowed, reason = check_correlation_limit("LTC/USDT", open_pos)
        assert allowed is False
        assert "correlation_limit" in reason
        refresh_cache()

    def test_known_correlated_returns_2nd_position_allowed(self):
        """Open 1 correlated. Adding a 2nd correlated is still allowed (max=2)."""
        _ensure_cache_built()
        open_pos = [{"symbol": "BTC/USDT"}]
        # ETH is correlated with BTC → 1st correlated, at limit but allowed
        allowed, reason = check_correlation_limit("ETH/USDT", open_pos)
        assert allowed is True
        assert "1 correlated" in reason


# ─────────────────────────────────────────────────────────────────────────────
# get_correlation_summary
# ─────────────────────────────────────────────────────────────────────────────

class TestGetCorrelationSummary:
    def test_empty_portfolio(self):
        _ensure_cache_built()
        summary = get_correlation_summary([])
        assert summary["total_positions"] == 0
        assert summary["violations"] == []

    def test_diversified_portfolio_no_violations(self):
        _ensure_cache_built()
        # BTC, TRX, ARB — at most one correlation each (BTC-TRX=independent on 1d)
        open_pos = [
            {"symbol": "BTC/USDT"},
            {"symbol": "TRX/USDT"},
            {"symbol": "ARB/USDT"},
        ]
        summary = get_correlation_summary(open_pos)
        assert summary["total_positions"] == 3
        # No group should have >2 positions
        assert summary["violations"] == [], f"unexpected violations: {summary['violations']}"

    def test_summary_includes_cache_metadata(self):
        _ensure_cache_built()
        summary = get_correlation_summary([{"symbol": "BTC/USDT"}])
        assert "cache_source" in summary
        assert summary["cache_source"] in ("rolling", "static_insufficient_data",
                                           "static_no_data", "static_no_db", "static_error")
        assert "cache_pairs" in summary
        assert "cache_candles" in summary
        assert "cache_age_sec" in summary

    def test_summary_groups_have_correct_counts(self):
        _ensure_cache_built()
        open_pos = [{"symbol": "BTC/USDT"}, {"symbol": "ETH/USDT"}]
        summary = get_correlation_summary(open_pos)
        # BTC and ETH must be in the same group (rolling or static)
        groups = summary["groups"]
        same_group = [g for g, syms in groups.items() if "BTC/USDT" in syms and "ETH/USDT" in syms]
        assert len(same_group) == 1, f"Expected BTC+ETH in one group, got groups: {groups}"
        # The group must contain both
        assert "BTC/USDT" in groups[same_group[0]]
        assert "ETH/USDT" in groups[same_group[0]]

    def test_includes_independent(self):
        _ensure_cache_built()
        open_pos = [{"symbol": "BTC/USDT"}, {"symbol": "UNKNOWN_PAIR/USDT"}]
        summary = get_correlation_summary(open_pos)
        assert "independent" in summary["groups"]
        assert summary["group_counts"]["independent"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# Cache behavior
# ─────────────────────────────────────────────────────────────────────────────

class TestCacheBehavior:
    def test_refresh_returns_status(self):
        status = refresh_cache()
        assert "source" in status
        assert "pairs" in status
        assert "candles" in status
        assert "groups" in status
        assert isinstance(status["built_at"], float)
        assert status["built_at"] > 0

    def test_rolling_when_db_has_data(self):
        status = refresh_cache()
        # If candles.db exists and has enough aligned daily candles,
        # source should be "rolling". Otherwise allow static fallback.
        if status["source"] == "rolling":
            assert status["pairs"] >= 10  # at least 10 pairs
            assert status["candles"] >= 60  # at least MIN_CANDLES
            assert status["groups"] >= 1

    def test_cache_survives_calls(self):
        """Multiple get_group calls within TTL should not re-query DB."""
        refresh_cache()
        before = cg._CACHE.built_at
        g1 = get_group("BTC/USDT")
        g2 = get_group("ETH/USDT")
        after = cg._CACHE.built_at
        # Same built_at → no rebuild
        assert before == after

    def test_get_pair_correlation_returns_none_for_unknown(self):
        _ensure_cache_built()
        rho = get_pair_correlation("BTC/USDT", "NOT_A_REAL_PAIR/USDT")
        assert rho is None

    def test_get_pair_correlation_self_is_one(self):
        _ensure_cache_built()
        rho = get_pair_correlation("BTC/USDT", "BTC/USDT")
        assert rho == 1.0


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))