"""Loads config/settings.yaml, config/sites.yaml and the .env file."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"


def load_env(path: Path = ROOT / ".env") -> None:
    """Minimal .env reader (KEY=VALUE per line); real env vars win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _read_yaml(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_settings(path: Path | None = None) -> dict[str, Any]:
    return _read_yaml(path or CONFIG_DIR / "settings.yaml")


def load_traffic_sources(path: Path | None = None) -> list[dict[str, Any]]:
    """Enabled per-product traffic sources from the `traffic:` section of sites.yaml."""
    data = _read_yaml(path or CONFIG_DIR / "sites.yaml")
    return [dict(s) for s in data.get("traffic") or [] if s.get("enabled", True)]


def load_sites(path: Path | None = None, only: list[str] | None = None) -> list[dict[str, Any]]:
    """Return the enabled sites. `only` (site names) overrides the enabled flag."""
    data = _read_yaml(path or CONFIG_DIR / "sites.yaml")
    sites = []
    for scope in ("local", "global"):
        for site in data.get(scope) or []:
            site = dict(site)
            site["scope"] = scope
            if only:
                if site["name"].lower() in {o.lower() for o in only}:
                    sites.append(site)
            elif site.get("enabled", True):
                sites.append(site)
    return sites
