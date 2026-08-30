"""
PaperTrader — high-level orchestrator (Phase 6).

Wraps confluence signals into simulated entries, polls for SL/TP hits via
CCXT, manages TP1 partial close + breakeven stop.

Pipeline:
    signal dict (from confluence) -> PaperPortfolio.open_position()
    monitor loop -> check SL / TP1 / TP2 against last close -> close

Sizing math, balance management, and DB persistence all live in
PaperPortfolio + PaperJournal. PaperTrader is the "glue".
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from src.config import (
    CONFLUENCE_A_PLUS,
    CONFLUENCE_MIN_VALID,
    PAPER_MAX_BARS_HOLD,
    PAPER_MONITOR_INTERVAL_SECONDS,
    PAPER_RISK_PER_TRADE,
    PAPER_TP1_CLOSE_PCT,
    PAPER_TP1_HIT_BREAKEVEN,
    PAPER_TP1_RR_RATIO,
    PAPER_TP2_RR_RATIO,
    PAPER_TIME_STOP_SECONDS,
)
from src.logger import logger

from .journal import PaperJournal
from .notifier import PaperNotifier
from .portfolio import PaperPortfolio


def make_trade_id(symbol: str, direction: str) -> str:
    """Generate a unique trade_id like 'BTCUSDT-L-20260829T1430-7f3a'."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    short = symbol.replace("/", "").replace(":", "")
    suffix = uuid.uuid4().hex[:4]
    dir_short = "L" if direction.lower() == "long" else "S"
    return f"{short}-{dir_short}-{ts}-{suffix}"


class PaperTrader:
    """
    High-level paper trading API.

    Usage:
        with PaperJournal() as j:
            trader = PaperTrader(journal=j, notifier=PaperNotifier())
            trader.portfolio.start()
            trader.open_from_signal(signal_dict)
            trader.monitor_loop(price_fetcher=lambda sym: get_ticker_price(sym))
    """

    def __init__(
        self,
        *,
        journal: PaperJournal,
        risk_per_trade: float = PAPER_RISK_PER_TRADE,
        notifier: PaperNotifier | None = None,
    ) -> None:
        self.journal = journal
        self.notifier: PaperNotifier | None = notifier
        self.portfolio = PaperPortfolio(journal=journal, notifier=notifier)
        self.risk_per_trade = float(risk_per_trade)

    # --- High level: open from confluence signal ---
    def open_from_signal(
        self,
        signal: dict[str, Any],
        *,
        symbol: str,
        signal_source: str = "scanner",
        entry_price: float | None = None,
        notes: str | None = None,
    ) -> dict[str, Any] | None:
        """
        Open a paper position from a confluence signal dict.
        `signal` must have: score, grade, direction, stop_loss,
        take_profit_1, take_profit_2.
        `entry_price` defaults to signal['entry_price'] or close.
        Returns the open trade dict, or None if skipped.
        """
        score = int(signal.get("score", 0))
        grade = str(signal.get("grade", "")).lower()
        direction = str(signal.get("direction", "")).lower()
        if score < CONFLUENCE_MIN_VALID or not direction:
            logger.debug(
                f"[trader] skip {symbol}: score={score} direction={direction}"
            )
            return None
        if grade not in ("a_plus", "valid"):
            logger.debug(f"[trader] skip {symbol}: grade={grade}")
            return None

        sl = signal.get("stop_loss")
        tp1 = signal.get("take_profit_1")
        tp2 = signal.get("take_profit_2")
        if sl is None or tp1 is None or tp2 is None:
            logger.warning(
                f"[trader] skip {symbol}: missing sl/tp1/tp2 in signal"
            )
            return None

        # entry price: prefer explicit, else signal entry_price, else close
        if entry_price is None:
            entry_price = signal.get("entry_price") or signal.get("close")
        if entry_price is None or float(entry_price) <= 0:
            logger.warning(f"[trader] skip {symbol}: invalid entry_price")
            return None

        size_multiplier = float(signal.get("size_multiplier", 1.0))
        # A+ gets 1.5x, valid gets 1.0x — already encoded by confluence scorer
        # but be defensive:
        if grade == "a_plus":
            size_multiplier = max(size_multiplier, 1.5)

        # Check risk gates (pass symbol for correlation check)
        allowed, reason = self.portfolio.can_open_new_position(symbol=symbol)
        if not allowed:
            logger.info(
                f"[trader] cannot open {symbol}: {reason} "
                f"(score={score} grade={grade})"
            )
            return None

        # Re-compute TP1/TP2 from R-multiples if they're suspiciously close
        # to entry (defensive — confluence scorer already does this).
        sl_dist = abs(float(entry_price) - float(sl))
        if sl_dist > 0:
            expected_tp1 = (
                float(entry_price) + sl_dist * PAPER_TP1_RR_RATIO
                if direction == "long"
                else float(entry_price) - sl_dist * PAPER_TP1_RR_RATIO
            )
            expected_tp2 = (
                float(entry_price) + sl_dist * PAPER_TP2_RR_RATIO
                if direction == "long"
                else float(entry_price) - sl_dist * PAPER_TP2_RR_RATIO
            )
            # If signal's TP is too tight, use ours instead
            if abs(float(tp1) - float(entry_price)) < sl_dist * 0.8:
                tp1 = expected_tp1
            if abs(float(tp2) - float(entry_price)) < sl_dist * 1.5:
                tp2 = expected_tp2

        trade_id = make_trade_id(symbol, direction)
        try:
            trade = self.portfolio.open_position(
                trade_id=trade_id,
                symbol=symbol,
                direction=direction,
                entry_price=float(entry_price),
                sl=float(sl),
                tp1=float(tp1),
                tp2=float(tp2),
                confluence_score=score,
                grade=grade,
                size_multiplier=size_multiplier,
                risk_per_trade=self.risk_per_trade,
                signal_source=signal_source,
                notes=notes,
            )
        except (RuntimeError, ValueError) as exc:
            logger.error(f"[trader] open failed for {symbol}: {exc}")
            return None
        logger.success(
            f"[trader] OPENED {direction.upper()} {symbol} @ "
            f"{float(entry_price):.4f} sl={float(sl):.4f} "
            f"tp1={float(tp1):.4f} tp2={float(tp2):.4f} "
            f"score={score}/4 grade={grade} id={trade_id}"
        )
        # Tier 1 Telegram notification
        if self.notifier is not None:
            try:
                self.notifier.notify_entry(trade)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[trader] notify_entry error: {exc}")
        return trade

    # --- Close (manual or via monitor) ---
    def close_trade(
        self,
        trade_id: str,
        exit_price: float,
        exit_reason: str,
    ) -> dict[str, Any] | None:
        """Close a specific open trade."""
        try:
            return self.portfolio.close_position(
                trade_id=trade_id,
                exit_price=exit_price,
                exit_reason=exit_reason,
            )
        except (KeyError, ValueError) as exc:
            logger.error(f"[trader] close {trade_id} failed: {exc}")
            return None

    def close_all(self) -> int:
        return self.portfolio.close_all(reason="manual_close_all")

    # --- Monitor loop ---
    def check_one_position(
        self, trade: dict[str, Any], current_price: float
    ) -> dict[str, Any] | None:
        """
        Decide whether to close `trade` given current price.
        Returns the close event dict (closed trade), or None.

        Logic (conservative — same as backtest engine):
            1. SL hit first if low <= sl (for long) or high >= sl (short)
               within current bar.
            2. Else TP1 hit -> close partial (50%), move SL to breakeven.
            3. Else TP2 hit -> close remainder.
            4. Else time-stop.
        For polling-based monitor, we use a simplified rule:
            - price <= SL  -> SL hit
            - price >= TP2 -> TP2 hit (close full)
            - price >= TP1 -> TP1 hit (close partial)
        We use last close price as proxy for "current price".
        """
        direction = trade["direction"]
        entry = float(trade["entry_price"])
        sl = float(trade["sl"])
        tp1 = float(trade["tp1"])
        tp2 = float(trade["tp2"])
        price = float(current_price)

        # Direction-aware triggers
        if direction == "long":
            sl_hit = price <= sl
            tp1_hit = price >= tp1
            tp2_hit = price >= tp2
        elif direction == "short":
            sl_hit = price >= sl
            tp1_hit = price <= tp1
            tp2_hit = price <= tp2
        else:
            logger.warning(f"[trader] unknown direction '{direction}'")
            return None

        # SL has highest priority (worst-case)
        if sl_hit and tp1_hit:
            # Both hit — assume SL (pessimistic like backtest engine)
            return self._close_full(trade, price, "sl")
        if sl_hit:
            return self._close_full(trade, price, "sl")
        if tp2_hit:
            return self._close_full(trade, price, "tp2")
        if tp1_hit:
            return self._close_partial_tp1(trade, price)

        # Time-stop check
        entry_time = int(trade["entry_time"])
        elapsed = int(time.time()) - entry_time
        if elapsed > PAPER_TIME_STOP_SECONDS:
            return self._close_full(trade, price, "time_stop")
        return None

    def _close_full(
        self, trade: dict[str, Any], price: float, reason: str
    ) -> dict[str, Any] | None:
        closed = self.close_trade(trade["trade_id"], price, reason)
        if closed is not None:
            logger.success(
                f"[trader] {reason.upper()} {trade['symbol']} "
                f"@ {price:.4f} pnl=${closed.get('pnl_usd', 0):+.2f}"
            )
            # Tier 2 Telegram notification
            if self.notifier is not None:
                try:
                    self.notifier.notify_exit(closed)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"[trader] notify_exit error: {exc}")
        return closed

    def _close_partial_tp1(
        self, trade: dict[str, Any], price: float
    ) -> dict[str, Any] | None:
        """
        TP1 hit: close PAPER_TP1_CLOSE_PCT of position, move SL to
        breakeven (if enabled), leave rest to run to TP2.
        For simplicity in paper tracking, we just close the WHOLE
        position at TP1 and record exit_reason='tp1' — the journal
        keeps the original TP1/TP2 plan. This matches backtest
        behavior where 'tp1' means "closed at TP1".
        """
        # Adjust SL to breakeven if enabled
        if PAPER_TP1_HIT_BREAKEVEN:
            self.portfolio.adjust_sl_to_breakeven(trade["trade_id"])
        closed = self.close_trade(trade["trade_id"], price, "tp1")
        if closed is not None:
            logger.success(
                f"[trader] TP1 {trade['symbol']} @ {price:.4f} "
                f"pnl=${closed.get('pnl_usd', 0):+.2f} "
                f"(closed={PAPER_TP1_CLOSE_PCT:.0%} of plan)"
            )
            # Tier 2 Telegram notification
            if self.notifier is not None:
                try:
                    self.notifier.notify_exit(closed)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"[trader] notify_exit error: {exc}")
        return closed

    def monitor_loop(
        self,
        *,
        price_fetcher: Callable[[str], float | None],
        interval_seconds: int = PAPER_MONITOR_INTERVAL_SECONDS,
        once: bool = False,
    ) -> int:
        """
        Poll every `interval_seconds`: for each open position, fetch
        current price and check SL/TP/time-stop.

        `price_fetcher(symbol)` -> last price (or None on failure).
        If `once=True`, run a single pass and return (used by tests).
        Returns number of cycles executed.
        """
        cycles = 0
        try:
            while True:
                cycles += 1
                open_positions = self.journal.get_open_positions()
                if not open_positions:
                    logger.info(
                        f"[trader] monitor cycle {cycles}: no open positions"
                    )
                else:
                    closed_count = 0
                    for p in open_positions:
                        sym = p["symbol"]
                        price = None
                        try:
                            price = price_fetcher(sym)
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                f"[trader] price fetch failed for {sym}: {exc}"
                            )
                        if price is None or float(price) <= 0:
                            continue
                        result = self.check_one_position(p, float(price))
                        if result is not None:
                            closed_count += 1
                    logger.info(
                        f"[trader] monitor cycle {cycles}: "
                        f"open={len(open_positions)} closed={closed_count}"
                    )
                if once:
                    return cycles
                time.sleep(max(1, int(interval_seconds)))
        except KeyboardInterrupt:
            logger.warning("[trader] monitor interrupted by user (Ctrl+C)")
            return cycles
        return cycles


# --- Convenience fetchers ---
def ccxt_price_fetcher(exchange) -> Callable[[str], float | None]:  # noqa: ANN001
    """
    Build a price_fetcher that uses a CCXT exchange instance.
    exchange: ccxt.binance() or similar.
    Returns a function: symbol -> last price (or None on failure).
    """
    def _fetch(symbol: str) -> float | None:
        try:
            t = exchange.fetch_ticker(symbol)
            return float(t.get("last") or 0) or None
        except Exception:  # noqa: BLE001
            return None
    return _fetch


__all__ = ["PaperTrader", "make_trade_id", "ccxt_price_fetcher"]
