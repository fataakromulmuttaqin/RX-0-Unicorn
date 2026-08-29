"""
Trade signal generator: run confluence engine across all pairs, simulate trades,
extract per-trade P/L for advanced backtest methods.

With $100 starting capital, position sizing is micro:
- Risk per trade: 1-2% = $1.00 - $2.00
- Size based on stop distance
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
import pandas as pd
from loguru import logger

from data.storage.candle_db import CandleDB
from confluence import score_confluence


def _load_pairs() -> list[str]:
    """Load all pair symbols from watchlist.json (flatten across tiers)."""
    start = Path(__file__).resolve()
    for parent in [start, *start.parents[:4]]:
        candidate = parent / "data" / "pairs" / "watchlist.json"
        if candidate.exists():
            with open(candidate) as f:
                wl = json.load(f)
            pairs: list[str] = []
            for tier_pairs in wl.values():
                if isinstance(tier_pairs, list):
                    pairs.extend(tier_pairs)
            return pairs
    raise FileNotFoundError(f"watchlist.json not found near {start}")


def generate_trades_from_confluence(
    initial_capital: float = 100.0,
    risk_per_trade: float = 0.02,
    max_bars_hold: int = 50,
    pairs: list[str] | None = None,
    timeframe: str = "1h",
    min_score: int = 3,
    slippage_pct: float = 0.05,
    commission_pct: float = 0.10,
) -> list[dict[str, Any]]:
    """
    Walk all available candles for all (or given) pairs, run confluence scorer,
    simulate trades with $1-2 risk per trade, return per-trade P/L list.

    For micro account ($100), position size = risk / stop_distance (in units).

    Realistic cost model:
    - slippage_pct: 0.05% per trade (typical for liquid crypto)
    - commission_pct: 0.10% per trade (Binance spot default)
    """
    if pairs is None:
        pairs = _load_pairs()

    trades: list[dict[str, Any]] = []
    capital = initial_capital

    with CandleDB() as cdb:
        for symbol in pairs:
            try:
                df = cdb.get_candles(pair=symbol, timeframe=timeframe, limit=200)
            except Exception as e:
                logger.debug(f"{symbol}: skip ({e})")
                continue
            if df is None or len(df) < 60:
                continue

            try:
                scored = score_confluence(df)
            except Exception as e:
                logger.debug(f"score {symbol}: {e}")
                continue

            if scored is None or scored.empty:
                continue

            # Pre-load MTF data for this pair (load 4H + 1D once)
            use_mtf = timeframe == "1h"  # only apply MTF when scanning 1H
            bias_4h_const = 0
            bias_1d_const = 0
            if use_mtf:
                try:
                    from confluence.mtf import compute_htf_bias
                    df_4h = cdb.get_candles(pair=symbol, timeframe="4h", limit=200)
                    if df_4h is not None and len(df_4h) >= 60:
                        b4 = compute_htf_bias(df_4h, timeframe="4h")
                        bias_4h_const = b4["bias"]
                    df_1d = cdb.get_candles(pair=symbol, timeframe="1d", limit=200)
                    if df_1d is not None and len(df_1d) >= 60:
                        b1d = compute_htf_bias(df_1d, timeframe="1d")
                        bias_1d_const = b1d["bias"]
                except Exception as e:
                    logger.debug(f"MTF load {symbol}: {e}")

            # Walk each bar looking for signals
            for i in range(len(scored) - 1):
                row = scored.iloc[i]
                score = int(row.get("confluence_score", 0) or 0)
                if score < min_score:
                    continue
                # Get direction from score direction OR direction column
                direction = str(row.get("confluence_direction", "long") or "long").lower()
                if direction not in ("long", "short"):
                    # Fallback: count individual signals
                    long_count = sum(1 for k in ["luminance_signal", "rsi_regime_signal", "structure_signal", "wavetrend_signal"]
                                     if int(row.get(k, 0) or 0) == 1)
                    short_count = sum(1 for k in ["luminance_signal", "rsi_regime_signal", "structure_signal", "wavetrend_signal"]
                                      if int(row.get(k, 0) or 0) == -1)
                    direction = "long" if long_count >= short_count else "short"
                if direction not in ("long", "short"):
                    continue

                # MTF filter: skip if 4H/1D bias disagrees with 1H signal direction
                if use_mtf:
                    # 4H bias must agree OR be neutral
                    if bias_4h_const != 0 and (
                        (bias_4h_const == 1 and direction != "long") or
                        (bias_4h_const == -1 and direction != "short")
                    ):
                        continue  # 4H disagrees
                    # 1D bias must agree OR be neutral (soft check)
                    if bias_1d_const != 0 and (
                        (bias_1d_const == 1 and direction != "long") or
                        (bias_1d_const == -1 and direction != "short")
                    ):
                        continue  # 1D disagrees

                # Get entry on next bar open
                if i + 1 >= len(scored):
                    continue
                entry_bar = scored.iloc[i + 1]
                entry_price = float(entry_bar["open"])
                if entry_price <= 0:
                    continue

                sl = float(row.get("stop_loss", 0) or 0)
                tp1 = float(row.get("take_profit_1", 0) or 0)
                tp2 = float(row.get("take_profit_2", 0) or 0)
                size_mult = float(row.get("size_multiplier", 1.0) or 1.0)

                if sl <= 0 or tp1 <= 0 or tp2 <= 0:
                    continue
                if direction == "long" and not (sl < entry_price < tp1 < tp2):
                    continue
                if direction == "short" and not (sl > entry_price > tp1 > tp2):
                    continue

                # Position size based on stop distance
                risk_dollar = capital * risk_per_trade * size_mult
                if direction == "long":
                    stop_distance = entry_price - sl
                else:
                    stop_distance = sl - entry_price
                if stop_distance <= 0:
                    continue
                units = risk_dollar / stop_distance
                if units <= 0:
                    continue

                # Walk forward to find exit
                exit_price = entry_price
                exit_reason = "time_stop"
                hold_bars = 0
                for j in range(i + 1, min(i + 1 + max_bars_hold, len(scored))):
                    bar = scored.iloc[j]
                    hold_bars += 1
                    high = float(bar["high"])
                    low = float(bar["low"])
                    if direction == "long":
                        hit_sl = low <= sl
                        hit_tp2 = high >= tp2
                        hit_tp1 = high >= tp1
                    else:
                        hit_sl = high >= sl
                        hit_tp2 = low <= tp2
                        hit_tp1 = low <= tp1

                    if hit_sl:
                        exit_price = sl
                        exit_reason = "sl"
                        break
                    if hit_tp2:
                        exit_price = tp2
                        exit_reason = "tp2"
                        break
                    if hit_tp1:
                        exit_price = tp1
                        exit_reason = "tp1"
                        break

                # Calculate PnL (with slippage + commission)
                # Slippage: entry filled slightly worse, exit slightly better/worse
                # Commission: applied on both entry and exit (round trip)
                if direction == "long":
                    # Entry: ask higher (slippage), Exit: bid lower (slippage)
                    entry_filled = entry_price * (1 + slippage_pct / 100)
                    exit_filled = exit_price * (1 - slippage_pct / 100)
                    pnl_per_unit = exit_filled - entry_filled
                else:
                    # Short: entry at bid lower, exit at ask higher
                    entry_filled = entry_price * (1 - slippage_pct / 100)
                    exit_filled = exit_price * (1 + slippage_pct / 100)
                    pnl_per_unit = entry_filled - exit_filled
                # Commission: round-trip (entry + exit)
                # Cost = units * (entry_price + exit_price) * commission_pct / 100
                commission = units * (entry_filled + exit_filled) * (commission_pct / 100)
                pnl = units * pnl_per_unit - commission

                trades.append({
                    "symbol": symbol,
                    "direction": direction,
                    "entry_bar_idx": i + 1,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "exit_reason": exit_reason,
                    "hold_bars": hold_bars,
                    "pnl": pnl,
                    "slippage_cost": units * (entry_price * slippage_pct / 100 * 2),  # both sides
                    "commission_cost": commission,
                    "capital_after": capital + pnl,
                })
                capital += pnl

    return trades


def trades_to_pnl_array(trades: list[dict]) -> np.ndarray:
    """Extract just the P/L values as numpy array."""
    return np.array([t["pnl"] for t in trades], dtype=np.float64)
