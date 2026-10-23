"""Paths and the three YAML files (settings, shops, review)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CACHE = ROOT / ".cache" / "top_products"
OUTPUT = ROOT / "output"
EXCEL_NAME = "Топ продукти по трафик.xlsx"


def _yaml(name: str) -> dict[str, Any]:
    return yaml.safe_load((HERE / name).read_text(encoding="utf-8")) or {}


def settings() -> dict[str, Any]:
    return _yaml("settings.yaml")


def shops() -> list[dict[str, Any]]:
    """Every shop with its category, in the order of shops.yaml."""
    return [{**s, "category": cat} for cat, items in _yaml("shops.yaml").items() for s in items or []]


def review() -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    """(accepted, rejected) name matches as (shop page URL, vendor barcode)."""
    data = _yaml("review.yaml")
    pick = lambda key: {(r["url"], str(r["ean"]).zfill(13)) for r in data.get(key) or []}  # noqa: E731
    return pick("accept"), pick("reject")
