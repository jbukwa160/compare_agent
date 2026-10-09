"""Developer helper: run ONE site on a few rows of a supplier file and print what it found and why.

    python scripts/try_site.py Technopolis 3,4,7
    python scripts/try_site.py "Klarna US" 4,26 --file input/products.xlsx

Use it after adding or changing a site in config/sites.yaml, or when a site suddenly finds nothing
(changed HTML = broken selectors). Each line: match score, match reason, listing title, price, stock,
listing EAN, URL. Score >= 0.75 (settings.yaml scoring.match_threshold) means "same product".
Responses are cached for 12 h in .cache/ like normal runs.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from compare_agent import matching  # noqa: E402
from compare_agent.config import load_env, load_settings, load_sites  # noqa: E402
from compare_agent.http import HttpClient  # noqa: E402
from compare_agent.loader import load_products  # noqa: E402
from compare_agent.sites import load_adapters  # noqa: E402,F401  (registers adapter types)
from compare_agent.sites.base import build_adapter  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("site", help="site name exactly as in sites.yaml")
    ap.add_argument("rows", help="Excel row numbers, e.g. 3,4,7")
    ap.add_argument("--file", default=str(ROOT / "input" / "products.xlsx"))
    ap.add_argument("--top", type=int, default=3, help="listings to show per product")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    load_env()
    settings = load_settings()
    matching.EAN_MISMATCH_PENALTY = settings["scoring"].get("ean_mismatch_penalty", 0.15)
    threshold = settings["scoring"]["match_threshold"]
    sites = load_sites(only=[args.site])
    if not sites:
        sys.exit(f"No site named {args.site!r} in config/sites.yaml")
    adapter = build_adapter(sites[0], HttpClient(ROOT / ".cache"))
    rows = {int(r) for r in args.rows.split(",")}
    for p in load_products(Path(args.file), settings):
        if p.row not in rows:
            continue
        try:
            offers = adapter.search(p, threshold)
        except Exception as exc:
            print(f"== {adapter.name} | {p.name}: ERROR {exc}")
            continue
        print(f"== {adapter.name} | {p.name} ({len(offers)} listings seen, queries: {adapter.queries(p)})")
        for o in sorted(offers, key=lambda o: -o.match_score)[: args.top]:
            print(f"   {o.match_score:.2f} {o.match_reason[:40]:40} | {o.title[:55]:55} | {o.price} {o.currency}"
                  f" | stock={o.in_stock} {o.availability_text[:25]!r} | ean={o.ean} | {o.url[:80]}")


if __name__ == "__main__":
    main()
