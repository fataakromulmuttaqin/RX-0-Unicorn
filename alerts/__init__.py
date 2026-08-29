"""
RX-0 Unicorn — Phase 4 Alert System.

Public API:
    alerts.telegram.TelegramBot       — HTTP client for Telegram Bot API
    alerts.formatter.format_signal    — format latest_confluence() dict -> text
    alerts.cooldown.CooldownManager   — SQLite-backed per-pair cooldown

Token & chat id dibaca dari os.environ (di-populate oleh python-dotenv
dari .env di root project). Jika kosong -> graceful degradation: alert
dicetak ke console saja, tidak ada network call, tidak ada crash.
"""

from alerts.cooldown import CooldownManager
from alerts.formatter import format_signal
from alerts.telegram import TelegramBot

__all__ = [
    "TelegramBot",
    "format_signal",
    "CooldownManager",
]
