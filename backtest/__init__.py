"""
Phase 5 — Backtest Engine untuk RX-0 Unicorn.

Modul ini memvalidasi strategi 4-indikator confluence (Phase 2+3) terhadap
data historis dan menghitung 6 metrics wajib dari STRATEGY.md:

    1. Win Rate        > 50%
    2. Profit Factor   > 1.5
    3. Max Drawdown    < 20%
    4. Sharpe Ratio    > 1.5
    5. Avg R-Multiple  > 1.5R
    6. Expectancy      > 0

Komponen:
    data_loader   — DB-first, fallback ke CCXT bila candle belum cukup
    engine        — walk-forward simulation, no look-ahead
    metrics       — kalkulasi 6 metrics + edge cases (0 trades, all-wins/losses)
    report        — text/JSON/equity-curve output
"""

from backtest.data_loader import ensure_data
from backtest.engine import (
    BacktestResult,
    Trade,
    run_backtest,
    simulate_trade,
)
from backtest.metrics import (
    calculate_metrics,
    empty_metrics,
)
from backtest.report import (
    format_report,
    to_equity_curve_chart,
    to_json,
)

__all__ = [
    "BacktestResult",
    "Trade",
    "calculate_metrics",
    "empty_metrics",
    "ensure_data",
    "format_report",
    "run_backtest",
    "simulate_trade",
    "to_equity_curve_chart",
    "to_json",
]
