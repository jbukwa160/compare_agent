"""Refresh of the traffic data (paid DataForSEO calls): which shops lead, and their products with the most traffic."""
from __future__ import annotations

import re
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import unquote, urlparse

from . import config
from .dataforseo import BudgetExceeded, Labs
from .pages import inspect_any, norm_url, shop_words, strip_shop

OPEN_RULE, OPEN_OTHER, OPEN_BY_PAGE = 45, 15, 80
CONTENT_NOTE = "трафикът е почти изцяло от статии, не от продукти"
UNVERIFIED = "сайтът не допуска автоматична проверка: името е от Google, цената липсва"


def rel_url(url: str) -> str:
    u = urlparse(url)
    return (u.path or "/") + (f"?{u.query}" if u.query else "")


def _path(url: str) -> str:
    """Decoded path only: URL rules ignore query strings (?utm=..., ?variant=...)."""
    return unquote(urlparse(url).path) or "/"


def on_site(url: str, domain: str) -> bool:
    return urlparse(url).netloc.lower() in (domain, "www." + domain)


def _geo(cfg: dict) -> dict:
    return {"location_code": cfg["location_code"], "language_code": cfg["language_code"]}


def top_pages(labs: Labs, shop: dict, limit: int, cfg: dict) -> list[dict]:
    """The shop's top pages by estimated monthly organic visits (relevant_pages takes no subdomains)."""
    task = {"target": shop.get("pages_target", shop["domain"]), **_geo(cfg), "item_types": ["organic"],
            "order_by": ["metrics.organic.etv,desc"], "limit": limit}
    if shop.get("pages_filters"):
        task["filters"] = shop["pages_filters"]
    res = labs.call("dataforseo_labs/google/relevant_pages/live", task)
    out = []
    for i in res.get("items") or []:
        m = (i.get("metrics") or {}).get("organic") or {}
        if on_site(i["page_address"], shop["domain"]):
            out.append({"url": i["page_address"], "etv": m.get("etv") or 0, "keywords": m.get("count") or 0})
    return out


def verify(shop: dict, pages: list[dict]) -> tuple[list[dict], list[dict]]:
    """(product pages, excluded pages): URL rule per shop, then each page opened and checked."""
    rx = re.compile(shop["product_rx"]) if shop.get("product_rx") else None
    skip = re.compile(shop["skip_rx"]) if shop.get("skip_rx") else None
    exclude = re.compile(shop["exclude_rx"]) if shop.get("exclude_rx") else None
    keep = re.compile(shop["keep_crumbs_rx"], re.I) if shop.get("keep_crumbs_rx") else None
    by_page = bool(shop.get("by_page"))
    for p in pages:
        path = _path(p["url"])
        p["rule"] = bool(rx.search(path)) and not (exclude and exclude.search(path)) if rx else False
        p["skip"] = bool(skip.search(path)) if skip else False
    if by_page:
        to_open = [p for p in pages if not p["skip"]][:shop.get("open_by_page", OPEN_BY_PAGE)]
    else:
        to_open = [p for p in pages if p["rule"]][:shop.get("open_rule", OPEN_RULE)] + \
                  [p for p in pages if not p["rule"] and not p["skip"]][:shop.get("open_other", OPEN_OTHER)]
    blocked_run = 0
    for p in to_open:
        if blocked_run >= 4:      # the site blocks us: don't hammer it, judge the rest by the URL rule only
            p["page"] = {"url": p["url"], "status": "blocked"}
            continue
        p["page"] = inspect_any(p["url"], shop)
        blocked_run = blocked_run + 1 if p["page"]["status"] in ("blocked", "error") else 0

    # shops whose product pages carry product data: a rule-matching page without any is not a product
    judged = [p["page"]["status"] for p in to_open if p["rule"] and p["page"]["status"] in ("product", "mixed", "other", "listing")]
    strict = bool(judged) and sum(st in ("product", "mixed") for st in judged) / len(judged) >= 0.8
    products, excluded = [], []
    for p in to_open:
        pg, st = p["page"], p["page"]["status"]
        moved = bool(pg.get("final_url")) and norm_url(pg["final_url"]) != norm_url(p["url"])
        final_rule = bool(rx.search(_path(pg["final_url"]))) if (rx and pg.get("final_url")) else False
        if st in ("blocked", "error"):
            (products if p["rule"] else excluded).append({**p, "verified": False, "why": st})
            continue
        if st.startswith("http"):
            excluded.append({**p, "why": st})
            continue
        ok = st == "product" if by_page else \
            (final_rule if moved else p["rule"]) and st != "listing" and not (strict and st == "other")
        if ok and keep and not keep.search(pg.get("crumbs_full") or ""):
            excluded.append({**p, "why": "other department"})
        elif ok:
            products.append({**p, "verified": True, "note": "пренасочва към нов адрес" if moved else ""})
        else:
            excluded.append({**p, "why": f"not a product page ({st})"})

    # the same product under several URLs (sizes / flavours / ?review-page=1): one row, traffic added up
    groups: dict[str, dict] = {}
    for p in products:
        pg = p["page"]
        canon = pg.get("canonical") or ""
        canon_ok = canon and on_site(canon, shop["domain"]) and rel_url(canon) not in ("/", "") and (
            by_page or not rx or bool(rx.search(_path(canon))))
        key = norm_url(canon if canon_ok else (pg.get("final_url") or p["url"])).split("?", 1)[0]
        if key not in groups:
            groups[key] = {**p, "urls": [p["url"]], "link": pg.get("final_url") or p["url"]}
        else:
            groups[key]["urls"].append(p["url"])
            groups[key]["etv"] += p["etv"]
            groups[key]["keywords"] += p["keywords"]
    merged = sorted(groups.values(), key=lambda g: -g["etv"])
    for g in merged:
        for part, fix in (shop.get("overrides") or {}).items():
            if part in g["url"]:
                g["page"] = {**g["page"], **{k: v for k, v in fix.items() if k != "note"}}
                g["note"] = "; ".join(x for x in (g.get("note"), fix.get("note")) if x)
    return merged, excluded


def main_keywords(labs: Labs, shop: dict, products: list[dict], cfg: dict) -> None:
    """The search phrase that brings each product most of its traffic (one call per shop)."""
    rels = {rel_url(u) for p in products for u in p["urls"]}
    rels = sorted(rels | {unquote(r) for r in rels})       # DataForSEO may keep Cyrillic paths decoded
    res = labs.call("dataforseo_labs/google/ranked_keywords/live",
                    {"target": shop["domain"], **_geo(cfg), "item_types": ["organic"],
                     "filters": [["ranked_serp_element.serp_item.relative_url", "in", rels]],
                     "order_by": ["ranked_serp_element.serp_item.etv,desc"], "limit": cfg["keyword_rows"]})
    by_rel = defaultdict(list)
    for it in res.get("items") or []:
        si = (it.get("ranked_serp_element") or {}).get("serp_item") or {}
        kd = it.get("keyword_data") or {}
        by_rel[norm_url("https://x" + (si.get("relative_url") or "")).split("?", 1)[0]].append(
            {"keyword": kd.get("keyword"), "volume": (kd.get("keyword_info") or {}).get("search_volume"),
             "rank": si.get("rank_group"), "etv": si.get("etv") or 0, "title": si.get("title")})
    for p in products:
        kws = [k for u in p["urls"] for k in by_rel.get(norm_url("https://x" + rel_url(u)).split("?", 1)[0], [])]
        p["kw"] = max(kws, key=lambda k: k["etv"] or 0) if kws else {}


def _clean_name(name: str) -> str:
    name = re.sub("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍]", " ", name)
    name = re.sub(r"(?<=\s)\?+(?=\s|$)", " ", name)
    name = re.sub(r"\s*\.\.\.$", " …", name)     # titles Google cut short
    return " ".join(name.split()).strip(" -–|")


def _slug_name(url: str) -> str:
    slug = re.sub(r"\.html?$", "", unquote(urlparse(url).path).rstrip("/").rsplit("/", 1)[-1])
    return " ".join(re.split(r"[-_]+", slug)).strip()


def run(budget: float | None = None) -> tuple[list[dict], list[dict], dict]:
    """(shops, product rows, meta) like workbook.read() returns them, from fresh DataForSEO data."""
    cfg = config.settings()["traffic"]
    labs = Labs(config.CACHE / "api", budget if budget is not None else cfg["budget_usd"])
    shops = config.shops()
    print(f"Traffic refresh: {len(shops)} shops, budget ${labs.budget:.2f}")
    res = labs.call("dataforseo_labs/google/bulk_traffic_estimation/live",
                    {"targets": sorted({s["domain"] for s in shops}), **_geo(cfg), "item_types": ["organic"]})
    site = {i["target"]: ((i.get("metrics") or {}).get("organic") or {}).get("etv") or 0 for i in res.get("items") or []}
    for s in shops:
        s["traffic"] = round(site.get(s["domain"], 0))
    for cat in dict.fromkeys(s["category"] for s in shops):
        ranked = sorted((s for s in shops if s["category"] == cat and not s.get("content_site")), key=lambda s: -s["traffic"])
        for s in ranked[:cfg["leading_per_category"]]:
            s["lead"] = True

    def work(shop: dict) -> dict:
        if not shop["traffic"]:
            return {"products": [], "note": "няма данни за сайта в DataForSEO"}
        try:
            pages = top_pages(labs, shop, cfg["pages_leading"] if shop.get("lead") else cfg["pages_other"], cfg)
            products, excluded = verify(shop, pages)
            products = products[:cfg["products_per_shop"]]
            if products:
                main_keywords(labs, shop, products, cfg)
        except BudgetExceeded as exc:
            return {"products": [], "note": f"не е проверен: {exc}"}
        note = ""
        if not products:
            blocked = excluded and all(e["why"] in ("blocked", "error") for e in excluded)
            note = "сайтът не допуска автоматична проверка" if blocked else \
                "няма продуктови страници с трафик сред прегледаните страници"
        print(f"  {shop['name']:<20} {len(products):>2} products")
        return {"products": products, "note": note}

    with ThreadPoolExecutor(20) as ex:
        results = list(ex.map(work, shops))
    rows, out_shops = [], []
    for shop, r in zip(shops, results):
        words = shop_words(shop)
        for rank, p in enumerate(r["products"], 1):
            pg, kw = p["page"], p.get("kw") or {}
            serp = strip_shop(" ".join((kw.get("title") or "").split()), words)
            note = UNVERIFIED if not p["verified"] else (p.get("note") or "")
            if len(p["urls"]) > 1:
                note = "; ".join(x for x in [note, f"трафикът е сбор от {len(p['urls'])} адреса на същия продукт"] if x)
            rows.append({"category": shop["category"], "store": shop["name"], "lead": "да" if shop.get("lead") else "не",
                         "rank": rank, "name": _clean_name(pg.get("name") or serp or _slug_name(p["url"])),
                         "brand": pg.get("brand") or None, "crumbs": pg.get("crumbs") or None, "etv": round(p["etv"]),
                         "keywords": p["keywords"], "kw": kw.get("keyword"), "kw_volume": kw.get("volume"),
                         "kw_rank": kw.get("rank"), "price_min": pg.get("price_min"),
                         "price_max": pg.get("price_max") if pg.get("price_max") != pg.get("price_min") else None,
                         "availability": pg.get("availability") or None, "url": p.get("link") or p["url"],
                         "note": note or None, "eans": pg.get("eans") or []})
        notes = [CONTENT_NOTE if shop.get("content_site") else "", shop.get("scope_note", ""), r["note"]]
        out_shops.append({"category": shop["category"], "store": shop["name"], "domain": shop["domain"],
                          "traffic": shop["traffic"] or None, "lead": bool(shop.get("lead")),
                          "note": "; ".join(n for n in notes if n) or None})
    meta = {"data_date": labs.data_date(), "checked": time.strftime("%d.%m.%Y"),
            "pages": f"{cfg['pages_leading']} / {cfg['pages_other']}", "cost": f"${labs.spent:.2f}"}
    print(f"Traffic refresh done: {len(rows)} products, DataForSEO ${labs.spent:.2f}")
    return out_shops, rows, meta
