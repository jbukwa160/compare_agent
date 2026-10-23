"""Is a product worth stocking? Profit against the cheapest Bulgarian shop, margin and traffic -> score 1-10."""
from __future__ import annotations

from collections import defaultdict


def score(profit: float, margin: float, traffic: float, cfg: dict) -> int:
    """One point per step reached (settings.yaml -> score); too little profit is 1, a thin margin is capped."""
    if profit < cfg["min_profit_eur"]:
        return 1
    points = (sum(profit >= s for s in cfg["profit_eur"]) + sum(margin >= s for s in cfg["margin"])
              + sum(traffic >= s for s in cfg["traffic"]))
    if margin < cfg["low_margin"]:
        points = min(points, cfg["low_margin_max_score"])
    return max(1, min(10, points))


def vendor_table(matches: list[dict], items: list[dict], settings: dict) -> tuple[list[dict], dict]:
    """One row per product (barcode): cheapest vendor, cheapest Bulgarian shop, profit, score.
    Products that are cheaper in Bulgaria than at the vendor (+VAT), or have no Bulgarian price, are left out."""
    vat, fx, cfg = settings["vendors"]["vat"], settings["vendors"]["fx_to_eur"], settings["score"]
    offers = defaultdict(dict)        # barcode -> vendor -> its cheapest row
    for i in items:
        if i["currency"] not in fx:
            continue
        eur = round(i["price"] * fx[i["currency"]], 2)
        best = offers[i["ean"]].get(i["vendor"])
        if best is None or eur < best["eur"]:
            offers[i["ean"]][i["vendor"]] = {**i, "eur": eur}
    shops = defaultdict(dict)         # barcode -> shop page URL -> (row, how)
    for m in matches:
        shops[m["ean"]][m["row"]["url"]] = (m["row"], m["how"])

    table, left_out = [], {"cheaper_in_bg": 0, "no_bg_price": 0}
    for ean, pages in shops.items():
        vendors = sorted(offers[ean].values(), key=lambda v: v["eur"])
        if not vendors:
            continue
        cheapest = vendors[0]
        listed = [r for r, _ in pages.values()]
        priced = sorted((r for r in listed if r.get("price_min") is not None), key=lambda r: r["price_min"])
        if not priced:
            left_out["no_bg_price"] += 1
            continue
        buy = round(cheapest["eur"] * (1 + vat), 2)
        bg = priced[0]
        profit = round(bg["price_min"] - buy, 2)
        if profit <= 0:
            left_out["cheaper_in_bg"] += 1
            continue
        margin = profit / bg["price_min"]
        traffic = sum(r["etv"] or 0 for r in listed)
        hows = {how for _, how in pages.values()}
        table.append({
            "score": score(profit, margin, traffic, cfg), "name": cheapest["name"] or listed[0]["name"], "ean": ean,
            "vendor": cheapest["vendor"], "vendor_price": cheapest["eur"], "vendor_price_vat": buy,
            "original": f"{cheapest['price']:.2f} {cheapest['currency']}" if cheapest["currency"] != "EUR" else None,
            "stock": cheapest["stock"], "bg_price": bg["price_min"], "bg_shop": bg["store"], "bg_url": bg["url"],
            "profit": profit, "margin": margin, "traffic": traffic,
            "shops": "; ".join(f"{r['store']} {r['price_min']:.2f}" if r.get("price_min") is not None else f"{r['store']} (без цена)"
                               for r in sorted(listed, key=lambda r: (r.get("price_min") is None, r.get("price_min") or 0))),
            "other_vendors": "; ".join(f"{v['vendor']} {v['eur']:.2f}" for v in vendors[1:]) or None,
            "how": "баркод" if "баркод" in hows else ("по име – за проверка" if any("за проверка" in h for h in hows)
                                                       else "по име (проверено)")})
    table.sort(key=lambda r: (-r["score"], -r["profit"]))
    return table, left_out
