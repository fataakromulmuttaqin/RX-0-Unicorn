"""
News fetcher — 3 free RSS sources aggregated.

Sources:
1. CoinDesk RSS (https://www.coindesk.com/arc/outboundfeeds/rss/)
2. Cointelegraph RSS (https://cointelegraph.com/rss)
3. The Block RSS (https://www.theblock.co/rss.xml)

Each article gets:
- title
- source
- published_at
- link
- summary
- currencies (extracted from title/description)
- impact_level: low / medium / high (based on keywords + category)

Cache: 30 min, SQLite.
"""
from __future__ import annotations

import sys
import time
import re
import json
import sqlite3
from pathlib import Path
from typing import Any
from datetime import datetime, timezone

import feedparser
import httpx
from loguru import logger

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

CACHE_DB = PROJECT_ROOT / "data" / "storage" / "news_cache.db"
CACHE_TTL_MINUTES = 30  # news changes fast, refresh every 30 min

# RSS sources
RSS_SOURCES = [
    ("coindesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("cointelegraph", "https://cointelegraph.com/rss"),
    ("theblock", "https://www.theblock.co/rss.xml"),
]

# Impact keywords (high = market-moving, low = general news)
_HIGH_IMPACT_KEYWORDS = [
    "sec", "etf", "fomc", "fed", "cpi", "rate cut", "rate hike", "halving",
    "hack", "exploit", "liquidation", "ban", "regulation", "approval",
    "binance", "coinbase", "blackrock", "grayscale",
    "crash", "rally", "all-time high", "ath", "plunge", "surge",
]
_MEDIUM_IMPACT_KEYWORDS = [
    "launch", "partnership", "integration", "upgrade", "mainnet",
    "token", "listing", "delist", "burn", "mint", "unlock", "vesting",
    "earnings", "revenue", "acquisition", "merge", "fork",
    "testnet", "staking", "reward", "yield",
]

# Common crypto symbols to extract from titles
_CRYPTO_SYMBOLS = [
    "BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "AVAX", "DOGE", "TRX", "DOT",
    "LINK", "LTC", "BCH", "NEAR", "ATOM", "UNI", "APT", "ARB", "OP", "INJ",
    "FIL", "IMX", "LDO", "AAVE", "ALGO", "SUI", "TIA", "WLD", "PEPE", "WIF",
    "BONK", "FET", "RUNE", "GRT", "SAND", "MANA", "AXS", "CHZ", "CRV", "SNX",
    "COMP", "1INCH", "ENS", "BLUR", "MASK", "DYDX", "GMX", "PENDLE", "TON",
    "MATIC", "SHIB", "MKR", "FTM", "RNDR", "EGLD",
]


# -----------------------------------------------------------------------------
# Cache layer
# -----------------------------------------------------------------------------
def _init_cache_db() -> None:
    CACHE_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(CACHE_DB)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS news_cache (
                article_id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                title TEXT NOT NULL,
                link TEXT NOT NULL,
                summary TEXT,
                published_at INTEGER NOT NULL,
                fetched_at INTEGER NOT NULL,
                impact_level TEXT NOT NULL,
                currencies TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_published ON news_cache(published_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_impact ON news_cache(impact_level)")
        conn.commit()
    finally:
        conn.close()


def _article_id(source: str, link: str) -> str:
    return f"{source}:{hash(link)}"


def _cache_save(article: dict) -> None:
    _init_cache_db()
    conn = sqlite3.connect(CACHE_DB)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO news_cache (article_id, source, title, link, summary, published_at, fetched_at, impact_level, currencies) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                article["article_id"],
                article["source"],
                article["title"],
                article["link"],
                article.get("summary", ""),
                article["published_at"],
                int(time.time()),
                article["impact_level"],
                json.dumps(article["currencies"]),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _cache_get_recent(hours_back: int = 24) -> list[dict]:
    """Get all articles from last N hours."""
    _init_cache_db()
    cutoff = int(time.time()) - hours_back * 3600
    conn = sqlite3.connect(CACHE_DB)
    try:
        cur = conn.execute(
            "SELECT article_id, source, title, link, summary, published_at, impact_level, currencies "
            "FROM news_cache WHERE published_at >= ? ORDER BY published_at DESC",
            (cutoff,),
        )
        results = []
        for row in cur.fetchall():
            results.append({
                "article_id": row[0],
                "source": row[1],
                "title": row[2],
                "link": row[3],
                "summary": row[4] or "",
                "published_at": row[5],
                "impact_level": row[6],
                "currencies": json.loads(row[7] or "[]"),
            })
        return results
    finally:
        conn.close()


# -----------------------------------------------------------------------------
# Impact detection
# -----------------------------------------------------------------------------
def detect_impact(title: str, summary: str = "") -> str:
    """Detect impact level based on keywords."""
    text = (title + " " + summary).lower()
    for kw in _HIGH_IMPACT_KEYWORDS:
        if kw in text:
            return "high"
    for kw in _MEDIUM_IMPACT_KEYWORDS:
        if kw in text:
            return "medium"
    return "low"


def extract_currencies(title: str, summary: str = "") -> list[str]:
    """Extract crypto symbols mentioned in title/summary."""
    text = (title + " " + summary).upper()
    found = []
    for sym in _CRYPTO_SYMBOLS:
        # Match as whole word (BTC, not BTC2)
        pattern = r"\b" + re.escape(sym) + r"\b"
        if re.search(pattern, text):
            found.append(sym)
    return found[:5]  # cap at 5


# -----------------------------------------------------------------------------
# Fetch from RSS
# -----------------------------------------------------------------------------
def fetch_rss_source(source_name: str, url: str) -> list[dict]:
    """Fetch a single RSS feed and return list of articles."""
    try:
        # feedparser can fetch directly
        d = feedparser.parse(url)
        if d.bozo and not d.entries:
            logger.debug(f"RSS {source_name} parse error: {d.bozo_exception}")
            return []
        articles = []
        for entry in d.entries[:30]:  # cap at 30 per source
            title = entry.get("title", "").strip()
            link = entry.get("link", "").strip()
            if not title or not link:
                continue
            summary = entry.get("summary", "")[:500]
            # Parse published time
            published_struct = entry.get("published_parsed")
            if published_struct:
                published_at = int(time.mktime(published_struct))
            else:
                published_at = int(time.time())
            article_id = _article_id(source_name, link)
            impact = detect_impact(title, summary)
            currencies = extract_currencies(title, summary)
            articles.append({
                "article_id": article_id,
                "source": source_name,
                "title": title,
                "link": link,
                "summary": summary,
                "published_at": published_at,
                "impact_level": impact,
                "currencies": currencies,
            })
        return articles
    except Exception as e:
        logger.debug(f"RSS {source_name}: {e}")
        return []


def fetch_all_news(force_refresh: bool = False) -> list[dict]:
    """Fetch from all RSS sources, cache results."""
    # Check if cache is fresh (unless force_refresh)
    if not force_refresh:
        cached = _cache_get_recent(hours_back=24)
        if len(cached) > 5:
            # Cache has data, return it
            return cached

    all_articles = []
    for source_name, url in RSS_SOURCES:
        articles = fetch_rss_source(source_name, url)
        for a in articles:
            _cache_save(a)
            all_articles.append(a)
        time.sleep(0.3)  # be nice to RSS servers

    return _cache_get_recent(hours_back=24)


# -----------------------------------------------------------------------------
# Filtering & reporting
# -----------------------------------------------------------------------------
def get_today_news(currencies: list[str] | None = None) -> list[dict]:
    """Get news from last 24h, optionally filtered by currencies."""
    news = _cache_get_recent(hours_back=24)
    if not currencies:
        return news
    currencies_upper = [c.upper() for c in currencies]
    return [n for n in news if any(c in n["currencies"] for c in currencies_upper)]


def get_high_impact_news(hours_back: int = 24) -> list[dict]:
    """Get only high-impact news."""
    return [n for n in _cache_get_recent(hours_back) if n["impact_level"] == "high"]


def format_news_for_telegram(articles: list[dict], max_items: int = 15) -> str:
    """Format news list for Telegram display."""
    if not articles:
        return "📰 No news in last 24h."

    lines = ["📰 **RX-0 News Digest (last 24h)**", "━━━━━━━━━━━━━━━━━━"]

    # Group by impact
    high = [a for a in articles if a["impact_level"] == "high"]
    medium = [a for a in articles if a["impact_level"] == "medium"]
    low = [a for a in articles if a["impact_level"] == "low"]

    counts = {}
    if high:
        counts["🔴 HIGH"] = len(high)
    if medium:
        counts["🟡 MEDIUM"] = len(medium)
    if low:
        counts["⚪ LOW"] = len(low)
    if counts:
        lines.append("📊 " + " | ".join(f"{k}:{v}" for k, v in counts.items()))
    lines.append("")

    shown = 0
    for article in articles[:max_items]:
        if shown >= max_items:
            break
        emoji = {"high": "🔴", "medium": "🟡", "low": "⚪"}.get(article["impact_level"], "⚪")
        title = article["title"][:90]
        currencies = " ".join(f"`{c}`" for c in article["currencies"][:3])
        time_str = datetime.fromtimestamp(article["published_at"], tz=timezone.utc).strftime("%H:%M UTC")
        lines.append(f"{emoji} **{title}**")
        meta_parts = [article["source"]]
        if currencies:
            meta_parts.append(currencies)
        meta_parts.append(time_str)
        lines.append(f"   {' • '.join(meta_parts)}")
        lines.append("")

    if len(articles) > max_items:
        lines.append(f"_... and {len(articles) - max_items} more_")

    return "\n".join(lines)


def format_sentiment_for_telegram(data: dict) -> str:
    """Format sentiment for Telegram display."""
    lines = ["📊 **RX-0 Sentiment**", "━━━━━━━━━━━━━━━━━━"]

    if data.get("market_mood"):
        fg = data["market_mood"]
        emoji = "🟢" if fg["value"] >= 60 else "🔴" if fg["value"] <= 40 else "🟡"
        lines.append(f"{emoji} Market: **{fg['value']}/100** ({fg['classification']})")

    composite = data.get("composite_sentiment", 50)
    emoji = "🟢" if composite >= 60 else "🔴" if composite <= 40 else "🟡"
    lines.append(f"{emoji} {data['symbol']}: **{composite:.1f}/100**")

    for src, info in data.get("sources", {}).items():
        if src == "lunarcrush":
            lines.append(f"\n📈 **LunarCrush**:")
            lines.append(f"  Galaxy: {info.get('galaxy_score', 0):.0f}/100")
            lines.append(f"  Sentiment: {info.get('sentiment', 50):.1f}/100")
        elif src == "coingecko_market":
            ch24 = info.get("price_change_24h_pct", 0)
            ch7 = info.get("price_change_7d_pct", 0)
            cap = info.get("market_cap_usd", 0)
            vol = info.get("volume_24h_usd", 0)
            lines.append(f"\n📈 **CoinGecko Market**:")
            lines.append(f"  24h: {ch24:+.2f}%  7d: {ch7:+.2f}%")
            if cap > 0:
                lines.append(f"  Cap: ${cap/1e9:.2f}B  Vol: ${vol/1e9:.2f}B")

    return "\n".join(lines)


# -----------------------------------------------------------------------------
# Test
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 60)
    print("News Fetcher — Smoke Test")
    print("=" * 60)

    print("\nFetching from all RSS sources (force refresh)...")
    articles = fetch_all_news(force_refresh=True)
    print(f"Got {len(articles)} articles (last 24h)")

    high = get_high_impact_news()
    print(f"  🔴 HIGH impact: {len(high)}")
    for a in high[:5]:
        ts = datetime.fromtimestamp(a["published_at"], tz=timezone.utc).strftime("%H:%M UTC")
        print(f"    [{ts}] {a['source']:15s} {a['title'][:80]}")

    print(f"\nFormatted digest (top 10):")
    print(format_news_for_telegram(articles, max_items=10))
