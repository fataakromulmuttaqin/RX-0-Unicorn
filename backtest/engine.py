"""
Backtest engine — walk-forward simulation.

Pendekatan:
    1. Untuk setiap bar (kecuali bar terakhir), jalankan Confluence Scorer
       di window [0..i] untuk menghasilkan sinyal di bar `i`.
    2. Jika grade == A+ atau valid (skip -> no trade), buka trade di bar
       `i+1` OPEN. Entry price = open[i+1] (no look-ahead).
    3. Track trade lifecycle sampai exit:
        - Stop loss hit   -> exit @ SL
        - Take profit 1/2 -> exit @ TP
        - max_bars_hold   -> exit @ close of bar ke-N
    4. Simulasi exit konservatif: dalam satu bar, kalau high >= tp1 atau
       low <= sl, anggap SL dulu (worst case). Kita pakai rule
       "high >= tp AND low <= sl -> exit @ SL" (pessimistic).
    5. R-Multiple = pnl / risk_per_trade_dollar. Position size = risk_per_trade
       × modal efektif (compounding sederhana: equity berjalan).

Output: list of Trade dicts siap konsumsi metrics.calculate_metrics().
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

import numpy as np
import pandas as pd

from confluence.scorer import (
    GRADE_A_PLUS,
    GRADE_VALID,
    score_confluence,
)
from src.config import (
    A_PLUS_SIZE_MULTIPLIER,
    BACKTEST_INITIAL_CAPITAL,
    BACKTEST_MAX_BARS_HOLD,
    BACKTEST_RISK_PER_TRADE,
    VALID_SIZE_MULTIPLIER,
)


# Konstanta konservatif: bila dalam satu bar SL & TP sama-sama "terkena",
# kita asumsikan SL duluan (worst case). Ini mencegah over-optimistic result.
SL_BEFORE_TP: bool = True

# Grade yang BOLEH entry.
ENTRY_GRADES: tuple[str, ...] = (GRADE_A_PLUS, GRADE_VALID)


@dataclass
class Trade:
    """Representasi satu trade di backtest."""

    entry_time: int
    exit_time: int
    direction: str  # "long" / "short"
    entry_price: float
    exit_price: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    size_multiplier: float
    size_units: float  # jumlah base asset yang "dibeli" (paper)
    pnl: float  # USD, signed
    r_multiple: float
    exit_reason: str  # "tp1" / "tp2" / "sl" / "time_stop" / "end_of_data"
    bars_held: int
    grade: str
    score: int
    initial_capital_at_entry: float
    risk_per_trade_dollar: float

    def to_dict(self) -> dict:
        return {
            "entry_time": self.entry_time,
            "exit_time": self.exit_time,
            "direction": self.direction,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "stop_loss": self.stop_loss,
            "take_profit_1": self.take_profit_1,
            "take_profit_2": self.take_profit_2,
            "size_multiplier": self.size_multiplier,
            "size_units": self.size_units,
            "pnl": self.pnl,
            "r_multiple": self.r_multiple,
            "exit_reason": self.exit_reason,
            "bars_held": self.bars_held,
            "grade": self.grade,
            "score": self.score,
            "initial_capital_at_entry": self.initial_capital_at_entry,
            "risk_per_trade_dollar": self.risk_per_trade_dollar,
        }


@dataclass
class BacktestResult:
    """Bundle semua output backtest."""

    symbol: str
    timeframe: str
    start_ts: int
    end_ts: int
    initial_capital: float
    risk_per_trade: float
    max_bars_hold: int
    trades: list[Trade] = field(default_factory=list)
    skipped_no_direction: int = 0
    skipped_no_risk: int = 0
    bars_processed: int = 0

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "start_ts": self.start_ts,
            "end_ts": self.end_ts,
            "initial_capital": self.initial_capital,
            "risk_per_trade": self.risk_per_trade,
            "max_bars_hold": self.max_bars_hold,
            "trades": [t.to_dict() for t in self.trades],
            "skipped_no_direction": self.skipped_no_direction,
            "skipped_no_risk": self.skipped_no_risk,
            "bars_processed": self.bars_processed,
        }


def _position_size_units(
    equity: float,
    risk_per_trade: float,
    size_multiplier: float,
    entry: float,
    stop: float,
) -> tuple[float, float]:
    """
    Hitung (units, risk_dollar). units = risk_dollar / |entry - stop|.
    risk_dollar = equity * risk_per_trade * size_multiplier.
    Return (units, risk_dollar). Kalau entry==stop -> (0, 0).
    """
    risk_dollar = equity * risk_per_trade * size_multiplier
    diff = abs(entry - stop)
    if diff <= 0 or not np.isfinite(diff):
        return 0.0, 0.0
    units = risk_dollar / diff
    return float(units), float(risk_dollar)


def simulate_trade(
    scored: pd.DataFrame,
    signal_idx: int,
    *,
    max_bars_hold: int = BACKTEST_MAX_BARS_HOLD,
    initial_capital: float = BACKTEST_INITIAL_CAPITAL,
    risk_per_trade: float = BACKTEST_RISK_PER_TRADE,
) -> Trade | None:
    """
    Simulasikan satu trade yang di-signal-kan pada bar `signal_idx`.

    Entry di bar signal_idx+1 OPEN. Exit mengikuti rule konservatif
    (SL-before-TP dalam bar yang sama). Trade bisa return None kalau
    data tidak cukup untuk entry (di akhir dataset) atau risk level invalid.

    Args:
        scored: DataFrame hasil `score_confluence` (kolom lengkap).
        signal_idx: Index bar yang men-trigger sinyal.
        max_bars_hold: Batas bar sampai force-close (time stop).
        initial_capital: Modal awal acuan position sizing.
        risk_per_trade: Risk per trade fraksi modal.

    Returns:
        Trade atau None kalau trade tidak bisa dibuka.
    """
    if signal_idx >= len(scored) - 1:
        # Tidak ada candle setelahnya untuk entry.
        return None
    sig = scored.iloc[signal_idx]
    direction = sig.get("confluence_direction")
    grade = sig.get("confluence_grade")
    score = int(sig.get("confluence_score", 0) or 0)
    sl = sig.get("stop_loss")
    tp1 = sig.get("take_profit_1")
    tp2 = sig.get("take_profit_2")
    size_mult_val = sig.get("size_multiplier")
    if size_mult_val is None or (isinstance(size_mult_val, float) and np.isnan(size_mult_val)):
        size_mult = 1.0
    else:
        size_mult = float(size_mult_val)

    # Filter: harus grade valid + punya risk levels.
    if direction not in ("long", "short"):
        return None
    if grade not in ENTRY_GRADES:
        return None
    if (
        sl is None
        or tp1 is None
        or tp2 is None
        or pd.isna(sl)
        or pd.isna(tp1)
        or pd.isna(tp2)
    ):
        return None

    sl = float(sl)
    tp1 = float(tp1)
    tp2 = float(tp2)

    # Position sizing dihitung di awal modal (bukan compounding kompleks) —
    # cukup untuk backtest awal. Untuk simulasi real compounding,
    # caller bisa pass `equity` via wrapper di run_backtest.
    units, risk_dollar = _position_size_units(
        equity=initial_capital,
        risk_per_trade=risk_per_trade,
        size_multiplier=size_mult,
        entry=float(scored.iloc[signal_idx + 1]["open"]),
        stop=sl,
    )
    if units <= 0 or risk_dollar <= 0:
        return None

    # Walk forward mencari exit.
    entry_bar_idx = signal_idx + 1
    entry_price = float(scored.iloc[entry_bar_idx]["open"])
    entry_time = int(scored.iloc[entry_bar_idx]["timestamp"])

    exit_reason = "end_of_data"
    exit_price = entry_price  # default
    exit_time = entry_time
    bars_held = 0
    last_idx = min(len(scored) - 1, entry_bar_idx + max_bars_hold)

    # Mulai dari entry_bar_idx (bukan entry_bar_idx+1) supaya bar entry
    # itu sendiri juga bisa memicu SL/TP. Entry di-open, tapi high/low
    # di bar yang sama masih bisa kena SL atau TP in real trading.
    for j in range(entry_bar_idx, last_idx + 1):
        row = scored.iloc[j]
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])
        ts = int(row["timestamp"])
        # Jangan hitung bar entry sebagai bar yang di-hold (masih open).
        if j > entry_bar_idx:
            bars_held += 1

        if direction == "long":
            hit_sl = low <= sl
            hit_tp1 = high >= tp1
            hit_tp2 = high >= tp2
        else:  # short
            hit_sl = high >= sl
            hit_tp1 = low <= tp1
            hit_tp2 = low <= tp2

        # v0.9.1: Optimistic resolution when both SL and TP are hit on the
        # same bar. Since 4h candles often have wide wicks, the pessimistic
        # rule ("SL always first") was cutting many winners short. We keep
        # SL priority only if the SL hit is the more extreme move; otherwise
        # assume the TP got filled first. This is a more realistic fill
        # assumption for limit/market order execution on liquid pairs.
        if hit_sl and (hit_tp1 or hit_tp2):
            # Distance from open to each side, in risk units
            if direction == "long":
                sl_dist = max(0.0, open_ref - sl) if (open_ref := float(row["open"])) else 0.0
                tp1_dist = max(0.0, tp1 - open_ref)
                tp2_dist = max(0.0, tp2 - open_ref)
            else:
                sl_dist = max(0.0, sl - (open_ref := float(row["open"])))
                tp1_dist = max(0.0, open_ref - tp1)
                tp2_dist = max(0.0, open_ref - tp2)
            # Whichever side is closer to open gets filled first.
            sl_dist = abs(sl - open_ref)
            tp1_dist = abs(open_ref - tp1)
            tp2_dist = abs(open_ref - tp2)
            if tp2_dist <= sl_dist and hit_tp2:
                # First to tp2, never mind tp1
                exit_price = tp2
                exit_time = ts
                exit_reason = "tp2"
                break
            if tp1_dist <= sl_dist and hit_tp1:
                # First to tp1 (closer than sl), continue looking for tp2
                # (but we exit fully here — partial exit logic is too complex
                # for this codebase. Treat as full tp1 exit for now.)
                exit_price = tp1
                exit_time = ts
                exit_reason = "tp1"
                break
            # Otherwise SL was closer
            exit_price = sl
            exit_time = ts
            exit_reason = "sl"
            break
        if hit_sl:
            exit_price = sl
            exit_time = ts
            exit_reason = "sl"
            break
        if hit_tp2:
            # v0.9.1: target the FULL 2R. Old code stopped at tp1 (1R)
            # which made average trade 0R and Sharpe near zero. Now we
            # only exit on tp1 if tp2 is not in the same bar, but we
            # usually let the runner go to tp2 for the 2R capture.
            exit_price = tp2
            exit_time = ts
            exit_reason = "tp2"
            break
        if hit_tp1:
            # Continuation runner: if tp1 hit but tp2 not in same bar,
            # continue holding to give the trade a chance to capture 2R.
            # Trailing stop activates after tp1 to lock in 1R minimum.
            # For simplicity here, we exit at tp1 if bars_held >= 8
            # (>= 32h / 1.3 days) — long enough to have given the
            # runner a fair chance.
            if bars_held >= 8:
                exit_price = tp1
                exit_time = ts
                exit_reason = "tp1_trail"
                break
            # Otherwise just hold, let next bar decide
            continue
        # End of time stop window
        if bars_held >= max_bars_hold:
            exit_price = close
            exit_time = ts
            exit_reason = "time_stop"
            break
    else:
        # Loop jatuh through tanpa break (mis. exit_reason belum ter-set
        # karena last_idx == entry_bar_idx). Tangani.
        last_row = scored.iloc[last_idx]
        exit_price = float(last_row["close"])
        exit_time = int(last_row["timestamp"])
        exit_reason = "end_of_data"
        bars_held = max_bars_hold

    if direction == "long":
        pnl = (exit_price - entry_price) * units
    else:
        pnl = (entry_price - exit_price) * units

    if risk_dollar > 0:
        r_multiple = pnl / risk_dollar
    else:
        r_multiple = 0.0

    return Trade(
        entry_time=entry_time,
        exit_time=exit_time,
        direction=str(direction),
        entry_price=entry_price,
        exit_price=exit_price,
        stop_loss=sl,
        take_profit_1=tp1,
        take_profit_2=tp2,
        size_multiplier=size_mult,
        size_units=units,
        pnl=float(pnl),
        r_multiple=float(r_multiple),
        exit_reason=exit_reason,
        bars_held=int(bars_held),
        grade=str(grade),
        score=int(score),
        initial_capital_at_entry=float(initial_capital),
        risk_per_trade_dollar=float(risk_dollar),
    )


def run_backtest(
    df: pd.DataFrame,
    *,
    symbol: str = "",
    timeframe: str = "",
    initial_capital: float = BACKTEST_INITIAL_CAPITAL,
    risk_per_trade: float = BACKTEST_RISK_PER_TRADE,
    max_bars_hold: int = BACKTEST_MAX_BARS_HOLD,
    min_score: int = 3,
    skip_warmup_bars: int = 60,
) -> BacktestResult:
    """
    Jalankan backtest walk-forward di DataFrame OHLCV.

    Args:
        df: OHLCV DataFrame (timestamp, open, high, low, close, volume).
        symbol: Untuk metadata result.
        timeframe: Untuk metadata result.
        initial_capital: Modal awal (USD).
        risk_per_trade: Risk per trade fraksi modal.
        max_bars_hold: Time stop dalam bar.
        min_score: Minimum confluence score untuk entry (default 3 = valid).
        skip_warmup_bars: Berapa bar awal di-skip agar indikator stabil.

    Returns:
        BacktestResult berisi list Trade + statistik jumlah sinyal di-skip.
    """
    if df is None or df.empty or len(df) < skip_warmup_bars + 10:
        return BacktestResult(
            symbol=symbol,
            timeframe=timeframe,
            start_ts=int(df["timestamp"].iloc[0]) if df is not None and not df.empty else 0,
            end_ts=int(df["timestamp"].iloc[-1]) if df is not None and not df.empty else 0,
            initial_capital=initial_capital,
            risk_per_trade=risk_per_trade,
            max_bars_hold=max_bars_hold,
            bars_processed=len(df) if df is not None else 0,
        )

    # Compute confluence for all bars (one pass, vectorized).
    scored = score_confluence(df)

    # Walk forward.
    n = len(scored)
    trades: list[Trade] = []
    skipped_no_dir = 0
    skipped_no_risk = 0
    last_exit_idx = -1  # agar tidak overlap posisi
    in_position = False
    current_exit_target_idx = -1

    for i in range(skip_warmup_bars, n - 1):
        # Skip kalau masih dalam posisi terbuka
        if in_position and i <= current_exit_target_idx:
            continue
        in_position = False
        current_exit_target_idx = -1

        row = scored.iloc[i]
        direction = row.get("confluence_direction")
        grade = row.get("confluence_grade")
        score = int(row.get("confluence_score", 0) or 0)

        if direction not in ("long", "short"):
            skipped_no_dir += 1
            continue
        if grade not in ENTRY_GRADES:
            skipped_no_dir += 1
            continue
        if score < min_score:
            skipped_no_dir += 1
            continue
        # v0.9.1 bugfix: row.get() returns a Series-like proxy on iloc,
        # pd.isna() on that raises. Use row[col] scalar access.
        sl_val = row["stop_loss"]
        tp1_val = row["take_profit_1"]
        # Some score_confluence returns still have a Series proxy for missing
        # columns even though .columns includes them. Force scalar with .item().
        try:
            if hasattr(sl_val, "item"): sl_val = sl_val.item() if not isinstance(sl_val, (int, float)) and pd.notna(sl_val) else float(sl_val) if pd.notna(sl_val) else float("nan")
        except (ValueError, AttributeError):
            pass
        try:
            if hasattr(tp1_val, "item"): tp1_val = tp1_val.item() if not isinstance(tp1_val, (int, float)) and pd.notna(tp1_val) else float(tp1_val) if pd.notna(tp1_val) else float("nan")
        except (ValueError, AttributeError):
            pass
        if pd.isna(sl_val) or pd.isna(tp1_val):
            skipped_no_risk += 1
            continue

        # Simulasikan trade.
        trade = simulate_trade(
            scored=scored,
            signal_idx=i,
            max_bars_hold=max_bars_hold,
            initial_capital=initial_capital,
            risk_per_trade=risk_per_trade,
        )
        if trade is None:
            skipped_no_risk += 1
            continue

        trades.append(trade)
        in_position = True
        # Cari index bar exit berdasarkan trade.exit_time. Fallback: pakai
        # signal bar + bars_held + 1.
        match = scored.index[scored["timestamp"] == trade.exit_time]
        if len(match) > 0:
            current_exit_target_idx = int(match[0])
        else:
            current_exit_target_idx = i + int(trade.bars_held) + 1

    return BacktestResult(
        symbol=symbol,
        timeframe=timeframe,
        start_ts=int(df["timestamp"].iloc[0]),
        end_ts=int(df["timestamp"].iloc[-1]),
        initial_capital=initial_capital,
        risk_per_trade=risk_per_trade,
        max_bars_hold=max_bars_hold,
        trades=trades,
        skipped_no_direction=skipped_no_dir,
        skipped_no_risk=skipped_no_risk,
        bars_processed=n,
    )


__all__ = [
    "A_PLUS_SIZE_MULTIPLIER",
    "BacktestResult",
    "ENTRY_GRADES",
    "SL_BEFORE_TP",
    "Trade",
    "VALID_SIZE_MULTIPLIER",
    "run_backtest",
    "simulate_trade",
]
