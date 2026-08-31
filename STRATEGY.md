# 🧠 Strategi Detail — RX-0 Unicorn (XAU/USD Gold)

> **Deep dive ke 4 strategi LuxAlgo yang jadi pondasi RX-0 Unicorn — calibrated untuk gold daily volatility (XAU/USD spot, $3000-3500/oz range).**

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

> **Gold-tuned:** Gold trending behavior lebih reliable dari crypto chop → expected win rate **lebih tinggi** (target > 55% vs crypto > 50%). Confluence filter lebih ketat (3/4 minimum) karena gold less noisy.

---

## 1️⃣ Luminance Breakout Engine

**Source:** [LuxAlgo Library](https://www.luxalgo.com/library/indicator/luminance-breakout-engine/)
**Backtest LuxAlgo:** PF 2.33, WR 71.6% (gold-tuned expected: similar or higher — trending asset)
**Fungsi:** Detect breakout dengan volume confirmation

### Logic
- Identifikasi range/consolidation zone (20-bar lookback)
- Wait for breakout candle (close beyond range boundary)
- Confirm dengan volume spike (≥ 1.5x average)
- Filter: avoid false breakout di low-volume hours (Asian session)

### Parameters
- Range lookback: 20 bars
- Volume threshold: 1.5x
- Min consolidation bars: 5
- Timeframe optimal: 1H, 4H, 1D (semua reliable di gold)

### Entry Rules
- **Long:** Breakout above resistance + volume confirm
- **Short:** Breakout below support + volume confirm
- **SL:** Beyond range boundary (opposite side)
- **TP1:** 1R (1x risk)
- **TP2:** 2R (2x risk)

> **Gold-specific note:** Gold daily vol 1-2% — breakout dari 20-bar range pada 1D timeframe sering yield 1R dalam 1-3 hari. Volume confirmation CRUCIAL karena gold false-breakout sering saat thin Asian session.

---

## 2️⃣ RSI Regime Filter

**Source:** [LuxAlgo RSI Regime Filter](https://www.luxalgo.com/library/indicator/rsi-regime-filter/)
**Fungsi:** Validasi momentum, anti-fading runaway trend

### Logic
- Calculate RSI(14)
- Classify market regime: trending vs ranging (ADX > 25 = trending)
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

> **Gold-specific note:** Gold trending behavior lebih jelas dari crypto — RSI(14) regime filter jarang stuck di extreme tanpa reason. **DXY inverse correlation awareness:** Kalau DXY (USD index) sedang strong uptrend, gold bullish RSI signals jadi lower probability (inverse correlation -0.7 to -0.85).

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
- Higher timeframe (4H, 1D) untuk structural bias
- Lower timeframe (1H) untuk entry trigger

> **Gold-specific note:** Gold structure pada 1D timeframe SANGAT clean — major BOS/CHoCH sering mark trend reversal yang holds untuk weeks. Fractal-based detection robust untuk gold karena less manipulation vs crypto.

---

## 4️⃣ WaveTrend Oscillator

**Source:** [LuxAlgo WaveTrend](https://www.luxalgo.com/library/) (LazyBear origin)
**Backtest LuxAlgo:** PF 2.2, WR 67% (gold-tuned expected: similar)
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
- Intraday momentum trades (1H)
- Daily momentum confirmation (1D)
- Exit timing di position yang sudah profit

> **Gold-specific note:** Pada 1H timeframe, WaveTrend cross overbought/oversold sering kali excellent exit timing (gold respects momentum more than mean reversion di intraday). 15m timeframe di-drop karena Yahoo Finance 15m limited 60 hari history — tidak reliable untuk backtest multi-year.

---

## 🎯 Confluence Combinations

### A+ Setup (4/4) — Best Odds
- Luminance breakout (volume confirm)
- RSI regime aligned dengan direction
- BOS confirming trend
- WaveTrend cross trigger

**Action:** Full size + 1.5x position multiplier

### Valid Setup (3/4) — Normal Entry
Pilih 3 dari 4 yang align. Contoh:
- Luminance + BOS + WaveTrend (skip RSI kalau ranging market)
- Luminance + RSI + BOS (skip WaveTrend di HTF trade)
- RSI + BOS + WaveTrend (skip Luminance kalau sideways)

**Action:** Normal size (1.5% risk per trade), conservative SL

### SKIP (≤ 2/4) — No Trade
**Action:** Stay out, save capital untuk better setup

> **Gold-tuned note:** Gold punya tendency untuk trending moves yang hold — A+ setups pada XAU/USD daily sering yield 2R-3R dalam 3-7 hari. Confluence filter critical untuk avoid entering saat gold ranging.

---

## 🛡️ Risk Management Integration

Setiap signal HARUS disertai:

1. **Position sizing:** Risk **1.5% modal per trade** (gold-tuned, turun dari 2% crypto)
   - Alasan: gold daily vol 1-2% lebih tinggi dari rata-rata crypto daily move → sizing lebih konservatif
2. **SL placement:** Beyond structure + **ATR-based buffer** (Average True Range 14-period × 1.5)
3. **R:R minimum:** 1:2 (TP minimal 2x SL distance)
4. **Correlation guard (rolling):** **Disabled di single-pair mode (v1.0.0)**
   - Ready for re-enable saat XAG/USD (silver) + XPT/USD (platinum) expansion di v1.1.0+
   - Threshold akan: |ρ| ≥ 0.70 (1d timeframe). Single-linkage clustering → group via chain correlation
   - Inversely correlated (ρ ≤ -0.70, mis. gold vs DXY) tetap dihitung sebagai risky untuk portfolio sizing
5. **Time stop:** Exit jika tidak bergerak dalam 30 candles (1D timeframe = ~1 month)
6. **News filter:** Skip 30 menit sebelum/sesudah red forex news (NFP, FOMC, CPI)
7. **Market hours filter:** Only trade saat forex gold hours active (Sun 5pm ET → Fri 5pm ET)

### Position Sizing Example (XAU/USD)

**Scenario:** $10,000 account, 1.5% risk per trade, ATR(14) 1D = $30

```
Risk amount = $10,000 × 0.015 = $150 per trade
SL distance = ATR(14) × 1.5 = $30 × 1.5 = $45 (≈ 45 pips di 1 oz)
Position size = $150 / $45 = 3.33 oz

At $3,300/oz × 3.33 oz = ~$11,000 notional
TP2 (2R) = entry ± 90 pips = ± $90/oz → 3.33 × $90 = $300 profit
```

**Standard lot** = 100 oz. 3.33 oz = micro lot position. Conservative sizing untuk $10k account.

> **Gold volatility note:** Gold daily ATR bisa spike ke $50-80 saat event risk (NFP, FOMC). Position sizing adapts — kalau ATR(14) = $60, SL = $90, position size = $150/$90 = 1.67 oz (lebih kecil). Volatility-adjusted sizing protects dari event-driven drawdown.

---

## 📊 Backtest Validation

Sebelum live trading, setiap strategi combination WAJIB melalui backtest dengan **6 metrics**:

| Metric | Target | Calculation |
|--------|--------|-------------|
| Win Rate | **> 55%** (gold-tuned, up dari crypto > 50%) | Wins / Total trades |
| Profit Factor | > 1.5 | Gross profit / Gross loss |
| Max Drawdown | < 20% | Peak-to-trough equity decline |
| Sharpe Ratio | > 1.5 | (Avg return - Risk-free) / Std dev |
| Avg R-Multiple | > 1.5R | Avg win size / Avg loss size |
| Expectancy | > 0 | (WR × avg_win) - ((1-WR) × avg_loss) |

**Minimum 30 trades** untuk statistical significance (gold-tuned, turun dari 100 karena gold daily = fewer but higher-quality trades).

### Backtest Methodology (v1.0.0)

- **Primary timeframe: 1d** — Yahoo Finance reliable, gold trending, less noise vs 1h chop
- **Data source: Yahoo Finance GC=F** — CME gold futures proxy, tracks spot < 0.5% delta
- **Walk-forward simulation** — no look-ahead, indikator + signal dihitung di bar `t`, entry di `t+1`
- **Slippage + commission:** 0.05% slippage + 0.10% commission per side (realistic forex/commodity)
- **Time stop:** Max 30 candles (1D) = ~1 bulan hold period
- **Sizing:** 1.5% modal per trade (gold-tuned)
- **A+ multiplier:** 1.5x position size untuk 4/4 confluence setups

### Kenapa 1D Primary?

1. **Yahoo Finance reliability** — 1D timeframe unlimited history, 4h aggregated (potential error), 1h max 730 hari
2. **Gold trending behavior** — Daily candles lebih clean, less noise vs 1h chop
3. **Backtest statistical power** — Multi-year daily data > 730 hari hourly untuk statistical significance
4. **Walk-forward friendly** — Daily candle lebih stabil untuk out-of-sample validation
5. **Forex market hours alignment** — 1D candle capture full 23-hour trading session (Sun 5pm ET → Fri 5pm ET)

---

## 🕐 Market Hours (NEW v1.0.0)

Forex gold hours berbeda dari crypto 24/7 — predictable trading window critical untuk strategy validation.

### Forex Gold Trading Hours (Eastern Time)

| Day | Open | Close | Status |
|-----|------|-------|--------|
| **Sunday** | 5:00 PM ET | Open | Session start (gold futures re-open) |
| **Monday-Thursday** | Open (continuous) | 5:00 PM ET | Active trading |
| **Monday-Thursday** | 5:00 PM ET | 6:00 PM ET | **Maintenance window** (no trading) |
| **Monday-Thursday** | 6:00 PM ET | Next day 5:00 PM ET | Active trading |
| **Friday** | Open (continuous) | 5:00 PM ET | Session close |
| **Saturday** | Closed | Closed | **No trading** |

### Key Facts
- **Total active hours:** ~23 jam/hari (Mon-Fri) — gap hanya 1 jam daily maintenance
- **Most liquid sessions:** London (3am-12pm ET) + New York (8am-5pm ET) overlap = 8am-12pm ET
- **Thin sessions:** Asian (5pm-3am ET), Friday afternoon post-2pm ET (volume drop ahead of weekend close)
- **Maintenance:** Daily 5-6pm ET (1 hour) — Yahoo Finance GC=F candles mungkin missing/aggregated differently

### Impact ke Strategy

1. **Paper trading filter:** `paper_daemon.py` skip trades di luar forex gold hours
2. **Backtest filter:** `backtest/run_yearly.py` exclude candles dari maintenance window
3. **Live trading (Phase 7):** Order execution disabled saat closed
4. **News scheduling:** NFP, FOMC biasanya release 8:30am ET (London/NY overlap) — highest volatility window

### Implementation

```python
from src.market_hours import is_forex_gold_open

if is_forex_gold_open(now_utc):
    # Allow trade execution
    execute_signal(signal)
else:
    # Queue signal untuk next valid session
    queue_signal(signal, valid_from=next_open_et)
```

> **Gold-specific note:** Forex market hours filter SANGAT penting untuk paper trading validation. Crypto 24/7 = noise from dead hours. Gold has predictable active hours → cleaner signal quality. Real-time trading wajib respect market hours untuk avoid slippage dari thin Asian session.

---

## 🔬 Research Sources

- [LuxAlgo Library](https://www.luxalgo.com/library/) — primary strategy source
- [LuxAlgo Backtest Results](https://www.luxalgo.com/features/backtesting/)
- [TradingView Indicators Backtest 2025](https://blog.pickmytrade.trade/best-tradingview-indicators-2025-backtest-results/)
- [Smart Money Concepts (SMC)](https://www.luxalgo.com/library/indicator/market-structure-scatter-dashboard/)
- [Yahoo Finance GC=F Contract Specs](https://www.cmegroup.com/markets/metals/precious/gold.html) — gold futures reference
- [Forex Market Hours](https://www.forexmarkethours.com/) — gold trading window
- [Gold-DXY Correlation Analysis](https://www.tradingview.com/symbols/TVC-DXY/) — inverse correlation reference

---

**Next Step:** Phase 7 — live trading execution dengan forex broker API atau CME futures, respecting forex market hours + 1.5% risk sizing + ATR-based SL/TP calibration dari strategy ini.