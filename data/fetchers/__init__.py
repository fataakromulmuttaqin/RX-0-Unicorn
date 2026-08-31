"""
Public fetcher exports for RX-0 Unicorn.

Since the v1.0 pivot to XAU/USD single-symbol focus, YahooFinanceFetcher
is the primary fetcher. CryptoFetcher remains for backward compatibility
(e.g., occasional cross-asset scan, tests, manual BTC/ETH debug runs).
"""

from __future__ import annotations

from .crypto_fetcher import CryptoFetcher, MultiExchangeFetcher
from .yahoo_fetcher import (
    YAHOO_INTERVALS,
    YAHOO_SYMBOL_MAP,
    YahooFinanceFetcher,
)

__all__ = [
    "CryptoFetcher",
    "MultiExchangeFetcher",
    "YahooFinanceFetcher",
    "YAHOO_SYMBOL_MAP",
    "YAHOO_INTERVALS",
]