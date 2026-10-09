from compare_agent.config import ROOT, load_settings
from compare_agent.models import Offer, Product
from compare_agent.runner import Runner
from compare_agent.sites.base import SiteAdapter, register

SETTINGS = load_settings()


@register("fake_shop")
class FakeShop(SiteAdapter):
    """Sells only the products listed in cfg['sells']; records every search."""
    searched: list[tuple[str, str]] = []

    def search_raw(self, query):
        FakeShop.searched.append((self.name, query))
        return [Offer(site=self.name, scope=self.scope, title=t, url=f"{self.name}/{t}", price=100, in_stock=True)
                for t in self.cfg["sells"] if t.lower() == query.lower()]


def sites():
    return [{"name": "BG shop", "type": "fake_shop", "scope": "local", "country": "BG",
             "search_by": "name", "sells": ["Marshall Major V"]},
            {"name": "World shop", "type": "fake_shop", "scope": "global", "country": "DE",
             "search_by": "name", "sells": ["Marshall Major V", "FRITZ!Box 5690"]}]


def test_fallback_checks_worldwide_only_for_products_not_sold_in_bulgaria():
    FakeShop.searched.clear()
    products = [Product(row=2, name="Marshall Major V", supplier_price=50),
                Product(row=3, name="FRITZ!Box 5690", supplier_price=150)]
    evs = Runner(SETTINGS, sites(), ROOT / ".cache", use_cache=False).run(products, worldwide="fallback")
    world_queries = [q for site, q in FakeShop.searched if site == "World shop"]
    assert world_queries and all("fritz" in q.lower() for q in world_queries)
    assert evs[0].sell_price_source == "Bulgaria"
    assert evs[1].sell_price_source.startswith("Worldwide")


def test_always_checks_worldwide_for_every_product():
    FakeShop.searched.clear()
    products = [Product(row=2, name="Marshall Major V", supplier_price=50)]
    Runner(SETTINGS, sites(), ROOT / ".cache", use_cache=False).run(products, worldwide="always")
    assert any(site == "World shop" for site, _ in FakeShop.searched)


def test_network_errors_are_retried_and_do_not_trip_the_breaker(monkeypatch):
    import compare_agent.sites.base as base
    monkeypatch.setattr(base, "RETRY_WAITS", (0, 0))

    @register("flaky_shop")
    class Flaky(SiteAdapter):
        calls = 0

        def search_raw(self, query):
            Flaky.calls += 1
            if Flaky.calls <= 2:
                raise RuntimeError("curl: (6) Could not resolve host: shop.bg")
            return [Offer(site=self.name, scope=self.scope, title=query, url="u", price=10, in_stock=True)]

    adapter = base.build_adapter({"name": "Flaky", "type": "flaky_shop", "scope": "local",
                                  "search_by": "name"}, None)
    offers = adapter.search(Product(row=2, name="Marshall Major V"), 0.75)
    assert offers and adapter.failures == 0


def test_blocking_errors_trip_the_breaker():
    import compare_agent.sites.base as base

    @register("blocked_shop")
    class Blocked(SiteAdapter):
        def search_raw(self, query):
            raise RuntimeError("HTTP 511")

    adapter = base.build_adapter({"name": "Blocked", "type": "blocked_shop", "scope": "local",
                                  "search_by": "name", "max_consecutive_failures": 1}, None)
    for _ in range(2):
        try:
            adapter.search(Product(row=2, name="Marshall Major V"), 0.75)
        except RuntimeError as exc:
            last = str(exc)
    assert "skipped" in last


def test_rate_limited_site_waits_and_retries(monkeypatch):
    import compare_agent.sites.base as base
    monkeypatch.setattr(base, "RATE_LIMIT_WAITS", (0, 0))

    @register("ratelimited_shop")
    class RateLimited(SiteAdapter):
        calls = 0

        def search_raw(self, query):
            RateLimited.calls += 1
            if RateLimited.calls == 1:
                raise RuntimeError("HTTP 403")
            return [Offer(site=self.name, scope=self.scope, title=query, url="u", price=10, in_stock=True)]

    adapter = base.build_adapter({"name": "RL", "type": "ratelimited_shop", "scope": "local", "search_by": "name",
                                  "retry_http": [403, 429]}, None)
    assert adapter.search(Product(row=2, name="Marshall Major V"), 0.75)


def test_product_without_name_is_searched_by_ean_and_named_after_the_listing():
    @register("ean_shop")
    class EanShop(SiteAdapter):
        queries_seen = []

        def search_raw(self, query):
            EanShop.queries_seen.append(query)
            if query == "4549292185447":
                return [Offer(site=self.name, scope=self.scope, title="Фотоапарат Canon EOS R7 Body", url="u",
                              price=1349, in_stock=True, ean="4549292185447")]
            return []

    site = {"name": "EAN shop", "type": "ean_shop", "scope": "local", "country": "BG"}
    p = Product(row=2, name="", ean="4549292185447", supplier_price=900)
    ev = Runner(SETTINGS, [site], ROOT / ".cache", use_cache=False).run([p])[0]
    assert EanShop.queries_seen == ["4549292185447"]        # no name -> EAN search only
    assert p.name == "Canon EOS R7 Body" and "EAN shop" in p.name_source
    assert ev.local.min_in_stock is not None
