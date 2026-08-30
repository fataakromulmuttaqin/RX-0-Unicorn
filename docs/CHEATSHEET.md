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

## 🏗️ Multi-Timeframe (MTF) — v0.7.0

**Hierarchy:** 1D → 4H → 1H → 15m (top filter ke entry precision)

### 3-Way Alignment Check (1D + 4H + 1H)

| 1D | 4H | 1H | Verdict |
|----|----|----|---------|
| BULL (+1) | BULL (+1) | LONG signal | ✅ **STRONG ALIGNED** (A+ eligible) |
| BEAR (-1) | BEAR (-1) | SHORT signal | ✅ **STRONG ALIGNED** |
| NEUTRAL (0) | BULL (+1) | LONG signal | ✅ **SOFT ALIGNED** (trade allowed) |
| BULL (+1) | BULL (+1) | SHORT signal | ❌ **MTF CONFLICT — SKIP** |
| BEAR (-1) | BULL (+1) | any | ❌ **MTF CONFLICT — SKIP** (counter-trend rally) |
| any | NEUTRAL (0) | any | ❌ **4H bias unclear — SKIP** |

**Bias strength:**
- 1D + 4H both strong (EMA diff > 5%, structure confirmed) → 1H + 15m entries OK
- 4H weak (EMA diff < 3%) → only 1H entries, no 15m
- All neutral → no trade

### 15m Entry Trigger (optional precision)

LTF entry on 15m when 4H+1H aligned:
- **EMA 9 cross EMA 21** + volume spike 1.5x → entry trigger
- **RSI(7) extreme** (<30 or >70) → confirmation
- **No trigger** → wait for pullback, don't chase

---

## 🛡️ Correlation Guard — v0.7.0

### 11 Correlation Groups (max 2 positions per group)

| Group | Examples |
|---|---|
| **L1 majors** | BTC, ETH, BCH, LTC |
| **L1 alts** | SOL, BNB, ADA, AVAX, NEAR, ATOM, APT, SUI, TIA |
| **L2s** | ARB, OP, MATIC, IMX, LDO |
| **DeFi** | UNI, AAVE, CRV, SNX, COMP, MKR, DYDX |
| **Memes** | DOGE, SHIB, PEPE, BONK, WIF |
| **AI** | FET, RNDR, WLD, TAO, AGIX |
| **Privacy** | XMR, ZEC, DASH |
| **GameFi** | AXS, MANA, SAND, GALA |
| **Infra** | FIL, AR, STORJ, GRT, LINK |
| **RWA** | ONDO, MATR |
| **Exchange** | OKB, KCS, LEO, CRO |

### Cross-Group Rules (17 rules)

Same group = correlated. **Plus** cross-group rules:
- `l1_majors ↔ {l1_alts, l2s, defi, memes, ai, privacy, rwa, gamefi, infra}` — BTC/ETH drop affects everything
- `l1_alts ↔ {l2s, memes, ai, gamefi, infra, rwa}` — altcoin season correlation
- `l2s ↔ defi` — share narrative
- `memes ↔ {l1_alts, l2s}` — altcoin season
- `ai ↔ {gamefi, l2s}` — tech narrative

### Example Decision

```
Open: BTC + ETH (both l1_majors, 2 correlated)
Try: SOL (l1_alt) — BTC↔SOL cross-group → correlated → BLOCKED
Try: ARB (l2s) — BTC↔ARB cross-group → correlated → BLOCKED
Try: DOGE (memes) — BTC↔DOGE cross-group → correlated → BLOCKED
Try: ZRX (independent) → ALLOWED
```

Telegram notif on block:
```
🚨 CORRELATION GUARD
Blocked: SOL/USDT (group: l1_alts)
Correlated with: BTC/USDT, ETH/USDT
Max correlated positions: 2
```

---

## 🚦 Pre-Entry Checklist (7 poin)

1. ✅ **MTF aligned** (1D + 4H + 1H same direction)? → kalau conflict, STOP
2. ✅ **Background Trending** (ADX > 25)? → kalau abu, STOP
3. ✅ **Luminance breakout** muncul (Lum▲/Lum▼)?
4. ✅ **BOS/CHoCH** label sesuai direction?
5. ✅ **WT1 cross WT2** di zona extreme (bukan tengah)?
6. ✅ **Confluence score**: 3/4 = Valid, 4/4 = A+
7. ✅ **Correlation OK** (max 2 correlated positions open)?

> **Min 6/7 = ENTRY. Kalau cuma 5/7, lean SKIP.**

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
| A+ + Trending + MTF aligned (4/4) | 70%+ | 1.5x |
| Valid + Trending + MTF aligned (3/4) | 55%+ | 1.0x |
| A+ + Ranging (4/4) | 50-60% | 0.5x |
| Valid + Ranging (3/4) | <50% | **SKIP** |
| Skip (<3/4) | — | **NO TRADE** |
| **MTF conflict** (1D vs 4H vs 1H) | — | **NO TRADE** |
| **Correlation limit hit** | — | **NO TRADE** (wait for current position to close) |

---

## 📰 News & Sentiment (informational only)

**On-demand via Telegram commands:**
- `/rx0 news` — top 12 news last 24h (high/medium/low impact)
- `/rx0 news BTC,ETH` — news filtered by currency
- `/rx0 sentiment` — market Fear & Greed index
- `/rx0 sentiment BTC/USDT` — per-coin sentiment (price action implied)

**News sources:** CoinDesk, Cointelegraph, The Block (3 RSS feeds)
**Sentiment sources:** CoinGecko (price action), Alternative.me (F&G)
**Fetch strategy:** Lazy, rate-limited (10 req/min), 1H cache — no scheduled daemon fetch
**Trade impact:** None — informational only, does NOT block entry (per user request)

---

**🦄 Print ini atau buka HTML di browser saat trading. Update tiap ada strategy change.**

**v0.7.0 (2026-08-30)** — Added MTF (1D/4H/1H/15m), correlation guard (11 groups + 17 cross-rules), news/sentiment (lazy, rate-limited)
