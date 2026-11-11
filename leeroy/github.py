"""GitHub-Releases: Abfragen und Downloads."""
from __future__ import annotations

import json
import re
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .constants import USER_AGENT
from .i18n import tr
from .config import config_dir, log, write_text_atomic
from .files import install_zip

# Nur an diese Server geht ein GitHub-Token (nie an Download-Server, auf die GitHub weiterleitet).
GITHUB_HOSTS = {"api.github.com", "github.com", "raw.githubusercontent.com", "codeload.github.com"}
SECRET_HEADERS = ("Authorization", "X-api-key")


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Folgt Weiterleitungen, schickt Token und API-Key aber nie an einen anderen Server mit.
    (urllib übernimmt sonst alle Kopfzeilen – Release-Downloads leiten z. B. zu objects.githubusercontent.com.)"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urllib.parse.urlparse(newurl).hostname != urllib.parse.urlparse(req.full_url).hostname:
            for key in SECRET_HEADERS:
                new.headers.pop(key, None)
                new.unredirected_hdrs.pop(key, None)
        return new


_OPENER = urllib.request.build_opener(SafeRedirect)


def open_url(req, timeout: float = 30):
    """urlopen mit sicheren Weiterleitungen (siehe SafeRedirect)."""
    return _OPENER.open(req, timeout=timeout)


# --------------------------------------------------------------------------- #
# GitHub
# --------------------------------------------------------------------------- #

def parse_repo(text: str) -> str:
    text = text.strip().rstrip("/")
    m = re.search(r"github\.com/([^/\s]+)/([^/\s#?]+)", text)
    if m:
        return f"{m.group(1)}/{m.group(2).removesuffix('.git')}"
    if re.fullmatch(r"[\w.-]+/[\w.-]+", text):
        return text
    raise ValueError(tr("Bitte 'owner/repo' oder eine GitHub-URL angeben."))


def github_request(url: str, token: str = "", accept: str = "application/vnd.github+json") -> urllib.request.Request:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    if token and urllib.parse.urlparse(url).hostname in GITHUB_HOSTS:
        req.add_header("Authorization", f"Bearer {token}")
    return req


def http_get(url: str, token: str = "", accept: str = "application/vnd.github+json") -> bytes:
    with open_url(github_request(url, token, accept)) as r:
        return r.read()


# ----- Zwischenspeicher für GitHub-Antworten (ETag) ------------------------------------------------ #
GH_CACHE_MAX_AGE = 300            # so lange gilt eine Antwort ohne neue Anfrage
_GH_LOCK = threading.Lock()
_GH_CACHE: dict = {}


def _gh_cache_file() -> Path:
    return config_dir() / "cache" / "github.json"


def _gh_cache() -> dict:
    if not _GH_CACHE:
        try:
            _GH_CACHE.update(json.loads(_gh_cache_file().read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
        _GH_CACHE.setdefault("entries", {})
    return _GH_CACHE["entries"]


def _gh_cache_save() -> None:
    entries = _GH_CACHE.get("entries", {})
    if len(entries) > 300:                          # älteste verwerfen
        for k in sorted(entries, key=lambda k: entries[k].get("t", 0))[:len(entries) - 300]:
            entries.pop(k, None)
    try:
        _gh_cache_file().parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(_gh_cache_file(), json.dumps({"entries": entries}, ensure_ascii=False))
    except OSError as e:
        log.warning("GitHub-Zwischenspeicher nicht gespeichert: %s", e)


def github_json(url: str, token: str = "", max_age: float = GH_CACHE_MAX_AGE):
    """GitHub-API mit Zwischenspeicher: Frische Antworten (< max_age) ohne Anfrage, sonst mit ETag. Antwortet
    GitHub mit 304 (unverändert), zählt das mit Token nicht ins Abfragelimit."""
    with _GH_LOCK:
        hit = dict(_gh_cache().get(url) or {})
    now = time.time()
    if hit and now - hit.get("t", 0) < max_age:
        return hit["data"]
    req = github_request(url, token)
    if hit.get("etag"):
        req.add_header("If-None-Match", hit["etag"])
    try:
        with open_url(req) as r:
            data = json.loads(r.read())
            etag = r.headers.get("ETag", "")
    except urllib.error.HTTPError as e:
        if e.code == 304 and hit:
            with _GH_LOCK:
                _gh_cache()[url] = {**hit, "t": now}
                _gh_cache_save()
            return hit["data"]
        raise
    with _GH_LOCK:
        _gh_cache()[url] = {"t": now, "etag": etag, "data": data}
        _gh_cache_save()
    return data


def asset_key(name: str) -> str:
    """Name ohne Versionsnummern – erkennt dieselbe Variante in neuen Releases wieder."""
    return re.sub(r"v?\d+(?:[._-]\d+)*", "#", name.lower())


def rank_assets(assets: list[dict], flavor: str = "") -> list[dict]:
    fl = flavor.lower()
    forever = "forever" in fl or fl == "_classic_beta_"

    def rank(a):
        n = a["name"].lower()
        if "source" in n or "nolib" in n:
            return 9
        if forever and "forever" in n:
            return 0
        if ("classic" in fl or forever) and ("vanilla" in n or "classic" in n) \
                and not any(x in n for x in ("wrath", "cata", "mists", "tbc", "bcc")):
            return 1
        if "retail" in fl and ("mainline" in n or "retail" in n):
            return 0
        return 5
    return sorted(assets, key=rank)


def github_latest(repo: str, token: str = "", max_age: float = GH_CACHE_MAX_AGE) -> dict:
    data = github_json(f"https://api.github.com/repos/{repo}/releases/latest", token, max_age)
    return release_info(repo, data)


def release_info(repo: str, data: dict) -> dict:
    assets = [{"name": a["name"], "url": a["browser_download_url"], "size": a.get("size", 0)}
              for a in data.get("assets", []) if a["name"].lower().endswith(".zip")]
    return {
        "repo": repo,
        "tag": data.get("tag_name", ""),
        "assets": assets,
        "all_assets": [{"name": a["name"], "url": a["browser_download_url"], "size": a.get("size", 0)}
                       for a in data.get("assets", [])],
        "zipball": data.get("zipball_url"),
        "notes": (data.get("body") or "").strip(),
        "page": data.get("html_url", f"https://github.com/{repo}/releases"),
        "published": (data.get("published_at") or "")[:10],
        "published_at": data.get("published_at") or "",
    }


def choose_asset(info: dict, flavor: str, key: str = "") -> dict | None:
    """Wählt die Datei: zuerst dieselbe Variante wie beim letzten Mal, sonst die beste."""
    assets = info["assets"]
    if key:
        for a in assets:
            if asset_key(a["name"]) == key:
                return a
    ranked = rank_assets(assets, flavor)
    if ranked:
        return ranked[0]
    if info.get("zipball"):
        return {"name": "zipball", "url": info["zipball"], "size": 0}
    return None


def github_install_asset(asset: dict, root: str, flavor: str, token: str = "",
                         rollback: tuple[str, dict] | None = None) -> list[str]:
    blob = http_get(asset["url"], token, accept="application/octet-stream")
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp.write(blob)
        tmp_path = Path(tmp.name)
    try:
        return install_zip(tmp_path, root, flavor, rollback=rollback)
    finally:
        tmp_path.unlink(missing_ok=True)
