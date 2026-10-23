"""python -m top_products [--vendors ZIP_OR_FOLDER] [--excel FILE] [--traffic [--budget USD]]

Rebuilds the Excel "Топ продукти по трафик" in output/:
  1. the traffic data: read from the current Excel, or with --traffic fetched again from DataForSEO (paid)
  2. the EU vendors' price lists (the "EU vendors" .zip from Google Drive): which products they offer,
     the profit against the cheapest Bulgarian shop and a 1-10 score (sheet "EU доставчици")
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import barcodes, config, matching, scoring, traffic, vendors, workbook


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m top_products", description=__doc__.split("\n\n")[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vendors", help='the "EU vendors" .zip or folder (default: the newest one here, in input/ or Downloads)')
    ap.add_argument("--excel", help="the workbook to update (default: the newest output/Топ продукти по трафик*.xlsx)")
    ap.add_argument("--traffic", action="store_true", help="fetch the traffic data again from DataForSEO (paid)")
    ap.add_argument("--budget", type=float, help="USD cap for --traffic (default: settings.yaml traffic.budget_usd)")
    args = ap.parse_args(argv)
    settings = config.settings()

    excel = Path(args.excel) if args.excel else workbook.find(config.OUTPUT, config.EXCEL_NAME)
    if args.traffic:
        shops, rows, meta = traffic.run(args.budget)
    elif excel.exists():
        shops, rows, meta = workbook.read(excel)
        print(f"Traffic data: {excel.name} ({len(rows)} products, {len(shops)} shops)")
    else:
        print(f"No workbook yet ({excel}). Run with --traffic to fetch the traffic data (paid DataForSEO).")
        return 1

    meta["checked_vendors"] = time.strftime("%d.%m.%Y")
    source, vendor_part = vendors.find_source(args.vendors), None
    if source is None or not source.exists():
        print('No "EU vendors" .zip or folder found: the sheet "EU доставчици" is left out (use --vendors PATH).')
    else:
        items, line = vendors.load(source)
        print(f"Vendors: {line}")
        eans = barcodes.for_rows(rows, settings["vendors"]["barcode_cache_days"])
        accepted, rejected = config.review()
        found = matching.match(rows, eans, items, settings["vendors"]["name_match_threshold"], accepted, rejected)
        table, left_out = scoring.vendor_table(found, items, settings)
        unchecked = sum(r["how"] == matching.TO_CHECK for r in table)
        vendor_part = {"table": table, "left_out": left_out, "source": line, "score_cfg": settings["score"],
                       "vat": settings["vendors"]["vat"],
                       "line": f"{len(table)} продукта, които EU доставчиците предлагат по-евтино, със скор 1–10 "
                               f"(източник: {source.name})."}
        print(f"EU vendors: {len({m['ean'] for m in found})} of the table's products found; {len(table)} cheaper than in "
              f"Bulgaria (left out: {left_out['cheaper_in_bg']} cheaper in BG, {left_out['no_bg_price']} without a BG price)"
              + (f"; {unchecked} name matches to check by hand" if unchecked else ""))
    try:
        workbook.write(excel, shops, rows, meta, vendor_part)
    except PermissionError:
        print(f"Cannot save {excel.name}: close it in Excel and run again.")
        return 1
    print(f"Saved: {excel}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
