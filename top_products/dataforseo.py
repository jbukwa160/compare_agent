"""DataForSEO Labs (Google): cached per request and month, with a hard budget for the run."""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import requests

from compare_agent.config import load_env
from compare_agent.traffic.dataforseo import auth_header

API = "https://api.dataforseo.com/v3/"
PRICE_TASK, PRICE_ROW = 0.012, 0.00012   # USD per request and per returned row


class BudgetExceeded(RuntimeError):
    pass


class Labs:
    def __init__(self, cache_dir: Path, budget: float):
        self.cache = cache_dir
        self.cache.mkdir(parents=True, exist_ok=True)
        self.budget = budget
        self.spent = 0.0
        self.month = time.strftime("%Y-%m")   # the same request in a new month is asked again

    def _headers(self) -> dict[str, str]:
        load_env()
        login, password = os.environ.get("DATAFORSEO_LOGIN", "").strip(), os.environ.get("DATAFORSEO_PASSWORD", "").strip()
        if not (login and password):
            raise RuntimeError("DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD are missing in .env")
        return auth_header(login, password)

    def data_date(self) -> str:
        """Free: the date of the Labs Google data, e.g. 2026-10-17."""
        r = requests.get(API + "dataforseo_labs/status", headers=self._headers(), timeout=60).json()
        return (((r.get("tasks") or [{}])[0].get("result") or [{}])[0].get("google") or {}).get("date_update", "")

    def call(self, endpoint: str, task: dict) -> dict:
        """One task; returns its first result. Billed only when not cached this month."""
        key = hashlib.sha1(json.dumps([self.month, endpoint, task], sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:20]
        path = self.cache / f"{endpoint.split('/')[-2]}_{key}.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
        else:
            worst = PRICE_TASK + PRICE_ROW * int(task.get("limit", 100))
            if self.spent + worst > self.budget:
                raise BudgetExceeded(f"stopped at ${self.spent:.2f}: the next call could pass the ${self.budget:.2f} budget")
            data = requests.post(API + endpoint, json=[task], headers=self._headers(), timeout=180).json()
            if data.get("status_code") != 20000:
                raise RuntimeError(f"DataForSEO: {data.get('status_code')} {data.get('status_message')}")
            self.spent += float(data.get("cost") or 0)
            t = data["tasks"][0]
            if t.get("status_code") != 20000:
                raise RuntimeError(f"DataForSEO {endpoint}: {t.get('status_code')} {t.get('status_message')}")
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return (data["tasks"][0].get("result") or [{}])[0] or {}
