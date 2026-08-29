"""
Runner: orchestrate the 4 advanced backtest methods on RX-0 Unicorn data.
Output: terminal report + JSON file + chart.
"""
from __future__ import annotations

import sys
import json
import argparse
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np
from loguru import logger

from backtest.advanced import (
    BacktestResult, monte_carlo, walk_forward, bootstrap, permutation
)
from backtest.trade_generator import (
    generate_trades_from_confluence, trades_to_pnl_array
)
from src.config import BACKTEST_INITIAL_CAPITAL, BACKTEST_RISK_PER_TRADE


def format_result(r: BacktestResult) -> str:
    """Format a BacktestResult as a readable text block."""
    lines = [
        "",
        "=" * 70,
        f"  {r.method.upper()}",
        "=" * 70,
        f"  Initial capital : ${r.initial_capital:,.2f}",
        f"  Final equity    : ${r.final_equity:,.2f}",
        f"  Total return    : {r.total_return_pct:+.2f}%",
        f"  Trades          : {r.n_trades}",
        f"  Win rate        : {r.win_rate*100:.1f}%",
        f"  Profit factor   : {r.profit_factor:.2f}",
        f"  Sharpe ratio    : {r.sharpe:.2f}",
        f"  Max drawdown    : {r.max_drawdown_pct:.2f}%",
    ]

    # Method-specific
    e = r.extra
    if r.method == "monte_carlo":
        lines += [
            "",
            f"  ── Monte Carlo ({e.get('n_simulations', '?')} simulations) ──",
            f"  Final equity 5th percentile : ${e.get('equity_p5', 0):,.2f}",
            f"  Final equity median         : ${e.get('equity_p50', 0):,.2f}",
            f"  Final equity 95th percentile: ${e.get('equity_p95', 0):,.2f}",
            f"  Final equity mean ± std     : ${e.get('equity_mean', 0):,.2f} ± ${e.get('equity_std', 0):,.2f}",
            f"  Final equity min/max        : ${e.get('equity_min', 0):,.2f} / ${e.get('equity_max', 0):,.2f}",
            f"  P(equity > initial)         : {e.get('prob_profit', 0)*100:.1f}%",
            f"  P(equity < 50% initial)     : {e.get('prob_ruin', 0)*100:.1f}% (ruin risk)",
            f"  Max drawdown 95th pct       : {e.get('max_dd_p95', 0):.2f}%",
            f"  Max drawdown 99th pct       : {e.get('max_dd_p99', 0):.2f}%",
        ]
    elif r.method == "walk_forward":
        lines += [
            "",
            f"  ── Walk Forward ──",
            f"  Train size / Test size / Step: {e.get('train_size', 0)} / {e.get('test_size', 0)} / {e.get('step', 0)}",
            f"  Number of OOS windows        : {e.get('n_windows', 0)}",
            f"  Out-of-sample trades         : {r.n_trades}",
        ]
        if e.get("windows"):
            lines.append("  Per-window results:")
            for w in e["windows"][:10]:  # show first 10
                lines.append(
                    f"    Win {w['window']:3d} | train [{w['train_start']:3d}:{w['train_end']:3d}] "
                    f"test [{w['test_start']:3d}:{w['test_end']:3d}] | "
                    f"train_sharpe={w['train_sharpe']:+.2f} test_ret={w['test_return_pct']:+.2f}% test_wr={w['test_win_rate']*100:.0f}%"
                )
    elif r.method == "bootstrap":
        lines += [
            "",
            f"  ── Bootstrap ({e.get('n_resamples', '?')} resamples) ──",
            f"  Sample size              : {e.get('sample_size', '?')}",
            f"  Return 5th percentile    : {e.get('return_p5', 0):+.2f}%",
            f"  Return median            : {e.get('return_p50', 0):+.2f}%",
            f"  Return 95th percentile   : {e.get('return_p95', 0):+.2f}%",
            f"  Return mean ± std        : {e.get('return_mean', 0):+.2f}% ± {e.get('return_std', 0):.2f}%",
            f"  Sharpe 5/50/95 percentile: {e.get('sharpe_p5', 0):.2f} / {e.get('sharpe_p50', 0):.2f} / {e.get('sharpe_p95', 0):.2f}",
            f"  Max DD 95th/99th pct     : {e.get('max_dd_p95', 0):.2f}% / {e.get('max_dd_p99', 0):.2f}%",
        ]
    elif r.method == "permutation":
        lines += [
            "",
            f"  ── Permutation Test ({e.get('n_permutations', '?')} perms) ──",
            f"  Actual return             : {e.get('actual_return_pct', 0):+.2f}%",
            f"  Random return mean        : {e.get('random_return_mean', 0):+.2f}%",
            f"  Random return 95th pct    : {e.get('random_return_p95', 0):+.2f}%",
            f"  Random return 99th pct    : {e.get('random_return_p99', 0):+.2f}%",
            f"  p-value (final equity)    : {e.get('p_value_final', 1.0):.4f}",
            f"  p-value (sharpe)          : {e.get('p_value_sharpe', 1.0):.4f}",
            f"  Significant @ 5%?         : {'✅ YES' if e.get('is_significant_p05') else '❌ NO'}",
            f"  Significant @ 10%?        : {'✅ YES' if e.get('is_significant_p10') else '❌ NO'}",
        ]

    return "\n".join(lines)


def compare_methods(results: list[BacktestResult]) -> str:
    """Cross-method comparison table."""
    lines = [
        "",
        "=" * 70,
        "  CROSS-METHOD COMPARISON",
        "=" * 70,
        f"  {'Method':<18} {'Return %':>10} {'Win %':>8} {'PF':>6} {'Sharpe':>8} {'MaxDD %':>10} {'Trades':>8}",
        "  " + "-" * 68,
    ]
    for r in results:
        lines.append(
            f"  {r.method:<18} {r.total_return_pct:>9.2f}% {r.win_rate*100:>7.1f}% "
            f"{r.profit_factor:>6.2f} {r.sharpe:>8.2f} {r.max_drawdown_pct:>9.2f}% {r.n_trades:>8d}"
        )

    # Verdict
    lines += [
        "",
        "  ── Statistical Verdict ──",
    ]
    if len(results) == 4:
        mc, wf, bs, pm = results
        # Significance (permutation p-value based on Sharpe, not just equity)
        p_val = pm.extra.get("p_value_sharpe", 1.0)
        is_sig = p_val < 0.05
        # Robustness
        wf_positive = wf.total_return_pct > 0
        bs_pos = bs.extra.get("return_p50", 0) > 0
        mc_pos = mc.extra.get("prob_profit", 0) > 0.5

        sig_text = "✅ STATISTICALLY SIGNIFICANT (p<0.05)" if is_sig else (
            "⚠️ MARGINALLY SIGNIFICANT (p<0.10)" if p_val < 0.10 else
            "❌ NOT SIGNIFICANT — could be luck"
        )
        lines.append(f"  Edge real?       : {sig_text} (Sharpe p={p_val:.4f})")
        lines.append(f"  Out-of-sample?   : {'✅' if wf_positive else '❌'} Walk-forward OOS positive ({wf.total_return_pct:+.2f}%)")
        lines.append(f"  Bootstrap robust?: {'✅' if bs_pos else '❌'} Median bootstrap return positive ({bs.extra.get('return_p50', 0):+.2f}%)")
        lines.append(f"  Monte Carlo?     : {'✅' if mc_pos else '❌'} P(profit) > 50% ({mc.extra.get('prob_profit', 0)*100:.1f}%)")

        # Overall grade
        # 4 pillars: stat sig, OOS positive, bootstrap robust, MC profit prob
        pillars_pass = sum([is_sig, wf_positive, bs_pos, mc_pos])
        pnl_positive = bs.extra.get("return_p50", 0) > 0 and wf.total_return_pct > 0
        if pillars_pass >= 3 and pnl_positive:
            verdict = "🟢 EXCELLENT — strategy likely profitable, ready for paper trading"
        elif pillars_pass >= 2 and pnl_positive:
            verdict = "🟡 PROMISING — partial signal, more data needed"
        else:
            verdict = "🔴 WEAK — not enough edge to trade"

        lines.append("")
        lines.append(f"  OVERALL VERDICT  : {verdict}")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Advanced backtest (monte carlo, walk forward, bootstrap, permutation)")
    parser.add_argument("--capital", type=float, default=100.0, help="Initial capital (default $100)")
    parser.add_argument("--risk", type=float, default=0.02, help="Risk per trade (default 2%)")
    parser.add_argument("--simulations", type=int, default=1000, help="MC/Bootstrap/Permutation iterations")
    parser.add_argument("--train-size", type=int, default=15, help="Walk-forward train window")
    parser.add_argument("--test-size", type=int, default=5, help="Walk-forward test window")
    parser.add_argument("--output", type=str, default="backtest/results/advanced_backtest.json", help="JSON output path")
    args = parser.parse_args()

    logger.info("=" * 70)
    logger.info(f"RX-0 Unicorn — Advanced Backtest")
    logger.info(f"  Initial capital: ${args.capital}")
    logger.info(f"  Risk/trade:     {args.risk*100:.1f}%")
    logger.info(f"  Simulations:    {args.simulations}")
    logger.info("=" * 70)

    # 1. Generate trades
    logger.info("\n📊 Step 1: Generating trades from confluence engine...")
    trades = generate_trades_from_confluence(
        initial_capital=args.capital,
        risk_per_trade=args.risk,
    )
    pnls = trades_to_pnl_array(trades)

    # If 0 trades (flat market), auto-lower threshold to min_score=2 to get data
    fallback_used = False
    if len(trades) == 0:
        logger.warning("  → 0 trades at default threshold (score>=3). Lowering to score>=2 for analysis...")
        trades = generate_trades_from_confluence(
            initial_capital=args.capital,
            risk_per_trade=args.risk,
            min_score=2,
        )
        pnls = trades_to_pnl_array(trades)
        fallback_used = True

    logger.info(f"  → Generated {len(trades)} trades")
    if len(trades) > 0:
        wins = sum(1 for t in trades if t["pnl"] > 0)
        losses = len(trades) - wins
        total_pnl = sum(t["pnl"] for t in trades)
        logger.info(f"  → Wins: {wins} | Losses: {losses} | Win rate: {wins/len(trades)*100:.1f}%")
        logger.info(f"  → Total P/L: ${total_pnl:+.2f}")
        logger.info(f"  → Per-trade avg: ${total_pnl/len(trades):+.2f}")
        # Show first 5
        logger.info("  → First 5 trades:")
        for t in trades[:5]:
            emoji = "🟢" if t["pnl"] > 0 else "🔴"
            logger.info(f"     {emoji} {t['symbol']:12s} {t['direction']:5s} entry={t['entry_price']:.4f} exit={t['exit_price']:.4f} reason={t['exit_reason']} pnl=${t['pnl']:+.2f}")
    else:
        logger.warning("  → No trades generated! Market may be too flat for this short window.")
        logger.warning("  → Advanced methods will return degenerate results.")
        return 1

    # 2. Run 4 methods
    logger.info("\n🔬 Step 2: Running 4 advanced backtest methods...")
    results = []

    logger.info("  [1/4] Monte Carlo...")
    mc = monte_carlo(pnls, args.capital, n_simulations=args.simulations, seed=42)
    results.append(mc)

    logger.info("  [2/4] Walk Forward...")
    wf = walk_forward(pnls, args.capital, train_size=args.train_size, test_size=args.test_size)
    results.append(wf)

    logger.info("  [3/4] Bootstrap...")
    bs = bootstrap(pnls, args.capital, n_resamples=args.simulations, seed=42)
    results.append(bs)

    logger.info("  [4/4] Permutation Test...")
    pm = permutation(pnls, args.capital, n_permutations=args.simulations, seed=42)
    results.append(pm)

    # 3. Print results
    for r in results:
        print(format_result(r))
    print(compare_methods(results))

    # 4. Save JSON
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump({
            "config": {
                "initial_capital": args.capital,
                "risk_per_trade": args.risk,
                "n_simulations": args.simulations,
                "train_size": args.train_size,
                "test_size": args.test_size,
            },
            "trades_count": len(trades),
            "results": [r.to_dict() for r in results],
        }, f, indent=2, default=str)
    logger.info(f"\n💾 Results saved to: {output_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
