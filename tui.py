#!/usr/bin/env python3
"""Interactive TUI for the battery pack builder.

Usage:
    python tui.py data.json
    python tui.py data.csv
    python tui.py data.md
"""

import json
import sys
from pathlib import Path
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, ScrollableContainer
from textual.reactive import reactive
from textual.widgets import (
    Button, DataTable, Footer, Header, Input, Label,
    Static, Switch, Select, Rule, SelectionList,
)
from textual.screen import ModalScreen

sys.path.insert(0, str(Path(__file__).parent))
from builder import load, find_packs, render_html, render_csv, render_markdown, render_price_curve


# ── numeric columns available for custom expression ───────────────────────────

NUMERIC_COLS = [
    ("Total €",      "price_total"),
    ("Pack Wh",      "wh_pack"),
    ("Wh/€",         "wh_per_eur"),
    ("Pack disch A", "pack_disch_a"),
    ("Pack max kW",  "pack_max_kw"),
    ("Pack kg",      "weight_kg"),
    ("Wh/kg",        "wh_per_kg"),
    ("Pack L",       "vol_l"),
    ("Wh/L",         "wh_per_l"),
    ("€/cell",       "cell_price"),
    ("Unit €/cell",  "cell_price_unit"),
    ("Tier savings",    "tier_savings"),
    ("Wh to next tier","wh_to_next_tier"),
    ("Cells",        "n_total"),
    ("Pack V",       "v_pack"),
    ("Pack Ah",      "ah_pack"),
    ("Cell disch A", "cell_disch_a"),
    ("Cell Wh",      "cell_wh"),
]

OPS = [
    ("÷  divide",    "/"),
    ("×  multiply",  "*"),
    ("＋ add",       "+"),
    ("－ subtract",  "-"),
]

_COL_LABEL = {key: label for label, key in NUMERIC_COLS}


# ── fixed table columns ───────────────────────────────────────────────────────

COLUMNS = [
    ("#",          "rank",          4),
    ("Config",     "config",       10),
    ("Cells",      "n_total",       6),
    ("Pack V",     "v_pack",        8),
    ("Pack Ah",    "ah_pack",       8),
    ("Pack Wh",    "wh_pack",       8),
    ("€/cell",     "cell_price",    8),
    ("Total €",    "price_total",   9),
    ("Tier saved", "tier_savings",    10),
    ("→ next tier","wh_to_next_tier", 11),
    ("Wh/€",       "wh_per_eur",      7),
    ("Disch A",    "pack_disch_a",  8),
    ("Max kW",     "pack_max_kw",   7),
    ("Wh/kg",      "wh_per_kg",     7),
    ("Wh/L",       "wh_per_l",      7),
    ("kg",         "weight_kg",     7),
    ("In stock",   "in_stock",      8),
    ("Chemistry",  "chemistry",    10),
    ("Brand",      "brand",        12),
    ("Cell",       "name",         52),
]


# ── helpers ───────────────────────────────────────────────────────────────────

def _f(v, d=2):
    return "—" if v is None else f"{v:.{d}f}"


def _compute_custom(pack: dict, col_a: str, op: str, col_b: str) -> float | None:
    a = pack.get(col_a)
    b = pack.get(col_b)
    if a is None or b is None:
        return None
    try:
        if op == "/":  return None if b == 0 else round(a / b, 4)
        if op == "*":  return round(a * b, 4)
        if op == "+":  return round(a + b, 4)
        if op == "-":  return round(a - b, 4)
    except Exception:
        return None


def _custom_label(col_a: str, op: str, col_b: str) -> str:
    sym = {"/" : "÷", "*": "×", "+": "+", "-": "−"}.get(op, op)
    return f"{_COL_LABEL.get(col_a, col_a)} {sym} {_COL_LABEL.get(col_b, col_b)}"


def row_cells(i: int, p: dict,
              hidden_cols: set | None = None,
              custom_val: float | None = None,
              custom_label: str | None = None) -> list[str]:
    hidden_cols = hidden_cols or set()
    all_vals = [
        str(i),
        f"{p['ns']}S×{p['np']}P",
        str(p["n_total"]),
        _f(p["v_pack"]),
        _f(p["ah_pack"]),
        _f(p["wh_pack"], 0),
        f"€{_f(p['cell_price'])}",
        f"€{_f(p['price_total'])}",
        f"€{_f(p['tier_savings'])}" if p.get("tier_savings") else "—",
        _f(p["wh_to_next_tier"], 0) if p.get("wh_to_next_tier") is not None else ("max" if p.get("tiers") else "—"),
        _f(p["wh_per_eur"]),
        _f(p["pack_disch_a"], 0),
        _f(p["pack_max_kw"]),
        _f(p["wh_per_kg"], 0),
        _f(p["wh_per_l"], 0),
        _f(p["weight_kg"]),
        "✓" if p["in_stock"] else "✗",
        p["chemistry"],
        p["brand"],
        p["name"],
    ]
    base = [v for (_, key, _), v in zip(COLUMNS, all_vals) if key not in hidden_cols]
    if custom_label is not None:
        base.append(_f(custom_val, 3))
    return base


def pack_detail(p: dict) -> str:
    stock = "✓ in stock" if p["in_stock"] else "✗ out of stock"
    price_note = f"€{_f(p['cell_price'])}/cell"
    if p.get("tier_qty"):
        price_note += (f" [tier ≥{p['tier_qty']}; unit €{_f(p['cell_price_unit'])},"
                       f" saving €{_f(p['tier_savings'])}]")
    lines = [
        f"[bold]{p['name']}[/bold]  [{stock}]",
        f"Brand: {p['brand']}  Chemistry: {p['chemistry']}  "
        f"Size: {p['size']}  Grade: {p['grade']}",
        f"Config: [bold]{p['ns']}S × {p['np']}P[/bold]  →  {p['n_total']} cells",
        f"Pack:  {_f(p['v_pack'])} V  ·  {_f(p['ah_pack'])} Ah  ·  {_f(p['wh_pack'], 0)} Wh",
        f"Price: {price_note}  →  [bold]€{_f(p['price_total'])} total[/bold]"
        f"  |  Wh/€: {_f(p['wh_per_eur'])}",
    ]
    if p.get("pack_disch_a"):
        lines.append(
            f"Discharge: {_f(p['pack_disch_a'], 0)} A pack  "
            f"({_f(p['cell_disch_a'], 0)} A/cell)  "
            f"|  Peak power: {_f(p['pack_max_kw'])} kW"
        )
    if p.get("weight_kg"):
        lines.append(
            f"Weight: {_f(p['weight_kg'])} kg  |  Wh/kg: {_f(p['wh_per_kg'], 0)}"
        )
    if p.get("vol_l"):
        lines.append(
            f"Volume: {_f(p['vol_l'])} L  |  Wh/L: {_f(p['wh_per_l'], 0)}"
        )
    if p.get("dims_mm") and p["dims_mm"] != "—":
        lines.append(f"Cell dims: {p['dims_mm']}")
    if p.get("url"):
        lines.append(f"URL: {p['url']}")
    return "\n".join(lines)


# ── smart numeric input ───────────────────────────────────────────────────────

class SmartInput(Input):
    """Input that increments/decrements the digit under the cursor with Up/Down.

    Carry and borrow propagate to more significant digits automatically.
    Value is clamped at 0; cursor position is preserved after each step.
    """

    def _step_value(self, direction: int) -> None:
        val = self.value
        if not val:
            return
        try:
            current = float(val)
        except ValueError:
            return

        # Find the effective cursor position, skipping non-digit chars
        pos = max(0, min(self.cursor_position, len(val) - 1))
        while pos < len(val) and not val[pos].isdigit():
            pos += 1
        if pos >= len(val):  # cursor past end or on trailing non-digit — use last digit
            pos = len(val) - 1
            while pos >= 0 and not val[pos].isdigit():
                pos -= 1
        if pos < 0:
            return

        dot = val.find(".")
        if dot == -1:
            step = float(10 ** (len(val) - pos - 1))
        elif pos < dot:
            step = float(10 ** (dot - pos - 1))
        else:
            step = 10.0 ** (-(pos - dot))

        new_val = max(0.0, current + direction * step)

        if dot == -1:
            new_str = str(int(round(new_val)))
        else:
            decimals = max(0, len(val) - dot - 1)
            new_str = f"{new_val:.{decimals}f}"

        # Cursor shifts right if the string grew (e.g. 999 → 1000)
        target = self.cursor_position + (len(new_str) - len(val))
        self.value = new_str
        self.call_after_refresh(self._restore_cursor, target)

    def _restore_cursor(self, pos: int) -> None:
        self.cursor_position = max(0, min(pos, len(self.value)))

    def on_key(self, event) -> None:
        if event.key == "up":
            self._step_value(1)
            event.stop()
            event.prevent_default()
        elif event.key == "down":
            self._step_value(-1)
            event.stop()
            event.prevent_default()


# ── export modal ──────────────────────────────────────────────────────────────

class ExportModal(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss", "Close")]

    def __init__(self, message: str):
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label(self._message, id="export-msg"),
            Button("Close", id="close-btn", variant="primary"),
            id="export-dialog",
        )

    def on_button_pressed(self, _: Button.Pressed) -> None:
        self.dismiss()


# ── column picker modal ───────────────────────────────────────────────────────

class ColumnPickerModal(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss", "Close")]

    def __init__(self, hidden: set[str]):
        super().__init__()
        self._hidden = hidden

    def compose(self) -> ComposeResult:
        options = [(label, key, key not in self._hidden) for label, key, _ in COLUMNS]
        yield Vertical(
            Label("Toggle visible columns", id="picker-title"),
            SelectionList(*options, id="col-picker"),
            Horizontal(
                Button("Apply", id="apply-cols", variant="primary"),
                Button("Cancel", id="cancel-cols"),
                id="picker-btns",
            ),
            id="col-picker-dialog",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "apply-cols":
            sel = self.query_one("#col-picker", SelectionList)
            visible = set(sel.selected)
            self.dismiss({key for _, key, _ in COLUMNS if key not in visible})
        else:
            self.dismiss(None)


# ── main app ──────────────────────────────────────────────────────────────────

class PackBuilderApp(App):
    CSS = """
    Screen { layout: vertical; }

    #main  { layout: horizontal; height: 1fr; }

    /* ── sidebar ── */
    #sidebar {
        width: 44;
        height: 100%;
        background: $panel;
        border-right: solid $primary-darken-2;
        overflow-y: auto;
        padding: 0 1 1 1;
    }

    #sidebar Label          { color: $text-muted; margin-top: 1; }
    #sidebar Input          { margin-bottom: 0; height: 3; }
    #sidebar Select         { margin-bottom: 0; height: 3; }
    #sidebar Rule           { margin: 1 0; }
    #chem-select            { height: auto; max-height: 8; margin-bottom: 0; border: solid $panel-darken-2; }

    #stock-row   { layout: horizontal; height: 3; align: left middle; margin-top: 1; }
    #stock-label { width: 20; color: $text-muted; }

    #btn-row     { layout: horizontal; height: 3; margin-top: 1; }
    #build-btn   { width: 18; margin-right: 1; }
    #clear-btn   { width: 14; }

    #status      { height: 1; color: $text-muted; margin-top: 1; text-style: italic; }

    /* custom col section */
    #custom-section Label  { color: $text-muted; margin-top: 1; }
    #custom-header         { color: $primary; margin-top: 1; text-style: bold; }
    #apply-btn             { width: 40; margin-top: 1; }
    #clear-custom-btn      { width: 40; margin-top: 0; }

    /* ── content ── */
    #content  { height: 100%; layout: vertical; }
    DataTable { height: 1fr; }

    /* ── detail panel ── */
    #detail-wrap {
        height: 11;
        background: $panel;
        border-top: solid $primary-darken-2;
        padding: 0 1;
    }
    #detail { height: 100%; }

    /* ── export modal ── */
    #export-dialog {
        width: 64;
        height: auto;
        background: $panel;
        border: solid $primary;
        padding: 1 2;
        margin: 4 8;
    }
    #export-msg { margin-bottom: 1; }

    /* ── column picker modal ── */
    #col-picker-dialog {
        width: 52;
        height: auto;
        max-height: 42;
        background: $panel;
        border: solid $primary;
        padding: 1 2;
        margin: 3 8;
    }
    #picker-title  { color: $primary; text-style: bold; margin-bottom: 1; }
    #col-picker    { height: auto; max-height: 30; border: solid $panel-darken-2; }
    #picker-btns   { layout: horizontal; height: 3; margin-top: 1; }
    #picker-btns Button { width: 18; margin-right: 1; }
    """

    BINDINGS = [
        Binding("q",      "quit",        "Quit"),
        Binding("b",      "build",       "Build"),
        Binding("v",      "columns",     "Columns"),
        Binding("p",      "price_curve", "Price curve"),
        Binding("e",      "export_html", "Export HTML"),
        Binding("c",      "export_csv",  "Export CSV"),
        Binding("m",      "export_md",   "Export MD"),
        Binding("ctrl+s", "export_html", "Export HTML", show=False),
    ]

    target_v  = reactive(48.0)
    target_wh = reactive(4096.0)
    v_tol     = reactive(15.0)
    wh_tol    = reactive(100.0)
    in_stock  = reactive(False)

    def __init__(self, data_path: str):
        super().__init__()
        self._data_path    = data_path
        self._cells: list[dict] = []
        self._packs: list[dict] = []
        self._sort_col     = "price_total"
        self._sort_asc     = True
        self._selected     : dict | None = None
        self._chem_filter  : str | None = None
        self._hidden_cols  : set[str] = set()
        self._custom_col_a : str | None = None
        self._custom_op    : str | None = "/"
        self._custom_col_b : str | None = None
        self._custom_active: bool = False
        self._load_config()

    # ── composition ───────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="main"):
            with ScrollableContainer(id="sidebar"):
                # ── pack parameters ──
                yield Label("Voltage (V)")
                yield SmartInput(value=f"{self.target_v:g}",  id="v-input",     placeholder="e.g. 48")
                yield Label("Target Wh")
                yield SmartInput(value=f"{self.target_wh:g}", id="wh-input",    placeholder="e.g. 4096")
                yield Label("V tolerance (%)")
                yield SmartInput(value=f"{self.v_tol:g}",     id="vtol-input",  placeholder="e.g. 15")
                yield Label("Wh overshoot cap (%)")
                yield SmartInput(value=f"{self.wh_tol:g}",    id="whtol-input", placeholder="e.g. 100")
                yield Label("Chemistry")
                yield SelectionList(id="chem-select")
                with Horizontal(id="stock-row"):
                    yield Label("In stock only", id="stock-label")
                    yield Switch(value=self.in_stock, id="stock-switch")
                with Horizontal(id="btn-row"):
                    yield Button("Build", id="build-btn", variant="primary")
                    yield Button("Clear", id="clear-btn")
                yield Label("", id="status")

                # ── custom column ──
                yield Rule()
                yield Label("─── Custom column ───", id="custom-header")
                yield Label("Column A")
                yield Select(
                    options=[(label, key) for label, key in NUMERIC_COLS],
                    id="col-a-select",
                    prompt="Select column A…",
                )
                yield Label("Operation")
                yield Select(
                    options=[(label, sym) for label, sym in OPS],
                    value="/",
                    id="op-select",
                )
                yield Label("Column B")
                yield Select(
                    options=[(label, key) for label, key in NUMERIC_COLS],
                    id="col-b-select",
                    prompt="Select column B…",
                )
                yield Button("Apply custom column", id="apply-btn", variant="success")
                yield Button("Remove custom column", id="clear-custom-btn", variant="default")

            with Vertical(id="content"):
                yield DataTable(id="results-table", cursor_type="row", zebra_stripes=True)
                with ScrollableContainer(id="detail-wrap"):
                    yield Static("Select a row to see details.", id="detail")

        yield Footer()

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        self.title = f"Pack Builder — {Path(self._data_path).name}"
        self._load_cells()
        self._init_table()
        self._build()

    def _config_path(self) -> Path:
        return Path(self._data_path).with_suffix(".config.json")

    def _load_config(self) -> None:
        p = self._config_path()
        if not p.exists():
            return
        try:
            cfg = json.loads(p.read_text())
            self._hidden_cols = set(cfg.get("hidden_cols", []))
            if "voltage"     in cfg: self.target_v    = float(cfg["voltage"])
            if "target_wh"   in cfg: self.target_wh   = float(cfg["target_wh"])
            if "v_tol"       in cfg: self.v_tol        = float(cfg["v_tol"])
            if "wh_tol"      in cfg: self.wh_tol       = float(cfg["wh_tol"])
            if "in_stock"    in cfg: self.in_stock      = bool(cfg["in_stock"])
            if "chem_filter" in cfg: self._chem_filter  = set(cfg["chem_filter"] or [])
        except Exception:
            pass

    def _save_config(self) -> None:
        cfg = {
            "hidden_cols":  sorted(self._hidden_cols),
            "voltage":      self.target_v,
            "target_wh":    self.target_wh,
            "v_tol":        self.v_tol,
            "wh_tol":       self.wh_tol,
            "in_stock":     self.in_stock,
            "chem_filter":  sorted(self._chem_filter),
        }
        try:
            self._config_path().write_text(json.dumps(cfg, indent=2))
        except Exception:
            pass

    def _load_cells(self) -> None:
        try:
            self._cells = load(self._data_path)
            self._set_status(f"{len(self._cells)} cells loaded")
            self._populate_chem_select()
        except Exception as e:
            self._set_status(f"[red]Load error: {e}[/red]")

    def _populate_chem_select(self) -> None:
        seen = {}
        for c in self._cells:
            ch = c.get("chemistry") or "Unknown"
            seen[ch] = seen.get(ch, 0) + 1
        all_chems = set(seen.keys())
        # Keep only saved filters that still exist in this dataset
        if self._chem_filter:
            self._chem_filter = self._chem_filter & all_chems
        opts = [
            (f"{ch}  ({seen[ch]})", ch, not self._chem_filter or ch in self._chem_filter)
            for ch in sorted(seen)
        ]
        self.query_one("#chem-select", SelectionList).set_options(opts)

    # ── table management ──────────────────────────────────────────────────────

    def _init_table(self) -> None:
        table = self.query_one("#results-table", DataTable)
        table.clear(columns=True)
        for label, key, width in COLUMNS:
            if key not in self._hidden_cols:
                table.add_column(label, key=key, width=width)
        if self._custom_active and self._custom_col_a and self._custom_col_b:
            lbl = _custom_label(self._custom_col_a, self._custom_op or "/", self._custom_col_b)
            table.add_column(lbl, key="__custom__", width=14)

    def _refresh_table(self, packs: list[dict]) -> None:
        table = self.query_one("#results-table", DataTable)
        table.clear()
        for i, p in enumerate(packs, 1):
            cval  = None
            clabel = None
            if self._custom_active and self._custom_col_a and self._custom_col_b:
                cval   = _compute_custom(p, self._custom_col_a,
                                         self._custom_op or "/", self._custom_col_b)
                clabel = "__custom__"
            table.add_row(*row_cells(i, p, self._hidden_cols, cval, clabel), key=str(i - 1))

    # ── build ─────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        if not self._cells:
            self._set_status("[red]No cells loaded.[/red]")
            return
        cells = (
            [c for c in self._cells if (c.get("chemistry") or "Unknown") in self._chem_filter]
            if self._chem_filter else self._cells
        )
        try:
            packs = find_packs(
                cells,
                self.target_v, self.target_wh,
                self.v_tol / 100, self.wh_tol / 100,
                self.in_stock,
            )
        except Exception as e:
            self._set_status(f"[red]{e}[/red]")
            return

        packs = self._sort(packs)
        self._packs = packs
        self._selected = None
        self._init_table()
        self._refresh_table(packs)
        self.query_one("#detail", Static).update("Select a row to see details.")
        note = "  (in-stock only)" if self.in_stock else ""
        if self._chem_filter:
            note += f"  [{', '.join(sorted(self._chem_filter))}]"
        self._set_status(f"{len(packs)} configuration{'s' if len(packs) != 1 else ''}{note}")
        self._save_config()

    def _sort(self, packs: list[dict]) -> list[dict]:
        # Sort by custom column if that's the active sort key
        if self._sort_col == "__custom__" and self._custom_active:
            def key(p):
                v = _compute_custom(p, self._custom_col_a,
                                    self._custom_op or "/", self._custom_col_b)
                return (v is None, v or 0)
        else:
            def key(p):
                v = p.get(self._sort_col)
                return (v is None, v or 0)
        return sorted(packs, key=key, reverse=not self._sort_asc)

    def _set_status(self, msg: str) -> None:
        self.query_one("#status", Label).update(msg)

    # ── events ────────────────────────────────────────────────────────────────

    def on_input_changed(self, event: Input.Changed) -> None:
        try:
            val = float(event.value)
        except ValueError:
            return
        mapping = {
            "v-input":     "target_v",
            "wh-input":    "target_wh",
            "vtol-input":  "v_tol",
            "whtol-input": "wh_tol",
        }
        attr = mapping.get(event.input.id)
        if attr:
            setattr(self, attr, val)
            self._build()

    def on_switch_changed(self, event: Switch.Changed) -> None:
        self.in_stock = event.value
        self._build()

    def on_selection_list_selected_changed(self, event: SelectionList.SelectedChanged) -> None:
        if event.selection_list.id != "chem-select":
            return
        selected = set(event.selection_list.selected)
        all_chems = {c.get("chemistry") or "Unknown" for c in self._cells}
        # Empty or full selection both mean "no filter"
        self._chem_filter = selected if selected and selected != all_chems else set()
        self._build()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "col-a-select":
            self._custom_col_a = None if event.value is Select.BLANK else str(event.value)
        elif event.select.id == "op-select":
            self._custom_op = None if event.value is Select.BLANK else str(event.value)
        elif event.select.id == "col-b-select":
            self._custom_col_b = None if event.value is Select.BLANK else str(event.value)
        # custom col selects: don't rebuild automatically — wait for Apply button

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "build-btn":
            self._build()
        elif bid == "clear-btn":
            self._packs = []
            self._init_table()
            self.query_one("#detail", Static).update("Select a row to see details.")
            self._set_status("Cleared.")
        elif bid == "apply-btn":
            if not self._custom_col_a or not self._custom_col_b or not self._custom_op:
                self._set_status("[yellow]Select column A, operation, and column B first.[/yellow]")
                return
            self._custom_active = True
            self._sort_col = "__custom__"
            self._sort_asc = True
            self._build()
        elif bid == "clear-custom-btn":
            self._custom_active = False
            if self._sort_col == "__custom__":
                self._sort_col = "price_total"
                self._sort_asc = True
            self._build()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        idx = int(event.row_key.value)
        if 0 <= idx < len(self._packs):
            self._selected = self._packs[idx]
            detail = pack_detail(self._selected)
            if self._custom_active and self._custom_col_a and self._custom_col_b:
                cval = _compute_custom(self._selected, self._custom_col_a,
                                       self._custom_op or "/", self._custom_col_b)
                lbl  = _custom_label(self._custom_col_a, self._custom_op or "/", self._custom_col_b)
                detail += f"\n[bold]{lbl}[/bold]: {_f(cval, 3)}"
            self.query_one("#detail", Static).update(detail)

    def on_data_table_header_selected(self, event: DataTable.HeaderSelected) -> None:
        col_key = str(event.column_key.value)
        if self._sort_col == col_key:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_col = col_key
            self._sort_asc = True
        self._packs = self._sort(self._packs)
        self._refresh_table(self._packs)

    # ── actions ───────────────────────────────────────────────────────────────

    def action_build(self) -> None:
        self._build()

    def action_columns(self) -> None:
        def on_close(hidden: set[str] | None) -> None:
            if hidden is None:
                return
            self._hidden_cols = hidden
            self._init_table()
            self._refresh_table(self._packs)
            self._save_config()
        self.push_screen(ColumnPickerModal(self._hidden_cols), on_close)

    def _export(self, ext: str) -> None:
        if not self._packs:
            self.push_screen(ExportModal("Nothing to export — run Build first."))
            return
        stem = Path(self._data_path).stem
        out  = (Path(self._data_path).parent /
                f"{stem}_pack_{int(self.target_v)}V_{int(self.target_wh)}Wh.{ext}")
        try:
            if ext == "html":
                text = render_html(self._packs, self.target_v, self.target_wh,
                                   self.in_stock, self.v_tol / 100, self.wh_tol / 100)
                out.write_text(text)
            elif ext == "csv":
                out.write_text(render_csv(self._packs), newline="")
            else:
                out.write_text(render_markdown(self._packs) + "\n")
            self.push_screen(ExportModal(f"Saved to:\n{out}"))
        except Exception as e:
            self.push_screen(ExportModal(f"Export failed:\n{e}"))

    def action_export_html(self) -> None: self._export("html")
    def action_export_csv(self)  -> None: self._export("csv")
    def action_export_md(self)   -> None: self._export("md")

    def action_price_curve(self) -> None:
        if not self._cells:
            self.push_screen(ExportModal("No cells loaded — run Build first."))
            return
        out = (Path(self._data_path).parent /
               f"{Path(self._data_path).stem}_price_curve_{int(self.target_v)}V.html")
        try:
            html = render_price_curve(
                self._cells,
                self.target_v, self.target_wh,
                self.v_tol / 100,
                self.in_stock,
                self._chem_filter or None,
            )
            out.write_text(html)
            self.push_screen(ExportModal(f"Price curve saved to:\n{out}"))
        except Exception as e:
            self.push_screen(ExportModal(f"Failed:\n{e}"))


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(f"Usage: python {sys.argv[0]} data.json|csv|md")
        sys.exit(1)
    PackBuilderApp(sys.argv[1]).run()


if __name__ == "__main__":
    main()
