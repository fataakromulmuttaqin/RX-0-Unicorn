"""
RX-0 Unicorn — Phase 1 + Phase 2 + Phase 3 + Phase 4 + Phase 5 CLI entry point.

Subcommands:
    fetch        Tarik candle untuk watchlist dan simpan ke SQLite.
    status       Tampilkan statistik database.
    cleanup      Hapus candle lama sesuai retention policy.
    scan         Jalankan Confluence Scorer (Phase 3) di data tersimpan.
    daemon       Loop forever: scan + kirim top-N alert ke Telegram (Phase 4).
    test-alert   Kirim sample alert (placeholder data) untuk verifikasi bot.
    cooldown     Manage alert cooldown table (list/clear/clear-all).
    backtest     Walk-forward backtest + 6 metrics wajib STRATEGY.md (Phase 5).
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from pathlib import Path
from typing import Iterable

# Pastikan root project ada di sys.path agar import src.* & data.* konsisten
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from alerts import CooldownManager, TelegramBot, format_signal
from confluence import GRADE_A_PLUS, GRADE_SKIP, GRADE_VALID, latest_confluence
from data.fetchers.crypto_fetcher import CryptoFetcher
from data.storage.candle_db import CandleDB
from backtest.data_loader import ensure_data
from backtest.engine import run_backtest
from backtest.metrics import calculate_metrics
from backtest.report import format_report, to_equity_curve_chart, to_json
from paper import (
    PaperJournal,
    PaperNotifier,
    PaperTrader,
    build_weekly_summary,
    generate_equity_chart,
    generate_report as generate_paper_report,
)
from src.config import (
    ALERT_COOLDOWN_MINUTES,
    ALERT_TOP_N,
    BACKTEST_DEFAULT_DAYS,
    BACKTEST_INITIAL_CAPITAL,
    BACKTEST_MAX_BARS_HOLD,
    BACKTEST_MIN_SAMPLE_SIZE,
    BACKTEST_OUTPUT_DIR,
    BACKTEST_RISK_PER_TRADE,
    CONFLUENCE_MIN_VALID,
    DEFAULT_LIMIT,
    DEFAULT_TIMEFRAME,
    PAPER_INITIAL_BALANCE,
    PAPER_MONITOR_INTERVAL_SECONDS,
    PAPER_REPORT_DEFAULT_DAYS,
    SCAN_INTERVAL_SECONDS,
    VALID_TIMEFRAMES,
    WATCHLIST_PATH,
    WATCHLIST_TIERS,
)
from src.logger import logger

# Minimal bar count supaya semua indikator (terutama RSI/ADX & WaveTrend)
# punya cukup warm-up data untuk hasil yang stabil.
SCAN_MIN_CANDLES: int = 120


def load_watchlist(path: Path = WATCHLIST_PATH) -> dict[str, list[str]]:
    """Load watchlist JSON. Raise kalau file rusak."""
    if not path.exists():
        raise FileNotFoundError(
            f"Watchlist tidak ditemukan di {path}. "
            f"Pastikan data/pairs/watchlist.json ada."
        )
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Watchlist harus object/dict, dapat: {type(data)}")
    return data


def resolve_symbols(
    watchlist: dict[str, list[str]],
    tier: str | None,
) -> list[str]:
    """
    Pilih simbol dari watchlist. Jika tier=None, gabungkan semua.
    """
    if tier is None:
        out: list[str] = []
        for t, syms in watchlist.items():
            if not isinstance(syms, list):
                logger.warning(f"Tier {t} bukan list, di-skip")
                continue
            out.extend(syms)
        # Dedup sambil pertahankan urutan
        seen = set()
        deduped = []
        for s in out:
            if s not in seen:
                seen.add(s)
                deduped.append(s)
        return deduped

    if tier not in watchlist:
        raise ValueError(
            f"Tier '{tier}' tidak ada. Pilihan: {list(watchlist.keys())}"
        )
    syms = watchlist[tier]
    if not isinstance(syms, list):
        raise ValueError(f"Tier {tier} harus berisi list")
    return list(syms)


def _format_stats(stats: dict) -> str:
    """Format statistik DB jadi string rapi untuk dicetak."""
    lines = [
        "=" * 60,
        "RX-0 Unicorn — Database Status",
        "=" * 60,
        f"Path             : {stats.get('db_path')}",
        f"Schema version   : {stats.get('schema_version')}",
        f"Size             : {stats.get('size_bytes', 0):,} bytes",
        f"Total rows       : {stats.get('total_rows', 0):,}",
        f"Distinct pairs   : {stats.get('distinct_pairs', 0):,}",
        f"Distinct TFs     : {stats.get('distinct_timeframes', 0):,}",
        f"First timestamp  : {stats.get('first_timestamp_iso') or 'N/A'}",
        f"Last  timestamp  : {stats.get('last_timestamp_iso') or 'N/A'}",
        "-" * 60,
        "Rows per timeframe:",
    ]
    per_tf = stats.get("rows_per_timeframe") or {}
    if per_tf:
        for tf in sorted(per_tf.keys()):
            lines.append(f"  {tf:>4} : {per_tf[tf]:>8,} rows")
    else:
        lines.append("  (no data yet)")
    lines.append("=" * 60)
    return "\n".join(lines)


# --- Subcommand handlers ---
def cmd_status(_args: argparse.Namespace) -> int:
    """Tampilkan statistik database."""
    with CandleDB() as db:
        stats = db.get_stats()
    print(_format_stats(stats))
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    """
    Tarik candle untuk watchlist dan simpan ke DB.

    Default: seluruh watchlist, timeframe dari --timeframe, limit dari --limit.
    """
    watchlist = load_watchlist()
    symbols = resolve_symbols(watchlist, args.tier)
    if not symbols:
        logger.error("Watchlist kosong setelah filter")
        return 1
    logger.info(
        f"Fetch plan: {len(symbols)} symbols, "
        f"tf={args.timeframe}, limit={args.limit}"
        + (f", tier={args.tier}" if args.tier else ", all tiers")
    )

    fetcher = CryptoFetcher(exchange_id="binance")
    start = time.time()
    try:
        results = fetcher.fetch_multiple(
            symbols=symbols,
            timeframe=args.timeframe,
            limit=args.limit,
        )
    finally:
        fetcher.close()

    inserted_total = 0
    failures: list[str] = []
    with CandleDB() as db:
        for sym, df in results.items():
            if df is None or df.empty:
                failures.append(sym)
                continue
            try:
                inserted_total += db.insert_candles(
                    df=df, pair=sym, timeframe=args.timeframe
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(f"DB insert failed for {sym}: {exc}")
                failures.append(sym)

    elapsed = time.time() - start
    logger.success(
        f"Done: {len(symbols) - len(failures)}/{len(symbols)} ok, "
        f"inserted={inserted_total} new rows, "
        f"failed={len(failures)}, elapsed={elapsed:.1f}s"
    )
    if failures:
        logger.warning(f"Failed symbols: {failures}")
    return 0


def cmd_cleanup(_args: argparse.Namespace) -> int:
    """Hapus candle lama sesuai retention policy."""
    with CandleDB() as db:
        deleted = db.cleanup_old()
    logger.success(f"Cleanup done: {deleted} rows deleted")
    return 0


def _scan_symbol(db: CandleDB, symbol: str, timeframe: str) -> dict | None:
    """
    Jalankan Confluence Scorer (Phase 3) untuk satu simbol dan kembalikan
    ringkasan bar terakhir. Return None kalau data belum cukup.
    """
    df = db.get_candles(pair=symbol, timeframe=timeframe)
    if df is None or len(df) < SCAN_MIN_CANDLES:
        return None

    try:
        summary = latest_confluence(df)
    except ValueError as exc:
        logger.warning(f"Scan skip {symbol}: {exc}")
        return None

    summary["symbol"] = symbol
    return summary


def _format_scan_results(results: list[dict], timeframe: str) -> str:
    """Format hasil scan jadi tabel teks, diurutkan dari confluence tertinggi."""
    results_sorted = sorted(results, key=lambda r: r["score"], reverse=True)
    lines = [
        "=" * 96,
        f"RX-0 Unicorn — Confluence Scan (timeframe={timeframe})",
        "=" * 96,
        f"{'Symbol':<12}{'Close':>12}  {'Grade':<6}{'Dir':<6}{'Score':<6}"
        f"{'SL':>12}{'TP1':>12}{'TP2':>12}  Signals",
        "-" * 96,
    ]
    for r in results_sorted:
        sig_str = " ".join(
            f"{name[:3]}:{val:+d}" for name, val in r["signals"].items()
        )
        direction = r["direction"] or "-"
        sl = f"{r['stop_loss']:.4f}" if r["stop_loss"] is not None else "-"
        tp1 = f"{r['take_profit_1']:.4f}" if r["take_profit_1"] is not None else "-"
        tp2 = f"{r['take_profit_2']:.4f}" if r["take_profit_2"] is not None else "-"
        lines.append(
            f"{r['symbol']:<12}{r['close']:>12.4f}  {r['grade']:<6}{direction:<6}"
            f"{r['score']}/4   {sl:>12}{tp1:>12}{tp2:>12}  {sig_str}"
        )
    if not results_sorted:
        lines.append("(tidak ada simbol dengan data cukup — jalankan 'fetch' dulu)")
    lines.append("=" * 96)
    return "\n".join(lines)


def cmd_scan(args: argparse.Namespace) -> int:
    """
    Scan watchlist (atau satu simbol) dengan Confluence Scorer (Phase 3) dan
    tampilkan setup (grade/score/SL/TP) per simbol berdasarkan bar terakhir.
    """
    if args.symbol:
        symbols = [args.symbol.strip().upper()]
    else:
        watchlist = load_watchlist()
        symbols = resolve_symbols(watchlist, args.tier)

    if not symbols:
        logger.error("Tidak ada simbol untuk di-scan")
        return 1

    results: list[dict] = []
    with CandleDB() as db:
        for sym in symbols:
            res = _scan_symbol(db, sym, args.timeframe)
            if res is not None:
                results.append(res)

    print(_format_scan_results(results, args.timeframe))
    if args.min_score is not None:
        hits = [r for r in results if r["score"] >= args.min_score]
        if hits:
            names = ", ".join(
                f"{r['symbol']} ({r['grade']}, {r['direction']})" for r in hits
            )
            logger.success(f"Confluence >= {args.min_score}: {names}")
        else:
            logger.info(f"Tidak ada simbol dengan confluence >= {args.min_score}")
    return 0


# --- Subcommand handlers (Phase 4) ----------------------------------------
def _sample_confluence_result() -> dict:
    """
    Placeholder confluence result untuk test-alert. Tidak bergantung pada
    data pasar — biar user bisa verifikasi bot config sebelum ada data.
    """
    return {
        "close": 62450.0,
        "regime": "trending",
        "direction": "long",
        "score": 4,
        "grade": GRADE_A_PLUS,
        "size_multiplier": 1.5,
        "entry_price": 62450.0,
        "stop_loss": 62180.0,
        "take_profit_1": 62990.0,
        "take_profit_2": 63530.0,
        "risk_reward": 2.0,
        "signals": {
            "luminance": 1,
            "rsi_regime": 1,
            "structure": 1,
            "wavetrend": 1,
        },
    }


def cmd_test_alert(_args: argparse.Namespace) -> int:
    """
    Kirim sample alert ke Telegram (atau console kalau token kosong).
    Berguna untuk verify bot config sebelum run daemon.
    """
    sample = _sample_confluence_result()
    text = format_signal(
        sample,
        pair="BTC/USDT",
        timeframe="1H",
    )
    if text is None:
        logger.error("format_signal returned None (unexpected for A+ sample)")
        return 1

    # Tampilkan sample ke console dulu (supaya user lihat kalau degraded)
    print("─" * 60)
    print("SAMPLE ALERT (A+ setup, placeholder data):")
    print("─" * 60)
    print(text)
    print("─" * 60)

    bot = TelegramBot()
    try:
        ok = bot.send_message(text)
    finally:
        bot.close()

    if ok:
        logger.success("Telegram send OK — check your chat!")
        return 0
    if not bot.is_configured:
        logger.info(
            "Bot not configured (no TELEGRAM_BOT_TOKEN/CHAT_ID). "
            "Sample alert printed to console above. "
            "Set token di .env untuk kirim real."
        )
        return 0
    logger.error("Telegram send failed — lihat log di atas")
    return 1


def cmd_cooldown(args: argparse.Namespace) -> int:
    """
    Manage alert_cooldown table. Default: tampilkan isi. Opsi --clear
    (dengan optional pair) hapus entry.
    """
    with CooldownManager(cooldown_minutes=ALERT_COOLDOWN_MINUTES) as cd:
        if args.clear_all or args.clear:
            pair = args.clear if args.clear else None
            deleted = cd.clear(pair)
            if pair:
                logger.success(f"Cooldown cleared untuk {pair} ({deleted} row)")
            else:
                logger.success(f"Cooldown cleared semua ({deleted} rows)")
            return 0

        # Default action: list
        pairs = cd.all_pairs()
        if not pairs:
            print("(alert_cooldown table kosong)")
            return 0
        print("Pair                          Last alert (UTC)")
        print("─" * 60)
        for p, ts in sorted(pairs.items(), key=lambda x: -x[1]):
            from datetime import datetime, timezone
            iso = datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            print(f"{p:<30} {iso}")
        print("─" * 60)
        print(f"Total: {len(pairs)} entries (cooldown={cd.cooldown_minutes}m)")
        return 0


# --- Subcommand handler (Phase 5) -------------------------------------------
def cmd_backtest(args: argparse.Namespace) -> int:
    """
    Jalankan backtest walk-forward untuk satu simbol/timeframe.

    Pipeline:
        1. ensure_data() -> DataFrame (DB-first, fallback CCXT)
        2. run_backtest() -> BacktestResult (list Trade)
        3. calculate_metrics() -> 6 metrics + turunan
        4. format_report() -> print
        5. Optional: simpan JSON / render equity chart
    """
    symbol = args.symbol.strip().upper()
    timeframe = args.timeframe
    days = int(args.days)
    initial_capital = float(args.initial_capital)
    risk_per_trade = float(args.risk_per_trade)
    max_bars_hold = int(args.max_bars_hold)

    if days < BACKTEST_MIN_SAMPLE_SIZE:
        logger.warning(
            f"Sample size {days} hari < minimum {BACKTEST_MIN_SAMPLE_SIZE}. "
            f"Hasil backtest mungkin tidak signifikan secara statistik."
        )

    logger.info(
        f"Backtest plan: symbol={symbol}, tf={timeframe}, days={days}, "
        f"capital=${initial_capital:,.2f}, risk={risk_per_trade * 100:.2f}%, "
        f"max_bars_hold={max_bars_hold}"
    )

    # 1. Data
    try:
        df = ensure_data(symbol=symbol, timeframe=timeframe, days_back=days)
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"Data load failed: {exc}")
        return 1
    if df is None or df.empty:
        logger.error(
            f"Tidak ada data untuk {symbol} {timeframe} — "
            f"coba 'python main.py fetch --symbol {symbol} --timeframe {timeframe}' dulu"
        )
        return 1

    # 2. Run engine
    try:
        result = run_backtest(
            df=df,
            symbol=symbol,
            timeframe=timeframe,
            initial_capital=initial_capital,
            risk_per_trade=risk_per_trade,
            max_bars_hold=max_bars_hold,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"Backtest engine error: {exc}")
        return 1

    # 3. Metrics
    trade_dicts = [t.to_dict() for t in result.trades]
    metrics = calculate_metrics(
        trade_dicts,
        initial_capital=initial_capital,
        risk_per_trade=risk_per_trade,
    )

    # 4. Print report
    period = (result.start_ts, result.end_ts)
    report_text = format_report(
        symbol=symbol,
        timeframe=timeframe,
        metrics=metrics,
        trades=trade_dicts,
        period=period,
        initial_capital=initial_capital,
        risk_per_trade=risk_per_trade,
    )
    print(report_text)

    logger.info(
        f"Backtest done: {len(result.trades)} trades, "
        f"skipped_no_direction={result.skipped_no_direction}, "
        f"skipped_no_risk={result.skipped_no_risk}, "
        f"bars={result.bars_processed}"
    )

    # 5. Optional output files
    metadata = {
        "symbol": symbol,
        "timeframe": timeframe,
        "days": days,
        "initial_capital": initial_capital,
        "risk_per_trade": risk_per_trade,
        "max_bars_hold": max_bars_hold,
        "skipped_no_direction": result.skipped_no_direction,
        "skipped_no_risk": result.skipped_no_risk,
        "bars_processed": result.bars_processed,
        "start_ts": result.start_ts,
        "end_ts": result.end_ts,
    }

    if args.output:
        to_json(metrics, args.output, metadata=metadata, trades=trade_dicts)

    if args.chart:
        chart_title = f"RX-0 Unicorn — {symbol} {timeframe} ({days}d) Equity Curve"
        to_equity_curve_chart(
            metrics,
            args.chart,
            title=chart_title,
            initial_capital=initial_capital,
        )

    return 0


def _run_one_scan_cycle(
    timeframe: str,
    top_n: int,
    cooldown: CooldownManager,
    bot: TelegramBot,
) -> tuple[int, int]:
    """
    Jalankan 1 siklus scan: load watchlist, hitung confluence semua pair,
    ambil top-N valid (A+/valid), filter by cooldown, kirim. Return
    tuple (sent_count, skipped_cooldown).
    """
    watchlist = load_watchlist()
    symbols = resolve_symbols(watchlist, tier=None)
    if not symbols:
        logger.error("Watchlist kosong")
        return (0, 0)

    results: list[dict] = []
    with CandleDB() as db:
        for sym in symbols:
            res = _scan_symbol(db, sym, timeframe)
            if res is None:
                continue
            sym_res = dict(res)
            sym_res["symbol"] = sym
            # Hanya ambil yang punya direction valid (skip auto-drop)
            if sym_res.get("direction") in (None, "", "None"):
                continue
            results.append(sym_res)

    # Sort: score desc, lalu grade A+ diprioritaskan
    grade_rank = {GRADE_A_PLUS: 2, GRADE_VALID: 1, GRADE_SKIP: 0}

    def _rank(r: dict) -> tuple:
        return (grade_rank.get(str(r.get("grade", "")).lower(), 0), r.get("score", 0))

    results_sorted = sorted(results, key=_rank, reverse=True)

    # Filter: hanya A+ & valid (skip sudah di-drop)
    eligible = [r for r in results_sorted if r.get("grade") in (GRADE_A_PLUS, GRADE_VALID)]

    sent = 0
    skipped = 0
    for r in eligible[:top_n]:
        pair = r["symbol"]
        if not cooldown.should_alert(pair):
            logger.debug(f"Cooldown skip: {pair}")
            skipped += 1
            continue
        text = format_signal(r, timeframe=timeframe)
        if text is None:
            continue
        ok = bot.send_message(text)
        if ok:
            cooldown.mark_alerted(pair)
            sent += 1
            logger.info(f"Alert sent: {pair} ({r.get('grade')} {r.get('score')}/4)")
        else:
            logger.warning(f"Alert failed: {pair}")
    return (sent, skipped)


def cmd_daemon(args: argparse.Namespace) -> int:
    """
    Daemon mode: loop forever, scan setiap --interval detik, kirim top-N alert.
    Graceful shutdown via Ctrl+C (SIGINT) atau SIGTERM.
    """
    timeframe = args.timeframe
    interval = args.interval
    top_n = args.top_n

    logger.info(
        f"Daemon start: timeframe={timeframe}, interval={interval}s, "
        f"top_n={top_n}, cooldown={ALERT_COOLDOWN_MINUTES}m"
    )

    bot = TelegramBot()
    if not bot.is_configured:
        logger.warning(
            "Telegram bot TIDAK configured — alert akan di-log ke console saja. "
            "Set TELEGRAM_BOT_TOKEN & TELEGRAM_CHAT_ID di .env untuk kirim real."
        )

    # Graceful shutdown
    stop_requested = {"flag": False}

    def _handle_signal(signum, _frame):  # noqa: ANN001
        signame = signal.Signals(signum).name
        logger.warning(f"Received {signame} — shutting down daemon gracefully...")
        stop_requested["flag"] = True

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    cycle = 0
    try:
        with CooldownManager(cooldown_minutes=ALERT_COOLDOWN_MINUTES) as cd:
            while not stop_requested["flag"]:
                cycle += 1
                cycle_start = time.time()
                try:
                    sent, skipped = _run_one_scan_cycle(
                        timeframe=timeframe,
                        top_n=top_n,
                        cooldown=cd,
                        bot=bot,
                    )
                    logger.info(
                        f"[cycle {cycle}] sent={sent}, "
                        f"cooldown_skip={skipped}, "
                        f"elapsed={time.time() - cycle_start:.1f}s"
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.exception(f"[cycle {cycle}] error: {exc}")

                # Cleanup cooldown table (best-effort) sekali per cycle
                try:
                    cd.cleanup_old(max_age_hours=24)
                except Exception:  # noqa: BLE001
                    pass

                if stop_requested["flag"]:
                    break

                # Sleep dalam slice pendek supaya SIGINT cepat di-respons
                slept = 0.0
                while slept < interval and not stop_requested["flag"]:
                    time.sleep(min(1.0, interval - slept))
                    slept += 1.0
    finally:
        bot.close()
        logger.info(f"Daemon stopped after {cycle} cycle(s).")
    return 0


# --- Subcommand handlers (Phase 6: Paper Trading) -------------------------
def _format_paper_status(state: dict, stats: dict, open_positions: list,
                         metrics: dict) -> str:
    """Format paper trading status jadi string rapi."""
    balance = state["balance"]
    initial = state["initial_balance"]
    peak = state["peak_equity"]
    equity = balance + sum(
        PaperPortfolio_compute_unrealized_helper(p) for p in open_positions
    ) if open_positions else balance
    cum_pnl = equity - initial
    cum_pct = (cum_pnl / initial * 100) if initial > 0 else 0
    drawdown = ((peak - equity) / peak * 100) if peak > 0 else 0
    lines = [
        "=" * 60,
        "RX-0 Unicorn — Paper Trading Status",
        "=" * 60,
        f"DB path          : {stats.get('db_path')}",
        f"DB size          : {stats.get('size_bytes', 0):,} bytes",
        "-" * 60,
        f"Initial balance  : ${initial:>13,.2f}",
        f"Cash balance     : ${balance:>13,.2f}",
        f"Equity (mark)    : ${equity:>13,.2f}",
        f"Peak equity      : ${peak:>13,.2f}",
        f"Cumulative P/L   : ${cum_pnl:>+13,.2f}  ({cum_pct:+.2f}%)",
        f"Current drawdown : {drawdown:>12.2f}%",
        "-" * 60,
        f"Total trades     : {stats.get('total_trades', 0):>5}",
        f"Open positions   : {stats.get('open_trades', 0):>5}",
        f"Closed trades    : {stats.get('closed_trades', 0):>5}",
        f"Win rate (all)   : {metrics.get('win_rate', 0) * 100:>12.2f}%",
        f"Total P/L (all)  : ${metrics.get('total_pnl', 0):>+12,.2f}",
        "-" * 60,
    ]
    if open_positions:
        lines.append("OPEN POSITIONS:")
        lines.append(
            f"  {'Symbol':<12}{'Dir':<6}{'Entry':>11}{'SL':>11}{'TP2':>11}"
            f"{'Risk$':>10}{'Score':>6}"
        )
        for p in open_positions:
            lines.append(
                f"  {p['symbol']:<12}{p['direction']:<6}"
                f"{float(p['entry_price']):>11.4f}"
                f"{float(p['sl']):>11.4f}"
                f"{float(p['tp2']):>11.4f}"
                f"${float(p['risk_usd']):>9.2f}"
                f"{int(p['confluence_score']):>5}/4"
            )
    else:
        lines.append("OPEN POSITIONS: (none)")
    lines.append("=" * 60)
    return "\n".join(lines)


def PaperPortfolio_compute_unrealized_helper(p: dict) -> float:
    """Helper used by status formatter to compute unrealized PnL
    using entry price as fallback (no live data)."""
    try:
        if p["direction"] == "long":
            return 0.0  # no current price -> 0 unrealized
        return 0.0
    except Exception:  # noqa: BLE001
        return 0.0


def cmd_paper_start(args: argparse.Namespace) -> int:
    """Initialize paper portfolio."""
    from paper import PaperPortfolio

    with PaperJournal() as j:
        portfolio = PaperPortfolio(journal=j)
        balance = args.balance if args.balance is not None else PAPER_INITIAL_BALANCE
        if args.reset:
            portfolio.reset(initial_balance=balance)
        else:
            state = portfolio.start(initial_balance=balance)
            balance = state["balance"]
        stats = j.get_stats()
        metrics = j.aggregate_performance(days_back=None)
        state = portfolio.get_state()
        print(_format_paper_status(state, stats, [], metrics))
    return 0


def cmd_paper_status(_args: argparse.Namespace) -> int:
    """Tampilkan balance, open positions, dan ringkasan P/L."""
    from paper import PaperPortfolio

    with PaperJournal() as j:
        portfolio = PaperPortfolio(journal=j)
        state = portfolio.get_state()
        stats = j.get_stats()
        open_pos = j.get_open_positions()
        metrics = j.aggregate_performance(days_back=None)
        print(_format_paper_status(state, stats, open_pos, metrics))
    return 0


def cmd_paper_scan_and_trade(args: argparse.Namespace) -> int:
    """Jalankan confluence scan, auto-open paper positions untuk A+/Valid."""
    watchlist = load_watchlist()
    symbols = resolve_symbols(watchlist, args.tier)
    if not symbols:
        logger.error("Watchlist kosong")
        return 1

    bot = TelegramBot()
    notifier = PaperNotifier(bot=bot)
    with PaperJournal() as j:
        trader = PaperTrader(journal=j, notifier=notifier)
        trader.portfolio.start()
        opened = 0
        with CandleDB() as db:
            for sym in symbols:
                res = _scan_symbol(db, sym, args.timeframe)
                if res is None:
                    continue
                if not res.get("direction") or res.get("direction") == "None":
                    continue
                if res["score"] < args.min_score:
                    continue
                if res["grade"] not in (GRADE_A_PLUS, GRADE_VALID):
                    continue
                trade = trader.open_from_signal(
                    res, symbol=sym, signal_source="scanner"
                )
                if trade is not None:
                    opened += 1
        bot.close()
        logger.success(
            f"Paper scan-and-trade done: opened={opened} from "
            f"{len(symbols)} symbols (tf={args.timeframe})"
        )
    return 0


def _make_paper_price_fetcher() -> "callable":
    """Build a price_fetcher from CCXT Binance."""
    try:
        fetcher = CryptoFetcher(exchange_id="binance")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[paper] cannot init fetcher: {exc}")
        return lambda _sym: None

    def _fetch(symbol: str):
        try:
            t = fetcher.exchange.fetch_ticker(symbol)
            return float(t.get("last") or 0) or None
        except Exception:  # noqa: BLE001
            return None
    return _fetch


def cmd_paper_monitor(args: argparse.Namespace) -> int:
    """Daemon: poll open positions dan close SL/TP hits."""
    bot = TelegramBot() if not args.no_telegram else None
    notifier = PaperNotifier(bot=bot) if bot is not None else None
    price_fetcher = _make_paper_price_fetcher()
    with PaperJournal() as j:
        trader = PaperTrader(journal=j, notifier=notifier)
        # daily-digest trigger: 00:05 UTC
        last_digest_key = "last_daily_digest_ts"
        last_digest_ts = j.get_state(last_digest_key, 0) or 0
        now_ts = int(time.time())
        today_str = time.strftime("%Y-%m-%d", time.gmtime(now_ts))
        last_digest_day = (
            time.strftime("%Y-%m-%d", time.gmtime(int(last_digest_ts)))
            if int(last_digest_ts) > 0 else ""
        )
        if last_digest_day != today_str and time.gmtime().tm_hour >= 0:
            # fire at first monitor cycle each day
            logger.info("[paper] sending daily digest (auto)")
            try:
                state = trader.portfolio.get_state()
                equity = trader.portfolio.get_equity()
                digest_state = {
                    "balance": state["balance"],
                    "equity": equity,
                    "initial_balance": state["initial_balance"],
                    "daily_pnl": j.daily_pnl_today(),
                    "trades_today": j.count_trades_today(),
                    "wins": 0,
                    "losses": 0,
                    "win_rate": 0,
                    "drawdown_pct": trader.portfolio.get_drawdown_pct(equity),
                    "open_count": j.count_open_positions(),
                }
                if notifier is not None:
                    notifier.notify_daily_digest(digest_state)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[paper] daily digest error: {exc}")
            j.set_state(last_digest_key, now_ts)
        # weekly report trigger: Sun 23:59 UTC
        last_weekly_ts = j.get_state("last_weekly_report_ts", 0) or 0
        gm = time.gmtime()
        last_weekly_day = (
            time.strftime("%Y-%m-%d", time.gmtime(int(last_weekly_ts)))
            if int(last_weekly_ts) > 0 else ""
        )
        if (
            gm.tm_wday == 6  # Sunday
            and last_weekly_day != time.strftime("%Y-%m-%d", gm)
        ):
            logger.info("[paper] sending weekly report (auto)")
            try:
                cmd_paper_weekly_report_inner(j, notifier)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[paper] weekly report error: {exc}")
            j.set_state("last_weekly_report_ts", now_ts)
        # run monitor loop
        cycles = trader.monitor_loop(
            price_fetcher=price_fetcher,
            interval_seconds=args.interval,
            once=args.once,
        )
        logger.info(f"[paper] monitor finished after {cycles} cycle(s)")
    if bot is not None:
        bot.close()
    return 0


def cmd_paper_close(args: argparse.Namespace) -> int:
    """Manual close satu paper position."""
    with PaperJournal() as j:
        trader = PaperTrader(journal=j)
        trade = j.get_trade_by_id(args.trade_id)
        if trade is None:
            logger.error(f"trade_id '{args.trade_id}' not found")
            return 1
        if trade["status"] != "open":
            logger.error(
                f"trade '{args.trade_id}' is {trade['status']}, not open"
            )
            return 1
        exit_price = (
            args.price if args.price is not None
            else float(trade["entry_price"])
        )
        closed = trader.close_trade(args.trade_id, exit_price, args.reason)
        if closed is None:
            return 1
        print(
            f"Closed {args.trade_id} @ ${exit_price:.4f} "
            f"reason={args.reason} pnl=${float(closed.get('pnl_usd', 0)):+.2f}"
        )
    return 0


def cmd_paper_close_all(_args: argparse.Namespace) -> int:
    """EMERGENCY close semua open paper positions."""
    with PaperJournal() as j:
        trader = PaperTrader(journal=j)
        count = trader.close_all()
        logger.warning(f"EMERGENCY closed {count} paper position(s)")
    return 0


def cmd_paper_report(args: argparse.Namespace) -> int:
    """Generate performance report (text + optional chart)."""
    days = int(args.days) if args.days and args.days > 0 else PAPER_REPORT_DEFAULT_DAYS
    with PaperJournal() as j:
        text = generate_paper_report(j, days_back=days)
        print(text)
        if args.chart:
            chart_path = generate_equity_chart(j, days_back=days)
            if chart_path:
                logger.info(f"Equity chart: {chart_path}")
            else:
                logger.info("No chart generated (no data or matplotlib missing)")
    return 0


def cmd_paper_journal(args: argparse.Namespace) -> int:
    """Tampilkan recent paper trades."""
    limit = max(1, int(args.limit))
    with PaperJournal() as j:
        trades = j.get_all_trades(limit=limit)
        if not trades:
            print("(no paper trades yet)")
            return 0
        print("=" * 90)
        print(f"RX-0 Unicorn — Paper Journal (last {limit} trades)")
        print("=" * 90)
        print(
            f"{'Trade ID':<32}{'Symbol':<10}{'Dir':<5}{'Status':<8}"
            f"{'Entry':>10}{'Exit':>10}{'P/L$':>10}{'R':>7}{'Reason':<10}"
        )
        print("-" * 90)
        for t in trades:
            tid = str(t.get("trade_id", ""))[:30]
            sym = str(t.get("symbol", ""))[:9]
            direction = str(t.get("direction", ""))[:4]
            status = str(t.get("status", ""))[:7]
            entry = float(t.get("entry_price") or 0)
            exit_p = float(t.get("exit_price") or 0)
            pnl = float(t.get("pnl_usd") or 0)
            r = float(t.get("pnl_r_multiple") or 0)
            reason = str(t.get("exit_reason") or "-")[:9]
            print(
                f"{tid:<32}{sym:<10}{direction:<5}{status:<8}"
                f"{entry:>10.4f}{exit_p:>10.4f}${pnl:>+9.2f}{r:>+6.2f}"
                f"{reason:<10}"
            )
        print("=" * 90)
    return 0


def cmd_paper_daily_digest(_args: argparse.Namespace) -> int:
    """Kirim daily digest ke Telegram."""
    bot = TelegramBot()
    notifier = PaperNotifier(bot=bot)
    try:
        with PaperJournal() as j:
            from paper import PaperPortfolio
            portfolio = PaperPortfolio(journal=j, notifier=notifier)
            state = portfolio.get_state()
            equity = portfolio.get_equity()
            today = time.strftime("%Y-%m-%d", time.gmtime())
            # gather metrics for today
            daily_pnl = j.daily_pnl_today()
            cnt = j.count_trades_today()
            wins = 0
            losses = 0
            wr = 0.0
            cutoff = int(
                __import__("datetime").datetime.strptime(today, "%Y-%m-%d")
                .replace(tzinfo=__import__("datetime").timezone.utc)
                .timestamp()
            )
            rows = j.conn.execute(
                "SELECT pnl_usd FROM paper_trades WHERE status='closed' "
                "AND exit_time >= ?",
                (cutoff,),
            ).fetchall()
            for r in rows:
                p = float(r["pnl_usd"] or 0)
                if p > 0:
                    wins += 1
                elif p < 0:
                    losses += 1
            total = wins + losses
            wr = (wins / total) if total > 0 else 0.0
            digest_state = {
                "balance": state["balance"],
                "equity": equity,
                "initial_balance": state["initial_balance"],
                "daily_pnl": daily_pnl,
                "trades_today": cnt,
                "wins": wins,
                "losses": losses,
                "win_rate": wr,
                "drawdown_pct": portfolio.get_drawdown_pct(equity),
                "open_count": j.count_open_positions(),
            }
            ok = notifier.notify_daily_digest(digest_state, date_str=today)
            if ok:
                logger.success(f"Daily digest sent for {today}")
            else:
                logger.info("Daily digest not sent (Telegram degraded)")
    finally:
        bot.close()
    return 0


def cmd_paper_weekly_report(_args: argparse.Namespace) -> int:
    """Kirim weekly report ke Telegram."""
    bot = TelegramBot()
    notifier = PaperNotifier(bot=bot)
    try:
        with PaperJournal() as j:
            cmd_paper_weekly_report_inner(j, notifier)
    finally:
        bot.close()
    return 0


def cmd_paper_weekly_report_inner(
    j: PaperJournal, notifier: PaperNotifier | None
) -> None:
    """Inner helper: build weekly summary, render chart, notify."""
    summary = build_weekly_summary(j, days_back=7)
    chart_path = None
    try:
        chart_path = generate_equity_chart(j, days_back=7)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[paper] weekly chart error: {exc}")
    if notifier is not None:
        ok = notifier.notify_weekly_report(summary, chart_path=chart_path)
        if ok:
            logger.success("Weekly report sent")
        else:
            logger.info("Weekly report not sent (Telegram degraded)")
    # Also print to console
    text = generate_paper_report(j, days_back=7)
    print(text)


# --- Argparse ---
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rx0-unicorn",
        description=(
            "RX-0 Unicorn — Phase 1 data foundation CLI. "
            "Tarik candle dari Binance, simpan ke SQLite, lihat status DB."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # status
    sub.add_parser(
        "status",
        help="Tampilkan statistik database (row count, distinct pairs, dll).",
    ).set_defaults(func=cmd_status)

    # fetch
    p_fetch = sub.add_parser(
        "fetch",
        help="Tarik OHLCV untuk watchlist dan simpan ke SQLite.",
    )
    p_fetch.add_argument(
        "--tier",
        choices=WATCHLIST_TIERS,
        default=None,
        help="Filter ke satu tier watchlist (default: semua).",
    )
    p_fetch.add_argument(
        "--timeframe",
        "-t",
        choices=VALID_TIMEFRAMES,
        default=DEFAULT_TIMEFRAME,
        help=f"Timeframe candle (default: {DEFAULT_TIMEFRAME}).",
    )
    p_fetch.add_argument(
        "--limit",
        "-l",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"Jumlah candle per simbol (default: {DEFAULT_LIMIT}).",
    )
    p_fetch.set_defaults(func=cmd_fetch)

    # cleanup
    sub.add_parser(
        "cleanup",
        help="Hapus candle lama sesuai retention policy (90d intraday, daily forever).",
    ).set_defaults(func=cmd_cleanup)

    # scan
    p_scan = sub.add_parser(
        "scan",
        help=(
            "Jalankan 4 indikator Phase 2 (Luminance/RSI Regime/Structure/"
            "WaveTrend) di data tersimpan dan tampilkan confluence score."
        ),
    )
    p_scan.add_argument(
        "--symbol",
        "-s",
        default=None,
        help="Scan satu simbol saja (e.g. BTC/USDT). Override --tier.",
    )
    p_scan.add_argument(
        "--tier",
        choices=WATCHLIST_TIERS,
        default=None,
        help="Filter ke satu tier watchlist (default: semua). Diabaikan jika --symbol diset.",
    )
    p_scan.add_argument(
        "--timeframe",
        "-t",
        choices=VALID_TIMEFRAMES,
        default=DEFAULT_TIMEFRAME,
        help=f"Timeframe candle (default: {DEFAULT_TIMEFRAME}).",
    )
    p_scan.add_argument(
        "--min-score",
        type=int,
        default=CONFLUENCE_MIN_VALID,
        choices=[0, 1, 2, 3, 4],
        help=f"Highlight simbol dengan confluence score >= nilai ini (default: {CONFLUENCE_MIN_VALID}).",
    )
    p_scan.set_defaults(func=cmd_scan)

    # daemon (Phase 4)
    p_daemon = sub.add_parser(
        "daemon",
        help=(
            "Loop forever: scan watchlist, hitung confluence, kirim top-N "
            "alert ke Telegram. Ctrl+C untuk stop."
        ),
    )
    p_daemon.add_argument(
        "--timeframe",
        "-t",
        choices=VALID_TIMEFRAMES,
        default=DEFAULT_TIMEFRAME,
        help=f"Timeframe candle (default: {DEFAULT_TIMEFRAME}).",
    )
    p_daemon.add_argument(
        "--interval",
        "-i",
        type=int,
        default=SCAN_INTERVAL_SECONDS,
        help=(
            f"Interval antar scan (detik, default: {SCAN_INTERVAL_SECONDS})."
        ),
    )
    p_daemon.add_argument(
        "--top-n",
        type=int,
        default=ALERT_TOP_N,
        help=f"Jumlah sinyal teratas yang dikirim per siklus (default: {ALERT_TOP_N}).",
    )
    p_daemon.set_defaults(func=cmd_daemon)

    # test-alert (Phase 4)
    sub.add_parser(
        "test-alert",
        help=(
            "Kirim sample alert (placeholder data) ke Telegram untuk "
            "verifikasi konfigurasi bot. Print ke console kalau token kosong."
        ),
    ).set_defaults(func=cmd_test_alert)

    # cooldown (Phase 4)
    p_cd = sub.add_parser(
        "cooldown",
        help="Manage alert_cooldown table (list / --clear [pair] / --clear-all).",
    )
    p_cd.add_argument(
        "--clear",
        metavar="PAIR",
        nargs="?",
        const="",  # bare --clear (tanpa argumen) -> hapus semua
        default=None,
        help=(
            "Hapus cooldown. Tanpa argumen: hapus semua. "
            "Dengan pair: hapus cooldown untuk pair itu."
        ),
    )
    p_cd.add_argument(
        "--clear-all",
        action="store_true",
        help="Hapus semua entry cooldown (alias: --clear tanpa argumen).",
    )
    p_cd.set_defaults(func=cmd_cooldown)

    # backtest (Phase 5)
    p_bt = sub.add_parser(
        "backtest",
        help=(
            "Jalankan backtest walk-forward untuk satu simbol/timeframe, "
            "hitung 6 metrics wajib dari STRATEGY.md (Win Rate, Profit Factor, "
            "Max Drawdown, Sharpe, Avg R-Multiple, Expectancy)."
        ),
    )
    p_bt.add_argument(
        "--symbol",
        "-s",
        required=True,
        help="Simbol trading (e.g. BTC/USDT). Required.",
    )
    p_bt.add_argument(
        "--timeframe",
        "-t",
        choices=VALID_TIMEFRAMES,
        default=DEFAULT_TIMEFRAME,
        help=f"Timeframe candle (default: {DEFAULT_TIMEFRAME}).",
    )
    p_bt.add_argument(
        "--days",
        "-d",
        type=int,
        default=BACKTEST_DEFAULT_DAYS,
        help=(
            f"Berapa hari data historis yang digunakan (default: "
            f"{BACKTEST_DEFAULT_DAYS}). Minimum {BACKTEST_MIN_SAMPLE_SIZE}."
        ),
    )
    p_bt.add_argument(
        "--initial-capital",
        type=float,
        default=BACKTEST_INITIAL_CAPITAL,
        help=(
            f"Modal awal USD untuk backtest (default: "
            f"{BACKTEST_INITIAL_CAPITAL:,.0f})."
        ),
    )
    p_bt.add_argument(
        "--risk-per-trade",
        type=float,
        default=BACKTEST_RISK_PER_TRADE,
        help=(
            f"Risk per trade sebagai fraksi modal (default: "
            f"{BACKTEST_RISK_PER_TRADE})."
        ),
    )
    p_bt.add_argument(
        "--max-bars-hold",
        type=int,
        default=BACKTEST_MAX_BARS_HOLD,
        help=(
            f"Time stop dalam bar (default: {BACKTEST_MAX_BARS_HOLD})."
        ),
    )
    p_bt.add_argument(
        "--output",
        "-o",
        default=None,
        help=(
            "Path untuk simpan hasil backtest JSON (default: tidak disimpan). "
            "Mis. backtest/results/btc_90d.json"
        ),
    )
    p_bt.add_argument(
        "--chart",
        "-c",
        default=None,
        help=(
            "Path untuk render equity curve PNG (default: tidak disimpan). "
            "Mis. backtest/results/btc_equity.png"
        ),
    )
    p_bt.set_defaults(func=cmd_backtest)

    # paper (Phase 6)
    p_paper = sub.add_parser(
        "paper",
        help=(
            "Paper trading (Phase 6) — simulated portfolio, journal, "
            "monitor daemon, dan reporting. NO real money. "
            "Tujuan: validasi strategi confluence real-time sebelum Phase 7."
        ),
    )
    paper_sub = p_paper.add_subparsers(dest="paper_command", required=True)

    # paper start
    p_paper_start = paper_sub.add_parser(
        "start",
        help=(
            "Initialize paper portfolio dengan balance awal. "
            "Idempotent: aman dipanggil berulang kali."
        ),
    )
    p_paper_start.add_argument(
        "--reset",
        action="store_true",
        help="Hapus semua paper trade + state lalu init ulang (DANGEROUS).",
    )
    p_paper_start.add_argument(
        "--balance",
        type=float,
        default=None,
        help=f"Modal awal USD (default: {PAPER_INITIAL_BALANCE:,.0f}).",
    )
    p_paper_start.set_defaults(func=cmd_paper_start)

    # paper status
    paper_sub.add_parser(
        "status",
        help="Tampilkan balance, open positions, dan ringkasan P/L.",
    ).set_defaults(func=cmd_paper_status)

    # paper scan-and-trade
    p_paper_scan = paper_sub.add_parser(
        "scan-and-trade",
        help=(
            "Jalankan confluence scan + auto-open paper position untuk "
            "sinyal A+/Valid (respects risk limits)."
        ),
    )
    p_paper_scan.add_argument(
        "--timeframe",
        "-t",
        choices=VALID_TIMEFRAMES,
        default=DEFAULT_TIMEFRAME,
        help=f"Timeframe candle (default: {DEFAULT_TIMEFRAME}).",
    )
    p_paper_scan.add_argument(
        "--tier",
        choices=WATCHLIST_TIERS,
        default=None,
        help="Filter ke satu tier watchlist (default: semua).",
    )
    p_paper_scan.add_argument(
        "--min-score",
        type=int,
        default=CONFLUENCE_MIN_VALID,
        choices=[0, 1, 2, 3, 4],
        help=f"Minimum confluence score untuk entry (default: {CONFLUENCE_MIN_VALID}).",
    )
    p_paper_scan.set_defaults(func=cmd_paper_scan_and_trade)

    # paper monitor
    p_paper_mon = paper_sub.add_parser(
        "monitor",
        help=(
            "Daemon: setiap --interval detik, cek SL/TP semua open "
            "positions. Close otomatis kalau hit. Ctrl+C untuk stop."
        ),
    )
    p_paper_mon.add_argument(
        "--interval",
        "-i",
        type=int,
        default=PAPER_MONITOR_INTERVAL_SECONDS,
        help=(
            f"Interval polling (detik, default: "
            f"{PAPER_MONITOR_INTERVAL_SECONDS})."
        ),
    )
    p_paper_mon.add_argument(
        "--once",
        action="store_true",
        help="Jalankan 1 cycle saja lalu exit (untuk testing).",
    )
    p_paper_mon.add_argument(
        "--no-telegram",
        action="store_true",
        help="Disable Telegram notifications (default: enabled jika bot configured).",
    )
    p_paper_mon.set_defaults(func=cmd_paper_monitor)

    # paper close <trade_id>
    p_paper_close = paper_sub.add_parser(
        "close",
        help="Manual close satu paper position (id dari 'paper journal').",
    )
    p_paper_close.add_argument(
        "trade_id",
        help="Trade ID yang akan di-close (lihat 'paper journal').",
    )
    p_paper_close.add_argument(
        "--price",
        type=float,
        default=None,
        help="Exit price. Default: entry price (PnL=0, used for emergency close).",
    )
    p_paper_close.add_argument(
        "--reason",
        default="manual",
        help="Exit reason (default: manual).",
    )
    p_paper_close.set_defaults(func=cmd_paper_close)

    # paper close-all
    paper_sub.add_parser(
        "close-all",
        help="EMERGENCY close semua open paper positions.",
    ).set_defaults(func=cmd_paper_close_all)

    # paper report
    p_paper_report = paper_sub.add_parser(
        "report",
        help="Generate performance report (text + optional equity chart).",
    )
    p_paper_report.add_argument(
        "--days",
        "-d",
        type=int,
        default=PAPER_REPORT_DEFAULT_DAYS,
        help=f"Lookback days (default: {PAPER_REPORT_DEFAULT_DAYS}).",
    )
    p_paper_report.add_argument(
        "--chart",
        action="store_true",
        help="Render equity curve PNG (paper/reports/equity_<ts>.png).",
    )
    p_paper_report.set_defaults(func=cmd_paper_report)

    # paper journal
    p_paper_journal = paper_sub.add_parser(
        "journal",
        help="Tampilkan recent paper trades (default: 20 terakhir).",
    )
    p_paper_journal.add_argument(
        "--limit",
        "-n",
        type=int,
        default=20,
        help="Jumlah trade yang ditampilkan (default: 20).",
    )
    p_paper_journal.set_defaults(func=cmd_paper_journal)

    # paper daily-digest
    paper_sub.add_parser(
        "daily-digest",
        help=(
            "Kirim daily digest ke Telegram sekarang (Tier 3). "
            "Biasanya dipanggil via cron di 00:05 UTC."
        ),
    ).set_defaults(func=cmd_paper_daily_digest)

    # paper weekly-report
    paper_sub.add_parser(
        "weekly-report",
        help=(
            "Kirim weekly report ke Telegram sekarang (Tier 4, "
            "include equity chart kalau ada data). Biasanya dipanggil "
            "via cron di Sunday 23:59 UTC."
        ),
    ).set_defaults(func=cmd_paper_weekly_report)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        logger.warning("Interrupted by user")
        return 130
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"Fatal: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
