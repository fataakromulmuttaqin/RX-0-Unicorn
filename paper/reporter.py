"""
Paper Reporter — text + chart report generator (Phase 6).

Provides:
    generate_report(journal, days_back) -> str
    generate_equity_chart(journal, days_back, output_path) -> str
    phase7_readiness(metrics, total_trades) -> dict
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.config import (
    PAPER_PHASE7_MAX_DRAWDOWN,
    PAPER_PHASE7_MIN_PROFIT_FACTOR,
    PAPER_PHASE7_MIN_TRADES,
    PAPER_PHASE7_WIN_RATE_TOLERANCE,
    PAPER_REPORTS_DIR,
    TARGET_AVG_R_MULTIPLE,
    TARGET_MAX_DRAWDOWN,
    TARGET_PROFIT_FACTOR,
    TARGET_WIN_RATE,
)
from src.logger import logger

from .journal import PaperJournal, _summarize_trades


def _fmt(x: float | None, fmt: str = "{:,.2f}") -> str:
    if x is None:
        return "N/A"
    return fmt.format(float(x))


def _pass(ok: bool) -> str:
    return "✅" if ok else "❌"


def _bar(value: float, max_value: float = 1.0, width: int = 20) -> str:
    if max_value <= 0:
        return " " * width
    fill = int(round((value / max_value) * width))
    fill = max(0, min(width, fill))
    return "█" * fill + "░" * (width - fill)


def generate_report(
    journal: PaperJournal,
    *,
    days_back: int = 7,
) -> str:
    """
    Build a human-readable report covering the last `days_back` days.
    Includes: total trades, win rate, PF, P/L, drawdown, top winners/
    losers, plus a Phase 7 readiness check.
    """
    if days_back <= 0:
        days_back = 7
    metrics = journal.aggregate_performance(days_back=days_back)
    closed = journal.get_closed_trades(days_back=days_back)
    open_positions = journal.get_open_positions()
    state_rows = journal.get_daily_history()

    lines: list[str] = []
    lines.append("=" * 70)
    lines.append(f"RX-0 Unicorn — Paper Trading Report (last {days_back} days)")
    lines.append("=" * 70)
    lines.append(f"Generated        : {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"DB               : {journal.db_path}")
    lines.append("-" * 70)

    # Headline metrics
    total = int(metrics.get("total_trades", 0))
    wins = int(metrics.get("wins", 0))
    losses = int(metrics.get("losses", 0))
    win_rate = float(metrics.get("win_rate", 0))
    pf = float(metrics.get("profit_factor", 0))
    total_pnl = float(metrics.get("total_pnl", 0))
    max_dd = float(metrics.get("max_drawdown_pct", 0))
    avg_r = float(metrics.get("avg_r_multiple", 0))
    sharpe = float(metrics.get("sharpe_ratio", 0))
    expectancy = float(metrics.get("expectancy", 0))
    largest_win = float(metrics.get("largest_win", 0))
    largest_loss = float(metrics.get("largest_loss", 0))

    lines.append("PERFORMANCE SUMMARY")
    lines.append(f"  Total trades     : {total}")
    lines.append(f"  Wins / Losses    : {wins} / {losses}")
    lines.append(f"  Win rate         : {win_rate * 100:.1f}%   "
                 f"{_bar(win_rate)}   target ≥ {TARGET_WIN_RATE * 100:.0f}%")
    lines.append(f"  Profit factor    : {pf:.2f}   "
                 f"{_bar(min(pf, 3) / 3)}   target ≥ {TARGET_PROFIT_FACTOR}")
    lines.append(f"  Total P/L        : ${total_pnl:+,.2f}")
    lines.append(f"  Avg R-multiple   : {avg_r:+.2f}R   "
                 f"target ≥ {TARGET_AVG_R_MULTIPLE}R")
    lines.append(f"  Max drawdown     : {max_dd * 100:.2f}%   "
                 f"target ≤ {TARGET_MAX_DRAWDOWN * 100:.0f}%")
    lines.append(f"  Sharpe (trade)   : {sharpe:.2f}")
    lines.append(f"  Expectancy/trade : ${expectancy:+,.4f}")
    lines.append(f"  Largest win/loss : ${largest_win:+,.2f} / ${largest_loss:+,.2f}")
    lines.append("-" * 70)

    # Open positions
    lines.append(f"OPEN POSITIONS  ({len(open_positions)})")
    if open_positions:
        lines.append(f"  {'Symbol':<12}{'Dir':<6}{'Entry':>12}{'Size':>14}{'Risk$':>10}")
        for p in open_positions:
            lines.append(
                f"  {p['symbol']:<12}{p['direction']:<6}"
                f"{float(p['entry_price']):>12.4f}"
                f"{float(p['position_size_units']):>14.4f}"
                f"${float(p['risk_usd']):>9.2f}"
            )
    else:
        lines.append("  (none)")
    lines.append("-" * 70)

    # Daily equity curve
    if state_rows:
        lines.append("DAILY EQUITY (from paper_daily)")
        lines.append(f"  {'Date':<12}{'Equity':>14}{'Daily P/L':>14}{'Cum P/L':>14}{'Trades':>8}{'WR':>8}")
        for row in state_rows:
            date_str = row.get("date", "")
            equity = float(row.get("total_equity") or 0)
            daily = float(row.get("daily_pnl") or 0)
            cum = float(row.get("cumulative_pnl") or 0)
            cnt = int(row.get("trades_count") or 0)
            wr = float(row.get("win_rate") or 0)
            lines.append(
                f"  {date_str:<12}${equity:>13,.2f}${daily:>+13,.2f}"
                f"${cum:>+13,.2f}{cnt:>8}{wr * 100:>7.1f}%"
            )
    else:
        lines.append("DAILY EQUITY (no data yet — close some trades to populate)")
    lines.append("-" * 70)

    # Top winners/losers
    if closed:
        sorted_wins = sorted(closed, key=lambda t: float(t.get("pnl_usd", 0) or 0), reverse=True)
        sorted_loss = sorted(closed, key=lambda t: float(t.get("pnl_usd", 0) or 0))
        lines.append(f"TOP WINNERS (top 3 of {len(sorted_wins)})")
        for t in sorted_wins[:3]:
            lines.append(
                f"  ✅ {t.get('symbol', '?'):<10} "
                f"pnl=${float(t.get('pnl_usd', 0) or 0):+,.2f}  "
                f"{float(t.get('pnl_r_multiple', 0) or 0):+.2f}R  "
                f"({t.get('grade', '?')})"
            )
        lines.append(f"TOP LOSERS (top 3 of {len(sorted_loss)})")
        for t in sorted_loss[:3]:
            lines.append(
                f"  ❌ {t.get('symbol', '?'):<10} "
                f"pnl=${float(t.get('pnl_usd', 0) or 0):+,.2f}  "
                f"{float(t.get('pnl_r_multiple', 0) or 0):+.2f}R  "
                f"({t.get('grade', '?')})"
            )
        lines.append("-" * 70)

    # Phase 7 readiness
    readiness = phase7_readiness(metrics=metrics, total_trades=total)
    lines.append("PHASE 7 LIVE TRADING READINESS")
    checks = [
        (
            f"min trades ≥ {PAPER_PHASE7_MIN_TRADES}",
            readiness["min_trades_ok"],
        ),
        (
            f"win rate ≥ {TARGET_WIN_RATE * 100 - PAPER_PHASE7_WIN_RATE_TOLERANCE * 100:.0f}%",
            readiness["win_rate_ok"],
        ),
        (
            f"profit factor ≥ {PAPER_PHASE7_MIN_PROFIT_FACTOR}",
            readiness["profit_factor_ok"],
        ),
        (
            f"max drawdown ≤ {PAPER_PHASE7_MAX_DRAWDOWN * 100:.0f}%",
            readiness["drawdown_ok"],
        ),
    ]
    for label, ok in checks:
        lines.append(f"  {_pass(ok)} {label}")
    overall = readiness["ready"]
    lines.append(
        f"  Overall: {'🟢 READY for Phase 7' if overall else '🔴 NOT READY yet'}"
    )
    lines.append("=" * 70)
    text = "\n".join(lines)
    logger.info(f"[reporter] generated report ({days_back}d, {total} trades)")
    return text


def phase7_readiness(
    *, metrics: dict[str, Any], total_trades: int
) -> dict[str, Any]:
    """
    Decide whether paper trading results are good enough to greenlight
    Phase 7 (live trading).

    Pass criteria (all must be True):
      - total_trades >= PAPER_PHASE7_MIN_TRADES (statistical significance)
      - win_rate >= TARGET_WIN_RATE - PAPER_PHASE7_WIN_RATE_TOLERANCE
      - profit_factor >= PAPER_PHASE7_MIN_PROFIT_FACTOR (default 1.0)
      - max_drawdown_pct <= PAPER_PHASE7_MAX_DRAWDOWN
    """
    win_rate = float(metrics.get("win_rate", 0))
    pf = float(metrics.get("profit_factor", 0))
    dd = float(metrics.get("max_drawdown_pct", 0))
    min_trades_ok = total_trades >= PAPER_PHASE7_MIN_TRADES
    wr_ok = win_rate >= (TARGET_WIN_RATE - PAPER_PHASE7_WIN_RATE_TOLERANCE)
    pf_ok = pf >= PAPER_PHASE7_MIN_PROFIT_FACTOR
    dd_ok = dd <= PAPER_PHASE7_MAX_DRAWDOWN
    ready = bool(min_trades_ok and wr_ok and pf_ok and dd_ok)
    return {
        "ready": ready,
        "min_trades_ok": min_trades_ok,
        "win_rate_ok": wr_ok,
        "profit_factor_ok": pf_ok,
        "drawdown_ok": dd_ok,
        "min_trades": PAPER_PHASE7_MIN_TRADES,
        "total_trades": total_trades,
        "win_rate": win_rate,
        "profit_factor": pf,
        "max_drawdown_pct": dd,
    }


def generate_equity_chart(
    journal: PaperJournal,
    *,
    days_back: int = 7,
    output_path: str | None = None,
) -> str | None:
    """
    Render equity curve PNG to output_path (or default
    paper/reports/equity_<timestamp>.png). Returns path, or None on
    failure / no data.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[reporter] matplotlib not available: {exc}")
        return None

    daily = journal.get_daily_history()
    if not daily:
        logger.info("[reporter] no daily data — skipping chart")
        return None

    if days_back > 0 and len(daily) > days_back:
        daily = daily[-int(days_back):]

    dates = [row.get("date", "") for row in daily]
    equity = [float(row.get("total_equity") or 0) for row in daily]
    cum_pnl = [float(row.get("cumulative_pnl") or 0) for row in daily]

    if not output_path:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        output_path = str(PAPER_REPORTS_DIR / f"equity_{ts}.png")

    try:
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
        ax1.plot(dates, equity, marker="o", color="#3b82f6", linewidth=2)
        ax1.set_title(f"RX-0 Paper Equity Curve (last {days_back}d)")
        ax1.set_ylabel("Equity (USD)")
        ax1.grid(True, alpha=0.3)
        ax1.axhline(y=equity[0] if equity else 0, color="#94a3b8",
                    linestyle="--", alpha=0.5, label="start")
        ax1.legend(loc="best")
        ax2.bar(dates, cum_pnl, color="#10b981", alpha=0.7)
        ax2.set_title("Cumulative P/L")
        ax2.set_ylabel("P/L (USD)")
        ax2.set_xlabel("Date (UTC)")
        ax2.grid(True, alpha=0.3)
        ax2.axhline(y=0, color="#94a3b8", linestyle="--", alpha=0.5)
        plt.xticks(rotation=30, ha="right")
        plt.tight_layout()
        Path_ = __import__("pathlib").Path
        Path_(output_path).parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(output_path, dpi=120, bbox_inches="tight")
        plt.close(fig)
        logger.info(f"[reporter] equity chart saved to {output_path}")
        return str(output_path)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"[reporter] chart save failed: {exc}")
        return None


def build_weekly_summary(
    journal: PaperJournal, *, days_back: int = 7
) -> dict[str, Any]:
    """
    Build a structured summary suitable for notifier (Tier 4).
    """
    metrics = journal.aggregate_performance(days_back=days_back)
    closed = journal.get_closed_trades(days_back=days_back)
    sorted_w = sorted(
        closed, key=lambda t: float(t.get("pnl_usd", 0) or 0), reverse=True
    )
    sorted_l = sorted(closed, key=lambda t: float(t.get("pnl_usd", 0) or 0))
    return {
        "period": f"last {days_back}d",
        "total_trades": int(metrics.get("total_trades", 0)),
        "wins": int(metrics.get("wins", 0)),
        "losses": int(metrics.get("losses", 0)),
        "win_rate": float(metrics.get("win_rate", 0)),
        "profit_factor": float(metrics.get("profit_factor", 0)),
        "total_pnl": float(metrics.get("total_pnl", 0)),
        "max_drawdown_pct": float(metrics.get("max_drawdown_pct", 0)),
        "avg_r_multiple": float(metrics.get("avg_r_multiple", 0)),
        "top_winners": [dict(t) for t in sorted_w[:3]],
        "top_losers": [dict(t) for t in sorted_l[:3]],
    }


__all__ = [
    "generate_report",
    "generate_equity_chart",
    "phase7_readiness",
    "build_weekly_summary",
]
