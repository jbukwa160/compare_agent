"""Plain data objects passed between the stages of the pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Product:
    """One row from the supplier offer file."""
    row: int
    name: str
    ean: Optional[str] = None
    sku: Optional[str] = None
    brand: Optional[str] = None
    category: Optional[str] = None
    supplier_price: Optional[float] = None      # net, per unit, in supplier_currency
    supplier_currency: str = "EUR"
    order_qty: Optional[int] = None
    supplier: Optional[str] = None
    keyword: Optional[str] = None             # optional traffic keyword override
    catalogue_group: str = "Not selling"      # "Selling" (has a Dilon code in the file) | "Not selling"
    raw: dict = field(default_factory=dict)   # the row exactly as in the input file (column -> value)
    name_source: str = ""                     # where the name came from when the supplier file has none

    @property
    def label(self) -> str:
        return f"{self.name} [{self.ean or 'no EAN'}]"


@dataclass
class Offer:
    """A product listing found on a reference website."""
    site: str
    scope: str                       # "local" | "global"
    title: str
    url: str
    price: Optional[float] = None    # as shown on the site (gross, incl. VAT)
    currency: str = "EUR"
    in_stock: Optional[bool] = None  # None = unknown
    availability_text: str = ""
    ean: Optional[str] = None
    country: Optional[str] = None     # set when the offer is not from a configured site (Google Shopping)
    shop_count: Optional[int] = None  # for price-comparison sites: number of shops offering it
    match_score: float = 0.0          # 0..1, filled by the matcher
    match_reason: str = ""
    price_eur_net: Optional[float] = None  # filled by the scorer


@dataclass
class SiteResult:
    site: str
    scope: str
    offers: list[Offer] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class Traffic:
    database: str                    # market: country / database code
    keyword: str
    volume: Optional[int] = None     # monthly searches
    cpc: Optional[float] = None
    competition: Optional[float] = None
    results: Optional[int] = None
    error: Optional[str] = None
    source: str = ""                 # traffic source name from sites.yaml
    scope: str = ""                  # "local" (Bulgaria) | "global" (worldwide)
    monthly: list[tuple[int, int, int]] = field(default_factory=list)  # (year, month, searches), oldest first


@dataclass
class Market:
    """What one check (local = Bulgaria, global = EU) found, prices in EUR net of VAT."""
    scope: str
    min_in_stock: Optional[float] = None
    min_price: Optional[float] = None
    median_price: Optional[float] = None
    listed_on: int = 0          # sites with a matching listing
    in_stock_on: int = 0
    shops: int = 0              # shops offering it (comparison sites count all their shops)
    searches: Optional[int] = None  # monthly searches, when a search-volume source is enabled
    monthly: dict[str, int] = field(default_factory=dict)  # "2026-03" -> searches (last 12 months)


@dataclass
class Evaluation:
    product: Product
    local: Market
    global_: Market
    offers: list[Offer]
    errors: list[str]
    traffic: list[Traffic]
    bg_sell_price: Optional[float] = None   # price the sell score is based on (EUR net)
    sell_price_source: str = ""             # "Bulgaria" | "Worldwide (not sold in BG)" | ...out of stock in BG
    eu_price: Optional[float] = None        # EU price the buy score is based on (EUR net)
    margin: Optional[float] = None          # (bg_sell_price - supplier price) / bg_sell_price
    buy_price_score: Optional[float] = None  # 1-10
    sell_price_score: Optional[float] = None  # 1-10
    sales_score: Optional[float] = None      # 1-10
    final_score: Optional[float] = None      # 1-10
    verdict: str = ""
    sells: str = ""                          # "Sells well" | "Average" | "Weak" | "No data" (from the sales score)
    trend: str = ""                          # "Rising" | "Stable" | "Falling" | "" (12-month searches)
