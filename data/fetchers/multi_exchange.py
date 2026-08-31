"""
Multi-exchange OHLCV fetcher with intelligent fallback chain.

Primary: data-api.binance.vision (Binance data, no geo-block, full public API)
Secondary: Gate.io, HTX (work without SSL bypass)
Tertiary: direct REST with SSL bypass to Bybit, OKX, Kucoin (in case Binance data API goes down)

This replaces the single-exchange fetch in CryptoFetcher with a robust chain
that ALWAYS gets data, even from restricted regions.
"""
from __future__ import annotations

import time
import json
import sqlite3
import requests
import urllib3
from pathlib import Path
from typing import Any
from datetime import datetime, timezone

# Suppress SSL warnings when verify=False
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Use a single requests Session for connection pooling
_session = requests.Session()
_session.headers.update({
    "User-Agent": "Mozilla/5.0 (RX-0-Unicorn)",
    "Accept": "application/json",
})


def _ccxt_to_ohlcv(ccxt_data: list) -> list[tuple]:
    """Convert ccxt OHLCV format to (timestamp_ms, OHLCV) tuples."""
    return [(int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])) for c in ccxt_data]


# ── v0.9.2: CCXT paginated fetcher (multi-exchange, rate-limit aware) ──────
# Used by backtest/run_yearly.py for 1y historical candles. Supports all
# major exchanges via ccxt. Pagination is required because most exchanges
# cap a single fetch_ohlcv() at 1000-1500 bars.

# Map our timeframe strings to ccxt format. ccxt accepts both "4h" and
# "1h" natively for most exchanges, so this is a passthrough.
_CCXT_TF_MAP = {
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1h", "4h": "4h", "1d": "1d",
}

# Symbol normalization per exchange.
def _to_ccxt_symbol(symbol: str, exchange: str) -> str:
    """Convert 'BTC/USDT' to exchange's native format."""
    base, quote = symbol.split("/")
    if exchange in ("binance", "bybit", "okx"):
        # Most use 'BTC/USDT' directly with ccxt
        return f"{base}/{quote}"
    elif exchange in ("gate", "gateio"):
        return f"{base}_{quote}"
    elif exchange in ("htx", "huobi"):
        return f"{base}/{quote}"
    elif exchange == "kucoin":
        return f"{base}-{quote}"
    return f"{base}/{quote}"


def _make_ccxt_exchange(exchange: str):
    """Lazy-create a ccxt exchange instance with sensible rate-limit defaults."""
    import ccxt
    klass = getattr(ccxt, exchange, None)
    if klass is None:
        raise ValueError(f"Unknown exchange '{exchange}'. Available: binance, bybit, okx, gate, htx, kucoin")
    return klass({
        "enableRateLimit": True,
        "rateLimit": 200,  # ms between calls — more aggressive than ccxt default 1000
        "timeout": 20000,
    })


def fetch_ohlcv_ccxt(
    symbol: str,
    timeframe: str = "4h",
    total_bars: int = 2200,
    exchange: str = "binance",
    verbose: bool = False,
) -> list[tuple] | None:
    """
    Fetch historical OHLCV via CCXT with automatic pagination.

    Most exchanges cap a single fetch_ohlcv() at 1000-1500 bars. To get 1y
    of 4h data (~2200 bars), we paginate by walking backward in time using
    the `since` parameter until we have enough bars.

    Args:
        symbol: 'BTC/USDT' format
        timeframe: '1m', '5m', '15m', '1h', '4h', '1d' (ccxt-compatible)
        total_bars: target number of bars to return
        exchange: ccxt exchange id (binance, bybit, okx, gate, htx, kucoin)
        verbose: print pagination progress

    Returns:
        List of (timestamp_ms, open, high, low, close, volume) tuples,
        sorted ascending. Returns None if all pages fail.
    """
    ccxt_tf = _CCXT_TF_MAP.get(timeframe, timeframe)
    ccxt_sym = _to_ccxt_symbol(symbol, exchange)

    try:
        ex = _make_ccxt_exchange(exchange)
    except Exception as e:
        if verbose:
            print(f"  [ccxt-{exchange}] init error: {e}")
        return None

    all_candles: list[list] = []
    # Most exchanges have a per-call limit of 1000-1500. We use 1000 to be safe.
    per_call = 1000
    end_ts_ms: int | None = None  # None = "give me the most recent"
    page = 0
    max_pages = (total_bars // per_call) + 3  # safety margin

    while len(all_candles) < total_bars and page < max_pages:
        try:
            params = {}
            if end_ts_ms is not None:
                # `since` is the START of the window we want. To paginate
                # backward, ask for bars before our current earliest.
                # We subtract 1ms so we don't re-fetch the same bar.
                params["since"] = end_ts_ms - 1
            candles = ex.fetch_ohlcv(ccxt_sym, ccxt_tf, limit=per_call, params=params)
        except Exception as e:
            if verbose:
                print(f"  [ccxt-{exchange}] page {page} error: {e}")
            break
        if not candles:
            break
        if end_ts_ms is not None:
            # The exchange gave us a window ending at end_ts_ms-1.
            # New end_ts is the first candle's timestamp.
            end_ts_ms = int(candles[0][0])
        else:
            # First call: walk backward from the most recent candle.
            end_ts_ms = int(candles[0][0])
        # Prepend (we're walking backward in time).
        all_candles = candles + all_candles
        page += 1
        if verbose:
            print(f"  [ccxt-{exchange}] page {page}: got {len(candles)} bars, total {len(all_candles)}/{total_bars}")
        if len(candles) < per_call:
            # Exchange has no more history for this symbol.
            break

    if not all_candles:
        return None
    # Take only the most-recent `total_bars`.
    all_candles = all_candles[-total_bars:]
    return _ccxt_to_ohlcv(all_candles)


def fetch_ohlcv_multi_paginated(
    symbol: str,
    timeframe: str = "4h",
    total_bars: int = 2200,
    preferred: str = "binance",
    verbose: bool = False,
) -> tuple[list[tuple] | None, str]:
    """
    Try CCXT paginated fetch across multiple exchanges in priority order.

    Returns (data, source_exchange). source_exchange is the ccxt id of the
    exchange that succeeded, or '' if all failed.

    Note: some regions / server IPs can't reach Binance/Bybit/OKX directly
    (geo-block or SSL hostname mismatch). We try preferred first, then fall
    through the list — Gate.io is usually reachable from anywhere and has
    the same 4h candles.
    """
    order = [preferred] + [k for k in ("binance", "bybit", "okx", "gate", "kucoin", "htx") if k != preferred]
    last_err = ""
    for ex_id in order:
        data = fetch_ohlcv_ccxt(symbol, timeframe, total_bars, exchange=ex_id, verbose=verbose)
        if data and len(data) > 0:
            if verbose and ex_id != preferred:
                print(f"  [fallback] using {ex_id} (preferred {preferred} failed)")
            return data, ex_id
    return None, ""


def _fetch_binance_data_api(symbol: str, interval: str = "1h", limit: int = 200) -> list[tuple] | None:
    """
    PRIMARY: Fetch from data-api.binance.vision (Binance's public data API).
    Returns None if fails. Works in geo-restricted regions.
    """
    base = "https://data-api.binance.vision"
    sym = symbol.replace("/", "")
    url = f"{base}/api/v3/klines"
    try:
        r = _session.get(url, params={"symbol": sym, "interval": interval, "limit": limit}, timeout=15)
        if r.status_code != 200:
            return None
        data = r.json()
        if not isinstance(data, list) or len(data) == 0:
            return None
        # Binance kline format: [openTime, o, h, l, c, volume, closeTime, ...]
        return [(int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])) for c in data]
    except Exception:
        return None


def _fetch_gate(symbol: str, interval: str = "1h", limit: int = 200) -> list[tuple] | None:
    """SECONDARY: Fetch from Gate.io via CCXT (no SSL bypass needed)."""
    try:
        import ccxt
        ex = ccxt.gate({"enableRateLimit": True})
        sym = symbol.replace("/", "_")
        ohlcv = ex.fetch_ohlcv(sym, interval, limit=limit)
        if not ohlcv:
            return None
        return _ccxt_to_ohlcv(ohlcv)
    except Exception:
        return None


def _fetch_htx(symbol: str, interval: str = "1h", limit: int = 200) -> list[tuple] | None:
    """SECONDARY: Fetch from HTX (Huobi) via CCXT."""
    try:
        import ccxt
        ex = ccxt.htx({"enableRateLimit": True})
        ohlcv = ex.fetch_ohlcv(symbol, interval, limit=limit)
        if not ohlcv:
            return None
        return _ccxt_to_ohlcv(ohlcv)
    except Exception:
        return None


def _fetch_bybit_no_ssl(symbol: str, interval: str = "1h", limit: int = 200) -> list[tuple] | None:
    """TERTIARY: Bybit direct REST with verify=False (SSL hostname bypass)."""
    base = "https://api.bybit.com"
    # Bybit interval mapping
    tf_map = {"1m": "1", "5m": "5", "15m": "15", "30m": "30", "1h": "60", "4h": "240", "1d": "D"}
    bybit_interval = tf_map.get(interval, "60")
    url = f"{base}/v5/market/kline"
    try:
        r = _session.get(url, params={
            "category": "spot",
            "symbol": symbol.replace("/", ""),
            "interval": bybit_interval,
            "limit": limit,
        }, verify=False, timeout=15)
        if r.status_code != 200:
            return None
        data = r.json()
        if data.get("retCode") != 0:
            return None
        result = data.get("result", {}).get("list", [])
        if not result:
            return None
        # Bybit returns: [ts, open, high, low, close, volume, turnover]
        # Sort ascending (Bybit returns descending)
        result.sort(key=lambda x: int(x[0]))
        return [(int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])) for c in result]
    except Exception:
        return None


def _fetch_okx_no_ssl(symbol: str, interval: str = "1h", limit: int = 200) -> list[tuple] | None:
    """TERTIARY: OKX direct REST with verify=False."""
    base = "https://www.okx.com"
    tf_map = {"1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "1H", "4h": "4H", "1d": "1D"}
    okx_interval = tf_map.get(interval, "1H")
    url = f"{base}/api/v5/market/candles"
    try:
        r = _session.get(url, params={
            "instId": symbol.replace("/", "-"),
            "bar": okx_interval,
            "limit": limit,
        }, verify=False, timeout=15)
        if r.status_code != 200:
            return None
        data = r.json()
        if data.get("code") != "0":
            return None
        result = data.get("data", [])
        if not result:
            return None
        # OKX: [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
        return [(int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])) for c in result]
    except Exception:
        return None


# Map symbol between exchanges (some pairs differ in format)
def _normalize_binance_to_gate(symbol: str) -> str:
    """BTC/USDT -> BTC_USDT for Gate."""
    return symbol.replace("/", "_")


def _normalize_to_bybit(symbol: str) -> str:
    """BTC/USDT -> BTCUSDT for Bybit."""
    return symbol.replace("/", "")


def _normalize_to_okx(symbol: str) -> str:
    """BTC/USDT -> BTC-USDT for OKX."""
    return symbol.replace("/", "-")


def fetch_ohlcv_multi(
    symbol: str,
    timeframe: str = "1h",
    limit: int = 200,
    preferred: str = "binance",
) -> list[tuple]:
    """
    Fetch OHLCV with multi-exchange fallback chain.

    Returns list of (timestamp_ms, open, high, low, close, volume) tuples.
    Raises RuntimeError if all exchanges fail.
    """
    fetchers = {
        "binance": _fetch_binance_data_api,
        "gate": _fetch_gate,
        "htx": _fetch_htx,
        "bybit": _fetch_bybit_no_ssl,
        "okx": _fetch_okx_no_ssl,
    }

    # Order: preferred first, then the rest
    order = [preferred] + [k for k in fetchers.keys() if k != preferred]

    last_err = None
    for ex_id in order:
        fetcher = fetchers[ex_id]
        # Normalize symbol per exchange
        if ex_id == "gate":
            sym_use = _normalize_binance_to_gate(symbol)
        elif ex_id == "bybit":
            sym_use = _normalize_to_bybit(symbol)
        elif ex_id == "okx":
            sym_use = _normalize_to_okx(symbol)
        else:
            sym_use = symbol

        try:
            if ex_id == "binance":
                # binance fetcher doesn't need symbol arg transform
                data = fetcher(symbol, timeframe, limit)
            elif ex_id in ("gate", "htx"):
                data = fetcher(symbol, timeframe, limit)
            else:
                data = fetcher(sym_use, timeframe, limit)
            if data and len(data) > 0:
                return data
        except Exception as e:
            last_err = f"{ex_id}: {e}"

    raise RuntimeError(
        f"All exchanges failed for {symbol} {timeframe}. Last error: {last_err}"
    )


def fetch_ticker_multi(symbol: str, preferred: str = "binance") -> float | None:
    """
    Fetch current price with multi-exchange fallback.
    Returns last close price, or None if all fail.
    """
    # 1. Try Binance data API
    try:
        sym = symbol.replace("/", "")
        r = _session.get(
            "https://data-api.binance.vision/api/v3/ticker/price",
            params={"symbol": sym},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            if "price" in data:
                return float(data["price"])
    except Exception:
        pass

    # 2. Try Gate
    try:
        import ccxt
        ex = ccxt.gate({"enableRateLimit": True})
        t = ex.fetch_ticker(symbol.replace("/", "_"))
        if t and t.get("last"):
            return float(t["last"])
    except Exception:
        pass

    # 3. Try Bybit (no SSL)
    try:
        sym = symbol.replace("/", "")
        r = _session.get(
            "https://api.bybit.com/v5/market/tickers",
            params={"category": "spot", "symbol": sym},
            verify=False,
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            items = data.get("result", {}).get("list", [])
            if items:
                return float(items[0].get("lastPrice", 0))
    except Exception:
        pass

    # 4. Try OKX (no SSL)
    try:
        inst = symbol.replace("/", "-")
        r = _session.get(
            "https://www.okx.com/api/v5/market/ticker",
            params={"instId": inst},
            verify=False,
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            items = data.get("data", [])
            if items:
                return float(items[0].get("last", 0))
    except Exception:
        pass

    # 5. Try HTX
    try:
        import ccxt
        ex = ccxt.htx({"enableRateLimit": True})
        t = ex.fetch_ticker(symbol)
        if t and t.get("last"):
            return float(t["last"])
    except Exception:
        pass

    return None


# Convenience: bulk fetch multiple symbols with rate limiting
def fetch_bulk_ohlcv(
    symbols: list[str],
    timeframe: str = "1h",
    limit: int = 200,
    preferred: str = "binance",
    progress: bool = True,
) -> dict[str, list[tuple]]:
    """
    Fetch OHLCV for many symbols with throttling.
    Returns {symbol: ohlcv_data} for successful fetches.
    """
    results = {}
    for i, sym in enumerate(symbols):
        try:
            data = fetch_ohlcv_multi(sym, timeframe, limit, preferred=preferred)
            if data:
                results[sym] = data
        except RuntimeError:
            pass
        if progress and (i + 1) % 10 == 0:
            print(f"  [{i+1}/{len(symbols)}] fetched {len(results)} successful")
        time.sleep(0.1)  # rate limit protection
    return results


# Smoke test
if __name__ == "__main__":
    print("=" * 60)
    print("Multi-Exchange Fetcher — Smoke Test")
    print("=" * 60)

    test_symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "AVAX/USDT", "MATIC/USDT"]

    print("\n[1] OHLCV via primary (binance data API):")
    for sym in test_symbols:
        try:
            data = fetch_ohlcv_multi(sym, "1h", 5, preferred="binance")
            print(f"  ✅ {sym:12s}  {len(data)} candles  latest close=${data[-1][4]:,.4f}  src=binance")
        except Exception as e:
            print(f"  ❌ {sym}: {e}")

    print("\n[2] Ticker (real-time price):")
    for sym in test_symbols:
        price = fetch_ticker_multi(sym)
        if price:
            print(f"  ✅ {sym:12s}  ${price:,.4f}")
        else:
            print(f"  ❌ {sym}: no price")

    print("\n[3] Bulk fetch with primary binance:")
    data = fetch_bulk_ohlcv(test_symbols, "1h", 10, progress=False)
    print(f"  ✅ Bulk: {len(data)}/{len(test_symbols)} successful")
    for sym, ohlcv in data.items():
        print(f"     {sym:12s}  {len(ohlcv)} candles")
