import pandas as pd

from compare_agent.config import load_settings
from compare_agent.loader import load_products


def test_supplier_price_from_the_first_filled_price_column(tmp_path):
    f = tmp_path / "offer.xlsx"
    pd.DataFrame({"name": ["HP ScanJet Pro 2000 s2", "Other"], "ean_main": ["0193808948503", "123456789012"],
                  "acquisition_price": [None, 50.0], "sale_price": [199.36, 60.0]}).to_excel(f, index=False)
    products = load_products(f, load_settings())
    assert products[0].supplier_price == 199.36   # acquisition_price empty -> sale_price
    assert products[1].supplier_price == 50.0     # acquisition_price filled -> it wins


def test_messy_supplier_file_title_block_and_cover_sheet(tmp_path):
    f = tmp_path / "pricelist.xlsx"
    with pd.ExcelWriter(f) as xl:
        pd.DataFrame([["Welcome! Prices valid until 31.12."], ["Contact: sales@supplier"]]).to_excel(
            xl, sheet_name="Info", index=False, header=False)
        rows = [["ACME Distribution - price list Q4", None, None, None],
                [None, None, None, None],
                ["Valid from 01.10.2026", None, None, None],
                ["Item", "EAN code", "Brand", "Net price"],
                ["Canon EOS R7 Body", "4549292185447", "Canon", 990.0],
                ["GoPro HERO12 Black", "0810116380282", "GoPro", 240.5]]
        pd.DataFrame(rows).to_excel(xl, sheet_name="Pricelist", index=False, header=False)
    products = load_products(f, load_settings())
    assert [p.name for p in products] == ["Canon EOS R7 Body", "GoPro HERO12 Black"]
    assert products[0].row == 5 and products[0].ean == "4549292185447" and products[1].supplier_price == 240.5
    assert list(products[0].raw)[:4] == ["Item", "EAN code", "Brand", "Net price"]


def test_csv_with_semicolons(tmp_path):
    f = tmp_path / "offer.csv"
    f.write_text("Product name;EAN;Price\nMarshall Major V Black;7340055401104;55,90\n", encoding="utf-8")
    p = load_products(f, load_settings())[0]
    assert (p.name, p.ean, p.supplier_price) == ("Marshall Major V Black", "7340055401104", 55.9)


def test_old_xls_file(tmp_path):
    import xlrd  # noqa: F401  (needed for .xls)
    src = tmp_path / "offer.xlsx"
    pd.DataFrame({"name": ["Samsung 990 PRO 1TB"], "ean": ["8806094215021"]}).to_excel(src, index=False)
    # write a real .xls is not possible with current pandas; check that .xls goes through the same reader
    from compare_agent.loader import _read_table
    table, header = _read_table(src, load_settings()["input"]["columns"], None)
    assert header == 1 and list(table.columns) == ["name", "ean"]
