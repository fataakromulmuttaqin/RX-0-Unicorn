"""Konfigurasi pusat untuk RX-0 Unicorn.

Semua path, konstanta, dan settings didefinisikan di sini agar mudah
diubah tanpa menyentuh kode di modul lain.
"""

import os
from pathlib import Path

# --- Project paths ---
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
DATA_DIR: Path = PROJECT_ROOT / "data"
STORAGE_DIR: Path = DATA_DIR / "storage"
FETCHERS_DIR: Path = DATA_DIR / "fetchers"
PAIRS_DIR: Path = DATA_DIR / "pairs"
LOGS_DIR: Path = PROJECT_ROOT / "logs"
SRC_DIR: Path = PROJECT_ROOT / "src"

# --- Database ---
DB_PATH: Path = STORAGE_DIR / "candles.db"

# --- Watchlist ---
WATCHLIST_PATH: Path = PAIRS_DIR / "watchlist.json"

# --- Exchange ---
EXCHANGE_ID: str = "binance"
EXCHANGE_NAME: str = "Binance"
# Hostname alternatif — beberapa region/jaringan mem-blokir api.binance.com.
# CCXT 'binance' menerima override 'hostname' / 'urls.api'. Default None
# (pakai host CCXT). Set via env BINANCE_HOSTNAME atau langsung di CryptoFetcher.
# Contoh nilai: "data-api.binance.vision"
BINANCE_HOSTNAME: str | None = os.getenv("BINANCE_HOSTNAME") or None

# --- Timeframes ---
# Tuple of (ccxt_id, display_name, seconds)
TIMEFRAMES: dict[str, dict] = {
    "5m": {"seconds": 5 * 60, "label": "5 minutes"},
    "15m": {"seconds": 15 * 60, "label": "15 minutes"},
    "1h": {"seconds": 60 * 60, "label": "1 hour"},
    "4h": {"seconds": 4 * 60 * 60, "label": "4 hours"},
    "1d": {"seconds": 24 * 60 * 60, "label": "1 day"},
}
VALID_TIMEFRAMES: tuple[str, ...] = tuple(TIMEFRAMES.keys())

# --- Fetcher settings ---
FETCHER_BATCH_SIZE: int = 1000  # Binance max per request
FETCHER_MAX_RETRIES: int = 5
FETCHER_BASE_BACKOFF: float = 1.0  # seconds
FETCHER_BACKOFF_FACTOR: float = 2.0
FETCHER_TIMEOUT: int = 30000  # ms (CCXT expects milliseconds)

# --- Storage retention ---
INTRADAY_RETENTION_DAYS: int = 90
DAILY_RETENTION_DAYS: int = 999999  # keep daily forever (effectively)

# --- Watchlist tiers ---
WATCHLIST_TIERS: tuple[str, ...] = (
    "tier_1_major",
    "tier_2_large_cap",
    "tier_3_mid_cap",
    "tier_4_development",
)

# --- Default CLI values ---
DEFAULT_TIMEFRAME: str = "1h"
DEFAULT_LIMIT: int = 500


def ensure_dirs() -> None:
    """Pastikan semua direktori yang dibutuhkan sudah ada."""
    for d in (DATA_DIR, STORAGE_DIR, FETCHERS_DIR, PAIRS_DIR, LOGS_DIR):
        d.mkdir(parents=True, exist_ok=True)
