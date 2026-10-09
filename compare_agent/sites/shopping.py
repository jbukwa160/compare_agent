"""Google Shopping through DataForSEO (Merchant API): shop offers and prices in many countries.

One search per product and country (up to 40 offers each). DataForSEO has no live endpoint for this:
tasks are posted (100 per request), then fetched when ready - about a minute in the priority queue.
Billing is per search ($0.002 priority / $0.001 normal); results stay fetchable for 30 days, so the
cached task ids make a re-run of the same file free. Uses DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD.
Docs: https://docs.dataforseo.com/v3/merchant/google/products/task_post/
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from ..matching import score_match
from ..models import Offer, Product
from ..traffic.dataforseo import STATUS_HELP, auth_header
from .base import SiteAdapter, clean_query, register

log = logging.getLogger("compare_agent")
POST_URL = "https://api.dataforseo.com/v3/merchant/google/products/task_post"
GET_URL = "https://api.dataforseo.com/v3/merchant/google/products/task_get/advanced/{id}"
READY, WAITING = 20000, {40601, 40602}  # done / still in the queue


def _ok_result(text: str) -> bool:
    """Cache only finished task results."""
    try:
        data = json.loads(text)
    except ValueError:
        return False
    tasks = data.get("tasks") or []
    return bool(tasks) and all(t.get("status_code") == READY for t in tasks)


@register("dataforseo_shopping")
class GoogleShoppingAdapter(SiteAdapter):
    """cfg: countries = [{location: Germany, language: German, country: DE}, ...], priority (1|2),
    login_env / password_env, max_wait_seconds. Works on the whole product list at once (search_many)."""

    batch = True

    def search_raw(self, query: str) -> list[Offer]:  # not used: see search_many
        return []

    def _headers(self) -> dict[str, str]:
        return auth_header(os.environ.get(self.cfg.get("login_env", "DATAFORSEO_LOGIN"), "").strip(),
                           os.environ.get(self.cfg.get("password_env", "DATAFORSEO_PASSWORD"), "").strip())

    def _keyword(self, p: Product) -> str:
        words = clean_query(p.name).split()
        return " ".join(words[:8])

    def search_many(self, products: list[Product], threshold: float) -> tuple[dict[int, list[Offer]], list[str]]:
        """{product row: offers} for every product and country, plus error messages."""
        headers = self._headers()
        jobs = []  # (product, country cfg, task body)
        for p in products:
            if not p.name:
                continue
            for c in self.cfg.get("countries", []):
                jobs.append((p, c, {"keyword": self._keyword(p), "location_name": c["location"],
                                    "language_name": c["language"], "priority": self.cfg.get("priority", 2),
                                    "tag": f"{p.row}|{c['country']}"}))
        offers: dict[int, list[Offer]] = {p.row: [] for p in products}
        errors: list[str] = []

        # 1) post the searches, 100 per request (a cached answer = the same searches posted earlier: free)
        task_ids: dict[str, tuple[Product, dict]] = {}
        for i in range(0, len(jobs), 100):
            chunk = jobs[i:i + 100]
            status, text = self.http.request("POST", POST_URL, json_body=[b for _, _, b in chunk], headers=headers,
                                             delay=0.5, cache_check=lambda t: '"status_code":20100' in t.replace(" ", ""))
            data = json.loads(text) if text else {}
            if status != 200 or data.get("status_code") != 20000:
                code = data.get("status_code")
                errors.append(f"{self.name}: {STATUS_HELP.get(code, data.get('status_message', f'HTTP {status}'))}")
                return offers, errors
            for (p, c, _), t in zip(chunk, data.get("tasks") or []):
                if t.get("status_code") == 20100:  # task created
                    task_ids[t["id"]] = (p, c)
                else:
                    errors.append(f"{self.name} ({c['country']}): {t.get('status_message')}")

        excluded = [s.lower() for s in self.cfg.get("exclude_sellers") or []]

        # 2) fetch each search when it is ready
        deadline = time.time() + self.cfg.get("max_wait_seconds", 600)
        pending = dict(task_ids)
        while pending and time.time() < deadline:
            for tid in list(pending):
                status, text = self.http.request("GET", GET_URL.format(id=tid), headers=headers, delay=0.2,
                                                 cache_check=_ok_result)
                data = json.loads(text) if text else {}
                task = (data.get("tasks") or [{}])[0]
                code = task.get("status_code")
                if code in WAITING:
                    continue
                p, c = pending.pop(tid)
                if code != READY:
                    errors.append(f"{self.name} ({c['country']}): {task.get('status_message', code)}")
                    continue
                for item in ((task.get("result") or [{}])[0] or {}).get("items") or []:
                    if item.get("type") != "google_shopping_serp" or not item.get("price"):
                        continue
                    if any(x in (item.get("seller") or "").lower() for x in excluded):
                        continue
                    pid = item.get("product_id")
                    o = Offer(site=f"{item.get('seller') or 'shop'} ({c['country']})", scope="global",
                              title=item.get("title") or "",
                              url=f"https://www.google.com/shopping/product/{pid}?gl={c['country'].lower()}"
                              if pid else "https://shopping.google.com",
                              price=float(item["price"]), currency=item.get("currency") or c.get("currency", "EUR"),
                              in_stock=True, availability_text="Google Shopping", country=c["country"])
                    o.match_score, o.match_reason = score_match(p, o)
                    offers[p.row].append(o)
            if pending:
                time.sleep(10)
        for tid, (p, c) in pending.items():
            errors.append(f"{self.name} ({c['country']}): not ready in time")
        return offers, errors


def site_cfg_for(offer: Offer, site_cfgs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Shopping offers are named after their shop, not a configured site: use their country for VAT."""
    return site_cfgs.get(offer.site) or {"country": offer.country or "EU"}
