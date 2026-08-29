# RX-0 Unicorn — TradingView Indicators

Visualisasi strategi **RX-0 Unicorn** dalam bentuk Pine Script v5, supaya
bisa di-overlay di chart TradingView sebagai konfirmasi visual sinyal
bot Python (`main.py scan`).

> **Bukan untuk live trading.** Script ini `indicator()` (bukan
> `strategy()`) — murni visualisasi + alert. Phase 7 (live execution) akan
> pakai `ccxt`/exchange API, bukan TradingView.

---

## 🎯 2-file plan (TradingView Free Tier)

TradingView Free membatasi **2 custom indicator per chart**. Kami rancang
RX-0 Unicorn menjadi 2 script yang saling melengkapi, cukup untuk lihat
keseluruhan framework:

| Pine Script              | Tipe                       | Isi                                                                                  | Python equivalent                                          |
| ------------------------ | -------------------------- | ------------------------------------------------------------------------------------ | ---------------------------------------------------------- |
| `rx0-confluence.pine` ⭐  | **Chart overlay** (main)   | Luminance Breakout + BOS/CHoCH Structure + Confluence scoring + info table          | `confluence/scorer.py` + `indicators/luminance.py` + `structure.py` |
| `rx0-momentum.pine`      | **Pane terpisah**          | RSI Wilder + ADX regime + WaveTrend LazyBear + zone tint + mini table                | `indicators/rsi_regime.py` + `indicators/wavetrend.py`     |

⭐ = **MAIN**. Cukup load `rx0-confluence.pine` saja untuk lihat
breakout arrow + structure labels + scoring tabel. Tambah
`rx0-momentum.pine` di pane terpisah untuk lihat oscillator momentum
+ regime classification.

### Kenapa digabung jadi 2?

- **Free tier** TradingView = max 2 indicator/chart → pasang tepat
  keduanya sudah merupakan setup maksimal.
- **Logical split**: chart-overlay (price action + structure) vs
  momentum pane (oscillators). Ini pemisahan yang umum di workstation
  trader.
- **Confluence scoring 3/3** di script overlay (Luminance + Structure +
  RSI-trend bonus) adalah equivalent dari 4/4 di Python dengan WaveTrend
  dikonfirmasi manual dari pane momentum.

---

## 🧠 Confluence scoring (skala 0-3 di `rx0-confluence.pine`)

Setiap indikator (Luminance, Structure, RSI-trend) mengeluarkan sinyal
-1 / 0 / +1 per bar. Tiap bar:

1. Hitung `long_count` (jumlah indikator = +1) dan `short_count` (-1).
2. Arah = sisi dengan hitungan lebih banyak. Equal → `None` (SKIP).
3. Score = jumlah yang align ke arah (0 sampai 3).
4. Grade:

   | Score | Grade  | Size Multiplier | Visual                                |
   | ----- | ------ | --------------- | ------------------------------------- |
   | 0–1   | skip   | 0.0x            | (tidak ada marker / background)       |
   | 2     | valid  | 1.0x            | Hijau muda, marker "V▲/V▼"           |
   | 3     | A+     | 1.5x            | Hijau tua, marker "A+▲/A+▼"         |

> Skor asli Python adalah 0-4 (dengan WT sebagai indikator ke-4). Di
> Pine, WT berada di pane terpisah → scoring jadi 0-3 dengan threshold
> "A+ = 3/3" (semua chart-overlay align) atau "valid = 2/3". Anda bisa
> tune di Settings (input "Min Score for A+ / Valid").

---

## 📐 Methodology — mapping Python ke Pine

Setiap Pine script adalah port langsung dari modul Python-nya, dengan
penyesuaian idiomatic Pine v5:

- **No look-ahead:** Range boundary, swing points, dan RSI Wilder
  di-`[1]` atau lewat `ta.rma`/`ta.ema` (sama dengan pandas `ewm`).
- **Wilder smoothing:** `ta.rma(...)` (alpha = 1/period) identik
  dengan `_wilder_rma` di Python (`ewm alpha=1/n`).
- **LazyBear WaveTrend:** `ta.ema(span=n)` bukan `ta.rma`, sesuai
  `ap.ewm(span=channel_len, ...)` di Python.
- **Fractal swing:** `ta.pivothigh/low(left, right)` identik dengan
  `rolling(window, center=True).max() == high` di Python.
- **Confluence score:** tabel kebenaran `long_count > short_count` dst.,
  sama dengan `_score_direction()` di `confluence/scorer.py`.

Default parameter identik dengan Python (lihat `STRATEGY.md`):
range_lookback=20, vol_mult=1.5, RSI=14, ADX=14 (threshold 25),
fractal=2+2 (ditingkatkan ke 5+5 untuk chart yang lebih bersih), WT
10/21/4. Semua bisa di-override di Settings panel.

---

## 🔍 Yang akan Anda lihat di chart

Load kedua script di BTCUSDT 1H:

### Chart (overlay) — `rx0-confluence.pine`

- **Background tint** per grade (hijau/merah).
- **Triangle "A+▲/A+▼"** saat 3/3 chart-overlay align.
- **"V▲/V▼"** lebih kecil saat 2/3.
- **Range box hijau-teal transparan** menandai zona konsolidasi Luminance.
- **Arrow "Lum▲/Lum▼"** di breakout bar (Luminance + volume confirm).
- **Label "BOS" (biru) & "CHoCH" (oranye)** di swing break.
- **Info table kanan-atas**: Score X/3 | Grade | Direction | Regime |
  raw signals (Lum/Str/RSI).

### Pane terpisah (bawah) — `rx0-momentum.pane`

- **Garis biru (WT1 fast) & merah (WT2 slow)** WaveTrend.
- **Garis teal/abu (RSI 14)** dengan warna regime-aware.
- **Garis oranye step-line (ADX 14)** dengan hline threshold 25.
- **Background tint**: biru = trending, abu = ranging, hijau/merah di
  zona WT oversold/overbought.
- **Triangle "WT▲/WT▼" & "R▲/R▼"** untuk cross events.
- **Info table**: Regime | RSI | WT1 | ADX real-time.

Cocokkan dengan output `main.py scan`:
- Ticker BTCUSDT, score 4/4, grade A+, direction long, regime trending
  → di chart: tabel kanan-atas chart overlay menunjukkan score 3/3
  (setara A+) + "A+▲" marker; tabel pane momentum menunjukkan regime
  trending, RSI > 50, WT1 baru cross up di zona bawah.

---

## 🚦 Alerts

| Script              | Alert conditions                                              |
| ------------------- | ------------------------------------------------------------- |
| `rx0-confluence`    | A+ long, A+ short, valid long, valid short                    |
| `rx0-momentum`      | Regime change, WT oversold cross, WT overbought cross, RSI long/short |

Cara setup: klik kanan di chart → "Add Alert…" → Condition: pilih
"RX-0 Confluence (Main)" atau "RX-0 Momentum" + event.

---

## 📂 Struktur folder

```
tradingview/
├── README.md            ← file ini
├── INSTALL.md           ← cara load ke TradingView (free tier setup)
├── rx0-confluence.pine  ← MAIN: chart overlay (Luminance + Structure + scoring)
└── rx0-momentum.pine    ← Pane terpisah: RSI + ADX + WaveTrend
```

## License

Private (internal RX-0 Unicorn). Lihat root `README.md`.
