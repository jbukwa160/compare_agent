"""Reads the supplier product file (.xlsx / .csv) into Product objects.

Column names are matched through the aliases in settings.yaml -> input.columns,
so files from different suppliers work without editing code.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from .models import Product


def normalize_ean(value: Any) -> Optional[str]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, float):
        value = f"{value:.0f}"
    digits = re.sub(r"\D", "", str(value))
    if len(digits) < 8:
        return None
    # UPC-A (12) -> EAN-13 by left-padding, which is how most shops store it
    return digits.zfill(13) if len(digits) == 12 else digits


def _to_float(value: Any) -> Optional[float]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(" ", "").replace(" ", "")
    text = re.sub(r"[^\d,.\-]", "", text)
    if not text:
        return None
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".") if text.rfind(",") > text.rfind(".") else text.replace(",", "")
    else:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def _clean(value: Any) -> Optional[str]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    return text or None


def _col_key(name: Any) -> str:
    """'Product name', 'product_name', 'PRODUCT-NAME' all compare equal."""
    return re.sub(r"[\s_\-]+", "_", str(name).strip().lower())


# When no exact alias matches: a column whose title CONTAINS one of these words (supplier files name
# columns freely: "EAN code", "Product description", "Net price EUR"). Retail-price columns are never
# taken as the supplier price.
CONTAINS = {
    "ean": (["ean", "gtin", "barcode", "баркод", "upc"], []),
    "name": (["product", "item", "description", "наименование", "продукт", "артикул"], ["code", "id", "№", "no."]),
    "supplier_price": (["price", "cost", "цена"], ["rrp", "msrp", "retail", "recommended", "пцд", "дребно", "srp"]),
}


def _resolve_columns(df: pd.DataFrame, aliases: dict[str, list[str]]) -> dict[str, str]:
    lookup = {_col_key(c): c for c in df.columns}
    resolved = {}
    for field, names in aliases.items():
        for name in names:
            if _col_key(name) in lookup:
                resolved[field] = lookup[_col_key(name)]
                break
    taken = set(resolved.values())
    for field, (words, never) in CONTAINS.items():
        if field in resolved:
            continue
        for col in df.columns:
            title = str(col).lower()
            if col not in taken and any(w in title for w in words) and not any(n in title for n in never):
                resolved[field] = col
                taken.add(col)
                break
    return resolved


def _header_score(values: list[Any], aliases: dict[str, list[str]]) -> int:
    """How many known fields this row names (a product name or an EAN column is required)."""
    titles = [v for v in values if v not in (None, "") and not (isinstance(v, float) and pd.isna(v))]
    resolved = _resolve_columns(pd.DataFrame(columns=[str(t) for t in titles]), aliases)
    return len(resolved) if ("name" in resolved or "ean" in resolved) else 0


HEADER_SCAN_ROWS = 30  # supplier price lists often have a logo / title block above the column names


def _read_table(path: Path, aliases: dict[str, list[str]], sheet: str | int | None) -> tuple[pd.DataFrame, int]:
    """Find the sheet and header row that hold the products. Returns (table, Excel row of the header)."""
    if path.suffix.lower() in (".xlsx", ".xlsm", ".xls"):
        frames = pd.read_excel(path, sheet_name=sheet if sheet is not None else None, header=None, dtype=object)
        if isinstance(frames, pd.DataFrame):
            frames = {sheet: frames}
    else:
        frames = {None: pd.read_csv(path, dtype=str, sep=None, engine="python", encoding="utf-8-sig", header=None)}
    best = (0, None, 0)  # (score, sheet, header row index)
    for name, raw in frames.items():
        for i in range(min(HEADER_SCAN_ROWS, len(raw))):
            score = _header_score(list(raw.iloc[i]), aliases)
            if score > best[0]:
                best = (score, name, i)
    score, name, i = best
    if score == 0:
        first = next(iter(frames.values()))
        raise ValueError(
            f"No product name or EAN column found in {path.name} (looked at the first {HEADER_SCAN_ROWS} rows of "
            f"{len(frames)} sheet(s)). First row: {[v for v in first.iloc[0].tolist() if pd.notna(v)][:12] if len(first) else []}. "
            f"Accepted names: {aliases.get('name')} - add the supplier's column name under input.columns.name "
            "in config/settings.yaml")
    raw = frames[name]
    headers, seen = [], set()
    for j, v in enumerate(raw.iloc[i]):
        h = str(v).strip() if v is not None and not (isinstance(v, float) and pd.isna(v)) else f"Column {j + 1}"
        while h in seen:  # duplicate column names in the file
            h += " (2)"
        seen.add(h)
        headers.append(h)
    table = raw.iloc[i + 1:].copy()
    table.columns = headers
    table = table.dropna(axis=1, how="all") if len(table) else table
    return table, i + 1


def load_products(path: Path, settings: dict[str, Any], sheet: str | int | None = None) -> list[Product]:
    path = Path(path)
    input_cfg = settings.get("input", {})
    df, _ = _read_table(path, input_cfg.get("columns", {}), sheet)

    cols = _resolve_columns(df, input_cfg.get("columns", {}))
    # the supplier price may sit in any of its alias columns (e.g. acquisition_price empty, sale_price filled):
    # per row, the first filled one in the order listed in settings.yaml wins
    lookup = {_col_key(c): c for c in df.columns}
    price_cols = [lookup[_col_key(n)] for n in input_cfg.get("columns", {}).get("supplier_price", [])
                  if _col_key(n) in lookup]
    if not price_cols and "supplier_price" in cols:
        price_cols = [cols["supplier_price"]]

    default_currency = input_cfg.get("default_supplier_currency", "EUR")
    products = []
    for idx, row in df.iterrows():
        get = lambda f: row[cols[f]] if f in cols else None  # noqa: E731
        name = _clean(get("name")) or ""
        ean = normalize_ean(get("ean"))
        if not name and not ean:  # empty / note rows
            continue
        qty = _to_float(get("order_qty"))
        products.append(Product(
            row=int(idx) + 1,  # Excel row number (idx counts from 0 at the file's first row)
            name=name,
            ean=ean,
            sku=_clean(get("sku")),
            brand=_clean(get("brand")),
            category=_clean(get("category")),
            supplier_price=next((v for v in (_to_float(row[c]) for c in price_cols) if v is not None), None),
            supplier_currency=(_clean(get("supplier_currency")) or default_currency).upper(),
            order_qty=int(qty) if qty else None,
            supplier=_clean(get("supplier")),
            keyword=_clean(get("keyword")),
            raw={str(c): (None if v is None or (isinstance(v, float) and pd.isna(v)) else v)
                 for c, v in row.items()},
        ))
    return products
