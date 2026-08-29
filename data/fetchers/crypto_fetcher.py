"""
CCXT fetcher untuk Binance public endpoints.

Tidak butuh API key untuk data historis OHLCV. Pagination ditangani
internal; rate limit di-handle dengan exponential backoff.
"""

from __future__ import annotations

import time
from typing import Iterable

import ccxt
import pandas as pd

from src.config import (
    BINANCE_HOSTNAME,
    DEFAULT_LIMIT,
    FETCHER_BACKOFF_FACTOR,
    FETCHER_BASE_BACKOFF,
    FETCHER_BATCH_SIZE,
    FETCHER_MAX_RETRIES,
    FETCHER_TIMEOUT,
    VALID_TIMEFRAMES,
)
from src.logger import logger


class CryptoFetcher:
    """
    Wrapper untuk CCXT Binance exchange dengan fokus pada:
    - OHLCV historical fetch (public endpoint, no API key needed)
    - Pagination otomatis (Binance max 1000 candles/request)
    - Exponential backoff untuk rate limit
    - Output konsisten: pandas DataFrame
    """

    SUPPORTED_TIMEFRAMES: tuple[str, ...] = VALID_TIMEFRAMES

    def __init__(
        self,
        exchange_id: str = "binance",
        hostname: str | None = BINANCE_HOSTNAME,
    ) -> None:
        self.exchange_id = exchange_id
        self.hostname = hostname
        self.exchange = self._build_exchange(exchange_id, hostname)
        logger.info(
            f"CryptoFetcher initialized: exchange={exchange_id}"
            f"{f', host={hostname}' if hostname else ''}"
        )

    @staticmethod
    def _build_exchange(
        exchange_id: str, hostname: str | None = None
    ) -> ccxt.Exchange:
        """Bangun instance CCXT dengan setting default yang aman."""
        exchange_class = getattr(ccxt, exchange_id)
        config: dict = {
            "enableRateLimit": True,  # CCXT auto-throttle
            "timeout": FETCHER_TIMEOUT,
            "options": {
                "defaultType": "spot",
            },
        }
        if hostname:
            # Beberapa mirror (e.g. data-api.binance.vision) tidak serve
            # endpoint 'exchangeInfo' untuk load_markets(). Kita arahkan
            # semua URL public ke mirror tsb, dan skip load_markets() dengan
            # pre-populate markets secara manual di load_markets().
            config["hostname"] = hostname
            config["urls"] = {
                "api": {
                    "public": f"https://{hostname}/api/v3",
                    "fapiPublic": f"https://{hostname}/api/v3",
                    "dapiPublic": f"https://{hostname}/api/v3",
                }
            }
        exchange = exchange_class(config)
        return exchange

    @staticmethod
    def _build_market_entry(symbol: str) -> dict:
        """
        Buat entri 'market' minimal yang dibutuhkan CCXT untuk spot trading
        tanpa harus load_markets(). Dipakai sebagai fallback kalau mirror
        tidak serve exchangeInfo.
        """
        norm = symbol.strip().upper()
        if "/" in norm:
            base, quote = norm.split("/", 1)
        else:
            # Heuristic
            for q in ("USDT", "USDC", "BUSD", "FDUSD", "DAI", "BTC", "ETH"):
                if norm.endswith(q) and len(norm) > len(q):
                    base, quote = norm[: -len(q)], q
                    break
            else:
                base, quote = norm, "USDT"
        return {
            "id": f"{base}{quote}",
            "symbol": f"{base}/{quote}",
            "base": base,
            "quote": quote,
            "active": True,
            "type": "spot",
            "spot": True,
            "option": False,
            "contract": False,
            "linear": None,
            "inverse": None,
            "future": False,
            "swap": False,
            "margin": False,
            "contractSize": None,
            "limits": {
                "amount": {"min": None, "max": None},
                "price": {"min": None, "max": None},
                "cost": {"min": None, "max": None},
            },
            "precision": {"amount": None, "price": None},
            "info": {},
        }

    def _ensure_market(self, norm_symbol: str) -> None:
        """
        Pastikan market entry ada di exchange.markets tanpa load_markets().
        Berguna untuk mirror yang tidak serve exchangeInfo.
        """
        if (
            self.exchange.markets is not None
            and norm_symbol in self.exchange.markets
        ):
            return
        try:
            # Coba load_markets() sekali; kalau gagal, fallback manual.
            self.exchange.load_markets()
            return
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                f"load_markets() failed ({exc}); "
                f"pre-populating market for {norm_symbol}"
            )
        if self.exchange.markets is None:
            self.exchange.markets = {}
        entry = self._build_market_entry(norm_symbol)
        self.exchange.markets[norm_symbol] = entry
        self.exchange.symbols = list(self.exchange.markets.keys())

    def _normalize_symbol(self, symbol: str) -> str:
        """
        Normalisasi simbol ke format CCXT (e.g. 'BTCUSDT' -> 'BTC/USDT').
        Idempotent untuk simbol yang sudah benar.
        """
        symbol = symbol.strip().upper()
        if "/" in symbol:
            return symbol
        # Heuristik: jika diakhiri USDT/USDC/BUSD/FDUSD/DAI, tambahkan '/'
        for quote in ("USDT", "USDC", "BUSD", "FDUSD", "DAI", "BTC", "ETH"):
            if symbol.endswith(quote) and len(symbol) > len(quote):
                base = symbol[: -len(quote)]
                return f"{base}/{quote}"
        return symbol

    def _validate_timeframe(self, timeframe: str) -> None:
        if timeframe not in self.SUPPORTED_TIMEFRAMES:
            raise ValueError(
                f"Timeframe '{timeframe}' tidak didukung. "
                f"Pilihan: {self.SUPPORTED_TIMEFRAMES}"
            )

    def _sleep_with_backoff(self, attempt: int) -> float:
        """Hitung delay exponential backoff dan sleep."""
        delay = FETCHER_BASE_BACKOFF * (FETCHER_BACKOFF_FACTOR ** attempt)
        # Tambah jitter sedikit supaya tidak menabrak rate limit bareng
        delay *= 1.0 + (0.1 * (attempt % 3))
        logger.debug(f"Backoff attempt={attempt}, sleeping {delay:.2f}s")
        time.sleep(delay)
        return delay

    def _fetch_ohlcv_with_retry(
        self, symbol: str, timeframe: str, since: int | None, limit: int
    ) -> list[list]:
        """
        Single batch fetch dengan retry+backoff.
        Returns raw OHLCV list dari CCXT: [[ts, o, h, l, c, v], ...]
        """
        last_exc: Exception | None = None
        for attempt in range(FETCHER_MAX_RETRIES):
            try:
                data = self.exchange.fetch_ohlcv(
                    symbol=symbol,
                    timeframe=timeframe,
                    since=since,
                    limit=limit,
                )
                if data is None:
                    data = []
                return data
            except ccxt.RateLimitExceeded as exc:
                last_exc = exc
                logger.warning(
                    f"Rate limit hit for {symbol} {timeframe} "
                    f"(attempt {attempt + 1}/{FETCHER_MAX_RETRIES})"
                )
                self._sleep_with_backoff(attempt)
            except ccxt.NetworkError as exc:
                last_exc = exc
                logger.warning(
                    f"Network error for {symbol} {timeframe}: {exc} "
                    f"(attempt {attempt + 1}/{FETCHER_MAX_RETRIES})"
                )
                self._sleep_with_backoff(attempt)
            except ccxt.ExchangeError as exc:
                # Beberapa exchange error lain (bad symbol, dst) tidak bisa di-retry
                logger.error(f"Exchange error for {symbol} {timeframe}: {exc}")
                raise
        logger.error(
            f"Max retries exceeded for {symbol} {timeframe}: {last_exc}"
        )
        raise ccxt.RateLimitExceeded(
            f"Max retries exceeded: {last_exc}"
        ) if last_exc else RuntimeError("Unknown failure")

    def _to_dataframe(self, raw: list[list]) -> pd.DataFrame:
        """Convert raw OHLCV list menjadi DataFrame dengan tipe data rapi."""
        if not raw:
            return pd.DataFrame(
                columns=["timestamp", "open", "high", "low", "close", "volume"]
            )
        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = df["timestamp"].astype("int64")
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = df[col].astype("float64")
        df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
        return df

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1h",
        limit: int = DEFAULT_LIMIT,
    ) -> pd.DataFrame:
        """
        Ambil OHLCV untuk satu simbol, dengan pagination otomatis.

        Args:
            symbol: Trading pair (e.g. 'BTC/USDT' atau 'BTCUSDT').
            timeframe: Salah satu 5m, 15m, 1h, 4h, 1d.
            limit: Jumlah candle yang diinginkan (akan di-paginate kalau > 1000).

        Returns:
            DataFrame dengan kolom: timestamp, open, high, low, close, volume.
        """
        self._validate_timeframe(timeframe)
        norm_symbol = self._normalize_symbol(symbol)
        self._ensure_market(norm_symbol)
        logger.info(
            f"Fetching {norm_symbol} {timeframe} limit={limit} "
            f"(batch_size={FETCHER_BATCH_SIZE})"
        )

        all_rows: list[list] = []
        remaining = limit
        since: int | None = None
        page = 0

        while remaining > 0:
            page += 1
            batch_size = min(remaining, FETCHER_BATCH_SIZE)
            batch = self._fetch_ohlcv_with_retry(
                norm_symbol, timeframe, since, batch_size
            )
            if not batch:
                logger.debug(
                    f"No more data from exchange (page {page}, "
                    f"got empty batch)"
                )
                break
            all_rows.extend(batch)
            remaining -= len(batch)
            # Pagination: geser `since` ke timestamp terakhir + 1 candle
            last_ts = batch[-1][0]
            since = last_ts + 1
            logger.debug(
                f"Page {page}: got {len(batch)} rows, "
                f"remaining={remaining}, since={since}"
            )
            # Kalau batch kurang dari yang diminta, exchange sudah habis
            if len(batch) < batch_size:
                break

        df = self._to_dataframe(all_rows)
        # Potong sesuai limit kalau over-fetch
        if len(df) > limit:
            df = df.tail(limit).reset_index(drop=True)
        logger.success(
            f"Fetched {len(df)} candles for {norm_symbol} {timeframe}"
        )
        return df

    def fetch_multiple(
        self,
        symbols: Iterable[str],
        timeframe: str = "1h",
        limit: int = DEFAULT_LIMIT,
    ) -> dict[str, pd.DataFrame]:
        """
        Ambil OHLCV untuk banyak simbol.

        Args:
            symbols: Iterable simbol trading pair.
            timeframe: Timeframe seragam untuk semua simbol.
            limit: Jumlah candle per simbol.

        Returns:
            Dict {symbol_normalized: DataFrame}. Simbol yang gagal akan berisi
            DataFrame kosong dan di-log sebagai error.
        """
        self._validate_timeframe(timeframe)
        results: dict[str, pd.DataFrame] = {}
        sym_list = list(symbols)
        logger.info(
            f"Fetching {len(sym_list)} symbols @ {timeframe} limit={limit}"
        )
        for sym in sym_list:
            try:
                results[sym] = self.fetch_ohlcv(sym, timeframe, limit)
            except Exception as exc:  # noqa: BLE001
                logger.error(f"Failed to fetch {sym}: {exc}")
                results[sym] = pd.DataFrame(
                    columns=["timestamp", "open", "high", "low", "close", "volume"]
                )
        logger.info(
            f"Batch fetch done: {sum(1 for v in results.values() if not v.empty)}"
            f"/{len(results)} successful"
        )
        return results

    def close(self) -> None:
        """Tutup koneksi exchange (best effort)."""
        try:
            if hasattr(self.exchange, "close"):
                self.exchange.close()
        except Exception:  # noqa: BLE001
            pass
