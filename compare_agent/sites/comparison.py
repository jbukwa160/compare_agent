"""Adapters for EU price-comparison platforms that need a little code (configured in sites.yaml).

  klarna    Klarna Shopping (klarna.com/<cc>/shopping) and PriceRunner (.dk/.se/.com) — same platform
  pricespy  PriceSpy / Prisjakt / LeDenicheur / Hintaopas GraphQL API
"""
from __future__ import annotations

import json
from typing import Any, Optional

from bs4 import BeautifulSoup

from ..models import Offer, Product
from .base import SiteAdapter, parse_price, register


def _amount(v: Any) -> Optional[float]:
    if isinstance(v, dict):
        v = v.get("amount")
    return parse_price(v)


@register("klarna")
class KlarnaAdapter(SiteAdapter):
    """cfg: base_url (e.g. https://www.klarna.com/de/shopping), offers_page (bool, default true).

    klarna.com rate-limits hard (AWS WAF): keep delay_seconds >= 3.
    """

    def _queries_state(self, url: str, params: dict | None = None) -> list[dict]:
        status, html, _ = self.fetch("GET", url, params=params)
        tag = BeautifulSoup(html, "lxml").find("script", id="initial_payload") if status == 200 else None
        if tag is None:
            raise RuntimeError(f"HTTP {status}, no data (rate limited by the site?) - raise delay_seconds")
        return json.loads(tag.string)["__DEHYDRATED_QUERY_STATE__"]["queries"]

    def search_raw(self, query: str) -> list[Offer]:
        base = self.cfg["base_url"].rstrip("/")
        for q in self._queries_state(f"{base}/results", {"q": query}):
            data = (q.get("state") or {}).get("data")
            if not isinstance(data, dict):
                continue
            for page in data.get("pages") or []:
                if isinstance(page, dict) and "products" in page:
                    return [self.offer(
                        title=p.get("name", ""), url=base + p.get("url", ""),
                        price=_amount(p.get("lowestPrice")),
                        currency=(p.get("lowestPrice") or {}).get("currency") or self.cfg.get("currency", "EUR"),
                        in_stock=None if p.get("outOfStock") is None else not p["outOfStock"],
                        availability_text="out of stock" if p.get("outOfStock") else "",
                        shop_count=(p.get("previewMerchants") or {}).get("count"),
                    ) for p in page["products"]]
        return []

    def _enrich(self, product: Product, offers: list[Offer], threshold: float) -> None:
        """'Lowest price' can be a bad outlier; the offers page gives the cheapest in-stock offer."""
        if not self.cfg.get("offers_page", True):
            return
        best = max((o for o in offers if o.match_score >= threshold), key=lambda o: o.match_score, default=None)
        if best is None:
            return
        try:
            for q in self._queries_state(best.url):
                if (q.get("queryKey") or [None])[0] != "product-detail-offers":
                    continue
                data = q["state"]["data"]
                shop_offers = data.get("offers") or []
                in_stock = [o for o in shop_offers if str(o.get("stockStatus", "")).upper() == "IN_STOCK"]
                min_in_stock = _amount((data.get("offersSummary") or {}).get("minPriceInStock"))
                if min_in_stock:
                    best.price = min_in_stock
                best.shop_count = len(shop_offers) or best.shop_count
                best.in_stock = bool(in_stock) if shop_offers else best.in_stock
                best.availability_text = f"{len(in_stock)}/{len(shop_offers)} shops in stock"
                return
        except Exception as exc:  # keep the search-level data
            best.match_reason += f" (offers page: {exc})"


PRICESPY_QUERY = """query($q:String!){newSearch(query:$q){results{products{nodes{...on Product{
 id name pathName priceSummary{regular}
 prices{nodes{price{exclShipping inclShipping} stock{status} condition store{name}}}}}}}}}"""


@register("pricespy")
class PriceSpyAdapter(SiteAdapter):
    """cfg: host (ledenicheur.fr | hintaopas.fi | pricespy.co.uk | www.prisjakt.nu)."""

    def search_raw(self, query: str) -> list[Offer]:
        host = self.cfg["host"]
        status, text, _ = self.fetch(
            "POST", f"https://{host}/api/graphql",
            json_body={"query": PRICESPY_QUERY, "variables": {"q": query}},
            headers={"Content-Type": "application/json", "Origin": f"https://{host}",
                     "Referer": f"https://{host}/"})
        if status != 200:
            raise RuntimeError(f"HTTP {status}")
        nodes = (((json.loads(text).get("data") or {}).get("newSearch") or {}).get("results") or {})
        nodes = ((nodes.get("products") or {}).get("nodes")) or []
        offers = []
        for n in nodes:
            if "prices" not in n:
                continue
            shop = [o for o in (n.get("prices") or {}).get("nodes") or [] if o.get("condition", "new") == "new"]
            stocked = [o for o in shop if (o.get("stock") or {}).get("status") == "in_stock"]
            prices = [p for p in (parse_price((o.get("price") or {}).get("exclShipping")) for o in (stocked or shop)) if p]
            offers.append(self.offer(
                title=n.get("name", ""), url=f"https://{host}{n.get('pathName', '')}",
                price=min(prices) if prices else parse_price((n.get("priceSummary") or {}).get("regular")),
                in_stock=True if stocked else None,   # "unknown" is common, so no stock data is not "out"
                availability_text=f"{len(stocked)}/{len(shop)} shops in stock",
                shop_count=len(shop),
            ))
        return offers
