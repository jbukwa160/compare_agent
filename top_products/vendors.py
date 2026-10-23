"""EU vendors' price lists: the "EU vendors" Google Drive folder, as its .zip download or extracted."""
from __future__ import annotations

import gzip
import json
import zipfile
from pathlib import Path

from openpyxl import load_workbook

from .config import CACHE, ROOT
from .pages import norm_gtin


def find_source(arg: str | None) -> Path | None:
    """The given path, else the newest "EU vendors*.zip" / "EU vendors" folder here, in input/ or in Downloads."""
    if arg:
        return Path(arg).expanduser()
    found = []
    for folder in (ROOT, ROOT / "input", ROOT.parent, Path.home() / "Downloads"):
        found += [p for p in folder.glob("EU vendors*") if p.suffix == ".zip" or p.is_dir()]
    return max(found, key=lambda p: p.stat().st_mtime) if found else None


def _extract(zip_path: Path) -> Path:
    """Unzips the spreadsheets once into the cache (paths checked: nothing lands outside it)."""
    st = zip_path.stat()
    out = CACHE / "vendors" / f"{zip_path.stem}-{st.st_size}-{int(st.st_mtime)}"
    if not (out / ".done").exists():
        out.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path) as zf:
            for info in zf.infolist():
                target = (out / info.filename).resolve()
                if info.is_dir() or not info.filename.lower().endswith(".xlsx") or out.resolve() not in target.parents:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(info))
        (out / ".done").write_text("ok")
    tops = [p for p in out.iterdir() if p.is_dir()]
    return tops[0] if len(tops) == 1 else out       # the zip holds one "EU vendors" folder


def _rows(path: Path, sheet: str | None = None):
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb[sheet] if sheet else wb.worksheets[0]
        it = ws.iter_rows(values_only=True)
        head = [str(h).strip() if h is not None else "" for h in next(it, [])]
        for r in it:
            yield dict(zip(head, r))
    finally:
        wb.close()


def _num(v) -> float | None:
    try:
        return float(str(v).replace(",", ".")) if v not in (None, "") else None
    except ValueError:
        return None


def _read(root: Path) -> list[dict]:
    """Every vendor folder: supplier_products.xlsx plus the supplier_products sheets of its offer files."""
    items = []
    for vendor_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        names = {}
        if (vendor_dir / "products.xlsx").exists():
            for r in _rows(vendor_dir / "products.xlsx"):
                e = norm_gtin(r.get("ean_main"))
                if e:
                    names[e] = (str(r.get("name") or "").strip(), str(r.get("brand") or "").strip())
        sources = [("supplier_products.xlsx", None)] if (vendor_dir / "supplier_products.xlsx").exists() else []
        for f in sorted(vendor_dir.glob("*.xlsx")):
            if f.name in ("supplier_products.xlsx", "products.xlsx"):
                continue
            try:
                wb = load_workbook(f, read_only=True)
                sheets = wb.sheetnames
                wb.close()
            except Exception:
                continue
            sources += [(f.name, sh) for sh in sheets if sh.startswith("supplier_products")]
        for fname, sheet in sources:
            for r in _rows(vendor_dir / fname, sheet):
                e = norm_gtin(r.get("supplier_ean")) or norm_gtin(r.get("supplier_sku"))
                price = _num(r.get("price"))
                if not e or price is None or price <= 0:
                    continue
                name, brand = names.get(e, ("", ""))
                items.append({"vendor": vendor_dir.name, "ean": e, "name": str(r.get("name") or "").strip() or name,
                              "brand": brand, "price": price, "currency": str(r.get("currency") or "EUR").strip().upper(),
                              "stock": r.get("stock"), "source": fname})
    return items


def load(source: Path) -> tuple[list[dict], str]:
    """(price rows, description). Parsed once per zip/folder version, then read from the cache."""
    st = source.stat()
    cache = CACHE / "vendors" / f"items-{source.stem}-{st.st_size}-{int(st.st_mtime)}.json.gz"
    if cache.exists():
        items = json.loads(gzip.decompress(cache.read_bytes()))
    else:
        root = _extract(source) if source.suffix == ".zip" else source
        items = _read(root)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(gzip.compress(json.dumps(items, ensure_ascii=False).encode()))
    n = lambda x: f"{x:,}".replace(",", " ")  # noqa: E731
    return items, (f"{source.name}: {len({i['vendor'] for i in items})} доставчика, {n(len(items))} реда с цена, "
                   f"{n(len({i['ean'] for i in items}))} баркода")
