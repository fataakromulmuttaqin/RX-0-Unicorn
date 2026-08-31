"""
Backtest 1 tahun terakhir dari Binance public API (data-api.binance.vision),
lalu jalankan engine confluence scoring → kumpulkan metrics per-pair
+ aggregate portfolio.

Kenapa 4h (bukan 1d)?
  1d timeframe: confluence_score max=2 (di-skip), zero signals.
  4h timeframe: confluence_score bisa sampai 3-4, ada valid trades.
  1h timeframe: noisier, butuh >2x data per pair (hemat bandwidth pakai 4h).

Kenapa fetch dari Binance langsung?
  1. candles.db lokal cuma punya 200 4h candles (~33 hari) — gak cukup untuk 1y.
  2. data-api.binance.vision: full public API, no geo-block, no API key needed.
  3. End-time pagination: max 1000 bars/request → butuh 2-3 calls per pair
     untuk cover 1y (~2190 bars @ 4h).

Usage:
    python -m backtest.run_yearly                  # all 57 watchlist pairs, 4h 1y
    python -m backtest.run_yearly --top 20        # first 20 from watchlist
    python -m backtest.run_yearly --pairs BTC/USDT,ETH/USDT
    python -m backtest.run_yearly --out /tmp/btest.json
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


# ─── Config ──────────────────────────────────────────────────────────────────

# Default Binance data API (no geo-block, no API key).
BINANCE_DATA_API = "https://data-api.binance.vision"

# Max bars per API call.
MAX_BARS_PER_REQUEST = 1000

# 4h candles per day (24/4 = 6).
BARS_PER_DAY_4H = 6

# Default lookback: 365 days × 6 bars/day = ~2190 bars.
DEFAULT_DAYS_BACK = 365
DEFAULT_BARS = DEFAULT_DAYS_BACK * BARS_PER_DAY_4H  # 2190

# Initial capital & risk per trade (must match engine defaults).
INITIAL_CAPITAL = 10_000.0
RISK_PER_TRADE = 0.02

# min_score for entry — confluence rules say score>=3 (valid). We allow 2
# (lower-confidence) for visibility; users can tighten in the engine.
DEFAULT_MIN_SCORE = 2

# Max trade size in USD per risk budget (kept cap for profit_factor overflow).
PROFIT_FACTOR_CAP = 999.0


# ─── Watchlist loader ───────────────────────────────────────────────────────

def load_watchlist(path: Path = PROJECT_ROOT / "data" / "pairs" / "watchlist.json") -> list[str]:
    if not path.exists():
        return []
    with open(path) as f:
        w = json.load(f)
    pairs: list[str] = []
    for tier in sorted(w.keys()):
        pairs.extend(w[tier])
    return pairs


# ─── Binance fetch (with end-time pagination) ──────────────────────────────

def _klines_request(symbol: str, interval: str, end_ts_ms: int | None = None) -> list[list]:
    """Single Binance klines call. Returns list of klines (12 fields each)."""
    url = f"{BINANCE_DATA_API}/api/v3/klines?symbol={symbol}&interval={interval}&limit={MAX_BARS_PER_REQUEST}"
    if end_ts_ms is not None:
        url += f"&endTime={end_ts_ms - 1}"
    req = urllib.request.Request(url, headers={"User-Agent": ":rx0-unicorn-backtest/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError) as e:
        raise RuntimeError(f"Binance fetch failed for {symbol} @ end_ts={end_ts_ms}: {e}") from e


# ─── v0.9.2: CCXT-based fetch (multi-exchange fallback) ────────────────────

def fetch_via_ccxt(
    symbol_ccxt: str,
    timeframe: str = "4h",
    total_bars: int = DEFAULT_BARS,
    preferred_exchange: str = "binance",
    verbose: bool = False,
) -> tuple[pd.DataFrame, str]:
    """
    Fetch historical OHLCV via CCXT, falling back across exchanges.
    Returns (df, source_exchange_id).

    v0.9.2: this is the new primary fetch path. It uses ccxt's paginated
    fetch_ohlcv() with multi-exchange fallback (binance → bybit → okx →
    gate → kucoin → htx). For some regions only Gate.io is reachable
    due to geo-blocking on Binance/Bybit/OKX.
    """
    from data.fetchers.multi_exchange import fetch_ohlcv_multi_paginated

    ohlcv, source = fetch_ohlcv_multi_paginated(
        symbol_ccxt, timeframe, total_bars, preferred=preferred_exchange, verbose=verbose
    )
    if not ohlcv:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"]), ""
    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = df["timestamp"].astype(int)
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)
    return df, source


# ─── v0.9.2: Yahoo Finance fetcher (forex/commodities — XAUUSD, EURUSD) ───
# v1.0+ pivot: Yahoo Finance becomes the *primary* data source. The legacy
# `fetch_via_yahoo()` helper below is kept as a thin adapter that delegates
# to `YahooFinanceFetcher` in `data/fetchers/yahoo_fetcher.py`. All mapping
# (XAU/USD -> GC=F, EUR/USD -> EURUSD=X, etc.) and interval-cap knowledge
# now lives in that class.

# Re-export for backward compatibility with any caller importing these names.
from data.fetchers.yahoo_fetcher import (
    YAHOO_INTERVALS as _YAHOO_INTERVALS,
    YAHOO_SYMBOL_MAP as _YAHOO_SYMBOL_MAP,
    YahooFinanceFetcher,
)

_YAHOO_SYMBOL_MAP: dict[str, str] = dict(_YAHOO_SYMBOL_MAP)  # type: ignore[assignment]
_YAHOO_INTERVALS: dict[str, dict[str, int]] = dict(_YAHOO_INTERVALS)  # type: ignore[assignment]


def fetch_via_yahoo(
    symbol: str,
    timeframe: str = "1d",
    total_bars: int = 500,
    verbose: bool = False,
) -> tuple[pd.DataFrame, str]:
    """
    Thin wrapper around YahooFinanceFetcher for the yearly backtest path.

    Returns (df, source_yahoo_ticker). Returns (empty_df, "") on any error.

    Note: Yahoo does NOT support 4h natively. For 4h requests we aggregate
    from 1h inside YahooFinanceFetcher. This wrapper treats 4h -> 1d as a
    safe downgrade only when explicitly asked via --timeframe 4h AND
    --source yahoo; the recommended path is --timeframe 1d.
    """
    fetcher = YahooFinanceFetcher()
    try:
        # If user explicitly asked 4h and our fetcher can do it via 1h resample,
        # let it. Otherwise fall back to 1d for the "1d-on-the-fly" legacy path.
        if timeframe == "4h":
            df = fetcher.fetch_ohlcv_paginated(
                symbol, "4h", total_bars=max(int(total_bars), 200)
            )
        else:
            df = fetcher.fetch_ohlcv_paginated(
                symbol, timeframe, total_bars=int(total_bars)
            )
    finally:
        fetcher.close()
    if df.empty:
        return df, ""
    # Map CCXT symbol -> yahoo ticker label for logging.
    from data.fetchers.yahoo_fetcher import YAHOO_SYMBOL_MAP as _MAP
    yahoo_sym = _MAP.get(symbol.strip().upper(), symbol)
    return df, yahoo_sym


def fetch_1y_4h(symbol_usdt: str, total_bars: int = DEFAULT_BARS, polite_sleep: float = 0.08) -> pd.DataFrame:
    """
    Fetch ~total_bars of 4h candles for `symbol_usdt` (e.g. 'BTCUSDT').
    Returns a DataFrame with columns: timestamp, open, high, low, close, volume.
    Sorted ASC by timestamp.
    """
    out: list[list] = []
    end_ts_ms: int | None = None
    while len(out) < total_bars:
        page = _klines_request(symbol_usdt, "4h", end_ts_ms)
        if not page:
            break
        # Binance returns ASC by default; we prepend so we keep oldest-first order
        out = page + out
        end_ts_ms = page[0][0]  # oldest open-time on this page
        if len(page) < MAX_BARS_PER_REQUEST:
            break  # exhausted history
        time.sleep(polite_sleep)  # be polite to public API

    if not out:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    df = pd.DataFrame(
        out,
        columns=[
            "timestamp", "open", "high", "low", "close", "volume",
            "close_time", "quote_vol", "trades", "taker_buy_base",
            "taker_buy_quote", "ignore",
        ],
    )
    df = df[["timestamp", "open", "high", "low", "close", "volume"]].copy()
    df["timestamp"] = df["timestamp"].astype("int64")
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = df[c].astype("float64")
    return df.reset_index(drop=True)


def _run_one_pair_with_trades(
    symbol_usdt: str, df: pd.DataFrame, min_score: int,
    min_grade_override: str | None = None,
) -> tuple[dict, list[dict]]:
    """
    Run a single backtest. Returns (per_pair_payload, list_of_{exit_time, pnl}).

    Args:
        min_grade_override: If set, monkeypatch ENTRY_GRADES in the engine
            to include this grade. Default engine uses ('a_plus', 'valid')
            which excludes 'skip'. For low-volatility assets (XAUUSD 1d),
            set to 'skip' to allow any direction to trigger an entry.
    """
    from backtest.engine import run_backtest as _rb
    from backtest.metrics import calculate_metrics
    import backtest.engine as _engine_mod

    symbol_ccxt = (
        symbol_usdt.replace("USDT", "/USDT")
        if not symbol_usdt.endswith("/USDT") else symbol_usdt
    )

    # Monkeypatch ENTRY_GRADES for low-volatility assets (e.g. XAUUSD 1d)
    original_grades = _engine_mod.ENTRY_GRADES
    if min_grade_override and min_grade_override not in original_grades:
        _engine_mod.ENTRY_GRADES = original_grades + (min_grade_override,)

    try:
        res = _rb(
            df,
            symbol=symbol_ccxt,
            timeframe="4h",
            skip_warmup_bars=50,
            min_score=min_score,
        )
    finally:
        # Always restore original
        _engine_mod.ENTRY_GRADES = original_grades

    trade_dicts = [t.to_dict() for t in res.trades]
    m = calculate_metrics(
        trade_dicts,
        initial_capital=INITIAL_CAPITAL,
        risk_per_trade=RISK_PER_TRADE,
    )

    per_pair_payload = {
        "symbol": symbol_ccxt,
        "bars_processed": res.bars_processed,
        "skipped_no_direction": res.skipped_no_direction,
        "skipped_no_risk": res.skipped_no_risk,
        "total_trades": m["total_trades"],
        "wins": m["wins"],
        "losses": m["losses"],
        "win_rate": round(m["win_rate"], 4),
        "profit_factor": (
            PROFIT_FACTOR_CAP if m["profit_factor"] >= PROFIT_FACTOR_CAP
            else round(m["profit_factor"], 3)
        ),
        "max_drawdown_pct": round(m["max_drawdown_pct"], 2),
        "sharpe_ratio": round(m["sharpe_ratio"], 2),
        "avg_r_multiple": round(m["avg_r_multiple"], 3),
        "expectancy": round(m["expectancy"], 2),
        "total_pnl": round(m["total_pnl"], 2),
        "equity_final": round(m["equity_final"], 2),
        "largest_win": round(m["largest_win"], 2),
        "largest_loss": round(m["largest_loss"], 2),
        "equity_curve": m["equity_curve"],
    }

    trade_list = [
        {"exit_time": int(t.exit_time), "pnl": float(t.pnl)}
        for t in res.trades
    ]
    return per_pair_payload, trade_list


# ─── Portfolio aggregation ───────────────────────────────────────────────────

def aggregate_portfolio(per_pair: list[dict]) -> dict:
    """
    Sum PnL across pairs to build an aggregate portfolio equity curve.
    We assume equal-weight per-pair (each starts with $10k); total = N × $10k.
    This is illustrative, not a true combined backtest.
    """
    # Aggregate per-pair PnL by index.
    n_pairs = len([p for p in per_pair if p["total_trades"] > 0])
    if n_pairs == 0:
        return {
            "total_trades": 0, "wins": 0, "losses": 0,
            "win_rate": 0.0, "profit_factor": 0.0,
            "max_drawdown_pct": 0.0, "sharpe_ratio": 0.0,
            "avg_r_multiple": 0.0, "expectancy": 0.0,
            "total_pnl": 0.0, "equity_final": 0.0,
        }

    total_trades = sum(p["total_trades"] for p in per_pair)
    wins = sum(p["wins"] for p in per_pair)
    losses = sum(p["losses"] for p in per_pair)
    total_pnl = sum(p["total_pnl"] for p in per_pair)
    equity_final = INITIAL_CAPITAL * n_pairs + total_pnl

    win_rate = wins / total_trades if total_trades else 0.0

    # Combine all per-pair equity curves by interleaving trades across pairs
    # in entry-time order (we have exit_time in trades; use that for ordering).
    # Simpler approach: sum per-pair cumulative-PnL (from each pair's equity_curve)
    # at each trade index — gives a synthetic portfolio equity curve.
    # That gives a curve with `n_pairs` segments stitched together. For the
    # dashboard chart we just want the *cumulative* portfolio PnL growth,
    # which we approximate by summing equity curves in trade order.
    # Here we'll just give the aggregate by-interleaving using per-trade data.
    # For simplicity, we keep per-pair equity_curve but ALSO add a flat
    # aggregate cumulative pnl curve (sum of per-pair curves stitched).
    # For dashboard readability we generate an "aggregate_equity_curve" that
    # is the trade-by-trade cumulative PnL across all pairs in exit_time order.
    return _aggregate_with_trade_interleave(per_pair)


def _aggregate_with_trade_interleave(per_pair: list[dict]) -> dict:
    """
    Build portfolio metrics by interleaving trades from all pairs in
    exit-time order, then computing equity curve + drawdown + Sharpe
    from the combined trade list.

    This is a meaningful aggregate — it shows what an equal-weight portfolio
    of all pairs would look like with trade-by-trade PnL summed.
    """
    import math
    import numpy as np

    all_trades: list[tuple[int, float]] = []  # (exit_time_ms, pnl)
    for p in per_pair:
        for t in p.get("_trade_list", []):
            all_trades.append((int(t["exit_time"]), float(t["pnl"])))

    if not all_trades:
        return {
            "total_trades": 0, "wins": 0, "losses": 0,
            "win_rate": 0.0, "profit_factor": 0.0,
            "max_drawdown_pct": 0.0, "sharpe_ratio": 0.0,
            "avg_r_multiple": 0.0, "expectancy": 0.0,
            "total_pnl": 0.0, "equity_final": 0.0,
            "aggregate_equity_curve": [],
            "aggregate_pnl_curve": [],
        }

    # Sort by exit_time.
    all_trades.sort(key=lambda x: x[0])
    pnls = np.array([p for _, p in all_trades], dtype=np.float64)
    n = len(pnls)

    wins_arr = pnls > 0
    losses_arr = pnls < 0
    wins = int(wins_arr.sum())
    losses = int(losses_arr.sum())
    win_rate = wins / n
    total_pnl = float(pnls.sum())
    cum_pnl = np.cumsum(pnls)

    gross_profit = float(pnls[wins_arr].sum()) if wins else 0.0
    gross_loss = float(pnls[losses_arr].sum()) if losses else 0.0
    profit_factor = (
        PROFIT_FACTOR_CAP if gross_loss == 0
        else gross_profit / abs(gross_loss)
    )
    if math.isnan(profit_factor) or math.isinf(profit_factor):
        profit_factor = PROFIT_FACTOR_CAP

    avg_win = float(pnls[wins_arr].mean()) if wins else 0.0
    avg_loss = float(abs(pnls[losses_arr].mean())) if losses else 0.0
    expectancy = (win_rate * avg_win) - ((1.0 - win_rate) * avg_loss)
    avg_r = (total_pnl / n) / (INITIAL_CAPITAL * RISK_PER_TRADE) if n else 0.0

    # Max drawdown.
    equity_series = np.concatenate(([float(INITIAL_CAPITAL)], INITIAL_CAPITAL + cum_pnl))
    running_peak = np.maximum.accumulate(equity_series)
    drawdown = (equity_series - running_peak) / running_peak
    max_dd_pct = float(abs(drawdown.min()) * 100.0)

    # Sharpe per-trade (rf=0).
    if n < 2:
        sharpe = 0.0
    else:
        std = float(pnls.std(ddof=1))
        sharpe = float(pnls.mean()) / std if std > 0 else 0.0

    n_pairs = len([p for p in per_pair if p["total_trades"] > 0])
    equity_final = INITIAL_CAPITAL * n_pairs + total_pnl

    return {
        "total_trades": n,
        "wins": wins,
        "losses": losses,
        "win_rate": round(win_rate, 4),
        "profit_factor": (
            PROFIT_FACTOR_CAP if profit_factor >= PROFIT_FACTOR_CAP
            else round(profit_factor, 3)
        ),
        "max_drawdown_pct": round(max_dd_pct, 2),
        "sharpe_ratio": round(sharpe, 2),
        "avg_r_multiple": round(avg_r, 3),
        "expectancy": round(expectancy, 2),
        "total_pnl": round(total_pnl, 2),
        "equity_final": round(equity_final, 2),
        "aggregate_equity_curve": [float(x) for x in cum_pnl],
    }


# ─── Main loop ──────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Backtest 1y for XAU/USD via Yahoo Finance (default) or CCXT/Binance (legacy)")
    parser.add_argument("--pairs", default="XAU/USD", help="Comma-separated list (overrides watchlist; default: 'XAU/USD')")
    parser.add_argument("--top", type=int, default=0, help="Limit to first N pairs from watchlist")
    parser.add_argument("--timeframe", default="1d", help="Candle timeframe (default: 1d for XAU/USD)")
    parser.add_argument("--days-back", type=int, default=DEFAULT_DAYS_BACK)
    parser.add_argument("--min-score", type=int, default=DEFAULT_MIN_SCORE)
    parser.add_argument("--out", help="Output JSON path (default: ./backtest.json)")
    parser.add_argument("--max-bars", type=int, default=DEFAULT_BARS)
    parser.add_argument(
        "--source", default="yahoo", choices=["ccxt", "binance", "yahoo"],
        help="Fetch source. Default v1.0: 'yahoo' (XAU/USD via GC=F). "
             "'ccxt'/'binance' retained for legacy crypto pairs."
    )
    parser.add_argument(
        "--exchange", default="binance",
        help="Preferred exchange for ccxt fetch (binance, bybit, okx, gate, kucoin, htx)"
    )
    parser.add_argument(
        "--min-grade-override", default=None, choices=["a_plus", "valid", "skip"],
        help="Override minimum confluence grade for this run. For low-volatility "
             "forex (XAUUSD 1d) you may want to set 'skip' to enter on any direction. "
             "Default: 'valid' (engine default)."
    )
    args = parser.parse_args()

    # Resolve pair list.
    if args.pairs:
        pairs = [p.strip() for p in args.pairs.split(",") if p.strip()]
    else:
        pairs = load_watchlist()
        if args.top and args.top > 0:
            pairs = pairs[:args.top]
    if not pairs:
        print("No pairs to backtest", file=sys.stderr)
        return 1

    # Keep pairs in 'BTC/USDT' format (ccxt-native). For Binance source
    # we convert to 'BTCUSDT' on the fly inside fetch_1y_4h.
    print(f"Backtest 1y @ {args.timeframe} for {len(pairs)} pairs:")
    for p in pairs:
        print(f"  - {p}")

    total_bars = int(args.days_back * 24 / max(1, _tf_to_hours(args.timeframe)))
    src_label = {
        "ccxt": f"CCXT (preferred: {args.exchange})",
        "binance": f"binance ({BINANCE_DATA_API})",
        "yahoo": "Yahoo Finance (forex/commodities)",
    }.get(args.source, args.source)
    print(f"\nFetching {total_bars} bars per pair from {src_label}...")
    overall_start = time.time()

    per_pair_results: list[dict] = []
    fetch_errors: list[dict] = []
    for i, sym_ccxt in enumerate(pairs, 1):
        t0 = time.time()
        source_used = ""
        try:
            if args.source == "ccxt":
                df, source_used = fetch_via_ccxt(
                    sym_ccxt, args.timeframe, total_bars=total_bars,
                    preferred_exchange=args.exchange, verbose=False
                )
            elif args.source == "yahoo":
                # Yahoo doesn't support 4h — silently substitute 1d if requested
                yahoo_tf = "1d" if args.timeframe in ("4h", "1d") else args.timeframe
                df, source_used = fetch_via_yahoo(
                    sym_ccxt, yahoo_tf, total_bars=max(total_bars, 500),
                    verbose=False
                )
            else:
                # Binance direct via data-api.binance.vision
                binance_sym = sym_ccxt.replace("/", "")
                df = fetch_1y_4h(binance_sym, total_bars=total_bars)
                source_used = "binance-direct"
        except Exception as e:
            fetch_errors.append({"symbol": sym_ccxt, "error": str(e)})
            print(f"  [{i}/{len(pairs)}] {sym_ccxt:10s} FETCH ERROR: {e}")
            continue
        if df.empty or len(df) < 100:
            fetch_errors.append({"symbol": sym_ccxt, "error": f"insufficient data: {len(df)} bars"})
            print(f"  [{i}/{len(pairs)}] {sym_ccxt:10s} insufficient data ({len(df)} bars)")
            continue

        try:
            res, trade_list = _run_one_pair_with_trades(
                sym_ccxt, df, min_score=args.min_score,
                min_grade_override=args.min_grade_override,
            )
        except Exception as e:
            fetch_errors.append({"symbol": sym_ccxt, "error": f"backtest error: {e}"})
            print(f"  [{i}/{len(pairs)}] {sym_ccxt:10s} BACKTEST ERROR: {e}")
            continue

        res["_trade_list"] = trade_list
        res["_data_source"] = source_used  # tag which exchange provided candles
        per_pair_results.append(res)
        # Compact per-pair log.
        print(f"  [{i}/{len(pairs)}] {sym_ccxt:10s} {len(df)} bars ({source_used:10s}), "
              f"{res['total_trades']:3d} trades, WR={res['win_rate']*100:5.1f}%, "
              f"PF={res['profit_factor']:6.2f}, PnL=${res['total_pnl']:+7.0f}, "
              f"({time.time()-t0:.1f}s)")

    print(f"\nAll pairs processed in {time.time()-overall_start:.1f}s")

    # Aggregate FIRST (still has _trade_list on each entry).
    print("Aggregating portfolio across pairs...")
    agg = _aggregate_with_trade_interleave(per_pair_results)

    # Now strip the private helper field for the on-disk payload.
    for r in per_pair_results:
        r.pop("_trade_list", None)

    # Build final payload.
    payload = {
        "generated_at": int(time.time() * 1000),  # epoch ms
        "config": {
            "timeframe": args.timeframe,
            "days_back": args.days_back,
            "min_score": args.min_score,
            "initial_capital": INITIAL_CAPITAL,
            "risk_per_trade": RISK_PER_TRADE,
            "pair_count": len(per_pair_results),
        },
        "aggregate": agg,
        "per_pair": per_pair_results,
        "errors": fetch_errors,
    }

    # ISO timestamp for human readability (epoch ISO).
    import datetime
    payload["generated_at_iso"] = datetime.datetime.fromtimestamp(
        payload["generated_at"] / 1000, tz=datetime.timezone.utc
    ).isoformat()

    out_path = Path(args.out) if args.out else (PROJECT_ROOT / "backtest.json")
    with open(out_path, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"\nWrote {out_path} ({out_path.stat().st_size:,} bytes)")

    # Print summary.
    print(f"\n{'='*60}")
    print(f"PORTFOLIO SUMMARY (1y, {args.timeframe}, equal-weight)")
    print(f"{'='*60}")
    a = payload["aggregate"]
    print(f"  Pairs traded:   {len(per_pair_results)}")
    print(f"  Total trades:   {a['total_trades']}")
    print(f"  Wins / Losses:   {a['wins']} / {a['losses']}")
    print(f"  Win Rate:        {a['win_rate']*100:.1f}%")
    print(f"  Profit Factor:   {a['profit_factor']:.2f}")
    print(f"  Sharpe:          {a['sharpe_ratio']:.2f}")
    print(f"  Max DD:          {a['max_drawdown_pct']:.2f}%")
    print(f"  Avg R:           {a['avg_r_multiple']:.2f}R")
    print(f"  Total PnL:       ${a['total_pnl']:+.2f}")
    print(f"  Equity Final:    ${a['equity_final']:,.2f}")

    return 0 if not fetch_errors else 2


def _tf_to_hours(tf: str) -> int:
    """Convert timeframe string to hours-per-bar."""
    tf = tf.lower()
    if tf.endswith("m"):
        return max(1, int(tf[:-1]) // 60 or 1)
    if tf.endswith("h"):
        return int(tf[:-1])
    if tf.endswith("d"):
        return int(tf[:-1]) * 24
    return 1


if __name__ == "__main__":
    sys.exit(main())