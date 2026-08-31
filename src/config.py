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
# Paper-trades DB (Phase 6) — disimpan terpisah dari candle DB supaya data
# paper trading tidak tercampur dengan candle historis. Schema didefinisikan
# di paper/journal.py (paper_trades + paper_daily).
PAPER_DB_PATH: Path = STORAGE_DIR / "paper_trades.db"

# --- Watchlist ---
WATCHLIST_PATH: Path = PAIRS_DIR / "watchlist.json"

# --- Exchange ---
# Default exchange identifier. Since the v1.0 pivot to XAU/USD, the primary
# data source is Yahoo Finance (no exchange concept), but we keep this for
# CCXT-based legacy fetchers (CryptoFetcher, MultiExchangeFetcher) and for
# optional cross-asset scans. Set via env EXCHANGE_ID for power-users.
EXCHANGE_ID: str = os.getenv("EXCHANGE_ID", "binance")
EXCHANGE_NAME: str = "Binance"
# Hostname alternatif — beberapa region/jaringan mem-blokir api.binance.com.
# CCXT 'binance' menerima override 'hostname' / 'urls.api'. Default None
# (pakai host CCXT). Set via env BINANCE_HOSTNAME atau langsung di CryptoFetcher.
# Contoh nilai: "data-api.binance.vision"
BINANCE_HOSTNAME: str | None = os.getenv("BINANCE_HOSTNAME") or None

# --- Data source selector (v1.0+ gold pivot) ---
# Default source for fetch operations. "yahoo" -> YahooFinanceFetcher (primary
# for XAU/USD); "binance"/"ccxt" -> legacy CryptoFetcher/MultiExchangeFetcher.
# Set via env DATA_SOURCE for power-users.
DEFAULT_DATA_SOURCE: str = os.getenv("DATA_SOURCE", "yahoo")

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
# v1.0 gold pivot: single tier ("forex_major") holding the one symbol we
# care about. The 57-pair crypto roster (tier_1_major ... tier_4_development)
# from 0.9.x is retained as a commented reference so legacy CLI flags
# like --tier tier_1_major still resolve to "no pairs" instead of crashing.
WATCHLIST_TIERS: tuple[str, ...] = (
    "forex_major",
    # legacy tiers from 0.9.x — kept for forward compatibility of CLI args
    "tier_1_major",
    "tier_2_large_cap",
    "tier_3_mid_cap",
    "tier_4_development",
)

# --- Default CLI values ---
# v1.0 pivot: 1d is the default timeframe for XAU/USD (gold moves ~1-2%/day,
# so 1d bars give confluence enough samples without over-fitting). 4h is a
# valid alternative via the Yahoo 1h-resample path in YahooFinanceFetcher.
# 1h is no longer the default because gold 1h bars are noisier and Yahoo's
# 1h history is capped at 730d anyway.
DEFAULT_TIMEFRAME: str = "1d"
DEFAULT_LIMIT: int = 500

# --- Confluence Scorer (Phase 3) ---
# Skor 0-4 berdasarkan berapa banyak dari 4 indikator yang align searah.
CONFLUENCE_MIN_VALID: int = 2  # turun dari 3 -> 2 (lebih banyak sinyal, WR lebih rendah)
CONFLUENCE_A_PLUS: int = 4  # A+ setup -> size up
CONFLUENCE_STRONG: int = 3  # Strong signal (3/4) -> normal size
A_PLUS_SIZE_MULTIPLIER: float = 1.5
VALID_SIZE_MULTIPLIER: float = 1.0
SKIP_SIZE_MULTIPLIER: float = 0.0
MIN_RISK_REWARD: float = 2.0  # R:R minimum 1:2 (TP >= 2x jarak SL)

# --- Daemon signal filters (Phase 6+) ---
# Volume filter: require current volume > 1.2x avg of last 20 bars
DAEMON_VOLUME_MULT: float = 1.2
DAEMON_VOLUME_LOOKBACK: int = 20
# Trend filter: require ADX > 20 for entry (avoid choppy/ranging)
DAEMON_MIN_ADX: float = 20.0
# Spread filter: skip pairs with spread > 0.3% (illiquid)
DAEMON_MAX_SPREAD_PCT: float = 0.3

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
BACKTEST_RISK_PER_TRADE: float = 0.015
# Time stop: berapa bar maksimum hold sebelum force-close.
# v0.9.1: turunkan dari 50 → 30 (~5 hari di 4h). Trade yang tidak bergerak
# dalam 5 hari adalah noise — modal lebih baik di-redeploy ke setup baru.
BACKTEST_MAX_BARS_HOLD: int = 30
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

# --- Paper Trading System (Phase 6) ---
# Flag mode paper trading. Default True (ON) — Phase 7 live trading akan
# override ke False. Override via env PAPER_MODE=false kalau perlu.
PAPER_MODE: bool = os.getenv("PAPER_MODE", "true").lower() == "true"
# Modal awal paper portfolio (USD). Dipakai oleh PaperPortfolio.start().
PAPER_INITIAL_BALANCE: float = 10_000.0
# Risk per trade (fraksi equity) — dipakai untuk position sizing.
# v1.0 pivot: turun dari 0.02 -> 0.015. Gold daily vol ~1-2% (vs BTC ~3-7%),
# so a 1.5% risk budget keeps expected max drawdown in line with the
# 0.9.x BTC numbers. Pair this with ATR-based SL/TP (sl_dist in
# PaperTrader.open_from_signal()) rather than fixed %.
PAPER_RISK_PER_TRADE: float = float(os.getenv("PAPER_RISK_PER_TRADE", "0.015"))
# Batas posisi terbuka simultan. Tetap 3 untuk future-proof: kalau nanti
# ada pairs kedua (mis. EUR/USD, XAG/USD) kita tidak perlu ubah config.
PAPER_MAX_OPEN_POSITIONS: int = 3
# Batas entry baru per hari (untuk mencegah over-trading).
PAPER_MAX_DAILY_TRADES: int = 3
# Max correlated positions (per STRATEGY.md line 162: "Max 2 posisi correlated").
# If BTC drops 5%, multiple L1-alts drop 8-12% — multiple correlated positions
# magnify risk, not diversify it. Default 2: e.g. 1 BTC + 1 ETH OK, 3rd L1-alt rejected.
PAPER_MAX_CORRELATED_POSITIONS: int = 2
# Daily loss limit (fraksi equity) — kalau tercapai, stop trading untuk hari itu.
# 5% dari balance awal = $500 di $10k.
PAPER_DAILY_LOSS_LIMIT: float = 0.05
# Maximum drawdown circuit breaker (fraksi dari peak equity).
# Kalau drawdown > 15%, pause trading selama 24 jam.
PAPER_MAX_DRAWDOWN_CIRCUIT: float = 0.15
# Move SL ke entry (breakeven) ketika TP1 hit. Standar money management
# untuk "lock in" trade yang sudah untung.
PAPER_TP1_HIT_BREAKEVEN: bool = True
# Berapa kali lipat (risk) untuk TP1 (1.0R) dan TP2 (2.0R default).
# Position ditutup 50% di TP1, sisa 50% di TP2.
PAPER_TP1_RR_RATIO: float = 1.0
PAPER_TP2_RR_RATIO: float = 2.0
PAPER_TP1_CLOSE_PCT: float = 0.50  # fraction closed at TP1
# Monitor loop interval (detik) untuk cek SL/TP.
PAPER_MONITOR_INTERVAL_SECONDS: int = int(os.getenv("PAPER_MONITOR_INTERVAL_SECONDS", "60"))
# Time-stop untuk monitor: berapa bar maksimum hold sebelum force-close.
# Default 50 (sama dengan backtest default untuk apples-to-apples compare).
PAPER_MAX_BARS_HOLD: int = int(os.getenv("PAPER_MAX_BARS_HOLD", "50"))
# Time-stop window dalam detik (default 4 jam = 1 cycle untuk 1h timeframe).
# Monitor akan force-close trade yang lebih lama dari ini.
PAPER_TIME_STOP_SECONDS: int = int(os.getenv("PAPER_TIME_STOP_SECONDS", "14400"))
# Default lookback days untuk paper report.
PAPER_REPORT_DEFAULT_DAYS: int = int(os.getenv("PAPER_REPORT_DEFAULT_DAYS", "7"))
# Path directory untuk chart output.
PAPER_REPORTS_DIR: Path = PROJECT_ROOT / "paper" / "reports"

# --- Multi-Timeframe (MTF) Filter for Paper Trading ---
# Kalau enabled, setiap 15M/entry-TF signal dicek terhadap daily bias.
# Daily bias direction (long/short) harus match dengan signal direction,
# else signal di-skip. Validated via backtest: PF 2.18 vs 0.82 baseline.
PAPER_MTF_ENABLED: bool = os.getenv("PAPER_MTF_ENABLED", "true").lower() == "true"
# Minimum confluence score di daily bar untuk qualify as valid bias.
# 1 = accept any non-skip daily grade (validated optimal).
PAPER_MTF_DAILY_MIN_SCORE: int = int(os.getenv("PAPER_MTF_DAILY_MIN_SCORE", "1"))
# Minimum confluence score di entry timeframe (15M/1H) untuk trigger entry.
PAPER_MTF_15M_MIN_SCORE: int = int(os.getenv("PAPER_MTF_15M_MIN_SCORE", "2"))
# Symbol yang dipakai untuk daily bias lookup. Default XAU/USD sesuai v1.0 pivot.
PAPER_MTF_DAILY_SYMBOL: str = os.getenv("PAPER_MTF_DAILY_SYMBOL", "XAU/USD")
# Cache TTL untuk daily bias (detik) — biar gak fetch tiap signal.
PAPER_MTF_BIAS_CACHE_TTL: int = int(os.getenv("PAPER_MTF_BIAS_CACHE_TTL", "3600"))

# --- Tighter MTF (v1.1.1) — adds 4H layer between 1D and 15M ---
# Opt-in flag (default OFF). When True, check_tight_mtf_filter replaces
# check_mtf_filter in PaperTrader.open_from_signal. 4H bias is computed
# from aggregated Yahoo 1H data (manual 4-bar groupby -> 1 4H bar).
# Validated backtest (/tmp/xauusd_mtf_tweaks_report.md):
#   tight_4h: 6 trades, WR 66.7%, PF 2.33, DD 1.92% (best DD), PnL +$264
PAPER_MTF_TIGHT_ENABLED: bool = os.getenv("PAPER_MTF_TIGHT_ENABLED", "false").lower() == "true"
# Minimum confluence score on 4H bar to qualify as valid 4H bias.
# Default 1 (accept any non-skip grade), mirrors PAPER_MTF_DAILY_MIN_SCORE.
PAPER_MTF_4H_MIN_SCORE: int = int(os.getenv("PAPER_MTF_4H_MIN_SCORE", "1"))

# --- Pass/fail criteria for greenlighting Phase 7 (live trading) ---
# Threshold ini yang dipakai paper/reporter.py untuk menilai apakah
# real-time paper performance "cocok" dengan backtest. Definisi:
#   - paper_win_rate >= backtest_win_rate - 0.10  (toleransi 10% lebih buruk)
#   - paper_profit_factor >= 1.0  (tidak rugi bersih)
#   - paper_max_drawdown <= BACKTEST target (0.20)
PAPER_PHASE7_WIN_RATE_TOLERANCE: float = 0.10
PAPER_PHASE7_MIN_PROFIT_FACTOR: float = 1.0
PAPER_PHASE7_MAX_DRAWDOWN: float = TARGET_MAX_DRAWDOWN
# Minimum closed trades untuk statistical significance.
PAPER_PHASE7_MIN_TRADES: int = 30


def ensure_dirs() -> None:
    """Pastikan semua direktori yang dibutuhkan sudah ada."""
    for d in (
        DATA_DIR,
        STORAGE_DIR,
        FETCHERS_DIR,
        PAIRS_DIR,
        LOGS_DIR,
        BACKTEST_OUTPUT_DIR,
        PAPER_REPORTS_DIR,
    ):
        d.mkdir(parents=True, exist_ok=True)
