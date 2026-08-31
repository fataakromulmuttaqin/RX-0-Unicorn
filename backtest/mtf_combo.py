"""
MTF Combo Strategy — XAU/USD.

Idea: aggressive entry (15M) without raising risk by using higher-timeframe
filters (1D trend + 1H intraday) to BLOCK trades that go against the
multi-timeframe bias.

Pyramid:
    1D (Daily, 5Y via xaus.com):  confluence_min_score >= 1 → TREND BIAS
    1H (Hourly, 2Y via Yahoo):    confluence_min_score >= 2 → INTRADAY BIAS
    15M (15-min, 60D via Yahoo):   confluence on 15M bars → entry candidate

Trigger:
    Open 15M trade ONLY when 1D bias == 1H bias == 15M direction.

Risk per trade: 1.5% (same as tuned 1D baseline — NO risk increase).
TP:SL ratio: 2.0 (TP2 = 2R; SL = 1R).

Reuses existing engines:
    from backtest.engine     import run_backtest, simulate_trade
    from confluence.scorer   import score_confluence
    from data.fetchers       import XAUSFetcher, YahooFinanceFetcher

Honest caveat: the test window is the OVERLAP of the 3 timeframes
(= ~60 days for the 15M Yahoo limit). We can NOT claim a 5Y MTF result —
just the 60D window where 1D + 1H + 15M all coexist. The baselines we
compare against (1D 5Y, 1H 2Y, 15M 60D) are reported with their own
windows for context, NOT as apples-to-apples comparisons.
"""

from __future__ import annotations

import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

import numpy as np
import pandas as pd
from scipy import stats

# ─── Path setup so this script works as `python backtest/mtf_combo.py` ────────
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest.engine import simulate_trade
from backtest.metrics import PROFIT_FACTOR_CAP, calculate_metrics
from confluence.scorer import score_confluence
from data.fetchers import XAUSFetcher, YahooFinanceFetcher


# ─── Configuration ────────────────────────────────────────────────────────────
SYMBOL = "XAU/USD"

# 1D fetch params
TOTAL_BARS_1D = 1300   # 5Y daily ≈ 1258
BIAS_1D_MIN_SCORE = 1  # confluence_min_score for 1D trend bias

# 1H fetch params
TOTAL_BARS_1H = 11424  # 2Y hourly ≈ 11424 (Yahoo cap)
BIAS_1H_MIN_SCORE = 2  # confluence_min_score for 1H intraday bias

# 15M fetch params
TOTAL_BARS_15M = 1935  # 60D 15-min ≈ 1935

# Risk (sama dengan tuned 1D)
RISK_PER_TRADE = 0.015
INITIAL_CAPITAL = 10_000.0

# Statistical config
SEED = 42
BOOT_N = 5000
MC_ITER = 10_000

# Output paths
OUT_TRADES = "/tmp/xauusd_mtf_trades.json"
OUT_STATS = "/tmp/xauusd_mtf_full_stats.json"
OUT_REPORT = "/tmp/xauusd_mtf_report.md"


# ─── Helper utilities (mirroring analyze_xau.py) ──────────────────────────────
def ms_to_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat()


def equity_curve_from_pnls(pnls: np.ndarray, initial_capital: float) -> np.ndarray:
    return np.concatenate(([initial_capital], initial_capital + np.cumsum(pnls)))


def max_drawdown_pct(equity: np.ndarray) -> float:
    if len(equity) < 2:
        return 0.0
    running_peak = np.maximum.accumulate(equity)
    drawdown = (equity - running_peak) / running_peak
    return float(abs(drawdown.min()) * 100.0)


def sharpe_from_pnls(pnls: np.ndarray) -> float:
    if len(pnls) < 2:
        return 0.0
    std = float(pnls.std(ddof=1))
    if std <= 0 or not np.isfinite(std):
        return 0.0
    return float(pnls.mean() / std)


# ─── Data fetchers ────────────────────────────────────────────────────────────
def fetch_1d_xaus(total_bars: int) -> pd.DataFrame:
    print(f"[fetch] 1D xaus.com ({total_bars} bars)...")
    f = XAUSFetcher()
    try:
        df = f.fetch_ohlcv_paginated(SYMBOL, "1d", total_bars=total_bars)
    finally:
        f.close()
    if df.empty:
        raise RuntimeError("xaus.com returned empty 1D data")
    print(f"  -> {len(df)} bars: {ms_to_iso(int(df['timestamp'].iloc[0]))} -> "
          f"{ms_to_iso(int(df['timestamp'].iloc[-1]))}")
    return df


def fetch_1h_yahoo(total_bars: int) -> pd.DataFrame:
    print(f"[fetch] 1H Yahoo ({total_bars} bars)...")
    f = YahooFinanceFetcher()
    try:
        df = f.fetch_ohlcv_paginated(SYMBOL, "1h", total_bars=total_bars)
    finally:
        f.close()
    if df.empty:
        raise RuntimeError("Yahoo returned empty 1H data")
    print(f"  -> {len(df)} bars: {ms_to_iso(int(df['timestamp'].iloc[0]))} -> "
          f"{ms_to_iso(int(df['timestamp'].iloc[-1]))}")
    return df


def fetch_15m_yahoo(total_bars: int) -> pd.DataFrame:
    print(f"[fetch] 15M Yahoo ({total_bars} bars)...")
    f = YahooFinanceFetcher()
    try:
        df = f.fetch_ohlcv_paginated(SYMBOL, "15m", total_bars=total_bars)
    finally:
        f.close()
    if df.empty:
        raise RuntimeError("Yahoo returned empty 15M data")
    print(f"  -> {len(df)} bars: {ms_to_iso(int(df['timestamp'].iloc[0]))} -> "
          f"{ms_to_iso(int(df['timestamp'].iloc[-1]))}")
    return df


# ─── MTF Combo core ───────────────────────────────────────────────────────────
def score_with_min(df: pd.DataFrame, min_score: int, label: str) -> pd.DataFrame:
    """
    Run score_confluence + mask out signals below `min_score`.

    Returns the scored df with extra `bias` column ('long'/'short'/None)
    representing the HTF bias filter.

    NOTE: we deliberately do NOT require confluence_grade in (A+, valid)
    here. The grade "valid" demands score >= CONFLUENCE_MIN_VALID (3),
    which would make the 1D bias appear only 4 times in 5 years — that's
    NOT what the MTF spec asks for. The spec says "1D confluence score ≥
    1 → TREND BIAS", meaning at least 1 of 4 indicators aligned with the
    direction. Same logic for 1H: "score ≥ 2" means at least 2 of 4.
    """
    scored = score_confluence(df)
    direction = scored["confluence_direction"]
    score = scored["confluence_score"]
    # bias = direction if score >= min_score AND direction is long/short
    mask = (score >= min_score) & direction.isin(["long", "short"])
    scored["bias"] = np.where(mask, direction, None)
    # Numeric helper: long=1, short=-1, neutral=0
    bias_num = scored["bias"].map({"long": 1, "short": -1}).fillna(0).astype(int)
    scored["bias_num"] = bias_num
    n_long = (scored["bias"] == "long").sum()
    n_short = (scored["bias"] == "short").sum()
    n_neutral = scored["bias"].isna().sum()
    print(f"  -> [{label}] scored {len(scored)} bars (min_score={min_score}): "
          f"bias_long={n_long}, bias_short={n_short}, neutral={n_neutral} "
          f"({(n_long + n_short) / len(scored) * 100:.1f}% biased)")
    return scored


def run_mtf_backtest(
    *,
    risk_per_trade: float = RISK_PER_TRADE,
    initial_capital: float = INITIAL_CAPITAL,
    bias_1d_min_score: int = BIAS_1D_MIN_SCORE,
    bias_1h_min_score: int = BIAS_1H_MIN_SCORE,
) -> dict:
    """
    Run the full MTF Combo backtest.

    Returns a dict with:
        - trades: list of dicts (entry/exit/PnL/HTF bias context)
        - metrics: 6 metrics from calculate_metrics + extras
        - meta: config + counts
    """
    print("=" * 72)
    print("MTF COMBO STRATEGY — XAU/USD")
    print("=" * 72)

    # ── Step 1: fetch data ───────────────────────────────────────────────────
    df_1d = fetch_1d_xaus(TOTAL_BARS_1D)
    df_1h = fetch_1h_yahoo(TOTAL_BARS_1H)
    df_15m = fetch_15m_yahoo(TOTAL_BARS_15M)

    # ── Step 2: confluence + bias per TF ─────────────────────────────────────
    print("[score] computing confluence per timeframe...")
    scored_1d = score_with_min(df_1d, bias_1d_min_score, "1D")
    scored_1h = score_with_min(df_1h, bias_1h_min_score, "1H")
    scored_15m = score_confluence(df_15m)

    # ── Step 3: align timestamps so we can look up HTF bias at each 15M bar ──
    # For each 15M bar, find the latest 1D bar whose timestamp <= that 15M bar
    # (i.e., the 1D bias AT the time the 15M bar opens), and likewise for 1H.
    # Use merge_asof for speed + correctness.
    print("[align] joining 1D & 1H biases onto 15M bars (merge_asof)...")
    t15 = scored_15m[["timestamp"]].copy()
    t15 = t15.sort_values("timestamp").reset_index(drop=True)

    # 1D join (1D bars are daily UTC midnight; the latest 1D bar with ts <= 15M ts
    # is the bias IN EFFECT when the 15M bar opens)
    d1 = scored_1d[["timestamp", "bias", "bias_num", "confluence_score"]].rename(
        columns={"bias": "bias_1d", "bias_num": "bias_num_1d",
                 "confluence_score": "score_1d"}
    ).sort_values("timestamp").reset_index(drop=True)
    merged = pd.merge_asof(
        t15, d1, on="timestamp", direction="backward"
    )

    # 1H join
    d2 = scored_1h[["timestamp", "bias", "bias_num", "confluence_score"]].rename(
        columns={"bias": "bias_1h", "bias_num": "bias_num_1h",
                 "confluence_score": "score_1h"}
    ).sort_values("timestamp").reset_index(drop=True)
    merged = pd.merge_asof(
        merged, d2, on="timestamp", direction="backward"
    )

    # Re-attach scored_15m columns aligned by index
    merged = merged.join(scored_15m.reset_index(drop=True), rsuffix="_15")
    # merged now has both 15M scoring and 1D/1H bias context

    print(f"  -> joined table has {len(merged)} rows; "
          f"bias_1d nulls: {merged['bias_1d'].isna().sum()}; "
          f"bias_1h nulls: {merged['bias_1h'].isna().sum()}")

    # ── Step 4: walk the 15M bars and build filtered entries ─────────────────
    # Skip warmup so indicators stabilize
    skip_warmup = 60
    n = len(merged)
    out_trades: list[dict] = []
    skipped_no_15m_signal = 0
    skipped_htf_misalign = 0
    skipped_no_trade_obj = 0
    last_exit_idx = -1
    in_position = False
    current_exit_target_idx = -1

    for i in range(skip_warmup, n - 1):
        if in_position and i <= current_exit_target_idx:
            continue
        in_position = False
        current_exit_target_idx = -1

        row = merged.iloc[i]

        # 15M must be valid signal (direction + grade + score >= 2 + SL/TP not NaN)
        direction = row.get("confluence_direction")
        grade = row.get("confluence_grade")
        score_15 = int(row.get("confluence_score", 0) or 0)
        if direction not in ("long", "short"):
            skipped_no_15m_signal += 1
            continue
        if grade not in ("A+", "valid"):
            skipped_no_15m_signal += 1
            continue
        if score_15 < 2:  # match the 1H floor for entry signal
            skipped_no_15m_signal += 1
            continue
        try:
            sl_val = row["stop_loss"]
            tp1_val = row["take_profit_1"]
            if pd.isna(sl_val) or pd.isna(tp1_val):
                raise ValueError("risk levels NaN")
        except (KeyError, ValueError):
            skipped_no_15m_signal += 1
            continue

        # ── MTF FILTER: 1D bias AND 1H bias must == trade direction ─────────
        bias_1d = row.get("bias_1d")
        bias_1h = row.get("bias_1h")
        if pd.isna(bias_1d) or pd.isna(bias_1h):
            skipped_htf_misalign += 1
            continue
        if bias_1d != direction or bias_1h != direction:
            skipped_htf_misalign += 1
            continue

        # ── Run simulate_trade() on this 15M signal ──────────────────────────
        trade = simulate_trade(
            scored=merged,  # full scored df with all rows
            signal_idx=i,
            initial_capital=initial_capital,
            risk_per_trade=risk_per_trade,
        )
        if trade is None:
            skipped_no_trade_obj += 1
            continue

        # Hydrate for output
        d = trade.to_dict()
        out_trades.append({
            "trade_id": len(out_trades) + 1,
            "entry_time": int(d["entry_time"]),
            "entry_time_iso": ms_to_iso(int(d["entry_time"])),
            "exit_time": int(d["exit_time"]),
            "exit_time_iso": ms_to_iso(int(d["exit_time"])),
            "direction": d["direction"],
            "entry_price": round(float(d["entry_price"]), 4),
            "exit_price": round(float(d["exit_price"]), 4),
            "stop_loss": round(float(d["stop_loss"]), 4),
            "take_profit_1": round(float(d["take_profit_1"]), 4),
            "take_profit_2": round(float(d["take_profit_2"]), 4),
            "pnl": round(float(d["pnl"]), 4),
            "r_multiple": round(float(d["r_multiple"]), 6),
            "exit_reason": d["exit_reason"],
            "bars_held": int(d["bars_held"]),
            "size_units": round(float(d["size_units"]), 6),
            "confluence_score_15m": int(d["score"]),
            "confluence_grade_15m": d["grade"],
            "size_multiplier": round(float(d["size_multiplier"]), 4),
            "initial_capital_at_entry": round(float(d["initial_capital_at_entry"]), 2),
            "risk_per_trade_dollar": round(float(d["risk_per_trade_dollar"]), 4),
            # MTF context
            "bias_1d_at_entry": str(bias_1d),
            "bias_1h_at_entry": str(bias_1h),
            "score_1d_at_entry": int(row.get("score_1d") or 0) if not pd.isna(row.get("score_1d")) else 0,
            "score_1h_at_entry": int(row.get("score_1h") or 0) if not pd.isna(row.get("score_1h")) else 0,
        })

        in_position = True
        # Find exit idx to avoid overlapping positions
        match = merged.index[merged["timestamp"] == trade.exit_time]
        if len(match) > 0:
            current_exit_target_idx = int(match[0])
        else:
            current_exit_target_idx = i + int(trade.bars_held) + 1

    # ── Step 5: metrics on the filtered trade list ────────────────────────────
    print(f"[done] {len(out_trades)} MTF-filtered trades "
          f"(skipped: no_signal={skipped_no_15m_signal}, "
          f"htf_misalign={skipped_htf_misalign}, no_trade={skipped_no_trade_obj})")

    # Calculate_metrics expects dicts with "pnl" and "r_multiple"
    metrics = calculate_metrics(
        [{"pnl": t["pnl"], "r_multiple": t["r_multiple"]} for t in out_trades],
        initial_capital=initial_capital,
        risk_per_trade=risk_per_trade,
    )
    # Also: count filtered vs raw 15M (for the comparison)

    meta = {
        "symbol": SYMBOL,
        "timeframe": "15M (MTF-filtered)",
        "initial_capital": initial_capital,
        "risk_per_trade": risk_per_trade,
        "bias_1d_min_score": bias_1d_min_score,
        "bias_1h_min_score": bias_1h_min_score,
        "skipped_no_15m_signal": skipped_no_15m_signal,
        "skipped_htf_misalign": skipped_htf_misalign,
        "skipped_no_trade_obj": skipped_no_trade_obj,
        "bars_processed_1d": len(scored_1d),
        "bars_processed_1h": len(scored_1h),
        "bars_processed_15m": len(scored_15m),
        "n_trades_mtf": len(out_trades),
        "first_trade_iso": out_trades[0]["entry_time_iso"] if out_trades else None,
        "last_trade_iso": out_trades[-1]["entry_time_iso"] if out_trades else None,
    }

    return {
        "trades": out_trades,
        "metrics": metrics,
        "meta": meta,
        "scored_15m": scored_15m,
        "merged": merged,
    }


# ─── Pure 15M (no MTF filter) for comparison ──────────────────────────────────
def run_pure_15m_baseline(
    *,
    risk_per_trade: float = RISK_PER_TRADE,
    initial_capital: float = INITIAL_CAPITAL,
) -> dict:
    """Re-run run_backtest() on the same 15M Yahoo bars with no MTF filter.

    This lets us compare apples-to-apples: same data window, same risk,
    same engine — only difference is the MTF filter.
    """
    from backtest.engine import run_backtest
    print("[baseline] pure 15M (no MTF filter) on same Yahoo 60D bars...")
    f = YahooFinanceFetcher()
    try:
        df = f.fetch_ohlcv_paginated(SYMBOL, "15m", total_bars=TOTAL_BARS_15M)
    finally:
        f.close()
    if df.empty:
        raise RuntimeError("Yahoo 15M baseline fetch failed")

    result = run_backtest(
        df,
        symbol=SYMBOL,
        timeframe="15m",
        skip_warmup_bars=60,
        min_score=2,
        initial_capital=initial_capital,
        risk_per_trade=risk_per_trade,
    )
    trades = [{
        "pnl": t.pnl,
        "r_multiple": t.r_multiple,
        "exit_time": t.exit_time,
    } for t in result.trades]
    metrics = calculate_metrics(
        [{"pnl": t["pnl"], "r_multiple": t["r_multiple"]} for t in trades],
        initial_capital=initial_capital,
        risk_per_trade=risk_per_trade,
    )
    print(f"  -> pure 15M: {len(trades)} trades, "
          f"WR={metrics['win_rate']*100:.2f}%, PF={metrics['profit_factor']:.3f}, "
          f"PnL=${metrics['total_pnl']:.2f}, DD={metrics['max_drawdown_pct']:.2f}%")
    return {"trades": trades, "metrics": metrics, "meta": {"timeframe": "15m", "n": len(trades)}}


# ─── Statistical analysis (reuses analyze_xau.py recipe) ──────────────────────
def statistical_analysis(mtf_trades: list[dict], meta: dict) -> dict:
    pnls = np.array([t["pnl"] for t in mtf_trades], dtype=np.float64)
    initial = float(meta["initial_capital"])
    n = len(pnls)

    if n < 2:
        return {
            "n_trades": n,
            "verdict": "TOO FEW TRADES (< 2) — stats not meaningful",
            "ttest": None,
            "bootstrap_95_ci": None,
            "monte_carlo_equity": None,
            "normality": None,
        }

    # T-test
    t_stat, t_p_two = stats.ttest_1samp(pnls, 0.0)
    t_p_one = t_p_two / 2.0 if t_stat > 0 else 1.0 - t_p_two / 2.0
    ttest = {
        "t_stat": round(float(t_stat), 4),
        "pvalue_two_sided": round(float(t_p_two), 6),
        "pvalue_one_sided_greater": round(float(t_p_one), 6),
        "mean_pnl": round(float(pnls.mean()), 4),
        "verdict": ("edge likely real (mean PnL > 0)"
                    if (t_p_one < 0.05 and t_stat > 0)
                    else "edge NOT statistically significant"),
    }
    print(f"  -> T-test: t={t_stat:.3f}, p_one={t_p_one:.4g} ({ttest['verdict']})")

    # Bootstrap 95% CI
    rng = np.random.default_rng(SEED)
    boot_metrics = {
        "mean_pnl": [], "sharpe": [], "max_dd_pct": [],
        "win_rate": [], "profit_factor": [], "expectancy": [],
    }
    for _ in range(BOOT_N):
        idx = rng.integers(0, n, size=n)
        s = pnls[idx]
        boot_metrics["mean_pnl"].append(float(s.mean()))
        boot_metrics["sharpe"].append(sharpe_from_pnls(s))
        eq = equity_curve_from_pnls(s, initial)
        boot_metrics["max_dd_pct"].append(max_drawdown_pct(eq))
        wins = int((s > 0).sum())
        wr = wins / n
        boot_metrics["win_rate"].append(float(wr))
        gross_p = float(s[s > 0].sum()) if wins else 0.0
        gross_l = float(abs(s[s < 0].sum())) if (n - wins) else 0.0
        pf = (gross_p / gross_l) if gross_l > 0 else (
            PROFIT_FACTOR_CAP if gross_p > 0 else 0.0)
        boot_metrics["profit_factor"].append(min(float(pf), PROFIT_FACTOR_CAP))
        avg_win = float(s[s > 0].mean()) if wins else 0.0
        avg_loss = float(abs(s[s < 0].mean())) if (n - wins) else 0.0
        boot_metrics["expectancy"].append(wr * avg_win - (1 - wr) * avg_loss)

    ci = {}
    for k, arr in boot_metrics.items():
        arr = np.array(arr)
        lo, hi = np.percentile(arr, [2.5, 97.5])
        ci[k] = {
            "mean": round(float(arr.mean()), 4),
            "ci_low": round(float(lo), 4),
            "ci_high": round(float(hi), 4),
        }

    # Monte Carlo — terminal equity over MC_ITER shuffled paths
    final_eqs = np.empty(MC_ITER, dtype=np.float64)
    max_dds = np.empty(MC_ITER, dtype=np.float64)
    ruin_threshold = 0.5 * initial
    ruin_count = 0
    idx_full = np.arange(n)
    for i in range(MC_ITER):
        sample = pnls[rng.choice(idx_full, size=n, replace=True)]
        eq = equity_curve_from_pnls(sample, initial)
        final_eqs[i] = eq[-1]
        max_dds[i] = max_drawdown_pct(eq)
        if eq.min() < ruin_threshold:
            ruin_count += 1
    p5, p50, p95 = np.percentile(final_eqs, [5, 50, 95])
    mc_equity = {
        "iterations": MC_ITER,
        "initial_capital": initial,
        "final_equity_p5": round(float(p5), 2),
        "final_equity_p50": round(float(p50), 2),
        "final_equity_p95": round(float(p95), 2),
        "final_equity_mean": round(float(final_eqs.mean()), 2),
        "final_equity_std": round(float(final_eqs.std(ddof=1)), 2),
        "max_dd_p50_pct": round(float(np.percentile(max_dds, 50)), 2),
        "max_dd_p95_pct": round(float(np.percentile(max_dds, 95)), 2),
        "ruin_probability_pct": round(ruin_count / MC_ITER * 100, 2),
        "prob_profit_pct": round(float((final_eqs > initial).mean()) * 100, 2),
    }

    # Normality (quick)
    jb_stat, jb_p = stats.jarque_bera(pnls)
    normality = {
        "jarque_bera_stat": round(float(jb_stat), 4),
        "jarque_bera_pvalue": round(float(jb_p), 6),
        "verdict": ("non-normal (skew/kurtosis deviate)"
                    if jb_p < 0.05 else "consistent with normal"),
        "skewness": round(float(stats.skew(pnls)), 4),
        "kurtosis_excess": round(float(stats.kurtosis(pnls)), 4),
    }

    return {
        "n_trades": n,
        "ttest": ttest,
        "bootstrap_95_ci": ci,
        "monte_carlo_equity": mc_equity,
        "normality": normality,
    }


# ─── Trade frequency analysis ─────────────────────────────────────────────────
def trade_frequency(trades: list[dict]) -> dict:
    """Compute signals per day / per week over the MTF test window."""
    if not trades:
        return {"n_trades": 0, "per_day": 0.0, "per_week": 0.0,
                "window_days": 0.0, "first_trade_iso": None,
                "last_trade_iso": None}
    first_ts = min(t["entry_time"] for t in trades)
    last_ts = max(t["entry_time"] for t in trades)
    window_days = max(1, (last_ts - first_ts) / (1000 * 86400))
    n = len(trades)
    return {
        "n_trades": n,
        "window_days": round(window_days, 2),
        "per_day": round(n / window_days, 3),
        "per_week": round(n / window_days * 7, 2),
        "first_trade_iso": ms_to_iso(first_ts),
        "last_trade_iso": ms_to_iso(last_ts),
    }


# ─── Report builder ───────────────────────────────────────────────────────────
def build_report(mtf: dict, pure_15m: dict, stats_out: dict, freq: dict) -> str:
    m = mtf["metrics"]
    b = pure_15m["metrics"]
    s = stats_out
    n_mtf = mtf["meta"]["n_trades_mtf"]
    n_pure = pure_15m["meta"]["n"]

    lines: list[str] = []
    A = lines.append
    A("# MTF Combo Strategy — XAU/USD Report")
    A("")
    A(f"_Generated: {datetime.now(timezone.utc).isoformat()}_")
    A("")
    A("## 1. Strategy recap")
    A("")
    A("- **Goal**: aggressive entry (15M) without raising risk by gating with HTF bias")
    A(f"- **1D filter**: xaus.com 5Y ({mtf['meta']['bars_processed_1d']} bars), confluence score >= {BIAS_1D_MIN_SCORE}")
    A(f"- **1H filter**: Yahoo 2Y ({mtf['meta']['bars_processed_1h']} bars), confluence score >= {BIAS_1H_MIN_SCORE}")
    A(f"- **15M trigger**: Yahoo 60D ({mtf['meta']['bars_processed_15m']} bars), confluence score >= 2")
    A(f"- **Rule**: open 15M trade only when 1D bias == 1H bias == 15M direction")
    A(f"- **Risk**: {RISK_PER_TRADE*100:.1f}% per trade (unchanged from tuned 1D)")
    A(f"- **TP:SL**: TP2 = 2R (TP1 = 1R if runner doesn't reach TP2)")
    A("")
    A("## 2. Headline metrics comparison")
    A("")
    A("| Strategy | n_trades | WR (%) | PF | Sharpe | DD (%) | PnL ($) | Equity final ($) |")
    A("|----------|---------:|-------:|---:|-------:|-------:|--------:|------------------:|")

    # MTF Combo (computed)
    wr_mtf = m["win_rate"] * 100
    pf_mtf = m["profit_factor"]
    sh_mtf = m["sharpe_ratio"]
    dd_mtf = m["max_drawdown_pct"]
    pnl_mtf = m["total_pnl"]
    eq_mtf = m["equity_final"]
    A(f"| **MTF Combo (this run)** | **{n_mtf}** | **{wr_mtf:.2f}** | **{pf_mtf:.3f}** | "
      f"**{sh_mtf:.3f}** | **{dd_mtf:.2f}** | **{pnl_mtf:.2f}** | **{eq_mtf:.2f}** |")

    # Pure 15M (re-run on same Yahoo data, no filter)
    A(f"| Pure 15M (same data, no MTF) | {n_pure} | {b['win_rate']*100:.2f} | "
      f"{b['profit_factor']:.3f} | {b['sharpe_ratio']:.3f} | {b['max_drawdown_pct']:.2f} | "
      f"{b['total_pnl']:.2f} | {b['equity_final']:.2f} |")

    # Pre-existing baselines (different windows — flagged)
    A("| 1D alone (5Y, xaus) | 50 | 56.00 | 2.07 | 0.31 | 3.18 | +2133 | 12133 |")
    A("| 1H alone (2Y, Yahoo) | 234 | 51.28 | 1.13 | 0.05 | 17.30 | +1428 | 11428 |")
    A("| 15M alone (60D, Yahoo) | 62 | 48.39 | 0.85 | -0.075 | 11.83 | -578 | 9422 |")
    A("")
    A("> **Caveat (READ CAREFULLY):** The pre-existing 1D/1H/15M-alone rows are from DIFFERENT")
    A("> windows (5Y, 2Y, 60D respectively). Direct comparison to MTF Combo's ~60D window is NOT")
    A("> apples-to-apples — only the **Pure 15M (same data)** row is a fair comparator.")
    A("> The MTF Combo test window is bounded by the shortest TF (15M Yahoo ≈ 60 days).")
    A("")

    # Delta vs pure 15M
    delta_wr = wr_mtf - b["win_rate"] * 100
    delta_pf = pf_mtf - b["profit_factor"]
    delta_pnl = pnl_mtf - b["total_pnl"]
    delta_dd = dd_mtf - b["max_drawdown_pct"]
    A("### Delta vs Pure 15M (same data, no MTF filter)")
    A("")
    A(f"- WR: {delta_wr:+.2f} pp")
    A(f"- PF: {delta_pf:+.3f}")
    A(f"- PnL: {delta_pnl:+.2f} $")
    A(f"- DD: {delta_dd:+.2f} pp")
    A("")

    A("## 3. Trade frequency analysis")
    A("")
    if freq and freq.get("n_trades", 0) > 0:
        A(f"- Window: {freq['first_trade_iso']} -> {freq['last_trade_iso']} "
          f"({freq['window_days']:.1f} days)")
        A(f"- Trades: {freq['n_trades']}")
        A(f"- **Signals per day**: {freq['per_day']:.2f}")
        A(f"- **Signals per week**: {freq['per_week']:.1f}")
    else:
        A("- **No trades captured.** Filter is too restrictive for the 60D window — see verdict.")
    A("")

    A("## 4. Statistical tests")
    A("")
    A(f"- **n_trades**: {s['n_trades']}")
    if s.get("ttest"):
        tt = s["ttest"]
        A(f"- **T-test (mean PnL > 0)**: t = {tt['t_stat']:.3f}, "
          f"p (one-sided) = {tt['pvalue_one_sided_greater']:.4g}")
        A(f"  - Verdict: **{tt['verdict']}**")
    else:
        A("- T-test: not run (n < 2)")
    if s.get("normality"):
        nm = s["normality"]
        A(f"- **Normality (Jarque-Bera)**: stat = {nm['jarque_bera_stat']:.2f}, "
          f"p = {nm['jarque_bera_pvalue']:.4g} — {nm['verdict']}")
        A(f"- Skewness = {nm['skewness']:.2f}, Excess kurtosis = {nm['kurtosis_excess']:.2f}")
    if s.get("bootstrap_95_ci"):
        ci = s["bootstrap_95_ci"]
        A("- **Bootstrap 95% CI** (5000 resamples):")
        A("  | Metric | Mean | CI low | CI high |")
        A("  |--------|-----:|-------:|--------:|")
        for k in ("win_rate", "profit_factor", "sharpe", "mean_pnl", "expectancy", "max_dd_pct"):
            row = ci.get(k)
            if row:
                A(f"  | {k} | {row['mean']:.4f} | {row['ci_low']:.4f} | {row['ci_high']:.4f} |")
    if s.get("monte_carlo_equity"):
        mc = s["monte_carlo_equity"]
        A(f"- **Monte Carlo** ({mc['iterations']:,} shuffled paths of {s['n_trades']} trades):")
        A(f"  - Final equity: p5=${mc['final_equity_p5']:.0f}, "
          f"p50=${mc['final_equity_p50']:.0f}, p95=${mc['final_equity_p95']:.0f}")
        A(f"  - Mean final: ${mc['final_equity_mean']:.0f} (± ${mc['final_equity_std']:.0f})")
        A(f"  - MaxDD p50 = {mc['max_dd_p50_pct']:.2f}%, p95 = {mc['max_dd_p95_pct']:.2f}%")
        A(f"  - Ruin prob (<50% capital): {mc['ruin_probability_pct']:.2f}%")
        A(f"  - Prob(profit): {mc['prob_profit_pct']:.2f}%")
    else:
        A("- Monte Carlo: not run (n < 2)")
    A("")

    # ── Verdict ────────────────────────────────────────────────────────────────
    A("## 5. Verdict — does the MTF filter work?")
    A("")
    edge_real = (s.get("ttest") and
                 s["ttest"]["pvalue_one_sided_greater"] < 0.05 and
                 s["ttest"]["t_stat"] > 0)
    improves_wr = wr_mtf > (b["win_rate"] * 100)
    improves_pf = pf_mtf > b["profit_factor"]
    improves_pnl = pnl_mtf > b["total_pnl"]

    if improves_wr and improves_pf and edge_real:
        verdict = (
            "**PASS — MTF filter improves edge.** Both WR and PF exceed pure 15M, "
            "and the bootstrap t-test suggests the mean PnL is meaningfully > 0. "
            "Consider proceeding to forward-test / paper-trade with this filter."
        )
    elif improves_wr or improves_pf:
        verdict = (
            "**MIXED — MTF filter helps one metric but not the other.** "
            "Insufficient evidence to claim a robust edge over the 60-day window. "
            "Recommend longer backtest with archived intraday data or relaxing "
            "the 1H filter (currently confluence >= 2 is restrictive)."
        )
    else:
        verdict = (
            "**FAIL — MTF filter does NOT improve edge over pure 15M.** "
            "The 60-day test window shows no meaningful uplift. "
            "Possible causes: (a) 1H/1D bias lag (HTF filter arrives too late), "
            "(b) confluence score threshold too strict, "
            "(c) 60D window is too short for trend signals to dominate noise."
        )
    A(verdict)
    A("")

    A("### Honest caveats")
    A("")
    A(f"1. **Window**: Only {mtf['meta']['bars_processed_15m']/96:.0f} days of 15M data "
      f"available from Yahoo, so MTF Combo is tested over ~60 days — NOT 5 years.")
    A(f"2. **Sample size**: {n_mtf} trades is small. Bootstrap CIs may be wide. "
      f"Be conservative — repeat on longer 15M data when available.")
    A("3. **Bias overlap**: if 1D and 1H bias are themselves derived from the same "
      "4-indicator confluence scorer (just different aggregations), the 'independent "
      "confirmation' may be partially redundant. Worth investigating.")
    A("4. **No slippage/commission modeled** in `simulate_trade()`. Add if going live.")
    A("")

    A("## 6. Recommendation")
    A("")
    if improves_wr and improves_pf and edge_real:
        A("**LANJUT KE PAPER MONITOR** dengan MTF filter ini. Tuning ideas:")
        A("- Lower 1H `bias_1h_min_score` from 2 to 1 untuk lihat efeknya ke frequency vs WR.")
        A("- Add 4H or 30M as second HTF filter untuk diversifikasi bias source.")
        A("- Track signal-to-trade conversion ratio (candidates / filtered / taken).")
    elif improves_wr or improves_pf:
        A("**RELAKSASI FILTER** dulu — current `bias_1h_min_score=2` seems too strict. Coba:")
        A("- `bias_1h_min_score=1` untuk lihat uplift di trade frequency vs WR.")
        A("- Tambah skip rule: skip if 1D and 1H disagree (currently blocks, may be over-blocking).")
        A("- Run ulang di 1Y 15M data (kalau Yahoo sempat expose lebih lama).")
    else:
        A("**PAUSE / ADJUST** — MTF filter tidak menambah edge dalam 60D window ini. Opsi:")
        A("- Stop pakai MTF filter di timeframe kecil. Tetap di 1D atau 1H murni.")
        A("- Atau balik ke baseline 1D (5Y, WR 56%, PF 2.07, DD 3.18% — paling aman).")
        A("- Sebelum adjust strategy, gather > 1Y data intraday untuk test ulang.")
    A("")
    A("---")
    A("")
    A("_Report generated by `backtest/mtf_combo.py`._")
    return "\n".join(lines) + "\n"


# ─── Main ─────────────────────────────────────────────────────────────────────
def main() -> int:
    mtf_result = run_mtf_backtest()
    pure_15m = run_pure_15m_baseline()

    # Stats on the MTF trades
    stats_out = statistical_analysis(mtf_result["trades"], mtf_result["meta"])
    freq = trade_frequency(mtf_result["trades"])

    # ── Persist JSON outputs ──────────────────────────────────────────────────
    payload_trades = {
        "config": mtf_result["meta"],
        "trades": mtf_result["trades"],
    }
    with open(OUT_TRADES, "w") as f:
        json.dump(payload_trades, f, indent=2)
    print(f"[write] {OUT_TRADES}: {len(mtf_result['trades'])} trades")

    # full stats payload — baseline (computed metrics) + stats + comparisons
    m_mtf = mtf_result["metrics"]
    m_pure = pure_15m["metrics"]
    full_stats = {
        "config": mtf_result["meta"],
        "baseline": {  # MTF Combo computed metrics (the headline result)
            "n_trades": int(m_mtf["total_trades"]),
            "wr_pct": round(m_mtf["win_rate"] * 100, 4),
            "pf": round(m_mtf["profit_factor"], 4),
            "sharpe": round(m_mtf["sharpe_ratio"], 4),
            "dd_pct": round(m_mtf["max_drawdown_pct"], 4),
            "pnl_usd": round(m_mtf["total_pnl"], 4),
            "equity_final_usd": round(m_mtf["equity_final"], 4),
            "avg_r_multiple": round(m_mtf["avg_r_multiple"], 4),
            "expectancy": round(m_mtf["expectancy"], 4),
            "avg_win": round(m_mtf["avg_win"], 4),
            "avg_loss": round(m_mtf["avg_loss"], 4),
            "wins": int(m_mtf["wins"]),
            "losses": int(m_mtf["losses"]),
        },
        "comparison": {
            "pure_15m_same_data": {
                "n_trades": int(m_pure["total_trades"]),
                "wr_pct": round(m_pure["win_rate"] * 100, 4),
                "pf": round(m_pure["profit_factor"], 4),
                "sharpe": round(m_pure["sharpe_ratio"], 4),
                "dd_pct": round(m_pure["max_drawdown_pct"], 4),
                "pnl_usd": round(m_pure["total_pnl"], 4),
                "equity_final_usd": round(m_pure["equity_final"], 4),
            },
            "delta_vs_pure_15m": {
                "wr_pp": round((m_mtf["win_rate"] - m_pure["win_rate"]) * 100, 4),
                "pf": round(m_mtf["profit_factor"] - m_pure["profit_factor"], 4),
                "sharpe": round(m_mtf["sharpe_ratio"] - m_pure["sharpe_ratio"], 4),
                "dd_pp": round(m_mtf["max_drawdown_pct"] - m_pure["max_drawdown_pct"], 4),
                "pnl_usd": round(m_mtf["total_pnl"] - m_pure["total_pnl"], 4),
            },
            "pre_existing_baselines_different_windows": {
                "1d_5y": {"n_trades": 50, "wr_pct": 56.0, "pf": 2.07,
                           "sharpe": 0.31, "dd_pct": 3.18, "pnl_usd": 2133.0,
                           "window": "5Y (2021-2026)"},
                "1h_2y": {"n_trades": 234, "wr_pct": 51.28, "pf": 1.122,
                           "sharpe": 0.049, "dd_pct": 17.30, "pnl_usd": 1369.28,
                           "window": "2Y (2024-09 to 2026-08)"},
                "15m_60d": {"n_trades": 62, "wr_pct": 48.39, "pf": 0.85,
                             "sharpe": -0.075, "dd_pct": 11.83, "pnl_usd": -578.0,
                             "window": "60D (2026-07 to 2026-08)"},
                "note": "These windows differ from MTF Combo's ~60D. Pure 15M "
                        "same_data row is the only apples-to-apples comparator.",
            },
        },
        "statistical_tests": stats_out,
        "trade_frequency": freq,
        "filter_attribution": {
            "skipped_no_15m_signal": int(mtf_result["meta"]["skipped_no_15m_signal"]),
            "skipped_htf_misalign": int(mtf_result["meta"]["skipped_htf_misalign"]),
            "skipped_no_trade_obj": int(mtf_result["meta"]["skipped_no_trade_obj"]),
        },
    }
    with open(OUT_STATS, "w") as f:
        json.dump(full_stats, f, indent=2)
    print(f"[write] {OUT_STATS}")

    # ── Markdown report ──────────────────────────────────────────────────────
    report = build_report(mtf_result, pure_15m, stats_out, freq)
    with open(OUT_REPORT, "w") as f:
        f.write(report)
    print(f"[write] {OUT_REPORT} ({len(report.splitlines())} lines)")

    return 0


if __name__ == "__main__":
    sys.exit(main())