"""
Backtest report — text, JSON, dan equity curve chart.

Tiga output:

    1. format_report(...)   — str tabel ASCII untuk CLI
    2. to_json(...)         — simpan dict metrics + trades ke JSON
    3. to_equity_curve_chart(...) — PNG equity curve via matplotlib
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from backtest.metrics import (
    PROFIT_FACTOR_CAP,
    calculate_metrics,
    empty_metrics,
    target_check,
)
from src.config import (
    BACKTEST_INITIAL_CAPITAL,
    BACKTEST_OUTPUT_DIR,
    TARGET_AVG_R_MULTIPLE,
    TARGET_MAX_DRAWDOWN,
    TARGET_PROFIT_FACTOR,
    TARGET_SHARPE,
    TARGET_WIN_RATE,
)
from src.logger import logger


def _ts_to_iso(ts_ms: int) -> str:
    if ts_ms is None or ts_ms <= 0:
        return "N/A"
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def _fmt_pct(value: float, digits: int = 2) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "N/A"
    return f"{value:.{digits}f}%"


def _fmt_money(value: float, digits: int = 2) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "N/A"
    return f"${value:,.{digits}f}"


def _check_mark(passed: bool) -> str:
    return "✓" if passed else "✗"


def format_report(
    symbol: str,
    timeframe: str,
    metrics: dict,
    trades: list[dict],
    *,
    period: tuple[int, int] | None = None,
    initial_capital: float = BACKTEST_INITIAL_CAPITAL,
    risk_per_trade: float = 0.02,
) -> str:
    """
    Format hasil backtest jadi tabel ASCII multi-section.

    Args:
        symbol: e.g. 'BTC/USDT'.
        timeframe: e.g. '1h'.
        metrics: Dict dari calculate_metrics() (atau empty_metrics()).
        trades: List of trade dicts (untuk top-5 / worst-5).
        period: (start_ts_ms, end_ts_ms) optional.
        initial_capital: Modal awal (untuk label di header).
        risk_per_trade: Risk per trade fraksi (untuk label).

    Returns:
        String siap cetak.
    """
    if metrics is None:
        metrics = empty_metrics()
    targets = target_check(metrics)

    start_ts, end_ts = period if period else (None, None)

    lines: list[str] = []
    lines.append("=" * 78)
    lines.append(f"RX-0 Unicorn — Backtest Report — {symbol} ({timeframe})")
    lines.append("=" * 78)
    lines.append(f"Period           : {_ts_to_iso(start_ts)} -> {_ts_to_iso(end_ts)}")
    lines.append(f"Initial capital  : {_fmt_money(initial_capital)}")
    lines.append(f"Risk per trade   : {risk_per_trade * 100:.2f}%")
    lines.append("-" * 78)

    # Headline metrics
    n = metrics.get("total_trades", 0)
    lines.append("OVERVIEW")
    lines.append(f"  Total trades   : {n}")
    lines.append(f"  Wins / Losses  : {metrics.get('wins', 0)} / {metrics.get('losses', 0)}")
    lines.append(f"  Total PnL      : {_fmt_money(metrics.get('total_pnl', 0.0))}")
    lines.append(f"  Equity final   : {_fmt_money(metrics.get('equity_final', initial_capital))}")
    lines.append("-" * 78)

    # 6 mandatory metrics
    pf_disp = metrics.get("profit_factor", 0.0)
    if pf_disp >= PROFIT_FACTOR_CAP:
        pf_disp_str = f">{PROFIT_FACTOR_CAP:.0f}"
    else:
        pf_disp_str = f"{pf_disp:.2f}"

    lines.append("6 MANDATORY METRICS (vs STRATEGY.md targets)")
    lines.append(
        f"  {_check_mark(targets['win_rate'])} Win Rate        : "
        f"{metrics.get('win_rate', 0.0) * 100:6.2f}%  "
        f"(target > {TARGET_WIN_RATE * 100:.0f}%)"
    )
    lines.append(
        f"  {_check_mark(targets['profit_factor'])} Profit Factor   : "
        f"{pf_disp_str:>6}      "
        f"(target > {TARGET_PROFIT_FACTOR:.1f})"
    )
    lines.append(
        f"  {_check_mark(targets['max_drawdown'])} Max Drawdown    : "
        f"{metrics.get('max_drawdown_pct', 0.0):6.2f}%  "
        f"(target < {TARGET_MAX_DRAWDOWN * 100:.0f}%)"
    )
    lines.append(
        f"  {_check_mark(targets['sharpe_ratio'])} Sharpe Ratio    : "
        f"{metrics.get('sharpe_ratio', 0.0):6.3f}    "
        f"(target > {TARGET_SHARPE:.1f})"
    )
    lines.append(
        f"  {_check_mark(targets['avg_r_multiple'])} Avg R-Multiple  : "
        f"{metrics.get('avg_r_multiple', 0.0):6.3f}R   "
        f"(target > {TARGET_AVG_R_MULTIPLE:.1f}R)"
    )
    lines.append(
        f"  {_check_mark(targets['expectancy'])} Expectancy      : "
        f"{_fmt_money(metrics.get('expectancy', 0.0))}   "
        f"(target > $0.00)"
    )
    lines.append("-" * 78)

    # Stats tambahan
    lines.append("TRADE STATS")
    lines.append(f"  Avg win         : {_fmt_money(metrics.get('avg_win', 0.0))}")
    lines.append(f"  Avg loss        : {_fmt_money(metrics.get('avg_loss', 0.0))}")
    lines.append(f"  Largest win     : {_fmt_money(metrics.get('largest_win', 0.0))}")
    lines.append(f"  Largest loss    : {_fmt_money(metrics.get('largest_loss', 0.0))}")
    lines.append("-" * 78)

    # Top 5 wins & worst 5 losses
    if trades:
        sorted_by_pnl = sorted(trades, key=lambda t: float(t.get("pnl", 0.0)), reverse=True)
        top5 = sorted_by_pnl[:5]
        worst5 = sorted_by_pnl[-5:][::-1]  # least profitable first

        lines.append("TOP 5 WINS")
        lines.append(
            f"  {'#':<3} {'Entry Time':<20}{'Dir':<6}{'Entry':>12}{'Exit':>12}"
            f"{'PnL':>12}  {'R':>6}  {'Reason':<10}"
        )
        for idx, t in enumerate(top5, start=1):
            lines.append(
                f"  {idx:<3} {_ts_to_iso(t.get('entry_time', 0)):<20}"
                f"{t.get('direction', '?'):<6}"
                f"{float(t.get('entry_price', 0.0)):>12.4f}"
                f"{float(t.get('exit_price', 0.0)):>12.4f}"
                f"{float(t.get('pnl', 0.0)):>12.2f}"
                f"  {float(t.get('r_multiple', 0.0)):>5.2f}R"
                f"  {t.get('exit_reason', '?'):<10}"
            )
        lines.append("")

        lines.append("WORST 5 LOSSES")
        lines.append(
            f"  {'#':<3} {'Entry Time':<20}{'Dir':<6}{'Entry':>12}{'Exit':>12}"
            f"{'PnL':>12}  {'R':>6}  {'Reason':<10}"
        )
        for idx, t in enumerate(worst5, start=1):
            lines.append(
                f"  {idx:<3} {_ts_to_iso(t.get('entry_time', 0)):<20}"
                f"{t.get('direction', '?'):<6}"
                f"{float(t.get('entry_price', 0.0)):>12.4f}"
                f"{float(t.get('exit_price', 0.0)):>12.4f}"
                f"{float(t.get('pnl', 0.0)):>12.2f}"
                f"  {float(t.get('r_multiple', 0.0)):>5.2f}R"
                f"  {t.get('exit_reason', '?'):<10}"
            )
        lines.append("-" * 78)
    else:
        lines.append("(no trades to display)")
        lines.append("-" * 78)

    # Verdict
    passed = sum(1 for v in targets.values() if v)
    total = len(targets)
    lines.append(
        f"VERDICT: {passed}/{total} metrics met target"
    )
    if passed == total:
        lines.append("  🦄 Strategy passes all targets — ready for paper trading")
    elif passed >= total - 1:
        lines.append("  ⚠️  Strategy almost passes — review 1 missing metric")
    else:
        lines.append("  ❌ Strategy below threshold — tune indicators / parameters")
    lines.append("=" * 78)
    return "\n".join(lines)


def to_json(
    metrics: dict,
    output_path: str | Path,
    *,
    metadata: dict | None = None,
    trades: list[dict] | None = None,
) -> Path:
    """
    Simpan metrics + metadata + trades ke JSON.

    Args:
        metrics: Dict dari calculate_metrics().
        output_path: Path absolut atau relatif.
        metadata: Dict tambahan (symbol, timeframe, dll).
        trades: List trade dicts (opsional, default ambil dari metrics['trades']).

    Returns:
        Path absolut file yang ditulis.
    """
    if metrics is None:
        metrics = empty_metrics()
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    payload: dict[str, Any] = {
        "metadata": metadata or {},
        "metrics": metrics,
        "targets": target_check(metrics),
    }
    if trades is None:
        trades = metrics.get("trades", [])
    payload["trades"] = trades

    with out_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
    logger.success(f"Backtest JSON saved: {out_path}")
    return out_path.resolve()


def to_equity_curve_chart(
    metrics: dict,
    output_path: str | Path,
    *,
    title: str = "RX-0 Unicorn — Equity Curve",
    initial_capital: float = BACKTEST_INITIAL_CAPITAL,
) -> Path:
    """
    Render equity curve PNG via matplotlib.

    Args:
        metrics: Dict metrics (butuh key 'equity_curve' list of cumulative pnl).
        output_path: Path file PNG.
        title: Judul chart.
        initial_capital: Modal awal (untuk titik start).

    Returns:
        Path absolut file PNG.
    """
    if metrics is None:
        metrics = empty_metrics()

    # matplotlib import di-scope agar module load tetap ringan kalau
    # user cuma butuh JSON / text report.
    import matplotlib

    matplotlib.use("Agg")  # non-GUI backend
    import matplotlib.pyplot as plt

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    eq_pnl: list[float] = list(metrics.get("equity_curve") or [])
    if not eq_pnl:
        # Empty plot
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.text(0.5, 0.5, "No trades — equity curve empty",
                ha="center", va="center", transform=ax.transAxes)
        ax.set_title(title)
        fig.tight_layout()
        fig.savefig(out_path, dpi=120)
        plt.close(fig)
        logger.info(f"Equity chart saved (empty): {out_path}")
        return out_path.resolve()

    equity = [initial_capital + float(x) for x in eq_pnl]
    x = list(range(1, len(equity) + 1))

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(x, equity, color="#1f77b4", linewidth=1.8, label="Equity")
    ax.axhline(initial_capital, color="gray", linestyle="--", linewidth=0.8,
               label=f"Start (${initial_capital:,.0f})")

    # Highlight max drawdown
    eq_arr = np.asarray(equity)
    peak = np.maximum.accumulate(eq_arr)
    drawdown_pct = (eq_arr - peak) / peak * 100.0
    if len(drawdown_pct) and drawdown_pct.min() < 0:
        worst_idx = int(np.argmin(drawdown_pct))
        ax.scatter([worst_idx + 1], [equity[worst_idx]], color="red", zorder=5,
                   label=f"Max DD {drawdown_pct[worst_idx]:.2f}%")

    ax.set_title(title)
    ax.set_xlabel("Trade #")
    ax.set_ylabel("Equity (USD)")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="best")
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    logger.success(f"Equity chart saved: {out_path}")
    return out_path.resolve()


__all__ = [
    "format_report",
    "to_equity_curve_chart",
    "to_json",
]


# --- Convenience -------------------------------------------------------------
def build_full_report_payload(
    result,  # type: ignore[type-arg]
    metrics: dict,
    *,
    extra: dict | None = None,
) -> dict:
    """
    Helper kecil: gabungkan BacktestResult + metrics + extra metadata
    jadi satu dict siap-simpan ke JSON.
    """
    return {
        "result": result.to_dict() if hasattr(result, "to_dict") else dict(result),
        "metrics": metrics,
        "extra": extra or {},
    }
