"""python -m top_products [--offline] [--budget USD] [--vendors FOLDER_OR_ZIP] [--excel FILE]

Rebuilds the Excel "Топ продукти по трафик" in output/:
  1. scrapes every shop in top_products/shops.yaml: the leading shops per category and their products with the most
     Google traffic (DataForSEO, kept for the month: only the first run of a month pays), price and stock from the pages
  2. checks every vendor in the "EU vendors" Google Drive folder: which of those products they sell cheaper,
     with the profit and a 1-10 score (sheets "EU доставчици" and "Проверени доставчици")
"""
from __future__ import annotations

import argparse
import sys
import time
from collections import defaultdict
from pathlib import Path

from . import barcodes, config, matching, scoring, traffic, vendors, workbook


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m top_products", description=__doc__.split("\n\n")[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offline", action="store_true",
                    help="no DataForSEO: reuse the traffic data already in the Excel (free)")
    ap.add_argument("--budget", type=float, help="USD cap for DataForSEO this run (default: settings.yaml)")
    ap.add_argument("--vendors", help='the "EU vendors" folder or its .zip (default: Google Drive, see settings.yaml)')
    ap.add_argument("--excel", help="the workbook to update (default: the newest output/Топ продукти по трафик*.xlsx)")
    args = ap.parse_args(argv)
    settings = config.settings()

    excel = Path(args.excel) if args.excel else workbook.find(config.OUTPUT, config.EXCEL_NAME)
    shops = rows = meta = None
    if not args.offline:
        try:
            shops, rows, meta = traffic.run(args.budget)
        except Exception as exc:      # no key / no balance / no network: keep going with the last data
            print(f"Shops not scraped ({exc}); using the traffic data in {excel.name}")
    if rows is None:
        if not excel.exists():
            print(f"No traffic data yet: {excel} does not exist and the shops could not be scraped.")
            return 1
        shops, rows, meta = workbook.read(excel)
        print(f"Traffic data: {excel.name} ({len(rows)} products, {len(shops)} shops)")

    meta["checked_vendors"] = time.strftime("%d.%m.%Y")
    source, origin = vendors.find_source(args.vendors, settings["vendors"])
    vendor_part = None
    if source is None or not source.exists():
        print('No "EU vendors" folder found (Google Drive for desktop, a service account or a .zip): '
              'the vendor sheets are left out. See top_products/settings.yaml -> vendors.')
    else:
        items, vendor_list = vendors.load(source)
        print(f"Vendors: {source} ({origin}): {len(vendor_list)} folders, {len(items)} prices")
        eans = barcodes.for_rows(rows, settings["vendors"]["barcode_cache_days"])
        accepted, rejected = config.review()
        found = matching.match(rows, eans, items, settings["vendors"]["name_match_threshold"], accepted, rejected)
        table, left_out = scoring.vendor_table(found, items, settings)
        offered = defaultdict(set)
        for i in items:
            offered[i["vendor"]].add(i["ean"])
        matched = {m["ean"] for m in found}
        for v in vendor_list:
            v["found"] = len(offered[v["vendor"]] & matched)
            v["cheapest"] = sum(r["vendor"] == v["vendor"] for r in table)
        unchecked = sum(r["how"] == matching.TO_CHECK for r in table)
        vendor_part = {"table": table, "left_out": left_out, "vendors": vendor_list, "score_cfg": settings["score"],
                       "vat": settings["vendors"]["vat"], "source": f"{origin}: {source.name}",
                       "line": f"{len(table)} продукта, които EU доставчиците предлагат по-евтино, със скор 1–10; "
                               f"проверени {len(vendor_list)} папки на доставчици ({origin})."}
        print(f"EU vendors: {len(matched)} of the table's products found; {len(table)} cheaper than in Bulgaria "
              f"(left out: {left_out['cheaper_in_bg']} cheaper in BG, {left_out['no_bg_price']} without a BG price)"
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
