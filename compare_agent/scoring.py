"""Three 1-10 scores per product, then an overall 1-10 score and a verdict.

  buy price   supplier price vs the cheapest EU price           (global check)
  sell price  margin at the Bulgarian selling price              (local check)
  sales       how much it sells: shops offering it / searches     (both)

Prices are compared net of VAT. No other costs are assumed.
"""
from __future__ import annotations

import math
import statistics
from typing import Any, Optional

from .models import Evaluation, Market, Offer, Product


def to_eur_net(offer: Offer, site_cfg: dict[str, Any], settings: dict[str, Any]) -> Optional[float]:
    if offer.price is None:
        return None
    rate = settings["fx_to_eur"].get(offer.currency.upper())
    if rate is None:
        return None
    vat = site_cfg.get("vat")
    if vat is None:
        vat = settings["vat_by_country"].get(site_cfg.get("country", "EU"), settings["vat_by_country"]["EU"])
    return offer.price * rate / (1 + vat)


def supplier_price_eur(product: Product, settings: dict[str, Any]) -> Optional[float]:
    if product.supplier_price is None:
        return None
    rate = settings["fx_to_eur"].get(product.supplier_currency.upper())
    return product.supplier_price * rate if rate else None


def scale(value: Optional[float], at_1: float, at_10: float) -> Optional[float]:
    """Linear map onto 1..10 (clamped), one decimal."""
    if value is None:
        return None
    x = 1 + 9 * (value - at_1) / (at_10 - at_1)
    return round(max(1.0, min(10.0, x)), 1)


def log_scale(value: Optional[float], at_10: float) -> Optional[float]:
    """0 -> 1, at_10 -> 10, logarithmic in between."""
    if value is None:
        return None
    x = 1 + 9 * math.log10(1 + value) / math.log10(1 + at_10)
    return round(max(1.0, min(10.0, x)), 1)


def search_trend(monthly: dict[str, int], change: float, min_avg: float = 30) -> str:
    """Last 3 months vs the 3 before: "Rising" / "Falling" beyond +-change, else "Stable".
    Below min_avg searches a month the ups and downs are noise (0 -> 10 is not a trend): no trend."""
    values = [v for _, v in sorted(monthly.items())]
    if len(values) < 6 or sum(values[-6:]) / 6 < min_avg:
        return ""
    recent, before = sum(values[-3:]) / 3, sum(values[-6:-3]) / 3
    if before == 0:
        return "Rising" if recent > 0 else ""
    ratio = recent / before - 1
    return "Rising" if ratio > change else ("Falling" if ratio < -change else "Stable")


def market(scope: str, offers: list[Offer]) -> Market:
    m = Market(scope=scope)
    best: dict[str, Offer] = {}
    for o in offers:  # best match per site
        if o.scope == scope and (o.site not in best or o.match_score > best[o.site].match_score):
            best[o.site] = o
    m.listed_on = len(best)
    m.in_stock_on = sum(1 for o in best.values() if o.in_stock)
    m.shops = sum(o.shop_count or 1 for o in best.values())
    priced = [o.price_eur_net for o in best.values() if o.price_eur_net is not None]
    stocked = [o.price_eur_net for o in best.values() if o.price_eur_net is not None and o.in_stock is not False]
    if priced:
        m.min_price = round(min(priced), 2)
        m.median_price = round(statistics.median(priced), 2)
    if stocked:
        m.min_in_stock = round(min(stocked), 2)
    return m


def evaluate(ev: Evaluation, settings: dict[str, Any]) -> None:
    sc = settings["scoring"]
    lo, gl = ev.local, ev.global_
    buy = supplier_price_eur(ev.product, settings)

    # prices the scores are based on
    ev.eu_price = gl.min_in_stock or gl.min_price or lo.min_in_stock
    # "sold in Bulgaria" = at least one BG shop has it in stock or on order
    if lo.min_in_stock is not None:
        ev.bg_sell_price = lo.min_in_stock      # we have to match the cheapest BG competitor
        ev.sell_price_source = "Bulgaria"
    elif gl.median_price is not None:           # not sold in Bulgaria: worldwide price level
        ev.bg_sell_price = gl.median_price
        ev.sell_price_source = ("Worldwide (out of stock in BG)" if lo.listed_on
                                else "Worldwide (not sold in BG)")
    elif lo.median_price is not None:           # only old out-of-stock BG listings, nothing worldwide
        ev.bg_sell_price = lo.median_price
        ev.sell_price_source = "Bulgaria (out of stock)"

    # 1) buy price
    if buy is not None and ev.eu_price:
        b = sc["buy_price"]
        ev.buy_price_score = scale(1 - buy / ev.eu_price, b["discount_for_1"], b["discount_for_10"])

    # 2) sell price
    if buy is not None and ev.bg_sell_price:
        ev.margin = round((ev.bg_sell_price - buy) / ev.bg_sell_price, 4)
        s = sc["sell_price"]
        ev.sell_price_score = scale(ev.margin, s["margin_for_1"], s["margin_for_10"])

    # 3) sales
    # Bulgarian demand decides (we sell in BG); worldwide shop counts stand in only when BG has none,
    # because price-comparison sites list 50-200 shops for any popular item and would rate everything high.
    d = sc["sales"]
    w = d.get("weights", {"bg_searches": 0.45, "bg_shops": 0.35, "world_searches": 0.2})
    parts = []
    if lo.listed_on:
        parts.append((log_scale(lo.shops, d["bg_shops_for_10"]), w["bg_shops"]))
    elif gl.listed_on:
        parts.append((log_scale(gl.shops, d["world_shops_for_10"]), w["bg_shops"]))
    if lo.searches is not None:
        parts.append((log_scale(lo.searches, d["bg_searches_for_10"]), w["bg_searches"]))
    if gl.searches is not None:
        parts.append((log_scale(gl.searches, d["world_searches_for_10"]), w["world_searches"]))
    if parts:
        ev.sales_score = round(sum(v * wt for v, wt in parts) / sum(wt for _, wt in parts), 1)
    if ev.sales_score is None:
        ev.sells = "No data"
    elif ev.sales_score >= d.get("sells_well_from", 7):
        ev.sells = "Sells well"
    elif ev.sales_score >= d.get("average_from", 4):
        ev.sells = "Average"
    else:
        ev.sells = "Weak"
    ev.trend = search_trend(lo.monthly or gl.monthly, d.get("trend_change", 0.15), d.get("trend_min_searches", 30))

    # overall
    w = sc["weights"]
    scored = [(ev.buy_price_score, w["buy_price"]), (ev.sell_price_score, w["sell_price"]),
              (ev.sales_score, w["sales"])]
    scored = [(v, wt) for v, wt in scored if v is not None]
    if scored and buy is not None:  # without a supplier price an overall score would be misleading
        ev.final_score = round(sum(v * wt for v, wt in scored) / sum(wt for _, wt in scored), 1)

    v = sc["verdict"]
    if not (lo.listed_on or gl.listed_on):
        ev.verdict = "NO DATA"
    elif buy is None:
        ev.verdict = "NEED PRICE"
    elif ev.margin is None:
        ev.verdict = "CHECK"
    elif ev.margin < v["min_margin"]:
        ev.verdict = "SKIP"
    elif ev.final_score >= v["buy"]:
        ev.verdict = "BUY"
    elif ev.final_score >= v["consider"]:
        ev.verdict = "CONSIDER"
    else:
        ev.verdict = "SKIP"
