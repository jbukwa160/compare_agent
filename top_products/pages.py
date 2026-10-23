"""Opens a shop page and tells whether it is a product page: name, brand, price, stock, category, barcodes."""
from __future__ import annotations

import html as htmllib
import json
import re
import time
from urllib.parse import unquote, urlparse

from bs4 import BeautifulSoup
from rapidfuzz import fuzz

from compare_agent.http import HttpClient

from .config import CACHE

PRODUCT_TYPES = {"product", "productgroup", "drug", "dietarysupplement", "individualproduct", "productmodel"}
SKIP_KEYS = {"offers", "review", "reviews", "aggregateRating", "isRelatedTo", "isSimilarTo", "isAccessoryOrSparePartFor"}
BLOCK_MARKERS = ("just a moment...", "cf-chl", "attention required! | cloudflare", "awswaf", "captcha-delivery",
                 "/cdn-cgi/challenge-platform", "access denied")
AVAILABILITY_BG = {"instock": "в наличност", "limitedavailability": "в наличност", "onlineonly": "в наличност",
                   "instoreonly": "само в магазин", "outofstock": "изчерпан", "soldout": "изчерпан",
                   "discontinued": "спрян", "preorder": "предварителна поръчка", "presale": "предварителна поръчка",
                   "backorder": "по поръчка", "notonline": "само в аптека (не се продава онлайн)"}
# pharmacies mark medicines they may not sell online as out of stock; the page itself says so
NOT_ONLINE = re.compile(r"не може да бъде закупен[аио]? онлайн|не се продава онлайн", re.I)
HOME_CRUMBS = {"начало", "начална страница", "home", "главна", "главна страница", "у дома", "магазин", "онлайн магазин",
               "list item", "item", "продукти", "продукти по категории"}
BRAND_PLACEHOLDERS = {"други марки", "други", "издателства", "без марка", "no brand", "generic", "n/a"}
GTIN_KEYS = {"gtin", "gtin8", "gtin12", "gtin13", "gtin14", "ean", "ean13", "barcode"}
GTIN_LABEL = re.compile(r"(?:EAN(?:\s*код)?|Баркод|Бар код|Barcode|GTIN(?:-13)?)\s*[:#№]?\s*(\d{8,14})\b", re.I)
EUR_PER_BGN = 1 / 1.95583

HTTP = HttpClient(CACHE / "pages", cache_ttl_hours=96, min_delay=1.5, timeout=30)


def valid_gtin(code: str) -> bool:
    if not code.isdigit() or len(code) not in (8, 12, 13, 14) or set(code) == {"0"}:
        return False
    digits = [int(c) for c in code[:-1]][::-1]
    return (10 - sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(digits)) % 10) % 10 == int(code[-1])


def norm_gtin(code) -> str:
    """One form for every barcode: 13 digits (UPC-12 gets its leading 0), GTIN-14 as it is."""
    s = re.sub(r"\D", "", str(code or "").split(".")[0])
    if not 8 <= len(s) <= 14:
        return ""
    return s.lstrip("0").zfill(13) if len(s) <= 13 else s


def _ld_json(soup: BeautifulSoup) -> list:
    docs = []
    for s in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
        raw = (s.string or s.get_text() or "").strip()
        for attempt in (raw, re.sub(r",\s*([}\]])", r"\1", raw)):
            try:
                docs.append(json.loads(attempt, strict=False))
                break
            except ValueError:
                continue
    return docs


def _types(node: dict) -> list[str]:
    t = node.get("@type")
    return [str(x).lower() for x in (t if isinstance(t, list) else [t])]


def ld_products(docs: list) -> tuple[list[dict], int, int]:
    """(Product objects outside ItemLists, products inside ItemLists, stand-alone Products = not variants).
    A product page has one stand-alone Product; listing pages have many (in ItemLists or side by side)."""
    own, listed, standalone = [], 0, 0
    queue = [(d, False, False) for d in docs]
    while queue:
        node, in_list, in_product = queue.pop(0)
        if isinstance(node, list):
            queue.extend((x, in_list, in_product) for x in node)
            continue
        if not isinstance(node, dict):
            continue
        is_product = any(t in PRODUCT_TYPES for t in _types(node))
        if is_product:
            if in_list:
                listed += 1
            else:
                own.append(node)
                standalone += not in_product
        for k, v in node.items():
            if isinstance(v, (dict, list)) and k not in SKIP_KEYS:
                queue.append((v, in_list or k == "itemListElement", in_product or is_product))
    return own, listed, standalone


def barcodes(soup: BeautifulSoup, docs: list) -> list[str]:
    """Valid barcodes of the page's own product (its variants included, related products not)."""
    found: set[str] = set()

    def walk(node, inside=False):
        if isinstance(node, list):
            for x in node:
                walk(x, inside)
            return
        if not isinstance(node, dict):
            return
        is_product = any(t in PRODUCT_TYPES for t in _types(node))
        if inside or is_product:
            found.update(re.sub(r"\D", "", str(v)) for k, v in node.items()
                         if k.lower() in GTIN_KEYS and isinstance(v, (str, int)))
        for k, v in node.items():
            if isinstance(v, (dict, list)) and k not in SKIP_KEYS - {"offers"} and k != "itemListElement":
                walk(v, inside or is_product)
    walk(docs)
    for el in soup.select('[itemprop^="gtin"], [itemprop="ean"], [data-ean], td[data-th="EAN"]'):
        found.add(re.sub(r"\D", "", el.get("content") or el.get("data-ean") or el.get_text()))
    for el in soup.find_all(attrs={"ean": True}):        # Technomarket: <tm-pointandplace ean="...">
        found.add(re.sub(r"\D", "", el["ean"]))
    if not found:
        found.update(GTIN_LABEL.findall(soup.get_text(" ", strip=True)))
    return sorted({norm_gtin(c) for c in found if valid_gtin(c)})


def _text(v) -> str:
    if isinstance(v, dict):
        v = v.get("name") or v.get("@value") or ""
    if isinstance(v, list):
        v = _text(v[0]) if v else ""
    return " ".join(htmllib.unescape(str(v or "")).split())


def _num(v) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    s = str(v).strip().replace("\xa0", "").replace(" ", "")
    if "," in s and "." in s:          # 1.299,00 or 1,299.00
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    else:
        s = s.replace(",", ".")
    m = re.search(r"\d+(?:\.\d+)?", s)
    return float(m.group()) if m else None


def breadcrumb(docs: list, soup: BeautifulSoup, product_name: str, shop_words: set[str]) -> tuple[str, str]:
    """(full, last 3) shop category path from the BreadcrumbList, without "Home", the shop and the product."""
    queue, names = list(docs), []
    while queue and not names:
        node = queue.pop(0)
        if isinstance(node, list):
            queue.extend(node)
            continue
        if not isinstance(node, dict):
            continue
        if "breadcrumblist" in _types(node):
            items = [i for i in node.get("itemListElement") or [] if isinstance(i, dict)]
            items.sort(key=lambda i: _num(i.get("position")) or 0)
            for i in items:
                item = i.get("item")
                names.append(_text(i.get("name") or (item.get("name") if isinstance(item, dict) else "")))
        else:
            queue.extend(v for v in node.values() if isinstance(v, (dict, list)))
    if not names:  # microdata
        bl = soup.find(attrs={"itemtype": re.compile(r"schema\.org/BreadcrumbList$", re.I)})
        if bl:
            names = [_text(n.get("content") or n.get_text(" ", strip=True)) for n in bl.find_all(attrs={"itemprop": "name"})]
    # strip decorations ("✪ Процесори", "☎ 0700 150 60"), "Home", the shop's name, phone numbers, the product itself
    names = [re.sub(r"^[^\w(]+|[^\w)]+$", "", n).strip() for n in names]
    names = [n for n in names if n and n.lower() not in HOME_CRUMBS and n.lower() not in shop_words
             and "онлайн магазин" not in n.lower() and not re.fullmatch(r"[\d\s+()/.-]+", n)]
    pn = product_name.lower()
    while names and pn and (names[-1].lower() in pn or pn in names[-1].lower()
                            or fuzz.token_set_ratio(names[-1].lower(), pn) >= 90):
        names.pop()
    names = [n for i, n in enumerate(names) if i == 0 or n.lower() != names[i - 1].lower()]
    return " > ".join(names), " > ".join(names[-3:])


def _offers(product: dict) -> tuple[float | None, float | None, str, str]:
    """(lowest price, highest price, currency, availability) of a schema.org Product (or its variants)."""
    def as_list(v):
        return v if isinstance(v, list) else [v] if v else []
    offers = as_list(product.get("offers"))
    if not offers:  # ProductGroup: the prices live on the variants
        for v in as_list(product.get("hasVariant")):
            if isinstance(v, dict):
                offers += as_list(v.get("offers"))
    prices, currency, avails = [], "", []
    for o in offers:
        if not isinstance(o, dict):
            continue
        spec = o.get("priceSpecification")
        spec = spec[0] if isinstance(spec, list) and spec else spec if isinstance(spec, dict) else {}
        found = [_num(o.get("price")), _num(spec.get("price"))]
        if "aggregateoffer" in _types(o):
            found += [_num(o.get("lowPrice")), _num(o.get("highPrice"))]
            offers += [x for x in as_list(o.get("offers")) if isinstance(x, dict)]
        found = [p for p in found if p]
        if found:
            prices += found
            currency = currency or str(o.get("priceCurrency") or spec.get("priceCurrency") or "").upper()
        avail = str(o.get("availability") or "").rstrip("/").rsplit("/", 1)[-1].lower()
        if avail:
            avails.append(avail)
    in_stock = [a for a in avails if AVAILABILITY_BG.get(a) == "в наличност"]
    avail = (in_stock or avails or [""])[0]
    if not prices:
        return None, None, currency, avail
    return min(prices), max(prices), currency, avail


def _micro_product(el) -> dict:
    """The fields we need from a schema.org Product written as microdata, in JSON-LD shape."""
    def value(t) -> str:
        v = t.get("content") or t.get("href") or t.get_text(" ", strip=True)
        if not v:
            inner = t.find(attrs={"itemprop": "name"})
            v = (inner.get("content") or inner.get_text(" ", strip=True)) if inner else ""
        return v

    def own(name: str) -> str:   # a property of the product itself, not of nested items (breadcrumbs, reviews)
        for t in el.find_all(attrs={"itemprop": name}):
            if t.find_parent(attrs={"itemscope": True}) is el:
                return value(t)
        return ""

    offer_el = el.find(attrs={"itemprop": "offers"}) or el

    def offer(name: str) -> str:
        t = offer_el.find(attrs={"itemprop": name})
        return value(t) if t else ""
    return {"name": own("name"), "brand": own("brand"),
            "offers": {"price": offer("lowPrice") or offer("price"), "highPrice": offer("highPrice"),
                       "priceCurrency": offer("priceCurrency"), "availability": offer("availability")}}


def norm_url(url: str) -> str:
    u = urlparse(url)
    host = u.netloc.lower().removeprefix("www.")
    return host + (unquote(u.path).rstrip("/") or "/") + (f"?{u.query}" if u.query else "")


def strip_shop(name: str, shop_words: set[str]) -> str:
    """"FLUDREX IMMUNOBOOSTER x 20 caps | Subra" -> "FLUDREX IMMUNOBOOSTER x 20 caps"."""
    for sep in (" | ", " - ", " – ", " — "):
        head, _, tail = name.rpartition(sep)
        if head and tail.strip().lower() in shop_words:
            return head.strip()
    return name


def shop_words(shop: dict) -> set[str]:
    return {w.lower() for w in [shop["name"], shop["domain"], *shop.get("aliases", [])]}


def fetch(url: str, shop: dict, impersonate: bool | None = None) -> tuple[int, str, str]:
    """(status, html, final URL) with the shop's own pace, Chrome mode and cache bypass."""
    bust = f"v={time.strftime('%Y%m%d')}" if shop.get("cache_bust") else ""
    status, html, final = HTTP.fetch("GET", url + ("&" if "?" in url else "?") + bust if bust else url,
                                     impersonate=shop.get("impersonate", False) if impersonate is None else impersonate,
                                     delay=shop.get("delay", 1.5))
    return status, html, re.sub(r"[?&]" + re.escape(bust) + "$", "", final) if bust else final


def inspect(url: str, shop: dict, impersonate: bool | None = None) -> dict:
    """Open a page and tell whether it is a product page, with its name / brand / price / barcodes."""
    info: dict = {"url": url}
    try:
        status, html, final = fetch(url, shop, impersonate)
    except Exception as exc:
        return {**info, "status": "error", "error": f"{type(exc).__name__}: {str(exc)[:120]}"}
    info["http"], info["final_url"] = status, final
    if status == 200 and len(html.strip()) < 500:      # quiet rate limiting
        return {**info, "status": "error", "error": "empty page"}
    low = html[:20000].lower()
    if status in (401, 403, 429, 503, 511) or (any(m in low for m in BLOCK_MARKERS) and len(html) < 60000):
        return {**info, "status": "blocked"}
    if status in (404, 410) or status >= 500:
        return {**info, "status": f"http {status}"}
    soup = BeautifulSoup(html, "lxml")
    words = shop_words(shop)
    docs = _ld_json(soup)
    own, listed, standalone = ld_products(docs)
    og_type = (soup.find("meta", property="og:type") or {}).get("content", "").strip().lower()
    micro = soup.find_all(attrs={"itemtype": re.compile(r"schema\.org/(Product|Drug|DietarySupplement)$", re.I)})
    info.update(canonical=(soup.find("link", rel="canonical") or {}).get("href", ""), eans=barcodes(soup, docs))
    h1 = soup.find("h1")
    og_title = (soup.find("meta", property="og:title") or {}).get("content", "")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    p = own[0] if own else (_micro_product(micro[0]) if len(micro) == 1 else {})
    lo, hi, cur, avail = _offers(p) if p else (None, None, "", "")
    if lo is None and shop.get("price_css"):     # shops without structured prices (Plesio, Jumbo, Ozone)
        el = soup.select_one(shop["price_css"])
        if el:
            text = el.get_text(" ", strip=True)
            lo = hi = _num(text)
            cur = "BGN" if "лв" in text.lower() else "EUR"
    if lo is None and len(micro) <= 1:
        meta_price = soup.find("meta", property="product:price:amount")
        if meta_price:
            lo = hi = _num(meta_price.get("content"))
            cur_tag = soup.find("meta", property="product:price:currency")
            cur = (cur_tag.get("content") or "").upper() if cur_tag else cur
    if NOT_ONLINE.search(soup.get_text(" ", strip=True)):
        avail = "notonline"
    if not avail and shop.get("oos_css") and soup.select_one(shop["oos_css"]):
        avail = "outofstock"
    elif not avail and shop.get("instock_css") and soup.select_one(shop["instock_css"]):
        avail = "instock"
    if cur == "BGN":
        lo, hi = (round(lo * EUR_PER_BGN, 2) if lo else lo), (round(hi * EUR_PER_BGN, 2) if hi else hi)
    if lo is not None and lo < 0.05:     # "0.00" = no real price on the page
        lo, hi = None, None
    names = [_text(p.get("name")) if p else "", h1.get_text(" ", strip=True) if h1 else "", _text(og_title), title]
    name = strip_shop(next((n for n in names if n and n.lower() not in HOME_CRUMBS), ""), words)
    if re.match(r"(?i)категории\b", name):     # the page's first heading is a menu label
        name = ""
    brand = _text(p.get("brand")) if p else ""
    if (shop.get("brand_is_shop") and brand.lower() in words) or brand.lower() in BRAND_PLACEHOLDERS:
        brand = ""      # the shop's own name on every product, or "other brands"
    info.update(name=name[:250], brand=brand, price_min=round(lo, 2) if lo else lo, price_max=round(hi, 2) if hi else hi,
                availability=AVAILABILITY_BG.get(avail, ""))
    info["crumbs_full"], info["crumbs"] = breadcrumb(docs, soup, name, words)
    is_product = bool(own) or og_type in ("product", "og:product", "product.item") or len(micro) == 1
    if is_product and (listed >= 3 or standalone >= 3):
        info["status"] = "mixed"            # a listing that also carries Product blocks
    elif is_product:
        info["status"] = "product"
    else:
        info["status"] = "listing" if listed >= 2 or len(micro) >= 3 else "other"
    return info


def inspect_any(url: str, shop: dict) -> dict:
    """inspect(); a page that blocks plain requests is tried once more as Chrome (curl_cffi)."""
    info = inspect(url, shop)
    if info["status"] in ("blocked", "error") and not shop.get("impersonate"):
        info = inspect(url, shop, impersonate=True)
    return info
