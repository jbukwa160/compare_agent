"""DataForSEO, tested against the documented response format (no API key needed)."""
import json

from compare_agent.config import ROOT, load_settings
from compare_agent.models import Product
from compare_agent.runner import Runner
from compare_agent.traffic import build_source
from compare_agent.traffic.dataforseo import _all_ok

SETTINGS = load_settings()


def test_sites_needing_a_key_are_skipped_without_it(monkeypatch):
    monkeypatch.delenv("SOME_SHOP_API_KEY", raising=False)
    site = {"name": "Keyed shop", "type": "html", "scope": "global", "api_key_env": "SOME_SHOP_API_KEY",
            "search_url": "https://example.com/?q={query}"}
    runner = Runner(SETTINGS, [site], ROOT / ".cache")
    assert runner.adapters == [] and runner.skipped_sites == [("Keyed shop", "SOME_SHOP_API_KEY")]


def test_dataforseo_matches_normalised_keywords_and_worldwide(monkeypatch):
    monkeypatch.setenv("DFS_L", "u")
    monkeypatch.setenv("DFS_P", "p")
    sent = []

    class Http:
        def request(self, method, url, **kw):
            sent.append(kw["json_body"][0])
            return 200, json.dumps({"status_code": 20000, "tasks": [{"status_code": 20000, "result": [
                {"keyword": "fritz box 5690 wi fi 7", "search_volume": 320}]}]})

    src = build_source({"name": "DataForSEO", "type": "dataforseo", "login_env": "DFS_L", "password_env": "DFS_P",
                        "global_markets": ["Worldwide"]}, Http())
    out = src.lookup_many([Product(row=8, name="FRITZ!Box 5690 WLAN Mesh Router Wi-Fi 7")])
    assert out[8][1][0].volume == 320
    assert "location_name" not in sent[0]  # worldwide = no location


def test_dataforseo_error_answers_are_not_cached():
    assert _all_ok(json.dumps({"status_code": 20000, "tasks": [{"status_code": 20000}]}))
    assert not _all_ok(json.dumps({"status_code": 20000, "tasks": [{"status_code": 40200}]}))
    assert not _all_ok(json.dumps({"status_code": 40100}))


def test_dataforseo_isolates_a_rejected_keyword(monkeypatch):
    monkeypatch.setenv("DFS_L", "u")
    monkeypatch.setenv("DFS_P", "p")

    class Http:
        calls = 0

        def request(self, method, url, **kw):
            Http.calls += 1
            kws = kw["json_body"][0]["keywords"]
            if any("bad" in k for k in kws):
                return 200, json.dumps({"status_code": 20000, "tasks": [
                    {"status_code": 40501, "status_message": "Invalid Field: 'keywords'."}]})
            return 200, json.dumps({"status_code": 20000, "tasks": [{"status_code": 20000, "result": [
                {"keyword": k, "search_volume": 100} for k in kws]}]})

    src = build_source({"name": "DataForSEO", "type": "dataforseo", "login_env": "DFS_L", "password_env": "DFS_P",
                        "local_markets": ["Bulgaria"]}, Http())
    products = [Product(row=i, name=n) for i, n in enumerate(["GoPro HERO12", "bad thing", "Marshall Major V",
                                                               "Canon EOS R7"], start=2)]
    out = src.lookup_many(products)
    assert out[2][0][0].volume == 100 and out[4][0][0].volume == 100 and out[5][0][0].volume == 100
    assert out[3][0][0].error and "rejected" in out[3][0][0].error


def test_wrong_login_stops_after_the_first_market(monkeypatch):
    monkeypatch.setenv("DFS_L", "u")
    monkeypatch.setenv("DFS_P", "p")

    class Http:
        calls = 0

        def request(self, method, url, **kw):
            Http.calls += 1
            return 401, json.dumps({"status_code": 40100, "status_message": "You are not authorized"})

    src = build_source({"name": "DataForSEO", "type": "dataforseo", "login_env": "DFS_L", "password_env": "DFS_P",
                        "local_markets": ["Bulgaria"], "global_markets": ["Worldwide", "United States"]}, Http())
    out = src.lookup_many([Product(row=2, name="GoPro HERO12 Black")])
    assert Http.calls == 1
    assert all("login" in t.error for t in out[2][0] + out[2][1])


def test_google_shopping_offers_are_read_and_matched(monkeypatch):
    from compare_agent.sites.base import build_adapter
    monkeypatch.setenv("DATAFORSEO_LOGIN", "u")
    monkeypatch.setenv("DATAFORSEO_PASSWORD", "p")
    import compare_agent.sites.shopping as shopping
    monkeypatch.setattr(shopping.time, "sleep", lambda s: None)

    class Http:
        def request(self, method, url, **kw):
            if method == "POST":
                return 200, json.dumps({"status_code": 20000, "tasks": [
                    {"status_code": 20100, "id": f"t{i}"} for i, _ in enumerate(kw["json_body"])]})
            return 200, json.dumps({"status_code": 20000, "tasks": [{"status_code": 20000, "result": [{"items": [
                {"type": "google_shopping_serp", "title": "Marshall Major V Bluetooth Kopfhörer schwarz",
                 "price": 79.99, "currency": "EUR", "seller": "MediaMarkt", "product_id": "123"},
                {"type": "google_shopping_serp", "title": "Marshall Major V casque blanc",
                 "price": 69.99, "currency": "EUR", "seller": "Fnac", "product_id": "456"},
                {"type": "google_shopping_serp", "title": "Marshall Major V Bluetooth Kopfhörer schwarz",
                 "price": 49.0, "currency": "EUR", "seller": "eBay - privat123", "product_id": "789"}]}]}]})

    cfg = {"name": "Google Shopping", "type": "dataforseo_shopping", "scope": "global",
           "countries": [{"location": "Germany", "language": "German", "country": "DE"}],
           "exclude_sellers": ["eBay"]}
    p = Product(row=15, name="MARSHALL MAJOR V Wireless On-Ear Headphones Black", brand="Marshall")
    found, errors = build_adapter(cfg, Http()).search_many([p], 0.75)
    assert not errors
    by_site = {o.site: o for o in found[15]}
    assert by_site["MediaMarkt (DE)"].match_score >= 0.75 and by_site["MediaMarkt (DE)"].country == "DE"
    assert by_site["Fnac (DE)"].match_score == 0.0   # "blanc" = white, not black
    assert not any("eBay" in site for site in by_site)  # second-hand / private sellers are ignored
