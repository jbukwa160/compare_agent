"""`python -m compare_agent --check-keys`: confirms each API key in .env works, without spending anything."""
from __future__ import annotations

import os

from .config import ROOT
from .http import HttpClient
from .traffic.dataforseo import check_account


def check_keys() -> int:
    http = HttpClient(ROOT / ".cache", use_cache=False)
    problems = 0

    login, password = os.environ.get("DATAFORSEO_LOGIN", "").strip(), os.environ.get("DATAFORSEO_PASSWORD", "").strip()
    if not (login and password):
        print("DataForSEO: not set (DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD) - traffic per product is off")
    else:
        try:
            print(f"DataForSEO: OK - {check_account(http, login, password)}")
        except Exception as exc:
            problems += 1
            print(f"DataForSEO: FAILED - {exc}")

    if os.environ.get("SEMRUSH_API_KEY", "").strip():
        print("Semrush: key set (not used unless enabled in sites.yaml -> traffic)")
    return 1 if problems else 0
