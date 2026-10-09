"""Config-driven adapters. Most sites need only a YAML entry using one of these types:

  html      - search page parsed with CSS selectors (falls back to JSON-LD on the page)
  json_api  - a JSON search endpoint read with dotted paths
  jsonld    - search page that embeds schema.org Product JSON-LD
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional
from urllib.parse import quote, urljoin

from bs4 import BeautifulSoup

from ..models import Offer
from .base import (SiteAdapter, detect_currency, dig, jsonld_products, offer_from_jsonld, parse_price,
                   register, select_value)


def offers_from_html_items(adapter: SiteAdapter, items: list[Any], base_url: str) -> list[Offer]:
    """Parse result cards with the `results:` CSS selectors from the site config."""
    r = adapter.cfg.get("results", {})
    offers = []
    for item in items:
        title = select_value(item, r.get("title"))
        href = select_value(item, r.get("url"))
        if not title or not href:
            continue
        if r.get("url_must_contain") and r["url_must_contain"] not in href:
            continue  # e.g. ads mixed into the results
        if r.get("price_remove") and r.get("price"):  # e.g. a crossed-out old price inside the price tag
            for alt in r["price"].split("||"):
                node = item.select_one(alt.split("@")[0].strip())
                for junk in node.select(r["price_remove"]) if node else []:
                    junk.decompose()
        price_text = select_value(item, r.get("price"))
        avail_text = select_value(item, r.get("availability"))
        in_stock = adapter.availability(avail_text)
        if r.get("in_stock_if_present") and item.select_one(r["in_stock_if_present"]):
            in_stock = True
        if r.get("on_order_if_present") and item.select_one(r["on_order_if_present"]):
            in_stock = None  # e.g. a delivery-time badge: sold from supplier stock
            avail_text = select_value(item, r["on_order_if_present"]) or "on order"
        if r.get("out_of_stock_if_present") and item.select_one(r["out_of_stock_if_present"]):
            in_stock = False
            avail_text = avail_text or "out of stock"
        offers.append(adapter.offer(
            title=" ".join(title.split()), url=urljoin(base_url, href),
            price=_price(price_text, r.get("price")),
            currency=detect_currency(price_text or "", adapter.cfg.get("currency", "EUR")),
            in_stock=in_stock, availability_text=avail_text or "",
            ean=select_value(item, r.get("ean")),
            shop_count=_int(select_value(item, r.get("shop_count"))),
        ))
    return offers


@register("html")
class HtmlAdapter(SiteAdapter):
    def search_raw(self, query: str) -> list[Offer]:
        url = self.url(self.cfg["search_url"], query)
        status, html, final_url = self.fetch("GET", url)
        if status == 404:
            return []
        if status != 200:
            raise RuntimeError(f"HTTP {status}")
        soup = BeautifulSoup(html, "lxml")
        item_sel = self.cfg.get("results", {}).get("item")
        offers = offers_from_html_items(self, soup.select(item_sel), final_url) if item_sel else []
        page = self.cfg.get("product_on_redirect")
        if not offers and page and final_url.split("?")[0] != url.split("?")[0]:
            # an exact hit jumps straight to the product page (e.g. Ultra on an EAN search)
            title, price_text = select_value(soup, page.get("title")), select_value(soup, page.get("price"))
            avail = select_value(soup, page.get("availability"))
            if title:
                # an EAN search that lands on one product page is an EAN match, if the site is trusted to do so
                ean = query if page.get("trust_query_ean") and query.isdigit() else None
                offers = [self.offer(title=title, url=final_url, price=_price(price_text, page.get("price")),
                                     in_stock=self.availability(avail), availability_text=avail or "", ean=ean,
                                     shop_count=_int(select_value(soup, page.get("shop_count"))))]
        if not offers and self.cfg.get("jsonld_fallback", True):
            offers = [offer_from_jsonld(self, p, final_url) for p in jsonld_products(html)]
        return offers


@register("jsonld")
class JsonLdAdapter(SiteAdapter):
    def search_raw(self, query: str) -> list[Offer]:
        url = self.url(self.cfg["search_url"], query)
        status, html, final_url = self.fetch("GET", url)
        if status == 404:
            return []
        if status != 200:
            raise RuntimeError(f"HTTP {status}")
        # e.g. kieskeurig redirects an EAN search straight to the product page; anything else = not found
        must = self.cfg.get("result_url_contains")
        if must and must not in final_url:
            return []
        offers: dict[str, Offer] = {}
        for p in jsonld_products(html):  # pages can nest several Product blocks for one item
            o = offer_from_jsonld(self, p, final_url)
            o.url = urljoin(final_url, o.url)  # some sites give relative product URLs
            if o.url not in offers or (offers[o.url].price is None and o.price is not None):
                offers[o.url] = o
        return list(offers.values())


def resolve(item: Any, spec: Any) -> Any:
    """A field spec is a dotted path ('price.value') or {pairs: 'att', key: 'oz_common_barcode'}
    for lists of [name, value] pairs. Lists of values return the first element."""
    if spec is None:
        return None
    if isinstance(spec, dict):
        pairs = dig(item, spec["pairs"]) or []
        value = next((v for k, v in (p for p in pairs if isinstance(p, (list, tuple)) and len(p) == 2)
                      if k == spec["key"]), None)
    else:
        value = dig(item, spec)
    if isinstance(value, list):
        value = value[0] if value else None
    return value


def _fill(template: Any, query: str) -> Any:
    """{query} -> as is (params get encoded by the HTTP client), {query_path} -> encoded for a URL path."""
    if isinstance(template, str):
        return template.replace("{query_path}", quote(query, safe="")).replace("{query}", query)
    if isinstance(template, dict):
        return {k: _fill(v, query) for k, v in template.items()}
    if isinstance(template, list):
        return [_fill(v, query) for v in template]
    return template


def call_api(adapter: SiteAdapter, query: str) -> Any:
    req = adapter.cfg["request"]
    status, text, _ = adapter.fetch(
        req.get("method", "GET").upper(), _fill(req["url"], query),
        params=_fill(req.get("params"), query),
        json_body=_fill(req.get("json"), query),
        headers=req.get("headers"))
    if status == 404:
        return None
    if status != 200:
        raise RuntimeError(f"HTTP {status}")
    data = json.loads(text)
    return dig(data, adapter.cfg.get("items")) if adapter.cfg.get("items") else data


@register("json_html")
class JsonHtmlAdapter(SiteAdapter):
    """JSON endpoint returning HTML: a list of product cards (Technomarket) or one HTML block with all
    results (PIC.bg, then `results.item` picks the cards). Parsed with the `results:` selectors."""

    def search_raw(self, query: str) -> list[Offer]:
        items = call_api(self, query) or []
        base = self.cfg.get("base_url", "")
        if isinstance(items, str):
            item_sel = self.cfg.get("results", {}).get("item")
            soup = BeautifulSoup(items, "lxml")
            return offers_from_html_items(self, soup.select(item_sel) if item_sel else [soup], base)
        nodes = [BeautifulSoup(h, "lxml") for h in items if isinstance(h, str) and h.strip()]
        return offers_from_html_items(self, nodes, base)


@register("json_api")
class JsonApiAdapter(SiteAdapter):
    def search_raw(self, query: str) -> list[Offer]:
        items = call_api(self, query)
        f = self.cfg["fields"]
        offers = []
        for item in items or []:
            title = resolve(item, f["title"])
            href = resolve(item, f["url"])
            if f.get("url_template"):  # build the URL from several fields, e.g. "{href}-product-{extra.kod}"
                href = re.sub(r"\{([^}]+)\}", lambda m: str(resolve(item, m.group(1)) or ""), f["url_template"])
            if not title or not href:
                continue
            avail = resolve(item, f.get("availability"))
            in_stock = self.availability(avail)
            if f.get("out_of_stock_if") and resolve(item, f["out_of_stock_if"]):
                in_stock = False
            shop_count = resolve(item, f.get("shop_count"))
            offers.append(self.offer(
                title=str(title).strip(),
                url=_absolute(str(href), f.get("url_prefix", "")),
                price=parse_price(resolve(item, f.get("price"))),
                currency=resolve(item, f.get("currency")) or self.cfg.get("currency", "EUR"),
                in_stock=in_stock,
                availability_text="" if avail is None else str(avail),
                ean=_str(resolve(item, f.get("ean"))),
                shop_count=_int(shop_count),
            ))
        return offers


def _absolute(href: str, prefix: str) -> str:
    if href.startswith("http") or not prefix:
        return href
    return prefix.rstrip("/") + "/" + href.lstrip("/")


def _price(text: Optional[str], spec: Optional[str]) -> Optional[float]:
    """Attribute values (meta content, data-price, :price) are machine numbers: "238.188" is 238.188,
    not 238 188. Visible text goes through parse_price, which handles "1.299 €" and "1 234,56 лв"."""
    if text and spec and "@" in spec and re.fullmatch(r"\d+(\.\d+)?", text.strip()):
        return round(float(text), 2)
    return parse_price(text)


def _int(v: Any) -> Optional[int]:
    """'66 оферти' -> 66"""
    if v is None:
        return None
    m = re.search(r"\d+", str(v).replace(" ", ""))
    return int(m.group()) if m else None


def _str(v: Any) -> Optional[str]:
    return None if v in (None, "") else str(v)
