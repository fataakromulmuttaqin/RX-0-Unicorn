# 🦄 RX-0 Unicorn — Trading Cheat Sheet

> **Versi ringkas untuk quick reference saat trading.** Detail lengkap: [CHEATSHEET.html](CHEATSHEET.html)

---

## 📊 rx0-momentum.pane — Lower Pane

### 4 Lines

| Line | Warna | Fungsi | Key Levels |
|------|-------|--------|------------|
| **ADX** | Orange stepline | Trend strength | **25** = threshold trending/ranging |
| **RSI** | Teal solid | Wilder momentum | **70/30** = overbought/oversold |
| **WT1** | Blue solid | Fast momentum | **+60/-60** = extreme zones |
| **WT2** | Red solid | Signal line | Cross WT1 = entry trigger |

### Background Tint (auto)
- 🔵 **Biru** = Trending (ADX > 25) → **TRADE-FRIENDLY**
- ⚪ **Abu** = Ranging (ADX < 25) → **SKIP, noise**
- 🟢 **Hijau** = WT oversold zone
- 🔴 **Merah** = WT overbought zone

### Signal Markers
- 🟢 **WT▲** di bawah bar = WT long signal
- 🔴 **WT▼** di atas bar = WT short signal

---

## 📈 rx0-confluence.pane — Chart Utama (Overlay)

| Element | Arti |
|---------|------|
| **Luminance range** (2 line teal + fill) | Consolidation zone |
| **Lum▲ / Lum▼** | Breakout arrows |
| **▼ / ▲** | Swing high / low markers |
| **BOS** (biru) | Break of Structure (continuation) |
| **CHoCH** (orange) | Change of Character (reversal) |
| **A+▲ / A+▼** | Grade A+ signal (4/4 confluence) |
| **V▲ / V▼** | Grade Valid signal (3/4 confluence) |

---

## 🎯 Decision Matrix

| Regime | Confluence | Action | Size |
|--------|-----------|--------|------|
| Trending | **4/4 (A+)** | ✅ **GO FULL** | 1.5x |
| Trending | **3/4 (Valid)** | ✅ **GO NORMAL** | 1.0x |
| Ranging | 4/4 (A+) | ⚠️ **CAUTION** | 0.5x |
| Ranging | 3/4 (Valid) | ❌ **SKIP** | — |
| Either | <3/4 | ❌ **SKIP** | — |

---

## 🚦 Pre-Entry Checklist (5 poin)

1. ✅ **Background Trending** (ADX > 25)? → kalau abu, STOP
2. ✅ **Luminance breakout** muncul (Lum▲/Lum▼)?
3. ✅ **BOS/CHoCH** label sesuai direction?
4. ✅ **WT1 cross WT2** di zona extreme (bukan tengah)?
5. ✅ **Confluence score**: 3/4 = Valid, 4/4 = A+

> **Min 4/5 = ENTRY. Kalau cuma 3/5, lean SKIP.**

---

## 🛡️ Risk Rules

| Rule | Value |
|------|-------|
| Risk per trade | 1-2% modal |
| Min R:R | 1:2 |
| Max trades/day | 3 |
| Daily loss limit | -5% → STOP |
| News filter | ±30 min red news |
| Max correlated | 2 posisi |
| Max drawdown circuit breaker | -15% → pause 24h |

---

## 🎨 Color Reference (TradingView)

| Use | Color | Hex |
|-----|-------|-----|
| Bullish / Long | 🟢 Hijau | `#00ff88` |
| Bearish / Short | 🔴 Merah | `#ff4757` |
| Trending | 🔵 Biru | `#5d8fff` |
| A+ Setup | 🌊 Teal | `#00ffd5` |
| Caution | 🟡 Kuning | `#ffd166` |
| Neutral | ⚪ Abu | `#6c7a8c` |

---

## 🛠️ Quick Commands

```bash
# Scan market
python main.py scan --timeframe 1h

# Backtest
python main.py backtest --symbol BTC/USDT --timeframe 1h --days 90

# Telegram alerts (daemon)
python main.py daemon --timeframe 1h --interval 300

# Test alert (no real Telegram needed)
python main.py test-alert

# Clear cooldown
python main.py cooldown --clear BTC/USDT
```

---

## 📌 Signal Strength Cheat

| Setup | Win Rate Target | Position Size |
|-------|----------------|---------------|
| A+ + Trending (4/4) | 70%+ | 1.5x |
| Valid + Trending (3/4) | 55%+ | 1.0x |
| A+ + Ranging (4/4) | 50-60% | 0.5x |
| Valid + Ranging (3/4) | <50% | **SKIP** |
| Skip (<3/4) | — | **NO TRADE** |

---

**🦄 Print ini atau buka HTML di browser saat trading. Update tiap ada strategy change.**
