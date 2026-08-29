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
