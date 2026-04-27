# NKON Battery Analysis Toolkit

A set of tools to scrape, analyse, visualise, and interactively explore battery listings from [nkon.nl](https://www.nkon.nl).

```
scrape.py   →   chart.py        (visualise scraped data)
scrape.py   →   builder.py      (find optimal pack configurations)
                builder.py  →   tui.py   (interactive explorer)
```

---

## Requirements

```bash
pip install requests beautifulsoup4 pandas plotly textual
```

---

## 1. scrape.py — Scrape battery listings

Fetches listing pages and each product's detail page, then outputs a table with voltage, capacity, energy density, price, and derived metrics. Tiered bulk pricing is captured per product where available.

### Basic usage

```bash
# Scrape a category URL, print markdown table to stdout
python scrape.py https://www.nkon.nl/en/rechargeable/li-ion.html

# Save to a file (format auto-detected from extension)
python scrape.py https://www.nkon.nl/en/rechargeable/li-ion.html -o li-ion.csv
python scrape.py https://www.nkon.nl/en/rechargeable/li-ion.html -o li-ion.json
python scrape.py https://www.nkon.nl/en/rechargeable/li-ion.html -o li-ion.md
```

### Filters

```bash
# List available chemistry/type filters for a page
python scrape.py https://www.nkon.nl/en/rechargeable.html --list-types

# Scrape only a specific type
python scrape.py https://www.nkon.nl/en/rechargeable.html --type lifepo4 -o lifepo4.csv

# Only in-stock items
python scrape.py https://www.nkon.nl/en/rechargeable/li-ion.html --in-stock -o li-ion-stock.csv

# Only out-of-stock items
python scrape.py https://www.nkon.nl/en/rechargeable/li-ion.html --out-of-stock -o li-ion-oos.csv
```

### Output columns

| Column                    | Description                                        |
|---------------------------|----------------------------------------------------|
| Name                      | Full product name                                  |
| Brand                     | Manufacturer                                       |
| Chemistry                 | e.g. LiFePO4, Li-ion, NiMH                        |
| Size                      | e.g. 18650, 21700, prismatic                       |
| Grade                     | OEM / retail / etc.                                |
| V                         | Nominal voltage (V)                                |
| Ah                        | Capacity (Ah)                                      |
| Wh                        | Energy (V × Ah)                                    |
| Price €                   | Unit price incl. VAT                               |
| Wh/€                      | Energy per euro (higher = better)                  |
| €/Wh                      | Cost per Wh                                        |
| Wh/kg                     | Gravimetric energy density                         |
| Wh/L                      | Volumetric energy density                          |
| C-rate                    | Max continuous discharge rate                      |
| Disch A                   | Max continuous discharge current (A)               |
| Weight g                  | Cell weight (g)                                    |
| Vol L                     | Cell volume (L)                                    |
| Bulk pricing (qty×€/unit) | Tiered quantity break prices, e.g. `10×€4.50 / 50×€4.20` |
| In stock                  | yes / no                                           |

---

## 2. chart.py — Interactive HTML charts

Reads a CSV from `scrape.py` and generates a self-contained HTML file with four interactive Plotly tabs.

```bash
python chart.py li-ion.csv
# → li-ion.html

python chart.py li-ion.csv output.html
```

### Tabs

| Tab | What it shows |
|-----|---------------|
| **Wh/€ ranking** | Horizontal bar chart sorted by value (best deal at bottom) |
| **Ragone plot** | Wh/kg vs Wh/L scatter; bubble size encodes Wh/€ |
| **Price vs Wh** | Scatter of price vs capacity; filled = in stock, open = out of stock |
| **C-rate vs value** | Application fit (drain vs energy cell) against cost efficiency; quadrant labels |

Hover any data point for full cell details. The chart fills the browser window and resizes with it.

---

## 3. builder.py — Pack configuration finder

Given a target voltage and energy capacity, finds all valid series/parallel cell combinations that meet the requirement. Automatically applies the best available bulk tier price for the number of cells each configuration requires.

```bash
python builder.py li-ion.json --voltage 48 --wh 4096
python builder.py lifepo4.csv --voltage 48 --wh 4096 --in-stock -o result.html
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--voltage V` | required | Target pack voltage (V) |
| `--wh W` | required | Target pack energy (Wh) |
| `--tolerance T` | 0.15 | Allowed voltage deviation (fraction, e.g. 0.15 = ±15%) |
| `--wh-tolerance T` | 1.0 | Max Wh overshoot above target (fraction, e.g. 1.0 = +100%) |
| `--in-stock` | off | Only use in-stock cells |
| `-o FILE` | stdout/md | Output file (`.html`, `.csv`, `.json`, `.md`) |

### How configurations are found

- `Ns = round(target_V / cell_V)` — cells in series to hit voltage
- `Np = ceil(target_Ah / cell_Ah)` — strings in parallel to hit capacity
- Voltage checked within `±tolerance`
- Pack Wh checked to not exceed `target_Wh × (1 + wh_tolerance)`
- **Tier pricing**: for each config the best bulk break price for `Ns × Np` cells is used automatically; savings vs unit price are reported
- Pack discharge A = `Np × cell_discharge_A`
- Sorted cheapest first by default

### HTML output tabs

| Tab | What it shows |
|-----|---------------|
| **Cost ranking** | Bar chart of total pack cost, cheapest first |
| **Cost vs Power** | Scatter of pack price vs peak kW output |
| **Value vs Discharge** | Wh/€ vs discharge current with quadrant labels |
| **Density** | Wh/L vs Wh/kg; bubble size ∝ 1/price |

---

## 4. tui.py — Interactive pack builder TUI

A full-screen terminal UI for exploring pack configurations in real time. Wraps `builder.py` with live filtering, sortable columns, column visibility control, a custom computed column, and persistent settings.

```bash
python tui.py lifepo4.json
python tui.py li-ion.csv
python tui.py batteries.json
```

### Interface overview

```
┌──────────────────────────────┬────────────────────────────────────────────┐
│  Sidebar                     │  Results table (click header to sort)      │
│                              │                                            │
│  Voltage (V)                 │  # │Config │Cells│Pack V│Total €│Tier saved│
│  Target Wh                   │  1 │15S×3P │  45 │ 48.0 │ €1420 │  €18.00  │
│  V tolerance (%)             │  2 │15S×1P │  15 │ 48.0 │  €520 │    —     │
│  Wh overshoot cap (%)        │  ...                                       │
│  Chemistry  [✓ LiFePO4]      ├────────────────────────────────────────────┤
│             [✓ Li-ion ]      │  Detail panel (selected row)               │
│             [  NiMH   ]      │  Cell · config · pack specs · tier info    │
│  In stock only  [ ]          │                                            │
│  [Build]  [Clear]            │                                            │
│                              │                                            │
│  ── Custom column ──         │                                            │
│  Column A  [Total €      ]   │                                            │
│  Operation [÷  divide    ]   │                                            │
│  Column B  [Pack disch A ]   │                                            │
│  [Apply custom column    ]   │                                            │
│  [Remove custom column   ]   │                                            │
└──────────────────────────────┴────────────────────────────────────────────┘
```

### Controls

| Key / Action | Effect |
|---|---|
| `b` | Build / rebuild with current parameters |
| `v` | Open column visibility picker |
| `e` | Export HTML (Plotly charts) |
| `c` | Export CSV |
| `m` | Export Markdown |
| `q` | Quit |
| Click column header | Sort by that column (click again to reverse) |
| Click row | Show full details in the bottom panel |

### Parameters (sidebar)

- **Voltage (V)** — target pack nominal voltage; rebuilds on change
- **Target Wh** — minimum pack energy; rebuilds on change
- **V tolerance (%)** — how far pack voltage may deviate (default 15%)
- **Wh overshoot cap (%)** — max excess Wh above target (default 100%)
- **Chemistry** — checklist; tick any combination of chemistries to filter (all ticked = no filter)
- **In stock only** — exclude out-of-stock cells

### Tier pricing

The **Tier saved** column shows how much cheaper the pack is compared to buying at unit price, because the configuration requires enough cells to qualify for a bulk discount. The detail panel shows the exact tier threshold applied and the per-cell price reduction.

### Column visibility

Press `v` to open a checklist of all table columns. Untick any column to hide it. Changes take effect immediately and are persisted to the config file.

### Custom column

Pick any two numeric columns and a math operation to create a derived metric on the fly. Examples:

| Expression | Insight |
|---|---|
| Total € ÷ Pack disch A | Cost per amp of discharge |
| Tier saved ÷ Total € | Fraction of pack cost saved via bulk pricing |
| Wh/€ × Pack disch A | Value-weighted power |
| Total € ÷ Pack Wh | Effective €/Wh for the whole pack |

Select Column A, Operation, and Column B, then press **Apply**. The table sorts by the new column automatically. Click the header to toggle sort direction. Press **Remove** to clear it.

### Persistent settings

All sidebar values, column visibility, and chemistry filter are saved automatically to `{data_stem}.config.json` next to the data file. They are restored on the next launch.

### Export filenames

Exports are saved alongside the input data file:

```
{stem}_pack_{V}V_{Wh}Wh.html / .csv / .md
```

---

## Typical workflow

```bash
# 1. Scrape all batteries (all chemistries, all stock states)
python scrape.py https://www.nkon.nl/en/rechargeable.html -o batteries.json

# 2. Explore interactively — filter chemistry, sort by Tier saved, build custom columns
python tui.py batteries.json

# 3. Or visualise the raw scraped data
python scrape.py ... -o batteries.csv
python chart.py batteries.csv

# 4. Build a specific pack non-interactively
python builder.py batteries.json --voltage 48 --wh 4096 \
    --in-stock -o pack_48v_4kwh.html
```
