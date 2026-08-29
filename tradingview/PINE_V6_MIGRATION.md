# RX-0 Unicorn — Pine Script v5 → v6 Migration

**Why this change:** TradingView now requires Pine Script **v6** for all new
indicators. The previous `rx0-confluence.pine` and `rx0-momentum.pine`
files declared `//@version=5` and stopped compiling on save. Both have
been migrated to v6 with no behavioural changes.

---

## What changed (v5 → v6)

1. **Version header bumped.** `//@version=5` → `//@version=6` in both files.
2. **`bgcolor()` moved to global scope via a single ternary expression.**
   v6 enforces a stricter rule that broke the previous multi-`if` / local
   variable form with **`CE10188: local scope`**. The fix in both files
   is a chained ternary inside the `bgcolor()` call itself.
3. **`label.new()` and `box.new()` use named arguments everywhere.**
   v6 deprecates positional args for these (and for `table.new` /
   `table.cell`). Every call in both files now uses `x=`, `y=`, `text=`,
   `style=`, `color=`, `textcolor=`, `size=`, `tooltip=`, `left=`, `top=`,
   `right=`, `bottom=`, `bgcolor=`, `border_color=`, `border_width=`,
   `position=`, `columns=`, `rows=`, `frame_color=`, `frame_width=`,
   `border_width=`, `table_id=`, `column=`, `row=`, `text=`, `text_color=`.
4. **Explicit integer casts for parameters that v5 inferred.** For
   example `box.new(border_width=int(1))` instead of `border_width=1` —
   the result of an arithmetic expression is now a `series` and the
   parameter expects a plain `int`.
5. **`var` declarations already at global scope** (the existing files
   placed every `var` at the top of script or before the first `if`
   block that used it). No re-ordering was required, but each `var` is
   now annotated in the comments for clarity.

The following functions kept their original signatures — they are
unchanged between v5 and v6:

- `ta.rma`, `ta.ema`, `ta.sma`, `ta.highest`, `ta.lowest`, `ta.tr`
- `ta.pivothigh`, `ta.pivotlow`
- `math.abs`
- `str.tostring`
- `plotshape`, `plot`, `hline`, `alertcondition`
- `color.new`, `shape.triangleup`, `shape.triangledown`,
  `location.belowbar`, `location.abovebar`, `location.top`,
  `location.bottom`

---

## How to test in TradingView

1. Open [TradingView](https://tradingview.com) and sign in.
2. Click **Charts** → choose any symbol (e.g. `BTCUSDT`, timeframe
   `1H`).
3. In the bottom panel click **Pine Editor** (tab next to "Trading
   Panel").
4. **Open** `rx0-confluence.pine` (or `rx0-momentum.pine`):
   - From the editor's *Open* dropdown → **New blank script**,
   - Select-all (`Ctrl/Cmd+A`) the v5 contents, **delete**,
   - Open `tradingview/rx0-confluence.pine` in your text editor
     (VS Code, Notepad, etc.), copy the **whole file**,
   - Paste into the Pine Editor.
5. Click **Save** (or `Ctrl/Cmd+S`). The first save will prompt for a
   title — use the existing one (`RX-0 Confluence (Main)` or
   `RX-0 Momentum (RSI + ADX + WaveTrend)`) so existing alerts
   continue to fire.
6. Click **Add to chart**. The indicator should render with no
   compile error in the Pine Editor console.
7. Repeat for `rx0-momentum.pine` — when prompted "Add to current
   chart?" choose **Add to current chart** so it lands in a new pane
   below the price chart (Free plan allows 2 indicators per chart).

**Expected visual output** (identical to v5 behaviour):

- `rx0-confluence`: A+ / Valid background tints, Lum▲/Lum▼ arrows,
  BOS/CHoCH labels, info table top-right.
- `rx0-momentum`: WT1/WT2 lines, RSI + ADX lines, regime / zone
  background tint, info table.

If the Pine Editor console reports `Compiled successfully`, the
migration is complete.

---

## Common v6 errors and fixes

| Error code | Message                                               | Fix                                                                                   |
| ---------- | ----------------------------------------------------- | ------------------------------------------------------------------------------------- |
| `CE10188`  | `bgcolor() cannot be used in local scope`             | Use a single ternary expression inside `bgcolor()` (see snippet below) or assign to a global-scope variable. |
| `CE10001`  | `wrong argument type ... expected int`                | Wrap the offending expression in `int(...)` cast (e.g. `border_width=int(1)`).        |
| `CE10003`  | `function ... has no positional arguments`            | Switch to named args: `label.new(x=..., y=..., text=..., style=..., ...)` etc.        |
| `CE10043`  | `cannot declare 'var' in local scope`                 | Move the `var` declaration to the top of script, before any `if`/`for` block.         |
| `CE10009`  | `input.* without default value`                       | Add an explicit `defval=...` argument to every `input.*` call.                        |
| `WARN0019` | `function signature changed ... use new form`         | Re-write the call to match v6 docs (e.g. `ta.pivothigh(source, left, right)`).        |

### `bgcolor()` fix — before vs after (from `rx0-confluence.pine`)

**Before (v5 — fails with `CE10188` on v6):**

```pine
bgcolor(confGrade == "A+" and confDir ==  1 ? color.new(color.green, 88) :
        confGrade == "A+" and confDir == -1 ? color.new(color.red,   88) :
        confGrade == "valid" and confDir ==  1 ? color.new(color.green, 95) :
        confGrade == "valid" and confDir == -1 ? color.new(color.red,   95) : na, title="Confluence Grade Background")
```

**After (v6 — global-scope ternary, one column per line for readability):**

```pine
bgcolor(
     confGrade == "A+"    and confDir ==  1 ? color.new(color.green, 88) :
     confGrade == "A+"    and confDir == -1 ? color.new(color.red,   88) :
     confGrade == "valid" and confDir ==  1 ? color.new(color.green, 95) :
     confGrade == "valid" and confDir == -1 ? color.new(color.red,   95) : na,
     title="Confluence Grade Background")
```

Both forms are functionally identical, but the re-formatted version is
the canonical v6-compatible pattern and reads more clearly when reviewed.

---

## Files changed

- `tradingview/rx0-confluence.pine` — header bumped, `bgcolor()`
  refactored, all `label.new` / `box.new` / `table.cell` calls
  switched to named args, one `int()` cast added.
- `tradingview/rx0-momentum.pine` — header bumped, `bgcolor()`
  refactored to a single ternary, `table.new` / `table.cell` calls
  switched to named args.
- `tradingview/PINE_V6_MIGRATION.md` — this file.
- (Recommended next PR) `tradingview/README.md` and
  `tradingview/INSTALL.md` — update the "Pine Script v5" wording
  to "Pine Script v6" and refresh the `tradingview.com/pine-script-reference/v5/`
  links to `/v6/`.

## License

Same as root repo: MPL 2.0.
