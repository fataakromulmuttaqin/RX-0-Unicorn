"""
RX-0 Unicorn Paper Trading Daemon (long-running)
Runs in background, scans every 5 min, monitors positions every 60s.
STOP: touch ~/.rx0_paper_stop  (or kill -TERM <pid>)
"""
import os
import sys
import time
import signal
import json
import traceback
from pathlib import Path
from datetime import datetime, timezone

# === BOOTSTRAP ===
PROJECT = "/home/fataakromulm/RX-0_Unicorn"
STOP_FILE = Path.home() / ".rx0_paper_stop"
LOG_PATH = f"{PROJECT}/logs/rx0_paper_daemon.log"
PID_PATH = Path.home() / ".rx0_paper_daemon.pid"
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL_SECONDS", "300"))
PRICE_POLL_INTERVAL = 60
WATCHLIST_PATH = f"{PROJECT}/data/pairs/watchlist.json"
PAPER_DB = f"{PROJECT}/data/storage/paper_trades.db"
CANDLE_DB = f"{PROJECT}/data/storage/candles.db"

# Load .env
env_file = Path(f"{PROJECT}/.env")
if env_file.exists():
    for line in env_file.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, PROJECT)

# Setup logging
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
from loguru import logger as _loguru_logger
_loguru_logger.remove()
_loguru_logger.add(sys.stderr, level="INFO", format="<green>{time:HH:mm:ss}</green> | {level: <7} | {message}")
_loguru_logger.add(LOG_PATH, rotation="10 MB", retention="7 days", level="DEBUG")

logger = _loguru_logger

# Write PID
PID_PATH.write_text(str(os.getpid()))
logger.info(f"PID file: {PID_PATH} = {os.getpid()}")

# === SIGNALS ===
shutdown_requested = False
def request_shutdown(*args):
    global shutdown_requested
    shutdown_requested = True
    logger.info("🛑 Shutdown signal received")
    # Also stop command listener thread
    if _cmd_stop is not None:
        try:
            _cmd_stop.set()
        except Exception:
            pass
    try:
        STOP_FILE.touch()
    except Exception:
        pass

signal.signal(signal.SIGINT, request_shutdown)
signal.signal(signal.SIGTERM, request_shutdown)

# Start Telegram command listener (background thread)
try:
    from alerts.commands import start_command_listener_thread
    _cmd_thread, _cmd_stop = start_command_listener_thread()
    logger.info("📱 Telegram command listener thread started")
except Exception as e:
    logger.warning(f"Telegram command listener not started: {e}")
    _cmd_thread = None
    _cmd_stop = None

# === IMPORTS ===
import ccxt
import httpx
from paper.notifier import PaperNotifier
from paper.journal import PaperJournal
from paper.portfolio import PaperPortfolio
from paper.trader import PaperTrader
from confluence import latest_confluence
from data.storage.candle_db import CandleDB
from src.config import (
    CONFLUENCE_MIN_VALID,
    CONFLUENCE_A_PLUS,
    DAEMON_VOLUME_MULT,
    DAEMON_VOLUME_LOOKBACK,
    DAEMON_MIN_ADX,
    DAEMON_MAX_SPREAD_PCT,
)

# Multi-exchange fetcher for robust data
try:
    from data.fetchers.multi_exchange import (
        fetch_ohlcv_multi, fetch_ticker_multi
    )
    MULTI_EXCHANGE_AVAILABLE = True
except ImportError:
    MULTI_EXCHANGE_AVAILABLE = False

# === INIT ===
logger.info("=" * 70)
logger.info("🚀 RX-0 Unicorn Paper Trading Daemon")
logger.info("=" * 70)
logger.info(f"PID:        {os.getpid()}")
logger.info(f"Scan every: {SCAN_INTERVAL}s")
logger.info(f"Poll every: {PRICE_POLL_INTERVAL}s")
logger.info(f"Stop file:  {STOP_FILE}")

# Remove old stop file
if STOP_FILE.exists():
    STOP_FILE.unlink()

# Notifier
notif = PaperNotifier()
logger.info(f"Telegram: {notif.bot.is_configured}")

# Exchange (Gate.io — Binance blocked)
ex = ccxt.gate({"enableRateLimit": True})
ex.load_markets()

# Watchlist
with open(WATCHLIST_PATH) as f:
    wl = json.load(f)
all_pairs = []
for tier_pairs in wl.values():
    all_pairs.extend(tier_pairs)
logger.info(f"Watchlist: {len(all_pairs)} pairs")

# DB
# Note: cdb must be used as context manager. We open it once at startup.
cdb = CandleDB()
cdb.__enter__()  # manually enter context (we'll never close until shutdown)

# === HELPERS ===
def fetch_price(symbol):
    """Get current price with multi-exchange fallback."""
    # Try multi-exchange first (Binance data API primary)
    if MULTI_EXCHANGE_AVAILABLE:
        price = fetch_ticker_multi(symbol, preferred="binance")
        if price and price > 0:
            return price
    # Fallback: Gate.io
    try:
        sym = symbol.replace("/", "_")
        if sym not in ex.markets:
            return None
        t = ex.fetch_ticker(sym)
        return float(t.get("last") or 0) or None
    except Exception as e:
        logger.debug(f"price {symbol}: {e}")
        return None


def get_spread_pct(symbol: str) -> float | None:
    """Get current bid-ask spread as %. Lower = more liquid."""
    try:
        if MULTI_EXCHANGE_AVAILABLE:
            # Use Binance data API for spread
            sym = symbol.replace("/", "")
            r = httpx.get(
                f"https://data-api.binance.vision/api/v3/ticker/bookTicker?symbol={sym}",
                timeout=5,
            )
            if r.status_code == 200:
                data = r.json()
                bid = float(data.get("bidPrice", 0))
                ask = float(data.get("askPrice", 0))
                if bid > 0 and ask > 0:
                    return (ask - bid) / ask * 100
    except Exception:
        pass
    return None


def passes_filter(symbol: str, df, conf: dict) -> tuple[bool, str]:
    """
    Apply daemon signal filters: volume + ADX + spread.
    Returns (passes: bool, reason: str).
    """
    if df is None or len(df) < DAEMON_VOLUME_LOOKBACK:
        return False, "insufficient_data"

    # 1. Volume filter
    try:
        avg_vol = float(df["volume"].tail(DAEMON_VOLUME_LOOKBACK).mean())
        cur_vol = float(df["volume"].iloc[-1])
        if avg_vol > 0 and cur_vol < avg_vol * DAEMON_VOLUME_MULT:
            return False, f"low_volume ({cur_vol/avg_vol:.2f}x avg)"
    except Exception:
        pass

    # 2. ADX filter (need at least 14 bars for ADX)
    try:
        # Check if ADX is in the scored dataframe
        adx_val = None
        for col in df.columns:
            if col.lower() == "adx":
                adx_val = float(df[col].iloc[-1])
                break
        if adx_val is not None and adx_val < DAEMON_MIN_ADX:
            return False, f"low_adx ({adx_val:.1f} < {DAEMON_MIN_ADX})"
    except Exception:
        pass

    # 3. Spread filter
    spread = get_spread_pct(symbol)
    if spread is not None and spread > DAEMON_MAX_SPREAD_PCT:
        return False, f"wide_spread ({spread:.2f}%)"

    return True, "ok"


def get_signals():
    """
    Get all signals (score >= CONFLUENCE_MIN_VALID) with quality filters.
    Filters: volume spike, ADX trending, tight spread.
    """
    sigs = []
    for symbol in all_pairs:
        try:
            df = cdb.get_candles(pair=symbol, timeframe="1h", limit=200)
            if df is None or len(df) < 60:
                continue
            conf = latest_confluence(df)
            if not conf or conf.get("score", 0) < CONFLUENCE_MIN_VALID:
                continue
            # Apply quality filters
            passes, reason = passes_filter(symbol, df, conf)
            if not passes:
                logger.debug(f"  ⏭ {symbol} filtered: {reason}")
                continue
            conf["symbol"] = symbol
            conf["entry_price"] = conf.get("entry_price") or float(df["close"].iloc[-1])
            conf["filter_reason"] = "ok"
            sigs.append(conf)
        except Exception as e:
            logger.debug(f"signal {symbol}: {e}")
    return sigs

def monitor():
    """
    Check open positions for SL/TP hits.
    Also apply trailing stop after TP1 hit.
    """
    closed = 0
    trailed = 0
    with PaperJournal() as j:
        p = PaperPortfolio(journal=j, notifier=notif)
        open_trades = [t for t in j.get_all_trades() if t.get("status") == "open"]
        for t in open_trades:
            sym = t["symbol"]
            entry = t["entry_price"]
            sl = t["stop_loss"]
            tp1 = t["take_profit_1"]
            tp2 = t["take_profit_2"]
            direction = t["direction"]
            price = fetch_price(sym)
            if price is None:
                continue
            # Check if TP1 was already hit (entry_time vs TP1 in trade state)
            # For now: if price > entry (in profit), apply trailing
            exit_price, reason = None, None
            if direction == "long":
                if price <= sl:
                    exit_price, reason = sl, "sl"
                elif price >= tp2:
                    exit_price, reason = tp2, "tp2"
                elif price >= tp1:
                    # TP1 hit: trail SL then exit at TP1
                    if p.trailing_stop(t["trade_id"], price, trail_pct=0.5):
                        trailed += 1
                        logger.debug(f"  📈 Trailing SL on {sym} (price={price})")
                    exit_price, reason = tp1, "tp1"
            else:
                if price >= sl:
                    exit_price, reason = sl, "sl"
                elif price <= tp2:
                    exit_price, reason = tp2, "tp2"
                elif price <= tp1:
                    if p.trailing_stop(t["trade_id"], price, trail_pct=0.5):
                        trailed += 1
                        logger.debug(f"  📉 Trailing SL on {sym} (price={price})")
                    exit_price, reason = tp1, "tp1"
            if exit_price is not None:
                r = p.close_position(trade_id=t["trade_id"], exit_price=exit_price, exit_reason=reason)
                if r:
                    pnl = r.get("pnl_usd", 0)
                    emoji = "🟢" if pnl > 0 else "🔴"
                    logger.info(f"{emoji} CLOSED {sym} {reason} @ {exit_price} → ${pnl:+.2f}")
                    closed += 1
    if trailed > 0:
        logger.info(f"  📈 Trailing stops updated on {trailed} position(s)")
    return closed

def send_daily_digest_if_needed():
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    with PaperJournal() as j:
        last = j.get_state("last_daily_digest_date", "")
    if now.hour == 0 and now.minute >= 5 and last != today:
        try:
            with PaperJournal() as j2:
                p = PaperPortfolio(journal=j2, notifier=notif)
                equity = p.get_equity()
                today_start = int(datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
                closed_today = [t for t in j2.get_all_trades() if t.get("status") == "closed" and (t.get("exit_time") or 0) >= today_start]
                wins = sum(1 for t in closed_today if (t.get("pnl_usd") or 0) > 0)
                losses = len(closed_today) - wins
                open_t = [t for t in j2.get_all_trades() if t.get("status") == "open"]
                daily_pnl = sum(t.get("pnl_usd") or 0 for t in closed_today)
                cum_pnl = equity - 10000.0
                cum_pct = cum_pnl / 10000.0
                win_rate = wins / max(1, len(closed_today))
                drawdown = p.get_drawdown_pct(equity)
                notif.notify_daily_digest({
                    "date": today,
                    "equity": equity,
                    "cum_pnl": cum_pnl,
                    "cum_pct": cum_pct,
                    "daily_pnl": daily_pnl,
                    "trades_today": len(closed_today),
                    "wins_today": wins,
                    "losses_today": losses,
                    "win_rate": win_rate,
                    "open_positions": len(open_t),
                    "drawdown": drawdown,
                })
                with PaperJournal() as j3:
                    j3.set_state("last_daily_digest_date", today)
                logger.info(f"📊 Daily digest sent for {today}")
        except Exception as e:
            logger.error(f"daily digest: {e}")

# === MAIN LOOP ===
try:
    # Startup notif
    notif._send(
        f"🚀 <b>RX-0 Paper Trading — STARTED</b>\n"
        f"Scan: {SCAN_INTERVAL}s | Watchlist: {len(all_pairs)} pairs\n"
        f"Stop file: {STOP_FILE}\n"
        f"PID: {os.getpid()}",
        tier=99, parse_mode="HTML"
    )
except Exception as e:
    logger.warning(f"startup notif: {e}")

last_scan = 0
last_poll = 0
cycle = 0
logger.info("🚀 Main loop starting")
print(f"✅ Daemon started PID {os.getpid()}. To stop: touch {STOP_FILE}")

while not shutdown_requested and not STOP_FILE.exists():
    try:
        now = time.time()
        cycle += 1

        # === SCAN CYCLE (every SCAN_INTERVAL) ===
        if now - last_scan >= SCAN_INTERVAL:
            sigs = get_signals()
            logger.info(f"🔍 Scan #{cycle}: {len(sigs)} valid signal(s)")
            with PaperJournal() as j:
                p = PaperPortfolio(journal=j, notifier=notif)
                t = PaperTrader(journal=j, notifier=notif)
                opened = 0
                for sig in sigs:
                    try:
                        r = t.open_from_signal(signal=sig, symbol=sig["symbol"])
                        if r:
                            opened += 1
                            logger.info(f"  ✅ OPEN {r['symbol']} {r.get('direction')} @ {r.get('entry_price')}")
                    except Exception as e:
                        logger.debug(f"open {sig['symbol']}: {e}")
                if opened == 0 and len(sigs) > 0:
                    logger.info(f"  → {len(sigs)} signals but risk limits hit")
                elif len(sigs) == 0:
                    logger.info("  → No valid signals (market flat)")
            last_scan = now

        # === MONITOR CYCLE (every PRICE_POLL_INTERVAL) ===
        if now - last_poll >= PRICE_POLL_INTERVAL:
            with PaperJournal() as j:
                open_count = len([t for t in j.get_all_trades() if t.get("status") == "open"])
            if open_count > 0:
                # monitor() already uses its own with-block, safe to call directly
                c = monitor()
                if c:
                    logger.info(f"👁 Closed {c} position(s)")
            last_poll = now

        # === DAILY DIGEST ===
        send_daily_digest_if_needed()

        # === STATUS every ~5 min (30 cycles of 10s) ===
        if cycle % 30 == 0:
            with PaperJournal() as j:
                p = PaperPortfolio(journal=j, notifier=notif)
                equity = p.get_equity()
                all_t = j.get_all_trades()
                closed_t = [t for t in all_t if t.get("status") == "closed"]
                open_t = [t for t in all_t if t.get("status") == "open"]
                wins = sum(1 for t in closed_t if (t.get("pnl_usd") or 0) > 0)
                wr = wins / max(1, len(closed_t)) * 100
                dd = p.get_drawdown_pct(equity)
                logger.info(
                    f"📊 Status: equity=${equity:,.2f} | open={len(open_t)} closed={len(closed_t)} | "
                    f"WR={wr:.0f}% | DD={dd:.2%}"
                )

        time.sleep(10)
    except Exception as e:
        logger.error(f"Loop error: {e}")
        logger.debug(traceback.format_exc())
        time.sleep(30)

# === SHUTDOWN ===
logger.info("=" * 70)
logger.info("🛑 Daemon STOPPED")
logger.info("=" * 70)
try:
    with PaperJournal() as j:
        p = PaperPortfolio(journal=j, notifier=notif)
        all_t = j.get_all_trades()
        closed = [t for t in all_t if t.get("status") == "closed"]
        open_p = [t for t in all_t if t.get("status") == "open"]
        wins = sum(1 for t in closed if (t.get("pnl_usd") or 0) > 0)
        equity = p.get_equity()
        logger.info(f"Final equity:  ${equity:,.2f} (P/L ${equity-10000:+,.2f} / {(equity-10000)/100:+.2f}%)")
        logger.info(f"Open: {len(open_p)} | Closed: {len(closed)} | Win rate: {wins/max(1,len(closed))*100:.0f}%")
        notif._send(
            f"🛑 <b>RX-0 Paper Trading — STOPPED</b>\n"
            f"Final equity: ${equity:,.2f}\n"
            f"P/L: ${equity-10000:+,.2f} ({(equity-10000)/100:+.2f}%)\n"
            f"Trades: {len(closed)} closed | Win rate: {wins/max(1,len(closed))*100:.0f}%",
            tier=99, parse_mode="HTML"
        )
except Exception as e:
    logger.error(f"Shutdown summary error: {e}")

# Cleanup
if STOP_FILE.exists():
    STOP_FILE.unlink()
if PID_PATH.exists():
    PID_PATH.unlink()
logger.info("Cleanup done. Bye! 🦄")
