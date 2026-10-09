"""DataForSEO — Google Ads search volume (keywords_data/google_ads/search_volume/live).

One request per market carries up to 1000 keywords and is billed as one request, so a whole supplier
file costs cents per market. Markets are DataForSEO location names ("Bulgaria", "United States");
"Worldwide" sends no location, which returns the all-locations total.
Limit: 12 requests per minute per account (sites.yaml delay_seconds: 5.5).
Credentials: API login + API password from https://app.dataforseo.com/api-access (not the
website password), in .env as DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD.
Docs: https://docs.dataforseo.com/v3/keywords_data/google_ads/search_volume/live/
"""
from __future__ import annotations

import base64
import json
import re

from ..http import HttpClient
from ..models import Traffic
from .base import SourceAccountError, TrafficSource, register

API_URL = "https://api.dataforseo.com/v3/keywords_data/google_ads/search_volume/live"
USER_URL = "https://api.dataforseo.com/v3/appendix/user_data"
BATCH = 1000
WORLDWIDE = "worldwide"
ACCOUNT_ERRORS = {40100, 40101, 40102, 40103, 40104, 40200, 40201, 40210}   # login / balance / access
INVALID_INPUT = {40501, 40503}   # invalid field / keyword: split the batch to isolate it
# DataForSEO status codes worth explaining (others are passed through as-is)
STATUS_HELP = {
    40100: "wrong DataForSEO login/password (use the API credentials from app.dataforseo.com/api-access)",
    40200: "DataForSEO balance is empty - top up the account",
    40202: "DataForSEO rate limit reached - wait a minute",
    40501: "invalid field in the request (check the market / location name in sites.yaml)",
}


def _clean(keyword: str) -> str:
    """Google Ads rejects some symbols and keywords over 10 words / 80 characters."""
    kw = re.sub(r"[^\w\s.\-]", " ", keyword)
    return " ".join(kw.split()[:10])[:80].strip().lower()


def _norm(keyword: str) -> str:
    """Comparison form: DataForSEO may return the keyword with punctuation normalised."""
    return " ".join(re.sub(r"[^\w]+", " ", keyword.lower()).split())


def _all_ok(text: str) -> bool:
    """Cache only fully successful answers (DataForSEO reports errors inside an HTTP 200)."""
    try:
        data = json.loads(text)
    except ValueError:
        return False
    return data.get("status_code") == 20000 and all(t.get("status_code") == 20000 for t in data.get("tasks") or [])


def auth_header(login: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{login}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}", "Content-Type": "application/json"}


def check_account(http: HttpClient, login: str, password: str) -> str:
    """Free call: confirms the credentials and returns the balance."""
    status, text = http.request("GET", USER_URL, headers=auth_header(login, password), cache=False)
    data = json.loads(text) if text else {}
    if status != 200 or data.get("status_code") != 20000:
        code = data.get("status_code")
        raise RuntimeError(STATUS_HELP.get(code, f"HTTP {status}: {data.get('status_message', text[:120])}"))
    money = ((data.get("tasks") or [{}])[0].get("result") or [{}])[0].get("money") or {}
    return f"balance ${money.get('balance', '?')}"


@register("dataforseo")
class DataForSeoSource(TrafficSource):
    @property
    def missing_credentials(self):
        for key in ("login_env", "password_env"):
            if not self.env(key):
                return self.cfg.get(key)
        return None

    def volumes(self, keywords: list[str], market: str) -> dict[str, Traffic]:
        cleaned = {kw: _clean(kw) for kw in keywords}
        found: dict[str, Traffic] = {}
        unique = sorted({c for c in cleaned.values() if c})
        for i in range(0, len(unique), BATCH):
            self._query(unique[i:i + BATCH], market, found)
        return {kw: found.get(_norm(c), Traffic(database=market, keyword=kw, volume=0))
                for kw, c in cleaned.items()}

    def _query(self, batch: list[str], market: str, found: dict[str, Traffic]) -> None:
        """One billed request. If Google rejects a keyword in it (invalid field), split the batch so one odd
        product name cannot cost the whole file its traffic data; the rejected keyword gets an error."""
        task = {"keywords": batch}
        if market.lower() != WORLDWIDE:  # no location = all locations worldwide
            task["location_name"] = market
        if self.cfg.get("language_name"):
            task["language_name"] = self.cfg["language_name"]
        status, text = self.http.request("POST", API_URL, json_body=[task], headers=auth_header(
            self.env("login_env"), self.env("password_env")), delay=self.cfg.get("delay_seconds", 5.5),
            cache_check=_all_ok)
        try:
            data = json.loads(text) if text else {}
        except ValueError:
            raise RuntimeError(f"HTTP {status}: not JSON ({text[:80]!r})")
        if status != 200 or data.get("status_code") != 20000:
            code = data.get("status_code")
            err = SourceAccountError if code in ACCOUNT_ERRORS or status in (401, 402) else RuntimeError
            raise err(STATUS_HELP.get(code, f"HTTP {status}: {data.get('status_message', text[:120])}"))
        for t in data.get("tasks") or []:
            code = t.get("status_code")
            if code == 20000:
                for item in t.get("result") or []:
                    months = sorted((m["year"], m["month"], m.get("search_volume") or 0)
                                    for m in item.get("monthly_searches") or [] if m.get("year") and m.get("month"))
                    found[_norm(item["keyword"])] = Traffic(
                        database=market, keyword=item["keyword"], volume=item.get("search_volume") or 0,
                        cpc=item.get("cpc"), competition=item.get("competition_index"), monthly=months)
            elif code in INVALID_INPUT and len(batch) > 1:
                half = len(batch) // 2
                self._query(batch[:half], market, found)
                self._query(batch[half:], market, found)
            elif code in INVALID_INPUT:
                found[_norm(batch[0])] = Traffic(database=market, keyword=batch[0],
                                                 error=f"rejected by Google Ads: {t.get('status_message', code)}")
            else:
                err = SourceAccountError if code in ACCOUNT_ERRORS else RuntimeError
                raise err(STATUS_HELP.get(code, t.get("status_message", "task failed")))
