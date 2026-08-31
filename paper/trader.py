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
    PAPER_MTF_4H_MIN_SCORE,
    PAPER_MTF_BIAS_CACHE_TTL,
    PAPER_MTF_DAILY_MIN_SCORE,
    PAPER_MTF_DAILY_SYMBOL,
    PAPER_MTF_ENABLED,
    PAPER_MTF_15M_MIN_SCORE,
    PAPER_MTF_TIGHT_ENABLED,
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


# --- MTF (Multi-Timeframe) helpers — v1.1.0 Relaxed MTF Combo ---
# Validated via backtest (/tmp/xauusd_mtf_tweaks_report.md):
#   - Relaxed MTF (1D≥1 + 15M≥2): 31 trades, WR 71%, PF 2.18, DD 3.91%, PnL +$1431
#   - Pure 15M (no filter): 45 trades, WR 48.9%, PF 0.82, DD 8.95%, PnL -$492
# Filter blocks trades yang melawan daily bias — higher quality, lower frequency.

# Simple in-memory cache untuk daily bias (avoid fetch tiap signal)
_daily_bias_cache: dict[str, tuple[float, str | None, int | None]] = {}


def _fetch_daily_bias(symbol: str) -> tuple[str | None, int | None]:
    """
    Ambil daily bias terkini untuk symbol tertentu.

    Returns:
        (direction, score) — direction adalah "long" / "short" / None.
        score adalah confluence_score (0-4) di daily bar terakhir.
        None direction = tidak ada valid bias (semua entry 15M di-block).
    """
    import time
    import pandas as pd
    from data.fetchers.xaus_fetcher import XAUSFetcher
    from confluence.scorer import score_confluence

    now = time.time()
    cached = _daily_bias_cache.get(symbol)
    if cached and (now - cached[0]) < PAPER_MTF_BIAS_CACHE_TTL:
        return cached[1], cached[2]

    try:
        fetcher = XAUSFetcher()
        df = fetcher.fetch_ohlcv(symbol, "1d", total_bars=60)
        if df is None or df.empty or len(df) < 20:
            logger.warning(f"[MTF] insufficient daily data for {symbol}")
            return None, None
        scored = score_confluence(df)
        last = scored.iloc[-1]
        direction = last.get("confluence_direction")
        score = int(last.get("confluence_score", 0) or 0)
        grade = str(last.get("confluence_grade", ""))
        if pd.isna(direction) or grade != "valid" or score < PAPER_MTF_DAILY_MIN_SCORE:
            _daily_bias_cache[symbol] = (now, None, score)
            return None, score
        _daily_bias_cache[symbol] = (now, str(direction), score)
        return str(direction), score
    except Exception as e:
        logger.error(f"[MTF] daily bias fetch failed for {symbol}: {e}")
        return None, None


def _clear_daily_bias_cache() -> None:
    """Reset cache — dipanggil di tests."""
    _daily_bias_cache.clear()


def check_mtf_filter(trade_direction: str, symbol: str | None = None) -> bool:
    """
    Cek apakah 15M/entry-TF signal diizinkan oleh HTF daily bias.

    Returns True (allow) kalau:
      - PAPER_MTF_ENABLED=False (default OFF, backward-compatible)
      - daily bias match dengan trade_direction
      - daily bias None (skip — jangan buka trade saat bias unclear)

    Returns False (block) kalau daily bias != trade_direction.

    Symbol-scoping: kalau `symbol != PAPER_MTF_DAILY_SYMBOL`, MTF pass-through
    (filter hanya untuk symbol yang di-configure). Backtest validate XAU/USD only;
    BTC/USDT test fixtures di-test dengan MTF off via monkeypatch anyway.
    """
    if not PAPER_MTF_ENABLED:
        return True
    sym = symbol or PAPER_MTF_DAILY_SYMBOL
    # Only enforce filter for the configured daily symbol — other symbols
    # pass through (MTF not configured for them yet).
    if sym != PAPER_MTF_DAILY_SYMBOL:
        logger.debug(
            f"[MTF] skip filter for {sym}: MTF_DAILY_SYMBOL={PAPER_MTF_DAILY_SYMBOL}"
        )
        return True
    bias_dir, bias_score = _fetch_daily_bias(sym)
    if bias_dir is None:
        logger.info(
            f"[MTF] block {trade_direction} {sym}: no valid daily bias "
            f"(score={bias_score})"
        )
        return False
    if bias_dir != trade_direction:
        logger.info(
            f"[MTF] block {trade_direction} {sym}: daily bias={bias_dir} "
            f"(score={bias_score})"
        )
        return False
    logger.debug(
        f"[MTF] allow {trade_direction} {sym}: aligned with daily bias "
        f"({bias_dir}, score={bias_score})"
    )
    return True


# --- Tighter MTF (v1.1.1) — adds 4H layer between 1D and 15M ---
# Validated via backtest (/tmp/xauusd_mtf_tweaks_report.md):
#   tight_4h: 6 trades, WR 66.7%, PF 2.33, DD 1.92% (best DD), PnL +$264
# Strategy: 1D ≥ 1 + (1H ≥ 2 → aggregate to 4H) + 4H ≥ 1 + 15M ≥ 2,
#   all biases must match trade direction.
# 4H bias comes from Yahoo 1H data — Yahoo doesn't expose native 4h, so
# we groupby 4 consecutive 1H bars into a synthetic 4H bar.

# Separate in-memory cache for 4H bias (independent of daily cache).
_4h_bias_cache: dict[str, tuple[float, str | None, int | None]] = {}


def _aggregate_1h_to_4h(df: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate 1H OHLCV data into synthetic 4H bars.

    Groups every 4 consecutive 1H bars into one 4H bar. Takes the LAST
    value per group for OHLC + any pre-computed indicator columns (e.g.
    confluence_direction, confluence_score, confluence_grade). This means
    the 4H bias reflects the most recent 1H bar's indicator snapshot,
    which is the most operationally useful representation for a
    real-time filter.

    Expects df to have integer ms-epoch `timestamp` column + OHLCV.
    Returns a new DataFrame with len(df) // 4 rows (drops trailing
    remainder).
    """
    import pandas as pd

    if df is None or df.empty or len(df) < 4:
        return df.iloc[0:0].copy() if df is not None else pd.DataFrame()

    n_groups = len(df) // 4
    trimmed = df.iloc[: n_groups * 4].copy()
    # Integer position group (every group = exactly 4 rows since we trimmed).
    group_id = (trimmed.index // 4)
    agg = trimmed.groupby(group_id).agg(
        timestamp=("timestamp", "last"),
        open=("open", "last"),
        high=("high", "last"),
        low=("low", "last"),
        close=("close", "last"),
        volume=("volume", "last"),
    )
    # For any extra indicator/bias columns, take the last of each group
    # so that downstream confluence_score picks up the most recent state.
    extra_cols = [c for c in trimmed.columns if c not in agg.columns]
    for col in extra_cols:
        agg[col] = trimmed.groupby(group_id)[col].last()
    agg = agg.reset_index(drop=True)
    return agg


def _fetch_4h_bias(symbol: str) -> tuple[str | None, int | None]:
    """
    Ambil 4H bias terkini (aggregate dari Yahoo 1H data).

    Returns:
        (direction, score) — direction adalah "long" / "short" / None.
        score adalah confluence_score (0-4) di 4H bar terakhir.
        None direction = tidak ada valid 4H bias (block trade).

    Yahoo native 4H isn't supported; we fetch 1H (max 730d ≈ 17,520 bars)
    then aggregate every 4 bars into 4H. Resulting history ≈ 4380 4H bars.
    """
    import time
    import pandas as pd
    from data.fetchers.yahoo_fetcher import YahooFinanceFetcher
    from confluence.scorer import score_confluence

    now = time.time()
    cached = _4h_bias_cache.get(symbol)
    if cached and (now - cached[0]) < PAPER_MTF_BIAS_CACHE_TTL:
        return cached[1], cached[2]

    try:
        fetcher = YahooFinanceFetcher()
        # 720 days of 1H bars ≈ 17,280 rows. Yahoo max for 1h is 730d.
        df = fetcher.fetch_ohlcv(symbol, "1h", total_bars=17_500)
        if df is None or df.empty or len(df) < 100:
            logger.warning(f"[MTF-tight] insufficient 1H data for {symbol}")
            return None, None
        agg = _aggregate_1h_to_4h(df)
        if agg is None or agg.empty or len(agg) < 20:
            logger.warning(f"[MTF-tight] insufficient 4H aggregate for {symbol}")
            return None, None
        scored = score_confluence(agg)
        last = scored.iloc[-1]
        direction = last.get("confluence_direction")
        score = int(last.get("confluence_score", 0) or 0)
        grade = str(last.get("confluence_grade", ""))
        if pd.isna(direction) or grade != "valid" or score < PAPER_MTF_4H_MIN_SCORE:
            _4h_bias_cache[symbol] = (now, None, score)
            return None, score
        _4h_bias_cache[symbol] = (now, str(direction), score)
        return str(direction), score
    except Exception as e:
        logger.error(f"[MTF-tight] 4H bias fetch failed for {symbol}: {e}")
        return None, None


def _clear_4h_bias_cache() -> None:
    """Reset 4H bias cache — dipanggil di tests."""
    _4h_bias_cache.clear()


def check_tight_mtf_filter(
    trade_direction: str, symbol: str | None = None
) -> bool:
    """
    Tighter MTF filter — requires alignment across 1D + 1H (aggregated 4H) + 4H.

    Strategy (from /tmp/xauusd_mtf_tweaks_report.md):
        - 1D confluence ≥ 1 → trend bias
        - 1H confluence ≥ 2 → alignment filter (we use 4H aggregate here)
        - 4H confluence ≥ 1 → additional granularity
        - 15M confluence ≥ 2 → entry trigger (checked elsewhere)

    Returns True (allow) kalau:
      - PAPER_MTF_TIGHT_ENABLED=False (default OFF, backward-compatible —
        behaviour identical to Relaxed MTF when disabled)
      - All three biases match trade_direction

    Returns False (block) kalau any bias is None OR any bias mismatches.
    \"safer\" because a missing 4H bias means either data is unavailable
    or the 4H trend is unclear — both situations are bad entries.
    """
    if not PAPER_MTF_TIGHT_ENABLED:
        return True
    sym = symbol or PAPER_MTF_DAILY_SYMBOL
    # Symbol-scoping: only enforce filter for the configured daily symbol.
    if sym != PAPER_MTF_DAILY_SYMBOL:
        logger.debug(
            f"[MTF-tight] skip filter for {sym}: "
            f"MTF_DAILY_SYMBOL={PAPER_MTF_DAILY_SYMBOL}"
        )
        return True
    # 1D bias
    d_dir, d_score = _fetch_daily_bias(sym)
    if d_dir is None:
        logger.info(
            f"[MTF-tight] block {trade_direction} {sym}: no valid 1D bias "
            f"(score={d_score})"
        )
        return False
    # 4H bias (aggregated from 1H Yahoo data)
    h4_dir, h4_score = _fetch_4h_bias(sym)
    if h4_dir is None:
        logger.info(
            f"[MTF-tight] block {trade_direction} {sym}: no valid 4H bias "
            f"(score={h4_score})"
        )
        return False
    # All three must align (we re-check 1D here for symmetry / readability).
    if d_dir != trade_direction or h4_dir != trade_direction:
        logger.info(
            f"[MTF-tight] block {trade_direction} {sym}: "
            f"1D={d_dir}({d_score}) 4H={h4_dir}({h4_score})"
        )
        return False
    logger.debug(
        f"[MTF-tight] allow {trade_direction} {sym}: "
        f"1D={d_dir}({d_score}) 4H={h4_dir}({h4_score})"
    )
    return True


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

        # v1.1.0 Relaxed MTF — kalau enabled, filter signal melawan daily bias.
        # Cek DULUAN sebelum risk/sizing biar gak waste compute.
        if not check_mtf_filter(direction, symbol=symbol):
            logger.info(
                f"[trader] MTF blocked {symbol} {direction}: "
                f"score={score} grade={grade}"
            )
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
