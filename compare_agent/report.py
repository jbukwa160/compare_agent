"""Excel report, laid out like the supplier's input file.

Sheet "Products": the input file's columns (the ones that have data), one row per product, followed by
the agent's result columns (blue headers). Row 2 explains each column. Products we sell (Dilon code in
internal_sku) are tinted blue. Sheet "Shop prices": every shop price that was used, with a link.
"""
from __future__ import annotations

import json
from dataclasses import asdict, fields
from datetime import datetime
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from .catalogue import SELLING
from .models import Evaluation, Market, Offer, Product, Traffic
from .scoring import supplier_price_eur

FONT = "Segoe UI"
INK, INK_2, MUTED = "0B0B0B", "52514E", "898781"
HAIRLINE = "E1E0D9"
INPUT_HEAD, INPUT_NOTE = "EFEEEA", "F7F6F3"           # input-file columns: neutral
RESULT_HEAD, RESULT_NOTE = "DCEAFB", "EEF4FD"          # columns added by the agent: blue
SELLING_ROW = "F2F7FE"                                 # rows of products we sell
BLUE = "2A78D6"
EUR = '#,##0.00 "€"'

TAGS = {"good": ("DFF3DF", "006300"), "warning": ("FEF0CC", "7A5200"), "critical": ("F9DCDC", "9C2323"),
        "neutral": ("F0EFEC", "52514E"), "info": ("DCEAFB", "104281")}
VERDICT = {"BUY": ("✔ BUY", "good"), "CONSIDER": ("~ CONSIDER", "warning"), "SKIP": ("✖ SKIP", "critical"),
           "NEED PRICE": ("? NEED PRICE", "info"), "CHECK": ("? CHECK", "neutral"),
           "NO DATA": ("– NO DATA", "neutral"), "ERROR": ("! ERROR", "critical")}
SELLS = {"Sells well": ("▲ Sells well", "good"), "Average": ("● Average", "warning"), "Weak": ("▼ Weak", "critical")}
TREND = {"Rising": " ↗", "Falling": " ↘", "Stable": " →"}
BARS = "▁▂▃▄▅▆▇█"


def _clean(v: Any) -> Any:
    """Excel refuses control characters, which scraped titles sometimes contain."""
    return ILLEGAL_CHARACTERS_RE.sub("", v) if isinstance(v, str) else v


def _cell(ws: Worksheet, row: int, col: int, value: Any, *, bold=False, size=10, color=INK, fill=None,
          fmt=None, align=None, wrap=False, link=None, italic=False):
    c = ws.cell(row, col, _clean(value))
    c.font = Font(name=FONT, size=size, bold=bold, italic=italic, color=BLUE if link else color,
                  underline="single" if link else None)
    if fill:
        c.fill = PatternFill("solid", fgColor=fill)
    if fmt:
        c.number_format = fmt
    if link:
        c.hyperlink = link
    c.alignment = Alignment(horizontal=align, vertical="center", wrap_text=wrap)
    c.border = Border(bottom=Side(style="thin", color=HAIRLINE))
    return c


def _tag(ws: Worksheet, row: int, col: int, text: str, kind: str):
    bg, fg = TAGS[kind]
    _cell(ws, row, col, text, bold=True, color=fg, fill=bg, align="center")


def sparkline(monthly: dict[str, int]) -> str:
    """12 months of searches as a little bar chart in one cell: ▁▂▃▅▇▅▃."""
    values = [v or 0 for _, v in sorted(monthly.items())][-12:]
    if not values or max(values) == 0:
        return ""
    top = max(max(values), 100)  # at least 100: 10 searches stay low bars, not a dramatic spike
    return "".join(BARS[min(len(BARS) - 1, round(v / top * (len(BARS) - 1)))] for v in values)


def _input_columns(evs: list[Evaluation]) -> list[str]:
    """The input file's columns in their order, leaving out the ones that are empty for every product."""
    order: list[str] = []
    for e in evs:
        for k in e.product.raw:
            if k not in order:
                order.append(k)
    used = [k for k in order if any(e.product.raw.get(k) not in (None, "") for e in evs)]
    return used or ["name", "ean_main"]


def _raw_value(p: Product, column: str) -> Any:
    if p.raw:
        return p.raw.get(column)
    return {"name": p.name, "ean_main": p.ean, "internal_sku": p.sku}.get(column)


def _best(e: Evaluation, scope: str, threshold: float) -> dict[str, Offer]:
    best: dict[str, Offer] = {}
    for o in e.offers:
        if o.scope == scope and o.match_score >= threshold and o.price is not None:
            if o.site not in best or o.match_score > best[o.site].match_score:
                best[o.site] = o
    return best


def _result_columns(world: bool, names: bool = False) -> list[tuple[str, str, int, str]]:
    """(header, explanation, width, number format)"""
    cols = [("Product name", "The supplier file has no names: from our files by EAN, or from the shop "
                             "listing that matched the EAN", 40, "")] if names else []
    cols += [("We sell it", "Yes = the row has a Dilon code (internal_sku)", 11, ""),
            ("Supplier price", "The price the agent used (EUR, without VAT)", 11, EUR),
            ("Supplier price + VAT (20%)", "Supplier price with Bulgarian VAT added - compares with the shop "
                                           "prices, which include VAT", 12, EUR),
            ("Cheapest in Bulgaria", "Cheapest Bulgarian shop that has it, price as shown (with VAT)", 12, EUR),
            ("Cheapest shop", "Click to open that shop's page", 18, ""),
            ("Shops in Bulgaria", "Shops that have it in stock / shops that list it", 11, "")]
    if world:
        cols += [("Cheapest worldwide", "Cheapest abroad, in EUR with 20% VAT added so it compares with the "
                                        "Bulgarian price", 12, EUR),
                 ("Cheapest worldwide shop", "Shop and country. Click to open the offer", 24, "")]
    cols += [("Margin in Bulgaria", "(Bulgarian price without VAT - supplier price) / Bulgarian price without "
                                    "VAT. Not sold in Bulgaria = the worldwide middle price is used", 10, "0.0%")]
    if world:
        cols += [("Margin abroad", "Selling at the cheapest worldwide shop's price: (that price without VAT - "
                                   "supplier price) / that price without VAT", 10, "0.0%")]
    cols += [("Searches / month", "Google searches per month in Bulgaria", 10, "#,##0"),
             ("Last 12 months", "Searches month by month (oldest left). Arrow = trend of the last 3 months", 17, ""),
             ("Sells", "▲ well · ● average · ▼ weak (searches + how many shops sell it)", 13, ""),
             ("Score 1-10", "Buy price + sell price + sales together. Higher = better", 9, "0.0"),
             ("Verdict", "BUY / CONSIDER / SKIP. NEED PRICE = add the supplier price to get a verdict", 14, "")]
    return cols


def _products_sheet(wb: Workbook, evs: list[Evaluation], settings: dict[str, Any], world: bool) -> None:
    ws = wb.create_sheet("Products")
    ws.sheet_view.showGridLines = False
    threshold = settings["scoring"]["match_threshold"]
    inputs = _input_columns(evs)
    results = _result_columns(world, names=any(e.product.name_source for e in evs))

    # header (row 1) + one-line explanation (row 2)
    for i, name in enumerate(inputs, 1):
        _cell(ws, 1, i, name, bold=True, fill=INPUT_HEAD)
        _cell(ws, 2, i, "from the input file", size=8, italic=True, color=MUTED, fill=INPUT_NOTE)
        longest = max([len(str(name))] + [len(str(_raw_value(e.product, name) or "")) for e in evs])
        ws.column_dimensions[get_column_letter(i)].width = max(10, min(46, longest + 2))
    first = len(inputs) + 1
    for j, (name, note, width, _) in enumerate(results):
        _cell(ws, 1, first + j, name, bold=True, color="104281", fill=RESULT_HEAD, align="center", wrap=True)
        _cell(ws, 2, first + j, note, size=8, italic=True, color=INK_2, fill=RESULT_NOTE, wrap=True)
        ws.column_dimensions[get_column_letter(first + j)].width = width
    ws.row_dimensions[1].height = 30
    ws.row_dimensions[2].height = 48

    col = {name: first + j for j, (name, _, _, _) in enumerate(results)}
    fmt = {name: f for name, _, _, f in results}
    for r, e in enumerate(sorted(evs, key=lambda e: e.product.row), 3):
        p = e.product
        selling = p.catalogue_group == SELLING
        fill = SELLING_ROW if selling else None
        for i, name in enumerate(inputs, 1):
            value = _raw_value(p, name)
            _cell(ws, r, i, value, fill=fill, bold=selling and name in ("name", "internal_sku"),
                  fmt="@" if isinstance(value, str) and value.isdigit() else None)

        bg = _best(e, "local", threshold)
        # prices far from the rest of the market (one overpriced marketplace seller) are not used
        unusual = [o for o in bg.values() if "price outlier" in o.match_reason]
        stocked = [o for o in bg.values() if o.in_stock is not False and o not in unusual]
        cheapest = min(stocked, key=lambda o: o.price) if stocked else None
        buy = supplier_price_eur(p, settings)
        # cheapest abroad: matched, in stock, not an unusual price; compared without VAT, shown with BG VAT
        abroad = [o for o in e.offers if o.scope == "global" and o.match_score >= threshold
                  and o.price_eur_net and o.in_stock is not False]
        world_best = min(abroad, key=lambda o: o.price_eur_net) if abroad else None
        bg_vat = settings["vat_by_country"].get("BG", 0.20)
        world_price = round(world_best.price_eur_net * (1 + bg_vat), 2) if world_best else None
        vat = settings["vat_by_country"].get("BG", 0.20)
        values = {"Product name": p.name or "(unknown - no shop matched the EAN)",
                  "Supplier price": round(buy, 2) if buy is not None else None,
                  "Supplier price + VAT (20%)": round(buy * (1 + vat), 2) if buy is not None else None,
                  "Cheapest in Bulgaria": cheapest.price if cheapest else None,
                  "Shops in Bulgaria": f"{sum(1 for o in bg.values() if o.in_stock)} / {len(bg)}" if bg else "0",
                  "Cheapest worldwide": world_price,
                  "Margin in Bulgaria": e.margin,
                  "Margin abroad": round((world_best.price_eur_net - buy) / world_best.price_eur_net, 4)
                  if world_best and buy is not None else None,
                  "Searches / month": e.local.searches,
                  "Last 12 months": (sparkline(e.local.monthly or e.global_.monthly) + TREND.get(e.trend, "")).strip(),
                  "Score 1-10": e.final_score}
        for name, c in col.items():
            if name in values:
                _cell(ws, r, c, values[name], fmt=fmt[name] or None, fill=fill,
                      align="center" if name in ("Shops in Bulgaria", "Last 12 months") else None)
        if world and world_best:
            _cell(ws, r, col["Cheapest worldwide shop"], world_best.site, link=world_best.url, fill=fill)
        elif world:
            _cell(ws, r, col["Cheapest worldwide shop"], "not found abroad", color=MUTED, italic=True, fill=fill)
        if cheapest:
            _cell(ws, r, col["Cheapest shop"], cheapest.site, link=cheapest.url, fill=fill)
        else:
            if unusual:
                o = min(unusual, key=lambda o: o.price)
                text = f"{o.site} {o.price:,.2f} € - unusual price, not used"
            else:
                text = "not sold in Bulgaria" if not bg else "out of stock everywhere"
            _cell(ws, r, col["Cheapest shop"], text, color=MUTED, italic=True, fill=fill)
        if selling:
            _tag(ws, r, col["We sell it"], "✔ Yes", "info")
        else:
            _cell(ws, r, col["We sell it"], "No", color=MUTED, align="center")
        if e.sells in SELLS:
            _tag(ws, r, col["Sells"], *SELLS[e.sells])
        else:
            _cell(ws, r, col["Sells"], "– no data", color=MUTED, align="center", fill=fill)
        _tag(ws, r, col["Verdict"], *VERDICT.get(e.verdict, (e.verdict, "neutral")))
        ws.cell(r, col["Last 12 months"]).font = Font(name=FONT, size=11, color=BLUE)

    last = 2 + len(evs)
    ws.auto_filter.ref = f"A1:{get_column_letter(first + len(results) - 1)}{last}"
    name_col = inputs.index("name") + 2 if "name" in inputs else 1
    ws.freeze_panes = ws.cell(3, name_col)  # headers and the product name stay visible while scrolling


def _shop_prices_sheet(wb: Workbook, evs: list[Evaluation], settings: dict[str, Any]) -> None:
    ws = wb.create_sheet("Shop prices")
    ws.sheet_view.showGridLines = False
    threshold = settings["scoring"]["match_threshold"]
    headers = [("Product", 44), ("We sell it", 10), ("Shop", 18), ("Where", 10), ("Price", 11), ("Currency", 9),
               ("In stock", 12), ("Shop's product title", 55), ("Link", 14)]
    for i, (h, w) in enumerate(headers, 1):
        _cell(ws, 1, i, h, bold=True, fill=INPUT_HEAD)
        ws.column_dimensions[get_column_letter(i)].width = w
    r = 2
    for e in sorted(evs, key=lambda e: e.product.row):
        offers = [o for scope in ("local", "global") for o in _best(e, scope, threshold).values()]
        for o in sorted(offers, key=lambda o: (o.scope != "local", o.price_eur_net or o.price or 0)):
            selling = e.product.catalogue_group == SELLING
            _cell(ws, r, 1, e.product.name, bold=selling, fill=SELLING_ROW if selling else None)
            _cell(ws, r, 2, "Yes" if selling else "No", color=INK if selling else MUTED)
            _cell(ws, r, 3, o.site)
            _cell(ws, r, 4, "Bulgaria" if o.scope == "local" else "Abroad")
            _cell(ws, r, 5, o.price, fmt="#,##0.00")
            _cell(ws, r, 6, o.currency)
            stock = {True: ("in stock", "good"), None: ("on order", "warning"), False: ("out of stock", "critical")}
            _tag(ws, r, 7, *stock[o.in_stock])
            _cell(ws, r, 8, o.title)
            _cell(ws, r, 9, "open", link=o.url)
            r += 1
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{max(2, r - 1)}"
    ws.freeze_panes = "B2"


def write_report(evaluations: list[Evaluation], settings: dict[str, Any], path: Path,
                 local_sites: list[str] | None = None, world_sites: list[str] | None = None,
                 meta: dict[str, Any] | None = None) -> Path:
    meta = meta or {}
    world = meta.get("scope") != "local" and any(o.scope == "global" for e in evaluations for o in e.offers)
    wb = Workbook()
    wb.remove(wb.active)
    _products_sheet(wb, evaluations, settings, world)
    _shop_prices_sheet(wb, evaluations, settings)
    wb.active = 0

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        wb.save(path)
    except PermissionError:  # the old report is open in Excel: keep the results under a new name
        path = path.with_name(f"{path.stem}_{datetime.now():%H%M%S}{path.suffix}")
        wb.save(path)
    return path


def write_json(evaluations: list[Evaluation], path: Path) -> Path:
    data = []
    for ev in evaluations:
        d = asdict(ev)
        d["global"] = d.pop("global_")
        data.append(d)
    text = json.dumps(data, ensure_ascii=False, indent=1, default=str)
    try:
        path.write_text(text, encoding="utf-8")
    except PermissionError:
        path = path.with_name(f"{path.stem}_{datetime.now():%H%M%S}{path.suffix}")
        path.write_text(text, encoding="utf-8")
    return path


def _build(cls, d: dict[str, Any]):
    names = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in d.items() if k in names})


def read_json(path: Path) -> list[Evaluation]:
    """Load the results a run saved next to its report (to rebuild a report without searching again)."""
    out = []
    for d in json.loads(Path(path).read_text(encoding="utf-8")):
        ev = _build(Evaluation, {**d, "product": _build(Product, d["product"]),
                                 "local": _build(Market, d["local"]), "global_": _build(Market, d["global"]),
                                 "offers": [_build(Offer, o) for o in d["offers"]],
                                 "traffic": [_build(Traffic, t) for t in d["traffic"]]})
        out.append(ev)
    return out
