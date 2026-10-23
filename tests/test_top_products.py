import pytest

from top_products import config
from top_products.dataforseo import BudgetExceeded, Labs
from top_products.pages import norm_gtin, valid_gtin
from top_products.scoring import score, vendor_table

SETTINGS = config.settings()
CFG = SETTINGS["score"]


def test_small_profit_scores_1_whatever_the_traffic():
    # 0733739012890: vendor 20.22 + 20% = 24.26, cheapest Bulgarian shop 25.86 -> 1.60 profit per unit
    assert score(25.86 - 24.26, (25.86 - 24.26) / 25.86, 1282, CFG) == 1


def test_points_for_profit_margin_and_traffic():
    assert score(60, 0.35, 6000, CFG) == 10
    assert score(153, 0.19, 411, CFG) == 6          # 4 (profit) + 1 (margin) + 1 (traffic)


def test_thin_margin_caps_the_score():
    assert score(88.2, 0.088, 3790, CFG) == 3       # 4 + 0 + 2 = 6, but a 9% margin is capped at 3


def _row(store, price, url, etv=100):
    return {"store": store, "price_min": price, "url": url, "etv": etv, "name": "x"}


def test_vendor_table_keeps_only_what_is_cheaper_at_the_vendor():
    items = [{"vendor": "A", "ean": "1", "name": "P1", "price": 20.22, "currency": "EUR", "stock": 5},
             {"vendor": "B", "ean": "1", "name": "P1", "price": 22.00, "currency": "EUR", "stock": 5},
             {"vendor": "A", "ean": "2", "name": "P2", "price": 100.0, "currency": "EUR", "stock": 5},
             {"vendor": "A", "ean": "3", "name": "P3", "price": 10.0, "currency": "EUR", "stock": 5}]
    matches = [{"ean": "1", "how": "баркод", "row": _row("iHerb", 25.86, "u1")},
               {"ean": "1", "how": "баркод", "row": _row("BB Shop", 32.0, "u2")},
               {"ean": "2", "how": "баркод", "row": _row("Shop", 110.0, "u3")},     # 120 with VAT: cheaper in BG
               {"ean": "3", "how": "баркод", "row": _row("Shop", None, "u4")}]      # no Bulgarian price
    table, left_out = vendor_table(matches, items, SETTINGS)
    assert [r["ean"] for r in table] == ["1"]
    row = table[0]
    assert (row["vendor"], row["vendor_price_vat"], row["bg_shop"], row["profit"]) == ("A", 24.26, "iHerb", 1.6)
    assert row["traffic"] == 200 and row["score"] == 1
    assert left_out == {"cheaper_in_bg": 1, "no_bg_price": 1}


def test_barcodes():
    assert valid_gtin("0733739012890") and not valid_gtin("0733739012891")
    assert norm_gtin("733739012890") == "0733739012890"       # UPC-12 written as EAN-13
    assert norm_gtin(5213001490380.0) == "5213001490380"
    assert norm_gtin("12345") == ""


def test_budget_refuses_a_paid_call(tmp_path):
    with pytest.raises(BudgetExceeded):
        Labs(tmp_path, budget=0.0).call("dataforseo_labs/google/relevant_pages/live", {"target": "x.bg", "limit": 300})


class FakeDrive:
    """Google Drive API double: a folder tree, two result pages, one native Google Sheet."""
    def __init__(self, tree):
        self.tree, self.downloads = tree, []

    def get(self, url, params):
        from types import SimpleNamespace
        if url.endswith("/files"):
            fid = params["q"].split("'")[1]
            files = self.tree.get(fid, [])
            page = 1 if params.get("pageToken") else 0
            data = {"files": files[:1]} if page == 0 and len(files) > 1 else {"files": files[1:] if page else files}
            if page == 0 and len(files) > 1:
                data["nextPageToken"] = "p2"
            return SimpleNamespace(json=lambda: data, raise_for_status=lambda: None)
        self.downloads.append(url)
        return SimpleNamespace(content=b"xlsx:" + url.encode(), raise_for_status=lambda: None)


def test_drive_sync_downloads_only_what_changed(tmp_path):
    from top_products import drive
    tree = {"root": [{"id": "v1", "name": "Wolfsohn", "mimeType": drive.FOLDER, "modifiedTime": "t"},
                     {"id": "doc", "name": "notes.docx", "mimeType": "application/msword", "modifiedTime": "t"}],
            "v1": [{"id": "a", "name": "supplier_products.xlsx", "mimeType": drive.XLSX, "modifiedTime": "1"},
                   {"id": "b", "name": "offer: 20/10", "mimeType": drive.SHEET, "modifiedTime": "1"}]}
    http = FakeDrive(tree)
    out = drive.sync(http, "root", tmp_path)
    assert (out / "Wolfsohn" / "supplier_products.xlsx").exists()
    assert (out / "Wolfsohn" / "offer_ 20_10.xlsx").exists()          # exported, name made safe for Windows
    assert not list(out.rglob("*.docx")) and len(http.downloads) == 2
    tree["v1"] = tree["v1"][:1]                                        # the offer was deleted on Drive
    drive.sync(http, "root", tmp_path)
    assert len(http.downloads) == 2                                    # nothing changed: nothing downloaded
    assert not (out / "Wolfsohn" / "offer_ 20_10.xlsx").exists()


def test_finds_the_drive_for_desktop_folder(tmp_path, monkeypatch):
    from pathlib import Path
    from top_products import vendors
    target = tmp_path / "Google Drive" / "Shared drives" / "S" / "EU vendors"
    target.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert vendors._drive_for_desktop("Shared drives/S/EU vendors") == target
    assert vendors._drive_for_desktop("Shared drives/S/EU vendor") == target      # as typed without the "s"
    assert vendors._drive_for_desktop(str(target)) == target
    assert vendors._drive_for_desktop("Shared drives/S/Other") is None


def test_every_vendor_folder_is_reported(tmp_path):
    from openpyxl import Workbook
    from top_products import vendors

    def sheet(path, rows):
        wb = Workbook()
        for r in rows:
            wb.active.append(r)
        wb.save(path)
    (tmp_path / "A").mkdir()
    (tmp_path / "B").mkdir()
    sheet(tmp_path / "A" / "supplier_products.xlsx", [["supplier_sku", "price", "supplier_ean", "currency", "stock"],
                                                      ["x", 20.22, "733739012890", "EUR", 5], ["y", 0, "5213001490380", "EUR", 1]])
    sheet(tmp_path / "A" / "products.xlsx", [["ean_main", "name", "brand"], ["0733739012890", "NOW Magnesium", "NOW"]])
    sheet(tmp_path / "B" / "условия_доставка.xlsx", [["Тема", "Условие"]])
    items, found = vendors._read(tmp_path)
    assert [(i["vendor"], i["ean"], i["name"], i["price"]) for i in items] == [("A", "0733739012890", "NOW Magnesium", 20.22)]
    assert [(v["vendor"], v["rows"], v["note"]) for v in found] == [
        ("A", 1, "1 реда без цена или баркод не са взети"), ("B", 0, "няма ценова листа (supplier_products) в папката")]
