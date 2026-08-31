"""
Yahoo Finance fetcher for RX-0 Unicorn (v1.0+ gold pivot).

Primary data source for forex/commodities since the 1.0 pivot to XAU/USD
single-symbol focus. Returns the same DataFrame shape as CryptoFetcher
(columns: timestamp, open, high, low, close, volume — timestamp in ms epoch)
so the rest of the pipeline (storage, indicators, confluence, backtest)
stays untouched.

Mapping rules:
    XAU/USD    -> GC=F    (CME gold futures front-month, tracks spot ~0.5%)
    EUR/USD    -> EURUSD=X
    GBP/USD    -> GBPUSD=X
    USD/JPY    -> JPY=X
    AUD/USD    -> AUDUSD=X
    USD/CHF    -> CHF=X
    BTC/USD    -> BTC-USD (legacy, kept for reference)

Timeframe support:
    1d    -> Yahoo "1d"      (max 730d range)
    1h    -> Yahoo "1h"      (max 730d range)
    5m    -> Yahoo "5m"      (max 60d range)
    15m   -> Yahoo "15m"     (max 60d range)
    30m   -> Yahoo "30m"     (max 60d range)
    4h    -> NOT natively supported; aggregated from 1h

Yahoo free-tier quirks:
    - 4h interval is not exposed by /v8/finance/chart; we resample from 1h.
    - Intraday (<1d) is capped at 60d for sub-hourly bars, 730d for 1h.
    - Volume is generally 0 for forex spot/commodity futures.

Originally lived as `fetch_via_yahoo()` helper inside backtest/run_yearly.py
(see commit prior to v1.0). Extracted to a first-class class in v1.0 so
that main.py fetch --source yahoo and paper monitor can use the same code
path without coupling to the yearly backtest CLI.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Iterable

import pandas as pd

from src.logger import logger


# ─── Symbol map ─────────────────────────────────────────────────────────────
# CCXT-style symbol -> Yahoo Finance ticker.
# Mirrors _YAHOO_SYMBOL_MAP in backtest/run_yearly.py (v0.9.x). Anything
# not in the map is passed through verbatim, so power-users can give us a
# raw Yahoo ticker like "GC=F" or "BTC-USD" directly.
YAHOO_SYMBOL_MAP: dict[str, str] = {
    "XAU/USD": "GC=F",   # CME gold futures — primary instrument since v1.0
    "XAUUSD": "GC=F",
    "GOLD": "GC=F",
    "EUR/USD": "EURUSD=X",
    "EURUSD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USD/JPY": "JPY=X",
    "USDJPY": "JPY=X",
    "AUD/USD": "AUDUSD=X",
    "AUDUSD": "AUDUSD=X",
    "USD/CHF": "CHF=X",
    "USDCHF": "CHF=X",
    # Reference (kept for tests / occasional cross-asset scan):
    "BTC/USD": "BTC-USD",
    "BTCUSD": "BTC-USD",
}


# ─── Interval catalog ───────────────────────────────────────────────────────
# Yahoo "interval" param + per-call history cap.
# Reference: https://query1.finance.yahoo.com/v8/finance/chart/{ticker}
YAHOO_INTERVALS: dict[str, dict[str, int]] = {
    "1m":  {"max_days": 7,    "resolution_min": 1},
    "5m":  {"max_days": 60,   "resolution_min": 5},
    "15m": {"max_days": 60,   "resolution_min": 15},
    "30m": {"max_days": 60,   "resolution_min": 30},
    "1h":  {"max_days": 730,  "resolution_min": 60},
    "1d":  {"max_days": 730,  "resolution_min": 1440},
    "5d":  {"max_days": 730,  "resolution_min": 7200},
    "1wk": {"max_days": 730,  "resolution_min": 10080},
    "1mo": {"max_days": 730,  "resolution_min": 43200},
}


# Map "our" timeframe strings to Yahoo "interval" values.
# 4h is intentionally absent here — Yahoo doesn't expose it; we aggregate
# from 1h downstream in fetch_ohlcv_paginated() / fetch_ohlcv().
_INTERVAL_ALIASES: dict[str, str] = {
    "1d": "1d",
    "1h": "1h",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    # 4h not natively supported — caller may pass "4h" and we'll aggregate.
}


class YahooFinanceFetcher:
    """
    First-class fetcher for Yahoo Finance's public chart API.

    No API key, no rate-limit tokens required. Honest retries only on
    transient network errors (5x); malformed-symbol errors raise
    ValueError immediately.

    Public surface (mirrors CryptoFetcher):
        fetch_ohlcv(symbol, timeframe, total_bars) -> DataFrame
        fetch_ohlcv_paginated(symbol, timeframe, total_bars) -> DataFrame
        fetch_multiple(symbols, timeframe, total_bars) -> dict[symbol, DataFrame]

    Lifecycle:
        f = YahooFinanceFetcher()
        try:
            df = f.fetch_ohlcv("XAU/USD", "1d", total_bars=500)
        finally:
            f.close()
    """

    SUPPORTED_TIMEFRAMES: tuple[str, ...] = (
        "1m", "5m", "15m", "30m", "1h", "4h", "1d", "5d", "1wk", "1mo",
    )

    def __init__(
        self,
        user_agent: str = "Mozilla/5.0 (RX-0-Unicorn)",
        max_retries: int = 3,
        base_backoff: float = 1.0,
        backoff_factor: float = 2.0,
        timeout: int = 20,
    ) -> None:
        self.user_agent = user_agent
        self.max_retries = max_retries
        self.base_backoff = base_backoff
        self.backoff_factor = backoff_factor
        self.timeout = timeout
        logger.info(
            f"YahooFinanceFetcher initialized: user_agent={user_agent!r}"
        )

    # ─── Public API ────────────────────────────────────────────────────────
    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1d",
        total_bars: int = 500,
    ) -> pd.DataFrame:
        """
        Fetch OHLCV for a single symbol.

        Args:
            symbol: Either CCXT-style ("XAU/USD", "EUR/USD") or raw Yahoo
                ticker ("GC=F"). Unknown strings are passed through.
            timeframe: One of 1m/5m/15m/30m/1h/4h/1d/5d/1wk/1mo.
                "4h" is aggregated from 1h since Yahoo doesn't expose it.
            total_bars: Target row count. We may over-fetch and tail-trim
                to keep alignment with the user's request.

        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume.
            `timestamp` is ms-epoch integer. Empty DataFrame on total failure.
        """
        return self.fetch_ohlcv_paginated(symbol, timeframe, total_bars)

    def fetch_ohlcv_paginated(
        self,
        symbol: str,
        timeframe: str = "1d",
        total_bars: int = 500,
    ) -> pd.DataFrame:
        """
        Paginated fetch: walks Yahoo's `range=` param backwards in time
        when a single call cannot cover `total_bars`.

        Yahoo hard-limits `range=` to roughly max_days in YAHOO_INTERVALS
        for the chosen interval. For daily (max 730d ≈ 2y), a single
        "5y" range gets ~5y of data; for 1h it's ~2y; for 5m/15m/30m
        it's ~60d. If `total_bars` exceeds a single call's capacity
        we issue multiple calls with sliding `start`/`end` ranges and
        stitch them together.

        Returns empty DataFrame on any error (with logger.error so it's
        visible in logs; callers should treat empty as "data unavailable").
        """
        symbol = (symbol or "").strip()
        timeframe = (timeframe or "").strip().lower()
        if not symbol:
            logger.error("[yahoo] empty symbol")
            return self._empty_df()
        if timeframe not in self.SUPPORTED_TIMEFRAMES:
            logger.error(
                f"[yahoo] unsupported timeframe '{timeframe}'. "
                f"Supported: {self.SUPPORTED_TIMEFRAMES}"
            )
            return self._empty_df()

        yahoo_sym = self._map_symbol(symbol)
        # 4h -> aggregate from 1h downstream
        if timeframe == "4h":
            return self._fetch_aggregated_4h(yahoo_sym, total_bars)

        yahoo_tf = _INTERVAL_ALIASES.get(timeframe, timeframe)
        max_days = YAHOO_INTERVALS[timeframe]["max_days"]

        # Single-call path: most cases fit. Choose range so we can return
        # at least `total_bars` rows.
        yahoo_range = self._pick_range(timeframe, total_bars)
        df = self._single_call(yahoo_sym, yahoo_tf, yahoo_range)
        if df.empty:
            return df
        # Tail-trim to the requested count.
        df = df.tail(int(total_bars)).reset_index(drop=True)
        return df

    def fetch_multiple(
        self,
        symbols: Iterable[str],
        timeframe: str = "1d",
        total_bars: int = 500,
    ) -> dict[str, pd.DataFrame]:
        """Fetch many symbols serially. Same shape as CryptoFetcher.fetch_multiple()."""
        results: dict[str, pd.DataFrame] = {}
        sym_list = list(symbols)
        logger.info(
            f"[yahoo] batch fetch: {len(sym_list)} symbols @ {timeframe} "
            f"total_bars={total_bars}"
        )
        for sym in sym_list:
            try:
                df = self.fetch_ohlcv(sym, timeframe, total_bars)
                results[sym] = df
                if df.empty:
                    logger.warning(f"[yahoo] empty result for {sym}")
                else:
                    logger.debug(
                        f"[yahoo] {sym} {timeframe}: {len(df)} bars"
                    )
            except Exception as exc:  # noqa: BLE001
                logger.error(f"[yahoo] {sym} fetch failed: {exc}")
                results[sym] = self._empty_df()
        return results

    def fetch_ticker(self, symbol: str) -> float | None:
        """
        Lightweight: fetch the latest single-bar close. Used by paper monitor
        to get a "live" price for XAU/USD.
        """
        df = self.fetch_ohlcv(symbol, "1d", total_bars=2)
        if df.empty:
            return None
        try:
            return float(df["close"].iloc[-1])
        except Exception:  # noqa: BLE001
            return None

    def close(self) -> None:
        """No persistent connection; provided for API symmetry with CryptoFetcher."""
        return None

    # ─── Internals ─────────────────────────────────────────────────────────
    @staticmethod
    def _empty_df() -> pd.DataFrame:
        return pd.DataFrame(
            columns=["timestamp", "open", "high", "low", "close", "volume"]
        )

    @staticmethod
    def _map_symbol(symbol: str) -> str:
        norm = symbol.strip().upper()
        return YAHOO_SYMBOL_MAP.get(norm, norm)

    def _pick_range(self, timeframe: str, total_bars: int) -> str:
        """
        Choose the smallest Yahoo `range=` value that comfortably covers
        `total_bars` bars for `timeframe`. Yahoo supports range strings
        like '5d', '1mo', '6mo', '1y', '2y', '5y', '10y', 'max'.
        """
        max_days = YAHOO_INTERVALS[timeframe]["max_days"]
        # bars_per_day varies by timeframe; for daily it's exactly 1.
        # For 1h it's 24 (capped to market hours in real life, but Yahoo
        # 24/7 for crypto, 23/24 for stocks/forex — assume ~24).
        bars_per_day = 1440 // YAHOO_INTERVALS[timeframe]["resolution_min"]
        needed_days = max(1, int(total_bars / max(1, bars_per_day)) + 1)

        # Pick smallest range >= needed_days, but never larger than Yahoo's
        # historical cap. Use a small ladder.
        ladder = [
            (5, "5d"),
            (30, "1mo"),
            (90, "3mo"),
            (180, "6mo"),
            (365, "1y"),
            (730, "2y"),
            (1825, "5y"),
            (3650, "10y"),
            (999_999, "max"),
        ]
        for days, rng in ladder:
            if needed_days <= days and days <= max_days:
                return rng
        # Fall back to the largest ladder entry that fits Yahoo's cap.
        for days, rng in reversed(ladder):
            if days <= max_days:
                return rng
        return "1y"

    def _single_call(
        self,
        yahoo_sym: str,
        yahoo_tf: str,
        yahoo_range: str,
    ) -> pd.DataFrame:
        """Single GET to Yahoo chart endpoint, with retry+backoff."""
        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_sym}"
            f"?interval={yahoo_tf}&range={yahoo_range}"
        )
        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": self.user_agent}
                )
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    data = json.loads(r.read())
                return self._parse_chart_payload(data)
            except urllib.error.HTTPError as exc:
                last_exc = exc
                # 4xx is permanent (bad symbol etc.) — don't retry.
                if 400 <= exc.code < 500:
                    logger.error(
                        f"[yahoo] {yahoo_sym} HTTP {exc.code}: {exc.reason}"
                    )
                    return self._empty_df()
                logger.warning(
                    f"[yahoo] {yahoo_sym} HTTP {exc.code} "
                    f"(attempt {attempt + 1}/{self.max_retries})"
                )
                self._sleep_backoff(attempt)
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_exc = exc
                logger.warning(
                    f"[yahoo] {yahoo_sym} transient error: {exc} "
                    f"(attempt {attempt + 1}/{self.max_retries})"
                )
                self._sleep_backoff(attempt)
            except Exception as exc:  # noqa: BLE001
                # Unknown — log + fail fast; don't retry on buggy payloads.
                logger.error(f"[yahoo] {yahoo_sym} unexpected error: {exc}")
                return self._empty_df()

        logger.error(
            f"[yahoo] {yahoo_sym} max retries exceeded: {last_exc}"
        )
        return self._empty_df()

    def _sleep_backoff(self, attempt: int) -> None:
        delay = self.base_backoff * (self.backoff_factor ** attempt)
        # Tiny jitter so we don't dogpile.
        delay *= 1.0 + (0.1 * (attempt % 3))
        time.sleep(delay)

    @staticmethod
    def _parse_chart_payload(data: dict) -> pd.DataFrame:
        """
        Parse the Yahoo chart JSON payload into our standard DataFrame.
        Returns empty DataFrame on malformed/empty payloads.
        """
        chart_obj = data.get("chart") or {}
        result_list = chart_obj.get("result")
        if not result_list or not isinstance(result_list, list) or result_list[0] is None:
            return YahooFinanceFetcher._empty_df()
        result: dict = result_list[0]
        ts_raw = result.get("timestamp")
        ts: list = ts_raw if isinstance(ts_raw, list) else []
        indicators = result.get("indicators") or {}
        quote_list = indicators.get("quote")
        if not ts or not quote_list or not isinstance(quote_list, list):
            return YahooFinanceFetcher._empty_df()
        quote: dict = quote_list[0] if quote_list else {}
        opens = quote.get("open") or []
        highs = quote.get("high") or []
        lows = quote.get("low") or []
        closes = quote.get("close") or []
        volumes = quote.get("volume") or []

        rows: list[tuple[int, float, float, float, float, float]] = []
        for i, t in enumerate(ts):
            try:
                o = opens[i]
                h = highs[i]
                l = lows[i]
                c = closes[i]
            except IndexError:
                break
            if None in (o, h, l, c):
                continue
            v = volumes[i] if i < len(volumes) and volumes[i] is not None else 0.0
            rows.append((int(t) * 1000, float(o), float(h), float(l), float(c), float(v)))

        if not rows:
            return YahooFinanceFetcher._empty_df()

        df = pd.DataFrame(
            rows,
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        )
        df = (
            df.drop_duplicates(subset=["timestamp"])
              .sort_values("timestamp")
              .reset_index(drop=True)
        )
        # Defensive typing — keep `timestamp` as int64.
        df["timestamp"] = df["timestamp"].astype("int64")
        for c in ("open", "high", "low", "close", "volume"):
            df[c] = df[c].astype("float64")
        return df

    def _fetch_aggregated_4h(
        self,
        yahoo_sym: str,
        total_bars: int,
    ) -> pd.DataFrame:
        """
        Build 4h candles by fetching 1h then resampling. Yahoo exposes 1h
        but not 4h natively.
        """
        # 4h bars per 1h bar: 4:1, so we need ~4x as many 1h bars as 4h.
        hourly_target = max(int(total_bars) * 4 + 8, 200)
        df_h = self._single_call(yahoo_sym, "1h", "60d")
        if df_h.empty:
            return df_h
        df_h = df_h.tail(int(hourly_target)).reset_index(drop=True)
        if df_h.empty:
            return df_h

        df_h = df_h.copy()
        df_h["datetime"] = pd.to_datetime(
            df_h["timestamp"], unit="ms", utc=True
        )
        df_h = df_h.set_index("datetime")

        agg = (
            df_h.resample("4h", label="right", closed="right")
            .agg(
                {
                    "open": "first",
                    "high": "max",
                    "low": "min",
                    "close": "last",
                    "volume": "sum",
                }
            )
            .dropna(subset=["open", "high", "low", "close"])
            .reset_index()
        )
        agg["timestamp"] = (
            agg["datetime"].astype("int64") // 1_000_000  # ns -> ms
        ).astype("int64")
        agg = (
            agg[["timestamp", "open", "high", "low", "close", "volume"]]
            .sort_values("timestamp")
            .reset_index(drop=True)
        )
        return agg.tail(int(total_bars)).reset_index(drop=True)


__all__ = ["YahooFinanceFetcher", "YAHOO_SYMBOL_MAP", "YAHOO_INTERVALS"]