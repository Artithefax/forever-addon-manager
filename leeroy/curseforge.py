"""CurseForge (eigener API-Key des Nutzers, wird nie im Programm mitgeliefert)."""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .constants import USER_AGENT
from .i18n import cur_lang, tr
from .files import install_zip
from .github import asset_key, http_get


# --------------------------------------------------------------------------- #
# CurseForge (eigener API-Key des Nutzers, wird nie im Programm mitgeliefert)
# --------------------------------------------------------------------------- #

CF_API = "https://api.curseforge.com/v1"
CF_GAME_ID = 1                                   # World of Warcraft
CF_PREFIX = "cf:"                                # Quellen-ID: cf:<Projekt-ID>
CF_KEY_URL = "https://console.curseforge.com/"
CF_DL_HOSTS = ("forgecdn.net", "curseforge.com")  # nur von hier wird heruntergeladen
CF_RELEASE_NAMES = {1: "Release", 2: "Beta", 3: "Alpha"}


class CFError(Exception):
    def __init__(self, msg: str, code: int = 0):
        super().__init__(msg)
        self.code = code


class ManualDownload(Exception):
    """Der Autor erlaubt keinen Download über Drittprogramme – Seite im Browser öffnen."""

    def __init__(self, page: str, reason: str):
        super().__init__(reason)
        self.page = page
        self.reason = reason


def cf_request(path: str, key: str, params: dict | None = None, body: dict | None = None) -> dict:
    key = (key or "").strip()
    if not key:
        raise CFError(tr("Kein CurseForge-API-Key hinterlegt (Einstellungen)."))
    url = CF_API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json", "x-api-key": key}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        msgs = {401: tr("CurseForge lehnt den API-Key ab. Bitte den Key in den Einstellungen prüfen."),
                403: tr("CurseForge verweigert den Zugriff (API-Key ungültig oder Download nicht erlaubt)."),
                404: tr("Bei CurseForge nicht gefunden."),
                429: tr("Zu viele Anfragen an CurseForge – bitte in ein paar Minuten erneut versuchen.")}
        raise CFError(msgs.get(e.code, tr("CurseForge-Fehler HTTP {0}.", e.code)), e.code) from None
    except urllib.error.URLError as e:
        raise CFError(tr("CurseForge nicht erreichbar: {0}", e.reason)) from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise CFError(tr("Unerwartete Antwort von CurseForge.")) from None


def parse_cf_ref(text: str):
    """Link, Projekt-ID oder Slug -> Slug (str) oder Projekt-ID (int)."""
    t = text.strip().rstrip("/")
    m = re.search(r"curseforge\.com/wow/addons/([\w-]+)", t, re.IGNORECASE)
    if m:
        return m.group(1).lower()
    m = re.search(r"curseforge\.com/projects/(\d+)", t, re.IGNORECASE)
    if m:
        return int(m.group(1))
    if re.fullmatch(r"\d+", t):
        return int(t)
    if re.fullmatch(r"[a-z0-9][a-z0-9-]*", t, re.IGNORECASE):
        return t.lower()
    raise ValueError(tr("Bitte den CurseForge-Link (…/wow/addons/name) oder den Namen des Addons angeben."))


def cf_get_mod(ref, key: str) -> dict:
    if isinstance(ref, int):
        mod = cf_request(f"/mods/{ref}", key).get("data") or {}
    else:
        found = cf_request("/mods/search", key, {"gameId": CF_GAME_ID, "slug": ref}).get("data") or []
        mod = next((m for m in found if str(m.get("slug", "")).lower() == ref), {})
    if not mod or mod.get("gameId", CF_GAME_ID) != CF_GAME_ID:
        raise CFError(tr("Kein WoW-Addon „{0}“ bei CurseForge gefunden.", ref))
    return mod


CF_SORTS = {"Beliebtheit": (2, "desc"), "Downloads": (6, "desc"),
            "Zuletzt aktualisiert": (3, "desc"), "Name": (4, "asc")}
CF_PAGE_SIZE = 50
WOW_FAMILIES = {1: "Classic", 2: "TBC", 3: "WotLK", 4: "Cata", 5: "MoP"}
FAMILY_ORDER = ["Retail", "MoP", "Cata", "WotLK", "TBC", "Classic"]


def wow_family(version: str) -> str:
    """Spielversion -> Spielreihe (1.x Classic, 2.x TBC … ab 6.x Retail)."""
    m = re.match(r"\s*(\d+)", str(version or ""))
    if not m:
        return ""
    major = int(m.group(1))
    return WOW_FAMILIES.get(major, "Retail" if major >= 6 else "")


def cf_mod_families(mod: dict) -> list[str]:
    fams = {wow_family(i.get("gameVersion", "")) for i in mod.get("latestFilesIndexes") or []}
    return [f for f in FAMILY_ORDER if f in fams]


def fmt_count(n) -> str:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return ""
    if n >= 1_000_000:
        return tr("{0} Mio", f"{n / 1_000_000:.1f}".replace(".", ",") if cur_lang() == "de" else f"{n / 1_000_000:.1f}")
    if n >= 1_000:
        return tr("{0} Tsd", n // 1_000)
    return str(n)


def cf_search(text: str, key: str, sort: str = "Beliebtheit", index: int = 0) -> tuple[list[dict], int]:
    """Durchsucht CurseForge – ausschließlich World-of-Warcraft-Addons (gameId 1).
    Ein eingefügter Link oder eine Projekt-ID findet genau dieses Addon."""
    text = (text or "").strip()
    if "curseforge.com" in text.lower() or text.isdigit():
        return [cf_get_mod(parse_cf_ref(text), key)], 1
    field, order = CF_SORTS.get(sort, CF_SORTS["Beliebtheit"])
    params = {"gameId": CF_GAME_ID, "sortField": field, "sortOrder": order,
              "index": index, "pageSize": CF_PAGE_SIZE}
    if text:
        params["searchFilter"] = text[:100]
    res = cf_request("/mods/search", key, params)
    mods = [m for m in res.get("data") or [] if isinstance(m, dict) and m.get("gameId", CF_GAME_ID) == CF_GAME_ID]
    total = (res.get("pagination") or {}).get("totalCount", len(mods))
    return mods, min(int(total or 0), 10_000)          # CurseForge liefert höchstens 10 000 Treffer


def cf_get_mods(ids: list[int], key: str) -> list[dict]:
    """Mehrere Projekte mit einer einzigen Anfrage holen."""
    return cf_request("/mods", key, body={"modIds": list(ids)}).get("data") or []


def toc_curse_id(meta: dict) -> int | None:
    """Projekt-ID, die der CurseForge-Packager in viele .toc-Dateien schreibt."""
    raw = str(meta.get("x-curse-project-id", "")).strip()
    return int(raw) if raw.isdigit() else None


def cf_match_installed(versions: list[str], assets: list[dict]) -> dict | None:
    """Findet die CurseForge-Datei, die zur installierten .toc-Version passt (oder None)."""
    def find(v: str):
        pat = re.compile(r"(?<![\d.])v?" + re.escape(v) + r"(?!\d|\.\d)", re.IGNORECASE)
        return [a for a in assets if pat.search(a["name"]) or pat.search(a.get("file_name", ""))]

    for ver in versions:
        v = (ver or "").strip().lstrip("vV")
        if not v or v.startswith("@") or "project-version" in v:
            continue
        cands = find(v)
        if not cands:
            m = re.match(r"(\d+(?:\.\d+)+)", v)
            if m and m.group(1) != v:
                cands = find(m.group(1))
        if cands:
            return sorted(cands, key=lambda a: a["date"], reverse=True)[0]
    return None


def cf_download_url(info: dict, asset: dict) -> str:
    """Die normale Download-Adresse der CurseForge-Webseite (wird im Browser des Nutzers geöffnet)."""
    slug = info.get("slug", "")
    if slug and asset.get("file_id"):
        return f"https://www.curseforge.com/wow/addons/{slug}/download/{asset['file_id']}"
    return info.get("page", "").rstrip("/") + "/files"


def cf_queue_item(info: dict, asset: dict) -> dict:
    return {"name": info.get("repo", "?"), "info": info, "asset": asset, "page": cf_download_url(info, asset)}


def zip_name_matches(name: str, wanted: str) -> bool:
    """Gleicher Dateiname, auch wenn der Browser „ (1)“ angehängt hat."""
    def stem(n: str) -> str:
        return re.sub(r"[\s_]*\(\d+\)$", "", Path(n).stem).lower().replace(" ", "_")
    return bool(wanted) and stem(name) == stem(wanted)


def default_download_dir() -> Path:
    """Der Downloads-Ordner des Browsers – unter Windows auch, wenn er verschoben wurde (z. B. nach D:)."""
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
                value, _ = winreg.QueryValueEx(k, "{374DE290-123F-4565-9164-39C4925E467B}")
            p = Path(os.path.expandvars(value))
            if p.is_dir():
                return p
        except OSError:
            pass
    return Path.home() / "Downloads"


def download_matches(path: Path, asset: dict, since: float) -> bool:
    """Ist diese (fertig geladene) ZIP genau die erwartete CurseForge-Datei?
    Mit SHA-1 der API eindeutig; sonst Dateigröße und Zeitpunkt des Downloads."""
    try:
        st = path.stat()
    except OSError:
        return False
    if asset.get("sha1"):
        h = hashlib.sha1()
        try:
            with open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
        except OSError:
            return False
        return h.hexdigest().lower() == asset["sha1"].lower()
    if asset.get("size") and st.st_size != asset["size"]:
        return False
    return st.st_mtime >= since


def cf_files(mod_id: int, key: str) -> list[dict]:
    data = cf_request(f"/mods/{mod_id}/files", key, {"pageSize": 50}).get("data") or []
    data = [f for f in data if f.get("isAvailable", True) and str(f.get("fileName", "")).lower().endswith(".zip")]
    return sorted(data, key=lambda f: f.get("fileDate", ""), reverse=True)


def cf_asset(f: dict) -> dict:
    name = f.get("displayName") or f.get("fileName") or str(f.get("id"))
    versions = [str(v) for v in f.get("gameVersions", [])]
    rtype = f.get("releaseType", 1)
    sha1 = next((h.get("value", "") for h in f.get("hashes", []) if h.get("algo") == 1), "")
    parts = [name]
    if versions:
        parts.append("/".join(versions[:4]))
    if rtype != 1:
        parts.append(CF_RELEASE_NAMES.get(rtype, "?"))
    parts.append(str(f.get("fileDate", ""))[:10])
    deps = sorted({d.get("modId") for d in f.get("dependencies") or []           # relationType 3 = benötigt
                   if isinstance(d, dict) and d.get("relationType") == 3 and isinstance(d.get("modId"), int)})
    return {"name": name, "label": "  ·  ".join(p for p in parts if p), "file_name": f.get("fileName", ""),
            "url": f.get("downloadUrl"), "size": f.get("fileLength", 0), "file_id": f.get("id"),
            "mod_id": f.get("modId"), "game_versions": versions, "release_type": rtype,
            "date": str(f.get("fileDate", "")), "sha1": sha1, "deps": deps}


def cf_rank(a: dict, flavor: str, game_ver: str) -> int:
    fl = flavor.lower()
    forever = "classic_beta" in fl or "forever" in fl
    hay = " ".join([a["name"], a["file_name"], *a["game_versions"]]).lower()
    if "nolib" in hay or "source" in hay:
        return 9
    if forever and "forever" in hay:
        return 0
    mm = ".".join(game_ver.split(".")[:2]) if game_ver else ""
    if mm and any(v == mm or v.startswith(mm + ".") for v in a["game_versions"]):
        return 1
    if (forever or "classic" in fl) and ("classic" in hay or "vanilla" in hay) \
            and not any(x in hay for x in ("wrath", "cata", "mists", "tbc", "bcc", "wotlk")):
        return 2
    if "retail" in fl and ("retail" in hay or "mainline" in hay):
        return 1
    return 5


def cf_pick(assets: list[dict], flavor: str, game_ver: str, stored: dict | None = None) -> dict | None:
    """Wählt die Datei: bei Updates dieselbe Variante wie zuvor, sonst die beste Schätzung."""
    if not assets:
        return None
    newest = sorted(assets, key=lambda a: a["date"], reverse=True)
    if stored:
        key = stored.get("asset_key", "")
        gv = set(stored.get("game_versions") or [])
        rt = stored.get("release_type", 1)
        for use_key in (True, False):
            cands = [a for a in newest
                     if a["release_type"] <= rt
                     and (not use_key or not key or asset_key(a["name"]) == key)
                     and (not gv or gv & set(a["game_versions"]))]
            if cands:
                return cands[0]
    return sorted(newest, key=lambda a: (cf_rank(a, flavor, game_ver), a["release_type"]))[0]


def cf_info(mod: dict, files: list[dict], flavor: str, game_ver: str, stored: dict | None = None) -> dict:
    assets = [cf_asset(f) for f in files]
    ranked = sorted(sorted(assets, key=lambda a: a["date"], reverse=True),
                    key=lambda a: (cf_rank(a, flavor, game_ver), a["release_type"]))
    best = cf_pick(assets, flavor, game_ver, stored)
    slug = mod.get("slug", "")
    page = (mod.get("links") or {}).get("websiteUrl") or f"https://www.curseforge.com/wow/addons/{slug}"
    return {"repo": mod.get("name", slug), "tag": best["name"] if best else "", "assets": ranked,
            "best": best, "notes": "", "page": page, "published": best["date"][:10] if best else "",
            "mod_id": mod.get("id"), "slug": slug, "author": ", ".join(
                a.get("name", "") for a in mod.get("authors", []) if a.get("name")),
            "allow_dl": mod.get("allowModDistribution") is not False}


def cf_mod_from_source(src: dict) -> dict:
    return {"id": src.get("mod_id"), "name": src.get("name", ""), "slug": src.get("slug", ""),
            "links": {"websiteUrl": src.get("page", "")}, "authors": [{"name": src.get("author", "")}],
            "allowModDistribution": src.get("allow_dl", True)}


def html_to_text(s: str) -> str:
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</h\d>|</ul>", "\n", s)
    s = re.sub(r"(?i)<li[^>]*>", "• ", s)
    s = re.sub(r"(?i)</li>", "\n", s)
    s = html.unescape(re.sub(r"<[^>]+>", "", s))
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def cf_changelog(mod_id: int, file_id: int, key: str) -> str:
    try:
        return html_to_text(cf_request(f"/mods/{mod_id}/files/{file_id}/changelog", key).get("data") or "")
    except CFError:
        return ""


def cf_check(src: dict, key: str, flavor: str, game_ver: str) -> dict:
    """Prüft ein installiertes CurseForge-Addon auf eine neuere Datei (1 Anfrage, +1 bei Update)."""
    files = cf_files(src["mod_id"], key)
    info = cf_info(cf_mod_from_source(src), files, flavor, game_ver, src)
    if not info["best"]:
        raise CFError(tr("keine passende Datei gefunden"))
    info["allow_dl"] = bool(info["best"].get("url"))     # ohne Download-Adresse: Autor hat es gesperrt
    if info["tag"] != src.get("tag"):
        info["notes"] = cf_changelog(src["mod_id"], info["best"]["file_id"], key)
    return info


def cf_install_asset(asset: dict, info: dict, root: str, flavor: str, key: str) -> list[str]:
    page = info.get("page", "").rstrip("/") + "/files"
    if not info.get("allow_dl", True):
        raise ManualDownload(page, tr("Der Autor erlaubt keinen Download über Programme wie diesen."))
    url = asset.get("url")
    if not url:
        try:
            url = cf_request(f"/mods/{asset['mod_id']}/files/{asset['file_id']}/download-url", key).get("data")
        except CFError as e:
            if e.code in (403, 404):
                raise ManualDownload(page, tr("Für diese Datei ist kein direkter Download freigegeben.")) from None
            raise
    if not isinstance(url, str) or not url:
        raise ManualDownload(page, tr("Für diese Datei ist kein direkter Download freigegeben."))
    host = urllib.parse.urlparse(url).hostname or ""
    if not url.startswith("https://") or not any(host == h or host.endswith("." + h) for h in CF_DL_HOSTS):
        raise CFError(tr("Unerwartete Download-Adresse ({0}) – Abbruch.", host or '?'))
    blob = http_get(url, "", accept="application/octet-stream")     # niemals den API-Key ans CDN senden
    if asset.get("sha1") and hashlib.sha1(blob).hexdigest().lower() != asset["sha1"].lower():
        raise CFError(tr("Prüfsumme der heruntergeladenen Datei stimmt nicht – Abbruch."))
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp.write(blob)
        tmp_path = Path(tmp.name)
    try:
        return install_zip(tmp_path, root, flavor)
    finally:
        tmp_path.unlink(missing_ok=True)


def cf_pack_item(src: dict, key: str, flavor: str, game_ver: str) -> dict:
    """Für den Paket-Import: die im Paket genannte Datei (oder die beste verfügbare) bestimmen."""
    mod = cf_request(f"/mods/{src['mod_id']}", key).get("data") or {}
    files = cf_files(src["mod_id"], key)
    info = cf_info(mod, files, flavor, game_ver)
    exact = next((a for a in info["assets"] if a["file_id"] == src.get("file_id")), None)
    if exact:
        info["best"], info["tag"] = exact, exact["name"]
    if not info["best"]:
        raise CFError(tr("keine passende Datei gefunden"))
    return info
