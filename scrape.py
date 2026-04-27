#!/usr/bin/env python3
"""NKON battery listing scraper.

Usage:
    python scrape.py [URL]

Defaults to the LiFePO4 prismatic listing if no URL is given.
Fetches each product's detail page for full specs, then outputs a markdown
table sorted by Wh/€ descending.
"""

import re
import sys
import time
import requests
from bs4 import BeautifulSoup

DEFAULT_URL = (
    "https://www.nkon.nl/en/rechargeable/lifepo4/prismatisch.html"
    "?___store=novat&product_list_order=loodaccuah&product_list_dir=desc"
)

HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
DELAY = 0.5  # seconds between detail page requests


def get(url: str) -> BeautifulSoup:
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


def page_url(base: str, page: int) -> str:
    sep = "&" if "?" in base else "?"
    return base if page == 1 else f"{base}{sep}p={page}"


def detect_type_filters(soup: BeautifulSoup) -> dict[str, str]:
    """Return {type_name: url} for any chemistry filter links on the page."""
    filters = {}
    for a in soup.find_all("a", href=True):
        if a.find("span", class_="count") and a.find("span", class_="filter-count-label"):
            name = a.get_text(separator=" ", strip=True)
            # strip the trailing "N items" suffix
            name = re.sub(r"\s*\d+\s*items?\s*$", "", name, flags=re.I).strip()
            if name:
                filters[name] = a["href"]
    return filters


# ---------- listing page ----------

def listing_products_one_page(soup: BeautifulSoup) -> list[dict]:
    products = []
    for item in soup.select(".product-item"):
        name_el = item.select_one(".product-item-link")
        price_el = item.select_one(
            '.price-wrapper.price-including-tax[data-price-type="finalPrice"]'
        )
        if not name_el:
            continue
        price_raw = price_el["data-price-amount"] if price_el else None
        products.append(
            {
                "name": name_el.get_text(strip=True),
                "url": name_el["href"],
                "price_incl": float(price_raw) if price_raw else None,
            }
        )
    return products


def listing_products(base_url: str) -> list[dict]:
    """Walk all pagination pages and return every product stub."""
    all_products: list[dict] = []
    seen_urls: set[str] = set()
    page = 1
    while True:
        url = page_url(base_url, page)
        print(f"  Fetching page {page}: {url}", file=sys.stderr)
        soup = get(url)
        batch = listing_products_one_page(soup)
        new = [p for p in batch if p["url"] not in seen_urls]
        if not new:
            break
        for p in new:
            seen_urls.add(p["url"])
        all_products.extend(new)
        # Stop if no next-page link exists
        if not soup.select_one('.pages-items a[href*="p="]'):
            break
        # Stop if current page has no next link beyond this page
        next_pages = [
            int(m.group(1))
            for a in soup.select('.pages-items a[href*="p="]')
            if (m := re.search(r"[?&]p=(\d+)", a["href"]))
        ]
        if not next_pages or max(next_pages) <= page:
            break
        page += 1
        time.sleep(DELAY)
    return all_products


# ---------- detail page ----------

def _attr_table(soup: BeautifulSoup) -> dict[str, str]:
    attrs = {}
    for table in soup.select("table.data.table.additional-attributes"):
        for row in table.select("tr"):
            th = row.select_one("th")
            td = row.select_one("td")
            if th and td:
                attrs[th.get_text(strip=True)] = td.get_text(strip=True)
    return attrs


def _tier_prices(soup: BeautifulSoup) -> list[dict]:
    """Parse quantity-break pricing rows."""
    tiers = []
    tier_el = soup.select_one(".prices-tier")
    if not tier_el:
        return tiers
    # Each tier is a <li> element
    for li in tier_el.select("li"):
        text = li.get_text(" ", strip=True)
        qty_m = re.search(r"Buy\s+(\d+)\s+piece", text, re.I)
        price_m = re.search(r"€\s*([\d.,]+)", text)
        if qty_m and price_m:
            tiers.append(
                {
                    "qty": int(qty_m.group(1)),
                    "price_incl": float(price_m.group(1).replace(",", ".")),
                }
            )
    return tiers


def detail(url: str) -> dict:
    soup = get(url)
    attrs = _attr_table(soup)

    # Price incl. VAT (unit price)
    price_el = soup.select_one(
        '.price-wrapper.price-including-tax[data-price-type="finalPrice"]'
    )
    price_incl = float(price_el["data-price-amount"]) if price_el else None

    # Price excl. VAT
    excl_m = re.search(
        r"Excl\. Tax:.*?€\s*([\d.,]+)",
        soup.get_text(" ", strip=True),
    )
    price_excl = float(excl_m.group(1).replace(",", ".")) if excl_m else None

    # Stock
    stock_el = soup.select_one(".stock")
    in_stock = (
        "in stock" in stock_el.get_text(strip=True).lower() if stock_el else None
    )

    tiers = _tier_prices(soup)

    def num(key: str) -> float | None:
        v = attrs.get(key, "").replace(",", ".")
        try:
            return float(re.sub(r"[^\d.]", "", v)) if v else None
        except ValueError:
            return None

    voltage = num("Voltage")
    weight_g = num("Weight - g")

    # Capacity: prismatic cells use "Capacity - Ah"; cylindrical use mAh keys
    capacity_ah = num("Capacity - Ah")
    if capacity_ah is None:
        mah = num("Typ. capacity - mAh") or num("Min. capacity - mAh")
        capacity_ah = round(mah / 1000, 4) if mah else None

    discharge_a = num("Discharge current - A")

    wh = round(voltage * capacity_ah, 4) if voltage and capacity_ah else None
    eur_per_wh = round(price_incl / wh, 4) if wh and price_incl else None
    wh_per_eur = round(wh / price_incl, 2) if wh and price_incl else None
    wh_per_kg = round(wh / (weight_g / 1000), 1) if wh and weight_g else None
    c_rate = round(discharge_a / capacity_ah, 2) if discharge_a and capacity_ah else None

    # Dimensions: prismatic = H×W×T; cylindrical = H×⌀D
    h = num("Height - mm")
    diam = num("Diameter in mm")
    w, t = num("Width - mm"), num("Thickness - mm")
    if h and diam:
        dims = f"{h}×⌀{diam}"
        vol_l = round(__import__("math").pi * (diam / 2) ** 2 * h / 1_000_000, 4)
    elif h and w and t:
        dims = f"{h}×{w}×{t}"
        vol_l = round(h * w * t / 1_000_000, 4)
    else:
        dims = None
        vol_l = None

    wh_per_l = round(wh / vol_l, 1) if wh and vol_l else None

    return {
        "price_incl": price_incl,
        "price_excl": price_excl,
        "in_stock": in_stock,
        "tiers": tiers,
        "ean": attrs.get("EAN / GTIN"),
        "brand": attrs.get("Brand"),
        "model": attrs.get("Model"),
        "grade": attrs.get("Grade"),
        "chemistry": attrs.get("Battery chemistry"),
        "size": attrs.get("Size"),
        "form_factor": attrs.get("Battery version"),
        "protection": attrs.get("Circuit protection"),
        "voltage_v": voltage,
        "capacity_ah": capacity_ah,
        "discharge_a": discharge_a,
        "c_rate": c_rate,
        "weight_g": weight_g,
        "vol_l": vol_l,
        "terminal": attrs.get("Terminal type"),
        "dims_mm": dims,
        "included": attrs.get("Included"),
        "year": attrs.get("Year of production"),
        "wh": wh,
        "eur_per_wh": eur_per_wh,
        "wh_per_eur": wh_per_eur,
        "wh_per_kg": wh_per_kg,
        "wh_per_l": wh_per_l,
    }


# ---------- formatting ----------

def f(value, decimals=2, suffix="") -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{decimals}f}{suffix}"
    return str(value)


def tier_str(tiers: list[dict]) -> str:
    if not tiers:
        return "—"
    return " / ".join(f"{t['qty']}×€{t['price_incl']:.2f}" for t in tiers)


# Columns: (header, align, field_fn)
def _columns() -> list[tuple]:
    return [
        ("Rank",                    "r", lambda i, p: str(i)),
        ("Name",                    "l", lambda i, p: p.get("name") or "—"),
        ("Brand",                   "l", lambda i, p: p.get("brand") or "—"),
        ("Model",                   "l", lambda i, p: p.get("model") or "—"),
        ("Chemistry",               "l", lambda i, p: p.get("chemistry") or "—"),
        ("Size",                    "l", lambda i, p: p.get("size") or "—"),
        ("Form factor",             "l", lambda i, p: p.get("form_factor") or "—"),
        ("Protection",              "l", lambda i, p: p.get("protection") or "—"),
        ("Grade",                   "l", lambda i, p: p.get("grade") or "—"),
        ("V",                       "r", lambda i, p: f(p.get("voltage_v"))),
        ("Ah",                      "r", lambda i, p: f(p.get("capacity_ah"), 4)),
        ("Wh",                      "r", lambda i, p: f(p.get("wh"), 3)),
        ("Price €",                 "r", lambda i, p: f(p.get("price_incl"))),
        ("€/Wh",                    "r", lambda i, p: f(p.get("eur_per_wh"), 4)),
        ("Wh/€",                    "r", lambda i, p: f(p.get("wh_per_eur"))),
        ("Wh/kg",                   "r", lambda i, p: f(p.get("wh_per_kg"), 1)),
        ("Wh/L",                    "r", lambda i, p: f(p.get("wh_per_l"), 1)),
        ("C-rate",                  "r", lambda i, p: f(p.get("c_rate"))),
        ("Weight g",                "r", lambda i, p: f(p.get("weight_g"), 0)),
        ("Vol L",                   "r", lambda i, p: f(p.get("vol_l"), 4)),
        ("Disch A",                 "r", lambda i, p: f(p.get("discharge_a"))),
        ("Terminal",                "l", lambda i, p: p.get("terminal") or "—"),
        ("Dims mm",                 "l", lambda i, p: p.get("dims_mm") or "—"),
        ("Included",                "l", lambda i, p: p.get("included") or "—"),
        ("Year",                    "l", lambda i, p: p.get("year") or "—"),
        ("Bulk pricing (qty×€/unit)","l", lambda i, p: tier_str(p.get("tiers", []))),
        ("In stock",                "l", lambda i, p: "yes" if p.get("in_stock") else "no" if p.get("in_stock") is False else "—"),
    ]


def render_markdown(products: list[dict]) -> str:
    cols = _columns()
    header = "| " + " | ".join(c[0] for c in cols) + " |"
    sep = "| " + " | ".join("---:" if c[1] == "r" else ":---" for c in cols) + " |"
    rows = [
        "| " + " | ".join(c[2](i, p) for c in cols) + " |"
        for i, p in enumerate(products, 1)
    ]
    return "\n".join([header, sep] + rows)


def render_csv(products: list[dict]) -> str:
    import csv, io
    cols = _columns()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([c[0] for c in cols])
    for i, p in enumerate(products, 1):
        w.writerow([c[2](i, p).replace("—", "") for c in cols])
    return buf.getvalue()


# ---------- main ----------

def parse_args():
    import argparse
    p = argparse.ArgumentParser(description="NKON battery listing scraper")
    p.add_argument("url", nargs="?", default=DEFAULT_URL, help="Listing URL to scrape")
    p.add_argument("-o", "--output", metavar="FILE", help="Write markdown table to FILE")
    p.add_argument("--type", metavar="TYPE", dest="chem_type",
                   help="Filter by chemistry type (e.g. Li-ion, LiFePO4, NiMH). "
                        "On category pages this selects the matching sub-listing.")
    p.add_argument("--list-types", action="store_true",
                   help="List available chemistry types on the given page and exit")
    stock = p.add_mutually_exclusive_group()
    stock.add_argument("--in-stock", action="store_true", help="Only show in-stock items")
    stock.add_argument("--out-of-stock", action="store_true", help="Only show out-of-stock items")
    return p.parse_args()


def resolve_url(args) -> str:
    """If the page has chemistry filter links, route --type to the right sub-URL."""
    soup = get(args.url)
    type_filters = detect_type_filters(soup)

    if args.list_types:
        if type_filters:
            print("Available types:")
            for name, url in type_filters.items():
                print(f"  {name:20s} -> {url}")
        else:
            print("No type filters found on this page.")
        sys.exit(0)

    if not type_filters or not args.chem_type:
        return args.url

    # Case-insensitive match against discovered type names
    needle = args.chem_type.lower()
    for name, url in type_filters.items():
        if needle in name.lower() or name.lower() in needle:
            print(f"Resolved type '{args.chem_type}' -> {url}", file=sys.stderr)
            return url

    print(f"Unknown type '{args.chem_type}'. Available: {', '.join(type_filters)}", file=sys.stderr)
    sys.exit(1)


def main():
    args = parse_args()
    url = resolve_url(args)
    print(f"Fetching listing: {url}", file=sys.stderr)
    stubs = listing_products(url)
    print(f"Found {len(stubs)} products — fetching detail pages…", file=sys.stderr)

    results = []
    for i, stub in enumerate(stubs, 1):
        print(f"  [{i}/{len(stubs)}] {stub['name']}", file=sys.stderr)
        try:
            d = detail(stub["url"])
        except Exception as exc:
            print(f"    ERROR: {exc}", file=sys.stderr)
            d = {}
        if not d.get("price_incl") and stub.get("price_incl"):
            d["price_incl"] = stub["price_incl"]
        d["name"] = stub["name"]
        d["url"] = stub["url"]
        results.append(d)
        time.sleep(DELAY)

    # Apply stock filter
    if args.in_stock:
        results = [p for p in results if p.get("in_stock") is True]
        print(f"Filtered to {len(results)} in-stock items.", file=sys.stderr)
    elif args.out_of_stock:
        results = [p for p in results if p.get("in_stock") is False]
        print(f"Filtered to {len(results)} out-of-stock items.", file=sys.stderr)

    # Sort by Wh/€ descending; unknowns last
    results.sort(key=lambda p: p.get("wh_per_eur") or -1, reverse=True)

    print(f"\nDone. Rendering table.\n", file=sys.stderr)
    ext = args.output.lower().rsplit(".", 1)[-1] if args.output else "md"

    if ext == "json":
        import json
        def serialisable(p):
            out = {k: v for k, v in p.items() if k != "tiers"}
            out["tiers"] = p.get("tiers", [])
            return out
        payload = [serialisable(p) for p in results]
        text = json.dumps(payload, indent=2, ensure_ascii=False)
        with open(args.output, "w") as fh:
            fh.write(text)
        print(f"Written to {args.output}", file=sys.stderr)
    elif ext == "csv":
        table = render_csv(results)
        with open(args.output, "w", newline="") as fh:
            fh.write(table)
        print(f"Written to {args.output}", file=sys.stderr)
    elif args.output:
        table = render_markdown(results)
        with open(args.output, "w") as fh:
            fh.write(table + "\n")
        print(f"Written to {args.output}", file=sys.stderr)
    else:
        print(render_markdown(results))


if __name__ == "__main__":
    main()
