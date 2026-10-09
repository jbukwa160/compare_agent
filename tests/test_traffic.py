import json
import os

from compare_agent.config import ROOT, load_settings
from compare_agent.models import Product, Traffic
from compare_agent.runner import Runner
from compare_agent.traffic import TrafficSource, build_source
from compare_agent.traffic.base import register

SETTINGS = load_settings()


@register("fake")
class FakeSource(TrafficSource):
    calls = []

    def volumes(self, keywords, market):
        FakeSource.calls.append((market, tuple(keywords)))
        return {k: Traffic(database=market, keyword=k, volume=100) for k in keywords}


def test_traffic_reaches_the_sales_score():
    cfg = {"name": "Fake", "type": "fake", "local_markets": ["bg"], "global_markets": ["us", "de"]}
    runner = Runner(SETTINGS, [], ROOT / ".cache", traffic_sources=[cfg])
    products = [Product(row=2, name="Samsung 990 PRO SSD NVMe 1TB"), Product(row=3, name="Samsung 990 PRO 1TB")]
    evs = runner.run(products)
    assert evs[0].local.searches == 100 and evs[0].global_.searches == 200   # us + de
    assert evs[0].sales_score is not None
    # both products share their two phrasings ("samsung 990 pro 1tb", "samsung 990 pro") -> sent once
    assert all(sorted(kws) == ["samsung 990 pro", "samsung 990 pro 1tb"] for _, kws in FakeSource.calls)


class FakeHttp:
    def __init__(self, payload):
        self.payload, self.requests = payload, []

    def request(self, method, url, **kw):
        self.requests.append(kw["json_body"])
        return 200, json.dumps(self.payload)


def test_dataforseo_parses_response_and_batches_one_request_per_market():
    os.environ["DFS_L"], os.environ["DFS_P"] = "user", "pass"
    payload = {"status_code": 20000, "tasks": [{"status_code": 20000, "result": [
        {"keyword": "samsung 990 pro 1tb", "search_volume": 880, "cpc": 0.4, "competition_index": 100},
        {"keyword": "marshall major v", "search_volume": None}]}]}
    http = FakeHttp(payload)
    src = build_source({"name": "DataForSEO", "type": "dataforseo", "login_env": "DFS_L", "password_env": "DFS_P",
                        "local_markets": ["Bulgaria"], "global_markets": []}, http)
    assert src.missing_credentials is None
    out = src.lookup_many([Product(row=2, name="Samsung 990 PRO SSD NVMe 1TB"),
                           Product(row=3, name="MARSHALL MAJOR V Wireless On-Ear Headphones Black")])
    assert out[2][0][0].volume == 880 and out[3][0][0].volume == 0
    assert len(http.requests) == 1 and http.requests[0][0]["location_name"] == "Bulgaria"


def test_missing_credentials_are_reported():
    src = build_source({"name": "S", "type": "semrush", "api_key_env": "NO_SUCH_VAR_123"}, None)
    assert src.missing_credentials == "NO_SUCH_VAR_123"


def test_worldwide_total_is_not_added_to_country_breakdown():
    @register("fake_ww")
    class WW(TrafficSource):
        def volumes(self, keywords, market):
            vol = {"Worldwide": 5000, "United States": 2000, "Germany": 800}[market]
            return {k: Traffic(database=market, keyword=k, volume=vol) for k in keywords}

    cfg = {"name": "WW", "type": "fake_ww", "global_markets": ["Worldwide", "United States", "Germany"]}
    ev = Runner(SETTINGS, [], ROOT / ".cache", traffic_sources=[cfg]).run([Product(row=2, name="GoPro HERO12 Black")])[0]
    assert ev.global_.searches == 5000



def test_the_phrasing_people_type_wins_when_the_long_one_has_no_data():
    @register("fake_variants")
    class Variants(TrafficSource):
        def volumes(self, keywords, market):
            data = {"canon eos r6 mark iii": None, "canon r6 mark iii": 320}
            return {k: Traffic(database=market, keyword=k, volume=data.get(k)) for k in keywords}

    cfg = {"name": "V", "type": "fake_variants", "local_markets": ["Bulgaria"]}
    out = build_source(cfg, None).lookup_many([Product(row=2, name="Canon EOS R6 Mark III Body")])
    assert out[2][0][0].keyword == "canon r6 mark iii" and out[2][0][0].volume == 320
