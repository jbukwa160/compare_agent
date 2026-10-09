"""Site adapter interface plus the parsing helpers every adapter shares."""
from __future__ import annotations

import json
import re
import time
from typing import Any, Callable, Optional
from urllib.parse import quote, quote_plus

from bs4 import BeautifulSoup
from rapidfuzz import fuzz

from ..http import HttpClient
from ..matching import _is_part_number, broad_query, score_match, short_name
from ..models import Offer, Product

ADAPTERS: dict[str, type["SiteAdapter"]] = {}

RETRY_WAITS = (5, 20)  # seconds to wait before retrying after a network error
RATE_LIMIT_WAITS = (20, 60)  # after a "slow down" answer (sites with retry_http, e.g. 403 / 429)
TRANSIENT_MARKERS = ("resolve host", "nameresolution", "getaddrinfo", "timed out", "timeout",
                     "connection reset", "connection aborted", "connectionerror", "max retries exceeded",
                     "temporarily unavailable", "curl: (6)", "curl: (7)", "curl: (28)", "curl: (35)", "curl: (56)")


def is_transient(exc: Exception) -> bool:
    """DNS / connection / timeout problems: worth retrying, and not a sign that the site blocks us."""
    text = f"{type(exc).__name__} {exc}".lower()
    return any(m in text for m in TRANSIENT_MARKERS)


def register(name: str) -> Callable[[type["SiteAdapter"]], type["SiteAdapter"]]:
    def deco(cls):
        ADAPTERS[name] = cls
        return cls
    return deco


def build_adapter(cfg: dict[str, Any], http: HttpClient) -> "SiteAdapter":
    kind = cfg.get("type", "html")
    if kind not in ADAPTERS:
        raise ValueError(f"Site '{cfg['name']}': unknown type '{kind}'. Known: {sorted(ADAPTERS)}")
    return ADAPTERS[kind](cfg, http)


class SiteAdapter:
    def __init__(self, cfg: dict[str, Any], http: HttpClient):
        self.cfg = cfg
        self.http = http
        self.name = cfg["name"]
        self.scope = cfg["scope"]
        self.delay = cfg.get("delay_seconds")
        self.max_results = cfg.get("max_results", 10)
        self.failures = 0          # consecutive failed searches (circuit breaker)
        self.last_error = ""

    # --- to implement --------------------------------------------------------
    def search_raw(self, query: str) -> list[Offer]:
        raise NotImplementedError

    # --- shared --------------------------------------------------------------
    def fetch(self, method: str, url: str, **kw) -> tuple[int, str, str]:
        """HTTP via the shared client with this site's delay and TLS impersonation settings."""
        kw.setdefault("delay", self.delay)
        kw.setdefault("impersonate", bool(self.cfg.get("impersonate")))
        if self.cfg.get("timeout_seconds"):
            kw.setdefault("timeout", self.cfg["timeout_seconds"])
        if self.cfg.get("encoding"):
            kw.setdefault("encoding", self.cfg["encoding"])
        return self.http.fetch(method, url, **kw)

    def get(self, url: str, **kw) -> tuple[int, str]:
        status, text, _ = self.fetch("GET", url, **kw)
        return status, text

    def queries(self, product: Product) -> list[str]:
        """Queries to try in order, per `search_by` (ean | name | ean_then_name).

        Name queries go from specific to broad: manufacturer part number, full name, short model name.
        """
        mode = self.cfg.get("search_by", "ean_then_name")
        eans = []
        if product.ean:
            eans.append(product.ean)
            if product.ean.startswith("0"):  # some shops store the 12-digit UPC
                eans.append(product.ean.lstrip("0"))
        if not product.name:  # supplier file without names and no name found by EAN: EAN search only
            return eans
        names = [c.strip("()[],;") for c in product.name.split() if _is_part_number(c)][:1]
        names += [clean_query(product.name), short_name(product)]
        if self.cfg.get("broad_query"):  # strict search engines: last try with brand + model code
            names.append(broad_query(product))
        if self.cfg.get("split_letter_digits"):  # sites that write "HERO 12", not "HERO12"
            names = [re.sub(r"\b([A-Za-z]{3,})(\d+)\b", r"\1 \2", q) for q in names]
        names = list(dict.fromkeys(q for q in names if q))  # dedupe, keep order
        if mode == "ean":
            return eans
        if mode == "name":
            return names
        return eans + names

    def search(self, product: Product, threshold: float) -> list[Offer]:
        """Search with a circuit breaker: after N failures in a row (site blocking us), skip the site."""
        limit = self.cfg.get("max_consecutive_failures", 3)
        if self.failures >= limit:
            raise RuntimeError(f"skipped - failed {self.failures} times in a row (blocked?), "
                               f"last error: {self.last_error}")
        rate_limited = [f"HTTP {code}" for code in self.cfg.get("retry_http", [])]
        for attempt in range(len(RETRY_WAITS) + 1):
            try:
                result = self._search(product, threshold)
                break
            except Exception as exc:
                last_try = attempt == len(RETRY_WAITS)
                if not last_try and is_transient(exc):  # network hiccup, not the site blocking us
                    time.sleep(RETRY_WAITS[attempt])
                    continue
                if not last_try and any(code in str(exc) for code in rate_limited):  # "slow down": wait longer
                    time.sleep(RATE_LIMIT_WAITS[attempt])
                    continue
                self.failures += 1
                self.last_error = str(exc)
                raise
        self.failures = 0
        return result

    def _search(self, product: Product, threshold: float) -> list[Offer]:
        """Try each query until one yields a match. Returns every scored offer seen."""
        seen: list[Offer] = []
        urls: set[str] = set()
        for q in self.queries(product)[: self.cfg.get("max_queries", 99)]:
            offers = [o for o in self.search_raw(q)[: self.max_results] if o.url not in urls]
            urls.update(o.url for o in offers)
            for o in offers:
                o.match_score, o.match_reason = score_match(product, o)
            self._enrich(product, offers, threshold)
            seen.extend(offers)
            if any(o.match_score >= threshold for o in offers):
                break
        return seen

    def _enrich(self, product: Product, offers: list[Offer], threshold: float) -> None:
        """Open product pages for likely matches that lack a price, stock or EAN (if the site supports it)."""
        page_cfg = self.cfg.get("product_page")
        if not page_cfg:
            return
        truncated = page_cfg.get("titles_truncated")  # cut-off card titles: judge them on the full page title
        candidates = [o for o in offers if (truncated or o.match_score >= threshold - 0.15)
                      and (o.price is None or o.ean is None or o.in_stock is None or truncated)]
        candidates.sort(key=lambda o: -(fuzz.partial_ratio(product.name.lower(), o.title.lower())
                                        if truncated else o.match_score))
        for o in candidates[: page_cfg.get("max_pages", 3)]:
            try:
                self.enrich_from_page(o, page_cfg)
            except Exception as exc:  # a broken product page should not lose the search result
                o.match_reason += f" (page error: {exc})"
                continue
            o.match_score, o.match_reason = score_match(product, o)

    def enrich_from_page(self, offer: Offer, page_cfg: dict[str, Any]) -> None:
        status, html = self.get(offer.url)
        if status != 200:
            return
        products = jsonld_products(html)
        if products:
            ld = offer_from_jsonld(self, products[0], offer.url)
            if offer.price is None and ld.price is not None:
                offer.price = ld.price
                if not self.cfg.get("force_currency"):  # some sites label EUR prices as BGN
                    offer.currency = ld.currency or offer.currency
            if ld.in_stock is not None and offer.in_stock is None:
                offer.in_stock, offer.availability_text = ld.in_stock, ld.availability_text
            offer.ean = offer.ean or ld.ean
            if len(ld.title) > len(offer.title):  # full name instead of a cut-off card title
                offer.title = ld.title
        soup = BeautifulSoup(html, "lxml")
        if not offer.ean and page_cfg.get("ean"):
            offer.ean = select_value(soup, page_cfg["ean"])
        if page_cfg.get("availability"):
            text = select_value(soup, page_cfg["availability"])
            if text:
                offer.availability_text = text
                offer.in_stock = self.availability(text)
        if offer.price is None and page_cfg.get("price"):
            offer.price = parse_price(select_value(soup, page_cfg["price"]))

    def offer(self, **kw) -> Offer:
        kw.setdefault("currency", self.cfg.get("currency", "EUR"))
        if self.cfg.get("force_currency"):  # the site mislabels its currency
            kw["currency"] = self.cfg.get("currency", "EUR")
        if kw.get("price") is not None and kw["price"] <= 0:  # "0.00" on discontinued items = no price
            kw["price"] = None
        # price-comparison sites only list products that shops are offering
        if kw.get("in_stock") is None and self.cfg.get("assume_in_stock"):
            kw["in_stock"] = True
        return Offer(site=self.name, scope=self.scope, **kw)

    def availability(self, text: Any) -> Optional[bool]:
        """Map a site's availability value to True/False/None using the configured keywords."""
        if text is None:
            return None
        if isinstance(text, bool):
            return text
        t = str(text).lower()
        for word in self.cfg.get("out_of_stock_values", DEFAULT_OOS):
            if word.lower() in t:
                return False
        # orderable but not on the shelf (supplier delivery, pre-order): neither yes nor no
        for word in self.cfg.get("on_order_values", DEFAULT_ON_ORDER):
            if word.lower() in t:
                return None
        for word in self.cfg.get("in_stock_values", DEFAULT_IN):
            if word.lower() in t:
                return True
        return None

    def url(self, template: str, query: str) -> str:
        """{query} -> URL-encoded for a query string, {query_path} -> encoded for a path segment."""
        return (template.replace("{query_path}", quote(query, safe=""))
                .replace("{query}", quote_plus(query)).replace("{query_raw}", query))


DEFAULT_IN = ["instock", "in stock", "в наличност", "на склад", "наличен", "наличност", "available",
              "limitedavailability", "lieferbar", "auf lager", "disponibile"]
DEFAULT_OOS = ["outofstock", "out of stock", "soldout", "sold out", "изчерпан", "няма наличност", "не е наличен",
               "discontinued", "nicht verfügbar", "non disponibile"]
DEFAULT_ON_ORDER = ["preorder", "pre-order", "преордър", "при доставчик", "с доставка до", "по заявка",
                    "latedelivery", "backorder"]

MOQ_NOTE = re.compile(r"\(\s*moq[^)]*\)", re.I)


def clean_query(name: str) -> str:
    """Product name as a search query: no supplier notes, no punctuation that confuses shop search."""
    q = MOQ_NOTE.sub(" ", name)
    q = re.sub(r"[\"“”]", " ", q)
    return re.sub(r"\s+", " ", q).strip()


def parse_price(value: Any) -> Optional[float]:
    """'1 234,56 лв.' / '€1.234,56' / '1234.56' / 1234.56 -> 1234.56"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(" ", " ")
    m = re.search(r"\d[\d\s.,']*", text)
    if not m:
        return None
    num = re.sub(r"[\s']", "", m.group(0)).rstrip(".,")
    if "," in num and "." in num:
        num = num.replace(".", "").replace(",", ".") if num.rfind(",") > num.rfind(".") else num.replace(",", "")
    elif "," in num:
        head, _, tail = num.rpartition(",")
        num = head.replace(",", "") + "." + tail if len(tail) != 3 else num.replace(",", "")
    elif num.count(".") > 1 or (num.count(".") == 1 and len(num.rpartition(".")[2]) == 3 and len(num) > 4):
        num = num.replace(".", "")
    try:
        return float(num)
    except ValueError:
        return None


def detect_currency(text: str, default: str) -> str:
    t = str(text).lower()
    if "лв" in t or "bgn" in t:
        return "BGN"
    if "€" in t or "eur" in t:
        return "EUR"
    if "zł" in t or "pln" in t:
        return "PLN"
    if "kč" in t or "czk" in t:
        return "CZK"
    if "lei" in t or "ron" in t:
        return "RON"
    if "£" in t or "gbp" in t:
        return "GBP"
    return default


def select_value(node: Any, spec: str | None) -> Optional[str]:
    """CSS selector spec: 'a.title' -> text, 'a.title@href' -> attribute, 'sel1 || sel2' -> first hit."""
    if not spec:
        return None
    for alt in spec.split("||"):
        css, _, attr = alt.strip().rpartition("@") if "@" in alt else ("", "", "")
        if not attr:
            css = alt.strip()
        # '@data-name' alone reads an attribute of the item itself
        el = node.select_one(css.strip()) if css.strip() else node
        if el is None:
            continue
        value = el.get(attr) if attr else el.get_text(" ", strip=True)
        if isinstance(value, list):  # multi-valued attributes such as class
            value = " ".join(value)
        if value:
            return str(value).strip()
    return None


def dig(data: Any, path: str | None) -> Any:
    """Read a dotted path like 'data.items[0].price.value' from nested JSON. '*' is not supported."""
    if not path:
        return None
    cur = data
    for part in re.findall(r"[^.\[\]]+|\[\d+\]", path):
        if cur is None:
            return None
        if part.startswith("["):
            idx = int(part[1:-1])
            cur = cur[idx] if isinstance(cur, list) and len(cur) > idx else None
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def jsonld_products(html: str) -> list[dict[str, Any]]:
    """All schema.org Product objects embedded as JSON-LD."""
    soup = BeautifulSoup(html, "lxml")
    found = []

    def walk(node):
        if isinstance(node, list):
            for n in node:
                walk(n)
        elif isinstance(node, dict):
            types = node.get("@type")
            types = types if isinstance(types, list) else [types]
            if "Product" in types:
                found.append(node)
            for key in ("@graph", "itemListElement", "item", "mainEntity"):
                if key in node:
                    walk(node[key])

    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            walk(json.loads(tag.string or tag.get_text() or "{}", strict=False))
        except json.JSONDecodeError:
            continue
    return found


REFURB_RE = re.compile(r"refurb|renewed|tweedekans|outlet|used|gebraucht|b-ware|2nd|second", re.I)


def _is_new(o: dict[str, Any]) -> bool:
    cond = str(o.get("itemCondition") or "NewCondition")
    seller = o.get("seller") or {}
    seller_name = seller.get("name", "") if isinstance(seller, dict) else str(seller)
    return "NewCondition" in cond and not REFURB_RE.search(seller_name)


def offer_from_jsonld(adapter: SiteAdapter, data: dict[str, Any], fallback_url: str) -> Offer:
    offers = data.get("offers") or {}
    if isinstance(offers, list):  # list of shop offers
        offers = {"offers": offers, **(offers[0] if offers else {})}
    price = parse_price(offers.get("price") or offers.get("lowPrice"))
    avail = offers.get("availability")
    in_stock = adapter.availability(avail)
    shop_count = int(offers["offerCount"]) if str(offers.get("offerCount", "")).isdigit() else None

    # price-comparison pages list every shop: use new, in-stock offers only (drops refurbished outliers)
    shop_offers = [o for o in offers.get("offers") or [] if isinstance(o, dict)] if isinstance(offers, dict) else []
    if len(shop_offers) > 1 or offers.get("@type") == "AggregateOffer" and shop_offers:
        new = [o for o in shop_offers if _is_new(o)]
        stocked = [o for o in new if adapter.availability(o.get("availability")) is not False]
        prices = [p for p in (parse_price(o.get("price")) for o in (stocked or new)) if p]
        if prices:
            price = min(prices)
        shop_count = len(new)
        in_stock = True if stocked else (False if new else in_stock)
        avail = f"{len(stocked)}/{len(new)} shops in stock"

    ean = next((str(data[k]) for k in ("gtin13", "gtin", "gtin12", "gtin14", "gtin8", "ean") if data.get(k)), None)
    return adapter.offer(
        title=str(data.get("name") or "").strip(),
        url=data.get("url") or offers.get("url") or fallback_url,
        price=price,
        currency=offers.get("priceCurrency") or adapter.cfg.get("currency", "EUR"),
        in_stock=in_stock,
        availability_text=str(avail or "").rsplit("/", 1)[-1] if "shops" not in str(avail) else avail,
        ean=ean,
        shop_count=shop_count,
    )
