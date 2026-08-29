"""
Sanity tests untuk Phase 1 data foundation.

Cakupan:
- Fetcher: import, inisialisasi exchange, validasi timeframe, normalisasi simbol
- Database: schema creation, insert idempotent, get_candles, get_latest, stats
- Watchlist: load & struktur tier
- (Optional, di-skip tanpa network) Real network fetch dari Binance

Network test di-skip otomatis kalau CCXT tidak bisa konek ke Binance
(untuk CI / offline environment). Untuk verifikasi manual, lihat README
atau jalankan:
    python main.py fetch --tier tier_1_major --timeframe 1h --limit 50
"""

from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path

import pandas as pd
import pytest

# Ensure project root on path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from data.fetchers.crypto_fetcher import CryptoFetcher  # noqa: E402
from data.storage.candle_db import CandleDB  # noqa: E402
from src.config import (  # noqa: E402
    BINANCE_HOSTNAME,
    DB_PATH,
    VALID_TIMEFRAMES,
    WATCHLIST_PATH,
)


# Honor env-var hostname for live network tests (mirror bypass untuk
# environment yang tidak bisa akses api.binance.com langsung).
TEST_HOSTNAME: str | None = os.getenv("BINANCE_HOSTNAME") or BINANCE_HOSTNAME


# --- Watchlist ---
def test_watchlist_loads_and_has_all_tiers():
    assert WATCHLIST_PATH.exists(), f"watchlist.json missing at {WATCHLIST_PATH}"
    with open(WATCHLIST_PATH, "r", encoding="utf-8") as f:
        wl = json.load(f)
    assert isinstance(wl, dict)
    expected_tiers = {"tier_1_major", "tier_2_large_cap", "tier_3_mid_cap", "tier_4_development"}
    assert expected_tiers.issubset(wl.keys()), f"Missing tiers: {expected_tiers - set(wl.keys())}"
    # Total 50+ pairs
    total = sum(len(v) for v in wl.values() if isinstance(v, list))
    assert total >= 50, f"Watchlist only has {total} pairs, expected >= 50"
    # All strings and slash-separated
    for tier, pairs in wl.items():
        for p in pairs:
            assert isinstance(p, str)
            assert "/" in p, f"Tier {tier}: {p} missing '/'"
            assert p.endswith("/USDT"), f"Tier {tier}: {p} not USDT-quoted"


# --- DB ---
@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    """Fixture: SQLite di tmp path supaya tidak ganggu production DB."""
    return tmp_path / "test_candles.db"


def _sample_df(n: int = 50, start_ts: int = 1_700_000_000_000) -> pd.DataFrame:
    """Bikin sample OHLCV DataFrame dengan timestamp terstruktur."""
    return pd.DataFrame(
        {
            "timestamp": [start_ts + i * 60_000 for i in range(n)],
            "open": [100.0 + i * 0.1 for i in range(n)],
            "high": [101.0 + i * 0.1 for i in range(n)],
            "low": [99.0 + i * 0.1 for i in range(n)],
            "close": [100.5 + i * 0.1 for i in range(n)],
            "volume": [10.0 + i for i in range(n)],
        }
    )


def test_db_schema_and_insert(tmp_db: Path):
    df = _sample_df(20)
    with CandleDB(db_path=tmp_db) as db:
        inserted = db.insert_candles(df, "BTC/USDT", "1h")
        assert inserted == 20
        # Insert duplicate -> UNIQUE constraint should make inserted=0
        inserted_dup = db.insert_candles(df, "BTC/USDT", "1h")
        assert inserted_dup == 0
        # Verify count
        out = db.get_candles("BTC/USDT", "1h")
        assert len(out) == 20
        assert list(out.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
        assert out["timestamp"].is_monotonic_increasing


def test_db_get_latest(tmp_db: Path):
    df = _sample_df(10)
    with CandleDB(db_path=tmp_db) as db:
        db.insert_candles(df, "ETH/USDT", "1h")
        last3 = db.get_latest("ETH/USDT", "1h", count=3)
        assert len(last3) == 3
        # Last timestamp should equal last of input
        assert int(last3.iloc[-1]["timestamp"]) == int(df.iloc[-1]["timestamp"])


def test_db_get_candles_filters(tmp_db: Path):
    df = _sample_df(n=100, start_ts=1_700_000_000_000)
    with CandleDB(db_path=tmp_db) as db:
        db.insert_candles(df, "SOL/USDT", "15m")
        # Filter by start_ts
        mid_ts = int(df.iloc[50]["timestamp"])
        out = db.get_candles("SOL/USDT", "15m", start_ts=mid_ts)
        assert len(out) == 50
        # Limit
        out5 = db.get_candles("SOL/USDT", "15m", limit=5)
        assert len(out5) == 5


def test_db_stats(tmp_db: Path):
    df = _sample_df(5)
    with CandleDB(db_path=tmp_db) as db:
        db.insert_candles(df, "BTC/USDT", "1h")
        db.insert_candles(df, "ETH/USDT", "4h")
        stats = db.get_stats()
    assert stats["total_rows"] == 10
    assert stats["distinct_pairs"] == 2
    assert stats["distinct_timeframes"] == 2
    assert "1h" in stats["rows_per_timeframe"]
    assert "4h" in stats["rows_per_timeframe"]


def test_db_invalid_timeframe_raises(tmp_db: Path):
    df = _sample_df(3)
    with CandleDB(db_path=tmp_db) as db:
        with pytest.raises(ValueError):
            db.insert_candles(df, "BTC/USDT", "2h")  # not in VALID_TIMEFRAMES


def test_db_context_manager_required(tmp_db: Path):
    db = CandleDB(db_path=tmp_db)
    with pytest.raises(RuntimeError):
        db.get_candles("BTC/USDT", "1h")  # not opened
    db.open()
    try:
        db.get_candles("BTC/USDT", "1h")  # ok
    finally:
        db.close()


# --- Fetcher (non-network) ---
def test_fetcher_init_and_validation():
    f = CryptoFetcher("binance")
    assert f.exchange is not None
    assert f.SUPPORTED_TIMEFRAMES == VALID_TIMEFRAMES
    f.close()


def test_fetcher_invalid_timeframe():
    f = CryptoFetcher("binance")
    try:
        with pytest.raises(ValueError):
            f.fetch_ohlcv("BTC/USDT", "2h", limit=10)
        with pytest.raises(ValueError):
            f.fetch_multiple(["BTC/USDT"], "99m", limit=10)
    finally:
        f.close()


def test_fetcher_symbol_normalization():
    f = CryptoFetcher("binance")
    try:
        assert f._normalize_symbol("BTC/USDT") == "BTC/USDT"
        assert f._normalize_symbol("btcusdt") == "BTC/USDT"
        assert f._normalize_symbol("ETHUSDT") == "ETH/USDT"
        assert f._normalize_symbol("SOL/USDC") == "SOL/USDC"
        # Symbols without recognized quote suffix left as-is
        assert f._normalize_symbol("WEIRD") == "WEIRD"
    finally:
        f.close()


# --- Fetcher (network, optional) ---
def _network_available(
    host: str = "api.binance.com", port: int = 443, timeout: float = 2.0
) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _alt_host_available(
    host: str = "data-api.binance.vision", port: int = 443, timeout: float = 2.0
) -> bool:
    """Cek apakah mirror alternatif reachable — beberapa env hanya punya
    akses ke data-api.binance.vision, bukan api.binance.com langsung."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _resolve_test_hostname() -> str | None:
    """Tentukan hostname untuk live tests. Prefer env var, fallback ke
    data-api mirror kalau api.binance.com tidak reachable."""
    if TEST_HOSTNAME:
        return TEST_HOSTNAME
    if not _network_available() and _alt_host_available():
        return "data-api.binance.vision"
    return None


@pytest.mark.skipif(
    not (_network_available() or _alt_host_available()),
    reason="Network unavailable — skip live Binance test",
)
def test_fetcher_real_binance_btc():
    host = _resolve_test_hostname()
    f = CryptoFetcher("binance", hostname=host)
    try:
        df = f.fetch_ohlcv("BTC/USDT", "1h", limit=10)
    finally:
        f.close()
    assert isinstance(df, pd.DataFrame)
    assert not df.empty
    assert len(df) <= 10
    assert list(df.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
    # Timestamps should be millisecond integers
    assert df["timestamp"].dtype.kind == "i"
    assert df["close"].dtype.kind == "f"
    # Reasonable BTC price range
    assert 1_000 < float(df["close"].iloc[-1]) < 10_000_000


@pytest.mark.skipif(
    not (_network_available() or _alt_host_available()),
    reason="Network unavailable — skip live Binance multi-symbol test",
)
def test_fetcher_real_binance_multiple():
    host = _resolve_test_hostname()
    f = CryptoFetcher("binance", hostname=host)
    try:
        results = f.fetch_multiple(["BTC/USDT", "ETH/USDT"], "1h", limit=20)
    finally:
        f.close()
    assert set(results.keys()) == {"BTC/USDT", "ETH/USDT"}
    for sym, df in results.items():
        assert not df.empty, f"{sym} returned empty"
        assert len(df) <= 20
