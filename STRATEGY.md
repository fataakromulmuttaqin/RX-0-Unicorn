# 🧠 Strategi Detail — RX-0 Unicorn

> **Deep dive ke 4 strategi LuxAlgo yang jadi pondasi RX-0 Unicorn.**

---

## 📐 Confluence Framework

RX-0 Unicorn tidak pakai single indicator — pakai **4-layer confluence** dimana signal hanya valid jika minimal **3/4 indicator aligned**.

```
Signal Strength:
★☆☆☆☆ (1/4) → SKIP
★★☆☆☆ (2/4) → SKIP  
★★★☆☆ (3/4) → VALID ENTRY (normal size)
★★★★☆ (4/4) → A+ SETUP (size up 1.5x)
```

---

## 1️⃣ Luminance Breakout Engine

**Source:** [LuxAlgo Library](https://www.luxalgo.com/library/indicator/luminance-breakout-engine/)
**Backtest LuxAlgo:** PF 2.33, WR 71.6% (BTCUSDT 1H, 102 trades)
**Fungsi:** Detect breakout dengan volume confirmation

### Logic
- Identifikasi range/consolidation zone
- Wait for breakout candle (close beyond range boundary)
- Confirm dengan volume spike (≥ 1.5x average)
- Filter: avoid false breakout di low-volume hours

### Parameters
- Range lookback: 20 bars
- Volume threshold: 1.5x
- Min consolidation bars: 5
- Timeframe optimal: 1H, 4H

### Entry Rules
- **Long:** Breakout above resistance + volume confirm
- **Short:** Breakout below support + volume confirm
- **SL:** Beyond range boundary (opposite side)
- **TP1:** 1R (1x risk)
- **TP2:** 2R (2x risk)

---

## 2️⃣ RSI Regime Filter

**Source:** [LuxAlgo RSI Regime Filter](https://www.luxalgo.com/library/indicator/rsi-regime-filter/)
**Fungsi:** Validasi momentum, anti-fading runaway trend

### Logic
- Calculate RSI(14)
- Classify market regime: trending vs ranging
- Only trigger RSI signal when regime matches signal direction
- 4 regime methods: RSI, EMA trend, ADX, composite vote

### Parameters
- RSI period: 14
- Regime threshold: ADX > 25 = trending
- Overbought: 70
- Oversold: 30

### Confluence Role
- **Trending market:** RSI divergence = strong reversal signal
- **Ranging market:** RSI extremes = mean reversion signal
- **Transition:** Wait for confirmation, jangan masuk blind

### Anti-Pattern
- ❌ Jangan fade strong trend dengan RSI oversold (extended trend = RSI bisa stuck di extreme)
- ❌ Jangan entry RSI signal di regime transition

---

## 3️⃣ BOS/CHoCH Structure Dashboard

**Source:** [LuxAlgo Structure Dashboard](https://www.luxalgo.com/library/indicator/market-structure-scatter-dashboard/)
**Fungsi:** Konfirmasi structural break, filter noise

### Definitions
- **BOS (Break of Structure):** Price breaks previous swing high/low, confirming trend continuation
- **CHoCH (Change of Character):** First structural break against prevailing trend (potential reversal)

### Logic
- Track swing highs/lows (fractal-based)
- Mark BOS saat close beyond previous swing
- Mark CHoCH saat structural break opposite to trend
- Quadrant scatter: impulse vs pullback percentage

### Confluence Role
- **Long entry:** BOS of recent high + pullback to demand zone
- **Short entry:** BOS of recent low + pullback to supply zone
- **No entry:** Choppy structure tanpa clear BOS/CHoCH

### Best Timeframe
- Higher timeframe (4H, Daily) untuk structural bias
- Lower timeframe (15m, 1H) untuk entry trigger

---

## 4️⃣ WaveTrend Oscillator

**Source:** [LuxAlgo WaveTrend](https://www.luxalgo.com/library/) (LazyBear origin)
**Backtest LuxAlgo:** PF 2.2, WR 67% (ETH/USD 15m)
**Fungsi:** Momentum exit timing, precision TP

### Logic
- Calculate ESA (EMA of close)
- Calculate D (EMA of abs(close - ESA))
- CI = (close - ESA) / (0.015 * D)
- WT1 = EMA(CI, 10) — fast line
- WT2 = SMA(WT1, 4) — slow line

### Signals
- **Cross above oversold (-60):** Potential long
- **Cross below overbought (60):** Potential short
- **Cross zero line:** Momentum shift

### Confluence Role
- **Entry trigger:** Wait for WaveTrend cross confirmation
- **Exit timing:** Take profit saat WaveTrend reaches extreme + divergence
- **Trend strength:** WaveTrend > 0 = bullish momentum, < 0 = bearish

### Best For
- Scalping (5m, 15m)
- Intraday momentum trades
- Exit timing di position yang sudah profit

---

## 🎯 Confluence Combinations

### A+ Setup (4/4) — Best Odds
- Luminance breakout (volume confirm)
- RSI regime aligned dengan direction
- BOS confirming trend
- WaveTrend cross trigger

**Action:** Full size + 1.5x leverage (kalau futures)

### Valid Setup (3/4) — Normal Entry
Pilih 3 dari 4 yang align. Contoh:
- Luminance + BOS + WaveTrend (skip RSI kalau ranging market)
- Luminance + RSI + BOS (skip WaveTrend di HTF trade)
- RSI + BOS + WaveTrend (skip Luminance kalau sideways)

**Action:** Normal size, conservative SL

### SKIP (≤ 2/4) — No Trade
**Action:** Stay out, save capital untuk better setup

---

## 🛡️ Risk Management Integration

Setiap signal HARUS disertai:

1. **Position sizing:** Risk 1-2% modal per trade
2. **SL placement:** Beyond structure, bukan angka random
3. **R:R minimum:** 1:2 (TP minimal 2x SL distance)
4. **Correlation guard (rolling):** Max 2 posisi correlated
   - Rolling Pearson ρ dari 90 daily candles (window ~3 bulan) → adaptif ke regime shift
   - Threshold: |ρ| ≥ 0.70 (1d timeframe). Single-linkage clustering → group terbentuk via chain correlation
   - Cache 5 menit TTL. Fallback ke static v0.7.0 map kalo DB/ data insufficient
   - Inversely correlated (ρ ≤ -0.70) tetap dihitung sebagai risky untuk portfolio sizing
5. **Time stop:** Exit jika tidak bergerak dalam 4 candle
6. **News filter:** Skip 30 menit sebelum/sesudah red news

---

## 📊 Backtest Validation

Sebelum live trading, setiap strategi combination WAJIB melalui backtest dengan **6 metrics**:

| Metric | Target | Calculation |
|--------|--------|-------------|
| Win Rate | > 50% | Wins / Total trades |
| Profit Factor | > 1.5 | Gross profit / Gross loss |
| Max Drawdown | < 20% | Peak-to-trough equity decline |
| Sharpe Ratio | > 1.5 | (Avg return - Risk-free) / Std dev |
| Avg R-Multiple | > 1.5R | Avg win size / Avg loss size |
| Expectancy | > 0 | (WR × avg_win) - ((1-WR) × avg_loss) |

**Minimum 100 trades** untuk statistical significance.

---

## 🔬 Research Sources

- [LuxAlgo Library](https://www.luxalgo.com/library/) — primary strategy source
- [LuxAlgo Backtest Results](https://www.luxalgo.com/features/backtesting/)
- [TradingView Indicators Backtest 2025](https://blog.pickmytrade.trade/best-tradingview-indicators-2025-backtest-results/)
- [Smart Money Concepts (SMC)](https://www.luxalgo.com/library/indicator/market-structure-scatter-dashboard/)

---

**Next Step:** Phase 2 — port semua 4 strategi ini ke Python dengan proper backtest.
