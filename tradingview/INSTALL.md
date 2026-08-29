# Cara Install Pine Script RX-0 Unicorn di TradingView

RX-0 Unicorn adalah **2 custom indicator** (bukan strategi) yang sengaja
dirancang untuk **TradingView Free Tier** (max 2 indicator/chart).
Install keduanya = setup maksimal RX-0 Unicorn di chart.

> ⏱ Total setup: ~3 menit. Cukup sekali, indicator akan tersedia di
> workspace Anda selamanya.

---

## 📋 Prasyarat

- Akun TradingView (gratis cukup). Daftar di [tradingview.com](https://tradingview.com) kalau belum.
- Browser modern (Chrome / Firefox / Edge / Safari).
- File `.pine` dari folder `tradingview/` di repo ini (sudah Anda clone
  atau download). Ada 2 file:
  - `rx0-confluence.pine` (chart overlay — main)
  - `rx0-momentum.pine` (pane terpisah)

---

## 🚀 Step-by-step (untuk tiap script)

### 1. Buka Pine Editor

1. Login ke [tradingview.com](https://tradingview.com) → klik **Charts**.
2. Pilih pair (misal **BTCUSDT**) dan timeframe (misal **1H**).
3. Di panel bawah chart, klik tab **"Pine Editor"**
   (pojok kanan-bawah, di samping "Trading Panel").
4. Pastikan editor **kosong / blank script**. Kalau ada code default,
   select all + delete.

> 💡 Tidak bisa menemukan Pine Editor? Klik **"Open in Pine Editor"**
> dari menu produk → Custom Indicators di sidebar.

### 2. Paste kode

1. Buka file `rx0-confluence.pine` di text editor (VS Code, Notepad, dll).
2. **Select all** (`Ctrl/Cmd+A`) → **Copy** (`Ctrl/Cmd+C`).
3. Kembali ke Pine Editor di browser → **Paste** (`Ctrl/Cmd+V`).

### 3. Save & Add to chart

1. Klik tombol **"Save"** (icon disket, atau `Ctrl/Cmd+S`).
   - Pertama kali akan diminta nama script. Misal: `RX-0 Confluence (Mine)`.
2. Klik **"Add to chart"** (tombol di sebelah Save, atau menu indicator).
3. Indicator muncul di chart dengan tabel info kanan-atas.

🎉 Selesai untuk script pertama. **Ulangi step 1-3** untuk
`rx0-momentum.pine`. Karena free plan = max 2 indicator/chart, Anda akan
mendapat prompt untuk replace atau add to different chart — pilih
**"Add to current chart"**.

---

## 🧭 Setup yang direkomendasikan

Buka **BTCUSDT** (atau pair favorit Anda) di timeframe **1H** atau **4H**
sesuai `STRATEGY.md`, lalu tambahkan:

| Script                  | Posisi                  | Tujuan                                                                  |
| ----------------------- | ----------------------- | ----------------------------------------------------------------------- |
| `rx0-confluence` ⭐     | Main chart (overlay)    | Breakout arrows (Luminance) + BOS/CHoCH labels + scoring tabel         |
| `rx0-momentum`          | Pane bawah (terpisah)   | RSI Wilder + ADX regime + WaveTrend WT1/WT2 + zone tint                |

⭐ Cukup load `rx0-confluence` saja kalau cuma ingin lihat sinyal
entry (A+/Valid). Tambah `rx0-momentum` di pane bawah untuk lihat
konteks oscillator + regime.

> ⚠️ Free TradingView batasi **2 indicator/chart**. RX-0 Unicorn
> sengaja dibikin 2 file supaya pas dengan limit tersebut. Kalau Anda
> perlu lebih (misal BOS/CHoCH terpisah), upgrade ke Pro.

---

## 🔔 Setup Alerts

1. Klik kanan di chart → **"Add Alert…"** (atau icon 🔔 di toolbar).
2. **Condition:** pilih indicator:
   - `RX-0 Confluence (Main)` → events: `A+ Long`, `A+ Short`, `Valid Long`, `Valid Short`
   - `RX-0 Momentum` → events: `Regime Change`, `WT Oversold Cross`, `WT Overbought Cross`, `RSI Long/Short`
3. **Expirations:** "Open-ended" (recommended) atau sesuai kebutuhan.
4. **Notifications:** centang `Show pop-up`, `Send email`. (Webhook
   butuh plan Pro — lihat tabel di bawah.)
5. Klik **"Create"**.

Alert sekarang akan fire setiap kali event trigger di chart aktif.

---

## 🔧 Customizing parameters

Semua parameter (range length, RSI period, ADX threshold, dst.) bisa
diubah **tanpa edit code**:

1. Klik ⚙️ **Settings** di sebelah nama indicator di chart legend
   (pojok kiri-atas chart, klik nama script).
2. Panel input muncul → geser-geser nilai.
3. Klik OK → chart re-render otomatis.

Default cocok untuk setup `STRATEGY.md`. Ubang hanya kalau Anda sudah
re-backtest dengan parameter baru (lihat `backtest/` folder).

---

## ❓ Troubleshooting

| Masalah                                | Solusi                                                                       |
| -------------------------------------- | ---------------------------------------------------------------------------- |
| "Syntax error at line X"               | Copy-paste ulang (kadang ada karakter tak terlihat). Atau buka file `.pine` di editor ASCII murni (VS Code), save as, lalu paste ulang. |
| "Too many indicators" (Free tier)      | Hapus indicator lain yang tidak Anda pakai. TradingView Free limit = 2. Upgrade ke Pro. |
| Background warna tidak muncul          | Pastikan `bgcolor()` di-enable. Cek Settings → "Style" tab → "Background". |
| Tabel kanan-atas overlap dengan price scale | Drag chart atau zoom out. Bisa juga pindah ke `position.top_left` (edit `.pine`). |
| Confluence score selalu 0              | Mungkin pair/timeframe sepi (low volatility). Coba pair lain atau TF lebih kecil. |
| Tidak ada pane terpisah saat add `rx0-momentum` | Klik kanan indicator di legend → "Move to new pane" (atau Settings → "Pane" dropdown). |
| Indikator "RX-0 Momentum" sinyal RSI/WT tidak match dengan chart | Pastikan timeframe chart = timeframe yang dipakai Python bot. Indikator Pine hitung real-time, sedangkan backtest mungkin beda. Cocokkan arah (long/short), bukan nilai exact. |

---

## 🆚 Gratis vs Berbayar

| Fitur                                | Free Plan        | Paid Plan        |
| ------------------------------------ | ---------------- | ---------------- |
| Custom Pine Script (indicator)       | ✅               | ✅               |
| **Max indicator per chart**          | **2**            | Unlimited        |
| Multiple charts (different pairs)    | ✅               | ✅               |
| Alert via email                      | ✅               | ✅               |
| Alert via webhook ke bot             | ❌               | ✅               |
| Multi-timeframe                      | Terbatas         | ✅               |
| Replay mode                          | ❌               | ✅               |

**RX-0 Unicorn sengaja didesain 2 file = pas dengan Free Plan 2-indicator
limit.** Cukup free plan untuk **visualisasi + manual review**.

> Note: RX-0 Unicorn tidak pakai webhook TradingView untuk live trading —
> pakai `ccxt` direct ke exchange (Phase 7). Jadi free plan cukup.

---

## 📚 Referensi

- [Pine Script v5 Reference](https://www.tradingview.com/pine-script-reference/v5/)
- [Pine Script User Manual](https://www.tradingview.com/pine-script-docs/en/v5/)
- [STRATEGY.md](../STRATEGY.md) — framework confluence lengkap
- [confluence/scorer.py](../confluence/scorer.py) — Python reference scoring

---

Selamat nge-chart! 🚀
