"""Which products of the table the EU vendors offer: same barcode, else same brand and model (reviewed by hand)."""
from __future__ import annotations

from collections import defaultdict

from compare_agent.matching import key_tokens, score_match, tokens
from compare_agent.models import Offer, Product

MAX_CANDIDATES = 400          # more vendor items share only generic numbers with the name: no name match
BARCODE, CHECKED, TO_CHECK = "баркод", "по име (проверено)", "по име – за проверка"


def _words(text: str) -> set[str]:
    return {t for t in tokens(text) if t.isalpha() and len(t) >= 3}


def match(rows: list[dict], eans: dict[str, list[str]], items: list[dict], threshold: float,
          accepted: set[tuple[str, str]], rejected: set[tuple[str, str]]) -> list[dict]:
    """[{row, ean, how}] for every (product row, vendor barcode) pair that is the same product."""
    by_ean = defaultdict(list)
    for i in items:
        by_ean[i["ean"]].append(i)
    index, words, names = defaultdict(set), defaultdict(set), {}
    for e, its in by_ean.items():
        name = next((i["name"] for i in its if i["name"]), "")
        brand = next((i["brand"] for i in its if i["brand"]), "")
        if name:
            names[e] = (name, brand)
            for t in key_tokens(name):
                index[t].add(e)
            for t in _words(f"{brand} {name}"):
                words[t].add(e)

    out = []
    for row in rows:
        own = set(eans.get(row["url"], []))
        hits = {e: BARCODE for e in own if e in by_ean}
        if not hits:
            keys, ws = key_tokens(row["name"]), _words(f"{row.get('brand') or ''} {row['name']}")
            cands = set()
            if keys and ws:
                cands = set.intersection(*(index.get(t, set()) for t in keys)) & set().union(*(words.get(t, set()) for t in ws))
                if len(cands) > MAX_CANDIDATES:
                    cands = set()
            offer = Offer(site=row["store"], scope="local", title=row["name"], url=row["url"],
                          ean=next(iter(own)) if len(own) == 1 else None)
            for e in cands:
                name, brand = names[e]
                if score_match(Product(row=0, name=name, ean=e, brand=brand or None), offer)[0] >= threshold:
                    hits[e] = CHECKED if (row["url"], e) in accepted else TO_CHECK
        for url, e in accepted:            # reviewed matches the name matcher does not find
            if url == row["url"] and e in by_ean:
                hits[e] = CHECKED
        out += [{"row": row, "ean": e, "how": how} for e, how in hits.items() if (row["url"], e) not in rejected]
    return out
