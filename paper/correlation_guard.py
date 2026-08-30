"""
Rolling correlation guard — prevents over-exposure to currently-correlated assets.

Why rolling instead of static groups (v0.7.0 → v0.8.0):
  - Static maps (BTC always with ETH/ALTS) ignore regime changes.
  - In real data, BTC-SOL rolling ρ ranges from 0.51 to 0.77 — sometimes
    they're "the same trade", sometimes they're not.
  - BTC-TRX has long-run ρ ≈ 0.43 (clearly independent) — a static
    "everything follows BTC" rule would have blocked that diversification.
  - Rolling window adapts to current market regime (alts decoupling,
    stablecoin depegs, BTC dominance shifts).

Algorithm (per STRATEGY.md §"Correlation Guard"):
  1. Load last `WINDOW` daily candles from SQLite for each pair.
  2. Pivot to wide format (inner join on timestamp → fully aligned rows).
  3. Compute log-returns for every pair.
  4. Build Pearson correlation matrix.
  5. Two symbols are "correlated" when ρ ≥ RHO_THRESHOLD (default 0.70).
  6. Two symbols are "inversely correlated" when ρ ≤ INVERSE_THRESHOLD
     (-0.70) — still counts as correlated for risk purposes (both = directional
     bet on BTC sentiment, just opposite signs).
  7. Greedy cluster: assign each symbol to a group; symbols join a group
     when |ρ| ≥ threshold with ALL existing members.
  8. Cache the matrix + per-pair group labels for CACHE_TTL seconds.
  9. On cache miss/error, fall back to the v0.7.0 static group map so
     behavior never goes unbounded.

Public API (unchanged from v0.7.0 — all callers stay compatible):
  - get_group(symbol) -> str
  - are_correlated(s1, s2) -> (bool, reason)
  - check_correlation_limit(symbol, open_positions, max_correlated=2) -> (bool, reason)
  - get_correlation_summary(open_positions) -> dict
  - get_pair_correlation(s1, s2) -> float | None  (new helper)
  - refresh_cache() -> dict  (new helper)
"""
from __future__ import annotations

import sys
import sqlite3
import time as _time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

# ─── Configuration ────────────────────────────────────────────────────────

# Path to candle DB (overridable for tests).
CANDLES_DB = PROJECT_ROOT / "data" / "storage" / "candles.db"

# Rolling window in daily candles. 90 ≈ 3 months — enough to span regime
# shifts without lagging too hard. Pairs DB has 500 daily candles (~1.4y)
# so 90 leaves headroom for warm-up.
WINDOW = 90

# Minimum aligned daily candles required before rolling correlation is trusted.
# Below this, fall back to static groups.
MIN_CANDLES = 60

# Cache lifetime (seconds). 5 min — long enough that we don't pound the DB,
# short enough that a regime shift shows up within one scan cycle.
CACHE_TTL = 300

# Correlation thresholds.
RHO_THRESHOLD = 0.70          # ρ above this = "correlated group"
INVERSE_THRESHOLD = -0.70     # ρ below this = inversely correlated (still risky)

# Timeframe to read from candles.db. Daily = smoothest, regime-stable.
TIMEFRAME = "1d"


# ─── Static fallback (v0.7.0 behavior, used only when rolling is unavailable)

# Correlation groups: pair -> group name
# Pairs not in this map default to "independent" (uncorrelated with others)
_STATIC_GROUPS: dict[str, str] = {
    # L1 majors (BTC + ETH treated as one group)
    "BTC/USDT": "l1_majors", "ETH/USDT": "l1_majors",
    "ETH/BTC": "l1_majors", "WBTC/USDT": "l1_majors",
    "BCH/USDT": "l1_majors", "LTC/USDT": "l1_majors",
    # L1 alternatives
    "SOL/USDT": "l1_alts", "BNB/USDT": "l1_alts", "ADA/USDT": "l1_alts",
    "AVAX/USDT": "l1_alts", "DOT/USDT": "l1_alts", "NEAR/USDT": "l1_alts",
    "ATOM/USDT": "l1_alts", "APT/USDT": "l1_alts", "SUI/USDT": "l1_alts",
    "TIA/USDT": "l1_alts", "ALGO/USDT": "l1_alts", "EGLD/USDT": "l1_alts",
    "FTM/USDT": "l1_alts", "INJ/USDT": "l1_alts",
    # L2s / Scaling
    "ARB/USDT": "l2s", "OP/USDT": "l2s", "MATIC/USDT": "l2s",
    "IMX/USDT": "l2s", "LDO/USDT": "l2s", "MANTA/USDT": "l2s",
    # DeFi
    "UNI/USDT": "defi", "AAVE/USDT": "defi", "CRV/USDT": "defi",
    "SNX/USDT": "defi", "COMP/USDT": "defi", "MKR/USDT": "defi",
    "1INCH/USDT": "defi", "DYDX/USDT": "defi", "GMX/USDT": "defi",
    "PENDLE/USDT": "defi", "GRT/USDT": "defi", "ENS/USDT": "defi",
    # Memes
    "DOGE/USDT": "memes", "SHIB/USDT": "memes", "PEPE/USDT": "memes",
    "BONK/USDT": "memes", "WIF/USDT": "memes", "FLOKI/USDT": "memes",
    # AI narrative
    "FET/USDT": "ai", "RNDR/USDT": "ai", "WLD/USDT": "ai",
    "TAO/USDT": "ai", "AGIX/USDT": "ai", "MASK/USDT": "ai",
    # Privacy
    "XMR/USDT": "privacy", "ZEC/USDT": "privacy", "DASH/USDT": "privacy",
    # GameFi / Metaverse
    "AXS/USDT": "gamefi", "MANA/USDT": "gamefi",
    "SAND/USDT": "gamefi", "GALA/USDT": "gamefi",
    # Storage / Infra
    "FIL/USDT": "infra", "AR/USDT": "infra", "STORJ/USDT": "infra",
    "LINK/USDT": "infra", "BAND/USDT": "infra",
    # RWA (Real World Assets)
    "ONDO/USDT": "rwa", "MATR/USDT": "rwa",
    # Exchange tokens
    "OKB/USDT": "exchange", "KCS/USDT": "exchange",
    "LEO/USDT": "exchange", "CRO/USDT": "exchange",
}


def _normalize(symbol: str) -> str:
    """Normalize symbol: uppercase, USDC/BUSD → USDT."""
    return symbol.upper().replace("USDC", "USDT").replace("BUSD", "USDT")


def _get_static_group(symbol: str) -> str:
    """v0.7.0 static fallback — never raises."""
    return _STATIC_GROUPS.get(_normalize(symbol), "independent")


# ─── Rolling correlation engine ────────────────────────────────────────────

class _CorrelationCache:
    """
    In-memory cache for the rolling correlation matrix.

    Holds:
      - matrix: dict[(s1, s2) -> float]  (symmetric, self-pair = 1.0)
      - groups: dict[symbol -> group_name]  (assigned by greedy clustering
        over the correlation matrix; pairs with |ρ| ≥ threshold share a group)
      - built_at: float  (when the cache was last computed)
      - source: "rolling" | "static_…"  (which mode produced this cache)
      - candle_count: int  (how many aligned rows fed the calculation)
      - pairs: list[str]  (symbols that contributed)
    """
    __slots__ = ("matrix", "groups", "built_at", "source", "candle_count", "pairs")

    def __init__(self):
        self.matrix: dict[tuple[str, str], float] = {}
        self.groups: dict[str, str] = {}
        self.built_at: float = 0.0
        self.source: str = "static"
        self.candle_count: int = 0
        self.pairs: list[str] = []

    def is_fresh(self) -> bool:
        return (_time.time() - self.built_at) < CACHE_TTL if self.built_at else False


_CACHE = _CorrelationCache()


def _build_rolling_matrix(db_path: Path = CANDLES_DB) -> _CorrelationCache:
    """
    Build the rolling correlation matrix from `candles.db`.

    Strategy:
      - Pull last `WINDOW + 5` daily candles for every pair.
      - Pivot to wide format (timestamp index, pair columns), drop pairs
        with any NaN — we need all pairs fully aligned for an inner-join
        correlation matrix.
      - Compute log-returns.
      - Pearson correlation matrix.
      - Greedy cluster: assign each symbol to a group; join when |ρ| ≥
        threshold with ALL existing members.

    Falls back to static groups on:
      - DB file missing
      - Less than MIN_CANDLES aligned rows
      - Any other read/compute error
    """
    cache = _CorrelationCache()
    cache.built_at = _time.time()

    if not db_path.exists():
        cache.source = "static_no_db"
        cache.groups = {s: g for s, g in _STATIC_GROUPS.items()}
        return cache

    try:
        # Lazy import numpy/pandas to keep this module import-cheap for
        # callers that don't actually trigger a fresh build.
        import numpy as np
        import pandas as pd

        con = sqlite3.connect(str(db_path), timeout=5.0)
        try:
            sql = """
                SELECT pair, timestamp, close
                FROM (
                    SELECT pair, timestamp, close,
                           ROW_NUMBER() OVER (PARTITION BY pair ORDER BY timestamp DESC) AS rn
                    FROM candles
                    WHERE timeframe = ?
                )
                WHERE rn <= ?
                ORDER BY pair, timestamp ASC
            """
            df = pd.read_sql_query(
                sql, con,
                params=[TIMEFRAME, WINDOW + 5],
            )
        finally:
            con.close()

        if df.empty:
            cache.source = "static_no_data"
            cache.groups = {s: g for s, g in _STATIC_GROUPS.items()}
            return cache

        # Pivot to wide format (timestamp index, pair columns), then drop
        # pairs with any NaN — fully aligned only.
        pivot = df.pivot(index="timestamp", columns="pair", values="close")
        # Normalize column names (USDT variants)
        pivot.columns = [_normalize(c) for c in pivot.columns]
        # Drop duplicate columns after normalize
        if pivot.columns.has_duplicates:
            pivot = pivot.loc[:, ~pivot.columns.duplicated()]
        # Drop pairs (columns) that have any NaN
        pivot = pivot.dropna(axis=1, how="any")

        cache.candle_count = len(pivot)
        cache.pairs = list(pivot.columns)

        if len(pivot) < MIN_CANDLES:
            cache.source = "static_insufficient_data"
            cache.groups = {s: _get_static_group(s) for s in cache.pairs}
            return cache

        # Log-returns + Pearson correlation
        rets = np.log(pivot / pivot.shift(1)).dropna()
        corr = rets.corr()

        # Populate matrix (symmetric)
        pairs = list(corr.columns)
        for i, a in enumerate(pairs):
            cache.matrix[(a, a)] = 1.0
            for b in pairs[i + 1:]:
                rho = float(corr.loc[a, b])
                if pd.isna(rho):
                    continue
                cache.matrix[(a, b)] = rho
                cache.matrix[(b, a)] = rho

        # Greedy cluster
        cache.groups = _greedy_cluster(pairs, cache.matrix, RHO_THRESHOLD)
        cache.source = "rolling"
        return cache

    except Exception:
        # Any failure → static fallback. We never want correlation_guard
        # to break the paper monitor because of a DB hiccup.
        cache.source = "static_error"
        cache.groups = {s: g for s, g in _STATIC_GROUPS.items()}
        return cache


def _greedy_cluster(
    pairs: list[str],
    matrix: dict[tuple[str, str], float],
    threshold: float,
) -> dict[str, str]:
    """
    Assign each pair to a group via greedy clustering on |ρ| ≥ threshold.

    Algorithm (single-linkage): a candidate joins the smallest existing
    group whose ANY member has |ρ| ≥ threshold with the candidate. This
    matches how risk diversifiers think: two assets are "in the same risk
    bucket" if a strong correlation path connects them.

    Strict transitivity (`all members correlated`) was tried first but
    produces too many singleton groups in crypto, where most pairs
    correlate at 0.6-0.9 but rarely all >= 0.7 simultaneously.

    Returns dict[symbol -> group_name].
    """
    groups: dict[str, list[str]] = {}
    next_id = 0

    for sym in sorted(pairs):
        target_group = None
        best_max_rho = 0.0
        for gname, members in groups.items():
            # Single-linkage: max |ρ| with any member of this group
            max_rho = max(abs(matrix.get((sym, m), 0.0)) for m in members)
            if max_rho >= threshold and max_rho > best_max_rho:
                target_group = gname
                best_max_rho = max_rho

        if target_group is not None:
            groups[target_group].append(sym)
        else:
            new_g = f"rolling_{next_id}"
            groups[new_g] = [sym]
            next_id += 1

    # Flatten
    out: dict[str, str] = {}
    for gname, members in groups.items():
        for m in members:
            out[m] = gname
    return out


def _ensure_cache() -> _CorrelationCache:
    """Return a fresh cache if expired or missing."""
    if not _CACHE.is_fresh():
        new_cache = _build_rolling_matrix()
        # Mutate in place (preserve singleton)
        _CACHE.matrix = new_cache.matrix
        _CACHE.groups = new_cache.groups
        _CACHE.built_at = new_cache.built_at
        _CACHE.source = new_cache.source
        _CACHE.candle_count = new_cache.candle_count
        _CACHE.pairs = new_cache.pairs
    return _CACHE


def refresh_cache(db_path: Path | None = None) -> dict[str, Any]:
    """Force-rebuild the cache. Returns a small status dict for logging."""
    path = db_path or CANDLES_DB
    new_cache = _build_rolling_matrix(path)
    _CACHE.matrix = new_cache.matrix
    _CACHE.groups = new_cache.groups
    _CACHE.built_at = new_cache.built_at
    _CACHE.source = new_cache.source
    _CACHE.candle_count = new_cache.candle_count
    _CACHE.pairs = new_cache.pairs
    return {
        "source": _CACHE.source,
        "pairs": len(_CACHE.pairs),
        "candles": _CACHE.candle_count,
        "groups": len(set(_CACHE.groups.values())),
        "built_at": _CACHE.built_at,
    }


# ─── Public API (backward-compatible) ──────────────────────────────────────

def get_group(symbol: str) -> str:
    """Get correlation group for a symbol. Returns 'independent' if not in map."""
    cache = _ensure_cache()
    return cache.groups.get(_normalize(symbol), "independent")


def are_correlated(symbol1: str, symbol2: str) -> tuple[bool, str]:
    """
    Check if two symbols are correlated based on the current rolling matrix.

    Returns (correlated: bool, reason: str).
    """
    if symbol1 == symbol2:
        return False, ""

    s1 = _normalize(symbol1)
    s2 = _normalize(symbol2)
    cache = _ensure_cache()

    g1 = cache.groups.get(s1, "independent")
    g2 = cache.groups.get(s2, "independent")

    # Same rolling-derived group → correlated
    if g1 == g2 and g1 != "independent":
        return True, f"both in group '{g1}' (rolling)"

    # Independent = never correlated
    if g1 == "independent" or g2 == "independent":
        return False, ""

    # Different rolling groups — but maybe their ρ is still high. Report ρ
    # if we have it (this is the whole point of rolling vs static).
    rho = cache.matrix.get((s1, s2))
    if rho is not None and abs(rho) >= RHO_THRESHOLD:
        # Shouldn't normally happen post-clustering, but guard for stale cache
        direction = "positive" if rho > 0 else "inverse"
        return True, (
            f"rolling ρ={rho:+.2f} ({direction}) ≥ {RHO_THRESHOLD:.2f}"
        )

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

    proposed_norm = _normalize(proposed_symbol)
    cache = _ensure_cache()

    if cache.groups.get(proposed_norm, "independent") == "independent":
        return True, "independent_symbol"

    correlated_symbols = []
    for pos in open_positions:
        pos_sym_raw = pos.get("symbol", "")
        if not pos_sym_raw:
            continue
        pos_norm = _normalize(pos_sym_raw)
        if pos_norm == proposed_norm:
            continue
        is_corr, reason = are_correlated(proposed_norm, pos_norm)
        if is_corr:
            correlated_symbols.append((pos_norm, reason))

    if len(correlated_symbols) >= max_correlated:
        names = ", ".join(s for s, _ in correlated_symbols)
        return False, (
            f"correlation_limit: {proposed_norm} correlated with "
            f"{len(correlated_symbols)} open positions: {names}"
        )

    return True, (
        f"only {len(correlated_symbols)} correlated positions open (max {max_correlated})"
    )


def get_correlation_summary(open_positions: list[dict[str, Any]]) -> dict[str, Any]:
    """Get summary of correlation distribution in current portfolio."""
    cache = _ensure_cache()

    groups: dict[str, list[str]] = {}
    for pos in open_positions:
        sym_raw = pos.get("symbol", "")
        if not sym_raw:
            continue
        sym = _normalize(sym_raw)
        g = cache.groups.get(sym, "independent")
        groups.setdefault(g, []).append(sym)

    # Find rolling pairs that ARE correlated but sit in different groups
    # (edge case where cache is stale mid-regime-shift).
    cross_linked: list[str] = []
    syms = [s for syms in groups.values() for s in syms]
    for i, a in enumerate(syms):
        for b in syms[i + 1:]:
            rho = cache.matrix.get((a, b))
            if rho is not None and abs(rho) >= RHO_THRESHOLD and groups.get(a) != groups.get(b):
                cross_linked.append(f"{a}~{b} ρ={rho:+.2f}")

    violations = []
    for g, symbols in groups.items():
        if len(symbols) > 2 and g != "independent":
            violations.append(f"{g}: {len(symbols)} positions ({', '.join(symbols)})")

    return {
        "total_positions": len(open_positions),
        "groups": groups,
        "group_counts": {g: len(s) for g, s in groups.items()},
        "violations": violations,
        "cross_linked": cross_linked,
        "cache_source": cache.source,
        "cache_pairs": len(cache.pairs),
        "cache_candles": cache.candle_count,
        "cache_built_at": cache.built_at,
        "cache_age_sec": _time.time() - cache.built_at if cache.built_at else None,
    }


def get_pair_correlation(symbol1: str, symbol2: str) -> float | None:
    """
    Return raw rolling ρ between two symbols, or None if not in the matrix.
    Useful for dashboards / debugging regime shifts.
    """
    cache = _ensure_cache()
    return cache.matrix.get((_normalize(symbol1), _normalize(symbol2)))


# ─── Smoke test ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("Rolling Correlation Guard — Smoke Test")
    print("=" * 60)

    status = refresh_cache()
    print(f"\nCache status: {status}")

    # Test 1: BTC and ETH — historically very correlated
    corr, reason = are_correlated("BTC/USDT", "ETH/USDT")
    rho = get_pair_correlation("BTC/USDT", "ETH/USDT")
    print(f"\nBTC vs ETH: correlated={corr}, ρ={rho:+.3f}")
    print(f"  reason: {reason}")

    # Test 2: BTC and TRX — historically weakly correlated
    corr, reason = are_correlated("BTC/USDT", "TRX/USDT")
    rho = get_pair_correlation("BTC/USDT", "TRX/USDT")
    print(f"\nBTC vs TRX: correlated={corr}, ρ={rho:+.3f}  (independent expected)")
    print(f"  reason: {reason}")

    # Test 3: ARB vs OP — L2s, usually high
    corr, reason = are_correlated("ARB/USDT", "OP/USDT")
    rho = get_pair_correlation("ARB/USDT", "OP/USDT")
    print(f"\nARB vs OP: correlated={corr}, ρ={rho:+.3f}")
    print(f"  reason: {reason}")

    # Test 4: AAVE vs LINK — different narratives, may split
    corr, reason = are_correlated("AAVE/USDT", "LINK/USDT")
    rho = get_pair_correlation("AAVE/USDT", "LINK/USDT")
    print(f"\nAAVE vs LINK: correlated={corr}, ρ={rho:+.3f}")
    print(f"  reason: {reason}")

    # Test 5: Portfolio check — typical open positions
    open_pos = [{"symbol": "BTC/USDT"}, {"symbol": "ETH/USDT"}]
    allowed, reason = check_correlation_limit("LTC/USDT", open_pos)
    print(f"\nOpen: BTC, ETH. Try to add LTC (also L1 majors per static):")
    print(f"  allowed={allowed}, reason={reason}")

    # Test 6: TRX should now be allowed (independent from BTC)
    allowed, reason = check_correlation_limit("TRX/USDT", [{"symbol": "BTC/USDT"}])
    print(f"\nOpen: BTC. Try to add TRX (independent from BTC on 1d):")
    print(f"  allowed={allowed}, reason={reason}")

    # Summary
    print(f"\n{'='*60}")
    print("Portfolio correlation summary:")
    summary = get_correlation_summary([
        {"symbol": "BTC/USDT"}, {"symbol": "ETH/USDT"}, {"symbol": "SOL/USDT"},
        {"symbol": "TRX/USDT"}, {"symbol": "ARB/USDT"}, {"symbol": "AAVE/USDT"},
    ])
    print(f"  Total: {summary['total_positions']}")
    print(f"  Groups: {summary['group_counts']}")
    print(f"  Violations: {summary['violations']}")
    print(f"  Cache: source={summary['cache_source']}, "
          f"pairs={summary['cache_pairs']}, "
          f"candles={summary['cache_candles']}, "
          f"age={summary['cache_age_sec']:.1f}s")