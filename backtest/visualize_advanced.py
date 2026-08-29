"""
Generate matplotlib charts from advanced backtest JSON output.
Creates:
- equity_curves.png (MC, bootstrap, permutation distributions)
- walk_forward_windows.png
- method_comparison.png
"""
from __future__ import annotations

import sys
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def visualize_advanced_backtest(json_path: str, output_dir: str = "backtest/results"):
    """Load advanced backtest JSON and create visualization charts."""
    with open(json_path) as f:
        data = json.load(f)

    results = {r["method"]: r for r in data["results"]}
    initial_capital = data["config"]["initial_capital"]

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # ============================================================
    # CHART 1: Distribution comparison (MC vs Bootstrap vs Permutation)
    # ============================================================
    fig, axes = plt.subplots(1, 3, figsize=(16, 5))
    fig.suptitle(
        f"RX-0 Unicorn — Advanced Backtest Distributions (n_simulations={data['config']['n_simulations']})",
        fontsize=14, fontweight="bold",
    )

    # MC
    ax = axes[0]
    if "monte_carlo" in results:
        eq = np.array(results["monte_carlo"]["extra"]["final_equities_sample"])
        # If all values are same (sum-invariant), show as vertical line + note
        if eq.std() < 0.01:
            ax.text(0.5, 0.5,
                    f"Final equity is invariant\n(sum of P/L is order-independent)\n\nActual: ${eq[0]:.2f}",
                    ha="center", va="center", transform=ax.transAxes, fontsize=11,
                    bbox=dict(boxstyle="round", facecolor="lightyellow", alpha=0.7))
            ax.set_title("Monte Carlo\n(Order does not affect final equity)")
        else:
            ax.hist(eq, bins=min(30, max(5, len(np.unique(eq)))), color="#5d8fff", alpha=0.7, edgecolor="black")
            ax.axvline(initial_capital, color="red", linestyle="--", label="Initial")
            ax.axvline(results["monte_carlo"]["final_equity"], color="green", linestyle="-", label="Actual")
            ax.set_title(f"Monte Carlo\nμ=${eq.mean():.2f} std=${eq.std():.2f}")
            ax.legend(fontsize=8)
        ax.set_xlabel("Final Equity ($)")
        ax.set_ylabel("Frequency")
        ax.grid(alpha=0.3)

    # Bootstrap
    ax = axes[1]
    if "bootstrap" in results:
        eq = np.array(results["bootstrap"]["extra"]["final_equities_sample"])
        ret_pct = (eq / initial_capital - 1) * 100
        ax.hist(ret_pct, bins=min(30, max(5, len(np.unique(ret_pct)))),
                color="#00ffd5", alpha=0.7, edgecolor="black")
        ax.axvline(0, color="red", linestyle="--", label="Breakeven")
        ax.axvline(results["bootstrap"]["total_return_pct"], color="green", linestyle="-", label="Actual")
        ax.set_title(f"Bootstrap\nμ={ret_pct.mean():.2f}%  median={np.median(ret_pct):.2f}%")
        ax.set_xlabel("Return (%)")
        ax.set_ylabel("Frequency")
        ax.legend(fontsize=8)
        ax.grid(alpha=0.3)

    # Permutation
    ax = axes[2]
    if "permutation" in results:
        eq = np.array(results["permutation"]["extra"]["random_finals_sample"])
        ret_pct = (eq / initial_capital - 1) * 100
        actual = results["permutation"]["extra"]["actual_return_pct"]
        pval = results["permutation"]["extra"]["p_value_sharpe"]
        ax.hist(ret_pct, bins=min(30, max(5, len(np.unique(ret_pct)))),
                color="#ffd166", alpha=0.7, edgecolor="black")
        ax.axvline(actual, color="green", linestyle="-", linewidth=2, label=f"Actual ({actual:+.2f}%)")
        ax.set_title(f"Permutation Test\np-value (Sharpe)={pval:.4f}")
        ax.set_xlabel("Return (%)")
        ax.set_ylabel("Frequency")
        ax.legend(fontsize=8)
        ax.grid(alpha=0.3)

    plt.tight_layout()
    chart1 = out / "advanced_distributions.png"
    plt.savefig(chart1, dpi=100, bbox_inches="tight")
    plt.close()
    print(f"✅ Saved: {chart1}")

    # ============================================================
    # CHART 2: Walk-forward window-by-window
    # ============================================================
    if "walk_forward" in results:
        wf = results["walk_forward"]
        windows = wf["extra"].get("windows", [])
        if windows:
            fig, ax = plt.subplots(figsize=(12, 5))
            test_rets = [w["test_return_pct"] for w in windows]
            test_wrs = [w["test_win_rate"] * 100 for w in windows]
            x = range(len(windows))

            ax.bar(x, test_rets, color=["green" if r > 0 else "red" for r in test_rets], alpha=0.7)
            ax.axhline(0, color="black", linewidth=0.5)
            ax.set_xlabel("Window #")
            ax.set_ylabel("OOS Return (%)", color="darkgreen")
            ax.tick_params(axis="y", labelcolor="darkgreen")
            ax2 = ax.twinx()
            ax2.plot(x, test_wrs, "o-", color="blue", label="OOS Win Rate %")
            ax2.set_ylabel("OOS Win Rate (%)", color="blue")
            ax2.tick_params(axis="y", labelcolor="blue")
            ax2.set_ylim(0, 100)
            ax.set_title(
                f"Walk-Forward OOS Performance ({wf['extra']['n_windows']} windows, "
                f"train={wf['extra']['train_size']}, test={wf['extra']['test_size']})"
            )
            ax.grid(alpha=0.3)
            plt.tight_layout()
            chart2 = out / "walk_forward.png"
            plt.savefig(chart2, dpi=100, bbox_inches="tight")
            plt.close()
            print(f"✅ Saved: {chart2}")

    # ============================================================
    # CHART 3: Method comparison bar chart
    # ============================================================
    fig, ax = plt.subplots(figsize=(10, 6))
    methods = [r["method"] for r in data["results"]]
    returns = [r["total_return_pct"] for r in data["results"]]
    sharpes = [r["sharpe"] for r in data["results"]]

    x = np.arange(len(methods))
    width = 0.35
    ax.bar(x - width/2, returns, width, label="Return %", color="#5d8fff", alpha=0.8)
    ax.bar(x + width/2, sharpes, width, label="Sharpe", color="#00ffd5", alpha=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels(methods, rotation=15, ha="right")
    ax.set_ylabel("Value")
    ax.set_title("Cross-Method Comparison")
    ax.axhline(0, color="black", linewidth=0.5)
    ax.legend()
    ax.grid(alpha=0.3)
    plt.tight_layout()
    chart3 = out / "method_comparison.png"
    plt.savefig(chart3, dpi=100, bbox_inches="tight")
    plt.close()
    print(f"✅ Saved: {chart3}")

    print(f"\nAll charts saved to: {out}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Visualize advanced backtest results")
    parser.add_argument("--input", default="backtest/results/advanced_backtest.json")
    parser.add_argument("--output-dir", default="backtest/results")
    args = parser.parse_args()
    visualize_advanced_backtest(args.input, args.output_dir)
