"""Barcodes (EAN/GTIN) of the products in the table, read from their shop pages and kept for a while."""
from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from bs4 import BeautifulSoup

from . import config
from .pages import HTTP, _ld_json, barcodes, fetch, norm_gtin

CACHE_FILE = config.CACHE / "barcodes.json"
TECHNOPOLIS_API = "https://api.technopolis.bg/videoluxcommercewebservices/v2/technopolis-bg/products/search"


def _technopolis(url: str) -> list[str]:
    """Technopolis pages carry no barcode; its search API does (by the product code in the URL)."""
    m = re.search(r"/p/(\d+)", url)
    if not m:
        return []
    status, text, _ = HTTP.fetch("GET", TECHNOPOLIS_API, headers={"Accept": "application/json"},
                                 params={"query": m.group(1), "lang": "bg", "curr": "EUR", "pageSize": 5, "currentPage": 0,
                                         "fields": "products(code,ean),pagination(DEFAULT),facets(code)"})
    if status != 200:
        return []
    return [norm_gtin(p.get("ean")) for p in json.loads(text).get("products", []) if p.get("code") == m.group(1) and norm_gtin(p.get("ean"))]


def _one(url: str, shop: dict) -> list[str] | None:
    """The page's barcodes, or None when the shop did not let us read the page."""
    if shop["name"] == "Technopolis":
        return _technopolis(url)
    status, html, _ = fetch(url, shop)
    if status != 200 or len(html.strip()) < 500 or "just a moment" in html[:5000].lower():
        return None
    soup = BeautifulSoup(html, "lxml")
    return barcodes(soup, _ld_json(soup))


def for_rows(rows: list[dict], max_age_days: int) -> dict[str, list[str]]:
    """{page URL: barcodes} for every row; pages read within max_age_days come from the cache."""
    cache = json.loads(CACHE_FILE.read_text(encoding="utf-8")) if CACHE_FILE.exists() else {}
    shops = {s["name"]: s for s in config.shops()}
    fresh = time.time() - max_age_days * 86400
    for r in rows:     # barcodes the traffic refresh already read from the same pages
        if r.get("eans"):
            cache[r["url"]] = {"eans": r["eans"], "at": time.time()}
    todo = defaultdict(list)
    for r in rows:
        c = cache.get(r["url"])
        if not c or c["at"] < fresh:
            todo[r["store"]].append(r["url"])

    def work(store: str) -> dict:
        shop, out, fails = shops.get(store, {"name": store, "domain": ""}), {}, 0
        for url in todo[store]:
            if fails >= 4:      # the shop blocks us: try again on a later run
                break
            try:
                codes = _one(url, shop)
            except Exception:
                codes = None
            if codes is None:
                fails += 1
                continue
            fails = 0
            out[url] = {"eans": codes, "at": time.time()}
        return out
    if todo:
        print(f"Reading barcodes from {sum(len(v) for v in todo.values())} shop pages ...")
        with ThreadPoolExecutor(30) as ex:
            for part in ex.map(work, list(todo)):
                cache.update(part)
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return {r["url"]: (cache.get(r["url"]) or {}).get("eans", []) for r in rows}
