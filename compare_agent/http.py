"""Shared HTTP client: browser-like headers, retries, per-domain throttling and an on-disk cache."""
from __future__ import annotations

import hashlib
import random
import json
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.8,*/*;q=0.7",
    "Accept-Language": "bg-BG,bg;q=0.9,en-US;q=0.8,en;q=0.7",
}


class HttpClient:
    def __init__(self, cache_dir: Path, cache_ttl_hours: float = 12, min_delay: float = 1.0,
                 timeout: float = 25, use_cache: bool = True):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_ttl = cache_ttl_hours * 3600
        self.min_delay = min_delay
        self.timeout = timeout
        self.use_cache = use_cache
        self._local = threading.local()
        self._domain_locks: dict[str, threading.Lock] = {}
        self._domain_last: dict[str, float] = {}
        self._guard = threading.Lock()

    @property
    def session(self) -> requests.Session:
        # requests.Session is not thread-safe, so one per worker thread
        if not hasattr(self._local, "session"):
            s = requests.Session()
            retry = Retry(total=3, backoff_factor=1.5, status_forcelist=(429, 500, 502, 503, 504),
                          allowed_methods=("GET", "POST"))
            s.mount("https://", HTTPAdapter(max_retries=retry))
            s.mount("http://", HTTPAdapter(max_retries=retry))
            s.headers.update(DEFAULT_HEADERS)
            self._local.session = s
        return self._local.session

    def _throttle(self, url: str, delay: Optional[float]) -> None:
        domain = urlparse(url).netloc
        with self._guard:
            lock = self._domain_locks.setdefault(domain, threading.Lock())
        with lock:
            base = delay if delay is not None else self.min_delay
            base += random.uniform(0, 0.3 * base)  # jitter: an exact rhythm looks like a bot
            wait = base - (time.time() - self._domain_last.get(domain, 0))
            if wait > 0:
                time.sleep(wait)
            self._domain_last[domain] = time.time()

    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / (hashlib.sha1(key.encode("utf-8")).hexdigest() + ".json")

    @property
    def browser_session(self):
        """curl_cffi session that mimics Chrome's TLS fingerprint, for sites that 403 plain requests."""
        if not hasattr(self._local, "browser_session"):
            try:
                from curl_cffi import requests as cffi_requests
            except ImportError as exc:
                raise RuntimeError("this site needs 'pip install curl_cffi' (impersonate: true)") from exc
            self._local.browser_session = cffi_requests.Session(impersonate="chrome")
        return self._local.browser_session

    def fetch(self, method: str, url: str, *, params: dict | None = None, json_body: Any = None,
              data: Any = None, headers: dict | None = None, delay: float | None = None,
              cache: bool = True, impersonate: bool = False,
              encoding: str | None = None, timeout: float | None = None,
              cache_check: Callable[[str], bool] | None = None) -> tuple[int, str, str]:
        """Returns (status_code, text, final_url after redirects). Successful responses are cached on disk."""
        key = json.dumps([method, url, params, json_body, data], sort_keys=True, default=str)
        path = self._cache_path(key)
        if cache and self.use_cache and path.exists() and time.time() - path.stat().st_mtime < self.cache_ttl:
            cached = json.loads(path.read_text(encoding="utf-8"))
            return cached["status"], cached["text"], cached.get("url", url)

        self._throttle(url, delay)
        if impersonate:
            resp = self.browser_session.request(method, url, params=params, json=json_body, data=data,
                                                headers=headers, timeout=timeout or self.timeout)
            if resp.status_code == 429:  # curl_cffi has no Retry adapter: one polite retry
                time.sleep(10)
                resp = self.browser_session.request(method, url, params=params, json=json_body, data=data,
                                                    headers=headers, timeout=timeout or self.timeout)
        else:
            resp = self.session.request(method, url, params=params, json=json_body, data=data,
                                        headers=headers, timeout=timeout or self.timeout)
            if resp.encoding is None or resp.encoding.lower() == "iso-8859-1":
                resp.encoding = resp.apparent_encoding or "utf-8"
        text = resp.content.decode(encoding, errors="replace") if encoding else resp.text
        final_url = str(resp.url)
        if cache and resp.status_code == 200 and (cache_check is None or cache_check(text)):
            path.write_text(json.dumps({"status": resp.status_code, "text": text, "url": final_url}),
                            encoding="utf-8")
        return resp.status_code, text, final_url

    def request(self, method: str, url: str, **kw) -> tuple[int, str]:
        status, text, _ = self.fetch(method, url, **kw)
        return status, text

    def get(self, url: str, **kw) -> tuple[int, str]:
        return self.request("GET", url, **kw)

    def get_json(self, url: str, **kw) -> Any:
        status, text = self.request("GET", url, **kw)
        if status != 200:
            raise RuntimeError(f"HTTP {status} for {url}")
        return json.loads(text)

    def post_json(self, url: str, body: Any, **kw) -> Any:
        status, text = self.request("POST", url, json_body=body, **kw)
        if status != 200:
            raise RuntimeError(f"HTTP {status} for {url}")
        return json.loads(text)
