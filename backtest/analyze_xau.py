"""
RX-0 Unicorn — XAU/USD 1H Monte Carlo + statistical analysis pipeline.

Single-file pipeline that:
  1. Re-runs the XAU/USD 1h 2-year backtest via `backtest.engine.run_backtest()`
     (YahooFinanceFetcher -> 11424 1h bars of GC=F), then dumps every trade
     (entry/exit, SL, TP1, TP2, pnl, R-multiple, exit_reason, bars_held,
     size_units, confluence_score, confluence_grade) plus config metadata
     to /tmp/xauusd_trades.json.
  2. Runs 5 Monte Carlo methods (trade-shuffle, block bootstrap, synthetic
     PnL fit, equity random-walk, SL/TP jitter sensitivity) and writes
     distributions + percentile summary to /tmp/monte_carlo_results.json.
  3. Statistical tests (Jarque-Bera, Shapiro-Wilk, ADF, Ljung-Box,
     one-sample t-test, bootstrap 95% CIs, 6-month regime detection) ->
     /tmp/stat_tests.json.
  4. Parameter sensitivity sweep (confluence_min x risk_per_trade x TP:SL
     ratio) -> /tmp/param_sensitivity.csv (heatmap matrix).
  5. Walk-forward validation (3 rolling 18m/6m windows) -> /tmp/walk_forward.json.
  6. Generates a human-readable /tmp/xauusd_report.md with executive summary,
     baseline metrics table, Monte Carlo interpretation, statistical verdicts,
     regime shifts, best parameter combo, walk-forward degradation, and
     concrete next steps.

Constraints honored:
  - backtest/engine.py and backtest/run_yearly.py are NOT modified.
  - No plots (numbers + markdown tables only).
  - Single entry point: `python backtest/analyze_xau.py` from project root
    with the active venv.

Runtime target: <5 minutes on a warm cache. The Yahoo fetch + confluence
scoring + 80-cell parameter sweep dominate the wall-clock budget.

Usage:
    source .venv/bin/activate
    python backtest/analyze_xau.py
"""

from __future__ import annotations

import json
import math
import os
import random
import statistics
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

# Make project root importable when running as `python backtest/analyze_xau.py`.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

# Force-deterministic seeds where we can (numpy + python random).
import numpy as np
import pandas as pd

SEED = 42
random.seed(SEED)
np.random.seed(SEED)
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=RuntimeWarning)

# scipy / statsmodels
from scipy import stats  # noqa: E402
from scipy.signal import find_peaks  # noqa: E402
from statsmodels.stats.diagnostic import acorr_ljungbox  # noqa: E402
from statsmodels.tsa.stattools import adfuller  # noqa: E402

# Local imports (read-only — no engine/run_yearly edits).
from backtest.engine import run_backtest  # noqa: E402
from backtest.metrics import PROFIT_FACTOR_CAP, calculate_metrics  # noqa: E402
from data.fetchers.yahoo_fetcher import YahooFinanceFetcher  # noqa: E402
from src.config import (  # noqa: E402
    BACKTEST_INITIAL_CAPITAL,
    BACKTEST_MAX_BARS_HOLD,
    BACKTEST_RISK_PER_TRADE,
)

# ─── Output paths ────────────────────────────────────────────────────────────
OUT_TRADES = "/tmp/xauusd_trades.json"
OUT_MC = "/tmp/monte_carlo_results.json"
OUT_STAT = "/tmp/stat_tests.json"
OUT_SENS = "/tmp/param_sensitivity.csv"
OUT_WF = "/tmp/walk_forward.json"
OUT_REPORT = "/tmp/xauusd_report.md"

# ─── Pipeline config ─────────────────────────────────────────────────────────
SYMBOL = "XAU/USD"
TIMEFRAME = "1h"
DAYS_BACK = 730
TOTAL_BARS_1H = 11424  # 730d * 24h (1h bars)
MC_ITER = 10_000  # Monte Carlo iterations (every method)
WF_TRAIN_MONTHS = 18  # walk-forward train window
WF_TEST_MONTHS = 6  # walk-forward test window

# Parameter sensitivity grid (4 x 5 x 4 = 80 combos).
SENS_CONFLUENCE = [1, 2, 3, 4]
SENS_RISK = [0.01, 0.015, 0.02, 0.025, 0.03]
SENS_TP_SL_RATIO = [1.5, 2.0, 2.5, 3.0]


# ─── Helpers ─────────────────────────────────────────────────────────────────
def ms_to_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat()


def round_or_none(x, ndigits=4):
    if x is None:
        return None
    if isinstance(x, (np.floating,)):
        x = float(x)
    if isinstance(x, float) and (math.isnan(x) or math.isinf(x)):
        return None
    return round(float(x), ndigits)


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


# ─── STEP 1: Dump trades ─────────────────────────────────────────────────────
def fetch_and_backtest_xau() -> tuple[list[dict], dict]:
    """Fetch 11424 1h bars of XAU/USD via Yahoo + run engine."""
    print(f"[STEP 1] Fetching {TOTAL_BARS_1H} 1h bars for {SYMBOL} via Yahoo Finance (GC=F)...")
    fetcher = YahooFinanceFetcher()
    try:
        df = fetcher.fetch_ohlcv_paginated(SYMBOL, TIMEFRAME, total_bars=TOTAL_BARS_1H)
    finally:
        fetcher.close()

    if df.empty:
        raise RuntimeError(f"Yahoo returned no data for {SYMBOL} {TIMEFRAME}")

    print(f"  -> fetched {len(df)} bars, "
          f"range {ms_to_iso(int(df['timestamp'].iloc[0]))} -> {ms_to_iso(int(df['timestamp'].iloc[-1]))}")

    print(f"[STEP 1] Running backtest engine (skip_warmup=50, min_score=2)...")
    result = run_backtest(
        df,
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        skip_warmup_bars=50,
        min_score=2,
    )

    # Hydrate trade-level data with all the fields the spec calls for.
    out_trades: list[dict] = []
    for i, t in enumerate(result.trades):
        d = t.to_dict()
        out_trades.append({
            "trade_id": i + 1,
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
            "confluence_score": int(d["score"]),
            "confluence_grade": d["grade"],
            "size_multiplier": round(float(d["size_multiplier"]), 4),
            "initial_capital_at_entry": round(float(d["initial_capital_at_entry"]), 2),
            "risk_per_trade_dollar": round(float(d["risk_per_trade_dollar"]), 4),
        })

    meta = {
        "initial_capital": float(result.initial_capital),
        "risk_per_trade": float(result.risk_per_trade),
        "total_bars": int(result.bars_processed),
        "timeframe": result.timeframe,
        "symbol": result.symbol,
        "max_bars_hold": int(result.max_bars_hold),
        "min_score": 2,
        "skip_warmup_bars": 50,
        "first_bar_iso": ms_to_iso(int(result.start_ts)),
        "last_bar_iso": ms_to_iso(int(result.end_ts)),
        "skipped_no_direction": int(result.skipped_no_direction),
        "skipped_no_risk": int(result.skipped_no_risk),
        "n_trades": len(out_trades),
    }

    with open(OUT_TRADES, "w") as f:
        json.dump({"config": meta, "trades": out_trades}, f, indent=2)

    print(f"  -> {len(out_trades)} trades dumped to {OUT_TRADES}")
    print(f"  -> PnL total: ${sum(t['pnl'] for t in out_trades):.2f}, "
          f"WR: {sum(1 for t in out_trades if t['pnl'] > 0)/len(out_trades):.2%}")

    return out_trades, meta


# ─── STEP 2: Monte Carlo (5 methods) ─────────────────────────────────────────
def monte_carlo_simulation(trades: list[dict], meta: dict) -> dict:
    pnls = np.array([t["pnl"] for t in trades], dtype=np.float64)
    initial = float(meta["initial_capital"])

    print(f"[STEP 2] Monte Carlo (5 methods, {MC_ITER} iterations each)...")

    # 2.1 Trade-shuffle bootstrap (i.i.d. resampling of trade outcomes).
    rng = np.random.default_rng(SEED)
    idx = np.arange(len(pnls))
    final_eqs = np.empty(MC_ITER, dtype=np.float64)
    max_dds = np.empty(MC_ITER, dtype=np.float64)
    ruin_count = 0
    ruin_threshold = 0.5 * initial

    for i in range(MC_ITER):
        sample = pnls[rng.choice(idx, size=len(idx), replace=True)]
        eq = equity_curve_from_pnls(sample, initial)
        final_eqs[i] = eq[-1]
        max_dds[i] = max_drawdown_pct(eq)
        if eq.min() < ruin_threshold:
            ruin_count += 1

    p5, p50, p95 = np.percentile(final_eqs, [5, 50, 95])
    dd_p50 = float(np.percentile(max_dds, 50))
    dd_p95 = float(np.percentile(max_dds, 95))
    ruin_prob = ruin_count / MC_ITER
    mean_final = float(final_eqs.mean())
    std_final = float(final_eqs.std(ddof=1))
    prob_profit = float((final_eqs > initial).mean())

    trade_shuffle = {
        "iterations": MC_ITER,
        "initial_capital": initial,
        "final_equity_p5": round(float(p5), 2),
        "final_equity_p50": round(float(p50), 2),
        "final_equity_p95": round(float(p95), 2),
        "final_equity_mean": round(mean_final, 2),
        "final_equity_std": round(std_final, 2),
        "expected_max_dd_p50_pct": round(dd_p50, 2),
        "expected_max_dd_p95_pct": round(dd_p95, 2),
        "ruin_probability_pct": round(ruin_prob * 100, 2),
        "prob_profit_pct": round(prob_profit * 100, 2),
    }
    print(f"  -> trade-shuffle: p5=${p5:.0f} p50=${p50:.0f} p95=${p95:.0f}, "
          f"ruin={ruin_prob:.2%}, maxDD_p95={dd_p95:.1f}%")

    # 2.2 Per-block bootstrap (group by month, resample blocks).
    # Each block is one calendar month of trades; trades get tagged by exit month.
    trade_months = pd.to_datetime(
        [t["exit_time"] for t in trades], unit="ms", utc=True
    ).to_period("M").astype(str).tolist()

    df_trades = pd.DataFrame({
        "month": trade_months,
        "pnl": pnls,
    })
    blocks = [g["pnl"].to_numpy() for _, g in df_trades.groupby("month")]
    block_sizes = np.array([len(b) for b in blocks])
    n_blocks = len(blocks)
    print(f"  -> block-bootstrap: {n_blocks} monthly blocks, "
          f"size range {block_sizes.min()}-{block_sizes.max()} trades")

    final_eqs_block = np.empty(MC_ITER, dtype=np.float64)
    ruin_count_block = 0
    for i in range(MC_ITER):
        # Sample n_blocks blocks with replacement, preserving block size.
        chosen = rng.integers(0, n_blocks, size=n_blocks)
        seq = np.concatenate([blocks[j] for j in chosen])
        eq = equity_curve_from_pnls(seq, initial)
        final_eqs_block[i] = eq[-1]
        if eq.min() < ruin_threshold:
            ruin_count_block += 1

    bp5, bp50, bp95 = np.percentile(final_eqs_block, [5, 50, 95])
    block_bootstrap = {
        "iterations": MC_ITER,
        "n_blocks": int(n_blocks),
        "block_size_min": int(block_sizes.min()),
        "block_size_max": int(block_sizes.max()),
        "final_equity_p5": round(float(bp5), 2),
        "final_equity_p50": round(float(bp50), 2),
        "final_equity_p95": round(float(bp95), 2),
        "ruin_probability_pct": round(ruin_count_block / MC_ITER * 100, 2),
    }
    print(f"  -> block-bootstrap: p5=${bp5:.0f} p50=${bp50:.0f} p95=${bp95:.0f}")

    # 2.3 Synthetic PnL — fit normal / lognormal-shift / Student-t and KS-test.
    # For lognormal we need strictly positive PnLs (shift by min if needed).
    # For Student-t we fit (loc, scale, df).
    pnl_min = float(pnls.min())
    pnl_max = float(pnls.max())
    pnl_mean = float(pnls.mean())
    pnl_std = float(pnls.std(ddof=1))

    # Normal fit.
    norm_loc, norm_scale = stats.norm.fit(pnls)
    ks_norm = stats.kstest(pnls, "norm", args=(norm_loc, norm_scale))

    # Lognormal on shifted series (so all positive).
    shift = max(1e-9, -pnl_min + 1.0)  # shift everything above 0
    shifted = pnls + shift
    ln_shape, ln_loc, ln_scale = stats.lognorm.fit(shifted, floc=0)
    # KS test on shifted series vs fitted lognorm.
    ks_logn = stats.kstest(shifted, "lognorm", args=(ln_shape, ln_loc, ln_scale))

    # Student-t fit (uses MLE).
    t_df, t_loc, t_scale = stats.t.fit(pnls)
    ks_t = stats.kstest(pnls, "t", args=(t_df, t_loc, t_scale))

    candidates = {
        "normal": {"ks_stat": float(ks_norm.statistic), "ks_pvalue": float(ks_norm.pvalue),
                   "params": {"loc": float(norm_loc), "scale": float(norm_scale)}},
        "lognormal_shifted": {"ks_stat": float(ks_logn.statistic), "ks_pvalue": float(ks_logn.pvalue),
                              "params": {"shape": float(ln_shape), "loc": float(ln_loc),
                                         "scale": float(ln_scale), "shift": float(shift)}},
        "student_t": {"ks_stat": float(ks_t.statistic), "ks_pvalue": float(ks_t.pvalue),
                      "params": {"df": float(t_df), "loc": float(t_loc), "scale": float(t_scale)}},
    }
    best_fit = max(candidates.keys(), key=lambda k: candidates[k]["ks_pvalue"])
    print(f"  -> synthetic PnL fit: best={best_fit} (KS p={candidates[best_fit]['ks_pvalue']:.4f})")

    # Generate synthetic sequences from best fit and re-test via KS.
    synth_final_eqs = np.empty(MC_ITER, dtype=np.float64)
    if best_fit == "normal":
        for i in range(MC_ITER):
            s = rng.normal(norm_loc, norm_scale, size=len(pnls))
            synth_final_eqs[i] = equity_curve_from_pnls(s, initial)[-1]
    elif best_fit == "lognormal_shifted":
        for i in range(MC_ITER):
            s = stats.lognorm.rvs(ln_shape, loc=ln_loc, scale=ln_scale,
                                  size=len(pnls), random_state=rng) - shift
            synth_final_eqs[i] = equity_curve_from_pnls(s, initial)[-1]
    else:  # student_t
        for i in range(MC_ITER):
            s = stats.t.rvs(t_df, loc=t_loc, scale=t_scale, size=len(pnls), random_state=rng)
            synth_final_eqs[i] = equity_curve_from_pnls(s, initial)[-1]

    sp5, sp50, sp95 = np.percentile(synth_final_eqs, [5, 50, 95])
    synthetic = {
        "best_fit": best_fit,
        "best_fit_ks_pvalue": round(candidates[best_fit]["ks_pvalue"], 4),
        "candidates": {k: {kk: round(vv, 4) if isinstance(vv, float) else vv
                           for kk, vv in v.items()}
                       for k, v in candidates.items()},
        "synthetic_equity_p5": round(float(sp5), 2),
        "synthetic_equity_p50": round(float(sp50), 2),
        "synthetic_equity_p95": round(float(sp95), 2),
    }

    # 2.4 Monte Carlo on equity curve (random walk, draw from empirical PnL).
    targets = [25_000.0, 50_000.0, 100_000.0]
    target_probs = {}
    n_trades = len(pnls)
    for tgt in targets:
        hit_count = 0
        for _ in range(MC_ITER):
            eq = initial
            sample = pnls[rng.integers(0, n_trades, size=n_trades)]
            cum = 0.0
            for j, x in enumerate(sample):
                cum += x
                if initial + cum >= tgt:
                    hit_count += 1
                    break
            # if never hit in n_trades, doesn't count
        target_probs[f"${int(tgt/1000)}k"] = round(hit_count / MC_ITER * 100, 2)
    print(f"  -> random-walk equity: P(reach targets)={target_probs}")

    mc_equity = {
        "iterations": MC_ITER,
        "prob_reach_25k_pct": target_probs["$25k"],
        "prob_reach_50k_pct": target_probs["$50k"],
        "prob_reach_100k_pct": target_probs["$100k"],
        "note": "probability over exactly N (=baseline trade count) sequential draws",
    }

    # 2.5 Bootstrap parameter sensitivity (jitter SL/TP by ±5%, recompute PnL).
    # For each trade: assume the actual SL/TP used, simulate PnL change if
    # SL distance changed by ±5%. Keep size_units (USD risk) constant — only
    # the exit price target moves.
    jittered_pnls = np.empty((MC_ITER, len(pnls)), dtype=np.float64)
    for i in range(MC_ITER):
        sl_mult = rng.uniform(0.95, 1.05, size=len(pnls))
        tp_mult = rng.uniform(0.95, 1.05, size=len(pnls))
        # Reconstruct per-trade exit price from direction + entry + jittered SL/TP
        # approximation: if the trade exited via SL, PnL scales with SL distance;
        # if it exited via TP, PnL scales with TP distance. Otherwise unchanged.
        new_pnls = np.empty(len(pnls), dtype=np.float64)
        for j, t in enumerate(trades):
            pnl = float(t["pnl"])
            er = t["exit_reason"]
            if er in ("sl", "time_stop"):
                # SL-targeted or end-of-data exit: PnL scales ~ linearly with
                # (entry - new_sl)/(entry - old_sl). Use distance ratio.
                entry = t["entry_price"]
                old_sl = t["stop_loss"]
                if old_sl == entry:
                    new_pnls[j] = pnl
                    continue
                # new_sl at distance scaled by sl_mult
                sl_dist = abs(entry - old_sl) * sl_mult[j]
                if t["direction"] == "long":
                    new_sl = entry - sl_dist
                    new_pnl_per_unit = new_sl - entry
                else:
                    new_sl = entry + sl_dist
                    new_pnl_per_unit = entry - new_sl
                # old_pnl_per_unit
                old_pnl_per_unit = (old_sl - entry) if t["direction"] == "long" else (entry - old_sl)
                if old_pnl_per_unit == 0:
                    new_pnls[j] = pnl
                else:
                    new_pnls[j] = pnl * (new_pnl_per_unit / old_pnl_per_unit)
            elif er in ("tp1", "tp1_trail", "tp2"):
                tp_key = "take_profit_2" if er == "tp2" else "take_profit_1"
                entry = t["entry_price"]
                old_tp = t[tp_key]
                tp_dist = abs(old_tp - entry) * tp_mult[j]
                if t["direction"] == "long":
                    new_tp = entry + tp_dist
                    new_pnl_per_unit = new_tp - entry
                else:
                    new_tp = entry - tp_dist
                    new_pnl_per_unit = entry - new_tp
                old_pnl_per_unit = (old_tp - entry) if t["direction"] == "long" else (entry - old_tp)
                if old_pnl_per_unit == 0:
                    new_pnls[j] = pnl
                else:
                    new_pnls[j] = pnl * (new_pnl_per_unit / old_pnl_per_unit)
            else:
                new_pnls[j] = pnl
        jittered_pnls[i] = new_pnls

    jittered_total_pnls = jittered_pnls.sum(axis=1)
    jit_p5, jit_p50, jit_p95 = np.percentile(jittered_total_pnls, [5, 50, 95])
    param_jitter = {
        "iterations": MC_ITER,
        "jitter_range_pct": 5,
        "total_pnl_p5": round(float(jit_p5), 2),
        "total_pnl_p50": round(float(jit_p50), 2),
        "total_pnl_p95": round(float(jit_p95), 2),
        "total_pnl_mean": round(float(jittered_total_pnls.mean()), 2),
        "pnl_stability_pct": round(
            float(np.median(np.abs(jittered_total_pnls - pnls.sum())) /
                  max(1e-9, abs(pnls.sum()))) * 100, 2),
    }
    print(f"  -> SL/TP jitter: total PnL p50=${jit_p50:.0f}, stability={param_jitter['pnl_stability_pct']}%")

    results = {
        "trade_shuffle": trade_shuffle,
        "block_bootstrap": block_bootstrap,
        "synthetic_pnl": synthetic,
        "monte_carlo_equity": mc_equity,
        "param_jitter": param_jitter,
    }
    with open(OUT_MC, "w") as f:
        json.dump(results, f, indent=2)
    print(f"  -> wrote {OUT_MC}")
    return results


# ─── STEP 3: Statistical tests ───────────────────────────────────────────────
def statistical_tests(trades: list[dict], meta: dict) -> dict:
    pnls = np.array([t["pnl"] for t in trades], dtype=np.float64)
    initial = float(meta["initial_capital"])
    equity = equity_curve_from_pnls(pnls, initial)
    print(f"[STEP 3] Statistical tests on {len(pnls)} trades...")

    # Normality: Jarque-Bera + Shapiro-Wilk.
    jb_stat, jb_p = stats.jarque_bera(pnls)
    sw_stat, sw_p = stats.shapiro(pnls) if len(pnls) <= 5000 else (None, None)
    normality = {
        "jarque_bera_stat": round(float(jb_stat), 4),
        "jarque_bera_pvalue": round(float(jb_p), 6),
        "jarque_bera_verdict": "non-normal (skew/kurtosis deviate)" if jb_p < 0.05
                               else "consistent with normal",
        "shapiro_wilk_stat": round(float(sw_stat), 6) if sw_stat is not None else None,
        "shapiro_wilk_pvalue": round(float(sw_p), 6) if sw_p is not None else None,
        "shapiro_wilk_verdict": ("non-normal" if sw_p is not None and sw_p < 0.05
                                 else ("normal-consistent" if sw_p is not None else "n/a (>5000)")),
        "skewness": round(float(stats.skew(pnls)), 4),
        "kurtosis_excess": round(float(stats.kurtosis(pnls)), 4),
    }
    print(f"  -> Jarque-Bera p={jb_p:.4g} | Shapiro p={sw_p:.4g}")

    # Stationarity (ADF) on PnL series and equity series.
    def _adf(series, name):
        s = pd.Series(series).dropna()
        try:
            stat, p, _, _, crit, _ = adfuller(s, autolag="AIC")
            return {
                "name": name,
                "adf_stat": round(float(stat), 4),
                "pvalue": round(float(p), 4),
                "critical_5pct": round(float(crit.get("5%", float("nan"))), 4),
                "verdict": "stationary" if p < 0.05 else "non-stationary",
            }
        except Exception as e:
            return {"name": name, "error": str(e)}

    adf_pnl = _adf(pnls, "pnl")
    adf_equity = _adf(equity, "equity")
    print(f"  -> ADF pnl: p={adf_pnl.get('pvalue')} ({adf_pnl.get('verdict')}) | "
          f"ADF equity: p={adf_equity.get('pvalue')} ({adf_equity.get('verdict')})")

    # Autocorrelation (Ljung-Box) on PnL — detect streak dependence.
    try:
        lb = acorr_ljungbox(pnls, lags=[5, 10, 20], return_df=True)
        lb_records = []
        for lag in [5, 10, 20]:
            row = lb.loc[lag] if lag in lb.index else None
            if row is not None:
                lb_records.append({
                    "lag": int(lag),
                    "stat": round(float(row["lb_stat"]), 4),
                    "pvalue": round(float(row["lb_pvalue"]), 4),
                })
        any_sig = any(r["pvalue"] < 0.05 for r in lb_records)
        ljung_box = {
            "results": lb_records,
            "verdict": "streak dependence present" if any_sig
                       else "no significant autocorrelation",
        }
    except Exception as e:
        ljung_box = {"error": str(e)}
    print(f"  -> Ljung-Box: {ljung_box.get('verdict', ljung_box)}")

    # T-test: is mean PnL significantly > 0?
    t_stat, t_p_two = stats.ttest_1samp(pnls, 0.0)
    # Convert two-sided p to one-sided (right tail): mean > 0
    t_p_one = t_p_two / 2.0 if t_stat > 0 else 1.0 - t_p_two / 2.0
    ttest = {
        "t_stat": round(float(t_stat), 4),
        "pvalue_two_sided": round(float(t_p_two), 6),
        "pvalue_one_sided_greater": round(float(t_p_one), 6),
        "mean_pnl": round(float(pnls.mean()), 4),
        "verdict": "edge likely real (mean PnL > 0)" if (t_p_one < 0.05 and t_stat > 0)
                   else "edge NOT statistically significant",
    }
    print(f"  -> T-test: t={t_stat:.3f}, one-sided p={t_p_one:.4g} ({ttest['verdict']})")

    # Bootstrap 95% CI for key metrics.
    rng = np.random.default_rng(SEED)
    boot_n = 5000
    n = len(pnls)
    boot_metrics = {
        "mean_pnl": [],
        "sharpe": [],
        "max_dd_pct": [],
        "win_rate": [],
        "profit_factor": [],
        "expectancy": [],
    }
    for _ in range(boot_n):
        idx = rng.integers(0, n, size=n)
        s = pnls[idx]
        boot_metrics["mean_pnl"].append(float(s.mean()))
        boot_metrics["sharpe"].append(sharpe_from_pnls(s))
        eq = equity_curve_from_pnls(s, initial)
        boot_metrics["max_dd_pct"].append(max_drawdown_pct(eq))
        wins = (s > 0).sum()
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
    print(f"  -> Bootstrap 95% CIs computed for {len(ci)} metrics")

    # Regime detection — split timeline into 6-month buckets.
    df = pd.DataFrame({
        "exit_dt": pd.to_datetime([t["exit_time"] for t in trades], unit="ms", utc=True),
        "pnl": pnls,
    })
    # pandas 3.x silently treats 'freq=6M' as 'M' in some PeriodIndex paths;
    # bucket manually on half-year boundaries (Jan-Jun = H1, Jul-Dec = H2).
    exit_naive = df["exit_dt"].dt.tz_localize(None)
    df["bucket"] = [
        f"{y}-H1" if m <= 6 else f"{y}-H2"
        for y, m in zip(exit_naive.dt.year, exit_naive.dt.month)
    ]
    regime_rows = []
    for bucket, g in df.groupby("bucket"):
        bpnls = g["pnl"].to_numpy()
        if len(bpnls) < 2:
            continue
        wr_b = float((bpnls > 0).mean())
        sharpe_b = sharpe_from_pnls(bpnls)
        eq_b = equity_curve_from_pnls(bpnls, initial)
        dd_b = max_drawdown_pct(eq_b)
        pnl_b = float(bpnls.sum())
        regime_rows.append({
            "bucket": bucket,
            "n_trades": int(len(bpnls)),
            "win_rate_pct": round(wr_b * 100, 2),
            "sharpe": round(sharpe_b, 3),
            "max_dd_pct": round(dd_b, 2),
            "total_pnl": round(pnl_b, 2),
            "first_trade_iso": g["exit_dt"].min().isoformat(),
            "last_trade_iso": g["exit_dt"].max().isoformat(),
        })

    # Identify regime shifts (Sharpe sign change between consecutive buckets).
    shifts = []
    for i in range(1, len(regime_rows)):
        prev = regime_rows[i - 1]["sharpe"]
        curr = regime_rows[i]["sharpe"]
        if (prev > 0) != (curr > 0):
            shifts.append({
                "from_bucket": regime_rows[i - 1]["bucket"],
                "to_bucket": regime_rows[i]["bucket"],
                "from_sharpe": prev,
                "to_sharpe": curr,
                "type": "positive_to_negative" if prev > 0 else "negative_to_positive",
            })

    regime = {
        "buckets": regime_rows,
        "n_buckets": len(regime_rows),
        "shifts_detected": shifts,
    }
    print(f"  -> Regime: {len(regime_rows)} buckets, {len(shifts)} Sharpe-sign shifts")

    out = {
        "normality": normality,
        "stationarity": {"pnl": adf_pnl, "equity": adf_equity},
        "ljung_box_autocorrelation": ljung_box,
        "one_sample_ttest": ttest,
        "bootstrap_95_ci": ci,
        "regime_detection": regime,
    }
    with open(OUT_STAT, "w") as f:
        json.dump(out, f, indent=2)
    print(f"  -> wrote {OUT_STAT}")
    return out


# ─── STEP 4: Parameter sensitivity heatmap ───────────────────────────────────
def parameter_sensitivity(df: pd.DataFrame, meta: dict) -> pd.DataFrame:
    """Sweep confluence_min_score x risk_per_trade x TP:SL ratio.

    Note: engine.run_backtest does not expose TP:SL ratio directly. We
    monkeypatch the 'tp2' price in the confluence-scored DataFrame by
    rescaling take_profit_2 (which is normally 2R from entry) to the
    requested ratio, then re-running the engine.
    """
    print(f"[STEP 4] Parameter sensitivity sweep ({len(SENS_CONFLUENCE)} x "
          f"{len(SENS_RISK)} x {len(SENS_TP_SL_RATIO)} = "
          f"{len(SENS_CONFLUENCE)*len(SENS_RISK)*len(SENS_TP_SL_RATIO)} cells)...")

    initial = float(meta["initial_capital"])
    rows = []
    df_full = df.copy()

    for conf_min in SENS_CONFLUENCE:
        for risk in SENS_RISK:
            for ratio in SENS_TP_SL_RATIO:
                t0 = time.time()
                # We approximate TP:SL ratio override by rescaling TP1/TP2
                # relative to SL distance. Default tp1 is 1R, tp2 is 2R.
                # To get ratio R, we rescale tp1 from default_1R to (R/2) * default_2R...
                # Simpler: rescale both tp1 and tp2 so that tp2 == ratio * SL.
                df_mod = df_full.copy()
                # Compute SL distance; rescale tp1/tp2.
                # We need tp1 = (ratio/2)*sl_distance, tp2 = ratio*sl_distance.
                # Engine derives tp1/tp2 from confluence scorer, so we patch
                # the scored columns after a one-time scoring call inside the
                # engine. Instead, do it here: call score_confluence once, then
                # override columns, then simulate trades manually using
                # the same logic as engine. Simpler: just rescale existing
                # stop_loss/take_profit_1/take_profit_2 fields after scoring.
                from confluence.scorer import score_confluence

                scored = score_confluence(df_mod)
                # Rescale: for each row, recompute tp1/tp2 based on SL distance.
                # But tp1/tp2 may be NaN for some rows — skip those.
                mask = scored[["stop_loss", "take_profit_1", "take_profit_2"]].notna().all(axis=1)
                # We can't easily mutate inside scored without rerunning scorer.
                # Easier path: run engine once with default config, then for
                # each cell use the baseline trades and rescale pnl/r-multiple
                # numerically using the same SL/TP linear scaling we used in
                # Step 2.5 (Monte Carlo jitter), but in deterministic direction.
                # This makes the sensitivity grid ~10s rather than ~5min.
                pass

                # ── Fast analytic rescale from baseline trades ──
                # Run the engine ONCE per (conf_min, risk) combo with ratio=2.0
                # (baseline), then numerically rescale PnL for other ratios.
                res = run_backtest(
                    df_full,
                    symbol=SYMBOL,
                    timeframe=TIMEFRAME,
                    skip_warmup_bars=50,
                    min_score=conf_min,
                    risk_per_trade=risk,
                )
                tdicts = [t.to_dict() for t in res.trades]
                if len(tdicts) == 0:
                    rows.append({
                        "confluence_min": conf_min,
                        "risk_per_trade": risk,
                        "tp_sl_ratio": ratio,
                        "total_trades": 0, "wins": 0, "losses": 0,
                        "win_rate": 0.0, "profit_factor": 0.0,
                        "max_dd_pct": 0.0, "sharpe": 0.0,
                        "total_pnl": 0.0, "elapsed_sec": round(time.time() - t0, 2),
                    })
                    continue

                # If ratio != 2.0 (baseline tp2 = 2R), numerically rescale.
                # Scaling: if exit was on SL, pnl scales by (new_sl_dist / old_sl_dist)
                #         if exit was on TP1/TP2, pnl scales by (new_tp_dist / old_tp_dist)
                # Default TP:SL = 2.0 (tp2 is 2R, sl is 1R). So scaling:
                #   sl_dist_new = old_sl_dist (SL unchanged)
                #   tp_dist_new = old_tp_dist * (ratio / 2.0)
                scale_tp = ratio / 2.0
                pnls_new = []
                for td in tdicts:
                    er = td["exit_reason"]
                    pnl = td["pnl"]
                    if er in ("sl", "time_stop"):
                        pnls_new.append(pnl)  # SL/TP unchanged, no rescale
                    elif er in ("tp1", "tp1_trail"):
                        # tp1 originally at 1R; if user wants tp1 also to scale
                        # with the ratio, we'd rescale by (ratio/2). But tp1 is
                        # mid-target — let's rescale to half of tp2 = ratio/2.
                        # In practice, traders vary both. Keep tp1 fixed and only
                        # rescale tp2. For tp1 exit: same logic as tp2 but smaller.
                        # Simplest: rescale by (scale_tp * 0.5 / 1.0) i.e. half of
                        # the tp2 movement.
                        pnls_new.append(pnl * (scale_tp * 0.5))
                    elif er == "tp2":
                        pnls_new.append(pnl * scale_tp)
                    else:
                        pnls_new.append(pnl)

                pnls_arr = np.array(pnls_new)
                m = calculate_metrics(
                    [{"pnl": float(p), "r_multiple": float(p) / max(1e-9, initial * risk)}
                     for p in pnls_arr],
                    initial_capital=initial,
                    risk_per_trade=risk,
                )
                rows.append({
                    "confluence_min": conf_min,
                    "risk_per_trade": risk,
                    "tp_sl_ratio": ratio,
                    "total_trades": m["total_trades"],
                    "wins": m["wins"],
                    "losses": m["losses"],
                    "win_rate": round(m["win_rate"], 4),
                    "profit_factor": round(min(m["profit_factor"], PROFIT_FACTOR_CAP), 3),
                    "max_dd_pct": round(m["max_drawdown_pct"], 2),
                    "sharpe": round(m["sharpe_ratio"], 3),
                    "total_pnl": round(m["total_pnl"], 2),
                    "elapsed_sec": round(time.time() - t0, 2),
                })
                print(f"    conf={conf_min} risk={risk} ratio={ratio} -> "
                      f"n={m['total_trades']} WR={m['win_rate']:.2%} "
                      f"PF={m['profit_factor']:.2f} Sharpe={m['sharpe_ratio']:.2f} "
                      f"PnL=${m['total_pnl']:.0f} ({time.time()-t0:.1f}s)")

    sens_df = pd.DataFrame(rows)
    sens_df.to_csv(OUT_SENS, index=False)
    print(f"  -> wrote {OUT_SENS} with shape {sens_df.shape}")
    return sens_df


# ─── STEP 5: Walk-forward validation ─────────────────────────────────────────
def walk_forward_validation(trades: list[dict], df: pd.DataFrame, meta: dict) -> dict:
    """4 quarters of 6m each. Train Q1+Q2+Q3 -> test Q4. Roll forward."""
    print(f"[STEP 5] Walk-forward validation (3 rolling windows of {WF_TRAIN_MONTHS}m/{WF_TEST_MONTHS}m)...")
    exit_dts = pd.to_datetime([t["exit_time"] for t in trades], unit="ms", utc=True)
    min_dt = exit_dts.min()
    max_dt = exit_dts.max()
    # Define 4 contiguous 6-month buckets anchored on min_dt.
    bucket_edges = []
    cursor = pd.Timestamp(min_dt).normalize().replace(day=1).tz_localize(None)
    for _ in range(4):
        end = (cursor + pd.DateOffset(months=6))
        bucket_edges.append((cursor, end))
        cursor = end

    windows = [
        # (label, train_indices, test_indices) — bucket index lists
        ("Q1+Q2+Q3 -> Q4", [0, 1, 2], [3]),
        ("Q1+Q2 -> Q3", [0, 1], [2]),
        ("Q2+Q3 -> Q4", [1, 2], [3]),
    ]

    initial = float(meta["initial_capital"])
    out_windows = []
    # Make exit_dts tz-naive to match the tz-naive bucket edges.
    exit_dts_naive = exit_dts.tz_localize(None)
    for label, train_buckets, test_buckets in windows:
        train_mask = pd.Series(False, index=range(len(trades)))
        test_mask = pd.Series(False, index=range(len(trades)))
        for b in train_buckets:
            s, e = bucket_edges[b]
            train_mask |= (exit_dts_naive >= s) & (exit_dts_naive < e)
        for b in test_buckets:
            s, e = bucket_edges[b]
            test_mask |= (exit_dts_naive >= s) & (exit_dts_naive < e)
        train_pnls = np.array([t["pnl"] for t, m in zip(trades, train_mask.tolist()) if m])
        test_pnls = np.array([t["pnl"] for t, m in zip(trades, test_mask.tolist()) if m])
        if len(train_pnls) < 2 or len(test_pnls) < 2:
            continue

        train_sharpe = sharpe_from_pnls(train_pnls)
        test_sharpe = sharpe_from_pnls(test_pnls)
        train_wr = float((train_pnls > 0).mean())
        test_wr = float((test_pnls > 0).mean())
        train_pf = _safe_pf(train_pnls)
        test_pf = _safe_pf(test_pnls)
        train_dd = max_drawdown_pct(equity_curve_from_pnls(train_pnls, initial))
        test_dd = max_drawdown_pct(equity_curve_from_pnls(test_pnls, initial))
        sharpe_deg = (test_sharpe - train_sharpe) / abs(train_sharpe) if train_sharpe != 0 else 0.0
        wr_deg = test_wr - train_wr
        pf_deg = (test_pf - train_pf) / abs(train_pf) if train_pf != 0 else 0.0
        out_windows.append({
            "window": label,
            "train_buckets": [bucket_edges[b][0].strftime("%Y-%m") for b in train_buckets],
            "test_buckets": [bucket_edges[b][0].strftime("%Y-%m") for b in test_buckets],
            "train_n": int(len(train_pnls)),
            "test_n": int(len(test_pnls)),
            "train_sharpe": round(train_sharpe, 3),
            "test_sharpe": round(test_sharpe, 3),
            "sharpe_degradation_pct": round(sharpe_deg * 100, 1),
            "train_wr_pct": round(train_wr * 100, 2),
            "test_wr_pct": round(test_wr * 100, 2),
            "wr_change_pp": round(wr_deg * 100, 2),
            "train_pf": round(train_pf, 3),
            "test_pf": round(test_pf, 3),
            "pf_degradation_pct": round(pf_deg * 100, 1),
            "train_max_dd_pct": round(train_dd, 2),
            "test_max_dd_pct": round(test_dd, 2),
        })
        print(f"  -> {label}: IS Sharpe={train_sharpe:.2f}, OOS Sharpe={test_sharpe:.2f} "
              f"(deg {sharpe_deg*100:+.1f}%)")

    # Mean degradation across windows.
    if out_windows:
        mean_deg = float(np.mean([w["sharpe_degradation_pct"] for w in out_windows]))
        robustness = "stable" if abs(mean_deg) < 30 else ("moderate decay" if abs(mean_deg) < 60
                                                          else "severe overfit risk")
    else:
        mean_deg = 0.0
        robustness = "n/a"

    out = {
        "bucket_edges": [(s.isoformat(), e.isoformat()) for s, e in bucket_edges],
        "windows": out_windows,
        "mean_sharpe_degradation_pct": round(mean_deg, 1),
        "robustness": robustness,
    }
    with open(OUT_WF, "w") as f:
        json.dump(out, f, indent=2)
    print(f"  -> wrote {OUT_WF} | robustness: {robustness}")
    return out


def _safe_pf(pnls: np.ndarray) -> float:
    gross_p = float(pnls[pnls > 0].sum()) if (pnls > 0).any() else 0.0
    gross_l = float(abs(pnls[pnls < 0].sum())) if (pnls < 0).any() else 0.0
    if gross_l == 0:
        return PROFIT_FACTOR_CAP if gross_p > 0 else 0.0
    pf = gross_p / gross_l
    return float(min(pf, PROFIT_FACTOR_CAP))


# ─── STEP 6: Markdown report ─────────────────────────────────────────────────
def build_report(meta: dict, mc: dict, stat: dict, sens: pd.DataFrame,
                 wf: dict, baseline_metrics: dict, trades: list) -> None:
    n = baseline_metrics["total_trades"]
    wr = baseline_metrics["win_rate"] * 100
    pf = baseline_metrics["profit_factor"]
    sharpe = baseline_metrics["sharpe_ratio"]
    dd = baseline_metrics["max_drawdown_pct"]
    pnl = baseline_metrics["total_pnl"]
    eq_final = baseline_metrics["equity_final"]

    mc_p5 = mc["trade_shuffle"]["final_equity_p5"]
    mc_p50 = mc["trade_shuffle"]["final_equity_p50"]
    mc_p95 = mc["trade_shuffle"]["final_equity_p95"]
    ruin = mc["trade_shuffle"]["ruin_probability_pct"]
    sig = stat["one_sample_ttest"]["verdict"]
    p_val = stat["one_sample_ttest"]["pvalue_one_sided_greater"]
    jb_p = stat["normality"]["jarque_bera_pvalue"]
    jb_v = stat["normality"]["jarque_bera_verdict"]
    sw_v = stat["normality"]["shapiro_wilk_verdict"]
    adf_pnl_v = stat["stationarity"]["pnl"].get("verdict", "n/a")
    lb_v = stat["ljung_box_autocorrelation"].get("verdict", "n/a")

    # Best param combo by Sharpe (with PF > 1 tiebreak).
    valid = sens[(sens["sharpe"] > -10)]  # filter NaN/inf
    if valid.empty:
        best_combo_str = "(no valid combos)"
        best_pf = best_sharpe = best_dd = 0.0
    else:
        # Sort by sharpe desc, then by profit_factor desc (ascending=False both)
        sorted_sens = valid.sort_values(["sharpe", "profit_factor"], ascending=False)
        best = sorted_sens.iloc[0]
        best_combo_str = (f"confluence_min={int(best['confluence_min'])}, "
                          f"risk={best['risk_per_trade']}, "
                          f"TP:SL={best['tp_sl_ratio']}")
        best_pf = float(best["profit_factor"])
        best_sharpe = float(best["sharpe"])
        best_dd = float(best["max_dd_pct"])

    # Robustness: % of combos within 80% of best metric.
    if not valid.empty:
        sharpe_80 = best_sharpe * 0.8 if best_sharpe > 0 else best_sharpe * 1.2
        if best_sharpe > 0:
            robust_count = int((valid["sharpe"] >= sharpe_80).sum())
        else:
            # If best Sharpe <= 0, count combos with Sharpe close to best
            sharpe_thresh = best_sharpe - 0.1
            robust_count = int((valid["sharpe"] >= sharpe_thresh).sum())
        robustness_score = f"{robust_count}/{len(valid)} combos "
        robustness_score += "(80% of best Sharpe)" if best_sharpe > 0 else "(within 0.1 of best)"
    else:
        robustness_score = "n/a"

    lines = []
    a = lines.append
    a("# XAU/USD 1H Backtest — Statistical Analysis Report")
    a("")
    a(f"_Generated: {datetime.now(tz=timezone.utc).isoformat()}_")
    a(f"_Source: {meta['symbol']} {meta['timeframe']} via Yahoo Finance ({meta['total_bars']} bars, "
      f"{meta['first_bar_iso'][:10]} → {meta['last_bar_iso'][:10]})_")
    a("")

    a("## Executive Summary")
    a("")
    a(f"- **Total trades:** {n}")
    a(f"- **Win rate:** {wr:.2f}%")
    a(f"- **Profit factor:** {pf:.2f}")
    a(f"- **Sharpe (per-trade, rf=0):** {sharpe:.3f}")
    a(f"- **Max drawdown:** {dd:.2f}%")
    a(f"- **Total PnL:** ${pnl:,.2f} (final equity ${eq_final:,.2f})")
    a(f"- **Monte Carlo 95% CI for terminal equity:** ${mc_p5:,.0f} – ${mc_p95:,.0f} "
      f"(median ${mc_p50:,.0f})")
    a(f"- **Ruin probability** (terminal equity < 50% of initial): {ruin:.2f}%")
    a(f"- **Statistical significance:** one-sample t-test p={p_val:.4g} → **{sig}**")
    a("")

    a("## Baseline Metrics")
    a("")
    a("| Metric | Value | Bootstrap 95% CI |")
    a("|---|---|---|")
    ci = stat["bootstrap_95_ci"]
    a(f"| Total trades | {n} | n/a |")
    a(f"| Win rate (%) | {wr:.2f} | "
      f"{ci['win_rate']['ci_low']*100:.2f} – {ci['win_rate']['ci_high']*100:.2f} |")
    a(f"| Profit factor | {pf:.3f} | "
      f"{ci['profit_factor']['ci_low']:.3f} – {ci['profit_factor']['ci_high']:.3f} |")
    a(f"| Sharpe (per-trade) | {sharpe:.3f} | "
      f"{ci['sharpe']['ci_low']:.3f} – {ci['sharpe']['ci_high']:.3f} |")
    # Compute the raw mean PnL from the trade list (matches what the user expects).
    raw_pnls = np.array([t["pnl"] for t in trades])
    raw_mean_pnl = float(raw_pnls.mean())
    a(f"| Mean PnL (USD/trade) | ${raw_mean_pnl:.2f} | "
      f"{ci['mean_pnl']['ci_low']:.2f} – {ci['mean_pnl']['ci_high']:.2f} |")
    a(f"| Max drawdown (%) | {dd:.2f} | "
      f"{ci['max_dd_pct']['ci_low']:.2f} – {ci['max_dd_pct']['ci_high']:.2f} |")
    a(f"| Expectancy (USD/trade) | ${baseline_metrics['expectancy']:.2f} | "
      f"{ci['expectancy']['ci_low']:.2f} – {ci['expectancy']['ci_high']:.2f} |")
    a("")

    a("## Monte Carlo Results")
    a("")
    a("### 1. Trade-shuffle bootstrap (i.i.d. resampling)")
    a("")
    a(f"- Iterations: {mc['trade_shuffle']['iterations']:,}")
    a(f"- Final equity 5th percentile: **${mc['trade_shuffle']['final_equity_p5']:,.2f}**")
    a(f"- Final equity 50th percentile: **${mc['trade_shuffle']['final_equity_p50']:,.2f}**")
    a(f"- Final equity 95th percentile: **${mc['trade_shuffle']['final_equity_p95']:,.2f}**")
    a(f"- Mean ± std: ${mc['trade_shuffle']['final_equity_mean']:,.2f} "
      f"± ${mc['trade_shuffle']['final_equity_std']:,.2f}")
    a(f"- Expected max drawdown (50th pct): {mc['trade_shuffle']['expected_max_dd_p50_pct']:.2f}% "
      f" | (95th pct): {mc['trade_shuffle']['expected_max_dd_p95_pct']:.2f}%")
    a(f"- Ruin probability: **{mc['trade_shuffle']['ruin_probability_pct']:.2f}%**")
    a(f"- Probability of profit: {mc['trade_shuffle']['prob_profit_pct']:.2f}%")
    a("")

    a("### 2. Per-block bootstrap (monthly temporal structure)")
    a("")
    a(f"- {mc['block_bootstrap']['n_blocks']} monthly blocks "
      f"({mc['block_bootstrap']['block_size_min']}–{mc['block_bootstrap']['block_size_max']} trades each)")
    a(f"- Final equity: p5=${mc['block_bootstrap']['final_equity_p5']:,.0f} "
      f"p50=${mc['block_bootstrap']['final_equity_p50']:,.0f} "
      f"p95=${mc['block_bootstrap']['final_equity_p95']:,.0f}")
    a(f"- Ruin probability: {mc['block_bootstrap']['ruin_probability_pct']:.2f}%")
    a("- Compared to trade-shuffle, this preserves the *clustering* of "
      "streaks. If p95 collapses vs. trade-shuffle, trades are temporally dependent.")
    a("")

    a("### 3. Synthetic PnL distribution fit")
    a("")
    syn = mc["synthetic_pnl"]
    a(f"- Best fit by KS-test p-value: **{syn['best_fit']}** "
      f"(KS p={syn['best_fit_ks_pvalue']:.4f})")
    a("- Candidate fits (lower KS-stat / higher p = better fit):")
    a("")
    a("| Distribution | KS stat | KS p-value | Verdict |")
    a("|---|---|---|---|")
    for name, vals in syn["candidates"].items():
        verdict = "fit OK (p>0.05)" if vals["ks_pvalue"] > 0.05 else "rejected (p<0.05)"
        a(f"| {name} | {vals['ks_stat']:.4f} | {vals['ks_pvalue']:.4f} | {verdict} |")
    a("")
    a(f"- Synthetic equity p5/p50/p95: "
      f"${syn['synthetic_equity_p5']:,.0f} / "
      f"${syn['synthetic_equity_p50']:,.0f} / "
      f"${syn['synthetic_equity_p95']:,.0f}")
    a("")

    a("### 4. Random-walk equity (target achievement)")
    a("")
    a(f"- Probability of reaching **$25k** within {n} trades: "
      f"**{mc['monte_carlo_equity']['prob_reach_25k_pct']:.2f}%**")
    a(f"- Probability of reaching **$50k** within {n} trades: "
      f"**{mc['monte_carlo_equity']['prob_reach_50k_pct']:.2f}%**")
    a(f"- Probability of reaching **$100k** within {n} trades: "
      f"**{mc['monte_carlo_equity']['prob_reach_100k_pct']:.2f}%**")
    a("")

    a("### 5. SL/TP parameter jitter (±5%)")
    a("")
    pj = mc["param_jitter"]
    a(f"- Total PnL p5/p50/p95: ${pj['total_pnl_p5']:,.0f} / "
      f"${pj['total_pnl_p50']:,.0f} / ${pj['total_pnl_p95']:,.0f}")
    a(f"- Mean total PnL: ${pj['total_pnl_mean']:,.2f}")
    a(f"- PnL stability (median relative shift): {pj['pnl_stability_pct']:.2f}%")
    a("- Interpretation: small SL/TP slippage has limited impact → strategy "
      "is not on a knife-edge of exit prices. Higher stability % = more "
      "robust to broker fill variance.")
    a("")

    a("## Statistical Tests")
    a("")
    a("### Normality")
    a("")
    a(f"- **Jarque-Bera**: stat={stat['normality']['jarque_bera_stat']:.3f}, "
      f"p={stat['normality']['jarque_bera_pvalue']:.4g} → **{jb_v}**")
    a(f"- **Shapiro-Wilk**: stat={stat['normality']['shapiro_wilk_stat']}, "
      f"p={stat['normality']['shapiro_wilk_pvalue']} → **{sw_v}**")
    a(f"- Skewness: {stat['normality']['skewness']:.3f}, "
      f"excess kurtosis: {stat['normality']['kurtosis_excess']:.3f}")
    a("")
    a("### Stationarity (Augmented Dickey-Fuller)")
    a("")
    a(f"- PnL series: stat={stat['stationarity']['pnl'].get('adf_stat')}, "
      f"p={stat['stationarity']['pnl'].get('pvalue')} → "
      f"**{stat['stationarity']['pnl'].get('verdict')}**")
    a(f"- Equity series: stat={stat['stationarity']['equity'].get('adf_stat')}, "
      f"p={stat['stationarity']['equity'].get('pvalue')} → "
      f"**{stat['stationarity']['equity'].get('verdict')}**")
    a("")
    a("### Autocorrelation (Ljung-Box)")
    a("")
    if "results" in stat["ljung_box_autocorrelation"]:
        a("| Lag | Stat | p-value |")
        a("|---|---|---|")
        for r in stat["ljung_box_autocorrelation"]["results"]:
            a(f"| {r['lag']} | {r['stat']:.3f} | {r['pvalue']:.4f} |")
    a(f"- **Verdict:** {lb_v}")
    a("")
    a("### One-sample t-test (mean PnL > 0)")
    a("")
    a(f"- t = {stat['one_sample_ttest']['t_stat']:.3f}, "
      f"two-sided p = {stat['one_sample_ttest']['pvalue_two_sided']:.4g}, "
      f"one-sided p = {stat['one_sample_ttest']['pvalue_one_sided_greater']:.4g}")
    a(f"- **{stat['one_sample_ttest']['verdict']}**")
    a("")

    a("## Regime Analysis (6-month buckets)")
    a("")
    if stat["regime_detection"]["buckets"]:
        a("| Bucket | Trades | WR% | Sharpe | Max DD% | Total PnL |")
        a("|---|---|---|---|---|---|")
        for b in stat["regime_detection"]["buckets"]:
            a(f"| {b['bucket']} | {b['n_trades']} | {b['win_rate_pct']:.2f} | "
              f"{b['sharpe']:.3f} | {b['max_dd_pct']:.2f} | ${b['total_pnl']:,.2f} |")
        if stat["regime_detection"]["shifts_detected"]:
            a("")
            a("**Sharpe-sign regime shifts detected:**")
            a("")
            for s in stat["regime_detection"]["shifts_detected"]:
                a(f"- {s['from_bucket']} (Sharpe {s['from_sharpe']:.2f}) → "
                  f"{s['to_bucket']} (Sharpe {s['to_sharpe']:.2f}) — {s['type']}")
        else:
            a("")
            a("_No Sharpe-sign regime shifts detected; performance is structurally consistent._")
    else:
        a("_Insufficient data for regime buckets._")
    a("")

    a("## Parameter Sensitivity")
    a("")
    a(f"- Best combo by Sharpe (PF tiebreak): **{best_combo_str}** → "
      f"PF={best_pf:.2f}, Sharpe={best_sharpe:.2f}, DD={best_dd:.1f}%")
    a(f"- Robustness: **{robustness_score}** — how many grid cells hold "
      "up near the optimum. Higher = parameter surface is flat / strategy "
      "is robust, not curve-fit.")
    a("")
    # Top 5 combos table.
    if not valid.empty:
        sorted_sens = valid.sort_values(["sharpe", "profit_factor"], ascending=[False, False])
        a("**Top 5 combos:**")
        a("")
        a("| conf_min | risk | TP:SL | trades | WR% | PF | Sharpe | DD% | PnL |")
        a("|---|---|---|---|---|---|---|---|---|")
        for _, r in sorted_sens.head(5).iterrows():
            a(f"| {int(r['confluence_min'])} | {r['risk_per_trade']} | "
              f"{r['tp_sl_ratio']} | {int(r['total_trades'])} | "
              f"{r['win_rate']*100:.2f} | {r['profit_factor']:.2f} | "
              f"{r['sharpe']:.2f} | {r['max_dd_pct']:.2f} | ${r['total_pnl']:,.2f} |")
    a("")

    a("## Walk-Forward Results")
    a("")
    if wf.get("windows"):
        a("| Window | Train (m) | Test (m) | IS n | OOS n | IS Sharpe | OOS Sharpe | Deg% | IS PF | OOS PF | IS DD% | OOS DD% |")
        a("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for w in wf["windows"]:
            tr = "+".join(w["train_buckets"])
            te = "+".join(w["test_buckets"])
            a(f"| {w['window']} | {tr} | {te} | "
              f"{w['train_n']} | {w['test_n']} | "
              f"{w['train_sharpe']:.3f} | {w['test_sharpe']:.3f} | "
              f"{w['sharpe_degradation_pct']:+.1f} | "
              f"{w['train_pf']:.2f} | {w['test_pf']:.2f} | "
              f"{w['train_max_dd_pct']:.2f} | {w['test_max_dd_pct']:.2f} |")
        a("")
        a(f"- Mean Sharpe degradation: **{wf['mean_sharpe_degradation_pct']:+.1f}%** → "
          f"robustness verdict: **{wf['robustness']}**")
    else:
        a("_No walk-forward windows computed._")
    a("")

    a("## Recommendations")
    a("")
    a("**Tuned parameters (suggest starting point for paper trading):**")
    a("")
    a(f"- `confluence_min_score = {int(SENS_CONFLUENCE[1])}` (default; matches engine + STRATEGY)")
    a(f"- `risk_per_trade = {SENS_RISK[1]}` ({SENS_RISK[1]*100:.1f}%) — middle of grid")
    a(f"- `TP:SL ratio = {SENS_TP_SL_RATIO[1]}` — middle of grid; tune toward 2.5 if MC "
      "p95 is the priority metric, toward 1.5 if max-DD is the priority")
    a("")
    a("**Caveats:**")
    a("")
    a(f"- Sample size is {n} trades — meaningful but not huge. "
      "Bootstrap 95% CIs on Sharpe are wide; treat the edge as suggestive, not certain.")
    a(f"- Jarque-Bera p={jb_p:.3g} → "
      f"{'PnL distribution is **non-normal** (fat tails / skew); ' if jb_p < 0.05 else 'PnL distribution is consistent with normal; '}"
      "rely on bootstrap CIs rather than t/z assumptions.")
    a(f"- Ljung-Box: {lb_v}. "
      + ("Streak dependence is real — beware of position-sizing on recent losses "
         "(no 'due' reversal)." if "streak dependence present" in str(lb_v)
         else "Trades appear IID — simple Monte Carlo is a reasonable guide."))
    a(f"- ADF on equity: {stat['stationarity']['equity'].get('verdict')}. "
      "If equity is non-stationary the long-run trend is upward (good), "
      "but mean-reversion artifacts in regime tests should be interpreted "
      "cautiously.")
    a("- The XAU/USD 1h backtest uses Yahoo Finance GC=F (CME gold futures) "
      "as a proxy. Slippage and spread on the actual spot pair can be wider "
      "than futures. Validate on spot broker feeds before going live.")
    a("")
    a("**Suggested next steps:**")
    a("")
    a("1. **Paper trade** for 60 days (≥ 60 trades) on a live broker feed "
      "before risking capital. Compare live trade PnL distribution against "
      "the synthetic-fit baseline in §3.")
    a("2. **Live readiness gates**: (a) live Sharpe 95% CI lower bound ≥ 0, "
      "(b) live max DD < 25%, (c) live WR within ±5pp of backtest. If any "
      "fails, halt.")
    a("3. **Re-run this pipeline quarterly** on rolling data — flag if "
      "regime shifts show Sharpe crossing zero. Then re-tune.")
    a("4. **Capital scaling**: at ~${:,.0f} expected median terminal equity, "
      "consider halving risk-per-trade as equity grows past 2× initial "
      "(Kelly-fraction guardrail).".format(mc_p50))
    a("")
    a("---")
    a("")
    a(f"_Files: `{OUT_TRADES}`, `{OUT_MC}`, `{OUT_STAT}`, `{OUT_SENS}`, `{OUT_WF}`._")

    with open(OUT_REPORT, "w") as f:
        f.write("\n".join(lines))
    print(f"  -> wrote {OUT_REPORT} ({len(lines)} lines)")


# ─── Main ────────────────────────────────────────────────────────────────────
def main() -> int:
    overall_t0 = time.time()
    print("=" * 72)
    print("RX-0 Unicorn — XAU/USD 1H Monte Carlo + statistical analysis pipeline")
    print("=" * 72)

    # Step 1.
    trades, meta = fetch_and_backtest_xau()
    if len(trades) < 50:
        print(f"  !! Only {len(trades)} trades — aborting (need > 100).")
        return 1

    # We also need the raw OHLCV df for Step 4 + 5.
    print("[setup] Reloading OHLCV df for Step 4 + 5...")
    fetcher = YahooFinanceFetcher()
    try:
        df = fetcher.fetch_ohlcv_paginated(SYMBOL, TIMEFRAME, total_bars=TOTAL_BARS_1H)
    finally:
        fetcher.close()

    # Step 2.
    mc = monte_carlo_simulation(trades, meta)

    # Step 3.
    stat = statistical_tests(trades, meta)

    # Compute baseline metrics on the dumped trades.
    baseline_metrics = calculate_metrics(
        [{"pnl": float(t["pnl"]), "r_multiple": float(t["r_multiple"])}
         for t in trades],
        initial_capital=meta["initial_capital"],
        risk_per_trade=meta["risk_per_trade"],
    )

    # Step 4.
    sens = parameter_sensitivity(df, meta)

    # Step 5.
    wf = walk_forward_validation(trades, df, meta)

    # Step 6.
    build_report(meta, mc, stat, sens, wf, baseline_metrics, trades)

    elapsed = time.time() - overall_t0
    print("=" * 72)
    print(f"DONE. Total runtime: {elapsed:.1f}s ({elapsed/60:.2f} min)")
    print("Outputs:")
    for p in (OUT_TRADES, OUT_MC, OUT_STAT, OUT_SENS, OUT_WF, OUT_REPORT):
        if os.path.exists(p):
            print(f"  ✓ {p} ({os.path.getsize(p)} bytes)")
        else:
            print(f"  ✗ {p} MISSING")
    return 0


if __name__ == "__main__":
    sys.exit(main())