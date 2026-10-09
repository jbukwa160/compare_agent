"""Rebuild the Excel report from a finished run's saved results - no searching again.

Every run saves its results next to the report (same name, .json). This rebuilds the .xlsx from them,
e.g. after a change to the report layout, re-reading the input file for its original columns.

    python scripts/rebuild_report.py output/bulgaria_test.json input/products.xlsx --scope local
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from compare_agent.catalogue import assign_groups  # noqa: E402
from compare_agent.config import load_settings  # noqa: E402
from compare_agent.loader import load_products  # noqa: E402
from compare_agent.report import read_json, write_report  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("results", type=Path, help="the run's .json")
    ap.add_argument("input", type=Path, help="the supplier file that run used")
    ap.add_argument("--scope", choices=["local", "global", "both"], default="both")
    ap.add_argument("--out", type=Path, help="default: the .json name with .xlsx")
    args = ap.parse_args()

    settings = load_settings()
    evs = read_json(args.results)
    by_row = {p.row: p for p in load_products(args.input, settings)}
    for e in evs:
        fresh = by_row.get(e.product.row)
        if fresh:  # original columns + current reading of the file (e.g. a price column added since)
            e.product.raw, e.product.sku = fresh.raw, fresh.sku
    assign_groups([e.product for e in evs])
    out = write_report(evs, settings, args.out or args.results.with_suffix(".xlsx"), meta={"scope": args.scope})
    print(out)


if __name__ == "__main__":
    main()
