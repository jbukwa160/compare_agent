from compare_agent.config import load_settings
from compare_agent.models import Evaluation, Offer, Product
from compare_agent.scoring import evaluate, market

SETTINGS = load_settings()


def offer(site, net, in_stock, scope="local", shops=None):
    o = Offer(site=site, scope=scope, title="x", url=site, price=net * 1.2, in_stock=in_stock, shop_count=shops)
    o.price_eur_net, o.match_score = net, 1.0
    return o


def run(product, offers):
    ev = Evaluation(product=product, local=market("local", offers), global_=market("global", offers),
                    offers=offers, errors=[], traffic=[])
    evaluate(ev, SETTINGS)
    return ev


def test_sell_price_uses_cheapest_in_stock_bg_competitor():
    ev = run(Product(row=2, name="x", supplier_price=70),
             [offer("A", 100, True), offer("B", 90, False), offer("C", 120, True)])
    assert ev.bg_sell_price == 100          # B is out of stock
    assert ev.margin == 0.3                 # (100 - 70) / 100, no other costs


def test_nobody_in_stock_in_bg_uses_median():
    ev = run(Product(row=2, name="x", supplier_price=70), [offer("A", 100, False), offer("B", 120, False)])
    assert ev.bg_sell_price == 110


def test_scores_are_1_to_10():
    ev = run(Product(row=2, name="x", supplier_price=60),
             [offer("A", 100, True), offer("E", 95, True, scope="global", shops=40)])
    for s in (ev.buy_price_score, ev.sell_price_score, ev.sales_score, ev.final_score):
        assert 1 <= s <= 10
    assert ev.buy_price_score == 10         # 37% below the cheapest EU price
    assert ev.verdict == "BUY"


def test_supplier_dearer_than_bg_market_is_skip():
    ev = run(Product(row=2, name="x", supplier_price=105),
             [offer("A", 100, True), offer("E", 95, True, scope="global")])
    assert ev.sell_price_score == 1 and ev.buy_price_score == 1
    assert ev.verdict == "SKIP"


def test_no_supplier_price_gives_only_sales_score():
    ev = run(Product(row=2, name="x"), [offer("A", 100, True)])
    assert ev.verdict == "NEED PRICE"
    assert ev.sales_score is not None
    assert ev.buy_price_score is None and ev.sell_price_score is None and ev.final_score is None


def test_not_listed_anywhere_is_no_data():
    assert run(Product(row=2, name="x", supplier_price=10), []).verdict == "NO DATA"


def test_not_sold_in_bulgaria_uses_worldwide_price():
    ev = run(Product(row=2, name="x", supplier_price=70),
             [offer("E1", 100, True, scope="global"), offer("E2", 120, True, scope="global")])
    assert ev.sell_price_source.startswith("Worldwide")
    assert ev.bg_sell_price == 110 and ev.sell_price_score is not None


def test_only_out_of_stock_in_bulgaria_uses_worldwide_price():
    ev = run(Product(row=2, name="x", supplier_price=70),
             [offer("BG", 48, False), offer("E1", 100, True, scope="global"), offer("E2", 110, True, scope="global")])
    assert ev.sell_price_source == "Worldwide (out of stock in BG)"
    assert ev.bg_sell_price == 105


def test_sells_label_follows_bulgarian_demand():
    popular = run(Product(row=2, name="x", supplier_price=50), [offer("Pazaruvaj", 100, True, shops=60)])
    popular.local.searches = 900
    niche = run(Product(row=3, name="y", supplier_price=50),
                [offer("A", 100, True), offer("Klarna", 90, True, scope="global", shops=150)])
    niche.local.searches = 5
    from compare_agent.scoring import evaluate as ev_
    ev_(popular, SETTINGS)
    ev_(niche, SETTINGS)
    assert popular.sells == "Sells well"
    assert niche.sells == "Weak"  # 150 shops abroad must not make a niche BG product look popular
