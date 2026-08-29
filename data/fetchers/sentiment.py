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

PROJECT_ROOT = Path(__file__).resolve().parents[1]
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


# -----------------------------------------------------------------------------
# Source 2: CoinGecko market data (free, no auth — always available)
# -----------------------------------------------------------------------------
def fetch_coingecko_market(symbol: str) -> dict[str, Any] | None:
    """
    Fetch market metrics from CoinGecko /coins/{id} endpoint.
    Free tier: market data only (community data moved to paid).
    Used for: price change %, market cap, volume trend (proxy for sentiment).
    """
    cached = _cache_get(symbol, "coingecko_market")
    if cached:
        return cached

    coin_id = _symbol_to_coingecko_id(symbol)
    if not coin_id:
        return None

    try:
        r = httpx.get(
            f"https://api.coingecko.com/api/v3/coins/{coin_id}",
            params={"localization": "false", "tickers": "false", "community_data": "false", "developer_data": "false"},
            timeout=10,
        )
        if r.status_code != 200:
            logger.debug(f"CoinGecko {coin_id}: HTTP {r.status_code}")
            return None
        data = r.json()
        market = data.get("market_data", {})
        price_change_24h = float(market.get("price_change_percentage_24h") or 0)
        price_change_7d = float(market.get("price_change_percentage_7d") or 0)
        price_change_30d = float(market.get("price_change_percentage_30d") or 0)
        market_cap = float((market.get("market_cap") or {}).get("usd") or 0)
        total_volume = float((market.get("total_volume") or {}).get("usd") or 0)
        # Volume / Market Cap = turnover ratio (proxy for activity)
        turnover_pct = (total_volume / market_cap * 100) if market_cap > 0 else 0

        # Implied sentiment from price action: 24h momentum
        # 50 = neutral, >50 = bullish, <50 = bearish
        # Use 7d change with smoothing
        sentiment_implied = 50 + (price_change_7d * 2)  # 7d change of +5% → 60 sentiment
        sentiment_implied = max(0, min(100, sentiment_implied))

        result = {
            "source": "coingecko_market",
            "symbol": symbol,
            "fetched_at": int(time.time()),
            "price_change_24h_pct": price_change_24h,
            "price_change_7d_pct": price_change_7d,
            "price_change_30d_pct": price_change_30d,
            "market_cap_usd": market_cap,
            "volume_24h_usd": total_volume,
            "turnover_pct": turnover_pct,
            "sentiment_implied": sentiment_implied,
        }
        _cache_set(symbol, "coingecko_market", result)
        return result
    except Exception as e:
        logger.debug(f"CoinGecko {coin_id}: {e}")
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
