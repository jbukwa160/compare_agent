import pytest

from compare_agent.matching import score_match
from compare_agent.models import Offer, Product

THRESHOLD = 0.75

CASES = [
    # product name, brand, listing title, should match?
    ("Samsung 990 PRO SSD NVMe 1TB", "Samsung", "SSD Samsung 990 PRO, 1 TB, NVMe M.2 PCIe 4.0 MZ-V9P1T0BW", True),
    ("Samsung 990 PRO SSD NVMe 1TB", "Samsung", "SSD Samsung 990 PRO 2TB NVMe", False),
    ("MARSHALL MAJOR V Pitch Black", "Marshall", "Слушалки Marshall Major V, черни", True),
    ("MARSHALL MAJOR V Pitch Black", "Marshall", "Слушалки Marshall Major IV, черни", False),
    ("Marshall Major IV Wireless Headphones Brown", "Marshall", "Marshall Major V Brown", False),
    ("Marshall Major V On-Ear Wireless Cream", "Marshall", "Marshall Major V Black", False),
    ("Canon EOS R6 Mark II", "Canon", "Canon EOS R6 Mark II + RF 24-105mm f/4-7.1", False),
    ("Canon EOS R6 Mark II", "Canon", "Фотоапарат Canon EOS R6 Mark II тяло", True),
    ("Canon EOS R6 Mark II", "Canon", "Canon EOS R6 Mark III Body", False),
    ("Canon EOS R6 Mark III + RF 24-105 mm f/4L IS USM", "Canon", "Canon EOS R6 Mark III + RF 24-105mm F4 L IS USM kit", True),
    ("Canon EOS R10 + RF-S 18-45mm", "Canon", "Canon EOS R10 + RF-S 18-150mm", False),
    ("Kodak Charmera 1987 (MOQ 528)", "Kodak", "Kodak Charmera 1987 keychain camera", True),
    ("Kingston Canvas Select Plus microSD 128GB SDCS3/128GB", "Kingston", "Карта памет Kingston Canvas Select Plus 128GB microSDXC", True),
    ("Kingston Canvas Select Plus microSD 128GB SDCS3/128GB", "Kingston", "Карта памет Kingston Canvas Select Plus 64GB microSDXC", False),
    ('GOODRAM CX400 Gen.2 1TB SATA III 2,5" SSDPR-CX400-01T-G2', "Goodram", 'SSD Goodram CX400 Gen.2 1TB 2.5" SATA III', True),
    ('GOODRAM CX400 Gen.2 1TB SATA III 2,5" SSDPR-CX400-01T-G2', "Goodram", 'SSD Goodram CX400 Gen.2 512GB 2.5" SATA III', False),
    ("GoPro HERO12 Black", "GoPro", "Екшън камера GoPro HERO 12 Black", True),
    ("GoPro HERO12 Black", "GoPro", "GoPro HERO13 Black", False),
    ("HP ScanJet Pro 2000 s2 6FW06A", None, "Скенер HP ScanJet Pro 2000 s2", True),
    ("HP ScanJet Pro 2000 s2 6FW06A", None, "Скенер HP ScanJet Pro 3000 s4", False),
]


@pytest.mark.parametrize("name,brand,title,expected", CASES)
def test_title_matching(name, brand, title, expected):
    score, reason = score_match(Product(row=0, name=name, brand=brand), Offer("x", "local", title, ""))
    assert (score >= THRESHOLD) == expected, f"{score} {reason}"


def test_ean_overrides_title():
    p = Product(row=0, name="Samsung 990 PRO 1TB", ean="8806094215021")
    assert score_match(p, Offer("x", "local", "anything", "", ean="8806094215021"))[0] == 1.0
    assert score_match(p, Offer("x", "local", "Samsung 990 PRO 2TB", "", ean="8806094215038"))[0] == 0.0


def test_region_ean_with_strong_title_still_matches():
    p = Product(row=0, name="Canon EOS R10 + RF-S 18-45mm", brand="Canon", ean="4549292189773")
    o = Offer("x", "local", "Безогледален фотоапарат CANON EOS R10+RF-S 18-45 IS STM", "", ean="8714574672434")
    assert score_match(p, o)[0] >= THRESHOLD


def test_other_colour_with_other_ean_rejected():
    p = Product(row=0, name="Marshall Major V On-Ear Wireless Cream", brand="Marshall", ean="7340055401111")
    o = Offer("x", "local", "Стерео слушалки MARSHALL MAJOR V BT BLACK", "", ean="7340055401104")
    assert score_match(p, o)[0] == 0.0


@pytest.mark.parametrize("name,title,expected", [
    ("Samsung 990 PRO SSD NVMe 1TB", "Solid-State Drive (SSD) Samsung 990 EVO Plus PCIe 4.0 NVMe 1TB", False),
    ("MARSHALL MAJOR V Wireless On-Ear Headphones Black", "Marshall Major V midnight blue (1008144) Слушалки", False),
    ("MARSHALL MAJOR V Wireless On-Ear Headphones Black", "Marshall Major V black (1006832) Слушалки", True),
    ("GoPro HERO12 Black", "GoPro HERO12 Black Creator Edition", False),
    ("Kingston Canvas Select Plus microSD 64GB SDCS3/64GB", "Kingston Canvas Select Plus 64GB microSDXC", True),
])
def test_variant_words(name, title, expected):
    score, reason = score_match(Product(row=0, name=name), Offer("x", "local", title, ""))
    assert (score >= THRESHOLD) == expected, f"{score} {reason}"


@pytest.mark.parametrize("name,brand,title,expected", [
    ("GoPro MAX Action Camera", "GoPro",
     "Залепващи подложки, залепващи стойки, стойка за каска 3M, извити и прави, за GoPro Max", False),
    ("GoPro MAX Action Camera", "GoPro", "Екшън камера GoPro - Max 360 2025, 5.6K, 16.6MPx, Wi-Fi, черна", True),
    ("MARSHALL MAJOR IV Wireless Over-Ear Headphones Black", "Marshall",
     "Слушалки In-Ear Marshall Minor IV, True Wireless, Bluetooth, 30 часа", False),
    ("MARSHALL MAJOR IV Wireless Over-Ear Headphones Black", "Marshall", "Marshall Major IV black (1005773) Слушалки", True),
    ("MARSHALL Major V Wireless Headphones Blue", "Marshall", "Dekoni Audio Marshall Major V Coussinets pour Marshall", False),
])
def test_accessories_and_model_words(name, brand, title, expected):
    score, reason = score_match(Product(row=0, name=name, brand=brand), Offer("x", "local", title, ""))
    assert (score >= THRESHOLD) == expected, f"{score} {reason}"


@pytest.mark.parametrize("title,expected", [
    ("Слушалки JBL T110, черни, с вграден микрофон, 3.5mm жак", True),
    ("Слушалки JBL Tune 110, черни", True),
    ("Слушалки JBL T110 бели", False),               # other colour
    ("Слушалки JBL Tune 120TWS, черни", False),      # other model
])
def test_alias_model_numbers_and_soft_specs(title, expected):
    p = Product(row=0, name="JBL Tune 110 T110 Jack 3.5mm Black", brand="Jbl")
    score, reason = score_match(p, Offer("x", "local", title, ""))
    assert (score >= THRESHOLD) == expected, f"{score} {reason}"


@pytest.mark.parametrize("name,brand,title,expected", [
    ("GoPro MAX Action Camera", "GoPro", "аксесоар GoPro Max Lens Mod 2.0", False),
    ("GoPro MAX Action Camera", "GoPro", "GoPro MAX 2 ( CHDHZ-311-RW ) black", False),
    ("GoPro MAX Action Camera", "GoPro", "GoPro MAX 360 Action Camera", True),
    ("Kodak FunSaver usa e getta 27 foto", "Kodak", "Фотоапарат с филм KODAK Water & Sport 27X1, син", False),
    ("Kodak FunSaver usa e getta 27 foto", "Kodak", "Kodak FunSaver 27 exp disposable camera", True),
    ("Samsung 990 PRO SSD NVMe 2TB", "Samsung", "SSD Samsung 990 PRO 2 TB NVMe", True),
])
def test_generation_numbers_and_weak_model_numbers(name, brand, title, expected):
    score, reason = score_match(Product(row=0, name=name, brand=brand), Offer("x", "local", title, ""))
    assert (score >= THRESHOLD) == expected, f"{score} {reason}"


@pytest.mark.parametrize("name,title,expected", [
    ("Samsung 990 PRO SSD NVMe 1TB", "Твърд диск, Samsung SSD 990 PRO 1TB PCIe 4.0 NVMe 2.0 M.2 V-NAND MLC", True),
    ("Kingston Canvas Select Plus microSD 128GB SDCS3/128GB",
     "Kingston Technology Canvas Select Plus microSDXC карта 128GB + SD адаптер", True),
    ("Kingston Canvas Select Plus microSD 128GB SDCS3/128GB", "Памет KINGSTON 128G Canvas Select Plus SDCS3", True),
    ("Canon EOS R7 Body", "Canon EOS R7 Mark II Body", False),
])
def test_vnand_adapter_and_short_units(name, title, expected):
    score, reason = score_match(Product(row=0, name=name), Offer("x", "local", title, ""))
    assert (score >= THRESHOLD) == expected, f"{score} {reason}"


def test_machine_readable_attribute_price():
    from compare_agent.sites.generic import _price
    assert _price("238.188", "price@:price") == 238.19
    assert _price("1.299 €", ".price") == 1299


@pytest.mark.parametrize("name,title,expected", [
    ("MARSHALL MAJOR V Wireless On-Ear Headphones Black", "Безжични слушалки Marshall Major V, тъмносин", False),
    ("MARSHALL MAJOR V Pitch Black", "Безжични слушалки Marshall Major V, бежов - 1006833", False),
    ("Marshall Major V On-Ear Wireless Cream", "Безжични слушалки Marshall Major V, бежов - 1006833", True),
    ("MARSHALL Major V Wireless Headphones Blue", "Безжични слушалки Marshall Major V, тъмносин - 1008144", True),
    ("MARSHALL MAJOR V Wireless On-Ear Headphones Black", "Безжични слушалки Marshall Major V, черен", True),
])
def test_bulgarian_colour_forms(name, title, expected):
    score, reason = score_match(Product(row=0, name=name, brand="Marshall"), Offer("x", "local", title, ""))
    assert (score >= THRESHOLD) == expected, f"{score} {reason}"


@pytest.mark.parametrize("title,expected", [
    ("GoPro Schutzgehäuse (HERO13 Black/HERO12 Black/HERO11 Black)", False),
    ("GoPro Battery Grip HERO12 HERO11 HERO10", False),
    ("GoPro HERO12 Black (CHDHX-121-RW) Спортна екшън камера", True),
])
def test_compatibility_lists_are_accessories(title, expected):
    score, reason = score_match(Product(row=0, name="GoPro HERO12 Black", brand="GoPro"), Offer("x", "local", title, ""))
    assert (score >= THRESHOLD) == expected, f"{score} {reason}"


def test_pazaruvaj_product_page_hit_from_ean_search_is_an_ean_match():
    from compare_agent.http import HttpClient  # noqa: F401
    from compare_agent.sites.base import build_adapter

    page = ('<html><h1>Marshall Major V black (1006832) Слушалки</h1>'
            '<span itemprop="lowPrice" content="176.02">от 90,00 €</span>'
            '<span itemprop="offerCount" content="14">14 оферти</span></html>')

    class Http:
        def fetch(self, method, url, **kw):
            return 200, page, "https://www.pazaruvaj.com/slushalki-c3109/marshall/major-v-black-1006832-p1357094674/"

    cfg = {"name": "Pazaruvaj", "type": "html", "scope": "local", "country": "BG", "currency": "EUR",
           "search_url": "https://www.pazaruvaj.com/CategorySearch.php?st={query}", "assume_in_stock": True,
           "results": {"item": "div.product-box", "title": ".name h2 a", "url": ".name h2 a@href"},
           "product_on_redirect": {"title": "h1", "price": "[itemprop=lowPrice]",
                                   "shop_count": "[itemprop=offerCount]@content", "trust_query_ean": True}}
    o = build_adapter(cfg, Http()).search_raw("7340055401104")[0]
    assert (o.ean, o.price, o.shop_count, o.in_stock) == ("7340055401104", 90.0, 14, True)


def test_broad_queries():
    from compare_agent.matching import broad_query
    cases = {"JBL Tune 110 T110 Jack 3.5mm Black": "jbl tune t110",
             'GOODRAM CX400 Gen.2 1TB SATA III 2,5" SSDPR-CX400-01T-G2': "goodram cx400",
             "Samsung 990 PRO SSD NVMe 1TB": "samsung 990 pro", "GoPro MAX Action Camera": "gopro max",
             "Kodak Charmera Millennium Edition (MOQ 528)": "kodak charmera"}
    for name, q in cases.items():
        assert broad_query(Product(row=0, name=name)) == q


def test_part_number_identifies_the_item_even_without_gen():
    p = Product(row=0, name='GOODRAM CX400 Gen.2 1TB SATA III 2,5" SSDPR-CX400-01T-G2', brand="Goodram")
    assert score_match(p, Offer("x", "local", "GOODRAM CX400 2.5 1TB SATA3 (SSDPR-CX400-01T-G2)", ""))[0] >= THRESHOLD
    # a different capacity's part number is not this item
    assert score_match(p, Offer("x", "local", "GOODRAM CX400 2.5 512GB SATA3 (SSDPR-CX400-512-G2)", ""))[0] < THRESHOLD


def test_sets_and_extra_kit_items_are_not_the_product():
    p = Product(row=0, name="Kodak Charmera Millennium Edition (MOQ 528)", brand="Kodak")
    assert score_match(p, Offer("x", "local", "Kodak Charmera Millennium (RK0603SET) Цифрови фотоапарати", ""))[0] < THRESHOLD
    k = Product(row=0, name="Canon EOS R10 + RF-S 18-150mm IS STM", brand="Canon")
    assert score_match(k, Offer("x", "local", "Canon EOS R10 + RF-S 18-150mm f/3.5-6.3 IS STM + RF 55-210", ""))[0] < THRESHOLD
    assert score_match(k, Offer("x", "local", "Canon EOS R10 + RF-S 18-150mm f/3.5-6.3 IS STM", ""))[0] >= THRESHOLD


@pytest.mark.parametrize("title,expected", [
    ("Samsung 990 PRO NVMe M.2 1 TB SSD – PCIe 4.0 – Top Zustand", False),
    ("Casque Marshall Major V Bluetooth pliable reconditionné", False),
    ("Casque sans fil Marshall Major V, état neuf", False),
    ("Samsung 990 PRO 1TB used good condition", False),
    ("Samsung 990 PRO M.2 NVMe 1TB", True),
])
def test_used_and_refurbished_offers_are_not_the_product(title, expected):
    name = "Samsung 990 PRO SSD NVMe 1TB" if "Samsung" in title else "MARSHALL MAJOR V Wireless On-Ear Headphones"
    score, reason = score_match(Product(row=0, name=name), Offer("x", "global", title, ""))
    assert (score >= THRESHOLD) == expected, f"{score} {reason}"
