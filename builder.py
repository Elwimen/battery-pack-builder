#!/usr/bin/env python3
"""Battery pack configurator.

Reads cell data exported by scrape.py (JSON, CSV, or Markdown) and calculates
series/parallel configurations to meet a target voltage and energy.

Usage:
    python builder.py --voltage 48 --wh 4096 data.json
    python builder.py --voltage 48 --wh 4096 data.csv --in-stock -o packs.html
    python builder.py --voltage 24 --wh 1000 data.md -o result.md

Pack geometry
─────────────
  Ns  cells in series   → pack voltage = Ns × V_cell
  Np  groups in parallel → pack Ah     = Np × Ah_cell
  Pack discharge A       = Np × cell_discharge_A
  Pack max power (kW)    = pack_discharge_A × V_pack / 1000
  Total cells = Ns × Np
"""

import argparse
import csv
import io
import json
import math
import re
import sys
from pathlib import Path


# ── loaders ───────────────────────────────────────────────────────────────────

NUMERIC = ["voltage_v", "capacity_ah", "wh", "price_incl",
           "wh_per_eur", "wh_per_kg", "wh_per_l", "c_rate",
           "weight_g", "vol_l", "discharge_a"]

COL_RENAME = {
    "V": "voltage_v", "Ah": "capacity_ah", "Wh": "wh",
    "Price €": "price_incl", "Wh/€": "wh_per_eur",
    "Wh/kg": "wh_per_kg", "Wh/L": "wh_per_l",
    "C-rate": "c_rate", "Weight g": "weight_g",
    "Vol L": "vol_l", "Disch A": "discharge_a",
    "Name": "name", "Brand": "brand", "Model": "model",
    "Chemistry": "chemistry", "Size": "size",
    "Form factor": "form_factor", "Protection": "protection",
    "Grade": "grade", "Terminal": "terminal",
    "Dims mm": "dims_mm", "Included": "included",
    "Year": "year", "In stock": "in_stock",
    "Bulk pricing (qty×€/unit)": "tiers_raw",
}


def _parse_tiers_raw(s: str) -> list[dict]:
    """Parse '10×€5.99 / 50×€5.50' (scrape.py tier_str format) into a tier list."""
    if not s or s in ("—", ""):
        return []
    tiers = []
    for part in s.split(" / "):
        m = re.match(r"(\d+)[×x]€([\d.]+)", part.strip())
        if m:
            tiers.append({"qty": int(m.group(1)), "price_incl": float(m.group(2))})
    return tiers


def _best_tier_price(cell: dict, n_total: int) -> tuple[float, int | None]:
    """Return (price_per_cell, tier_qty_applied).

    Picks the cheapest qualifying tier for buying n_total cells.
    Falls back to unit price when no tier applies.
    """
    base = cell.get("price_incl") or 0.0
    tiers = cell.get("tiers") or []
    best_price = base
    best_qty: int | None = None
    for tier in sorted(tiers, key=lambda t: t["qty"]):
        if n_total >= tier["qty"] and tier["price_incl"] < best_price:
            best_price = tier["price_incl"]
            best_qty = tier["qty"]
    return best_price, best_qty


def _coerce(records: list[dict]) -> list[dict]:
    for r in records:
        for f in NUMERIC:
            v = r.get(f)
            if isinstance(v, str):
                v = v.strip().replace("—", "").replace(",", ".")
                try:
                    r[f] = float(v) if v else None
                except ValueError:
                    r[f] = None
            elif v == "":
                r[f] = None
        r["in_stock"] = str(r.get("in_stock", "")).lower() in ("true", "yes", "1")
        # Ensure tiers is always a list (JSON has it natively; CSV/MD have tiers_raw string)
        if not isinstance(r.get("tiers"), list):
            r["tiers"] = _parse_tiers_raw(r.get("tiers_raw", "") or "")
    return records


def _rename(rows: list[dict]) -> list[dict]:
    return [{COL_RENAME.get(k, k): v for k, v in r.items()} for r in rows]


def load_json(path: str) -> list[dict]:
    with open(path) as fh:
        data = json.load(fh)
    return _coerce(data)


def load_csv(path: str) -> list[dict]:
    with open(path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    return _coerce(_rename(rows))


def load_md(path: str) -> list[dict]:
    lines = [l.rstrip() for l in Path(path).read_text().splitlines()
             if l.strip().startswith("|")]
    if len(lines) < 3:
        sys.exit(f"Could not parse markdown table in {path}")
    headers = [c.strip() for c in lines[0].split("|")[1:-1]]
    rows = []
    for line in lines[2:]:
        cells = [c.strip() for c in line.split("|")[1:-1]]
        if len(cells) == len(headers):
            rows.append(dict(zip(headers, cells)))
    return _coerce(_rename(rows))


def load(path: str) -> list[dict]:
    ext = path.lower().rsplit(".", 1)[-1]
    if ext == "json":   return load_json(path)
    if ext == "csv":    return load_csv(path)
    if ext in ("md", "markdown", "txt"): return load_md(path)
    sys.exit(f"Unsupported file type: .{ext}  (supported: json, csv, md)")


# ── pack calculator ───────────────────────────────────────────────────────────

def find_packs(cells: list[dict], target_v: float, target_wh: float,
               tolerance: float, wh_tolerance: float,
               in_stock_only: bool) -> list[dict]:
    results = []
    seen: set = set()

    for cell in cells:
        v     = cell.get("voltage_v")
        ah    = cell.get("capacity_ah")
        wh    = cell.get("wh")
        price = cell.get("price_incl")

        if not all([v, ah, wh, price]):
            continue
        if in_stock_only and not cell.get("in_stock"):
            continue

        ns = round(target_v / v)
        if ns < 1:
            continue
        v_pack = ns * v
        if abs(v_pack - target_v) / target_v > tolerance:
            continue

        # Minimum Np to reach target Wh, then check upper bound
        np_ = max(1, math.ceil((target_wh / v_pack) / ah))
        wh_pack_check = ns * np_ * ah * v
        wh_upper = target_wh * (1 + wh_tolerance)
        if wh_pack_check > wh_upper:
            continue

        key = (cell.get("name", ""), ns, np_)
        if key in seen:
            continue
        seen.add(key)

        n_total          = ns * np_
        ah_pack          = round(np_ * ah, 2)
        wh_pack          = round(ns * ah_pack * v, 2)

        cell_price_unit  = price
        cell_price, tier_qty = _best_tier_price(cell, n_total)
        price_total      = round(n_total * cell_price, 2)
        tier_savings     = round((cell_price_unit - cell_price) * n_total, 2)
        wh_per_eur       = round(wh_pack / price_total, 2)

        weight_kg  = round(n_total * cell["weight_g"] / 1000, 2) if cell.get("weight_g") else None
        wh_per_kg  = round(wh_pack / weight_kg, 1)                if weight_kg            else None
        vol_l      = round(n_total * cell["vol_l"], 3)             if cell.get("vol_l")    else None
        wh_per_l   = round(wh_pack / vol_l, 1)                     if vol_l                else None

        cell_disch   = cell.get("discharge_a")
        pack_disch_a = round(np_ * cell_disch, 1)        if cell_disch else None
        pack_max_kw  = round(pack_disch_a * v_pack / 1000, 2) if pack_disch_a else None

        # Distance to next price tier (None = already at max tier)
        tiers_sorted = sorted(cell.get("tiers") or [], key=lambda t: t["qty"])
        next_tier    = next((t for t in tiers_sorted if t["qty"] > n_total), None)
        if next_tier:
            _cells_gap        = next_tier["qty"] - n_total
            wh_to_next_tier    = round(_cells_gap * wh, 1)
            cells_to_next_tier = next_tier["qty"]
            np_to_next_tier    = math.ceil(_cells_gap / ns)
            ns_to_next_tier    = math.ceil(_cells_gap / np_)
        else:
            wh_to_next_tier    = None
            cells_to_next_tier = None
            np_to_next_tier    = None
            ns_to_next_tier    = None

        results.append({
            "name":             cell.get("name", "—"),
            "brand":            cell.get("brand") or "—",
            "chemistry":        cell.get("chemistry") or "—",
            "size":             cell.get("size") or "—",
            "grade":            cell.get("grade") or "—",
            "in_stock":         bool(cell.get("in_stock")),
            "cell_v":           v,
            "cell_ah":          ah,
            "cell_wh":          wh,
            "cell_price":       cell_price,
            "cell_price_unit":  cell_price_unit,
            "tier_qty":         tier_qty,
            "tier_savings":     tier_savings,
            "wh_to_next_tier":   wh_to_next_tier,
            "cells_to_next_tier": cells_to_next_tier,
            "np_to_next_tier":   np_to_next_tier,
            "ns_to_next_tier":   ns_to_next_tier,
            "tiers":            cell.get("tiers") or [],
            "ns":               ns,
            "np":               np_,
            "n_total":          n_total,
            "v_pack":           round(v_pack, 2),
            "ah_pack":          ah_pack,
            "wh_pack":          wh_pack,
            "price_total":      price_total,
            "wh_per_eur":       wh_per_eur,
            "weight_kg":    weight_kg,
            "wh_per_kg":    wh_per_kg,
            "vol_l":        vol_l,
            "wh_per_l":     wh_per_l,
            "cell_disch_a": cell_disch,
            "pack_disch_a": pack_disch_a,
            "pack_max_kw":  pack_max_kw,
            "dims_mm":      cell.get("dims_mm") or "—",
            "url":          cell.get("url") or "",
        })

    results.sort(key=lambda r: r["price_total"])
    return results


# ── text formatting ───────────────────────────────────────────────────────────

def f(v, d=2):
    return "—" if v is None else f"{v:.{d}f}"


def render_summary(target_v, target_wh, n_results, in_stock_only, tolerance, wh_tolerance):
    wh_cap = f"up to +{wh_tolerance*100:.0f}%" if wh_tolerance < 1.0 else "no cap"
    return "\n".join([
        "## Pack builder results", "",
        f"**Target:** {target_v} V · {target_wh:,.0f} Wh",
        f"**Voltage tolerance:** ±{tolerance*100:.0f}%",
        f"**Wh overshoot cap:** {wh_cap}",
        f"**Stock filter:** {'in-stock only' if in_stock_only else 'all'}",
        f"**Configurations found:** {n_results}", "",
    ])


def _tier_note(p) -> str:
    if not p.get("tier_qty"):
        return "—"
    return f"≥{p['tier_qty']}→€{p['cell_price']:.2f} (save €{p['tier_savings']:.2f})"


def _cols():
    return [
        ("Rank",         lambda i, p: str(i)),
        ("Cell",         lambda i, p: p["name"]),
        ("Brand",        lambda i, p: p["brand"]),
        ("Chem",         lambda i, p: p["chemistry"]),
        ("Size",         lambda i, p: p["size"]),
        ("Grade",        lambda i, p: p["grade"]),
        ("In stock",     lambda i, p: "✓" if p["in_stock"] else "✗"),
        ("Config",       lambda i, p: f"{p['ns']}S×{p['np']}P"),
        ("Cells",        lambda i, p: str(p["n_total"])),
        ("Pack V",       lambda i, p: f(p["v_pack"])),
        ("Pack Ah",      lambda i, p: f(p["ah_pack"])),
        ("Pack Wh",      lambda i, p: f(p["wh_pack"], 0)),
        ("€/cell",       lambda i, p: f(p["cell_price"])),
        ("Unit €/cell",  lambda i, p: f(p["cell_price_unit"])),
        ("Tier savings €",  lambda i, p: f(p["tier_savings"]) if p.get("tier_savings") else "—"),
        ("Wh to next tier", lambda i, p: f(p["wh_to_next_tier"], 0) if p.get("wh_to_next_tier") is not None else "max"),
        ("Tier applied",    lambda i, p: _tier_note(p)),
        ("Total €",      lambda i, p: f(p["price_total"])),
        ("Pack Wh/€",    lambda i, p: f(p["wh_per_eur"])),
        ("Pack kg",      lambda i, p: f(p["weight_kg"])),
        ("Pack Wh/kg",   lambda i, p: f(p["wh_per_kg"], 1)),
        ("Pack L",       lambda i, p: f(p["vol_l"])),
        ("Pack Wh/L",    lambda i, p: f(p["wh_per_l"], 1)),
        ("Cell disch A", lambda i, p: f(p["cell_disch_a"], 1)),
        ("Pack disch A", lambda i, p: f(p["pack_disch_a"], 1)),
        ("Pack max kW",  lambda i, p: f(p["pack_max_kw"])),
        ("Dims mm",      lambda i, p: p["dims_mm"]),
    ]


def render_markdown(packs: list[dict]) -> str:
    cols = _cols()
    header = "| " + " | ".join(c[0] for c in cols) + " |"
    sep    = "| " + " | ".join("---" for _ in cols) + " |"
    rows   = ["| " + " | ".join(fn(i, p) for _, fn in cols) + " |"
              for i, p in enumerate(packs, 1)]
    return "\n".join([header, sep] + rows)


def render_csv(packs: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    fields = ["rank","name","brand","chemistry","size","grade","in_stock","config",
              "n_total","v_pack","ah_pack","wh_pack",
              "cell_price","cell_price_unit","tier_qty","tier_savings",
              "price_total","wh_per_eur","weight_kg","wh_per_kg","vol_l","wh_per_l",
              "cell_disch_a","pack_disch_a","pack_max_kw","dims_mm","url"]
    w.writerow(fields)
    for i, p in enumerate(packs, 1):
        w.writerow([
            i, p["name"], p["brand"], p["chemistry"], p["size"], p["grade"],
            "yes" if p["in_stock"] else "no", f"{p['ns']}S×{p['np']}P",
            p["n_total"], p["v_pack"], p["ah_pack"], p["wh_pack"],
            p["cell_price"], p["cell_price_unit"], p["tier_qty"] or "", p["tier_savings"] or "",
            p["price_total"], p["wh_per_eur"],
            p["weight_kg"] or "", p["wh_per_kg"] or "",
            p["vol_l"] or "", p["wh_per_l"] or "",
            p["cell_disch_a"] or "", p["pack_disch_a"] or "", p["pack_max_kw"] or "",
            p["dims_mm"], p["url"],
        ])
    return buf.getvalue()


def render_json_text(packs: list[dict]) -> str:
    out = [dict(p, rank=i, config=f"{p['ns']}S×{p['np']}P")
           for i, p in enumerate(packs, 1)]
    return json.dumps(out, indent=2, ensure_ascii=False)


# ── plotly HTML ───────────────────────────────────────────────────────────────

def render_html(packs: list[dict], target_v: float, target_wh: float,
                in_stock_only: bool, tolerance: float, wh_tolerance: float) -> str:
    import plotly.graph_objects as go

    PLOTLY_CONFIG = {"responsive": True, "displaylogo": False}

    palette = ["#4c72b0","#dd8452","#55a868","#c44e52",
               "#8172b2","#937860","#da8bc3","#8c8c8c"]
    chems = sorted({p["chemistry"] for p in packs})
    color_map = {c: palette[i % len(palette)] for i, c in enumerate(chems)}

    def tip(p):
        stock = "✓ in stock" if p["in_stock"] else "✗ out of stock"
        price_str = f"€{p['cell_price']:.2f}/cell"
        if p.get("tier_qty"):
            price_str += f" (tier ≥{p['tier_qty']}; was €{p['cell_price_unit']:.2f})"
        lines = [
            f"<b>{p['name']}</b>",
            f"Config: {p['ns']}S×{p['np']}P  ({p['n_total']} cells)  {stock}",
            f"Pack: {p['v_pack']:.1f} V · {p['ah_pack']:.1f} Ah · {p['wh_pack']:.0f} Wh",
            f"Cost: {price_str} → <b>€{p['price_total']:.2f} total</b>",
            f"Wh/€: {p['wh_per_eur']:.2f}",
        ]
        if p.get("tier_savings"):
            lines.append(f"Tier savings: €{p['tier_savings']:.2f} vs unit price")
        if p.get("wh_to_next_tier") is not None:
            lines.append(f"Next tier: {p['wh_to_next_tier']:.0f} Wh away")
        if p["pack_disch_a"]:
            lines.append(f"Max discharge: {p['pack_disch_a']:.0f} A  /  {p['pack_max_kw']:.1f} kW")
        if p["weight_kg"]:
            lines.append(f"Weight: {p['weight_kg']:.1f} kg  ({p['wh_per_kg']:.0f} Wh/kg)")
        if p["vol_l"]:
            lines.append(f"Volume: {p['vol_l']:.2f} L  ({p['wh_per_l']:.0f} Wh/L)")
        return "<br>".join(lines)

    def traces(x_key, y_key, size_key=None, symbol_stock=False):
        ts = []
        for chem in chems:
            group = [p for p in packs if p["chemistry"] == chem]
            for stock_val in ([True, False] if symbol_stock else [None]):
                sub = [p for p in group if stock_val is None or p["in_stock"] == stock_val]
                if not sub:
                    continue
                xs = [p.get(x_key) for p in sub]
                ys = [p.get(y_key) for p in sub]
                if all(v is None for v in xs) or all(v is None for v in ys):
                    continue
                sizes = None
                if size_key:
                    raw = [p.get(size_key) or 0 for p in sub]
                    mn, mx = min(raw), max(raw)
                    span = mx - mn or 1
                    sizes = [8 + 28 * (v - mn) / span for v in raw]
                sym = ("circle" if stock_val is not False else "circle-open") if symbol_stock else "circle"
                label = chem if not symbol_stock else f"{chem} ({'✓' if stock_val else '✗'})"
                ts.append(go.Scatter(
                    x=xs, y=ys, mode="markers", name=label,
                    legendgroup=chem,
                    showlegend=True,
                    marker=dict(
                        size=sizes or 10,
                        color=color_map[chem],
                        symbol=sym,
                        opacity=0.85,
                        line=dict(width=1.5, color=color_map[chem]),
                    ),
                    customdata=[tip(p) for p in sub],
                    hovertemplate="%{customdata}<extra></extra>",
                ))
        return ts

    base = dict(
        autosize=True,
        paper_bgcolor="white", plot_bgcolor="#f9f9f9",
        font=dict(family="system-ui, sans-serif", size=12),
        margin=dict(l=60, r=40, t=50, b=50),
        legend=dict(x=1.01, xanchor="left", y=1, yanchor="top"),
    )

    # ── 1. Cost ranking bar ──────────────────────────────────────────────────
    # Single trace with per-bar colors so Plotly respects the global sort order.
    # A dummy legend trace per chemistry provides the color key.
    sorted_packs = sorted(packs, key=lambda p: p["price_total"])
    labels = [f"{p['ns']}S×{p['np']}P  {p['name'][:42]}" for p in sorted_packs]
    # Cheapest at top → reverse so smallest bar is at top of chart
    labels_rev = labels[::-1]
    prices_rev = [p["price_total"] for p in sorted_packs[::-1]]
    colors_rev = [color_map[p["chemistry"]] for p in sorted_packs[::-1]]
    tips_rev   = [tip(p) for p in sorted_packs[::-1]]

    fig1 = go.Figure()
    fig1.add_trace(go.Bar(
        y=labels_rev, x=prices_rev,
        orientation="h",
        marker_color=colors_rev,
        customdata=tips_rev,
        hovertemplate="%{customdata}<extra></extra>",
        showlegend=False,
        name="",
    ))
    # Invisible legend entries for color key
    for chem in chems:
        fig1.add_trace(go.Bar(
            x=[None], y=[None], orientation="h",
            name=chem, marker_color=color_map[chem], showlegend=True,
        ))
    # Force y-axis to respect our explicit order
    fig1.update_yaxes(categoryorder="array", categoryarray=labels_rev)
    row_px = 24
    bar_layout = dict(**base)
    bar_layout["margin"] = dict(l=340, r=40, t=50, b=50)
    fig1.update_layout(**bar_layout,
        title=dict(text="Pack cost ranking — cheapest first (€)", x=0),
        xaxis_title="Total pack cost (€)",
        barmode="overlay",
        height=max(400, len(packs) * row_px + 130),
        legend_title="Chemistry",
    )

    # ── 2. Cost vs max power ─────────────────────────────────────────────────
    fig2 = go.Figure(traces("price_total", "pack_max_kw",
                            size_key="wh_pack", symbol_stock=True))
    fig2.update_layout(**base,
        title=dict(text="Pack cost vs max power  (bubble = pack Wh)", x=0),
        xaxis_title="Total pack cost (€)",
        yaxis_title="Max discharge power (kW)",
        legend_title="Chemistry / Stock",
    )

    # ── 3. Value vs discharge current ────────────────────────────────────────
    fig3 = go.Figure(traces("wh_per_eur", "pack_disch_a", symbol_stock=True))
    for x, y, txt in [
        (0.98, 0.98, "High power · good value"),
        (0.02, 0.98, "High power · expensive"),
        (0.98, 0.02, "Low power · good value"),
        (0.02, 0.02, "Low power · expensive"),
    ]:
        fig3.add_annotation(x=x, y=y, xref="paper", yref="paper", text=txt,
                            showarrow=False, font=dict(size=10, color="#888"),
                            xanchor="right" if x > 0.5 else "left",
                            yanchor="top"   if y > 0.5 else "bottom")
    fig3.update_layout(**base,
        title=dict(text="Pack value (Wh/€) vs max discharge current (A)", x=0),
        xaxis_title="Pack Wh/€  (higher = better value)",
        yaxis_title="Max discharge current (A)",
        legend_title="Chemistry / Stock",
    )

    # ── 4. Energy density scatter ────────────────────────────────────────────
    # size ∝ 1/price so cheaper = bigger bubble
    fig4 = go.Figure()
    for chem in chems:
        sub = [p for p in packs if p["chemistry"] == chem
               and p.get("wh_per_kg") and p.get("wh_per_l")]
        if not sub:
            continue
        inv = [1 / p["price_total"] for p in sub]
        mn, mx = min(inv), max(inv)
        span = mx - mn or 1
        sizes = [8 + 28 * (v - mn) / span for v in inv]
        fig4.add_trace(go.Scatter(
            x=[p["wh_per_l"]  for p in sub],
            y=[p["wh_per_kg"] for p in sub],
            mode="markers", name=chem,
            marker=dict(size=sizes, color=color_map[chem], opacity=0.8,
                        line=dict(width=1, color="white")),
            customdata=[tip(p) for p in sub],
            hovertemplate="%{customdata}<extra></extra>",
        ))
    fig4.update_layout(**base,
        title=dict(text="Pack energy density  (bubble size ∝ 1/cost — bigger = cheaper)", x=0),
        xaxis_title="Volumetric density (Wh/L)",
        yaxis_title="Gravimetric density (Wh/kg)",
        legend_title="Chemistry",
    )

    # ── assemble tabbed page ─────────────────────────────────────────────────
    PAGE_STYLE = """
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  html, body { height: 100%; overflow: hidden;
               font-family: system-ui, sans-serif; background: #eef0f3; }
  body { display: flex; flex-direction: column; }
  #chrome { flex-shrink: 0; background: #fff; border-bottom: 1px solid #d0d4da;
            padding: 10px 20px 0; display: flex; flex-direction: column; gap: 6px; }
  #chrome h1 { font-size: 0.92rem; font-weight: 600; color: #444; }
  #chrome p  { font-size: 0.78rem; color: #666; }
  .tabs { display: flex; gap: 3px; align-items: flex-end; }
  .tab  { padding: 7px 16px; cursor: pointer; border-radius: 5px 5px 0 0;
          background: #e0e3e8; border: 1px solid #c8cdd5; border-bottom: none;
          font-size: 0.82rem; user-select: none; color: #555; transition: background .1s; }
  .tab:hover  { background: #d0d4da; }
  .tab.active { background: #fff; border-bottom: 1px solid #fff; font-weight: 600;
                color: #111; margin-bottom: -1px; padding-bottom: 8px; }
  #panels { flex: 1; min-height: 0; position: relative; background: #fff;
            border-top: 1px solid #c8cdd5; }
  .panel  { display: none; position: absolute; inset: 0; flex-direction: column;
            overflow: hidden; }
  .panel.active { display: flex; }
  .panel.scrollable { overflow-y: auto; }
  .panel .plotly-graph-div { flex: 1; min-height: 0; width: 100% !important; }
  .panel.scrollable .plotly-graph-div { flex: none; width: 100% !important; }
</style>"""

    PAGE_SCRIPT = """
<script>
  function showTab(id) {
    document.querySelectorAll('.tab, .panel').forEach(e => e.classList.remove('active'));
    document.getElementById('tab-' + id).classList.add('active');
    const panel = document.getElementById('panel-' + id);
    panel.classList.add('active');
    requestAnimationFrame(() => {
      panel.querySelectorAll('.plotly-graph-div').forEach(d => {
        if (window.Plotly) Plotly.Plots.resize(d);
      });
    });
  }
  window.addEventListener('resize', () => {
    document.querySelectorAll('.panel.active .plotly-graph-div').forEach(d => {
      if (window.Plotly) Plotly.Plots.resize(d);
    });
  });
  window.addEventListener('load', () => showTab(0));
</script>"""

    charts = [
        ("Cost ranking",   fig1, True),
        ("Cost vs Power",  fig2, False),
        ("Value vs Power", fig3, False),
        ("Density",        fig4, False),
    ]

    tabs = "".join(
        f'<div class="tab" id="tab-{i}" onclick="showTab({i})">{name}</div>'
        for i, (name, _, _) in enumerate(charts)
    )
    panels = "".join(
        f'<div class="panel {"scrollable" if scroll else ""}" id="panel-{i}">'
        + fig.to_html(full_html=False, include_plotlyjs=(i == 0), config=PLOTLY_CONFIG)
        + "</div>"
        for i, (_, fig, scroll) in enumerate(charts)
    )

    wh_cap = f"+{wh_tolerance*100:.0f}% Wh cap" if wh_tolerance < 1.0 else "no Wh cap"
    subtitle = (
        f"Target: {target_v} V · {target_wh:,.0f} Wh  |  "
        f"V tol ±{tolerance*100:.0f}%  |  {wh_cap}  |  "
        f"{'In-stock only' if in_stock_only else 'All cells'}  |  "
        f"{len(packs)} configurations"
    )

    return (
        "<!DOCTYPE html><html lang='en'><head>"
        "<meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Pack Builder</title>"
        f"{PAGE_STYLE}</head><body>"
        f"<div id='chrome'>"
        f"<h1>Battery pack builder</h1>"
        f"<p>{subtitle}</p>"
        f"<div class='tabs'>{tabs}</div></div>"
        f"<div id='panels'>{panels}</div>"
        f"{PAGE_SCRIPT}</body></html>"
    )


# ── price-curve chart ─────────────────────────────────────────────────────────

# 16 perceptually distinct colours (Paul Tol's palette, dark-background safe)
_CURVE_COLORS = [
    "#4477AA", "#EE6677", "#228833", "#CCBB44",
    "#66CCEE", "#AA3377", "#BBBBBB", "#FFFFFF",
    "#332288", "#117733", "#44AA99", "#88CCEE",
    "#DDCC77", "#CC6677", "#882255", "#AA4499",
]
_CURVE_DASHES   = ["solid", "dash", "dot", "dashdot"]
_CURVE_MARKERS  = [
    "triangle-up", "triangle-down", "triangle-left", "triangle-right",
    "diamond", "cross", "x", "star",
]


def _curve_style(i: int) -> tuple[str, str, str | None]:
    """Return (color, dash, marker_symbol) for trace index i.

    Levels:
      0–15   : 16 colours, solid line, no marker
      16–63  : colours cycle, dash varies (4 dashes × 16 colours)
      64+    : colours + dashes cycle, chevron marker added
    """
    color  = _CURVE_COLORS[i % 16]
    dash   = _CURVE_DASHES[(i // 16) % 4]
    mk_idx = i // 64          # 0 = no marker, 1+ = use marker
    marker = _CURVE_MARKERS[(mk_idx - 1) % len(_CURVE_MARKERS)] if mk_idx else None
    return color, dash, marker


def render_price_curve(cells: list[dict], target_v: float, target_wh: float,
                       v_tol: float, in_stock_only: bool,
                       chem_filter: set | None = None) -> str:
    """Return a full-page HTML Plotly chart: pack cost vs required Wh.

    Each valid cell becomes a staircase trace — price is flat until another
    parallel string is needed, then steps up.  Tier pricing is applied at
    every step based on the actual cell count required.

    Style hierarchy (so each trace stays visually distinct):
      1. 16 perceptually distinct colours
      2. 4 line dashes  (activated after 16 traces)
      3. 8 chevron/marker symbols  (activated after 64 traces)
    """
    import plotly.graph_objects as go

    PLOTLY_CONFIG = {"responsive": True, "displaylogo": False}

    filtered = [
        c for c in cells
        if (not in_stock_only or c.get("in_stock"))
        and (not chem_filter or (c.get("chemistry") or "Unknown") in chem_filter)
    ]

    wh_max = target_wh * 3
    fig    = go.Figure()
    ti     = 0   # trace index for style assignment

    for cell in filtered:
        v     = cell.get("voltage_v")
        ah    = cell.get("capacity_ah")
        name  = cell.get("name") or "?"

        if not all([v, ah, cell.get("price_incl")]):
            continue

        ns = round(target_v / v)
        if ns < 1:
            continue
        v_pack = ns * v
        if abs(v_pack - target_v) / target_v > v_tol:
            continue

        # Build explicit step endpoints so hover tracks anywhere along each
        # flat segment (not just at the data points used by line_shape="hv").
        # Each step i contributes two points: (wh_prev, price_i) and
        # (wh_curr, price_i), giving a real data point every hover can snap to.
        xs   = []
        ys   = []
        tips = []
        wh_prev = 0.0

        np_ = 1
        while True:
            wh_pack = v_pack * np_ * ah
            n_total = ns * np_
            cp, tq  = _best_tier_price(cell, n_total)
            price   = round(n_total * cp, 2)
            tier_note = (f"tier ≥{tq}: €{cp:.2f}/cell" if tq
                         else f"unit: €{cp:.2f}/cell")
            tip = (
                f"<b>{name[:55]}</b><br>"
                f"Config: {ns}S×{np_}P  ({n_total} cells)<br>"
                f"Pack Wh: {wh_pack:.0f}  |  <b>€{price:.2f}</b><br>"
                f"{tier_note}"
            )
            xs.extend([wh_prev, wh_pack])
            ys.extend([price, price])
            tips.extend([tip, tip])
            wh_prev = wh_pack

            if wh_pack >= wh_max:
                break
            np_ += 1

        if not xs:
            continue

        color, dash, marker = _curve_style(ti)
        ti += 1

        fig.add_trace(go.Scatter(
            x=xs, y=ys,
            mode="lines+markers" if marker else "lines",
            line=dict(color=color, width=1.5, dash=dash),
            marker=dict(symbol=marker or "circle", size=6, color=color,
                        opacity=0.8) if marker else dict(size=0),
            name=name[:55],
            customdata=tips,
            hovertemplate="%{customdata}<extra></extra>",
            opacity=0.85,
        ))

    # Mark the current target Wh
    fig.add_vline(
        x=target_wh, line_dash="dash", line_color="#aaa", line_width=1.5,
        annotation_text=f"Target {target_wh:,.0f} Wh",
        annotation_position="top right",
        annotation_font=dict(color="#ccc", size=11),
    )

    fig.update_layout(
        template="plotly_dark",
        autosize=True,
        title=dict(
            text=(f"Pack cost vs required Wh  —  {target_v} V  "
                  f"| V-tol ±{v_tol*100:.0f}%"
                  + ("  | in-stock only" if in_stock_only else "")
                  + (f"  | {', '.join(sorted(chem_filter))}" if chem_filter else "")),
            x=0,
        ),
        xaxis_title="Required pack energy (Wh)",
        yaxis_title="Total pack cost (€)",
        legend=dict(x=1.01, xanchor="left", y=1, yanchor="top",
                    groupclick="toggleitem"),
        font=dict(family="system-ui, sans-serif", size=12),
        margin=dict(l=60, r=200, t=60, b=50),
        hovermode="x",
    )

    return fig.to_html(full_html=True, include_plotlyjs=True, config=PLOTLY_CONFIG)


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(
        description="Battery pack configurator.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("data",          help="Cell data file (json / csv / md)")
    p.add_argument("--voltage","-v",type=float, required=True,
                   help="Target pack voltage (e.g. 48)")
    p.add_argument("--wh",     "-w",type=float, required=True,
                   help="Target pack energy in Wh (e.g. 4096)")
    p.add_argument("--tolerance","-t",type=float, default=0.15,
                   help="Voltage tolerance as fraction (default 0.15 = ±15%%)")
    p.add_argument("--wh-tolerance","--wht",type=float, default=1.0,
                   help="Max allowed Wh overshoot as fraction of target (default 1.0 = +100%%, i.e. no cap)")
    p.add_argument("--in-stock",    action="store_true",
                   help="Only use in-stock cells")
    p.add_argument("--output","-o", metavar="FILE",
                   help="Output file (.md / .csv / .json / .html); default stdout (md)")
    return p.parse_args()


def main():
    args = parse_args()
    cells = load(args.data)
    print(f"Loaded {len(cells)} cells from {args.data}", file=sys.stderr)

    packs = find_packs(cells, args.voltage, args.wh, args.tolerance,
                       args.wh_tolerance, args.in_stock)
    print(f"Found {len(packs)} configurations.", file=sys.stderr)

    if not packs:
        print("No configurations found. Try relaxing --tolerance.", file=sys.stderr)
        sys.exit(1)

    ext = args.output.lower().rsplit(".", 1)[-1] if args.output else "md"

    if ext == "html":
        text = render_html(packs, args.voltage, args.wh, args.in_stock,
                           args.tolerance, args.wh_tolerance)
    elif ext == "json":
        text = render_json_text(packs)
    elif ext == "csv":
        text = render_csv(packs)
    else:
        summary = render_summary(args.voltage, args.wh, len(packs),
                                 args.in_stock, args.tolerance, args.wh_tolerance)
        text = summary + render_markdown(packs)

    if args.output:
        kwargs = {"newline": ""} if ext == "csv" else {}
        with open(args.output, "w", **kwargs) as fh:
            fh.write(text)
        print(f"Written to {args.output}", file=sys.stderr)
    else:
        print(text)


if __name__ == "__main__":
    main()
