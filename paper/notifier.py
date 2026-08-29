"""
PaperNotifier — 5-tier Telegram notification system for paper trading (Phase 6).

Tiers:
  1. notify_entry(trade)            -> entry alert (Tier 1)
  2. notify_exit(trade)             -> exit alert with P/L (Tier 2)
  3. notify_daily_digest(state)     -> end-of-day summary (Tier 3)
  4. notify_weekly_report(report)   -> weekly report + chart (Tier 4)
  5. notify_risk_breach(type, ...)  -> risk alert (Tier 5)

Graceful degradation: kalau TELEGRAM_BOT_TOKEN kosong (lihat
alerts/telegram.py), notifier log ke console (level INFO) dan return
False. Sama pattern dengan Phase 4 alert system.

Daily / weekly scheduling: PAPER_DAILY_DIGEST_HOUR_UTC dan
PAPER_WEEKLY_REPORT_DOW (0=Monday..6=Sunday) dipakai oleh
PaperTrader.monitor_loop() untuk fire otomatis. User juga bisa
trigger manual via `python main.py paper daily-digest` atau
`python main.py paper weekly-report`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from alerts.telegram import TelegramBot
from src.logger import logger


# Tier labels
TIER_ENTRY: int = 1
TIER_EXIT: int = 2
TIER_DAILY: int = 3
TIER_WEEKLY: int = 4
TIER_RISK: int = 5

# Tier emoji prefixes
_TIER_EMOJI: dict[int, str] = {
    TIER_ENTRY: "🟢",  # entry
    TIER_EXIT: "🔵",  # exit
    TIER_DAILY: "📊",  # daily
    TIER_WEEKLY: "📈",  # weekly
    TIER_RISK: "🚨",  # risk
}

# Grade emoji (sama dengan Phase 4 convention)
_GRADE_EMOJI: dict[str, str] = {
    "a_plus": "⭐",
    "valid": "🟢",
    "skip": "⚪",
}
_EXIT_REASON_EMOJI: dict[str, str] = {
    "tp1": "🎯",
    "tp2": "🎯",
    "sl": "🛑",
    "time_stop": "⏰",
    "manual": "✋",
    "end_of_data": "📉",
    "cancelled": "❌",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _fmt_money(x: float) -> str:
    """Format USD: $1,234.56."""
    if x is None:
        return "$0.00"
    return f"${x:,.2f}"


def _fmt_pct(x: float) -> str:
    return f"{x * 100:.2f}%"


class PaperNotifier:
    """
    5-tier Telegram notifier untuk paper trading.

    Usage:
        bot = TelegramBot()  # graceful if not configured
        notifier = PaperNotifier(bot=bot)
        notifier.notify_entry(trade_dict)
    """

    def __init__(self, *, bot: TelegramBot | None = None) -> None:
        self.bot: TelegramBot = bot or TelegramBot()
        self._enabled: bool = self.bot.is_configured

    # --- Tier 1: entry ---
    def notify_entry(self, trade: dict[str, Any]) -> bool:
        """
        Send Tier 1 entry alert. Trade dict shape: PaperJournal.open row.
        """
        if trade is None:
            return False
        symbol = trade.get("symbol", "?")
        direction = (trade.get("direction") or "?").upper()
        entry = float(trade.get("entry_price") or 0)
        # Bug fix: support both short keys (sl/tp1/tp2) and long keys
        # (stop_loss/take_profit_1/take_profit_2) since the trade dict may
        # come from either PaperJournal.open() row or from manual dict.
        sl  = float(trade.get("sl")  or trade.get("stop_loss")      or 0)
        tp1 = float(trade.get("tp1") or trade.get("take_profit_1")  or 0)
        tp2 = float(trade.get("tp2") or trade.get("take_profit_2")  or 0)
        score = int(trade.get("confluence_score") or 0)
        grade = str(trade.get("grade") or "?")
        size_mult = float(trade.get("size_multiplier") or 1.0)
        risk_usd = float(trade.get("risk_usd") or 0)
        trade_id = str(trade.get("trade_id") or "?")
        emoji = _GRADE_EMOJI.get(grade, "•")
        tier_emoji = _TIER_EMOJI[TIER_ENTRY]
        body = (
            f"{tier_emoji} *PAPER ENTRY* — Tier 1\n"
            f"──────────────────────────\n"
            f"{emoji} *{direction} {symbol}*  (score {score}/4, {grade})\n"
            f"💵 Entry : `{entry:,.4f}`\n"
            f"🛑 SL    : `{sl:,.4f}`\n"
            f"🎯 TP1   : `{tp1:,.4f}`  (1R)\n"
            f"🎯 TP2   : `{tp2:,.4f}`  (2R)\n"
            f"📦 Size mult : `{size_mult:.2f}x`  |  "
            f"Risk : `{_fmt_money(risk_usd)}`\n"
            f"🆔 `{trade_id}`\n"
            f"🕒 {_now_iso()}\n"
        )
        return self._send(body, tier=TIER_ENTRY, parse_mode="Markdown")

    # --- Tier 2: exit ---
    def notify_exit(self, trade: dict[str, Any]) -> bool:
        """
        Send Tier 2 exit alert. Trade dict shape: PaperJournal.closed row.
        """
        if trade is None:
            return False
        symbol = trade.get("symbol", "?")
        direction = (trade.get("direction") or "?").upper()
        entry = float(trade.get("entry_price") or 0)
        exit_price = float(trade.get("exit_price") or 0)
        pnl = float(trade.get("pnl_usd") or 0)
        r_mult = float(trade.get("pnl_r_multiple") or 0)
        grade = str(trade.get("grade") or "?")
        reason = str(trade.get("exit_reason") or "?")
        trade_id = str(trade.get("trade_id") or "?")
        grade_emoji = _GRADE_EMOJI.get(grade, "•")
        reason_emoji = _EXIT_REASON_EMOJI.get(reason, "•")
        # Compute pnl pct
        pnl_pct = 0.0
        if entry > 0:
            if direction.upper() == "LONG":
                pnl_pct = (exit_price - entry) / entry
            else:
                pnl_pct = (entry - exit_price) / entry
        sign = "✅" if pnl >= 0 else "❌"
        tier_emoji = _TIER_EMOJI[TIER_EXIT]
        body = (
            f"{tier_emoji} *PAPER EXIT* — Tier 2\n"
            f"──────────────────────────\n"
            f"{grade_emoji} {direction} *{symbol}*  "
            f"({grade}, score {trade.get('confluence_score', '?')}/4)\n"
            f"{reason_emoji} Reason : `{reason}`\n"
            f"💵 Entry : `{entry:,.4f}`  →  Exit `{exit_price:,.4f}`\n"
            f"{sign} P/L   : `{_fmt_money(pnl)}`  "
            f"({_fmt_pct(pnl_pct)})  |  {r_mult:+.2f}R\n"
            f"🆔 `{trade_id}`\n"
            f"🕒 {_now_iso()}\n"
        )
        return self._send(body, tier=TIER_EXIT, parse_mode="Markdown")

    # --- Tier 3: daily digest ---
    def notify_daily_digest(
        self, portfolio_state: dict[str, Any], *, date_str: str | None = None
    ) -> bool:
        """
        Send Tier 3 daily digest. portfolio_state shape: dict with
        keys: balance, equity, initial_balance, daily_pnl, trades_today,
        wins, losses, win_rate, drawdown_pct, open_count.
        """
        date_str = date_str or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        balance = float(portfolio_state.get("balance") or 0)
        equity = float(portfolio_state.get("equity") or balance)
        initial = float(portfolio_state.get("initial_balance") or balance)
        daily_pnl = float(portfolio_state.get("daily_pnl") or 0)
        trades_today = int(portfolio_state.get("trades_today") or 0)
        wins = int(portfolio_state.get("wins") or 0)
        losses = int(portfolio_state.get("losses") or 0)
        win_rate = float(portfolio_state.get("win_rate") or 0)
        drawdown = float(portfolio_state.get("drawdown_pct") or 0)
        open_count = int(portfolio_state.get("open_count") or 0)

        total = wins + losses
        cum_pnl = equity - initial
        cum_pct = (cum_pnl / initial) if initial > 0 else 0.0
        sign = "✅" if daily_pnl >= 0 else "❌"
        tier_emoji = _TIER_EMOJI[TIER_DAILY]
        body = (
            f"{tier_emoji} *DAILY DIGEST* — Tier 3\n"
            f"──────────────────────────\n"
            f"📅 Date  : `{date_str}`\n"
            f"💼 Equity: `{_fmt_money(equity)}`  "
            f"(cum P/L `{_fmt_money(cum_pnl)}`, {_fmt_pct(cum_pct)})\n"
            f"{sign} Day P/L: `{_fmt_money(daily_pnl)}`\n"
            f"📊 Trades today : `{trades_today}`  "
            f"({wins}W / {losses}L, WR {win_rate * 100:.1f}%)\n"
            f"🔓 Open positions: `{open_count}`\n"
            f"📉 Drawdown      : `{_fmt_pct(drawdown)}`\n"
            f"🕒 {_now_iso()}\n"
        )
        return self._send(body, tier=TIER_DAILY, parse_mode="Markdown")

    # --- Tier 4: weekly report ---
    def notify_weekly_report(
        self,
        report_data: dict[str, Any],
        *,
        chart_path: str | None = None,
    ) -> bool:
        """
        Send Tier 4 weekly report. report_data keys: period, total_trades,
        wins, losses, win_rate, profit_factor, total_pnl, max_drawdown_pct,
        avg_r_multiple, top_winners (list), top_losers (list).
        """
        if report_data is None:
            return False
        period = report_data.get("period", "last 7d")
        total = int(report_data.get("total_trades") or 0)
        wins = int(report_data.get("wins") or 0)
        losses = int(report_data.get("losses") or 0)
        win_rate = float(report_data.get("win_rate") or 0)
        pf = float(report_data.get("profit_factor") or 0)
        total_pnl = float(report_data.get("total_pnl") or 0)
        max_dd = float(report_data.get("max_drawdown_pct") or 0)
        avg_r = float(report_data.get("avg_r_multiple") or 0)
        top_w = report_data.get("top_winners") or []
        top_l = report_data.get("top_losers") or []

        sign = "✅" if total_pnl >= 0 else "❌"
        tier_emoji = _TIER_EMOJI[TIER_WEEKLY]
        body_lines = [
            f"{tier_emoji} *WEEKLY REPORT* — Tier 4",
            f"──────────────────────────",
            f"📅 Period: `{period}`",
            f"📊 Trades: `{total}`  ({wins}W / {losses}L, WR {win_rate * 100:.1f}%)",
            f"💰 Profit Factor: `{pf:.2f}`",
            f"{sign} Total P/L  : `{_fmt_money(total_pnl)}`",
            f"📈 Avg R-multiple: `{avg_r:+.2f}R`",
            f"📉 Max Drawdown : `{_fmt_pct(max_dd)}`",
        ]
        if top_w:
            body_lines.append("🏆 Top winners:")
            for t in top_w[:3]:
                body_lines.append(
                    f"   • {t.get('symbol','?')} {(_fmt_money(t.get('pnl_usd', 0)))} "
                    f"({float(t.get('pnl_r_multiple', 0)):+.2f}R)"
                )
        if top_l:
            body_lines.append("💀 Top losers:")
            for t in top_l[:3]:
                body_lines.append(
                    f"   • {t.get('symbol','?')} {(_fmt_money(t.get('pnl_usd', 0)))} "
                    f"({float(t.get('pnl_r_multiple', 0)):+.2f}R)"
                )
        if chart_path:
            body_lines.append(f"📎 Chart: `{chart_path}`")
        body_lines.append(f"🕒 {_now_iso()}")
        body = "\n".join(body_lines)
        return self._send(body, tier=TIER_WEEKLY, parse_mode="Markdown")

    # --- Tier 5: risk breach ---
    def notify_risk_breach(
        self, alert_type: str, details: dict[str, Any] | None = None
    ) -> bool:
        """
        Send Tier 5 risk alert. alert_type in:
          - 'daily_loss_limit'
          - 'drawdown_circuit'
          - 'max_open_positions'
          - 'max_daily_trades'
        """
        details = details or {}
        tier_emoji = _TIER_EMOJI[TIER_RISK]
        body_lines = [
            f"{tier_emoji} *RISK ALERT* — Tier 5",
            f"──────────────────────────",
            f"⚠️ Type : `{alert_type}`",
        ]
        for k, v in details.items():
            if isinstance(v, float):
                if "pct" in k.lower() or "drawdown" in k.lower():
                    body_lines.append(f"   • {k}: `{_fmt_pct(v)}`")
                else:
                    body_lines.append(f"   • {k}: `{v:.4f}`")
            else:
                body_lines.append(f"   • {k}: `{v}`")
        body_lines.append(f"🕒 {_now_iso()}")
        body = "\n".join(body_lines)
        return self._send(body, tier=TIER_RISK, parse_mode="Markdown")

    # --- Internal ---
    def _send(self, text: str, *, tier: int, parse_mode: str | None) -> bool:
        if not self._enabled:
            # Graceful degradation — log to console only.
            logger.info(
                f"[paper-notify:tier{tier}:degraded] (no telegram) "
                f"would send:\n{text}"
            )
            return False
        try:
            ok = self.bot.send_message(text, parse_mode=parse_mode)
            if ok:
                logger.debug(f"[paper-notify:tier{tier}] sent OK")
            return ok
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[paper-notify:tier{tier}] send error: {exc}")
            return False

    # --- Scheduling helpers ---
    @staticmethod
    def should_send_daily_digest(
        *,
        hour_utc: int = 0,
        minute_utc: int = 5,
        last_sent_key: str = "last_daily_digest_ts",
    ) -> bool:
        """
        Return True kalau sekarang >= HH:MM UTC dan belum pernah dikirim
        hari ini. Pure helper (no DB access) — caller tracks state.
        """
        now = datetime.now(timezone.utc)
        if now.hour == hour_utc and now.minute >= minute_utc:
            return True
        return False

    @staticmethod
    def should_send_weekly_report(
        *,
        target_dow: int = 6,  # 0=Mon, 6=Sun
        hour_utc: int = 23,
        minute_utc: int = 59,
    ) -> bool:
        """Return True kalau sekarang Sun >= 23:59 UTC."""
        now = datetime.now(timezone.utc)
        if now.weekday() == target_dow and now.hour >= hour_utc:
            return True
        return False


__all__ = [
    "PaperNotifier",
    "TIER_ENTRY",
    "TIER_EXIT",
    "TIER_DAILY",
    "TIER_WEEKLY",
    "TIER_RISK",
]
