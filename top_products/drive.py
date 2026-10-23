"""Google Drive API with a service account: copies the "EU vendors" folder into the cache, only what changed.

Needed only on a computer without Google Drive for desktop. Setup (once): a Google Cloud service account with the
Drive API enabled, its JSON key saved on this computer and named in .env as GOOGLE_SERVICE_ACCOUNT_FILE, and the
service account's e-mail added to the shared drive as a Viewer.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

API = "https://www.googleapis.com/drive/v3/files"
FOLDER = "application/vnd.google-apps.folder"
SHEET = "application/vnd.google-apps.spreadsheet"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def session(key_file: str):
    from google.auth.transport.requests import AuthorizedSession   # pip install google-auth
    from google.oauth2 import service_account
    creds = service_account.Credentials.from_service_account_file(
        key_file, scopes=["https://www.googleapis.com/auth/drive.readonly"])
    return AuthorizedSession(creds)


def _safe(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip() or "_"


def sync(http, folder_id: str, out_dir: Path) -> Path:
    """Mirror the folder's spreadsheets (any depth) into out_dir; returns out_dir."""
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_file = out_dir / ".manifest.json"
    manifest = json.loads(manifest_file.read_text(encoding="utf-8")) if manifest_file.exists() else {}
    seen, downloaded = set(), 0

    def walk(fid: str, rel: Path) -> None:
        nonlocal downloaded
        token = None
        while True:
            params = {"q": f"'{fid}' in parents and trashed = false", "pageSize": 1000,
                      "fields": "nextPageToken, files(id, name, mimeType, modifiedTime)",
                      "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}
            if token:
                params["pageToken"] = token
            r = http.get(API, params=params)
            r.raise_for_status()
            data = r.json()
            for f in data.get("files", []):
                path = rel / _safe(f["name"])
                if f["mimeType"] == FOLDER:
                    walk(f["id"], path)
                    continue
                if f["mimeType"] == SHEET:          # a native Google Sheet: export it as .xlsx
                    path, url, params_dl = path.with_name(path.name + ".xlsx"), f"{API}/{f['id']}/export", {"mimeType": XLSX}
                elif f["name"].lower().endswith(".xlsx"):
                    url, params_dl = f"{API}/{f['id']}", {"alt": "media", "supportsAllDrives": "true"}
                else:
                    continue
                key = path.as_posix()
                seen.add(key)
                if manifest.get(key) == f["modifiedTime"] and (out_dir / path).exists():
                    continue
                resp = http.get(url, params=params_dl)
                resp.raise_for_status()
                (out_dir / path).parent.mkdir(parents=True, exist_ok=True)
                (out_dir / path).write_bytes(resp.content)
                manifest[key] = f["modifiedTime"]
                downloaded += 1
            token = data.get("nextPageToken")
            if not token:
                break

    walk(folder_id, Path("."))
    for key in set(manifest) - seen:              # deleted on Drive: delete here too
        (out_dir / key).unlink(missing_ok=True)
        manifest.pop(key)
    manifest_file.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    print(f"Google Drive: {len(seen)} spreadsheets, {downloaded} downloaded (changed since the last run)")
    return out_dir
