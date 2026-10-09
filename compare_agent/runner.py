"""Orchestrates one run: traffic sources + products x reference sites -> scored evaluations."""
from __future__ import annotations

import logging
import os
import re
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable, Optional

from . import matching
from .http import HttpClient
from .models import Evaluation, Market, Offer, Product, Traffic
from .scoring import evaluate, market, to_eur_net
from .traffic import TrafficSource, build_source
from .sites import load_adapters  # noqa: F401  (registers adapter types)
from .sites.base import SiteAdapter, build_adapter
from .sites.shopping import site_cfg_for

log = logging.getLogger("compare_agent")


class Runner:
    def __init__(self, settings: dict[str, Any], sites: list[dict[str, Any]], cache_dir: Path,
                 use_cache: bool = True, traffic_sources: list[dict[str, Any]] | None = None):
        self.settings = settings
        self.site_cfgs = {s["name"]: s for s in sites}
        h = settings.get("http", {})
        self.http = HttpClient(cache_dir, cache_ttl_hours=h.get("cache_ttl_hours", 12),
                               min_delay=h.get("min_delay_seconds", 1.5), timeout=h.get("timeout_seconds", 25),
                               use_cache=use_cache)
        # sites that need an API key (`api_key_env`) are skipped until the key is in .env
        self.skipped_sites = [(s["name"], s["api_key_env"]) for s in sites
                              if s.get("api_key_env") and not os.environ.get(s["api_key_env"], "").strip()]
        skip = {name for name, _ in self.skipped_sites}
        self.adapters: list[SiteAdapter] = [build_adapter(s, self.http) for s in sites if s["name"] not in skip]
        self.traffic: list[TrafficSource] = [build_source(t, self.http) for t in traffic_sources or []]
        self.threshold = settings["scoring"]["match_threshold"]
        matching.EAN_MISMATCH_PENALTY = settings["scoring"].get("ean_mismatch_penalty", 0.15)

    def _search(self, product: Product, adapter: SiteAdapter) -> tuple[list[Offer], Optional[str]]:
        try:
            return adapter.search(product, self.threshold), None
        except Exception as exc:  # one broken site must not stop the run
            if "skipped" not in str(exc):  # the first failures are worth a line; "skipped" repeats are not
                log.warning("%s: %s (%s)", adapter.name, str(exc)[:100], product.name[:40])
            return [], f"{adapter.name}: {exc}"

    def run(self, products: list[Product], progress: Callable[[int, int, Product], None] | None = None,
            worldwide: str = "always") -> list[Evaluation]:
        """worldwide = "always": every product is checked in Bulgaria and worldwide.
        worldwide = "fallback": worldwide only for products nobody in Bulgaria sells."""
        results: dict[int, dict[str, Any]] = {p.row: {"offers": [], "errors": []} for p in products}

        local = [a for a in self.adapters if a.scope == "local"]
        world = [a for a in self.adapters if a.scope != "local"]
        searched = local + world if worldwide == "always" else local
        batch = [a for a in searched if getattr(a, "batch", False)]  # e.g. Google Shopping: all products at once
        for a in batch:
            log.warning("%s: searching %d products (results in about a minute)...", a.name, len(products))
            try:
                found, errs = a.search_many(products, self.threshold)
            except Exception as exc:  # a failing source must not stop the run
                found, errs = {}, [f"{a.name}: {exc}"]
            for row, offers in found.items():
                results[row]["offers"].extend(offers)
            for err in errs[:3]:
                log.warning(err)
        self._search_all(products, [a for a in searched if a not in batch], results, progress)
        name_from_listings(products, results)
        # traffic: one batch per source and market for the whole file (paid APIs bill per request)
        for src in self.traffic:
            if src.missing_credentials:
                log.info("traffic source %s skipped: set %s in .env", src.name, src.missing_credentials)
                continue
            found = src.lookup_many(products)
            for row, (local_t, global_t) in found.items():
                results[row].setdefault("traffic_local", []).extend(local_t)
                results[row].setdefault("traffic_global", []).extend(global_t)
            problems = sorted({f"{t.database}: {t.error}" for lt, gt in found.values() for t in lt + gt if t.error})
            if problems:  # say it now, not only in the Excel Errors sheet
                log.warning("traffic %s: %s%s", src.name, problems[0],
                            f" (+{len(problems) - 1} more)" if len(problems) > 1 else "")

        evaluations = {p.row: self.evaluate(p, results[p.row]) for p in products}

        if worldwide != "always" and world:
            # not sold in Bulgaria = no BG shop has it in stock or on order
            missing = [p for p in products if evaluations[p.row].local.min_in_stock is None]
            if missing:
                log.warning("not sold in Bulgaria: %d products -> worldwide check", len(missing))
                if progress:
                    progress(0, len(missing), None)
                self._search_all(missing, world, results, progress)
                evaluations.update({p.row: self.evaluate(p, results[p.row]) for p in missing})
        return [evaluations[p.row] for p in products]

    def _search_all(self, products: list[Product], adapters: list[SiteAdapter], results: dict[int, dict[str, Any]],
                    progress: Callable[[int, int, Product], None] | None) -> None:
        if not adapters:
            return
        workers = self.settings.get("http", {}).get("max_workers", 6)
        remaining = {p.row: len(adapters) for p in products}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(self._search, p, a): p for p in products for a in adapters}
            finished = 0
            for fut in as_completed(futures):
                p = futures[fut]
                offers, err = fut.result()
                results[p.row]["offers"].extend(offers)
                if err:
                    results[p.row]["errors"].append(err)
                remaining[p.row] -= 1
                if remaining[p.row] == 0:
                    finished += 1
                    if progress:
                        progress(finished, len(products), p)

    def evaluate(self, product: Product, bucket: dict[str, Any]) -> Evaluation:
        """Score one product; an unexpected problem marks that product ERROR instead of stopping the run."""
        try:
            return self._evaluate(product, bucket)
        except Exception as exc:
            log.exception("scoring failed for %s", product.label)
            return Evaluation(product=product, local=Market(scope="local"), global_=Market(scope="global"),
                              offers=bucket.get("offers", []), traffic=[], verdict="ERROR",
                              errors=bucket.get("errors", []) + [f"scoring failed: {exc}"])

    def _evaluate(self, product: Product, bucket: dict[str, Any]) -> Evaluation:
        offers: list[Offer] = bucket["offers"]
        for o in offers:
            o.price_eur_net = to_eur_net(o, site_cfg_for(o, self.site_cfgs), self.settings)
        matched = [o for o in offers if o.match_score >= self.threshold]
        drop_outliers(matched, self.settings["scoring"].get("outliers", {}))
        local_t, global_t = bucket.get("traffic_local", []), bucket.get("traffic_global", [])
        ev = Evaluation(product=product, local=market("local", matched), global_=market("global", matched),
                        offers=offers, errors=bucket["errors"], traffic=list(local_t) + list(global_t))
        for m, traffic in ((ev.local, local_t), (ev.global_, global_t)):
            ok = [t for t in traffic if t.volume is not None and not t.error]
            # a "Worldwide" figure already includes every country: don't add the country breakdown on top
            worldwide = [t.volume for t in ok if t.database.lower() == "worldwide"]
            volumes = worldwide or [t.volume for t in ok]
            m.searches = max(volumes) if worldwide else (sum(volumes) if volumes else None)
            # 12-month history: the Worldwide figure if there is one, else the sum of the markets
            months: dict[str, int] = {}
            for t in ok:
                if worldwide and t.database.lower() != "worldwide":
                    continue
                for y, mo, v in t.monthly:
                    months[f"{y}-{mo:02d}"] = months.get(f"{y}-{mo:02d}", 0) + v
            m.monthly = dict(sorted(months.items())[-12:])
        evaluate(ev, self.settings)
        return ev


def name_from_listings(products: list[Product], results: dict[int, dict[str, Any]]) -> None:
    """Products the supplier file didn't name (and our files didn't know): take the title of a listing that
    matched by EAN. Latin-script titles are preferred, and Cyrillic words dropped, so the name also works
    as a Google search keyword ("Вътрешен диск SAMSUNG 990 PRO 1 TB" -> "SAMSUNG 990 PRO 1 TB")."""
    for p in products:
        if p.name:
            continue
        hits = [o for o in results[p.row]["offers"] if o.match_reason == "EAN match" and o.title]
        if not hits:
            continue
        latin = lambda t: " ".join(w for w in t.split() if not re.search(r"[\u0400-\u04FF]", w))  # noqa: E731
        best = max(hits, key=lambda o: (o.scope != "local", len(latin(o.title))))
        p.name, p.name_source = (latin(best.title) or best.title).strip(" ,-"), f"shop listing ({best.site})"


def drop_outliers(matched: list[Offer], cfg: dict[str, Any]) -> None:
    """Ignore prices far from the median of all matched listings (refurbished, wrong pack size, typos).

    The listing still counts for availability; only its price is not used.
    """
    priced = [o for o in matched if o.price_eur_net]
    if len(priced) < cfg.get("min_offers", 3):
        return
    mid = statistics.median(o.price_eur_net for o in priced)
    low, high = cfg.get("low", 0.6) * mid, cfg.get("high", 1.8) * mid
    for o in priced:
        if not low <= o.price_eur_net <= high:
            o.match_reason += f"; price outlier vs median EUR {mid:.2f} net - not used"
            o.price_eur_net = None


def traffic_errors(evaluations: list[Evaluation]) -> list[Traffic]:
    return [t for ev in evaluations for t in ev.traffic if t.error]
