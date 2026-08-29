"""
Backtest metrics — 6 metrics wajib per STRATEGY.md.

Setiap metric menggunakan formula standar industri:

    Win Rate        = wins / total_trades
    Profit Factor   = gross_profit / |gross_loss|
    Max Drawdown    = peak-to-trough equity decline, %
    Sharpe Ratio    = (mean(trade_pnl) - 0) / std(trade_pnl)  (rf = 0)
    Avg R-Multiple  = mean(r_multiple) dimana r_multiple = pnl / risk_per_trade
    Expectancy      = (WR * avg_win) - ((1 - WR) * avg_loss)

Catatan:
- Risk per trade diambil dari settings caller (`risk_per_trade`), bukan
  diturunkan dari R-multiple aktual (mencegah circular definition).
- Edge case 0 trades: kembalikan dict dengan nilai 0 (atau np.nan untuk
  ratio yang tak terdefinisi) — bukan exception.
- Profit factor "infinite" (no losses) di-cap di `PROFIT_FACTOR_CAP` agar
  JSON / report tidak menampilkan 'inf'.
"""

from __future__ import annotations

import math
from typing import Iterable

import numpy as np

from src.config import TARGET_AVG_R_MULTIPLE, TARGET_MAX_DRAWDOWN, TARGET_PROFIT_FACTOR, TARGET_SHARPE, TARGET_WIN_RATE

# Cap untuk profit factor "infinite" (no losing trades).
PROFIT_FACTOR_CAP: float = 999.0

# Threshold "kecil" untuk menganggap dua nilai float identik di test.
FLOAT_TOL: float = 1e-9


def empty_metrics() -> dict:
    """
    Return dict metrics default untuk 0 trades. Dipakai report ketika
    backtest tidak menghasilkan sinyal (mis. market flat / data tipis).
    """
    return {
        "total_trades": 0,
        "wins": 0,
        "losses": 0,
        "win_rate": 0.0,
        "profit_factor": 0.0,
        "max_drawdown_pct": 0.0,
        "sharpe_ratio": 0.0,
        "avg_r_multiple": 0.0,
        "expectancy": 0.0,
        "avg_win": 0.0,
        "avg_loss": 0.0,
        "largest_win": 0.0,
        "largest_loss": 0.0,
        "total_pnl": 0.0,
        "equity_curve": [],
        "equity_final": 0.0,
    }


def _to_pnl_array(trades: Iterable[dict]) -> np.ndarray:
    """Ekstrak pnl dari list trade (np.nan -> 0)."""
    pnls: list[float] = []
    for t in trades:
        pnl = t.get("pnl")
        if pnl is None:
            pnls.append(0.0)
        else:
            pnls.append(float(pnl))
    return np.asarray(pnls, dtype=np.float64)


def calculate_metrics(
    trades: list[dict],
    *,
    initial_capital: float = 10_000.0,
    risk_per_trade: float = 0.02,
) -> dict:
    """
    Hitung 6 metrics + beberapa turunan dari list trade.

    Args:
        trades: List of trade dicts, masing-masing punya minimal key 'pnl'
            (USD, signed) dan 'r_multiple' (signed; opsional tapi dipakai
            untuk Avg R-Multiple). Field lain diabaikan.
        initial_capital: Modal awal (untuk equity curve, default 10_000).
        risk_per_trade: Risk per trade sebagai fraksi modal (default 0.02).
            Dipakai untuk denominator R-multiple kalau trade tidak punya
            field 'r_multiple'.

    Returns:
        Dict dengan keys:
            total_trades, wins, losses, win_rate, profit_factor,
            max_drawdown_pct, sharpe_ratio, avg_r_multiple, expectancy,
            avg_win, avg_loss, largest_win, largest_loss, total_pnl,
            equity_curve (list cumulative pnl), equity_final.
    """
    if not trades:
        return empty_metrics()

    pnls = _to_pnl_array(trades)
    n = len(pnls)
    wins_mask = pnls > 0
    losses_mask = pnls < 0
    wins = int(wins_mask.sum())
    losses = int(losses_mask.sum())
    win_rate = wins / n if n > 0 else 0.0

    gross_profit = float(pnls[wins_mask].sum()) if wins else 0.0
    gross_loss = float(pnls[losses_mask].sum()) if losses else 0.0  # negative

    # Profit factor: rasio gross_profit terhadap |gross_loss|. Edge case:
    # - no losses   -> "infinite" -> cap di PROFIT_FACTOR_CAP
    # - no wins     -> 0.0
    if gross_loss == 0:
        if gross_profit > 0:
            profit_factor = PROFIT_FACTOR_CAP
        else:
            profit_factor = 0.0
    else:
        profit_factor = gross_profit / abs(gross_loss)
        if math.isinf(profit_factor) or math.isnan(profit_factor):
            profit_factor = PROFIT_FACTOR_CAP

    # Avg win / avg loss (USD). avg_loss positif (magnitudes).
    avg_win = float(pnls[wins_mask].mean()) if wins else 0.0
    avg_loss = float(abs(pnls[losses_mask].mean())) if losses else 0.0
    largest_win = float(pnls.max()) if n else 0.0
    largest_loss = float(pnls.min()) if n else 0.0

    # Expectancy per trade (USD). = (WR * avg_win) - ((1 - WR) * avg_loss)
    expectancy = (win_rate * avg_win) - ((1.0 - win_rate) * avg_loss)

    # Total PnL & equity curve.
    total_pnl = float(pnls.sum())
    cum_pnl = np.cumsum(pnls)
    equity_curve = [float(x) for x in cum_pnl]
    equity_final = float(initial_capital + cum_pnl[-1])

    # Max drawdown (%). Equity dihitung dari modal awal, drawdown = peak-to-trough
    # decline. Return 0.0 kalau modal awal <= 0 (defensive).
    if initial_capital <= 0:
        max_dd_pct = 0.0
    else:
        # PENTING: anchor series dengan modal awal di index -1 supaya peak
        # di awal backtest adalah initial_capital (bukan equity setelah
        # trade pertama kalau trade itu rugi).
        equity_series = np.concatenate(
            ([float(initial_capital)], initial_capital + cum_pnl)
        )
        running_peak = np.maximum.accumulate(equity_series)
        drawdown = (equity_series - running_peak) / running_peak
        # drawdown <= 0; ambil yang paling negatif -> magnitudo = -min
        max_dd_pct = float(abs(drawdown.min()) * 100.0) if len(drawdown) else 0.0

    # Sharpe ratio per-trade (rf = 0). Kalau std = 0 (semua trade sama) -> 0.
    if n < 2:
        sharpe = 0.0
    else:
        mean_pnl = float(pnls.mean())
        std_pnl = float(pnls.std(ddof=1))
        if std_pnl <= 0 or math.isnan(std_pnl):
            sharpe = 0.0
        else:
            sharpe = mean_pnl / std_pnl

    # Avg R-Multiple. Prioritas: trade-level r_multiple; fallback ke pnl/risk.
    r_per_trade_dollar = max(1e-12, initial_capital * float(risk_per_trade))
    r_values: list[float] = []
    for t in trades:
        if "r_multiple" in t and t["r_multiple"] is not None:
            r_values.append(float(t["r_multiple"]))
        else:
            r_values.append(float(t.get("pnl", 0.0)) / r_per_trade_dollar)
    r_arr = np.asarray(r_values, dtype=np.float64)
    avg_r = float(r_arr.mean()) if len(r_arr) else 0.0
    if math.isnan(avg_r) or math.isinf(avg_r):
        avg_r = 0.0

    return {
        "total_trades": n,
        "wins": wins,
        "losses": losses,
        "win_rate": float(win_rate),
        "profit_factor": float(profit_factor),
        "max_drawdown_pct": float(max_dd_pct),
        "sharpe_ratio": float(sharpe),
        "avg_r_multiple": float(avg_r),
        "expectancy": float(expectancy),
        "avg_win": float(avg_win),
        "avg_loss": float(avg_loss),
        "largest_win": float(largest_win),
        "largest_loss": float(largest_loss),
        "total_pnl": float(total_pnl),
        "equity_curve": equity_curve,
        "equity_final": float(equity_final),
    }


def target_check(metrics: dict) -> dict:
    """
    Bandingkan metrics dengan target STRATEGY.md.

    Return dict {metric: bool}. True = met target, False = miss.
    """
    return {
        "win_rate": metrics.get("win_rate", 0.0) > TARGET_WIN_RATE,
        "profit_factor": metrics.get("profit_factor", 0.0) > TARGET_PROFIT_FACTOR,
        "max_drawdown": metrics.get("max_drawdown_pct", 100.0) < TARGET_MAX_DRAWDOWN * 100.0,
        "sharpe_ratio": metrics.get("sharpe_ratio", 0.0) > TARGET_SHARPE,
        "avg_r_multiple": metrics.get("avg_r_multiple", 0.0) > TARGET_AVG_R_MULTIPLE,
        "expectancy": metrics.get("expectancy", 0.0) > 0.0,
    }


__all__ = [
    "FLOAT_TOL",
    "PROFIT_FACTOR_CAP",
    "calculate_metrics",
    "empty_metrics",
    "target_check",
]
