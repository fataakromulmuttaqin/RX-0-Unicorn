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


def compute_htf_bias(df: pd.DataFrame, timeframe: str = "auto") -> dict[str, Any]:
    """
    Compute Higher TimeFrame bias from candle data.

    Uses:
    - EMA 50 vs EMA 200 (trend direction)
    - Market structure (higher highs / higher lows vs lower)
    - Recent price action (last 5 candles)

    Args:
        df: candle dataframe
        timeframe: '4h' or '1d' (auto = detect from df length)

    Returns:
        bias: 1 (bullish), 0 (neutral), -1 (bearish)
        strength: 0-100 (how strong)
    """
    if df is None or len(df) < 60:
        return {"bias": 0, "strength": 0, "reason": "insufficient_data"}

    close = df["close"].astype(float)
    # For higher TFs (4H, 1D), use longer EMA spans
    if timeframe == "1d" or (timeframe == "auto" and len(df) >= 200):
        ema_fast, ema_slow = 20, 50
    else:
        ema_fast, ema_slow = 50, 200

    ema_fast_val = close.ewm(span=ema_fast, adjust=False).mean()
    ema_slow_val = close.ewm(span=ema_slow, adjust=False).mean()

    # EMA signal
    ema_diff_pct = ((ema_fast_val.iloc[-1] - ema_slow_val.iloc[-1]) / ema_slow_val.iloc[-1]) * 100
    ema_bullish = ema_fast_val.iloc[-1] > ema_slow_val.iloc[-1]

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
        "timeframe": timeframe,
        "reason": f"EMA{ema_fast}({('>' if ema_bullish else '<')}EMA{ema_slow}), structure={structure}",
    }


def compute_ltf_entry_signal(df_15m: pd.DataFrame) -> dict[str, Any]:
    """
    Compute Lower TimeFrame (15m) entry signal — light version of confluence.

    Used for entry precision when 4H+1H aligned.
    Quick checks:
    - EMA 9 vs EMA 21 cross
    - RSI(7) extreme zones (>70 / <30)
    - Volume spike (1.5x avg of last 20)

    Returns:
        has_signal: bool
        direction: 'long' / 'short' / None
        strength: 0-100
        entry_type: 'ema_cross' / 'rsi_extreme' / 'volume_spike' / None
    """
    if df_15m is None or len(df_15m) < 30:
        return {"has_signal": False, "direction": None, "strength": 0, "entry_type": None, "reason": "insufficient_data"}

    close = df_15m["close"].astype(float)
    volume = df_15m["volume"].astype(float) if "volume" in df_15m.columns else None

    ema9 = close.ewm(span=9, adjust=False).mean()
    ema21 = close.ewm(span=21, adjust=False).mean()

    # EMA cross (last 2 candles)
    cross_up = ema9.iloc[-1] > ema21.iloc[-1] and ema9.iloc[-2] <= ema21.iloc[-2]
    cross_down = ema9.iloc[-1] < ema21.iloc[-1] and ema9.iloc[-2] >= ema21.iloc[-2]

    # RSI(7)
    delta_series = close.diff()
    gain = delta_series.clip(lower=0).rolling(7).mean()
    loss = (-delta_series.clip(upper=0)).rolling(7).mean()
    # Avoid division by zero
    loss_safe = loss.replace(0, 1e-10)
    rs = gain / loss_safe
    rsi_series = 100 - (100 / (1 + rs))
    rsi_val = float(rsi_series.iloc[-1]) if not rsi_series.empty else 50.0

    # Volume spike
    vol_spike = False
    if volume is not None and len(volume) >= 20:
        avg_vol = float(volume.tail(20).mean())
        cur_vol = float(volume.iloc[-1])
        vol_spike = avg_vol > 0 and cur_vol > avg_vol * 1.5

    # Decide
    has_signal = False
    direction = None
    strength = 0
    entry_type = None
    reason = ""

    if cross_up and rsi_val < 70 and vol_spike:
        has_signal = True
        direction = "long"
        strength = 80
        entry_type = "ema_cross_volume"
        reason = f"EMA9 crossed up EMA21, RSI={rsi_val:.0f}, volume spike"
    elif cross_down and rsi_val > 30 and vol_spike:
        has_signal = True
        direction = "short"
        strength = 80
        entry_type = "ema_cross_volume"
        reason = f"EMA9 crossed down EMA21, RSI={rsi_val:.0f}, volume spike"
    elif cross_up:
        has_signal = True
        direction = "long"
        strength = 50
        entry_type = "ema_cross"
        reason = f"EMA9 crossed up EMA21, RSI={rsi_val:.0f} (no vol spike)"
    elif cross_down:
        has_signal = True
        direction = "short"
        strength = 50
        entry_type = "ema_cross"
        reason = f"EMA9 crossed down EMA21, RSI={rsi_val:.0f} (no vol spike)"
    elif rsi_val < 25:
        has_signal = True
        direction = "long"
        strength = 40
        entry_type = "rsi_extreme"
        reason = f"RSI extreme low {rsi_val:.0f}"
    elif rsi_val > 75:
        has_signal = True
        direction = "short"
        strength = 40
        entry_type = "rsi_extreme"
        reason = f"RSI extreme high {rsi_val:.0f}"
    else:
        reason = f"No trigger (RSI={rsi_val:.0f}, no EMA cross)"

    return {
        "has_signal": has_signal,
        "direction": direction,
        "strength": strength,
        "entry_type": entry_type,
        "reason": reason,
        "rsi": rsi_val,
    }


def get_mtf_bias_and_confluence(pair: str) -> dict[str, Any]:
    """
    Get multi-timeframe bias + 1H confluence for a pair.

    Returns dict with:
        bias_1d: -1/0/+1 (long-term trend)
        bias_4h: -1/0/+1
        strength_4h: 0-100
        bias_1h: -1/0/+1 (from 4 indicators confluence, long/short)
        confluence_score: 0-4
        confluence_grade: 'a_plus'/'valid'/'skip'
        aligned: bool (1H direction same as 4H bias, AND 4H same as 1D)
        allow_1h_entry: bool
        allow_15m_entry: bool
    """
    # Get 1D bias (long-term view)
    df_1d = get_timeframe_df(pair, "1d", limit=200)
    if df_1d is None or len(df_1d) < 60:
        bias_1d_data = {"bias": 0, "strength": 0, "reason": "no_1d_data"}
    else:
        bias_1d_data = compute_htf_bias(df_1d, timeframe="1d")

    # Get 4H bias
    df_4h = get_timeframe_df(pair, "4h", limit=200)
    if df_4h is None or len(df_4h) < 60:
        return {
            "aligned": False, "allow_1h_entry": False, "allow_15m_entry": False,
            "reason": "no_4h_data",
            "bias_1d": bias_1d_data.get("bias", 0),
        }

    bias_4h = compute_htf_bias(df_4h, timeframe="4h")

    # Get 1H confluence
    df_1h = get_timeframe_df(pair, "1h", limit=200)
    if df_1h is None or len(df_1h) < 60:
        return {
            "aligned": False, "allow_1h_entry": False, "allow_15m_entry": False,
            "reason": "no_1h_data",
            "bias_1d": bias_1d_data.get("bias", 0),
            "bias_4h": bias_4h["bias"],
        }

    # Run confluence scorer
    try:
        from confluence import score_confluence, latest_confluence
        scored = score_confluence(df_1h)
        if scored is None or len(scored) == 0:
            return {
                "aligned": False, "allow_1h_entry": False, "allow_15m_entry": False,
                "reason": "no_confluence",
                "bias_1d": bias_1d_data.get("bias", 0),
                "bias_4h": bias_4h["bias"],
            }
        latest = scored.iloc[-1]
        conf_score = int(latest.get("confluence_score", 0) or 0)
        conf_dir = str(latest.get("confluence_direction", "long") or "long").lower()
        conf_grade = str(latest.get("confluence_grade", "skip") or "skip").lower()
        bias_1h = 1 if conf_dir == "long" else -1 if conf_dir == "short" else 0
    except Exception as e:
        return {
            "aligned": False, "allow_1h_entry": False, "allow_15m_entry": False,
            "reason": f"confluence_err: {e}",
            "bias_1d": bias_1d_data.get("bias", 0),
            "bias_4h": bias_4h["bias"],
        }

    # 3-way alignment: 1D + 4H + 1H must agree
    aligned_1d_4h = (bias_1d_data["bias"] == 0 or bias_4h["bias"] == 0 or
                    bias_1d_data["bias"] == bias_4h["bias"])
    aligned_4h_1h = (bias_4h["bias"] == 0 or bias_1h == 0 or
                    bias_4h["bias"] == bias_1h)
    # Strong alignment: all 3 same direction
    strongly_aligned = (bias_1d_data["bias"] != 0 and bias_4h["bias"] != 0 and bias_1h != 0 and
                       bias_1d_data["bias"] == bias_4h["bias"] == bias_1h)
    # Soft alignment: 4H + 1H agree, 1D either agrees or neutral
    aligned = aligned_1d_4h and aligned_4h_1h

    # Entry rules
    allow_1h_entry = False
    allow_15m_entry = False
    reason = ""

    if not aligned:
        reason = f"MTF conflict: 1D={bias_1d_data['bias']} 4H={bias_4h['bias']} 1H={bias_1h}, NO TRADE"
    elif conf_grade == "skip" or conf_score < 2:
        reason = f"Confluence too weak ({conf_score}/4 grade={conf_grade})"
    elif bias_4h["strength"] < 30:
        # 4H weak bias → only 1H allowed, no 15m
        allow_1h_entry = (conf_score >= 3 and conf_grade in ("valid", "a_plus"))
        allow_15m_entry = False
        reason = f"4H weak bias, 1H entry only (score={conf_score})"
    else:
        # 4H strong → both timeframes OK
        allow_1h_entry = (conf_score >= 2 and conf_grade in ("valid", "a_plus"))
        allow_15m_entry = (conf_score >= 2 and conf_grade in ("valid", "a_plus"))
        strongly_tag = " (STRONG 1D+4H+1H aligned)" if strongly_aligned else ""
        reason = f"4H+1H aligned{strongly_tag}, score={conf_score} grade={conf_grade}"

    return {
        "pair": pair,
        "bias_1d": bias_1d_data["bias"],
        "bias_4h": bias_4h["bias"],
        "strength_4h": bias_4h["strength"],
        "bias_1h": bias_1h,
        "confluence_score": conf_score,
        "confluence_grade": conf_grade,
        "aligned": aligned,
        "strongly_aligned": strongly_aligned,
        "allow_1h_entry": allow_1h_entry,
        "allow_15m_entry": allow_15m_entry,
        "reason": reason,
        "ema_diff_1d": bias_1d_data.get("ema_diff_pct", 0),
        "ema_diff_4h": bias_4h.get("ema_diff_pct", 0),
        "structure_4h": bias_4h.get("structure", "unknown"),
    }


def get_15m_entry_signal(pair: str, direction: str) -> dict[str, Any]:
    """
    Get 15m entry signal for a pair (used after 4H+1H aligned).
    Args:
        pair: e.g. "BTC/USDT"
        direction: expected direction from 4H+1H ('long' or 'short')
    Returns dict with has_signal, entry_type, strength.
    """
    df = get_timeframe_df(pair, "15m", limit=200)
    if df is None or len(df) < 30:
        return {"has_signal": False, "reason": "no_15m_data"}
    sig = compute_ltf_entry_signal(df)
    if sig["has_signal"] and sig["direction"] == direction:
        return sig
    elif sig["has_signal"]:
        return {
            "has_signal": False,
            "reason": f"15m signal is {sig['direction']}, expected {direction} from 4H+1H",
        }
    else:
        return sig


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
