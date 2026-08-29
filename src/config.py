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

# --- Confluence Scorer (Phase 3) ---
# Skor 0-4 berdasarkan berapa banyak dari 4 indikator yang align searah.
CONFLUENCE_MIN_VALID: int = 3  # < ini -> SKIP (lihat STRATEGY.md)
CONFLUENCE_A_PLUS: int = 4  # A+ setup -> size up
A_PLUS_SIZE_MULTIPLIER: float = 1.5
VALID_SIZE_MULTIPLIER: float = 1.0
SKIP_SIZE_MULTIPLIER: float = 0.0
MIN_RISK_REWARD: float = 2.0  # R:R minimum 1:2 (TP >= 2x jarak SL)

# --- Telegram Alert System (Phase 4) ---
# Bot token & chat id dibaca dari .env via python-dotenv (lihat alerts/telegram.py).
# Default kosong string = graceful degradation (alert dicetak ke console saja).
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

# Cooldown antar alert untuk pair yang sama (menit). Default 15.
ALERT_COOLDOWN_MINUTES: int = int(os.getenv("ALERT_COOLDOWN_MINUTES", "15"))

# Interval scan daemon (detik). Default 5 menit.
SCAN_INTERVAL_SECONDS: int = int(os.getenv("SCAN_INTERVAL_SECONDS", "300"))

# Jumlah sinyal teratas yang dikirim per siklus scan (ranking by score).
ALERT_TOP_N: int = int(os.getenv("ALERT_TOP_N", "5"))

# --- Alert emojis (Phase 4) ---
A_PLUS_EMOJI: str = "⭐"
VALID_EMOJI: str = "🟢"
SKIP_EMOJI: str = "⚪"

# --- Backtest Engine (Phase 5) ---
# Modal awal (USD) dan risk parameter default untuk backtest.
BACKTEST_DEFAULT_DAYS: int = 90
BACKTEST_INITIAL_CAPITAL: float = 10_000.0
BACKTEST_RISK_PER_TRADE: float = 0.02
# Time stop: berapa bar maksimum hold sebelum force-close.
BACKTEST_MAX_BARS_HOLD: int = 50
# Minimum sample size (jumlah hari data) untuk dianggap layak di-backtest.
BACKTEST_MIN_SAMPLE_SIZE: int = 30
# Default directory untuk hasil backtest (JSON, equity chart).
BACKTEST_OUTPUT_DIR: Path = PROJECT_ROOT / "backtest" / "results"
# Minimum confluence score untuk entry di backtest (3 = valid, 4 = A+).
BACKTEST_MIN_SCORE: int = 3
# Warm-up bars di-skip sebelum mulai evaluasi sinyal (supaya indikator
# WaveTrend/ADX punya cukup data). ~2.5 hari untuk 1h.
BACKTEST_WARMUP_BARS: int = 60

# Target 6 metrics wajib (STRATEGY.md). Dipakai report + target_check().
TARGET_WIN_RATE: float = 0.50  # > 50%
TARGET_PROFIT_FACTOR: float = 1.5  # > 1.5
TARGET_MAX_DRAWDOWN: float = 0.20  # < 20%
TARGET_SHARPE: float = 1.5  # > 1.5
TARGET_AVG_R_MULTIPLE: float = 1.5  # > 1.5R


def ensure_dirs() -> None:
    """Pastikan semua direktori yang dibutuhkan sudah ada."""
    for d in (DATA_DIR, STORAGE_DIR, FETCHERS_DIR, PAIRS_DIR, LOGS_DIR, BACKTEST_OUTPUT_DIR):
        d.mkdir(parents=True, exist_ok=True)
