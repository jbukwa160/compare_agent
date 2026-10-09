"""Semrush keyword search volume (Analytics API v3, report type phrase_this).

Markets are Semrush database codes (bg, us, uk, de, ...). About 10 API units per keyword per database.
"""
from __future__ import annotations

from ..models import Traffic
from .base import TrafficSource, register

API_URL = "https://api.semrush.com/"


@register("semrush")
class SemrushSource(TrafficSource):
    @property
    def missing_credentials(self):
        return None if self.env("api_key_env") else self.cfg.get("api_key_env", "SEMRUSH_API_KEY")

    def volumes(self, keywords: list[str], market: str) -> dict[str, Traffic]:
        return {kw: self._one(kw, market) for kw in keywords}

    def _one(self, keyword: str, database: str) -> Traffic:
        t = Traffic(database=database, keyword=keyword)
        params = {"type": "phrase_this", "key": self.env("api_key_env"), "phrase": keyword,
                  "database": database, "export_columns": "Ph,Nq,Cp,Co,Nr"}
        try:
            status, text = self.http.get(API_URL, params=params, delay=0.2)
        except Exception as exc:
            t.error = str(exc)
            return t
        text = text.strip()
        if text.startswith("ERROR 50"):      # NOTHING FOUND -> real zero volume
            t.volume = 0
            return t
        if status != 200 or text.startswith("ERROR"):
            t.error = text[:120] or f"HTTP {status}"
            return t
        lines = text.splitlines()
        if len(lines) < 2:
            t.volume = 0
            return t
        cells = lines[1].split(";")
        num = lambda i, f: f(cells[i]) if len(cells) > i and cells[i] not in ("", "n/a") else None  # noqa: E731
        t.volume, t.cpc, t.competition, t.results = num(1, int), num(2, float), num(3, float), num(4, int)
        return t
