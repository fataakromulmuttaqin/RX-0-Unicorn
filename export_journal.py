"""
export_journal.py — Ekspor riwayat trading & scanning RX-0 Unicorn ke journal.json

File JSON hasil export ini yang dibaca oleh tab "Jurnal Trading" di rx0-unicorn.html.
Taruh journal.json di folder yang sama dengan file HTML tersebut, lalu refresh browser
(atau biarkan saja — halaman polling ulang setiap 30 detik).

CARA PAKAI (default sudah disesuaikan dengan layout repo RX-0 Unicorn)
---------------------------------------------------------------------
    python export_journal.py

Default membaca `data/storage/paper_trades.db` (tabel `paper_trades`) dan menulis
`journal.json` di folder yang sama dengan HTML. Override via flag jika perlu:

    python export_journal.py --db /path/lain/paper.db --out /srv/site/journal.json

Override nama tabel:
    --trades-table paper_trades   (default)
    --scans-table  scan_history   (default; dilewati jika tabel tidak ada)

JADWALKAN OTOMATIS
------------------
Supaya jurnal di web selalu up to date selama bot jalan, jadwalkan tiap beberapa
menit lewat cron:

    */5 * * * * cd /path/to/RX-0-Unicorn && /path/to/.venv/bin/python export_journal.py

CATATAN SKEMA
-------------
Skema aktual tabel `paper_trades` di repo ini (lihat paper/journal.py):
    id, trade_id, symbol, direction, entry_time, entry_price, exit_time,
    exit_price, sl, tp1, tp2, exit_reason, pnl_usd, pnl_r_multiple,
    confluence_score, grade, size_multiplier, signal_source,
    position_size_units, risk_usd, status, notes
"""
import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone

# Pemetaan: "field JSON" -> "nama kolom asli di DB".
# Hanya kolom yang ADA di DB yang akan dipakai (lihat rows_mapped()).
# Tambahkan/ubah di sini kalau skema DB berubah.
COLUMN_MAP = {
    # identitas
    "trade_id":        "trade_id",
    "symbol":          "symbol",
    "side":            "direction",      # long/short
    "timeframe":       "timeframe",
    # grading & confluence
    "grade":           "grade",
    "confluence_score":"confluence_score",
    "size_multiplier": "size_multiplier",
    "signal_source":   "signal_source",
    # entry
    "entry_time":      "entry_time",
    "entry_price":     "entry_price",
    "sl":              "sl",
    "tp1":             "tp1",
    "tp2":             "tp2",
    "risk_usd":        "risk_usd",
    "position_size_units": "position_size_units",
    # exit
    "exit_time":       "exit_time",
    "exit_price":      "exit_price",
    "exit_reason":     "exit_reason",
    # pnl
    "pnl_usd":         "pnl_usd",
    "r_multiple":      "pnl_r_multiple",
    "status":          "status",         # open / closed
    "notes":           "notes",
}

# Placeholder — repo ini belum punya tabel scan_history; dilewati diam-diam jika absen.
SCAN_COLUMN_MAP = {
    "time":      "time",
    "symbol":    "symbol",
    "timeframe": "timeframe",
    "score":     "score",
    "grade":     "grade",
    "bias":      "bias",
}

# Default path yang sesuai dengan layout repo (data/storage/paper_trades.db)
DEFAULT_DB = "data/storage/paper_trades.db"
DEFAULT_OUT = "journal.json"


def table_columns(cur, table):
    cur.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}


def _iso(value):
    """Konversi epoch (int/float) atau string epoch ke ISO 8601 UTC. None/NaN -> None."""
    if value is None:
        return None
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return value  # sudah string/non-numeric, biarkan apa adanya
    if ts <= 0:
        return None
    # Auto-detect detik vs milidetik
    if ts > 1e12:
        ts /= 1000.0
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def rows_mapped(cur, table, colmap):
    available = table_columns(cur, table)
    used = {json_key: db_col for json_key, db_col in colmap.items() if db_col in available}
    if not used:
        print(f"[warn] tidak ada kolom yang cocok di tabel {table}; ekspor kosong.", file=sys.stderr)
        return []
    select_cols = ", ".join(used.values())
    cur.execute(f"SELECT {select_cols} FROM {table}")
    # Kolom yang perlu dikonversi epoch -> ISO 8601 (buat tampilan web)
    time_fields = {"entry_time", "exit_time"}
    out = []
    for row in cur.fetchall():
        rec = dict(zip(used.keys(), row))
        for k in time_fields:
            if k in rec:
                rec[k] = _iso(rec[k])
        out.append(rec)
    return out


def export(db_path, out_path, trades_table, scans_table):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    trades = []
    try:
        trades = rows_mapped(cur, trades_table, COLUMN_MAP)
    except sqlite3.OperationalError as e:
        print(f"[warn] gagal baca tabel trade '{trades_table}': {e}", file=sys.stderr)

    scans = []
    try:
        scans = rows_mapped(cur, scans_table, SCAN_COLUMN_MAP)
    except sqlite3.OperationalError as e:
        print(f"[info] tabel scan '{scans_table}' tidak ditemukan (opsional, dilewat): {e}", file=sys.stderr)

    # Tambahan opsional: state (balance/peak/initial) & daily aggregate, kalau tabelnya ada.
    state = {}
    try:
        rows = cur.execute("SELECT key, value FROM paper_state").fetchall()
        for k, v in rows:
            # value disimpan sebagai TEXT di DB — coba parse angka
            try:
                state[k] = json.loads(v) if isinstance(v, str) and v.strip().startswith(('{', '"', '[')) else float(v)
            except (ValueError, TypeError):
                state[k] = v
    except sqlite3.OperationalError:
        pass  # tabel paper_state tidak ada -> skip

    daily = []
    try:
        cols = table_columns(cur, "paper_daily")
        if {"date", "total_equity", "daily_pnl"}.issubset(cols):
            daily_rows = cur.execute(
                "SELECT date, total_equity, daily_pnl, trades_count, wins, losses, win_rate, cumulative_pnl "
                "FROM paper_daily ORDER BY date ASC"
            ).fetchall()
            daily = [
                {"date": r[0], "total_equity": r[1], "daily_pnl": r[2],
                 "trades_count": r[3], "wins": r[4], "losses": r[5],
                 "win_rate": r[6], "cumulative_pnl": r[7]}
                for r in daily_rows
            ]
    except sqlite3.OperationalError:
        pass

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "is_demo": False,
        "trades": trades,
        "scans": scans,
        "state": state,   # balance, initial_balance, peak_equity (kalau ada)
        "daily": daily,   # list per-day aggregate (kalau ada)
    }

    with open(out_path, "w") as f:
        json.dump(payload, f, indent=2, default=str)

    print(f"OK — {len(trades)} trade & {len(scans)} scan diekspor ke {out_path}"
          + (f" (+state: {list(state.keys())})" if state else "")
          + (f" (+daily: {len(daily)} hari)" if daily else ""))
    conn.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Ekspor riwayat RX-0 Unicorn ke journal.json")
    p.add_argument("--db", default=DEFAULT_DB, help=f"Path SQLite (default: {DEFAULT_DB})")
    p.add_argument("--out", default=DEFAULT_OUT, help=f"Path JSON output (default: {DEFAULT_OUT})")
    p.add_argument("--trades-table", default="paper_trades", help="Nama tabel trade (default: paper_trades)")
    p.add_argument("--scans-table", default="scan_history", help="Nama tabel scan (default: scan_history; dilewati jika absen)")
    args = p.parse_args()
    export(args.db, args.out, args.trades_table, args.scans_table)
