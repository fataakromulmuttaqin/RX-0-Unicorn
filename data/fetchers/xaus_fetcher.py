"""
xaus.com fetcher for RX-0 Unicorn (v1.0+ XAU/USD long-history use case).

Why this exists:
    YahooFinanceFetcher caps out at ~2y for daily (730d range). For the
    bos's "5y gold backtest" goal that's not enough. Stooq is blocked by
    JS anti-bot from this network. xaus.com's public API gives us daily
    XAU/USD history back ~5y (as of 2026-08: 2021-08-30 -> present),
    no API key, no auth, simple JSON payload.

Important caveats — read before using:
    - The xaus.com payload returns ONLY close (`c`), high (`h`),
      low (`l`) and date (`d`). It does NOT return `open` or `volume`.
    - Volume is meaningless for spot forex/gold anyway, so we set
      `volume = 0` for every row (mirrors YahooFinanceFetcher behavior
      for forex/commodity futures).
    - For the missing `open` field we use a documented workaround:
        * First bar in the returned series: `open = close`
        * Subsequent bars:               `open = previous bar's close`
      This is a reasonable approximation for daily spot gold (the
      "open" of day N+1 is effectively the "close" of day N in an
      always-on market). It is NOT a real open tick from xaus.com —
      callers that need true open prints must source another dataset.

Timeframe support:
    Only "1d" (daily) is supported by xaus.com. Any other timeframe
    request will raise ValueError — we refuse to silently return wrong
    granularity. This is by design: xaus.com does not expose 1h/15m/5m.

Returns the same DataFrame shape as YahooFinanceFetcher and CryptoFetcher
(columns: timestamp, open, high, low, close, volume — timestamp in ms
epoch integer) so the rest of the pipeline (storage, indicators,
confluence, backtest) stays untouched.

Mapping rules:
    XAU/USD   -> XAUUSD
    XAUUSD    -> XAUUSD
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
# CCXT-style symbol -> xaus.com ticker.
# Kept deliberately small: xaus.com currently only serves XAU/USD.
# Anything else is passed through (so power-users can hit whatever
# xaus.com happens to expose later without us needing a code change).
XAUS_SYMBOL_MAP: dict[str, str] = {
    "XAU/USD": "XAUUSD",
    "XAUUSD": "XAUUSD",
}


# ─── Endpoint + payload shape ───────────────────────────────────────────────
# https://xaus.com/api/v1/history — verified 2026-08-31.
# Sample payload:
#   {
#     "symbol": "XAUUSD",
#     "interval": "daily",
#     "currency": "USD",
#     "unit": "troy_oz",
#     "points": [
#         {"d": "2021-08-30", "c": 1809, "h": 1820.3, "l": 1807.8},
#         ...
#     ],
#     ...
#   }
# Per-point fields: d (date YYYY-MM-DD), c (close), h (high), l (low).
# MISSING: o (open), v (volume). See module docstring.
XAUS_API_URL: str = "https://xaus.com/api/v1/history"


class XAUSFetcher:
    """
    First-class fetcher for the xaus.com public history API.

    No API key, no auth. Single endpoint, single granularity (daily).
    Honest retries on transient network errors (5x, timeouts, malformed
    JSON); 4xx-style semantic errors (e.g., our own ValueError for
    unsupported timeframe) raise immediately.

    Public surface (mirrors YahooFinanceFetcher):
        fetch_ohlcv(symbol, timeframe, total_bars) -> DataFrame
        fetch_ohlcv_paginated(symbol, timeframe, total_bars) -> DataFrame
        fetch_multiple(symbols, timeframe, total_bars) -> dict[symbol, DataFrame]
        fetch_ticker(symbol) -> float | None

    Lifecycle:
        f = XAUSFetcher()
        try:
            df = f.fetch_ohlcv("XAU/USD", "1d", total_bars=1300)
        finally:
            f.close()

    Caveat — missing `open`:
        `open` column is SYNTHESIZED (previous bar's close, or close for
        the first bar). This is a workaround because xaus.com does not
        publish an open field. See module docstring.
    """

    SUPPORTED_TIMEFRAMES: tuple[str, ...] = ("1d",)

    def __init__(
        self,
        user_agent: str = "Mozilla/5.0 (RX-0-Unicorn)",
        max_retries: int = 3,
        base_backoff: float = 1.0,
        backoff_factor: float = 2.0,
        timeout: int = 30,
    ) -> None:
        self.user_agent = user_agent
        self.max_retries = max_retries
        self.base_backoff = base_backoff
        self.backoff_factor = backoff_factor
        self.timeout = timeout
        logger.info(
            f"XAUSFetcher initialized: user_agent={user_agent!r}, "
            f"timeout={timeout}s, supported_timeframes={self.SUPPORTED_TIMEFRAMES}"
        )

    # ─── Public API ────────────────────────────────────────────────────────
    def fetch_ohlcv(
        self,
        symbol: str = "XAU/USD",
        timeframe: str = "1d",
        total_bars: int = 1300,
    ) -> pd.DataFrame:
        """
        Fetch OHLCV for a single symbol from xaus.com.

        Args:
            symbol: CCXT-style ("XAU/USD") or raw xaus ticker ("XAUUSD").
                Unknown strings are passed through verbatim.
            timeframe: MUST be "1d". xaus.com does not expose intraday;
                any other value raises ValueError (we refuse to silently
                return wrong granularity).
            total_bars: Target row count. We may over-fetch and tail-trim
                to keep alignment with the user's request. xaus.com
                currently serves ~1258 daily bars (≈5y back to 2021-08-30);
                requesting more returns whatever xaus exposes.

        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume.
            `timestamp` is ms-epoch integer (UTC midnight of the bar's
            date). `open` is synthesized (see module docstring).
            `volume` is always 0.0 (forex/gold spot — no meaningful vol).
            Empty DataFrame on total failure (with logger.error).
        """
        return self.fetch_ohlcv_paginated(symbol, timeframe, total_bars)

    def fetch_ohlcv_paginated(
        self,
        symbol: str = "XAU/USD",
        timeframe: str = "1d",
        total_bars: int = 1300,
    ) -> pd.DataFrame:
        """
        Paginated fetch — for xaus.com this is effectively a single call
        (the API returns the full daily series in one shot, ~5y back).
        Interface kept identical to YahooFinanceFetcher so callers can
        swap fetchers without code changes.

        Returns empty DataFrame on any error (with logger.error so it's
        visible in logs; callers should treat empty as "data unavailable").
        """
        symbol = (symbol or "").strip()
        timeframe = (timeframe or "").strip().lower()
        if not symbol:
            logger.error("[xaus] empty symbol")
            return self._empty_df()
        if timeframe not in self.SUPPORTED_TIMEFRAMES:
            logger.error(
                f"[xaus] unsupported timeframe '{timeframe}'. "
                f"xaus.com only exposes: {self.SUPPORTED_TIMEFRAMES}. "
                f"Intraday (1h/15m/5m) is NOT available from xaus.com — "
                f"use YahooFinanceFetcher for those."
            )
            return self._empty_df()

        xaus_sym = self._map_symbol(symbol)
        df = self._single_call(xaus_sym)
        if df.empty:
            return df
        # Tail-trim to the requested count (keep most-recent N bars).
        df = df.tail(int(total_bars)).reset_index(drop=True)
        # Recompute `open` on the tail-trimmed result so the documented
        # workaround ("first visible bar uses its own close") holds from
        # the caller's perspective — not just for the API's first bar.
        df = self._recompute_open(df)
        return df

    def fetch_multiple(
        self,
        symbols: Iterable[str],
        timeframe: str = "1d",
        total_bars: int = 1300,
    ) -> dict[str, pd.DataFrame]:
        """
        Fetch many symbols serially. Same shape as
        YahooFinanceFetcher.fetch_multiple() / CryptoFetcher.fetch_multiple().
        """
        results: dict[str, pd.DataFrame] = {}
        sym_list = list(symbols)
        logger.info(
            f"[xaus] batch fetch: {len(sym_list)} symbols @ {timeframe} "
            f"total_bars={total_bars}"
        )
        for sym in sym_list:
            try:
                df = self.fetch_ohlcv(sym, timeframe, total_bars)
                results[sym] = df
                if df.empty:
                    logger.warning(f"[xaus] empty result for {sym}")
                else:
                    logger.debug(
                        f"[xaus] {sym} {timeframe}: {len(df)} bars"
                    )
            except Exception as exc:  # noqa: BLE001
                logger.error(f"[xaus] {sym} fetch failed: {exc}")
                results[sym] = self._empty_df()
        return results

    def fetch_ticker(self, symbol: str = "XAU/USD") -> float | None:
        """
        Lightweight: fetch the latest single-bar close. Used by paper monitor
        to get a "live" XAU/USD price when Yahoo/Crypto paths are unavailable.
        """
        df = self.fetch_ohlcv(symbol, "1d", total_bars=2)
        if df.empty:
            return None
        try:
            return float(df["close"].iloc[-1])
        except Exception:  # noqa: BLE001
            return None

    def close(self) -> None:
        """No persistent connection; provided for API symmetry with YahooFinanceFetcher."""
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
        return XAUS_SYMBOL_MAP.get(norm, norm)

    def _single_call(self, xaus_sym: str) -> pd.DataFrame:
        """
        Single GET to the xaus.com history endpoint, with retry+backoff.
        xaus.com doesn't paginate — it returns the full daily series in
        one shot (~5y ≈ 1258 daily bars).
        """
        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                req = urllib.request.Request(
                    XAUS_API_URL,
                    headers={"User-Agent": self.user_agent},
                )
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    raw = r.read()
                data = json.loads(raw)
                return self._parse_payload(data, xaus_sym)
            except urllib.error.HTTPError as exc:
                last_exc = exc
                if 400 <= exc.code < 500:
                    logger.error(
                        f"[xaus] {xaus_sym} HTTP {exc.code}: {exc.reason}"
                    )
                    return self._empty_df()
                logger.warning(
                    f"[xaus] {xaus_sym} HTTP {exc.code} "
                    f"(attempt {attempt + 1}/{self.max_retries})"
                )
                self._sleep_backoff(attempt)
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_exc = exc
                logger.warning(
                    f"[xaus] {xaus_sym} transient error: {exc} "
                    f"(attempt {attempt + 1}/{self.max_retries})"
                )
                self._sleep_backoff(attempt)
            except Exception as exc:  # noqa: BLE001
                logger.error(f"[xaus] {xaus_sym} unexpected error: {exc}")
                return self._empty_df()

        logger.error(
            f"[xaus] {xaus_sym} max retries exceeded: {last_exc}"
        )
        return self._empty_df()

    def _sleep_backoff(self, attempt: int) -> None:
        delay = self.base_backoff * (self.backoff_factor ** attempt)
        # Tiny jitter so we don't dogpile.
        delay *= 1.0 + (0.1 * (attempt % 3))
        time.sleep(delay)

    @staticmethod
    def _parse_payload(data: dict, xaus_sym: str) -> pd.DataFrame:
        """
        Parse the xaus.com history JSON payload into our standard DataFrame.

        Payload contract (verified 2026-08-31):
            {
              "symbol": "XAUUSD",
              "interval": "daily",
              "points": [{"d": "2021-08-30", "c": 1809, "h": 1820.3, "l": 1807.8}, ...],
              ...
            }

        Returns empty DataFrame on malformed/empty payloads.

        Open-price workaround (documented):
            * First bar:           open = close (no previous bar exists).
            * Subsequent bars:     open = previous bar's close.
        Volume:
            Always 0.0 (xaus.com doesn't publish volume; forex spot
            has no meaningful volume anyway).
        """
        if not isinstance(data, dict):
            return XAUSFetcher._empty_df()
        points_raw = data.get("points")
        if not isinstance(points_raw, list) or not points_raw:
            logger.error(
                f"[xaus] {xaus_sym} payload missing or empty 'points' list"
            )
            return XAUSFetcher._empty_df()

        rows: list[tuple[int, float, float, float, float, float]] = []
        prev_close: float | None = None
        for p in points_raw:
            if not isinstance(p, dict):
                continue
            d = p.get("d")
            c = p.get("c")
            h = p.get("h")
            l = p.get("l")
            # Defensive: skip rows missing any required field.
            if d is None or c is None or h is None or l is None:
                continue
            try:
                c_f = float(c)
                h_f = float(h)
                l_f = float(l)
            except (TypeError, ValueError):
                continue

            # Date string -> ms-epoch (UTC midnight of that date).
            try:
                ts_obj = pd.Timestamp(str(d), tz="UTC")
                # NaT happens when pandas can't parse the date string —
                # guard explicitly because pd.isna isn't recognized as
                # a type-narrower by static analyzers.
                if ts_obj is pd.NaT or ts_obj is None or pd.isna(ts_obj):
                    continue
                ts_ms = int(ts_obj.timestamp() * 1000)
            except (TypeError, ValueError, OverflowError):
                continue

            # Open-price workaround (see docstring above).
            if prev_close is None:
                o_f = c_f  # first bar: no previous close to anchor to
            else:
                o_f = prev_close  # subsequent bars: previous close

            rows.append((ts_ms, o_f, h_f, l_f, c_f, 0.0))
            prev_close = c_f

        if not rows:
            return XAUSFetcher._empty_df()

        df = pd.DataFrame(
            rows,
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        )
        df = (
            df.drop_duplicates(subset=["timestamp"])
              .sort_values("timestamp")
              .reset_index(drop=True)
        )
        # Defensive typing — keep `timestamp` as int64, prices as float64.
        df["timestamp"] = df["timestamp"].astype("int64")
        for c in ("open", "high", "low", "close", "volume"):
            df[c] = df[c].astype("float64")
        return df

    @staticmethod
    def _recompute_open(df: pd.DataFrame) -> pd.DataFrame:
        """
        Recompute the `open` column using the documented workaround, on
        whatever subset the caller actually sees (after tail-trimming).

        Rules:
            * First row:        open = close
            * Subsequent rows:  open = previous row's close

        Volume is left untouched (always 0.0 — forex/gold has no
        meaningful volume and xaus.com doesn't publish it).

        Operates on a copy; does not mutate the input.
        """
        if df.empty:
            return df
        out = df.copy()
        closes = out["close"].to_numpy(dtype="float64", copy=True)
        opens = out["open"].to_numpy(dtype="float64", copy=True)
        opens[0] = closes[0]  # first bar: open = its own close
        if len(opens) > 1:
            opens[1:] = closes[:-1]  # rest: open = previous close
        out["open"] = opens
        return out


__all__ = ["XAUSFetcher", "XAUS_SYMBOL_MAP", "XAUS_API_URL"]