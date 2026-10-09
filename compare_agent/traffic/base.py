"""Per-product traffic (monthly searches) sources, configured under `traffic:` in sites.yaml.

Every source gets the whole product list at once (`lookup_many`), because the paid APIs charge
per request and accept many keywords per request.
"""
from __future__ import annotations

import os
import re
from typing import Any, Callable

from ..http import HttpClient
from ..matching import VARIANT_WORDS, short_name, tokens
from ..models import Product, Traffic

SOURCES: dict[str, type["TrafficSource"]] = {}


class SourceAccountError(RuntimeError):
    """Account-level problem (wrong credentials, no balance): stop asking this source for this run."""


def register(name: str) -> Callable[[type["TrafficSource"]], type["TrafficSource"]]:
    def deco(cls):
        SOURCES[name] = cls
        return cls
    return deco


def build_source(cfg: dict[str, Any], http: HttpClient) -> "TrafficSource":
    kind = cfg.get("type")
    if kind not in SOURCES:
        raise ValueError(f"Traffic source '{cfg['name']}': unknown type '{kind}'. Known: {sorted(SOURCES)}")
    return SOURCES[kind](cfg, http)


def keyword_for(product: Product, max_words: int = 5) -> str:
    """Search phrase people would type: brand + model ('samsung 990 pro 1tb'); input column `keyword` overrides."""
    return product.keyword.lower() if product.keyword else short_name(product, max_words)


GENERATION_WORDS = {"mark", "gen", "ii", "iii", "iv", "v", "vi"}


def keyword_variants(product: Product, max_words: int = 5) -> list[str]:
    """The full model name plus the short form people type: brand + model code + generation
    ("canon eos r6 mark iii" -> "canon r6 mark iii", "jbl tune 110 t110 3.5mm" -> "jbl t110").
    Google often has no data for the long form, so both are asked and the better-supported one is kept."""
    full = keyword_for(product, max_words)
    if product.keyword:
        return [full]
    words = tokens(short_name(product, 8))
    alnum = [w for w in words[1:] if re.fullmatch(r"[a-z]+\d+[a-z\d]*", w)]  # T110, R6, CX400
    # first model code in the name; a bare number that a letter code repeats ("110" in "T110") is skipped
    codes = [i for i, w in enumerate(words[1:], 1)
             if w in alnum or (re.fullmatch(r"\d{3,}", w) and not any(w in a for a in alnum))]
    if not words or not codes:
        return [full]
    i = codes[0]
    prefix = []
    if words[i].isdigit():  # a bare number needs its product line: "scanjet pro 2000", "fritz box 5690"
        j = i - 1
        while j > 0 and words[j] in VARIANT_WORDS:
            j -= 1
        if j > 0 and words[j].isalpha():
            prefix = words[j:i]
    tail = []
    for w in words[i + 1:]:
        if w in GENERATION_WORDS or w in VARIANT_WORDS:
            tail.append(w)
        else:
            break
    short = " ".join([words[0]] + prefix + [words[i]] + tail)
    return [full] if short == full else [full, short]


class TrafficSource:
    def __init__(self, cfg: dict[str, Any], http: HttpClient):
        self.cfg = cfg
        self.http = http
        self.name = cfg["name"]
        self.local_markets: list[str] = list(cfg.get("local_markets") or [])
        self.global_markets: list[str] = list(cfg.get("global_markets") or [])

    def env(self, key: str) -> str:
        return os.environ.get(self.cfg.get(key, ""), "").strip()

    @property
    def missing_credentials(self) -> str | None:
        """Name of the missing env variable, or None when the source can run."""
        return None

    def volumes(self, keywords: list[str], market: str) -> dict[str, Traffic]:
        """Monthly searches for each keyword in one market (country)."""
        raise NotImplementedError

    def lookup_many(self, products: list[Product]) -> dict[int, tuple[list[Traffic], list[Traffic]]]:
        """{product row: (BG traffic, worldwide traffic)}"""
        keywords = {p.row: keyword_variants(p, self.cfg.get("max_keyword_words", 5)) for p in products}
        unique = sorted({k for kws in keywords.values() for k in kws})
        out: dict[int, tuple[list[Traffic], list[Traffic]]] = {p.row: ([], []) for p in products}
        fatal: str | None = None
        for scope_idx, markets in enumerate((self.local_markets, self.global_markets)):
            for market in markets:
                try:
                    if fatal:  # e.g. wrong login or empty balance: other markets would fail the same way
                        raise SourceAccountError(fatal)
                    found = self.volumes(unique, market)
                except Exception as exc:  # a failing source must not stop the run
                    if isinstance(exc, SourceAccountError):
                        fatal = str(exc)
                    found = {k: Traffic(database=market, keyword=k, error=str(exc)[:150]) for k in unique}
                for row, kws in keywords.items():
                    options = [found.get(k) or Traffic(database=market, keyword=k, volume=0) for k in kws]
                    # the phrasing with the most searches wins; on a tie, the more specific (first) one
                    t = max(options, key=lambda o: (o.error is None, o.volume or 0, -options.index(o)))
                    t = Traffic(**{**t.__dict__})  # own copy: several products can share a keyword
                    t.source, t.scope = self.name, ("local", "global")[scope_idx]
                    out[row][scope_idx].append(t)
        return out
