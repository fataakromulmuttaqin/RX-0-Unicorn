"""
PaperPortfolio — virtual balance + open position manager (Phase 6).

State disimpan ke dalam PaperJournal.paper_state (singleton key/value):
  - balance         : float — cash balance (USD)
  - peak_equity     : float — peak equity seen so far (untuk drawdown calc)
  - initial_balance : float — modal awal (PAPER_INITIAL_BALANCE)
  - started_at      : int   — unix ts of first init
  - last_daily_loss : dict  — {date, loss_usd} for daily-loss-limit check
  - circuit_until   : int   — unix ts; trading paused until this time

Position state disimpan ke paper_journal.paper_trades (status='open').
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from src.config import (
    PAPER_DAILY_LOSS_LIMIT,
    PAPER_INITIAL_BALANCE,
    PAPER_MAX_DAILY_TRADES,
    PAPER_MAX_DRAWDOWN_CIRCUIT,
    PAPER_MAX_OPEN_POSITIONS,
    PAPER_TP1_HIT_BREAKEVEN,
)
from src.logger import logger

from .journal import PaperJournal
from .notifier import PaperNotifier


# State keys (kept here to avoid magic strings)
K_BALANCE = "balance"
K_PEAK_EQUITY = "peak_equity"
K_INITIAL = "initial_balance"
K_STARTED_AT = "started_at"
K_CIRCUIT_UNTIL = "circuit_until"
K_LAST_DAILY = "last_daily_loss"


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _today_cutoff_ts() -> int:
    s = _today_str()
    return int(
        datetime.strptime(s, "%Y-%m-%d")
        .replace(tzinfo=timezone.utc)
        .timestamp()
    )


class PaperPortfolio:
    """
    Virtual portfolio backed by PaperJournal (singleton state).

    Usage:
        with PaperJournal() as j:
            p = PaperPortfolio(journal=j)
            p.start(initial_balance=10000)  # idempotent
            p.open_position(...)
            equity = p.get_equity(mark_prices={...})
    """

    def __init__(
        self,
        journal: PaperJournal,
        notifier: PaperNotifier | None = None,
    ) -> None:
        self.journal = journal
        self.notifier: PaperNotifier | None = notifier

    # --- Init / reset ---
    def start(
        self,
        initial_balance: float = PAPER_INITIAL_BALANCE,
        *,
        reset: bool = False,
    ) -> dict[str, Any]:
        """
        Initialize paper portfolio. Idempotent: kalau sudah ada state
        (balance), tidak di-reset kecuali `reset=True`.
        Returns current state dict.
        """
        existing = self.journal.get_state(K_BALANCE)
        if existing is not None and not reset:
            logger.info(
                f"[portfolio] already initialized (balance=${existing:.2f})"
            )
            return self.get_state()
        self.journal.set_state(K_BALANCE, float(initial_balance))
        self.journal.set_state(K_PEAK_EQUITY, float(initial_balance))
        self.journal.set_state(K_INITIAL, float(initial_balance))
        self.journal.set_state(K_STARTED_AT, int(time.time()))
        self.journal.set_state(K_CIRCUIT_UNTIL, 0)
        self.journal.set_state(K_LAST_DAILY, {"date": _today_str(), "loss_usd": 0.0})
        logger.success(
            f"[portfolio] initialized — balance=${initial_balance:,.2f}"
        )
        return self.get_state()

    def reset(self, initial_balance: float = PAPER_INITIAL_BALANCE) -> dict[str, Any]:
        """Wipe everything and start fresh. Use with care."""
        self.journal.wipe_all()
        return self.start(initial_balance=initial_balance, reset=True)

    # --- State accessors ---
    def get_state(self) -> dict[str, Any]:
        return {
            "balance": float(self.journal.get_state(K_BALANCE, PAPER_INITIAL_BALANCE)),
            "peak_equity": float(self.journal.get_state(K_PEAK_EQUITY, PAPER_INITIAL_BALANCE)),
            "initial_balance": float(self.journal.get_state(K_INITIAL, PAPER_INITIAL_BALANCE)),
            "started_at": int(self.journal.get_state(K_STARTED_AT, int(time.time()))),
            "circuit_until": int(self.journal.get_state(K_CIRCUIT_UNTIL, 0)),
            "last_daily_loss": self.journal.get_state(
                K_LAST_DAILY, {"date": _today_str(), "loss_usd": 0.0}
            ),
        }

    def get_balance(self) -> float:
        return float(self.journal.get_state(K_BALANCE, PAPER_INITIAL_BALANCE))

    def get_peak_equity(self) -> float:
        return float(self.journal.get_state(K_PEAK_EQUITY, PAPER_INITIAL_BALANCE))

    def get_initial_balance(self) -> float:
        return float(self.journal.get_state(K_INITIAL, PAPER_INITIAL_BALANCE))

    # --- Position math ---
    @staticmethod
    def compute_position_size(
        *,
        equity: float,
        risk_per_trade: float,
        entry_price: float,
        stop_loss: float,
        size_multiplier: float = 1.0,
    ) -> tuple[float, float]:
        """
        Compute position size in base units + risk in USD.

        Formula:
            risk_usd = equity * risk_per_trade * size_multiplier
            sl_distance = abs(entry_price - stop_loss)
            size_units = risk_usd / sl_distance

        Returns: (size_units, risk_usd)
        Raises ValueError on bad inputs.
        """
        if equity <= 0:
            raise ValueError(f"equity must be > 0, got {equity}")
        if risk_per_trade <= 0 or risk_per_trade > 1:
            raise ValueError(
                f"risk_per_trade must be in (0, 1], got {risk_per_trade}"
            )
        if entry_price <= 0 or stop_loss <= 0:
            raise ValueError("entry_price and stop_loss must be > 0")
        sl_distance = abs(float(entry_price) - float(stop_loss))
        if sl_distance <= 0:
            raise ValueError("stop_loss == entry_price (no risk distance)")
        risk_usd = equity * float(risk_per_trade) * float(size_multiplier)
        size_units = risk_usd / sl_distance
        return size_units, risk_usd

    @staticmethod
    def compute_pnl(
        *,
        direction: str,
        entry_price: float,
        exit_price: float,
        size_units: float,
    ) -> float:
        """
        PnL in USD. direction 'long' / 'short'.
        """
        if direction == "long":
            return (float(exit_price) - float(entry_price)) * float(size_units)
        if direction == "short":
            return (float(entry_price) - float(exit_price)) * float(size_units)
        raise ValueError(f"direction must be 'long' or 'short', got {direction!r}")

    # --- Mark-to-market ---
    def get_equity(self, mark_prices: dict[str, float] | None = None) -> float:
        """
        Total equity = balance + sum(unrealized PnL of open positions).
        `mark_prices` is dict of {symbol: price}. Symbols missing from
        the dict use entry_price as fallback (so PnL=0).
        """
        balance = self.get_balance()
        open_positions = self.journal.get_open_positions()
        mark: dict[str, float] = dict(mark_prices) if mark_prices else {}
        unrealized = 0.0
        for p in open_positions:
            price = mark.get(p["symbol"], float(p["entry_price"]))
            try:
                pnl = self.compute_pnl(
                    direction=p["direction"],
                    entry_price=p["entry_price"],
                    exit_price=price,
                    size_units=p["position_size_units"],
                )
            except ValueError:
                pnl = 0.0
            unrealized += pnl
        return balance + unrealized

    def get_unrealized_pnl(
        self, mark_prices: dict[str, float] | None = None
    ) -> float:
        equity = self.get_equity(mark_prices=mark_prices)
        return equity - self.get_balance()

    def get_open_positions(self) -> list[dict[str, Any]]:
        return self.journal.get_open_positions()

    def get_closed_trades(self, *, limit: int | None = None) -> list[dict[str, Any]]:
        return self.journal.get_closed_trades(limit=limit)

    # --- Drawdown & circuit breaker ---
    def update_peak_equity(self, current_equity: float | None = None) -> float:
        """Update peak_equity in state if current is higher. Returns peak."""
        if current_equity is None:
            current_equity = self.get_equity()
        peak = self.get_peak_equity()
        if current_equity > peak:
            self.journal.set_state(K_PEAK_EQUITY, float(current_equity))
            peak = float(current_equity)
        return peak

    def get_drawdown_pct(self, current_equity: float | None = None) -> float:
        """Current drawdown as fraction (0.0 -> 0%, 0.20 -> 20%)."""
        if current_equity is None:
            current_equity = self.get_equity()
        peak = self.get_peak_equity()
        if peak <= 0:
            return 0.0
        dd = (peak - current_equity) / peak
        return max(0.0, dd)

    def is_drawdown_circuit_active(self) -> bool:
        until = int(self.journal.get_state(K_CIRCUIT_UNTIL, 0))
        return int(time.time()) < until

    def trip_drawdown_circuit(self, pause_seconds: int = 86400) -> None:
        """Pause trading for `pause_seconds` (default 24h)."""
        until = int(time.time()) + int(pause_seconds)
        self.journal.set_state(K_CIRCUIT_UNTIL, until)
        logger.warning(
            f"[portfolio] drawdown circuit TRIPPED — paused until "
            f"{datetime.fromtimestamp(until, tz=timezone.utc).isoformat()}"
        )
        # Tier 5 Telegram notification
        if self.notifier is not None:
            try:
                equity = self.get_equity()
                dd = self.get_drawdown_pct(equity)
                self.notifier.notify_risk_breach(
                    "drawdown_circuit",
                    {
                        "drawdown_pct": dd,
                        "equity": equity,
                        "paused_until": datetime.fromtimestamp(
                            until, tz=timezone.utc
                        ).isoformat(),
                    },
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[portfolio] notify_risk_breach error: {exc}")

    def is_daily_loss_limit_hit(self) -> bool:
        """Return True kalau P/L harian sudah <= -PAPER_DAILY_LOSS_LIMIT * equity."""
        pnl = self.journal.daily_pnl_today()
        equity = self.get_equity()
        if equity <= 0:
            return False
        threshold = -PAPER_DAILY_LOSS_LIMIT * equity
        return pnl <= threshold

    def can_open_new_position(self) -> tuple[bool, str]:
        """
        Pre-flight check: boleh buka posisi baru hari ini?
        Returns (allowed, reason_if_not).
        Checks: drawdown circuit, daily loss limit, max open positions,
        max daily trades.
        """
        if self.is_drawdown_circuit_active():
            return False, "drawdown_circuit_active"
        if self.is_daily_loss_limit_hit():
            # Tier 5: notify once per session per day (best-effort)
            if self.notifier is not None:
                try:
                    pnl = self.journal.daily_pnl_today()
                    equity = self.get_equity()
                    self.notifier.notify_risk_breach(
                        "daily_loss_limit",
                        {
                            "daily_pnl_usd": pnl,
                            "equity": equity,
                            "limit_pct": PAPER_DAILY_LOSS_LIMIT,
                        },
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        f"[portfolio] notify_risk_breach error: {exc}"
                    )
            return False, "daily_loss_limit_hit"
        if self.journal.count_open_positions() >= PAPER_MAX_OPEN_POSITIONS:
            return False, "max_open_positions_reached"
        if self.journal.count_trades_today() >= PAPER_MAX_DAILY_TRADES:
            return False, "max_daily_trades_reached"
        return True, "ok"

    # --- Position lifecycle (callable by PaperTrader) ---
    def open_position(
        self,
        *,
        trade_id: str,
        symbol: str,
        direction: str,
        entry_price: float,
        sl: float,
        tp1: float,
        tp2: float,
        confluence_score: int,
        grade: str,
        size_multiplier: float,
        risk_per_trade: float,
        signal_source: str = "scanner",
        notes: str | None = None,
    ) -> dict[str, Any]:
        """
        Open a new paper position. Raises if not allowed or invalid.
        Returns the open position dict (from journal).
        """
        allowed, reason = self.can_open_new_position()
        if not allowed:
            raise RuntimeError(f"position not allowed: {reason}")

        equity = self.get_equity()
        size_units, risk_usd = self.compute_position_size(
            equity=equity,
            risk_per_trade=risk_per_trade,
            entry_price=entry_price,
            stop_loss=sl,
            size_multiplier=size_multiplier,
        )
        self.journal.log_open_position(
            trade_id=trade_id,
            symbol=symbol,
            direction=direction,
            entry_time=int(time.time()),
            entry_price=entry_price,
            sl=sl,
            tp1=tp1,
            tp2=tp2,
            confluence_score=confluence_score,
            grade=grade,
            size_multiplier=size_multiplier,
            position_size_units=size_units,
            risk_usd=risk_usd,
            signal_source=signal_source,
            notes=notes,
        )
        return self.journal.get_trade_by_id(trade_id) or {}

    def close_position(
        self,
        *,
        trade_id: str,
        exit_price: float,
        exit_reason: str,
        exit_time: int | None = None,
    ) -> dict[str, Any]:
        """Close an open paper position. Realize PnL into balance."""
        trade = self.journal.get_trade_by_id(trade_id)
        if trade is None:
            raise KeyError(f"trade_id '{trade_id}' not found")
        if trade["status"] != "open":
            raise ValueError(
                f"trade_id '{trade_id}' is {trade['status']}, not open"
            )
        pnl = self.compute_pnl(
            direction=trade["direction"],
            entry_price=trade["entry_price"],
            exit_price=exit_price,
            size_units=trade["position_size_units"],
        )
        r_multiple = (
            (pnl / trade["risk_usd"]) if trade.get("risk_usd") else 0.0
        )
        self.journal.log_close_position(
            trade_id=trade_id,
            exit_time=int(exit_time if exit_time is not None else time.time()),
            exit_price=exit_price,
            exit_reason=exit_reason,
            pnl_usd=pnl,
            pnl_r_multiple=r_multiple,
        )
        # Update balance
        new_balance = self.get_balance() + pnl
        self.journal.set_state(K_BALANCE, float(new_balance))
        # Update daily loss tracking
        daily = self.journal.get_state(
            K_LAST_DAILY, {"date": _today_str(), "loss_usd": 0.0}
        )
        if daily.get("date") != _today_str():
            daily = {"date": _today_str(), "loss_usd": 0.0}
        if pnl < 0:
            daily["loss_usd"] = float(daily.get("loss_usd", 0.0)) + float(pnl)
        self.journal.set_state(K_LAST_DAILY, daily)
        # Update peak equity
        equity_after = self.get_equity()
        peak = self.update_peak_equity(equity_after)
        # Trip drawdown circuit if breached
        dd = self.get_drawdown_pct(equity_after)
        if dd >= PAPER_MAX_DRAWDOWN_CIRCUIT and not self.is_drawdown_circuit_active():
            self.trip_drawdown_circuit()
        # Persist daily equity snapshot
        cumulative_pnl = equity_after - self.get_initial_balance()
        self.journal.update_daily_equity(
            date_str=_today_str(),
            total_equity=equity_after,
            cumulative_pnl=cumulative_pnl,
        )
        logger.info(
            f"[portfolio] closed {trade_id} pnl=${pnl:+.2f} "
            f"new_balance=${new_balance:.2f} equity=${equity_after:.2f} "
            f"dd={dd:.2%}"
        )
        # Tier 2: Telegram exit notification (BUG FIX: was missing)
        if self.notifier is not None:
            try:
                closed_trade = self.journal.get_trade_by_id(trade_id) or {}
                # Compute hold time in hours
                entry_time = closed_trade.get("entry_time")
                exit_ts = int(exit_time if exit_time is not None else time.time())
                if entry_time:
                    hold_hours = (exit_ts - int(entry_time)) / 3600.0
                else:
                    hold_hours = 0.0
                # Compute daily P/L for context
                daily_pnl = self.journal.daily_pnl_today()
                self.notifier.notify_exit({
                    "trade_id": trade_id,
                    "symbol": closed_trade.get("symbol", "?"),
                    "direction": closed_trade.get("direction", "?"),
                    "grade": closed_trade.get("grade", "?"),
                    "entry_price": closed_trade.get("entry_price", 0),
                    "exit_price": exit_price,
                    "pnl_usd": pnl,
                    "pnl_pct": pnl / max(1.0, trade.get("risk_usd", 1.0)) if trade.get("risk_usd") else 0.0,
                    "r_multiple": r_multiple,
                    "hold_time_hours": hold_hours,
                    "exit_reason": exit_reason,
                    "daily_pnl_usd": daily_pnl,
                    "daily_pnl_pct": daily_pnl / max(1.0, self.get_initial_balance()),
                    "equity": equity_after,
                    "win_rate": 0.0,  # notifier doesn't use this; computed at report level
                })
            except Exception as exc:  # noqa: BLE001
                logger.error(f"[portfolio] notify_exit failed: {exc}")
        return self.journal.get_trade_by_id(trade_id) or {}

    def adjust_sl_to_breakeven(
        self, trade_id: str, new_sl: float | None = None
    ) -> bool:
        """
        Optional helper: move SL to entry (breakeven) for a trade.
        Returns True if updated.
        Only takes effect if PAPER_TP1_HIT_BREAKEVEN is True.
        """
        if not PAPER_TP1_HIT_BREAKEVEN:
            return False
        trade = self.journal.get_trade_by_id(trade_id)
        if trade is None or trade["status"] != "open":
            return False
        target_sl = (
            float(new_sl) if new_sl is not None else float(trade["entry_price"])
        )
        # Don't loosen the stop
        if trade["direction"] == "long" and target_sl < trade["sl"]:
            return False
        if trade["direction"] == "short" and target_sl > trade["sl"]:
            return False
        self.journal.conn.execute(
            "UPDATE paper_trades SET sl = ? WHERE trade_id = ?",
            (target_sl, trade_id),
        )
        return True

    def close_all(self, reason: str = "manual_close_all") -> int:
        """
        Emergency close every open position. Returns count closed.
        Exit price = current entry (no live data available) so PnL=0.
        Use `PaperTrader.monitor_loop()` for proper mark-to-market close.
        """
        positions = self.journal.get_open_positions()
        count = 0
        for p in positions:
            try:
                self.close_position(
                    trade_id=p["trade_id"],
                    exit_price=p["entry_price"],
                    exit_reason=reason,
                )
                count += 1
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    f"[portfolio] close_all failed for {p['trade_id']}: {exc}"
                )
        return count


__all__ = ["PaperPortfolio"]
