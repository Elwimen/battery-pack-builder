#!/usr/bin/env python3
"""Generate an interactive HTML chart from a scrape.py CSV output.

Usage:
    python chart.py input.csv [output.html]

Output defaults to input filename with .html extension.
"""

import sys
import pandas as pd
import plotly.graph_objects as go

PLOTLY_CONFIG = {"responsive": True, "displaylogo": False}


def load(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.replace("", float("nan"), inplace=True)
    for col in ["V", "Ah", "Wh", "Price €", "€/Wh", "Wh/€", "Wh/kg", "Wh/L",
                "C-rate", "Weight g", "Vol L", "Disch A"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def short_name(name: str) -> str:
    return name[:45] + "…" if len(name) > 45 else name


def hover(row) -> str:
    lines = [f"<b>{row['Name']}</b>"]
    for label, col in [
        ("Chemistry", "Chemistry"), ("Size", "Size"), ("Grade", "Grade"),
        ("Voltage", "V"), ("Capacity", "Ah"), ("Wh", "Wh"),
        ("Price", "Price €"), ("Wh/€", "Wh/€"), ("€/Wh", "€/Wh"),
        ("Wh/kg", "Wh/kg"), ("Wh/L", "Wh/L"), ("C-rate", "C-rate"),
        ("Discharge A", "Disch A"), ("In stock", "In stock"),
    ]:
        val = row.get(col, "")
        if pd.notna(val) and val != "":
            lines.append(f"{label}: {val}")
    return "<br>".join(lines)


def chemistry_colors(df: pd.DataFrame) -> dict:
    palette = ["#4c72b0", "#dd8452", "#55a868", "#c44e52",
               "#8172b2", "#937860", "#da8bc3", "#8c8c8c"]
    chems = df["Chemistry"].dropna().unique().tolist()
    return {c: palette[i % len(palette)] for i, c in enumerate(chems)}


def _base_layout(**kwargs) -> dict:
    return dict(
        autosize=True,
        margin=dict(l=60, r=40, t=50, b=50),
        legend=dict(orientation="v", x=1.01, xanchor="left", y=1, yanchor="top"),
        paper_bgcolor="white",
        plot_bgcolor="#f9f9f9",
        font=dict(family="system-ui, sans-serif", size=12),
        **kwargs,
    )


# ── Chart 1: Wh/€ bar chart ───────────────────────────────────────────────────

def bar_value(df: pd.DataFrame) -> go.Figure:
    d = df.dropna(subset=["Wh/€"]).sort_values("Wh/€", ascending=True)
    colors = chemistry_colors(df)
    fig = go.Figure()
    for chem, grp in d.groupby("Chemistry", sort=False):
        fig.add_trace(go.Bar(
            y=[short_name(n) for n in grp["Name"]],
            x=grp["Wh/€"],
            orientation="h",
            name=str(chem),
            marker_color=colors.get(chem, "#888"),
            customdata=grp.apply(hover, axis=1),
            hovertemplate="%{customdata}<extra></extra>",
        ))
    row_px = 26
    layout = _base_layout(
        title=dict(text="Value ranking — Wh/€ (higher = better deal)", x=0),
        xaxis_title="Wh/€",
        barmode="stack",
        height=max(420, len(d) * row_px + 130),
        legend_title="Chemistry",
    )
    layout["margin"] = dict(l=280, r=40, t=50, b=50)
    fig.update_layout(**layout)
    return fig


# ── Chart 2: Ragone plot ──────────────────────────────────────────────────────

def ragone(df: pd.DataFrame) -> go.Figure:
    d = df.dropna(subset=["Wh/kg", "Wh/L"])
    colors = chemistry_colors(df)
    wh_eur = d["Wh/€"].fillna(0)
    span = (wh_eur.max() - wh_eur.min()) or 1
    sizes = 8 + 32 * (wh_eur - wh_eur.min()) / span

    fig = go.Figure()
    for chem, grp in d.groupby("Chemistry", sort=False):
        fig.add_trace(go.Scatter(
            x=grp["Wh/L"], y=grp["Wh/kg"],
            mode="markers",
            name=str(chem),
            marker=dict(size=sizes[grp.index], color=colors.get(chem, "#888"),
                        opacity=0.8, line=dict(width=1, color="white")),
            customdata=grp.apply(hover, axis=1),
            hovertemplate="%{customdata}<extra></extra>",
        ))
    fig.update_layout(**_base_layout(
        title=dict(text="Ragone — Wh/kg vs Wh/L  (bubble size = Wh/€)", x=0),
        xaxis_title="Volumetric energy density (Wh/L)",
        yaxis_title="Gravimetric energy density (Wh/kg)",
        legend_title="Chemistry",
    ))
    return fig


# ── Chart 3: Price vs Wh ─────────────────────────────────────────────────────

def price_vs_wh(df: pd.DataFrame) -> go.Figure:
    d = df.dropna(subset=["Price €", "Wh"])
    colors = chemistry_colors(df)
    stock_symbols = {"yes": "circle", "no": "circle-open"}

    fig = go.Figure()
    for chem, grp in d.groupby("Chemistry", sort=False):
        for stock, sgrp in grp.groupby("In stock", sort=False):
            label = "in stock" if str(stock) == "yes" else "out of stock"
            fig.add_trace(go.Scatter(
                x=sgrp["Price €"], y=sgrp["Wh"],
                mode="markers",
                name=f"{chem} ({label})",
                marker=dict(
                    size=10, color=colors.get(chem, "#888"),
                    symbol=stock_symbols.get(str(stock), "circle"),
                    opacity=0.85,
                    line=dict(width=1.5, color=colors.get(chem, "#888")),
                ),
                customdata=sgrp.apply(hover, axis=1),
                hovertemplate="%{customdata}<extra></extra>",
            ))
    fig.update_layout(**_base_layout(
        title=dict(text="Price vs Capacity — filled = in stock, open = out of stock", x=0),
        xaxis_title="Price incl. VAT (€)",
        yaxis_title="Capacity (Wh)",
        legend_title="Chemistry / Stock",
    ))
    return fig


# ── Chart 4: C-rate vs Wh/€ ──────────────────────────────────────────────────

def crate_vs_value(df: pd.DataFrame) -> go.Figure:
    d = df.dropna(subset=["C-rate", "Wh/€"])
    colors = chemistry_colors(df)
    fig = go.Figure()
    for chem, grp in d.groupby("Chemistry", sort=False):
        fig.add_trace(go.Scatter(
            x=grp["Wh/€"], y=grp["C-rate"],
            mode="markers", name=str(chem),
            marker=dict(size=9, color=colors.get(chem, "#888"),
                        opacity=0.85, line=dict(width=1, color="white")),
            customdata=grp.apply(hover, axis=1),
            hovertemplate="%{customdata}<extra></extra>",
        ))
    for x, y, text in [
        (0.98, 0.98, "High drain · good value"),
        (0.02, 0.98, "High drain · expensive"),
        (0.98, 0.02, "Energy cell · good value"),
        (0.02, 0.02, "Energy cell · expensive"),
    ]:
        fig.add_annotation(x=x, y=y, xref="paper", yref="paper", text=text,
                           showarrow=False, font=dict(size=10, color="#777"),
                           xanchor="right" if x > 0.5 else "left",
                           yanchor="top" if y > 0.5 else "bottom")
    fig.update_layout(**_base_layout(
        title=dict(text="C-rate vs Wh/€ — application fit vs cost efficiency", x=0),
        xaxis_title="Wh/€ (value, higher = better)",
        yaxis_title="C-rate (discharge speed, higher = faster)",
        legend_title="Chemistry",
    ))
    return fig


# ── HTML shell ────────────────────────────────────────────────────────────────

PAGE_STYLE = """
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  html, body {
    height: 100%;
    overflow: hidden;
    font-family: system-ui, sans-serif;
    background: #eef0f3;
    color: #222;
  }

  body {
    display: flex;
    flex-direction: column;
  }

  /* ── chrome (header + tabs) ── */
  #chrome {
    flex-shrink: 0;
    background: #fff;
    border-bottom: 1px solid #d0d4da;
    padding: 10px 20px 0;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  #chrome h1 {
    font-size: 0.95rem;
    font-weight: 600;
    color: #444;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .tabs {
    display: flex;
    gap: 3px;
    align-items: flex-end;
  }

  .tab {
    padding: 7px 16px;
    cursor: pointer;
    border-radius: 6px 6px 0 0;
    background: #e0e3e8;
    border: 1px solid #c8cdd5;
    border-bottom: none;
    font-size: 0.82rem;
    user-select: none;
    transition: background 0.1s;
    color: #555;
  }

  .tab:hover { background: #d0d4da; }

  .tab.active {
    background: #fff;
    border-bottom: 1px solid #fff;
    font-weight: 600;
    color: #111;
    margin-bottom: -1px;
    padding-bottom: 8px;
  }

  /* ── panels area ── */
  #panels {
    flex: 1;
    min-height: 0;         /* critical: lets flex child shrink below content size */
    position: relative;
    background: #fff;
    border-top: 1px solid #c8cdd5;
  }

  /* scatter panels: fill full height, no scroll */
  .panel {
    display: none;
    position: absolute;
    inset: 0;
    flex-direction: column;
    overflow: hidden;
  }

  .panel.active { display: flex; }

  /* bar chart panel: content-height, scrollable */
  .panel.scrollable {
    overflow-y: auto;
    overflow-x: hidden;
  }

  /* make plotly divs fill their panel */
  .panel .plotly-graph-div {
    flex: 1;
    min-height: 0;
    width: 100% !important;
  }

  /* bar chart plotly div: natural height, full width */
  .panel.scrollable .plotly-graph-div {
    flex: none;
    width: 100% !important;
  }
</style>
"""

PAGE_SCRIPT = """
<script>
  function showTab(id) {
    document.querySelectorAll('.tab, .panel').forEach(e => e.classList.remove('active'));
    document.getElementById('tab-' + id).classList.add('active');
    const panel = document.getElementById('panel-' + id);
    panel.classList.add('active');
    // let the browser apply display:flex before resizing
    requestAnimationFrame(() => {
      panel.querySelectorAll('.plotly-graph-div').forEach(d => {
        if (window.Plotly) Plotly.Plots.resize(d);
      });
    });
  }

  // Resize active plot on window resize
  window.addEventListener('resize', () => {
    document.querySelectorAll('.panel.active .plotly-graph-div').forEach(d => {
      if (window.Plotly) Plotly.Plots.resize(d);
    });
  });

  window.addEventListener('load', () => showTab(0));
</script>
"""


def fig_html(fig: go.Figure, include_plotlyjs: bool) -> str:
    return fig.to_html(
        full_html=False,
        include_plotlyjs=include_plotlyjs,
        config=PLOTLY_CONFIG,
        div_id=None,
    )


def build_html(charts: list[tuple[str, go.Figure, bool]], source: str) -> str:
    tabs = "".join(
        f'<div class="tab" id="tab-{i}" onclick="showTab({i})">{name}</div>'
        for i, (name, _, _) in enumerate(charts)
    )
    panels = "".join(
        f'<div class="panel {"scrollable" if scroll else ""}" id="panel-{i}">'
        + fig_html(fig, include_plotlyjs=(i == 0))
        + "</div>"
        for i, (_, fig, scroll) in enumerate(charts)
    )
    return (
        "<!DOCTYPE html><html lang='en'><head>"
        "<meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>NKON Battery Charts</title>"
        f"{PAGE_STYLE}"
        "</head><body>"
        f"<div id='chrome'><h1>NKON battery comparison — {source}</h1>"
        f"<div class='tabs'>{tabs}</div></div>"
        f"<div id='panels'>{panels}</div>"
        f"{PAGE_SCRIPT}"
        "</body></html>"
    )


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(f"Usage: python {sys.argv[0]} input.csv [output.html]")
        sys.exit(1)

    csv_path = sys.argv[1]
    html_path = sys.argv[2] if len(sys.argv) > 2 else csv_path.rsplit(".", 1)[0] + ".html"

    df = load(csv_path)
    print(f"Loaded {len(df)} rows from {csv_path}", file=sys.stderr)

    # (tab name, figure, scrollable)
    charts = [
        ("Wh/€ ranking",    bar_value(df),       True),
        ("Ragone plot",     ragone(df),           False),
        ("Price vs Wh",     price_vs_wh(df),      False),
        ("C-rate vs value", crate_vs_value(df),   False),
    ]

    html = build_html(charts, csv_path)
    with open(html_path, "w") as fh:
        fh.write(html)
    print(f"Charts written to {html_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
