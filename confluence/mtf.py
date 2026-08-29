"""
Multi-timeframe analysis untuk RX-0 Unicorn.

Hierarchy:
  4H  → Bias (HTF trend direction, market structure)
  1H  → Confluence (4 indicators, validate against 4H bias)
  15m → Entry precision (lowest timeframe for trigger)

Entry rules:
  - 4H + 1H aligned (same direction) → 15m entry allowed
  - 4H + 1H aligned, strong 1H momentum → 1H entry allowed
  - 4H + 1H conflict → NO TRADE
  - 15m can NEVER override 4H bias (HTF dominance)

Output: bias (1/0/-1), strength (0-100), entry_timeframe (1h/15m)
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from data.storage.candle_db import CandleDB


def get_timeframe_df(pair: str, timeframe: str, limit: int = 200) -> pd.DataFrame | None:
    """Load candles for a pair/timeframe."""
    with CandleDB() as cdb:
        df = cdb.get_candles(pair=pair, timeframe=timeframe, limit=limit)
    return df


def compute_htf_bias(df: pd.DataFrame) -> dict[str, Any]:
    """
    Compute Higher TimeFrame bias from candle data.

    Uses:
    - EMA 50 vs EMA 200 (trend direction)
    - Market structure (higher highs / higher lows vs lower)
    - Recent price action (last 5 candles)

    Returns:
        bias: 1 (bullish), 0 (neutral), -1 (bearish)
        strength: 0-100 (how strong)
    """
    if df is None or len(df) < 60:
        return {"bias": 0, "strength": 0, "reason": "insufficient_data"}

    close = df["close"].astype(float)
    ema50 = close.ewm(span=50, adjust=False).mean()
    ema200 = close.ewm(span=200, adjust=False).mean()

    # EMA signal
    ema_diff_pct = ((ema50.iloc[-1] - ema200.iloc[-1]) / ema200.iloc[-1]) * 100
    ema_bullish = ema50.iloc[-1] > ema200.iloc[-1]

    # Market structure: last 20 candles
    recent = df.tail(20).reset_index(drop=True)
    highs = np.array(recent["high"].astype(float).values)
    lows = np.array(recent["low"].astype(float).values)
    # Simple: compare last 5 vs prior 5
    last5_high = float(highs[-5:].max())
    prev5_high = float(highs[-10:-5].max())
    last5_low = float(lows[-5:].min())
    prev5_low = float(lows[-10:-5].min())
    higher_highs = last5_high > prev5_high
    higher_lows = last5_low > prev5_low
    lower_highs = last5_high < prev5_high
    lower_lows = last5_low < prev5_low

    # Determine structure
    if higher_highs and higher_lows:
        structure = "uptrend"  # HH+HL
    elif lower_highs and lower_lows:
        structure = "downtrend"  # LH+LL
    else:
        structure = "choppy"

    # Combine signals
    bias = 0
    if ema_bullish and structure == "uptrend":
        bias = 1
    elif not ema_bullish and structure == "downtrend":
        bias = -1
    elif ema_bullish:
        bias = 1  # EMA up but structure choppy → mild bullish
    elif not ema_bullish:
        bias = -1
    # else 0 = neutral

    # Strength: combine EMA diff % + structure agreement
    structure_score = 1.0 if (bias == 1 and structure == "uptrend") or (bias == -1 and structure == "downtrend") else 0.5
    ema_strength = min(50, abs(ema_diff_pct) * 10)  # 0-50
    strength = min(100, int(structure_score * 50 + ema_strength))

    return {
        "bias": bias,  # -1, 0, +1
        "strength": strength,  # 0-100
        "ema_diff_pct": float(ema_diff_pct),
        "structure": structure,
        "reason": f"EMA50 {('>' if ema_bullish else '<')} EMA200, structure={structure}",
    }


def get_mtf_bias_and_confluence(pair: str) -> dict[str, Any]:
    """
    Get multi-timeframe bias + 1H confluence for a pair.

    Returns dict with:
        bias_4h: -1/0/+1
        strength_4h: 0-100
        bias_1h: -1/0/+1 (from 4 indicators confluence, long/short)
        confluence_score: 0-4
        confluence_grade: 'a_plus'/'valid'/'skip'
        aligned: bool (1H direction same as 4H bias)
        allow_1h_entry: bool
        allow_15m_entry: bool
    """
    # Get 4H bias
    df_4h = get_timeframe_df(pair, "4h", limit=200)
    if df_4h is None or len(df_4h) < 60:
        return {"aligned": False, "allow_1h_entry": False, "allow_15m_entry": False, "reason": "no_4h_data"}

    bias_4h = compute_htf_bias(df_4h)

    # Get 1H confluence
    df_1h = get_timeframe_df(pair, "1h", limit=200)
    if df_1h is None or len(df_1h) < 60:
        return {"aligned": False, "allow_1h_entry": False, "allow_15m_entry": False, "reason": "no_1h_data"}

    # Run confluence scorer
    try:
        from confluence import score_confluence, latest_confluence
        scored = score_confluence(df_1h)
        if scored is None or len(scored) == 0:
            return {"aligned": False, "allow_1h_entry": False, "allow_15m_entry": False, "reason": "no_confluence"}
        latest = scored.iloc[-1]
        conf_score = int(latest.get("confluence_score", 0) or 0)
        conf_dir = str(latest.get("confluence_direction", "long") or "long").lower()
        conf_grade = str(latest.get("confluence_grade", "skip") or "skip").lower()
        bias_1h = 1 if conf_dir == "long" else -1 if conf_dir == "short" else 0
    except Exception as e:
        return {"aligned": False, "allow_1h_entry": False, "allow_15m_entry": False, "reason": f"confluence_err: {e}"}

    # Alignment check
    aligned = (bias_4h["bias"] != 0 and bias_1h != 0 and bias_4h["bias"] == bias_1h)

    # Entry rules
    allow_1h_entry = False
    allow_15m_entry = False
    reason = ""

    if not aligned:
        reason = f"4H bias={bias_4h['bias']} != 1H bias={bias_1h}, NO TRADE"
    elif conf_grade == "skip" or conf_score < 2:
        reason = f"Confluence too weak ({conf_score}/4 grade={conf_grade})"
    elif bias_4h["strength"] < 30:
        # 4H bias weak → only 1H allowed, no 15m
        allow_1h_entry = (conf_score >= 3 and conf_grade in ("valid", "a_plus"))
        allow_15m_entry = False
        reason = f"4H weak bias, 1H entry only (score={conf_score})"
    else:
        # 4H strong → both timeframes OK
        allow_1h_entry = (conf_score >= 2 and conf_grade in ("valid", "a_plus"))
        allow_15m_entry = (conf_score >= 2 and conf_grade in ("valid", "a_plus"))
        reason = f"4H+1H aligned, score={conf_score} grade={conf_grade}"

    return {
        "pair": pair,
        "bias_4h": bias_4h["bias"],
        "strength_4h": bias_4h["strength"],
        "bias_1h": bias_1h,
        "confluence_score": conf_score,
        "confluence_grade": conf_grade,
        "aligned": aligned,
        "allow_1h_entry": allow_1h_entry,
        "allow_15m_entry": allow_15m_entry,
        "reason": reason,
        "ema_diff_4h": bias_4h.get("ema_diff_pct", 0),
        "structure_4h": bias_4h.get("structure", "unknown"),
    }


def get_mtf_entry_check(pair: str, proposed_direction: str, proposed_timeframe: str = "1h") -> dict[str, Any]:
    """
    Final check before opening a trade.

    Args:
        pair: e.g. "BTC/USDT"
        proposed_direction: "long" or "short"
        proposed_timeframe: "1h" or "15m"

    Returns:
        approved: bool
        reason: str
    """
    mtf = get_mtf_bias_and_confluence(pair)

    if not mtf.get("aligned"):
        return {
            "approved": False,
            "reason": f"4H bias disagrees with 1H: {mtf.get('reason')}",
            "mtf": mtf,
        }

    # Check direction matches HTF bias
    if mtf["bias_4h"] == 1 and proposed_direction != "long":
        return {"approved": False, "reason": "4H bias is BULLISH but direction is short", "mtf": mtf}
    if mtf["bias_4h"] == -1 and proposed_direction != "short":
        return {"approved": False, "reason": "4H bias is BEARISH but direction is long", "mtf": mtf}

    # Check timeframe allowed
    if proposed_timeframe == "15m" and not mtf.get("allow_15m_entry"):
        return {"approved": False, "reason": f"15m entry not allowed: {mtf.get('reason')}", "mtf": mtf}
    if proposed_timeframe == "1h" and not mtf.get("allow_1h_entry"):
        return {"approved": False, "reason": f"1H entry not allowed: {mtf.get('reason')}", "mtf": mtf}

    return {"approved": True, "reason": mtf.get("reason", "ok"), "mtf": mtf}


# Smoke test
if __name__ == "__main__":
    print("=" * 60)
    print("Multi-Timeframe — Smoke Test")
    print("=" * 60)

    for pair in ["BTC/USDT", "ETH/USDT", "SOL/USDT"]:
        print(f"\n--- {pair} ---")
        mtf = get_mtf_bias_and_confluence(pair)
        print(f"  4H bias: {mtf.get('bias_4h')} (strength={mtf.get('strength_4h')})")
        print(f"  4H EMA diff: {mtf.get('ema_diff_4h', 0):+.2f}%")
        print(f"  4H structure: {mtf.get('structure_4h')}")
        print(f"  1H bias: {mtf.get('bias_1h')} (conf={mtf.get('confluence_score')}/4 grade={mtf.get('confluence_grade')})")
        print(f"  Aligned: {mtf.get('aligned')}")
        print(f"  Allow 1H entry: {mtf.get('allow_1h_entry')}")
        print(f"  Allow 15m entry: {mtf.get('allow_15m_entry')}")
        print(f"  Reason: {mtf.get('reason')}")
