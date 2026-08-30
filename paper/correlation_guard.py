"""
Correlation guard — prevents over-exposure to correlated assets.

Per STRATEGY.md line 162:
"Max 2 posisi correlated"

If BTC drops 5%, L1 alts typically drop 8-12%. Having 3 BTC-correlated
positions means 3x the risk, NOT 3x the diversification.

Groups:
  - L1 majors: BTC, ETH (these two are highly correlated, treat as one)
  - L1 alternatives: SOL, BNB, ADA, AVAX, DOT, NEAR, ATOM, APT, SUI, TIA
  - L2s: ARB, OP, MATIC, IMX, LDO
  - DeFi: UNI, AAVE, CRV, SNX, COMP, MKR, 1INCH
  - Memes: DOGE, SHIB, PEPE, BONK, WIF, FLOKI
  - AI narrative: FET, RLC, AGIX
  - Privacy: XMR, ZEC
  - Exchange tokens: BNB, OKB, KCS (BNB overlaps L1 alts)
  - RWA: ONDO, PENDLE
  - GameFi: AXS, MANA, SAND, GALA
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


# Correlation groups: pair -> group name
# Pairs not in this map default to "independent" (uncorrelated with others)
_CORRELATION_GROUPS: dict[str, str] = {
    # L1 majors (BTC + ETH treated as one group)
    "BTC/USDT": "l1_majors",
    "ETH/USDT": "l1_majors",
    "ETH/BTC": "l1_majors",
    "WBTC/USDT": "l1_majors",
    "BCH/USDT": "l1_majors",
    "LTC/USDT": "l1_majors",

    # L1 alternatives (ETH-killers + smart contract L1s)
    "SOL/USDT": "l1_alts",
    "BNB/USDT": "l1_alts",
    "ADA/USDT": "l1_alts",
    "AVAX/USDT": "l1_alts",
    "DOT/USDT": "l1_alts",
    "NEAR/USDT": "l1_alts",
    "ATOM/USDT": "l1_alts",
    "APT/USDT": "l1_alts",
    "SUI/USDT": "l1_alts",
    "TIA/USDT": "l1_alts",
    "ALGO/USDT": "l1_alts",
    "EGLD/USDT": "l1_alts",
    "FTM/USDT": "l1_alts",
    "INJ/USDT": "l1_alts",

    # L2s / Scaling
    "ARB/USDT": "l2s",
    "OP/USDT": "l2s",
    "MATIC/USDT": "l2s",
    "IMX/USDT": "l2s",
    "LDO/USDT": "l2s",
    "MANTA/USDT": "l2s",

    # DeFi
    "UNI/USDT": "defi",
    "AAVE/USDT": "defi",
    "CRV/USDT": "defi",
    "SNX/USDT": "defi",
    "COMP/USDT": "defi",
    "MKR/USDT": "defi",
    "1INCH/USDT": "defi",
    "DYDX/USDT": "defi",
    "GMX/USDT": "defi",
    "PENDLE/USDT": "defi",
    "GRT/USDT": "defi",
    "LDO/USDT": "defi",  # already in l2s but also defi - priority to l2s
    "ENS/USDT": "defi",
    "SNX/USDT": "defi",

    # Memes
    "DOGE/USDT": "memes",
    "SHIB/USDT": "memes",
    "PEPE/USDT": "memes",
    "BONK/USDT": "memes",
    "WIF/USDT": "memes",
    "FLOKI/USDT": "memes",

    # AI narrative
    "FET/USDT": "ai",
    "RNDR/USDT": "ai",
    "WLD/USDT": "ai",
    "TAO/USDT": "ai",
    "AGIX/USDT": "ai",
    "MASK/USDT": "ai",

    # Privacy
    "XMR/USDT": "privacy",
    "ZEC/USDT": "privacy",

    # GameFi / Metaverse
    "AXS/USDT": "gamefi",
    "MANA/USDT": "gamefi",
    "SAND/USDT": "gamefi",
    "GALA/USDT": "gamefi",

    # Storage / Infra
    "FIL/USDT": "infra",
    "AR/USDT": "infra",
    "STORJ/USDT": "infra",
    "GRT/USDT": "infra",  # already in defi but also infra
    "LINK/USDT": "infra",
    "BAND/USDT": "infra",

    # RWA (Real World Assets)
    "ONDO/USDT": "rwa",
    "MATR/USDT": "rwa",

    # Exchange tokens
    "OKB/USDT": "exchange",
    "KCS/USDT": "exchange",
    "LEO/USDT": "exchange",
    "CRO/USDT": "exchange",

    # Privacy-adjacent
    "DASH/USDT": "privacy",
    "ZEC/USDT": "privacy",
}


# Cross-correlations: some pairs are correlated ACROSS groups
_CROSS_CORRELATIONS: list[tuple[str, str, str]] = [
    # (group1, group2, reason)
    ("l1_majors", "l1_alts", "BTC drop affects alts"),
    ("l1_majors", "l2s", "ETH drop affects L2s"),
    ("l1_majors", "defi", "DeFi follows BTC/ETH"),
    ("l1_majors", "memes", "memes dump when BTC dumps"),
    ("l1_majors", "ai", "AI tokens follow BTC narrative"),
    ("l1_majors", "privacy", "alts dump with BTC"),
    ("l1_majors", "rwa", "RWA follows macro/BTC"),
    ("l1_majors", "gamefi", "GameFi follows altcoin season"),
    ("l1_alts", "l2s", "smart contract L1s move with L2s"),
    ("l1_alts", "memes", "altcoin season correlation"),
    ("l1_alts", "ai", "alts correlation"),
    ("l1_alts", "gamefi", "alts correlation"),
    ("l1_alts", "infra", "infra tokens follow L1 alts"),
    ("l1_majors", "infra", "infra projects (LINK, GRT) follow BTC/ETH"),
    ("l1_alts", "rwa", "RWA narrative overlaps alts"),
    ("l2s", "defi", "L2s and DeFi share narrative"),
    ("ai", "gamefi", "tech narrative tokens"),
    ("ai", "l2s", "tech narrative tokens"),
    ("memes", "l1_alts", "altcoin season: memes + alts move together"),
    ("memes", "l2s", "memecoin season affects L2s"),
    ("privacy", "l1_alts", "altcoin season correlation"),
]


def get_group(symbol: str) -> str:
    """Get correlation group for a symbol. Returns 'independent' if not in map."""
    # Normalize symbol (uppercase, USDT preferred)
    sym = symbol.upper().replace("USDC", "USDT").replace("BUSD", "USDT")
    return _CORRELATION_GROUPS.get(sym, "independent")


def are_correlated(symbol1: str, symbol2: str) -> tuple[bool, str]:
    """
    Check if two symbols are in correlated groups.
    Returns (correlated: bool, reason: str).
    """
    if symbol1 == symbol2:
        return False, ""

    g1 = get_group(symbol1)
    g2 = get_group(symbol2)

    # Same group = correlated
    if g1 == g2 and g1 != "independent":
        return True, f"both in group '{g1}'"

    # Independent = never correlated
    if g1 == "independent" or g2 == "independent":
        return False, ""

    # Check cross-correlation rules
    for cg1, cg2, reason in _CROSS_CORRELATIONS:
        if (g1 == cg1 and g2 == cg2) or (g1 == cg2 and g2 == cg1):
            return True, f"cross-group: {reason}"

    return False, ""


def check_correlation_limit(
    proposed_symbol: str,
    open_positions: list[dict[str, Any]],
    max_correlated: int = 2,
) -> tuple[bool, str]:
    """
    Check if opening a new position would exceed correlation limit.

    Args:
        proposed_symbol: pair to be opened (e.g. "BTC/USDT")
        open_positions: list of open trade dicts (must have 'symbol' key)
        max_correlated: max positions allowed per correlation group (default 2)

    Returns:
        (allowed: bool, reason: str)
    """
    if not open_positions:
        return True, "no_open_positions"

    # Count open positions in each correlation group
    group_counts: dict[str, list[str]] = {}
    for pos in open_positions:
        sym = pos.get("symbol", "")
        if not sym:
            continue
        g = get_group(sym)
        if g != "independent":
            group_counts.setdefault(g, []).append(sym)

    # Check proposed symbol
    proposed_group = get_group(proposed_symbol)
    if proposed_group == "independent":
        return True, "independent_symbol"

    # Count how many open positions are correlated with proposed
    correlated_symbols = []
    for pos in open_positions:
        pos_sym = pos.get("symbol", "")
        if not pos_sym or pos_sym == proposed_symbol:
            continue
        is_corr, reason = are_correlated(proposed_symbol, pos_sym)
        if is_corr:
            correlated_symbols.append((pos_sym, reason))

    if len(correlated_symbols) >= max_correlated:
        names = ", ".join(s for s, _ in correlated_symbols)
        return False, (
            f"correlation_limit: {proposed_symbol} ({proposed_group}) "
            f"correlated with {len(correlated_symbols)} open positions: {names}"
        )

    return True, f"only {len(correlated_symbols)} correlated positions open (max {max_correlated})"


def get_correlation_summary(open_positions: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Get summary of correlation distribution in current portfolio.
    """
    groups: dict[str, list[str]] = {}
    for pos in open_positions:
        sym = pos.get("symbol", "")
        if not sym:
            continue
        g = get_group(sym)
        groups.setdefault(g, []).append(sym)

    # Count violations
    violations = []
    for g, symbols in groups.items():
        if len(symbols) > 2 and g != "independent":
            violations.append(f"{g}: {len(symbols)} positions ({', '.join(symbols)})")

    return {
        "total_positions": len(open_positions),
        "groups": groups,
        "group_counts": {g: len(s) for g, s in groups.items()},
        "violations": violations,
    }


# Smoke test
if __name__ == "__main__":
    print("=" * 60)
    print("Correlation Guard — Smoke Test")
    print("=" * 60)

    # Test 1: Are BTC and ETH correlated?
    corr, reason = are_correlated("BTC/USDT", "ETH/USDT")
    print(f"\nBTC vs ETH: correlated={corr}, reason={reason}")

    # Test 2: Are BTC and PEPE correlated?
    corr, reason = are_correlated("BTC/USDT", "PEPE/USDT")
    print(f"BTC vs PEPE: correlated={corr}, reason={reason}")

    # Test 3: Are ARB and OP correlated?
    corr, reason = are_correlated("ARB/USDT", "OP/USDT")
    print(f"ARB vs OP: correlated={corr}, reason={reason}")

    # Test 4: Open positions, try to add another L1 alt
    open_pos = [
        {"symbol": "BTC/USDT"},
        {"symbol": "SOL/USDT"},
    ]
    allowed, reason = check_correlation_limit("AVAX/USDT", open_pos)
    print(f"\nOpen: BTC, SOL. Try to add AVAX:")
    print(f"  allowed={allowed}, reason={reason}")

    # Test 5: Try to add 3rd L1 alt (should fail)
    open_pos = [
        {"symbol": "BTC/USDT"},
        {"symbol": "SOL/USDT"},
        {"symbol": "AVAX/USDT"},
    ]
    allowed, reason = check_correlation_limit("NEAR/USDT", open_pos)
    print(f"\nOpen: BTC, SOL, AVAX. Try to add NEAR:")
    print(f"  allowed={allowed}, reason={reason}")

    # Test 6: Try to add uncorrelated (e.g. meme vs defi)
    allowed, reason = check_correlation_limit("DOGE/USDT", [{"symbol": "UNI/USDT"}])
    print(f"\nOpen: UNI. Try to add DOGE:")
    print(f"  allowed={allowed}, reason={reason}")

    # Summary
    print(f"\n{'='*60}")
    print("Portfolio correlation summary:")
    summary = get_correlation_summary([
        {"symbol": "BTC/USDT"},
        {"symbol": "ETH/USDT"},
        {"symbol": "SOL/USDT"},
        {"symbol": "ARB/USDT"},
        {"symbol": "DOGE/USDT"},
        {"symbol": "AAVE/USDT"},
    ])
    print(f"  Total: {summary['total_positions']}")
    print(f"  By group: {summary['group_counts']}")
    print(f"  Violations: {summary['violations']}")
