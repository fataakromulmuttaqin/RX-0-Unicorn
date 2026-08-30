"""
Tests for correlation_guard module.

Covers:
- Group lookups
- Correlation detection (same group, cross-group, independent)
- Limit enforcement (max 2 correlated positions)
- Edge cases (independents, unknown symbols)
"""
import sys
from pathlib import Path

# Setup path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from paper.correlation_guard import (
    get_group,
    are_correlated,
    check_correlation_limit,
    get_correlation_summary,
)


class TestGetGroup:
    def test_btc_eth_in_l1_majors(self):
        assert get_group("BTC/USDT") == "l1_majors"
        assert get_group("ETH/USDT") == "l1_majors"

    def test_sol_bnb_in_l1_alts(self):
        assert get_group("SOL/USDT") == "l1_alts"
        assert get_group("BNB/USDT") == "l1_alts"

    def test_arb_op_in_l2s(self):
        assert get_group("ARB/USDT") == "l2s"
        assert get_group("OP/USDT") == "l2s"

    def test_doge_in_memes(self):
        assert get_group("DOGE/USDT") == "memes"
        assert get_group("SHIB/USDT") == "memes"
        assert get_group("PEPE/USDT") == "memes"

    def test_unknown_symbol_independent(self):
        assert get_group("UNKNOWN/USDT") == "independent"

    def test_normalize_usdc_to_usdt(self):
        assert get_group("BTC/USDC") == "l1_majors"


class TestAreCorrelated:
    def test_same_group_correlated(self):
        corr, reason = are_correlated("BTC/USDT", "ETH/USDT")
        assert corr is True
        assert "l1_majors" in reason

    def test_l1_alts_same_group(self):
        corr, reason = are_correlated("SOL/USDT", "AVAX/USDT")
        assert corr is True
        assert "l1_alts" in reason

    def test_memes_same_group(self):
        corr, reason = are_correlated("DOGE/USDT", "SHIB/USDT")
        assert corr is True

    def test_l2s_same_group(self):
        corr, reason = are_correlated("ARB/USDT", "OP/USDT")
        assert corr is True

    def test_btc_l1_alt_cross_correlated(self):
        # BTC drop affects L1 alts
        corr, reason = are_correlated("BTC/USDT", "SOL/USDT")
        assert corr is True
        assert "cross-group" in reason or "BTC drop" in reason

    def test_defi_unrelated_to_memes(self):
        corr, reason = are_correlated("UNI/USDT", "DOGE/USDT")
        # UNI (defi) vs DOGE (memes): cross-group
        # memes dump when BTC dumps, defi follows BTC/ETH
        # So UNI and DOGE both correlate to BTC, but NOT to each other
        # However: defi follows BTC/ETH, memes dump with BTC
        # These are different paths — they shouldn't be directly correlated
        # Result: not correlated (good — diversification)
        assert corr is False

    def test_independent_never_correlated(self):
        corr, reason = are_correlated("UNKNOWN/USDT", "BTC/USDT")
        assert corr is False

    def test_same_symbol_not_correlated(self):
        corr, reason = are_correlated("BTC/USDT", "BTC/USDT")
        assert corr is False


class TestCheckCorrelationLimit:
    def test_no_open_positions_allowed(self):
        allowed, reason = check_correlation_limit("BTC/USDT", [])
        assert allowed is True
        assert reason == "no_open_positions"

    def test_independent_symbol_always_allowed(self):
        open_pos = [{"symbol": "BTC/USDT"}, {"symbol": "ETH/USDT"}]
        allowed, reason = check_correlation_limit("UNKNOWN/USDT", open_pos)
        assert allowed is True
        assert reason == "independent_symbol"

    def test_one_correlated_allowed(self):
        open_pos = [{"symbol": "BTC/USDT"}]
        allowed, reason = check_correlation_limit("ETH/USDT", open_pos)
        assert allowed is True
        assert "1 correlated" in reason or "max 2" in reason

    def test_two_correlated_at_limit_blocked(self):
        # Open: BTC + ETH (both l1_majors, 2 correlated)
        # Try SOL — cross-group correlation (l1_alts follows l1_majors)
        # BTC↔SOL correlated + ETH↔SOL correlated = 2 correlated positions
        # At max=2 → blocked (would be 3rd correlated position)
        open_pos = [{"symbol": "BTC/USDT"}, {"symbol": "ETH/USDT"}]
        allowed, reason = check_correlation_limit("SOL/USDT", open_pos)
        assert allowed is False
        assert "correlation_limit" in reason

    def test_three_l1_alts_blocked(self):
        # BTC + SOL open, try to add AVAX (l1_alt) — AVAX is correlated with both
        open_pos = [{"symbol": "BTC/USDT"}, {"symbol": "SOL/USDT"}]
        allowed, reason = check_correlation_limit("AVAX/USDT", open_pos)
        assert allowed is False
        assert "correlation_limit" in reason
        assert "AVAX/USDT" in reason

    def test_three_l1_majors_blocked(self):
        # Try to add a 3rd BTC/ETH/LTC position (all l1_majors)
        open_pos = [{"symbol": "BTC/USDT"}, {"symbol": "ETH/USDT"}]
        allowed, reason = check_correlation_limit("LTC/USDT", open_pos)
        assert allowed is False
        assert "correlation_limit" in reason

    def test_mixed_groups_blocked_correctly(self):
        # 2 memes open (DOGE + SHIB), try to add BONK (3rd meme)
        open_pos = [{"symbol": "DOGE/USDT"}, {"symbol": "SHIB/USDT"}]
        allowed, reason = check_correlation_limit("BONK/USDT", open_pos)
        assert allowed is False
        assert "memes" in reason

    def test_diversification_allowed(self):
        # Open: BTC (l1_majors) + DOGE (memes) — 2 correlated (cross-group l1_majors↔memes)
        # Try to add UNI (defi)
        # - UNI ↔ BTC: cross-group (l1_majors↔defi) → correlated
        # - UNI ↔ DOGE: NO direct cross-corr rule between defi and memes
        # So only 1 correlated (BTC) → allowed
        open_pos = [
            {"symbol": "BTC/USDT"},
            {"symbol": "DOGE/USDT"},
        ]
        allowed, reason = check_correlation_limit("UNI/USDT", open_pos)
        assert allowed is True, f"expected allowed but got: {reason}"

    def test_max_correlated_param(self):
        # Test custom max_correlated=1
        open_pos = [{"symbol": "BTC/USDT"}]
        allowed, reason = check_correlation_limit("ETH/USDT", open_pos, max_correlated=1)
        # BTC + ETH both l1_majors, 1 correlated (BTC) → at max
        # 1 position is at the limit (1 ≥ max_correlated=1) → blocked
        assert allowed is False


class TestGetCorrelationSummary:
    def test_empty_portfolio(self):
        summary = get_correlation_summary([])
        assert summary["total_positions"] == 0
        assert summary["violations"] == []

    def test_diversified_portfolio(self):
        # 1 from each group = good
        open_pos = [
            {"symbol": "BTC/USDT"},
            {"symbol": "SOL/USDT"},
            {"symbol": "ARB/USDT"},
        ]
        summary = get_correlation_summary(open_pos)
        assert summary["total_positions"] == 3
        assert summary["group_counts"]["l1_majors"] == 1
        assert summary["group_counts"]["l1_alts"] == 1
        assert summary["group_counts"]["l2s"] == 1
        # No group has more than 2 positions
        assert summary["violations"] == []

    def test_portfolio_with_violation(self):
        # 3 L1 majors (BTC, ETH, LTC) — exceeds max 2
        open_pos = [
            {"symbol": "BTC/USDT"},
            {"symbol": "ETH/USDT"},
            {"symbol": "LTC/USDT"},
        ]
        summary = get_correlation_summary(open_pos)
        assert len(summary["violations"]) >= 1
        # Should flag l1_majors with 3 positions
        assert any("l1_majors" in v for v in summary["violations"])

    def test_includes_independent(self):
        open_pos = [
            {"symbol": "BTC/USDT"},
            {"symbol": "UNKNOWN/USDT"},
        ]
        summary = get_correlation_summary(open_pos)
        assert "independent" in summary["groups"]
        assert summary["group_counts"]["independent"] == 1


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
