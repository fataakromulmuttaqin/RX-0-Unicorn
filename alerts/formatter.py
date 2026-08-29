"""
Alert formatter untuk RX-0 Unicorn — Phase 4.

Mengubah ringkasan `latest_confluence()` (dict, native Python) jadi
string alert yang siap dikirim ke Telegram. Format fix:

    {emoji} RX-0 SIGNAL — {GRADE}
    ━━━━━━━━━━━━━━━
    Pair:       BTC/USDT
    TF:         1H
    Score:      3/4
    Grade:      VALID
    Direction:  LONG
    Entry:      $62,450
    SL:         $62,180 (-0.43%)
    TP1:        $62,990 (+0.87%)
    TP2:        $63,530 (+1.73%)
    R:R:        1:2.0 / 1:4.0
    Regime:     trending
    Confluence:
      ✓ Luminance breakout
      ✓ RSI regime aligned
      ✓ BOS confirm
      ✗ WaveTrend (no cross)
    Time:       2026-08-29 14:23 UTC

Grade A+ -> emoji ⭐ dan header "RX-0 SIGNAL — A+".
Grade valid -> emoji 🟢 dan header "RX-0 SIGNAL — VALID".
Grade skip -> return None (alert TIDAK dikirim untuk skip).

Arah long/short menentukan sign pada SL/TP pct dan struktur entry di atas/
di bawah SL.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from confluence.scorer import GRADE_A_PLUS, GRADE_SKIP, GRADE_VALID
from src.config import A_PLUS_EMOJI, SKIP_EMOJI, VALID_EMOJI


# Human-readable indicator names buat daftar confluence
SIGNAL_LABELS: dict[str, str] = {
    "luminance": "Luminance breakout",
    "rsi_regime": "RSI regime aligned",
    "structure": "BOS confirm",
    "wavetrend": "WaveTrend (no cross)",
}
# Catatan: label wavetrend "no cross" di atas adalah default; formatter akan
# swap ke "WaveTrend cross" kalau sinyal != 0 (lihat _format_confluence_lines).


# Price formatting: USDT 4 desimal di bawah $1k, 2 desimal di atas.
# Sederhana saja — fokus pada readability.
def _fmt_price(value: float | None, *, default: str = "N/A") -> str:
    if value is None:
        return default
    if value >= 1000:
        return f"${value:,.2f}"
    if value >= 1:
        return f"${value:,.4f}"
    return f"${value:,.6f}"


def _fmt_pct(pct: float | None) -> str:
    if pct is None:
        return "N/A"
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.2f}%"


def _pct_change(target: float | None, base: float | None) -> float | None:
    if target is None or base is None or base == 0:
        return None
    return (target - base) / base * 100.0


def _format_confluence_lines(signals: dict[str, int] | None) -> list[str]:
    """
    Render baris per-indikator dengan ✓/✗. Order fix (luminance, rsi,
    structure, wavetrend) untuk konsistensi visual.
    """
    if not signals:
        return ["  (no signal data)"]
    lines: list[str] = []
    for key in ("luminance", "rsi_regime", "structure", "wavetrend"):
        v = signals.get(key, 0)
        label = SIGNAL_LABELS[key]
        if key == "wavetrend" and v != 0:
            # Override default "no cross" kalau WaveTrend sebenarnya cross
            label = "WaveTrend cross"
        marker = "✓" if v != 0 else "✗"
        lines.append(f"  {marker} {label}")
    return lines


def format_signal(
    latest_confluence_result: dict[str, Any],
    *,
    pair: str | None = None,
    timeframe: str | None = None,
    now: datetime | None = None,
) -> str | None:
    """
    Format dict dari `latest_confluence()` jadi string alert.

    Args:
        latest_confluence_result: dict hasil latest_confluence(df).
        pair: Override pair (default baca dari result['symbol']).
        timeframe: Display label mis. "1H", "4H" — ditambahkan ke header
                  jika diberikan.
        now: Override timestamp (untuk testing). Default UTC now.

    Returns:
        Formatted alert string, atau None kalau grade == "skip"
        (alert skip TIDAK boleh dikirim sesuai STRATEGY.md).
    """
    grade_raw = str(latest_confluence_result.get("grade", GRADE_SKIP)).lower()
    if grade_raw == GRADE_SKIP:
        return None

    # Normalize grade: "a+" -> "A+", "valid" -> "VALID" (display uppercase)
    if grade_raw in ("a+", "a-plus", "a_plus"):
        grade = GRADE_A_PLUS
        grade_display = "A+"
        emoji = A_PLUS_EMOJI
    elif grade_raw == GRADE_VALID:
        grade = GRADE_VALID
        grade_display = "VALID"
        emoji = VALID_EMOJI
    else:
        # Unknown grade -> treat as skip, don't send
        return None

    direction = (latest_confluence_result.get("direction") or "").lower()
    if direction not in ("long", "short"):
        # No bias -> skip
        return None

    sym = pair or latest_confluence_result.get("symbol") or "?"
    score = int(latest_confluence_result.get("score", 0))
    entry = latest_confluence_result.get("entry_price")
    sl = latest_confluence_result.get("stop_loss")
    tp1 = latest_confluence_result.get("take_profit_1")
    tp2 = latest_confluence_result.get("take_profit_2")
    regime = latest_confluence_result.get("regime") or "N/A"
    signals = latest_confluence_result.get("signals") or {}

    sl_pct = _pct_change(sl, entry)
    tp1_pct = _pct_change(tp1, entry)
    tp2_pct = _pct_change(tp2, entry)
    # Risk-multiple (1:2.0, 1:4.0) — dihitung dari jarak, default 2.0 / 4.0
    rr1 = 2.0
    rr2 = 4.0
    if sl is not None and entry is not None:
        risk = abs(entry - sl)
        if risk > 0:
            if tp1 is not None:
                rr1 = abs(tp1 - entry) / risk
            if tp2 is not None:
                rr2 = abs(tp2 - entry) / risk

    ts = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC")
    tf_label = f" {timeframe}" if timeframe else ""

    direction_display = direction.upper()
    lines: list[str] = [
        f"{emoji} RX-0 SIGNAL — {grade_display}{tf_label}",
        "━" * 15,
        f"Pair:       {sym}",
        f"TF:         {timeframe or 'N/A'}",
        f"Score:      {score}/4",
        f"Grade:      {grade_display}",
        f"Direction:  {direction_display}",
        f"Entry:      {_fmt_price(entry)}",
        f"SL:         {_fmt_price(sl)} ({_fmt_pct(sl_pct)})",
        f"TP1:        {_fmt_price(tp1)} ({_fmt_pct(tp1_pct)})",
        f"TP2:        {_fmt_price(tp2)} ({_fmt_pct(tp2_pct)})",
        f"R:R:        1:{rr1:.1f} / 1:{rr2:.1f}",
        f"Regime:     {regime}",
        "Confluence:",
    ]
    lines.extend(_format_confluence_lines(signals))
    lines.append(f"Time:       {ts}")
    return "\n".join(lines)
