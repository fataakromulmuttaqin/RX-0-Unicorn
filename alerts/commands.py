"""
Telegram command interface for RX-0 Unicorn.

Listen to /rx0 commands from authorized chat_id:
  /rx0 status      → current equity, positions, P/L
  /rx0 trades      → recent closed trades
  /rx0 stop        → stop daemon (writes stop file)
  /rx0 start       → resume daemon
  /rx0 help        → show this help
  /rx0 daily       → force daily digest now
  /rx0 weekly      → force weekly report now
  /rx0 journal     → show last N trades
  /rx0 cooldown    → list/clear cooldowns

Polls Telegram getUpdates every 2 seconds in a separate thread.
"""
from __future__ import annotations

import os
import sys
import time
import threading
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone

from loguru import logger
import httpx
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")
sys.path.insert(0, str(PROJECT_ROOT))

# Import notifier for sending responses
from alerts.telegram import TelegramBot


def _send_message(bot: TelegramBot, chat_id: str, text: str) -> bool:
    """Send a message back to the user."""
    if not bot.is_configured:
        logger.info(f"[tg-cmd:no-bot] would send to {chat_id}:\n{text[:200]}")
        return False
    try:
        url = f"{bot.API_BASE}/bot{bot.token}/sendMessage"
        r = httpx.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)
        return r.status_code == 200
    except Exception as e:
        logger.error(f"send_message error: {e}")
        return False


def get_status_text() -> str:
    """Build current status text from paper DB."""
    try:
        db = PROJECT_ROOT / "data" / "storage" / "paper_trades.db"
        if not db.exists():
            return "📊 No paper trading data yet. Start daemon first."
        conn = sqlite3.connect(db)
        cur = conn.cursor()

        # Get balance
        cur.execute("SELECT key, value FROM paper_state WHERE key='K_BALANCE'")
        row = cur.fetchone()
        balance = float(row[1]) if row else 10000.0
        initial = 10000.0
        pnl = balance - initial
        pnl_pct = (pnl / initial) * 100

        # Open/closed counts
        cur.execute("SELECT COUNT(*) FROM paper_trades WHERE status='open'")
        open_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM paper_trades WHERE status='closed'")
        closed_count = cur.fetchone()[0]

        # Win rate
        cur.execute("SELECT COUNT(*) FROM paper_trades WHERE status='closed' AND pnl_usd > 0")
        wins = cur.fetchone()[0]
        wr = (wins / closed_count * 100) if closed_count > 0 else 0

        # Daemon status
        pid_file = Path.home() / ".rx0_paper_daemon.pid"
        daemon_status = "🟢 RUNNING" if pid_file.exists() else "🔴 STOPPED"
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                os.kill(pid, 0)  # check if alive
            except (OSError, ProcessLookupError):
                daemon_status = "🔴 DEAD (stale PID)"

        # Open positions detail
        cur.execute(
            "SELECT trade_id, symbol, direction, entry_price, sl, tp1, tp2, confluence_score, grade "
            "FROM paper_trades WHERE status='open' ORDER BY entry_time DESC LIMIT 5"
        )
        open_lines = []
        for r in cur.fetchall():
            emoji = "🟢" if r[2] == "long" else "🔴"
            open_lines.append(
                f"  {emoji} {r[1]:10s} {r[2]:5s} @ {r[3]:.4f} SL={r[4]:.4f} TP1={r[5]:.4f} ({r[7]}/4 {r[8]})"
            )

        open_section = "\n".join(open_lines) if open_lines else "  (none)"

        text = (
            f"📊 **RX-0 Unicorn Status**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💰 Equity: ${balance:,.2f}\n"
            f"📈 P/L: ${pnl:+,.2f} ({pnl_pct:+.2f}%)\n"
            f"📊 Trades: {open_count} open / {closed_count} closed\n"
            f"🎯 Win rate: {wins}/{closed_count} = {wr:.1f}%\n"
            f"🤖 Daemon: {daemon_status}\n"
            f"\n**Open positions:**\n{open_section}"
        )
        conn.close()
        return text
    except Exception as e:
        return f"❌ Error reading status: {e}"


def get_trades_text(limit: int = 10) -> str:
    """Show last N closed trades."""
    try:
        db = PROJECT_ROOT / "data" / "storage" / "paper_trades.db"
        conn = sqlite3.connect(db)
        cur = conn.cursor()
        cur.execute(
            "SELECT trade_id, symbol, direction, entry_price, exit_price, exit_reason, pnl_usd, pnl_r_multiple, exit_time "
            "FROM paper_trades WHERE status='closed' ORDER BY exit_time DESC LIMIT ?",
            (limit,),
        )
        rows = cur.fetchall()
        if not rows:
            return "📋 No closed trades yet."
        lines = ["📋 **Recent Closed Trades**", "━━━━━━━━━━━━━━━━━━"]
        for r in rows:
            emoji = "🟢" if r[6] > 0 else "🔴"
            from datetime import datetime as dt
            ts = dt.fromtimestamp(r[8], tz=timezone.utc).strftime("%m-%d %H:%M")
            lines.append(
                f"{emoji} {r[1]:10s} {r[2]:5s} "
                f"${r[3]:.4f}→${r[4]:.4f} "
                f"({r[5]}) "
                f"${r[6]:+.2f} ({r[7]:+.2f}R) {ts}"
            )
        conn.close()
        return "\n".join(lines)
    except Exception as e:
        return f"❌ Error: {e}"


def get_help_text() -> str:
    """Command list."""
    return (
        "🤖 **RX-0 Unicorn Commands**\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "/rx0 status    → equity, positions, P/L\n"
        "/rx0 trades    → recent closed trades\n"
        "/rx0 stop      → stop daemon gracefully\n"
        "/rx0 start     → resume daemon (writes start flag)\n"
        "/rx0 daily     → send daily digest now\n"
        "/rx0 weekly    → send weekly report now\n"
        "/rx0 journal N → show last N trades (default 10)\n"
        "/rx0 help      → this message\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Bisa juga langsung: /status /trades /help"
    )


def handle_command(bot: TelegramBot, chat_id: str, cmd: str) -> str | None:
    """
    Process a command, return response text (or None if no response needed).
    """
    cmd = cmd.strip().lower()

    # Handle both /rx0 X and /X
    if cmd.startswith("/rx0 "):
        cmd = cmd[5:].strip()
    elif cmd.startswith("/"):
        cmd = cmd[1:].strip()
    else:
        return None  # not a command

    if cmd in ("status", "s"):
        return get_status_text()

    elif cmd in ("trades", "t"):
        return get_trades_text(limit=10)

    elif cmd in ("help", "h", "?"):
        return get_help_text()

    elif cmd in ("stop",):
        # Write stop file
        stop_file = Path.home() / ".rx0_paper_stop"
        stop_file.touch()
        return "🛑 Daemon will stop after current cycle. Use 'start' to resume."

    elif cmd in ("start",):
        # Remove stop file
        stop_file = Path.home() / ".rx0_paper_stop"
        if stop_file.exists():
            stop_file.unlink()
        return "▶️ Daemon resume flag set. Will continue on next scan."

    elif cmd.startswith("journal"):
        # /rx0 journal N
        parts = cmd.split()
        n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 10
        n = min(n, 50)  # cap
        return get_trades_text(limit=n)

    elif cmd in ("daily",):
        # Trigger daily digest
        try:
            from paper.journal import PaperJournal
            from paper.notifier import PaperNotifier
            from paper.portfolio import PaperPortfolio
            local_notif = PaperNotifier()
            with PaperJournal() as j:
                p = PaperPortfolio(journal=j, notifier=local_notif)
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                closed_today = [t for t in j.get_all_trades() if t.get("status") == "closed"]
                wins = sum(1 for t in closed_today if (t.get("pnl_usd") or 0) > 0)
                equity = p.get_equity()
                local_notif.notify_daily_digest({
                    "date": today,
                    "equity": equity,
                    "cum_pnl": equity - 10000,
                    "cum_pct": (equity - 10000) / 10000,
                    "daily_pnl": sum(t.get("pnl_usd") or 0 for t in closed_today),
                    "trades_today": len(closed_today),
                    "wins_today": wins,
                    "losses_today": len(closed_today) - wins,
                    "win_rate": wins / max(1, len(closed_today)),
                    "open_positions": len([t for t in j.get_all_trades() if t.get("status") == "open"]),
                    "drawdown": p.get_drawdown_pct(equity),
                })
                with PaperJournal() as j2:
                    j2.set_state("last_daily_digest_date", today)
            return f"📊 Daily digest sent for {today}"
        except Exception as e:
            return f"❌ Daily digest error: {e}"

    elif cmd in ("weekly",):
        return "📈 Weekly report generation — run `python main.py paper weekly-report`"

    else:
        return f"❓ Unknown command: {cmd}\n\n{get_help_text()}"


def is_authorized_chat(chat_id: str) -> bool:
    """Check if chat_id is the authorized one (from .env)."""
    expected = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    return bool(expected) and chat_id == expected


def run_command_listener(stop_event: threading.Event | None = None):
    """
    Main loop: poll Telegram getUpdates, process commands.
    Run in a separate thread from main daemon.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    if not token or not chat_id:
        logger.warning("Telegram command listener disabled (no token/chat_id)")
        return

    bot = TelegramBot()  # for sending responses
    last_update_id = 0
    url = f"https://api.telegram.org/bot{token}/getUpdates"

    logger.info(f"📱 Telegram command listener started (chat_id={chat_id})")

    while stop_event is None or not stop_event.is_set():
        try:
            r = httpx.get(
                url,
                params={"offset": last_update_id + 1, "timeout": 5, "allowed_updates": ["message"]},
                timeout=10,
            )
            data = r.json()
            if not data.get("ok"):
                time.sleep(2)
                continue

            for update in data.get("result", []):
                last_update_id = max(last_update_id, update["update_id"])
                msg = update.get("message") or {}
                text = msg.get("text", "").strip()
                chat = msg.get("chat", {}).get("id", "")
                if not text or not chat:
                    continue

                if not is_authorized_chat(str(chat)):
                    logger.warning(f"Unauthorized command from chat {chat}: {text}")
                    continue

                if text.startswith("/rx0") or text.startswith("/") and any(
                    text[1:].startswith(c) for c in ["status", "trades", "stop", "start", "help", "daily", "weekly", "journal", "s", "t", "h"]
                ):
                    logger.info(f"📱 Command: {text}")
                    response = handle_command(bot, str(chat), text)
                    if response:
                        _send_message(bot, str(chat), response)

            time.sleep(2)  # poll interval

        except Exception as e:
            logger.debug(f"command listener error: {e}")
            time.sleep(5)

    logger.info("📱 Telegram command listener stopped")


def start_command_listener_thread() -> tuple[threading.Thread, threading.Event]:
    """Start listener in background thread. Returns (thread, stop_event)."""
    stop_event = threading.Event()
    t = threading.Thread(
        target=run_command_listener,
        args=(stop_event,),
        daemon=True,
        name="tg-command-listener",
    )
    t.start()
    return t, stop_event


if __name__ == "__main__":
    # Standalone test
    print(get_status_text())
    print("---")
    print(get_trades_text(limit=5))
    print("---")
    print(get_help_text())
