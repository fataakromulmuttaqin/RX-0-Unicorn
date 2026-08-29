"""
RX-0 Unicorn — Phase 6 Paper Trading System.

Simulates real-time trading with NO real money. Validates the confluence
strategy in real-time before greenlighting Phase 7 (live trading).

Public API:
    PaperJournal       — SQLite persistence (paper_trades, paper_daily)
    PaperPortfolio     — virtual balance + open position manager
    PaperTrader        — high-level orchestrator (open / close / monitor)
    PaperNotifier      — 5-tier Telegram notifications (Phase 6 extension)
    generate_report    — text report
    generate_equity_chart — matplotlib PNG chart
    phase7_readiness   — greenlight check
    make_trade_id      — unique trade id helper
    ccxt_price_fetcher — pluggable price source

Usage example:
    with PaperJournal() as j:
        trader = PaperTrader(journal=j)
        trader.portfolio.start()
        trader.open_from_signal(signal_dict, symbol="BTC/USDT")
        trader.monitor_loop(price_fetcher=ccxt_price_fetcher(exchange))
"""

from .journal import (
    ALLOWED_DIRECTIONS,
    ALLOWED_EXIT_REASONS,
    ALLOWED_GRADES,
    ALLOWED_SIGNAL_SOURCES,
    ALLOWED_STATUSES,
    PaperJournal,
    SCHEMA_SQL,
)
from .notifier import (
    PaperNotifier,
    TIER_DAILY,
    TIER_ENTRY,
    TIER_EXIT,
    TIER_RISK,
    TIER_WEEKLY,
)
from .portfolio import PaperPortfolio
from .reporter import (
    build_weekly_summary,
    generate_equity_chart,
    generate_report,
    phase7_readiness,
)
from .trader import PaperTrader, ccxt_price_fetcher, make_trade_id


__all__ = [
    # Core
    "PaperJournal",
    "PaperPortfolio",
    "PaperTrader",
    "PaperNotifier",
    # Reporter
    "generate_report",
    "generate_equity_chart",
    "phase7_readiness",
    "build_weekly_summary",
    # Helpers
    "make_trade_id",
    "ccxt_price_fetcher",
    "SCHEMA_SQL",
    # Enums
    "ALLOWED_DIRECTIONS",
    "ALLOWED_GRADES",
    "ALLOWED_SIGNAL_SOURCES",
    "ALLOWED_STATUSES",
    "ALLOWED_EXIT_REASONS",
    # Notification tiers
    "TIER_ENTRY",
    "TIER_EXIT",
    "TIER_DAILY",
    "TIER_WEEKLY",
    "TIER_RISK",
]
