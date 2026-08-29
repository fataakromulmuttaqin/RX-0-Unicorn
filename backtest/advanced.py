"""
Advanced backtest methods untuk RX-0 Unicorn.

Methods:
- monte_carlo: randomize trade order → equity distribution
- walk_forward: rolling window out-of-sample validation
- bootstrap: resample with replacement → confidence intervals
- permutation: shuffle P/L → test 'is edge real or luck?'

All methods return standardized results dict so the runner can aggregate.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Any
from dataclasses import dataclass, field, asdict


@dataclass
class BacktestResult:
    """Standardized result from any advanced backtest method."""
    method: str
    initial_capital: float
    final_equity: float
    total_return_pct: float
    sharpe: float
    max_drawdown_pct: float
    win_rate: float
    profit_factor: float
    n_trades: int
    # Method-specific extras
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


# -----------------------------------------------------------------------------
# SHARED: compute_metrics from a sequence of P/L values
# -----------------------------------------------------------------------------
def _compute_metrics(pnls: np.ndarray, initial_capital: float) -> dict[str, float]:
    """Compute standard metrics from an array of per-trade P/L dollar values."""
    if len(pnls) == 0:
        return {
            "final_equity": initial_capital,
            "total_return_pct": 0.0,
            "sharpe": 0.0,
            "max_drawdown_pct": 0.0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "n_trades": 0,
        }

    equity_curve = initial_capital + np.cumsum(pnls)
    final_equity = float(equity_curve[-1])
    total_return = (final_equity / initial_capital - 1.0) * 100.0

    # Max drawdown on equity curve
    peaks = np.maximum.accumulate(equity_curve)
    drawdown = (equity_curve - peaks) / peaks
    max_dd_pct = float(abs(drawdown.min()) * 100.0) if len(drawdown) > 0 else 0.0

    # Win rate
    wins = (pnls > 0).sum()
    win_rate = float(wins / len(pnls))

    # Profit factor
    gross_wins = pnls[pnls > 0].sum()
    gross_losses = abs(pnls[pnls < 0].sum())
    pf = float(gross_wins / gross_losses) if gross_losses > 0 else (10.0 if gross_wins > 0 else 0.0)

    # Sharpe (annualized assuming ~1 trade/day)
    if pnls.std() > 0:
        sharpe = float(pnls.mean() / pnls.std() * np.sqrt(252))
    else:
        sharpe = 0.0

    return {
        "final_equity": final_equity,
        "total_return_pct": total_return,
        "sharpe": sharpe,
        "max_drawdown_pct": max_dd_pct,
        "win_rate": win_rate,
        "profit_factor": min(pf, 99.9),  # cap
        "n_trades": len(pnls),
    }


# -----------------------------------------------------------------------------
# 1. MONTE CARLO — randomize trade order, compute equity distribution
# -----------------------------------------------------------------------------
def monte_carlo(
    trades_pnl: np.ndarray,
    initial_capital: float,
    n_simulations: int = 1000,
    seed: int | None = 42,
) -> BacktestResult:
    """
    Randomly shuffle the order of trades N times.
    For each shuffle, compute final equity. Get distribution.
    Answers: 'What is the range of possible outcomes if I just got lucky/unlucky with order?'
    """
    if seed is not None:
        rng = np.random.default_rng(seed)
    else:
        rng = np.random.default_rng()

    n = len(trades_pnl)
    if n == 0:
        return BacktestResult(
            method="monte_carlo", initial_capital=initial_capital,
            final_equity=initial_capital, total_return_pct=0.0,
            sharpe=0.0, max_drawdown_pct=0.0, win_rate=0.0,
            profit_factor=0.0, n_trades=0,
            extra={"simulations": []},
        )

    final_equities = np.zeros(n_simulations)
    max_drawdowns = np.zeros(n_simulations)
    sharpes = np.zeros(n_simulations)

    for i in range(n_simulations):
        shuffled = rng.permutation(trades_pnl)
        equity = initial_capital + np.cumsum(shuffled)
        final_equities[i] = equity[-1]

        # Max drawdown
        peaks = np.maximum.accumulate(equity)
        dd = (equity - peaks) / peaks
        max_drawdowns[i] = abs(dd.min()) * 100.0

        # Sharpe
        if shuffled.std() > 0:
            sharpes[i] = shuffled.mean() / shuffled.std() * np.sqrt(252)

    # Use the actual chronological order as the "primary" result
    base_metrics = _compute_metrics(trades_pnl, initial_capital)

    return BacktestResult(
        method="monte_carlo",
        initial_capital=initial_capital,
        final_equity=base_metrics["final_equity"],
        total_return_pct=base_metrics["total_return_pct"],
        sharpe=base_metrics["sharpe"],
        max_drawdown_pct=base_metrics["max_drawdown_pct"],
        win_rate=base_metrics["win_rate"],
        profit_factor=base_metrics["profit_factor"],
        n_trades=base_metrics["n_trades"],
        extra={
            "n_simulations": n_simulations,
            "equity_p5": float(np.percentile(final_equities, 5)),
            "equity_p50": float(np.percentile(final_equities, 50)),
            "equity_p95": float(np.percentile(final_equities, 95)),
            "equity_mean": float(final_equities.mean()),
            "equity_std": float(final_equities.std()),
            "equity_min": float(final_equities.min()),
            "equity_max": float(final_equities.max()),
            "prob_profit": float((final_equities > initial_capital).mean()),
            "prob_ruin": float((final_equities < initial_capital * 0.5).mean()),
            "max_dd_p95": float(np.percentile(max_drawdowns, 95)),
            "max_dd_p99": float(np.percentile(max_drawdowns, 99)),
            "final_equities_sample": final_equities[:100].tolist(),
        },
    )


# -----------------------------------------------------------------------------
# 2. WALK FORWARD — rolling window out-of-sample
# -----------------------------------------------------------------------------
def walk_forward(
    trades_pnl: np.ndarray,
    initial_capital: float,
    train_size: int = 30,
    test_size: int = 10,
    step: int | None = None,
) -> BacktestResult:
    """
    Walk-forward analysis: train on N trades, test on next M trades, slide forward.
    Tests if strategy generalizes out-of-sample (not just curve-fit).
    Combines OOS results as the 'real' performance estimate.
    """
    if step is None:
        step = test_size  # non-overlapping windows by default

    n = len(trades_pnl)
    if n < train_size + test_size:
        return BacktestResult(
            method="walk_forward", initial_capital=initial_capital,
            final_equity=initial_capital, total_return_pct=0.0,
            sharpe=0.0, max_drawdown_pct=0.0, win_rate=0.0,
            profit_factor=0.0, n_trades=n,
            extra={"n_windows": 0, "oos_pnl": []},
        )

    oos_pnl = []
    window_results = []
    equity = initial_capital

    i = 0
    while i + train_size + test_size <= n:
        train = trades_pnl[i : i + train_size]
        test = trades_pnl[i + train_size : i + train_size + test_size]

        # Quick train metrics (in-sample, for context)
        train_metrics = _compute_metrics(train, equity)

        # Apply test window to current equity
        for pnl in test:
            oos_pnl.append(pnl)
            equity += pnl

        window_results.append({
            "window": i // step,
            "train_start": i,
            "train_end": i + train_size,
            "test_start": i + train_size,
            "test_end": i + train_size + test_size,
            "train_sharpe": train_metrics["sharpe"],
            "test_return_pct": (test.sum() / equity) * 100.0,
            "test_win_rate": (test > 0).mean(),
        })
        i += step

    oos_arr = np.array(oos_pnl)
    oos_metrics = _compute_metrics(oos_arr, initial_capital)

    return BacktestResult(
        method="walk_forward",
        initial_capital=initial_capital,
        final_equity=oos_metrics["final_equity"],
        total_return_pct=oos_metrics["total_return_pct"],
        sharpe=oos_metrics["sharpe"],
        max_drawdown_pct=oos_metrics["max_drawdown_pct"],
        win_rate=oos_metrics["win_rate"],
        profit_factor=oos_metrics["profit_factor"],
        n_trades=oos_metrics["n_trades"],
        extra={
            "n_windows": len(window_results),
            "windows": window_results,
            "train_size": train_size,
            "test_size": test_size,
            "step": step,
        },
    )


# -----------------------------------------------------------------------------
# 3. BOOTSTRAP — resample with replacement, confidence intervals
# -----------------------------------------------------------------------------
def bootstrap(
    trades_pnl: np.ndarray,
    initial_capital: float,
    n_resamples: int = 1000,
    sample_size: int | None = None,
    seed: int | None = 42,
) -> BacktestResult:
    """
    Resample trades with replacement N times, compute equity for each.
    Answers: 'What's the 95% CI of expected return given this trade distribution?'
    """
    if seed is not None:
        rng = np.random.default_rng(seed)
    else:
        rng = np.random.default_rng()

    n = len(trades_pnl)
    if n == 0:
        return BacktestResult(
            method="bootstrap", initial_capital=initial_capital,
            final_equity=initial_capital, total_return_pct=0.0,
            sharpe=0.0, max_drawdown_pct=0.0, win_rate=0.0,
            profit_factor=0.0, n_trades=0,
            extra={"returns": []},
        )

    if sample_size is None:
        sample_size = n  # same size as original

    final_equities = np.zeros(n_resamples)
    sharpes = np.zeros(n_resamples)
    max_dds = np.zeros(n_resamples)

    for i in range(n_resamples):
        sample = rng.choice(trades_pnl, size=sample_size, replace=True)
        equity = initial_capital + np.cumsum(sample)
        final_equities[i] = equity[-1]

        peaks = np.maximum.accumulate(equity)
        dd = (equity - peaks) / peaks
        max_dds[i] = abs(dd.min()) * 100.0

        if sample.std() > 0:
            sharpes[i] = sample.mean() / sample.std() * np.sqrt(252)

    base_metrics = _compute_metrics(trades_pnl, initial_capital)

    return BacktestResult(
        method="bootstrap",
        initial_capital=initial_capital,
        final_equity=base_metrics["final_equity"],
        total_return_pct=base_metrics["total_return_pct"],
        sharpe=base_metrics["sharpe"],
        max_drawdown_pct=base_metrics["max_drawdown_pct"],
        win_rate=base_metrics["win_rate"],
        profit_factor=base_metrics["profit_factor"],
        n_trades=base_metrics["n_trades"],
        extra={
            "n_resamples": n_resamples,
            "sample_size": sample_size,
            "return_p5": float(np.percentile(final_equities, 5) / initial_capital - 1) * 100,
            "return_p50": float(np.percentile(final_equities, 50) / initial_capital - 1) * 100,
            "return_p95": float(np.percentile(final_equities, 95) / initial_capital - 1) * 100,
            "return_mean": float(final_equities.mean() / initial_capital - 1) * 100,
            "return_std": float(final_equities.std() / initial_capital) * 100,
            "sharpe_p5": float(np.percentile(sharpes, 5)),
            "sharpe_p50": float(np.percentile(sharpes, 50)),
            "sharpe_p95": float(np.percentile(sharpes, 95)),
            "max_dd_p95": float(np.percentile(max_dds, 95)),
            "max_dd_p99": float(np.percentile(max_dds, 99)),
            "final_equities_sample": final_equities[:100].tolist(),
        },
    )


# -----------------------------------------------------------------------------
# 4. PERMUTATION TEST — randomize P/L signs, compare to actual
# -----------------------------------------------------------------------------
def permutation(
    trades_pnl: np.ndarray,
    initial_capital: float,
    n_permutations: int = 1000,
    seed: int | None = 42,
) -> BacktestResult:
    """
    Randomly permute (shuffle) the P/L values of actual trades many times.
    If our actual result is BETTER than 95% of random shuffles, our edge is real.
    Answers: 'Is my performance due to skill, or could I get same with random P/L assignment?'
    """
    if seed is not None:
        rng = np.random.default_rng(seed)
    else:
        rng = np.random.default_rng()

    n = len(trades_pnl)
    if n == 0:
        return BacktestResult(
            method="permutation", initial_capital=initial_capital,
            final_equity=initial_capital, total_return_pct=0.0,
            sharpe=0.0, max_drawdown_pct=0.0, win_rate=0.0,
            profit_factor=0.0, n_trades=0,
            extra={"p_value": 1.0, "is_significant": False},
        )

    # Actual result
    actual_equity = initial_capital + np.cumsum(trades_pnl)
    actual_final = float(actual_equity[-1])
    actual_return_pct = (actual_final / initial_capital - 1) * 100

    # Permute
    random_finals = np.zeros(n_permutations)
    random_sharpes = np.zeros(n_permutations)
    for i in range(n_permutations):
        shuffled = rng.permutation(trades_pnl)
        equity = initial_capital + np.cumsum(shuffled)
        random_finals[i] = equity[-1]
        if shuffled.std() > 0:
            random_sharpes[i] = shuffled.mean() / shuffled.std() * np.sqrt(252)

    # p-value: fraction of random runs that beat our actual
    p_value_final = float((random_finals >= actual_final).mean())
    p_value_sharpe = float((random_sharpes >= base_sharpe(trades_pnl)).mean())

    base_metrics = _compute_metrics(trades_pnl, initial_capital)

    return BacktestResult(
        method="permutation",
        initial_capital=initial_capital,
        final_equity=base_metrics["final_equity"],
        total_return_pct=base_metrics["total_return_pct"],
        sharpe=base_metrics["sharpe"],
        max_drawdown_pct=base_metrics["max_drawdown_pct"],
        win_rate=base_metrics["win_rate"],
        profit_factor=base_metrics["profit_factor"],
        n_trades=base_metrics["n_trades"],
        extra={
            "n_permutations": n_permutations,
            "actual_return_pct": actual_return_pct,
            "p_value_final": p_value_final,
            "p_value_sharpe": p_value_sharpe,
            "is_significant_p05": p_value_final < 0.05,
            "is_significant_p10": p_value_final < 0.10,
            "random_return_mean": float((random_finals / initial_capital - 1).mean() * 100),
            "random_return_p95": float(np.percentile(random_finals / initial_capital - 1, 95) * 100),
            "random_return_p99": float(np.percentile(random_finals / initial_capital - 1, 99) * 100),
            "random_finals_sample": random_finals[:100].tolist(),
        },
    )


def base_sharpe(pnls: np.ndarray) -> float:
    if pnls.std() > 0:
        return float(pnls.mean() / pnls.std() * np.sqrt(252))
    return 0.0
