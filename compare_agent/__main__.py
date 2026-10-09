"""Command line:  python -m compare_agent input/products.xlsx [options]"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from .catalogue import assign_groups, fill_names
from .config import ROOT, load_env, load_settings, load_sites, load_traffic_sources
from .keys import check_keys
from .loader import load_products
from .report import write_json, write_report
from .runner import Runner


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="compare_agent",
                                 description="Score new-supplier products for pre-order: price first, then availability.")
    ap.add_argument("input", type=Path, nargs="?", help="supplier product file (.xlsx or .csv)")
    ap.add_argument("--check-keys", action="store_true", help="test the API keys in .env and exit (free)")
    ap.add_argument("--open", action="store_true", help="open the report in Excel when the run is done")
    ap.add_argument("--out", type=Path, default=None, help="output .xlsx (default: output/<file>_<Bulgaria|Worldwide>_<date>.xlsx)")
    ap.add_argument("--scope", choices=["local", "global", "both"], default="both",
                    help="both = Bulgaria + worldwide; local = Bulgaria only (no worldwide search); global = worldwide only")
    ap.add_argument("--sites", help="comma-separated site names to use (overrides 'enabled' in sites.yaml)")
    ap.add_argument("--rows", help="only these Excel row numbers, e.g. 3,4,10")
    ap.add_argument("--limit", type=int, help="only the first N products")
    ap.add_argument("--sheet", help="sheet name/index in the .xlsx")
    ap.add_argument("--settings", type=Path, help="alternative settings.yaml")
    ap.add_argument("--sites-file", type=Path, help="alternative sites.yaml")
    ap.add_argument("--no-cache", action="store_true", help="ignore cached web responses")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(message)s")
    load_env()
    if args.check_keys:
        return check_keys()
    if args.input is None:
        ap.error("the product file is required (or use --check-keys)")
    settings = load_settings(args.settings)
    sites = load_sites(args.sites_file, only=args.sites.split(",") if args.sites else None)
    if args.scope != "both":  # local = Bulgarian shops only (no worldwide search at all); global = worldwide only
        sites = [s for s in sites if s["scope"] == args.scope]
    traffic = load_traffic_sources(args.sites_file)
    if args.scope == "local":
        for t in traffic:
            t["global_markets"] = []
    if args.scope == "global":
        for t in traffic:
            t["local_markets"] = []

    sheet = int(args.sheet) if args.sheet and args.sheet.isdigit() else args.sheet
    products = load_products(args.input, settings, sheet=sheet)
    if args.rows:
        wanted = {int(r) for r in args.rows.split(",")}
        products = [p for p in products if p.row in wanted]
    if args.limit:
        products = products[: args.limit]
    if not products:
        print("No products to check.")
        return 1

    unnamed = sum(1 for p in products if not p.name)
    if unnamed:
        filled = fill_names(products, settings, ROOT)
        print(f"The file has no product names for {unnamed} products: {filled} found by EAN in our own files"
              + (f", {unnamed - filled} will be named after the shop listing that matches their EAN"
                 if unnamed - filled else ""))
    assign_groups(products)
    groups = Counter(p.catalogue_group for p in products)
    print("Products: " + ", ".join(f"{n} {g.lower()}" for g, n in groups.most_common())
          + " (selling = has a Dilon code in internal_sku)")

    runner = Runner(settings, sites, ROOT / ".cache", use_cache=not args.no_cache, traffic_sources=traffic)
    print(f"{len(products)} products x {len(runner.adapters)} sites: {', '.join(a.name for a in runner.adapters)}")
    if runner.skipped_sites:
        keys = sorted({k for _, k in runner.skipped_sites})
        print(f"Skipped {len(runner.skipped_sites)} sites until {', '.join(keys)} is set in .env: "
              + ", ".join(n for n, _ in runner.skipped_sites))
    if not runner.traffic:
        print("Traffic: no source enabled (sites.yaml -> traffic) - sales score uses shop counts only")
    for src in runner.traffic:
        missing = src.missing_credentials
        print(f"Traffic: {src.name}" + (f" - SKIPPED, set {missing} in .env" if missing else
              f" ({len(src.local_markets)} BG + {len(src.global_markets)} worldwide markets)"))
    no_price = sum(1 for p in products if p.supplier_price is None)
    if no_price:
        print(f"Note: {no_price} products have no supplier price -> NEED PRICE (only the sales score is given)")

    def progress(done: int, total: int, p) -> None:
        if p is None:
            print(f"Not sold in Bulgaria: {total} products -> checking worldwide")
        else:
            print(f"  [{done}/{total}] {p.name[:60]}")

    evaluations = runner.run(products, progress)

    label = {"local": "Bulgaria", "global": "Abroad", "both": "Worldwide"}[args.scope]
    out = args.out or ROOT / "output" / f"{args.input.stem}_{label}_{datetime.now():%Y%m%d_%H%M}.xlsx"
    # the raw results first, so a long run is never lost even if the Excel step fails
    data_file = write_json(evaluations, out.with_suffix(".json"))
    failed = Counter(err.split(":")[0] for e in evaluations for err in e.errors)
    if failed:
        print("\nSites that failed or blocked us for some products (the other sites still counted): "
              + ", ".join(f"{site} ({n} products)" for site, n in failed.most_common()))
    print("\nVerdicts: " + ", ".join(f"{k} {v}" for k, v in Counter(e.verdict for e in evaluations).most_common()))
    try:
        report = write_report(evaluations, settings, out,
                              local_sites=[a.name for a in runner.adapters if a.scope == "local"],
                              world_sites=[a.name for a in runner.adapters if a.scope != "local"],
                              meta={"input_name": args.input.name, "scope": args.scope})
    except Exception as exc:
        print(f"Could not write the Excel report ({exc}). The results are saved in {data_file}")
        return 1
    print(f"Report: {report}")
    if args.open and hasattr(os, "startfile"):
        os.startfile(report)  # Windows: opens in Excel
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
