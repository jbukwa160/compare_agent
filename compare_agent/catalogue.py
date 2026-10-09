"""Which products in the supplier file Dilon already sells.

The supplier file says it itself: rows we sell carry our Dilon code in the internal_sku column
(e.g. "Dilon-165687"); rows without one are products we don't sell yet.
"""
from __future__ import annotations

import csv
from pathlib import Path

from .models import Product

SELLING = "Selling"
NOT_SELLING = "Not selling"


def assign_groups(products: list[Product]) -> None:
    for p in products:
        p.catalogue_group = SELLING if (p.sku or "").strip() else NOT_SELLING


def _names_from_file(path: Path, wanted: set[str], settings: dict) -> dict[str, str]:
    """EAN (without leading zeros) -> product name, for the EANs we need."""
    found: dict[str, str] = {}
    if path.suffix.lower() == ".csv" and path.stat().st_size > 20_000_000:  # the big shop export: stream it
        csv.field_size_limit(10 ** 9)
        with open(path, encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            ean_col = next((c for c in reader.fieldnames or [] if c.strip().lower() in ("ean", "ean_main", "gtin")), None)
            name_col = next((c for c in reader.fieldnames or [] if c.strip().lower() in ("name", "product name")), None)
            if not (ean_col and name_col):
                return found
            for row in reader:
                ean = (row.get(ean_col) or "").strip().lstrip("0")
                if ean in wanted and ean not in found and (row.get(name_col) or "").strip():
                    found[ean] = row[name_col].strip()
        return found
    from .loader import load_products  # small product files: same reader as supplier files
    for p in load_products(path, settings):
        if p.ean and p.name and p.ean.lstrip("0") in wanted:
            found.setdefault(p.ean.lstrip("0"), p.name)
    return found


def fill_names(products: list[Product], settings: dict, root: Path) -> int:
    """Supplier files without product names: take the name from our own files by EAN (settings.yaml
    product_names, first file that knows the EAN wins). Returns how many names were filled."""
    missing = {p.ean.lstrip("0"): p for p in products if not p.name and p.ean}
    filled = 0
    for rel in settings.get("product_names") or []:
        if not missing:
            break
        path = (root / rel).resolve()
        if not path.exists():
            continue
        try:
            names = _names_from_file(path, set(missing), settings)
        except Exception:  # a broken lookup file must not stop the run
            continue
        for ean, name in names.items():
            p = missing.pop(ean)
            p.name, p.name_source = name, path.name
            filled += 1
    return filled
