"""
Sentiment data fetcher — 3 free sources aggregated.

Sources:
1. LunarCrush API (free tier: 100 calls/day, sentiment + galaxy score per coin)
2. CoinGecko community data (free, no auth, sub count + engagement)
3. Alternative.me Fear & Greed Index (free, daily market-wide)

All sources return a unified SentimentResult so the daemon can use one interface.
"""
from __future__ import annotations

import sys
import time
import json
import sqlite3
from pathlib import Path
from typing import Any
from datetime import datetime, timezone, timedelta

import httpx
from loguru import logger

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

CACHE_DB = PROJECT_ROOT / "data" / "storage" / "sentiment_cache.db"
CACHE_TTL_HOURS = 6  # sentiment doesn't change fast, cache 6h


# -----------------------------------------------------------------------------
# Cache layer
# -----------------------------------------------------------------------------
def _init_cache_db() -> None:
    """Create cache DB if not exists."""
    CACHE_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(CACHE_DB)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sentiment_cache (
                symbol TEXT NOT NULL,
                source TEXT NOT NULL,
                fetched_at INTEGER NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (symbol, source)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_fetched ON sentiment_cache(fetched_at)")
        conn.commit()
    finally:
        conn.close()


def _cache_get(symbol: str, source: str, ttl_hours: int = CACHE_TTL_HOURS) -> dict | None:
    """Get cached value if not expired."""
    _init_cache_db()
    conn = sqlite3.connect(CACHE_DB)
    try:
        cur = conn.execute(
            "SELECT payload, fetched_at FROM sentiment_cache WHERE symbol=? AND source=?",
            (symbol, source),
        )
        row = cur.fetchone()
        if not row:
            return None
        payload, fetched_at = row
        age_hours = (time.time() - fetched_at) / 3600
        if age_hours > ttl_hours:
            return None
        return json.loads(payload)
    finally:
        conn.close()


def _cache_set(symbol: str, source: str, data: dict) -> None:
    """Store value in cache."""
    _init_cache_db()
    conn = sqlite3.connect(CACHE_DB)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO sentiment_cache (symbol, source, fetched_at, payload) VALUES (?, ?, ?, ?)",
            (symbol, source, int(time.time()), json.dumps(data)),
        )
        conn.commit()
    finally:
        conn.close()


# -----------------------------------------------------------------------------
# Source 1: LunarCrush (free tier)
# -----------------------------------------------------------------------------
def fetch_lunarcrush(symbol: str) -> dict[str, Any] | None:
    """
    Fetch sentiment from LunarCrush public API.
    Free: 100 calls/day per IP. No auth required.
    """
    cached = _cache_get(symbol, "lunarcrush")
    if cached:
        return cached

    if not _check_rate_limit():
        return None

    # Convert BTC/USDT -> BTC for LunarCrush
    coin = symbol.split("/")[0]
    try:
        r = httpx.get(
            "https://lunarcrush.com/api/v1",
            params={"symbol": coin, "data": "metrics"},
            timeout=10,
        )
        if r.status_code != 200:
            logger.debug(f"LunarCrush {coin}: HTTP {r.status_code}")
            return None
        data = r.json()
        if not isinstance(data, dict) or "data" not in data:
            return None
        metrics = data["data"]
        result = {
            "source": "lunarcrush",
            "symbol": coin,
            "fetched_at": int(time.time()),
            "galaxy_score": float(metrics.get("galaxy_score", 0) or 0),  # 0-100
            "alt_rank": int(metrics.get("alt_rank", 0) or 0),
            "social_score": float(metrics.get("social_score", 0) or 0),
            "social_volume": float(metrics.get("social_volume", 0) or 0),
            "sentiment": float(metrics.get("sentiment", 50) or 50),  # 0-100, >50 bullish
            "tweet_volume": float(metrics.get("tweet_volume", 0) or 0),
        }
        _cache_set(symbol, "lunarcrush", result)
        return result
    except Exception as e:
        logger.debug(f"LunarCrush {coin}: {e}")
        return None


# Cache for batch fetch
_batch_cache: dict[str, Any] = {"data": None, "fetched_at": 0}
_BATCH_TTL_SECONDS = 3600  # 1 hour

# Rate limiter for ALL external API calls (token bucket, conservative)
_api_call_log: list[float] = []
_API_RATE_LIMIT = 10  # max calls per minute (CoinGecko free tier)
_RATE_WINDOW = 60.0  # seconds


def _check_rate_limit() -> bool:
    """
    Simple sliding window rate limiter.
    Returns True if OK to call, False if should wait.
    """
    global _api_call_log
    now = time.time()
    # Drop old entries
    _api_call_log = [t for t in _api_call_log if now - t < _RATE_WINDOW]
    if len(_api_call_log) >= _API_RATE_LIMIT:
        wait_time = _RATE_WINDOW - (now - _api_call_log[0])
        logger.warning(f"⏸ Rate limit hit ({len(_api_call_log)} calls/{_RATE_WINDOW}s). Wait {wait_time:.0f}s")
        return False
    _api_call_log.append(now)
    return True


# -----------------------------------------------------------------------------
# Source 2: CoinGecko batch market data (1 call for all coins, free)
# -----------------------------------------------------------------------------
def fetch_coingecko_batch(symbols: list[str] | None = None) -> dict[str, dict[str, Any]] | None:
    """
    Batch fetch market data for multiple coins in ONE call.
    Uses CoinGecko /coins/markets endpoint (free, 10-30 calls/min, ~250 coins per call).

    Returns: dict of symbol -> market data, or None on failure.
    """
    global _batch_cache
    # Check cache first
    if _batch_cache["data"] and (time.time() - _batch_cache["fetched_at"]) < _BATCH_TTL_SECONDS:
        return _batch_cache["data"]

    if symbols is None:
        # Default: load from watchlist
        try:
            from data.fetchers.sentiment import _symbol_to_coingecko_id
        except ImportError:
            _symbol_to_coingecko_id = None
        if _symbol_to_coingecko_id:
            # Build IDs from our watchlist
            import json
            with open(PROJECT_ROOT / "data" / "pairs" / "watchlist.json") as f:
                wl = json.load(f)
            all_pairs = [p for tier in wl.values() for p in tier]
            ids = []
            for p in all_pairs:
                cg_id = _symbol_to_coingecko_id(p)
                if cg_id:
                    ids.append(cg_id)
            ids = list(set(ids))  # dedupe
        else:
            ids = ["bitcoin", "ethereum", "solana"]
    else:
        # Convert symbols to CoinGecko IDs
        from data.fetchers.sentiment import _symbol_to_coingecko_id
        ids = []
        for s in symbols:
            cg_id = _symbol_to_coingecko_id(s)
            if cg_id:
                ids.append(cg_id)
        ids = list(set(ids))

    if not ids:
        return None

    # Rate limit check
    if not _check_rate_limit():
        logger.warning("CoinGecko batch skipped: rate limit")
        return _batch_cache["data"]  # return stale

    # Single API call for all coins (max 250 per call, we have 50ish)
    try:
        r = httpx.get(
            "https://api.coingecko.com/api/v3/coins/markets",
            params={
                "vs_currency": "usd",
                "ids": ",".join(ids[:250]),  # API limit
                "price_change_percentage": "1h,24h,7d,30d",
                "per_page": 250,
            },
            timeout=15,
        )
        if r.status_code != 200:
            logger.warning(f"CoinGecko batch: HTTP {r.status_code}")
            return _batch_cache["data"]  # return stale if available
        coins = r.json()
        if not isinstance(coins, list):
            return _batch_cache["data"]

        result = {}
        for c in coins:
            symbol = c.get("symbol", "").upper()
            if not symbol:
                continue
            price_change_7d = float(c.get("price_change_percentage_7d_in_currency") or 0)
            sentiment_implied = max(0, min(100, 50 + price_change_7d * 2))
            result[f"{symbol}/USDT"] = {
                "source": "coingecko_market",
                "symbol": f"{symbol}/USDT",
                "fetched_at": int(time.time()),
                "price_usd": float(c.get("current_price", 0)),
                "price_change_1h_pct": float(c.get("price_change_percentage_1h_in_currency") or 0),
                "price_change_24h_pct": float(c.get("price_change_percentage_24h_in_currency") or 0),
                "price_change_7d_pct": price_change_7d,
                "price_change_30d_pct": float(c.get("price_change_percentage_30d_in_currency") or 0),
                "market_cap_usd": float(c.get("market_cap", 0) or 0),
                "volume_24h_usd": float(c.get("total_volume", 0) or 0),
                "turnover_pct": (float(c.get("total_volume", 0) or 0) / float(c.get("market_cap", 1) or 1)) * 100,
                "sentiment_implied": sentiment_implied,
            }
        _batch_cache["data"] = result
        _batch_cache["fetched_at"] = time.time()
        logger.info(f"📊 CoinGecko batch: {len(result)} coins in 1 call (cached 1h)")
        return result
    except Exception as e:
        logger.debug(f"CoinGecko batch error: {e}")
        return _batch_cache["data"]  # return stale if available


# -----------------------------------------------------------------------------
# Per-symbol getter (uses batch cache)
# -----------------------------------------------------------------------------
def fetch_coingecko_market(symbol: str) -> dict[str, Any] | None:
    """
    Get market data for a single symbol from the batch cache.
    Trigger batch fetch if cache empty/expired.
    """
    # Check per-symbol cache first
    cached = _cache_get(symbol, "coingecko_market", ttl_hours=24)
    if cached:
        return cached

    # Ensure batch is loaded
    batch = fetch_coingecko_batch()
    if not batch:
        return None

    # Find symbol in batch
    data = batch.get(symbol.upper())
    if data:
        # Cache per-symbol
        _cache_set(symbol, "coingecko_market", data)
        return data
    return None


# Symbol → CoinGecko ID mapping (most common ones)
_COINGECKO_IDS = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "BNB": "binancecoin",
    "XRP": "ripple", "ADA": "cardano", "AVAX": "avalanche-2", "DOGE": "dogecoin",
    "TRX": "tron", "DOT": "polkadot", "LINK": "chainlink", "LTC": "litecoin",
    "BCH": "bitcoin-cash", "NEAR": "near", "ATOM": "cosmos", "UNI": "uniswap",
    "APT": "aptos", "ARB": "arbitrum", "OP": "optimism", "INJ": "injective-protocol",
    "FIL": "filecoin", "IMX": "immutable-x", "LDO": "lido-dao", "AAVE": "aave",
    "ALGO": "algorand", "SUI": "sui", "TIA": "celestia", "WLD": "worldcoin-wld",
    "PEPE": "pepe", "WIF": "dogwifcoin", "BONK": "bonk", "FET": "fetch-ai",
    "RUNE": "thorchain", "GRT": "the-graph", "SAND": "the-sandbox", "MANA": "decentraland",
    "AXS": "axie-infinity", "CHZ": "chiliz", "CRV": "curve-dao-token", "SNX": "havven",
    "COMP": "compound-governance-token", "1INCH": "1inch", "ENS": "ethereum-name-service",
    "BLUR": "blur", "MASK": "mask-network", "DYDX": "dydx", "GMX": "gmx",
    "PENDLE": "pendle", "EGLD": "elrond-erd-2", "FTM": "fantom", "MKR": "maker",
    "MATIC": "matic-network", "TON": "the-open-network", "SHIB": "shiba-inu",
    "PEPE": "pepe",
}


def _symbol_to_coingecko_id(symbol: str) -> str | None:
    coin = symbol.split("/")[0]
    return _COINGECKO_IDS.get(coin)


# -----------------------------------------------------------------------------
# Source 3: Alternative.me Fear & Greed Index (free, no auth)
# -----------------------------------------------------------------------------
def fetch_fear_greed() -> dict[str, Any] | None:
    """
    Fetch crypto market-wide Fear & Greed Index.
    0 = extreme fear, 100 = extreme greed.
    """
    cached = _cache_get("MARKET", "fear_greed")
    if cached:
        return cached

    if not _check_rate_limit():
        return None

    try:
        r = httpx.get(
            "https://api.alternative.me/fng/",
            params={"limit": 1, "format": "json"},
            timeout=10,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        items = data.get("data", [])
        if not items:
            return None
        latest = items[0]
        result = {
            "source": "fear_greed",
            "value": int(latest.get("value", 50)),
            "classification": latest.get("value_classification", "Neutral"),
            "timestamp": int(latest.get("timestamp", 0)),
            "fetched_at": int(time.time()),
        }
        _cache_set("MARKET", "fear_greed", result)
        return result
    except Exception as e:
        logger.debug(f"Fear & Greed: {e}")
        return None


# -----------------------------------------------------------------------------
# Unified sentiment API
# -----------------------------------------------------------------------------
def get_sentiment_for_symbol(symbol: str) -> dict[str, Any]:
    """
    Get aggregated sentiment for a symbol.
    Returns dict with all available sources + composite score.
    """
    result = {
        "symbol": symbol,
        "fetched_at": int(time.time()),
        "sources": {},
        "composite_sentiment": 50.0,  # neutral default
        "market_mood": None,
    }

    # 1. LunarCrush
    lc = fetch_lunarcrush(symbol)
    if lc:
        result["sources"]["lunarcrush"] = lc
        # LunarCrush sentiment (0-100, >50 bullish)
        result["composite_sentiment"] = lc["sentiment"]

    # 2. CoinGecko market data (implied sentiment from price action)
    cg = fetch_coingecko_market(symbol)
    if cg:
        result["sources"]["coingecko_market"] = cg
        # If LunarCrush gave nothing, use CoinGecko implied
        if "lunarcrush" not in result["sources"]:
            result["composite_sentiment"] = cg["sentiment_implied"]

    # 3. Fear & Greed (market-wide, not per-coin)
    fg = fetch_fear_greed()
    if fg:
        result["market_mood"] = fg

    return result


def get_market_sentiment_summary() -> dict[str, Any]:
    """
    Get market-wide sentiment summary (Fear & Greed + maybe top coins).
    """
    fg = fetch_fear_greed()
    return {
        "fear_greed": fg,
        "fetched_at": int(time.time()),
    }


# -----------------------------------------------------------------------------
# Telegram formatters
# -----------------------------------------------------------------------------
def format_market_sentiment(data: dict) -> str:
    """Format market-wide sentiment for Telegram."""
    fg = data.get("fear_greed")
    if not fg:
        return "📊 Market sentiment unavailable."
    emoji = "🟢" if fg["value"] >= 60 else "🔴" if fg["value"] <= 40 else "🟡"
    lines = [
        "📊 **Market Sentiment**",
        "━━━━━━━━━━━━━━━━━━",
        f"{emoji} **Fear & Greed: {fg['value']}/100**",
        f"   {fg['classification']}",
        "",
        f"_Updated: {datetime.fromtimestamp(fg['fetched_at'], tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_",
    ]
    return "\n".join(lines)


def format_symbol_sentiment(data: dict) -> str:
    """Format per-symbol sentiment for Telegram."""
    symbol = data.get("symbol", "?")
    composite = data.get("composite_sentiment", 50)
    emoji = "🟢" if composite >= 60 else "🔴" if composite <= 40 else "🟡"

    lines = [
        f"📊 **Sentiment: {symbol}**",
        "━━━━━━━━━━━━━━━━━━",
        f"{emoji} Composite: **{composite:.1f}/100**",
    ]

    for src, info in data.get("sources", {}).items():
        if src == "lunarcrush":
            lines.append("")
            lines.append("📈 **LunarCrush**:")
            lines.append(f"  Galaxy: {info.get('galaxy_score', 0):.0f}/100")
            lines.append(f"  Sentiment: {info.get('sentiment', 50):.1f}/100")
            lines.append(f"  Social volume: {info.get('social_volume', 0):,.0f}")
        elif src == "coingecko_market":
            ch24 = info.get("price_change_24h_pct", 0)
            ch7 = info.get("price_change_7d_pct", 0)
            ch30 = info.get("price_change_30d_pct", 0)
            cap = info.get("market_cap_usd", 0)
            vol = info.get("volume_24h_usd", 0)
            lines.append("")
            lines.append("📈 **Price action (7d implied)**:")
            ch7_emoji = "🟢" if ch7 > 0 else "🔴"
            lines.append(f"  {ch7_emoji} 24h: {ch24:+.2f}%  7d: {ch7:+.2f}%  30d: {ch30:+.2f}%")
            if cap > 0:
                lines.append(f"  Cap: ${cap/1e9:.2f}B  Vol: ${vol/1e9:.2f}B")

    if data.get("market_mood"):
        fg = data["market_mood"]
        fg_emoji = "🟢" if fg["value"] >= 60 else "🔴" if fg["value"] <= 40 else "🟡"
        lines.append("")
        lines.append(f"{fg_emoji} Market mood: {fg['value']}/100 ({fg['classification']})")

    return "\n".join(lines)


# -----------------------------------------------------------------------------
# Test
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 60)
    print("Sentiment Fetcher — Smoke Test")
    print("=" * 60)

    for sym in ["BTC/USDT", "ETH/USDT", "SOL/USDT"]:
        print(f"\n--- {sym} ---")
        data = get_sentiment_for_symbol(sym)
        print(f"  Composite sentiment: {data['composite_sentiment']:.1f}/100")
        for src, info in data["sources"].items():
            print(f"  [{src}]")
            for k, v in info.items():
                if k not in ("fetched_at", "symbol", "source"):
                    if isinstance(v, float) and abs(v) > 1000:
                        print(f"    {k}: {v:,.0f}")
                    else:
                        print(f"    {k}: {v}")
        if data["market_mood"]:
            fg = data["market_mood"]
            print(f"  [market] Fear/Greed: {fg['value']}/100 ({fg['classification']})")

    print("\n--- Market mood ---")
    mkt = get_market_sentiment_summary()
    fg = mkt["fear_greed"]
    if fg:
        print(f"  Fear & Greed: {fg['value']}/100 ({fg['classification']})")
