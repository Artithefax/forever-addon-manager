#!/usr/bin/env python3
"""
Forever Addon Manager
Ein schlanker Addon-Manager mit GUI für World of Warcraft (Forever, Classic, Retail).

Funktionen:
  - WoW-Installation und Spielversionen (_forever_, _classic_beta_, _retail_ ...) erkennen
  - Installierte Addons auflisten (.toc: Titel, Version, Interface, Abhängigkeiten)
  - Interface-Version gegen die installierte Spielversion prüfen (.build.info)
  - Addons aus ZIP-Dateien oder GitHub-Releases installieren (mit Dateiauswahl)
  - GitHub-Addons auf Updates prüfen (auch beim Start), Release-Notes anzeigen
  - Addon-Sets: Gruppen von Addons speichern und mit einem Klick umschalten
  - Addons deaktivieren/aktivieren (Verschieben nach Interface/AddOns_Disabled)
  - Addon-Pakete (.faddons) exportieren und importieren, um Addons zu teilen
  - WTF-Ordner sichern und wiederherstellen, automatisches Backup vor Updates
  - Warnung, wenn WoW läuft; Drag & Drop (mit tkinterdnd2); Logdatei

Läuft mit der Python-Standardbibliothek; tkinterdnd2 ist optional (Drag & Drop).
Arbeitet ausschließlich mit Dateien im Addon-/WTF-Ordner – kein Eingriff in den Spielclient.
"""

import hashlib
import json
import logging
import logging.handlers
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
import webbrowser
import zipfile
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

try:                                   # optional: Drag & Drop
    from tkinterdnd2 import TkinterDnD, DND_FILES
    BaseTk = TkinterDnD.Tk
    HAS_DND = True
except Exception:                      # noqa – Paket fehlt oder Tk-Erweiterung nicht ladbar
    BaseTk = tk.Tk
    DND_FILES = None
    HAS_DND = False

APP_NAME = "Forever Addon Manager"
APP_VERSION = "2.4.0"
USER_AGENT = f"ForeverAddonManager/{APP_VERSION}"

# Selbst-Update über GitHub-Releases dieses Repos
UPDATE_REPO = "Artithefax/forever-addon-manager"
EXE_ASSET = "ForeverAddonManager.exe"

# Link-Buttons in der Kopfzeile (Text, Adresse) – hier einfach anpassbar
HEADER_LINKS = [
    ("CurseForge", "https://www.curseforge.com/wow"),
    ("GitHub", "https://github.com/search?q=wow+forever+addon&type=repositories&s=updated"),
    ("Projektseite", "https://github.com/Artithefax/forever-addon-manager"),
]

# --------------------------------------------------------------------------- #
# Konfiguration
# --------------------------------------------------------------------------- #

def config_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    d = Path(base) / "ForeverAddonManager"
    d.mkdir(parents=True, exist_ok=True)
    return d


CONFIG_FILE = config_dir() / "config.json"
BACKUP_DIR = config_dir() / "backups"
LOG_FILE = config_dir() / "manager.log"
AUTO_BACKUPS_KEEP = 10

log = logging.getLogger("fam")


def setup_logging() -> None:
    if log.handlers:
        return
    h = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=512 * 1024, backupCount=3,
                                             encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s",
                                     "%Y-%m-%d %H:%M:%S"))
    log.addHandler(h)
    log.setLevel(logging.INFO)


def load_config() -> dict:
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, json.JSONDecodeError):
        cfg = {}
    cfg.setdefault("wow_root", "")
    cfg.setdefault("flavor", "")
    cfg.setdefault("sources", {})   # flavor -> {source_id: {...}}
    cfg.setdefault("github_token", "")
    cfg.setdefault("sets", {})      # flavor -> {setname: [ordner, ...]}
    cfg.setdefault("auto_backup", True)
    cfg.setdefault("check_on_start", True)
    cfg.setdefault("self_update_check", True)
    cfg.setdefault("skip_manager_version", "")
    cfg.setdefault("bundles", {})        # flavor -> [[ordner, ...], ...] gemeinsam installiert
    cfg.setdefault("group_addons", True)
    return cfg


def save_config(cfg: dict) -> None:
    tmp = CONFIG_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    tmp.replace(CONFIG_FILE)

# --------------------------------------------------------------------------- #
# WoW-Installation erkennen
# --------------------------------------------------------------------------- #

DEFAULT_ROOTS = [
    r"C:\Program Files (x86)\World of Warcraft",
    r"C:\Program Files\World of Warcraft",
    r"D:\World of Warcraft",
    r"D:\Games\World of Warcraft",
    r"E:\World of Warcraft",
    "/Applications/World of Warcraft",
]

FLAVOR_RE = re.compile(r"^_[a-z0-9_]+_$", re.IGNORECASE)


def find_wow_root() -> str:
    for r in DEFAULT_ROOTS:
        if Path(r).is_dir() and list_flavors(r):
            return r
    return ""


def list_flavors(root: str) -> list[str]:
    """Liefert Unterordner wie _retail_, _classic_era_, _forever_."""
    p = Path(root)
    if not p.is_dir():
        return []
    out = []
    for child in sorted(p.iterdir()):
        if child.is_dir() and FLAVOR_RE.match(child.name):
            out.append(child.name)
    # Forever nach vorne sortieren (in der Beta-Phase liegt Forever in _classic_beta_)
    def prio(n: str) -> int:
        n = n.lower()
        if "forever" in n:
            return 0
        if n == "_classic_beta_":
            return 1
        return 2
    out.sort(key=lambda n: (prio(n), n))
    return out


def flavor_label(flavor: str) -> str:
    """Anzeigename für das Auswahlfeld."""
    n = flavor.lower()
    if "forever" in n or n == "_classic_beta_":
        return f"{flavor}  (Forever)"
    return flavor

def flavor_product(flavor: str) -> str:
    """_retail_ -> wow, _classic_beta_ -> wow_classic_beta, _forever_ -> wow_forever"""
    n = flavor.strip("_").lower()
    return "wow" if n == "retail" else f"wow_{n}"


def read_game_version(root: str, flavor: str) -> tuple[str, int] | None:
    """Liest Spielversion und Interface-Nummer aus der .build.info (z. B. 1.15.8 -> 11508)."""
    info = Path(root) / ".build.info"
    try:
        lines = [l for l in info.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
    except OSError:
        return None
    if len(lines) < 2:
        return None
    cols = [c.split("!")[0].strip().lower() for c in lines[0].split("|")]
    if "version" not in cols or "product" not in cols:
        return None
    iv, ip = cols.index("version"), cols.index("product")
    want = flavor_product(flavor)
    for line in lines[1:]:
        vals = line.split("|")
        if len(vals) <= max(iv, ip) or vals[ip].strip().lower() != want:
            continue
        m = re.match(r"(\d+)\.(\d+)\.(\d+)", vals[iv].strip())
        if m:
            a, b, c = (int(x) for x in m.groups())
            return f"{a}.{b}.{c}", a * 10000 + b * 100 + c
    return None


def find_wow_exe(root: str, flavor: str) -> Path | None:
    """Sucht die Spiel-.exe im Versionsordner (z. B. WowClassicB.exe in _classic_beta_)."""
    folder = Path(root) / flavor
    if not root or not flavor or not folder.is_dir():
        return None
    skip = ("error", "crash", "launcher", "update", "repair", "uninstall")
    exes = [e for e in folder.glob("*.exe")
            if e.name.lower().startswith("wow") and not any(x in e.name.lower() for x in skip)]
    if not exes:
        return None
    return max(exes, key=lambda e: e.stat().st_size)   # die eigentliche Spieldatei ist die größte


def running_wow(root: str, flavor: str) -> list[str]:
    """Liefert laufende WoW-Prozesse (nur Windows)."""
    if not sys.platform.startswith("win"):
        return []
    exes = {e.name.lower() for e in (Path(root) / flavor).glob("*.exe")
            if e.name.lower().startswith("wow")} if root and flavor else set()
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True,
                             creationflags=0x08000000, timeout=10).stdout   # ohne Konsolenfenster
    except (OSError, subprocess.SubprocessError):
        return []
    names = {l.split('","')[0].strip('"').lower() for l in out.splitlines() if l.strip()}
    if exes:
        return sorted(names & exes)
    return sorted(n for n in names if n.startswith("wow") and n.endswith(".exe"))

# --------------------------------------------------------------------------- #
# TOC-Parsing
# --------------------------------------------------------------------------- #

COLOR_RE = re.compile(r"\|c[0-9a-fA-F]{8}|\|r|\|T[^|]*\|t|\|A[^|]*\|a")


def clean_wow_text(s: str) -> str:
    return COLOR_RE.sub("", s).strip()


def pick_toc(folder: Path, flavor: str, game_if: int | None = None) -> Path | None:
    """Wählt die passende .toc-Datei (beachtet Suffixe wie _Vanilla, -Classic, _Forever)."""
    tocs = [t for t in folder.glob("*.toc")]
    if not tocs:
        return None
    name = folder.name.lower()
    fl = flavor.strip("_").lower()
    suffix_pref = []
    if "forever" in fl or fl == "classic_beta":
        suffix_pref = ["forever", "vanilla", "classic"]
    elif "classic" in fl:
        suffix_pref = ["vanilla", "classic", "cata", "mists", "wrath"]
    elif "retail" in fl:
        suffix_pref = ["mainline", "retail"]

    def score(t: Path) -> tuple:
        stem = t.stem.lower()
        if not stem.startswith(name):
            return (3, stem)
        rest = stem[len(name):].lstrip("_-")
        if rest == "":
            return (1, stem)
        if rest in suffix_pref:
            return (0, suffix_pref.index(rest))
        return (2, stem)

    ordered = sorted(tocs, key=score)
    if game_if:
        # Bevorzugt die .toc, deren Interface-Liste die Spielversion enthält – so wählt auch
        # WoW selbst. Erst danach greift die Reihenfolge nach Namens-Suffix.
        for t in ordered:
            if not t.stem.lower().startswith(name):
                continue
            nums = re.findall(r"\d{4,6}", parse_toc(t).get("interface", ""))
            if str(game_if) in nums:
                return t
    return ordered[0]


def parse_toc(path: Path) -> dict:
    meta = {}
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return meta
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("##"):
            continue
        body = line[2:].strip()
        if ":" not in body:
            continue
        key, val = body.split(":", 1)
        meta[key.strip().lower()] = val.strip()
    return meta


def toc_deps(meta: dict) -> list[str]:
    raw = ",".join(meta.get(k, "") for k in ("dependencies", "requireddeps", "dep"))
    return [d.strip() for d in raw.split(",") if d.strip()]


class Addon:
    def __init__(self, folder: Path, enabled: bool, flavor: str, game_if: int | None = None):
        self.folder = folder
        self.name = folder.name
        self.enabled = enabled
        toc = pick_toc(folder, flavor, game_if)
        self.toc_name = toc.name if toc else ""
        self.meta = parse_toc(toc) if toc else {}
        self.valid = toc is not None
        self.title = clean_wow_text(self.meta.get("title", self.name))
        self.version = self.meta.get("version", "")
        self.interface = self.meta.get("interface", "")
        self.interfaces = [int(x) for x in re.findall(r"\d{4,6}", self.interface)]
        self.author = clean_wow_text(self.meta.get("author", ""))
        self.deps = toc_deps(self.meta)

# --------------------------------------------------------------------------- #
# Dateioperationen
# --------------------------------------------------------------------------- #

def addons_dir(root: str, flavor: str) -> Path:
    return Path(root) / flavor / "Interface" / "AddOns"


def disabled_dir(root: str, flavor: str) -> Path:
    return Path(root) / flavor / "Interface" / "AddOns_Disabled"


def scan_addons(root: str, flavor: str, game_if: int | None = None) -> list[Addon]:
    result = []
    for d, enabled in ((addons_dir(root, flavor), True), (disabled_dir(root, flavor), False)):
        if not d.is_dir():
            continue
        for child in d.iterdir():
            if child.is_dir() and not child.name.startswith("."):
                result.append(Addon(child, enabled, flavor, game_if))
    result.sort(key=lambda a: a.name.lower())
    return result


def safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    """Entpacken mit Schutz gegen Pfade außerhalb des Zielordners (Zip-Slip)."""
    dest = dest.resolve()
    for member in zf.infolist():
        target = (dest / member.filename).resolve()
        if dest not in target.parents and target != dest:
            raise ValueError(f"Unsicherer Pfad im Archiv: {member.filename}")
    zf.extractall(dest)


def find_addon_folders(base: Path, max_depth: int = 3) -> list[Path]:
    """Sucht Ordner, die eine .toc mit gleichem Namen enthalten."""
    found = []

    def walk(p: Path, depth: int):
        if depth > max_depth:
            return
        tocs = [t.stem.lower() for t in p.glob("*.toc")]
        name = p.name.lower()
        if any(t == name or t.startswith(name + "_") or t.startswith(name + "-") for t in tocs):
            found.append(p)
            return  # Unterordner eines Addons nicht weiter durchsuchen
        for c in p.iterdir():
            if c.is_dir() and not c.name.startswith((".", "__MACOSX")):
                walk(c, depth + 1)

    walk(base, 0)
    return found


def install_zip(zip_path: Path, root: str, flavor: str) -> list[str]:
    target = addons_dir(root, flavor)
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_p = Path(tmp)
        with zipfile.ZipFile(zip_path) as zf:
            safe_extract(zf, tmp_p)
        folders = find_addon_folders(tmp_p)
        if not folders:
            raise ValueError("Im Archiv wurde kein Addon (Ordner mit passender .toc) gefunden.")
        installed = []
        for f in folders:
            # Ordnernamen von GitHub-Zipballs wie "Repo-1.2.3" bereinigen
            name = f.name
            tocs = [t.stem for t in f.glob("*.toc")]
            if tocs and not any(t.lower().startswith(name.lower()) for t in tocs):
                name = re.split(r"[_-]", tocs[0])[0]
            dest = target / name
            dis = disabled_dir(root, flavor) / name
            if dis.exists():
                shutil.rmtree(dis)
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(f, dest)
            installed.append(name)
        return installed


def set_enabled(addon: Addon, root: str, flavor: str, enable: bool) -> None:
    src = addon.folder
    dst_dir = addons_dir(root, flavor) if enable else disabled_dir(root, flavor)
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / addon.name
    if dst.exists():
        raise FileExistsError(f"{dst} existiert bereits.")
    shutil.move(str(src), str(dst))


BACKUP_RE_TMPL = r"^WTF{flavor}(\d{{8}}_\d{{6}})(?:_(auto|vorher))?\.zip$"


def backup_wtf(root: str, flavor: str, kind: str = "") -> Path:
    """Sichert den WTF-Ordner. kind: '' (manuell), 'auto' (vor Updates), 'vorher' (vor Wiederherstellung)."""
    wtf = Path(root) / flavor / "WTF"
    if not wtf.is_dir():
        raise FileNotFoundError(f"Kein WTF-Ordner gefunden: {wtf}")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = BACKUP_DIR / (f"WTF{flavor}{stamp}" + (f"_{kind}" if kind else ""))
    path = Path(shutil.make_archive(str(base), "zip", root_dir=wtf.parent, base_dir="WTF"))
    if kind == "auto":
        prune_auto_backups(flavor)
    return path


def list_backups(flavor: str) -> list[dict]:
    rx = re.compile(BACKUP_RE_TMPL.format(flavor=re.escape(flavor)))
    out = []
    if BACKUP_DIR.is_dir():
        for f in BACKUP_DIR.iterdir():
            m = rx.match(f.name)
            if m:
                out.append({"path": f, "when": datetime.strptime(m.group(1), "%Y%m%d_%H%M%S"),
                            "kind": m.group(2) or "", "size": f.stat().st_size})
    out.sort(key=lambda b: b["when"], reverse=True)
    return out


def prune_auto_backups(flavor: str, keep: int = AUTO_BACKUPS_KEEP) -> None:
    autos = [b for b in list_backups(flavor) if b["kind"] == "auto"]
    for b in autos[keep:]:
        b["path"].unlink(missing_ok=True)


def restore_backup(backup: Path, root: str, flavor: str) -> Path | None:
    """Stellt einen WTF-Stand wieder her; der aktuelle Stand wird vorher gesichert."""
    wtf = Path(root) / flavor / "WTF"
    safety = backup_wtf(root, flavor, "vorher") if wtf.is_dir() else None
    with tempfile.TemporaryDirectory(dir=str(Path(root) / flavor)) as tmp:
        tmp_p = Path(tmp)
        with zipfile.ZipFile(backup) as zf:
            safe_extract(zf, tmp_p)
        new = tmp_p / "WTF"
        if not new.is_dir():
            raise ValueError("Das Backup enthält keinen WTF-Ordner.")
        if wtf.exists():
            shutil.rmtree(wtf)
        shutil.move(str(new), str(wtf))
    return safety


# --------------------------------------------------------------------------- #
# Addon-Pakete zum Teilen (.faddons = ZIP mit manifest.json + AddOns/<Ordner>/...)
# --------------------------------------------------------------------------- #

PACK_EXT = ".faddons"
PACK_FORMAT = 1
PACK_MAX_BYTES = 1024 ** 3          # Schutz gegen übergroße/manipulierte Pakete (1 GB entpackt)
SAFE_NAME_RE = re.compile(r"^[\w .+\-!()&'\[\]]+$")


def valid_addon_name(name: str) -> bool:
    return bool(name) and name not in (".", "..") and SAFE_NAME_RE.match(name) is not None


def export_pack(addons: list, sources: dict, flavor: str, dest: Path) -> Path:
    """Schreibt die gewählten Addons inkl. Quellinfos in eine .faddons-Datei."""
    entries = []
    for a in addons:
        entry = {"folder": a.name, "title": a.title, "version": a.version, "source": None}
        for repo, s in sources.items():
            if a.name in s.get("folders", []):
                entry["source"] = {"type": "github", "repo": repo, "tag": s.get("tag", ""),
                                   "folders": s.get("folders", [])}
                break
        entries.append(entry)
    manifest = {
        "format": PACK_FORMAT,
        "app": f"{APP_NAME} {APP_VERSION}",
        "flavor": flavor,
        "created": datetime.now().isoformat(timespec="seconds"),
        "addons": entries,
    }
    tmp = dest.with_suffix(dest.suffix + ".part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        for a in addons:
            for f in sorted(a.folder.rglob("*")):
                if f.is_file():
                    rel = f.relative_to(a.folder).as_posix()
                    zf.write(f, f"AddOns/{a.name}/{rel}")
    tmp.replace(dest)
    return dest


def read_pack(path: Path) -> dict:
    """Liest und prüft das Manifest eines Pakets."""
    try:
        with zipfile.ZipFile(path) as zf:
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            total = sum(i.file_size for i in zf.infolist())
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError, UnicodeDecodeError):
        raise ValueError("Das ist kein gültiges Addon-Paket.")
    if manifest.get("format") != PACK_FORMAT:
        raise ValueError("Paket stammt aus einer anderen Manager-Version (Format "
                         f"{manifest.get('format')}). Bitte Manager aktualisieren.")
    if total > PACK_MAX_BYTES:
        raise ValueError("Paket ist zu groß (über 1 GB entpackt).")
    addons = manifest.get("addons")
    if not isinstance(addons, list) or not addons:
        raise ValueError("Paket enthält keine Addons.")
    for e in addons:
        if not isinstance(e, dict) or not valid_addon_name(str(e.get("folder", ""))):
            raise ValueError(f"Ungültiger Addon-Name im Paket: {e!r}")
    return manifest


def import_pack(path: Path, root: str, flavor: str, folders: list[str]) -> list[str]:
    """Installiert die angegebenen Addon-Ordner aus einem Paket."""
    wanted = set(folders)
    target = addons_dir(root, flavor)
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(path) as zf:
        tmp_p = Path(tmp).resolve()
        members = []
        for m in zf.infolist():
            parts = m.filename.split("/")
            if len(parts) >= 3 and parts[0] == "AddOns" and parts[1] in wanted:
                dest = (tmp_p / m.filename).resolve()
                if tmp_p not in dest.parents:
                    raise ValueError(f"Unsicherer Pfad im Paket: {m.filename}")
                members.append(m)
        zf.extractall(tmp_p, members=members)
        installed = []
        for name in folders:
            src = tmp_p / "AddOns" / name
            if not src.is_dir():
                continue
            for old in (target / name, disabled_dir(root, flavor) / name):
                if old.exists():
                    shutil.rmtree(old)
            shutil.copytree(src, target / name)
            installed.append(name)
    return installed


def open_in_explorer(path: Path) -> None:
    if sys.platform.startswith("win"):
        os.startfile(str(path))  # noqa
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])

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
    raise ValueError("Bitte 'owner/repo' oder eine GitHub-URL angeben.")


def http_get(url: str, token: str = "", accept: str = "application/vnd.github+json") -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


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


def github_latest(repo: str, token: str = "") -> dict:
    data = json.loads(http_get(f"https://api.github.com/repos/{repo}/releases/latest", token))
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


def github_install_asset(asset: dict, root: str, flavor: str, token: str = "") -> list[str]:
    blob = http_get(asset["url"], token, accept="application/octet-stream")
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp.write(blob)
        tmp_path = Path(tmp.name)
    try:
        return install_zip(tmp_path, root, flavor)
    finally:
        tmp_path.unlink(missing_ok=True)

# --------------------------------------------------------------------------- #
# Gruppierung zusammengehöriger Addon-Ordner
# --------------------------------------------------------------------------- #

LIB_RE = re.compile(r"^(!|lib|ace\d|ace-|ace_|callbackhandler|taintless)", re.IGNORECASE)


def name_key(name: str) -> str:
    """Präfix vor dem ersten Trenner: Bagnon_Bank -> bagnon, DBM-Core -> dbm."""
    parts = re.split(r"[_\-]", name, maxsplit=1)
    return parts[0].lower() if len(parts) > 1 else ""


def compute_groups(addons: list, bundles: list[list[str]]) -> list[dict]:
    """Fasst zusammengehörige Ordner zusammen.

    Hinweise (in dieser Reihenfolge): gemeinsam installiert (ZIP/GitHub-Release),
    Namens-Präfix (Bagnon_Bank -> Bagnon, QuestieDB -> Questie), gemeinsames Präfix vor
    einem Trenner (Leatrix_Maps + Leatrix_Plus) und Abhängigkeiten, die nur eine einzige
    Gruppe benötigt (BagBrother -> Bagnon). Bibliotheken bleiben eigenständig.
    """
    names = {a.name.lower(): a for a in addons}
    parent = {n: n for n in names}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        if a in parent and b in parent:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

    is_lib = {n: bool(LIB_RE.match(n)) for n in names}
    # 1) gemeinsam installiert
    for bundle in bundles:
        members = [m.lower() for m in bundle if m.lower() in names]
        for m in members[1:]:
            union(members[0], m)
    # 2) Präfix: questiedb beginnt mit questie, bagnon_bank mit bagnon
    for a in names:
        if len(a) < 4 or is_lib[a]:
            continue
        for b in names:
            if b != a and not is_lib[b] and b.startswith(a):
                union(a, b)
    # 3) gemeinsames Präfix vor Trenner (mindestens zwei Ordner)
    keys: dict[str, list[str]] = {}
    for n in names:
        k = name_key(n)
        if len(k) >= 3 and not is_lib[n]:
            keys.setdefault(k, []).append(n)
    for members in keys.values():
        for m in members[1:]:
            union(members[0], m)
    # 4) Abhängigkeit, die nur von einer Gruppe gebraucht wird
    for _ in range(2):
        users: dict[str, set] = {}
        for n, a in names.items():
            for d in a.deps:
                d = d.lower()
                if d in names and d != n and not is_lib[d]:
                    users.setdefault(d, set()).add(find(n))
        for d, groups in users.items():
            if len(groups) == 1:
                union(next(iter(groups)), d)

    clusters: dict[str, list] = {}
    for n in names:
        clusters.setdefault(find(n), []).append(names[n])

    result = []
    for members in clusters.values():
        members.sort(key=lambda a: a.name.lower())
        low = [m.name.lower() for m in members]
        root = None
        if len(members) > 1:
            # Hauptordner: der, mit dem die meisten anderen beginnen; sonst der meistgebrauchte
            best = max(members, key=lambda m: (sum(1 for o in low if o != m.name.lower()
                                                   and o.startswith(m.name.lower())),
                                               sum(1 for o in members if m.name.lower()
                                                   in (d.lower() for d in o.deps)),
                                               -len(m.name)))
            if any(o != best.name.lower() and o.startswith(best.name.lower()) for o in low) or \
                    any(best.name.lower() in (d.lower() for d in o.deps) for o in members):
                root = best
        if root:
            title = root.name
        elif len(members) > 1:
            prefix = os.path.commonprefix([m.name for m in members]).rstrip("_- ")
            title = prefix if len(prefix) >= 3 else members[0].name
        else:
            title = members[0].name
        result.append({"title": title, "root": root, "members": members})
    return result


# --------------------------------------------------------------------------- #
# Selbst-Update des Managers
# --------------------------------------------------------------------------- #

def parse_version(v: str) -> tuple[int, int, int]:
    nums = [int(x) for x in re.findall(r"\d+", v)[:3]]
    return tuple(nums + [0] * (3 - len(nums)))  # type: ignore[return-value]


def is_frozen() -> bool:
    """True, wenn der Manager als .exe (PyInstaller) läuft."""
    return bool(getattr(sys, "frozen", False))


class NoReleaseYet(Exception):
    """Im Repo gibt es (noch) kein veröffentlichtes Release."""


def check_self_update(token: str = "") -> dict | None:
    """Liefert die Release-Infos, wenn auf GitHub eine neuere Version liegt."""
    try:
        info = github_latest(UPDATE_REPO, token)
    except urllib.error.HTTPError as e:
        if e.code == 404:      # GitHub meldet 404, solange kein Release veröffentlicht ist
            raise NoReleaseYet() from e
        raise
    if parse_version(info["tag"]) > parse_version(APP_VERSION):
        return info
    return None


def self_update_asset(info: dict) -> dict | None:
    for a in info.get("all_assets", []):
        if a["name"].lower() == EXE_ASSET.lower():
            return a
    return None


def download_self_update(info: dict, token: str = "") -> Path:
    """Lädt die neue .exe neben die laufende und prüft sie (MZ-Header, ggf. SHA-256)."""
    asset = self_update_asset(info)
    if not asset:
        raise ValueError("Das Release enthält noch keine ForeverAddonManager.exe.")
    blob = http_get(asset["url"], token, accept="application/octet-stream")
    if len(blob) < 1_000_000 or blob[:2] != b"MZ":
        raise ValueError("Die heruntergeladene Datei ist keine gültige Windows-Anwendung.")
    sha_asset = next((a for a in info.get("all_assets", [])
                      if a["name"].lower() == (EXE_ASSET + ".sha256").lower()), None)
    if sha_asset:
        expected = http_get(sha_asset["url"], token, accept="application/octet-stream")
        expected = expected.decode("ascii", "replace").split()[0].strip().lower()
        actual = hashlib.sha256(blob).hexdigest()
        if expected != actual:
            raise ValueError("Prüfsumme stimmt nicht – Download beschädigt. Bitte später erneut versuchen.")
    target = Path(sys.executable).with_name("ForeverAddonManager.new.exe")
    try:
        target.write_bytes(blob)
    except PermissionError:
        raise PermissionError(f"Keine Schreibrechte in {target.parent}. Verschiebe den Manager z. B. "
                              "in deinen Benutzerordner oder lade das Update von der Release-Seite.")
    return target


def launch_replacer(new_exe: Path) -> None:
    """Startet ein kleines Skript, das nach dem Beenden die alte .exe ersetzt und neu startet."""
    old_exe = Path(sys.executable)
    bat = Path(tempfile.gettempdir()) / f"fam_update_{os.getpid()}.bat"
    script = f"""@echo off
chcp 65001 >nul
set "PID={os.getpid()}"
:wait
tasklist /FI "PID eq %PID%" 2>nul | find "%PID%" >nul && (ping -n 2 127.0.0.1 >nul & goto wait)
set /a n=0
:move
move /Y "{new_exe}" "{old_exe}" >nul 2>&1 && goto start
set /a n+=1
if %n% GEQ 30 goto start
ping -n 2 127.0.0.1 >nul
goto move
:start
start "" "{old_exe}"
(goto) 2>nul & del "%~f0"
"""
    bat.write_text(script, encoding="utf-8")
    subprocess.Popen(["cmd", "/c", str(bat)], close_fds=True,
                     creationflags=0x08000000 | 0x00000008)   # ohne Fenster, losgelöst


def cleanup_self_update() -> None:
    """Räumt ein liegengebliebenes .new.exe nach einem abgebrochenen Update weg."""
    if is_frozen():
        Path(sys.executable).with_name("ForeverAddonManager.new.exe").unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# Theme (Braun/Gold im Stil eines Fantasy-MMO-Interfaces)
# --------------------------------------------------------------------------- #

THEME = {
    # Farbwerte nach dem Stil der Vorlage (dunkles Petrol-Schwarz, Bernstein-Gold, Pergament)
    "bg":          "#0d1212",   # Seitenhintergrund
    "header":      "#090e0e",   # Kopf- und Fußzeile
    "panel":       "#131c1c",   # Kästen
    "row_a":       "#0f1515",
    "row_b":       "#121a1a",
    "active":      "#1f241e",   # aktive Zeile / Hover
    "pill":        "#222722",
    "line":        "#1e2626",   # feine Trennlinien
    "border":      "#67593d",   # gedämpfter Goldrahmen
    "border_soft": "#3b3a2c",
    "field_line":  "#6e715f",   # Rahmen von Eingabefeldern
    "gold":        "#e6bb70",   # Akzent
    "gold_deep":   "#c9963f",
    "text":        "#f3f0e8",   # Pergament-Weiß
    "text_dim":    "#b4bbb9",
    "text_faint":  "#6b7372",
    "btn_primary": "#644e2b",   # Bronze-Button
    "btn_primary_hi": "#7a5f34",
    "btn_text":    "#fff7e6",
    "crest":       "#16242c",   # Wappen-Nachtblau
    "warn":        "#e69633",
    "update":      "#7ddc6a",
    "old":         "#d6c37a",   # Interface passt nicht zur Spielversion
}


def spaced(text: str) -> str:
    """Großbuchstaben mit Sperrung wie bei den Kategorie-Labels (Tk kennt kein letter-spacing)."""
    return "\u2009".join(text.upper())


def pick_font(root, candidates, size, weight="normal"):
    import tkinter.font as tkfont
    families = set(tkfont.families(root))
    for f in candidates:
        if f in families:
            return (f, size, weight)
    return ("TkDefaultFont", size, weight)


def apply_theme(root) -> dict:
    t = THEME
    style = ttk.Style(root)
    style.theme_use("clam")      # einziges Theme, das Farben überall zuverlässig übernimmt
    serif = ["Georgia", "Cambria", "Book Antiqua", "Times New Roman"]
    sans = ["Segoe UI", "Source Sans 3", "Helvetica", "Arial"]
    fonts = {
        "base":  pick_font(root, sans, 10),
        "small": pick_font(root, sans, 9),
        "bold":  pick_font(root, ["Segoe UI Semibold"] + sans, 10, "bold"),
        "label": pick_font(root, ["Segoe UI Semibold"] + sans, 8, "bold"),
        "brand": pick_font(root, serif, 9, "bold"),
        "title": pick_font(root, serif, 22, "bold"),
        "sub":   pick_font(root, serif, 10, "italic"),
    }
    root.configure(bg=t["bg"])

    style.configure(".", background=t["bg"], foreground=t["text"], font=fonts["base"],
                    bordercolor=t["border_soft"], darkcolor=t["bg"], lightcolor=t["bg"],
                    troughcolor=t["bg"], focuscolor=t["gold"],
                    selectbackground=t["active"], selectforeground=t["gold"],
                    fieldbackground=t["panel"], insertcolor=t["gold"])
    style.configure("TFrame", background=t["bg"])
    style.configure("Panel.TFrame", background=t["panel"])
    style.configure("Header.TFrame", background=t["header"])
    style.configure("TLabel", background=t["bg"], foreground=t["text_dim"])
    style.configure("Panel.TLabel", background=t["panel"], foreground=t["text_dim"])
    style.configure("Section.TLabel", background=t["bg"], foreground=t["gold"], font=fonts["label"])
    style.configure("Count.TLabel", background=t["bg"], foreground=t["text_dim"])
    style.configure("Hint.TLabel", background=t["bg"], foreground=t["text_faint"], font=fonts["small"])
    style.configure("TCheckbutton", background=t["panel"], foreground=t["text_dim"],
                    indicatorbackground=t["bg"], indicatorforeground=t["gold"],
                    indicatorcolor=t["bg"], bordercolor=t["field_line"], focuscolor=t["panel"],
                    upperbordercolor=t["field_line"], lowerbordercolor=t["field_line"])
    style.configure("Plain.TCheckbutton", background=t["bg"], foreground=t["text_dim"],
                    focuscolor=t["bg"])
    style.map("Plain.TCheckbutton", background=[("active", t["bg"])],
              foreground=[("active", t["gold"])],
              indicatorcolor=[("selected", t["gold"]), ("pressed", t["active"])],
              indicatorbackground=[("selected", t["gold"])])
    style.map("TCheckbutton", background=[("active", t["panel"])],
              foreground=[("active", t["gold"])],
              indicatorcolor=[("selected", t["gold"]), ("pressed", t["active"])],
              indicatorbackground=[("selected", t["gold"])])
    style.configure("Status.TLabel", background=t["header"], foreground=t["text_dim"],
                    padding=(12, 5))

    # Sekundär-Buttons: ruhig, feiner Goldrahmen, Text wird beim Hover golden
    style.configure("TButton", background=t["panel"], foreground=t["text"], font=fonts["base"],
                    bordercolor=t["border"], lightcolor=t["panel"], darkcolor=t["panel"],
                    padding=(12, 5), relief="solid", borderwidth=1)
    style.map("TButton",
              background=[("pressed", t["pill"]), ("active", t["active"])],
              foreground=[("disabled", t["text_faint"]), ("active", t["gold"])],
              bordercolor=[("active", t["gold"])],
              lightcolor=[("active", t["active"])], darkcolor=[("active", t["active"])])
    # Haupt-Buttons: Bronze mit hellem Goldrand (wie "Suchen")
    style.configure("Primary.TButton", background=t["btn_primary"], foreground=t["btn_text"],
                    bordercolor=t["gold"], lightcolor=t["btn_primary"],
                    darkcolor=t["btn_primary"], font=fonts["bold"])
    style.map("Primary.TButton",
              background=[("pressed", t["btn_primary"]), ("active", t["btn_primary_hi"])],
              foreground=[("active", t["btn_text"])],
              lightcolor=[("active", t["btn_primary_hi"])],
              darkcolor=[("active", t["btn_primary_hi"])])

    # Spielen-Button in der Kopfzeile: Bronze mit Goldrand
    style.configure("Play.TButton", background=t["btn_primary"], foreground=t["btn_text"],
                    bordercolor=t["gold"], lightcolor=t["btn_primary"], darkcolor=t["btn_primary"],
                    font=fonts["bold"], padding=(16, 3))
    style.map("Play.TButton",
              background=[("pressed", t["btn_primary"]), ("active", t["btn_primary_hi"]),
                          ("disabled", t["panel"])],
              foreground=[("disabled", t["text_faint"])],
              bordercolor=[("disabled", t["border_soft"])],
              lightcolor=[("active", t["btn_primary_hi"])], darkcolor=[("active", t["btn_primary_hi"])])

    # Kleine Link-Buttons in der Kopfzeile
    style.configure("Link.TButton", background=t["header"], foreground=t["text_dim"],
                    font=fonts["small"], bordercolor=t["border_soft"], lightcolor=t["header"],
                    darkcolor=t["header"], padding=(10, 3))
    style.map("Link.TButton",
              background=[("pressed", t["panel"]), ("active", t["panel"])],
              foreground=[("active", t["gold"])],
              bordercolor=[("active", t["gold"])],
              lightcolor=[("active", t["panel"])], darkcolor=[("active", t["panel"])])

    style.configure("TEntry", fieldbackground=t["bg"], foreground=t["text"],
                    bordercolor=t["field_line"], lightcolor=t["bg"], darkcolor=t["bg"],
                    padding=5)
    style.map("TEntry", bordercolor=[("focus", t["gold"])])
    style.configure("TCombobox", fieldbackground=t["bg"], background=t["bg"],
                    foreground=t["text"], arrowcolor=t["gold"], bordercolor=t["field_line"],
                    lightcolor=t["bg"], darkcolor=t["bg"], padding=4)
    style.map("TCombobox", fieldbackground=[("readonly", t["bg"])],
              foreground=[("readonly", t["text"])],
              selectbackground=[("readonly", t["bg"])],
              selectforeground=[("readonly", t["text"])],
              background=[("active", t["active"])],
              bordercolor=[("focus", t["gold"]), ("active", t["gold"])])
    root.option_add("*TCombobox*Listbox.background", t["panel"])
    root.option_add("*TCombobox*Listbox.foreground", t["text"])
    root.option_add("*TCombobox*Listbox.selectBackground", t["active"])
    root.option_add("*TCombobox*Listbox.selectForeground", t["gold"])
    root.option_add("*TCombobox*Listbox.font", fonts["base"])

    style.configure("Treeview", background=t["row_a"], fieldbackground=t["row_a"],
                    foreground=t["text"], rowheight=30, bordercolor=t["border_soft"],
                    lightcolor=t["row_a"], darkcolor=t["row_a"], borderwidth=1)
    style.map("Treeview", background=[("selected", t["active"])],
              foreground=[("selected", t["gold"])])
    style.configure("Treeview.Heading", background=t["header"], foreground=t["gold"],
                    font=fonts["label"], bordercolor=t["line"], lightcolor=t["header"],
                    darkcolor=t["header"], relief="flat", padding=(8, 7))
    style.map("Treeview.Heading", background=[("active", t["panel"])])

    style.configure("Vertical.TScrollbar", background=t["panel"], troughcolor=t["bg"],
                    bordercolor=t["bg"], arrowcolor=t["gold"],
                    lightcolor=t["panel"], darkcolor=t["panel"], gripcount=0)
    style.map("Vertical.TScrollbar", background=[("active", t["border"])])
    return fonts


def draw_emblem(cv, x0: int, y0: int, s: int) -> None:
    """Eigenes Wappen: Schild mit gekreuzten Schwertern und Edelstein."""
    t = THEME

    def p(fx, fy):
        return x0 + fx * s, y0 + fy * s

    shield = [p(.12, .10), p(.50, .02), p(.88, .10), p(.88, .48),
              p(.72, .78), p(.50, .96), p(.28, .78), p(.12, .48)]
    flat = [c for pt in shield for c in pt]
    cv.create_polygon(flat, fill=t["crest"], outline=t["gold"], width=max(2, s // 24))
    inner = [p(.20, .16), p(.50, .10), p(.80, .16), p(.80, .47),
             p(.67, .72), p(.50, .87), p(.33, .72), p(.20, .47)]
    cv.create_polygon([c for pt in inner for c in pt], fill="", outline=t["border"],
                      width=max(1, s // 40))
    w = max(2, s // 16)
    for (ax, ay, bx, by, gx1, gy1, gx2, gy2) in (
            (.26, .22, .74, .74, .62, .70, .74, .58),
            (.74, .22, .26, .74, .26, .58, .38, .70)):
        cv.create_line(*p(ax, ay), *p(bx, by), fill=t["text"], width=w, capstyle="round")
        cv.create_line(*p(gx1, gy1), *p(gx2, gy2), fill=t["gold"], width=w, capstyle="round")
    r = s * .07
    cx, cy = p(.50, .44)
    cv.create_oval(cx - r, cy - r, cx + r, cy + r, fill=t["gold"], outline=t["gold_deep"])


def make_icon(root, n: int = 32):
    """Fenstersymbol als Pixelgrafik (ohne Zusatzbibliotheken)."""
    t = THEME
    img = tk.PhotoImage(master=root, width=n, height=n)
    mid = (n - 1) / 2
    for y in range(n):
        fy = y / (n - 1)
        hw = (n * .40) if fy < .5 else (n * .40) * max(0.0, (1 - fy) / .5)
        row = []
        for x in range(n):
            dx = abs(x - mid)
            if dx > hw or fy < .04:
                row.append(None)
            elif dx > hw - 2 or fy < .10:
                row.append(t["gold"])
            elif abs((x - mid) - (y - n * .48)) < 1.2 or abs((x - mid) + (y - n * .48)) < 1.2:
                row.append(t["text"])
            else:
                row.append(t["crest"])
        for x, c in enumerate(row):
            if c is None:
                img.put(t["header"], (x, y))
                img.transparency_set(x, y, True)
            else:
                img.put(c, (x, y))
    return img


# --------------------------------------------------------------------------- #
# Dialoge im App-Design
# --------------------------------------------------------------------------- #

class Dialog(tk.Toplevel):
    """Modaler Dialog im Theme. Unterklassen füllen self.body und setzen self.result."""

    def __init__(self, parent, title: str, heading: str, width: int = 560, height: int = 420):
        super().__init__(parent)
        t = THEME
        self.parent = parent
        self.result = None
        self.title(title)
        self.configure(bg=t["bg"])
        self.transient(parent)
        self.geometry(f"{width}x{height}")
        self.minsize(380, 260)
        ttk.Label(self, text=spaced(heading), style="Section.TLabel",
                  padding=(18, 16, 18, 6)).pack(anchor="w")
        self.buttons = ttk.Frame(self, padding=(18, 12, 18, 16))
        self.buttons.pack(fill="x", side="bottom")      # zuerst packen, damit sie nie verdeckt werden
        self.body = ttk.Frame(self, padding=(18, 0, 18, 0))
        self.body.pack(fill="both", expand=True)
        self.bind("<Escape>", lambda e: self.cancel())
        self.protocol("WM_DELETE_WINDOW", self.cancel)

    def add_button(self, text, cmd, primary=False):
        ttk.Button(self.buttons, text=text, command=cmd,
                   style="Primary.TButton" if primary else "TButton").pack(side="right", padx=(8, 0))

    def listbox(self, items: list[str], height=10) -> tk.Listbox:
        t = THEME
        frame = tk.Frame(self.body, bg=t["border_soft"])
        frame.pack(fill="both", expand=True)
        lb = tk.Listbox(frame, height=height, activestyle="none", exportselection=False,
                        bg=t["row_a"], fg=t["text"], selectbackground=t["active"],
                        selectforeground=t["gold"], highlightthickness=0, borderwidth=0,
                        font=self.parent.fonts["base"])
        for it in items:
            lb.insert("end", "  " + it)
        sb = ttk.Scrollbar(frame, orient="vertical", command=lb.yview)
        lb.configure(yscrollcommand=sb.set)
        lb.pack(side="left", fill="both", expand=True, padx=1, pady=1)
        sb.pack(side="right", fill="y", padx=(0, 1), pady=1)
        return lb

    def text_box(self, content: str) -> tk.Text:
        t = THEME
        frame = tk.Frame(self.body, bg=t["border_soft"])
        frame.pack(fill="both", expand=True)
        txt = tk.Text(frame, wrap="word", bg=t["row_a"], fg=t["text"], insertbackground=t["gold"],
                      highlightthickness=0, borderwidth=0, padx=12, pady=10,
                      font=self.parent.fonts["base"])
        txt.tag_configure("head", foreground=t["gold"], font=self.parent.fonts["bold"],
                          spacing1=8, spacing3=4)
        txt.tag_configure("dim", foreground=t["text_dim"])
        txt.tag_configure("sub", foreground=t["text"], font=self.parent.fonts["bold"],
                          spacing1=6, spacing3=2)
        sb = ttk.Scrollbar(frame, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        txt.pack(side="left", fill="both", expand=True, padx=1, pady=1)
        sb.pack(side="right", fill="y", padx=(0, 1), pady=1)
        return txt

    def show(self):
        self.update_idletasks()
        px = self.parent.winfo_rootx() + (self.parent.winfo_width() - self.winfo_width()) // 2
        py = self.parent.winfo_rooty() + (self.parent.winfo_height() - self.winfo_height()) // 3
        self.geometry(f"+{max(px, 0)}+{max(py, 0)}")
        self.grab_set()
        self.focus_set()
        self.wait_window()
        return self.result

    def cancel(self):
        self.result = None
        self.destroy()


class AssetDialog(Dialog):
    """Auswahl der Release-Datei, wenn ein Autor mehrere Varianten hochlädt."""

    def __init__(self, parent, info: dict, preselect: dict | None):
        super().__init__(parent, APP_NAME, "Datei auswählen", 560, 400)
        ttk.Label(self.body, text=f"{info['repo']}  ·  {info['tag']} bietet mehrere Dateien an. "
                                  "Welche passt zu deiner Spielversion?",
                  wraplength=500, padding=(0, 0, 0, 10)).pack(anchor="w")
        self.assets = info["assets"]
        lines = [f"{a['name']}   ({a['size'] / 1024 / 1024:.1f} MB)" if a.get("size") else a["name"]
                 for a in self.assets]
        self.lb = self.listbox(lines)
        idx = self.assets.index(preselect) if preselect in self.assets else 0
        self.lb.selection_set(idx)
        self.lb.see(idx)
        self.lb.bind("<Double-1>", lambda e: self.ok())
        ttk.Label(self.body, text="Die Auswahl wird für spätere Updates gemerkt.",
                  padding=(0, 8, 0, 0)).pack(anchor="w")
        self.add_button("Installieren", self.ok, primary=True)
        self.add_button("Abbrechen", self.cancel)

    def ok(self):
        sel = self.lb.curselection()
        if sel:
            self.result = self.assets[sel[0]]
            self.destroy()


class UpdateDialog(Dialog):
    """Zeigt verfügbare Updates mit Release-Notes vor dem Aktualisieren."""

    def __init__(self, parent, items: list[dict], auto_backup: bool, heading: str = "",
                 ok_text: str = "Jetzt aktualisieren", cancel_text: str = "Abbrechen", hint: str = ""):
        super().__init__(parent, APP_NAME, heading or f"{len(items)} Update(s) verfügbar", 680, 520)
        txt = self.text_box("")
        for it in items:
            txt.insert("end", f"{it['repo']}   {it['old'] or '?'} → {it['new']}\n", "head")
            if it.get("published"):
                txt.insert("end", f"veröffentlicht am {it['published']}\n", "dim")
            notes = it.get("notes") or "Keine Release-Notes angegeben."
            if len(notes) > 3000:
                notes = notes[:3000] + "\n… (gekürzt, vollständig auf GitHub)"
            for line in notes.splitlines():
                stripped = line.strip()
                if stripped.startswith("#"):
                    txt.insert("end", stripped.lstrip("#").strip() + "\n", "sub")
                    continue
                line = re.sub(r"^(\s*)[-*+] ", r"\1•  ", line)
                line = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m.group(1) or m.group(2), line)
                line = re.sub(r"`([^`]+)`", r"\1", line)
                line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
                txt.insert("end", line + "\n")
            txt.insert("end", "\n")
        txt.configure(state="disabled")
        if hint or auto_backup:
            ttk.Label(self.body, text=hint or "Vor dem Update wird automatisch ein WTF-Backup angelegt.",
                      wraplength=620, padding=(0, 8, 0, 0)).pack(anchor="w")
        self.add_button(ok_text, self.ok, primary=True)
        self.add_button(cancel_text, self.cancel)

    def ok(self):
        self.result = True
        self.destroy()


class BackupDialog(Dialog):
    """Auswahl eines WTF-Backups zum Wiederherstellen."""

    KINDS = {"": "manuell", "auto": "automatisch vor Update", "vorher": "vor Wiederherstellung"}

    def __init__(self, parent, backups: list[dict]):
        super().__init__(parent, APP_NAME, "Backup wiederherstellen", 560, 420)
        ttk.Label(self.body, text="Wähle den Stand, den du zurückholen willst. Dein aktueller "
                                  "WTF-Ordner wird vorher automatisch gesichert.",
                  wraplength=500, padding=(0, 0, 0, 10)).pack(anchor="w")
        self.backups = backups
        lines = [f"{b['when']:%d.%m.%Y  %H:%M}    {self.KINDS.get(b['kind'], b['kind']):<26}"
                 f"{b['size'] / 1024 / 1024:6.1f} MB" for b in backups]
        self.lb = self.listbox(lines)
        self.lb.selection_set(0)
        self.lb.bind("<Double-1>", lambda e: self.ok())
        self.add_button("Wiederherstellen", self.ok, primary=True)
        self.add_button("Abbrechen", self.cancel)
        self.add_button("Ordner öffnen", lambda: open_in_explorer(BACKUP_DIR))

    def ok(self):
        sel = self.lb.curselection()
        if sel:
            self.result = self.backups[sel[0]]
            self.destroy()


# --------------------------------------------------------------------------- #
# Hauptfenster
# --------------------------------------------------------------------------- #

SET_ALL_OFF = "— Alle deaktivieren —"
SET_ALL_ON = "— Alle aktivieren —"


class App(BaseTk):
    COLUMNS = ("status", "name", "title", "version", "interface", "source", "update")
    HEADINGS = {
        "status": "Status", "name": "Ordner", "title": "Titel", "version": "Version",
        "interface": "Interface", "source": "Quelle", "update": "Update",
    }

    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} {APP_VERSION}")
        self.geometry("1180x780")
        self.minsize(900, 520)

        self.cfg = load_config()
        self.addons: list[Addon] = []
        self.updates: dict[str, str] = {}       # repo -> neuester Tag
        self.update_info: dict[str, dict] = {}  # repo -> Release-Infos
        self.game: tuple[str, int] | None = None
        self.jobs: queue.Queue = queue.Queue()
        self.busy = False

        if not self.cfg["wow_root"]:
            self.cfg["wow_root"] = find_wow_root()

        self._build_ui()
        self._load_flavors()
        self.after(100, self._poll_jobs)
        if HAS_DND:
            try:
                self.drop_target_register(DND_FILES)
                self.dnd_bind("<<Drop>>", self.on_drop)
            except Exception as e:  # noqa
                log.warning("Drag & Drop nicht verfügbar: %s", e)
        if self.cfg.get("check_on_start") and self.sources():
            self.after(700, lambda: self.check_updates(silent=True))
        cleanup_self_update()
        if self.cfg.get("self_update_check"):
            self.after(1500, lambda: self.check_manager_update(silent=True))
        log.info("Gestartet: %s %s, Ordner=%s, Version=%s", APP_NAME, APP_VERSION,
                 self.root_path, self.flavor)

    # ----- Aufbau ---------------------------------------------------------- #
    def _build_ui(self):
        t = THEME
        self.fonts = apply_theme(self)
        try:
            self._icon = make_icon(self)
            self.iconphoto(True, self._icon)
        except tk.TclError:
            pass

        # Kopfzeile: Wappen + zweizeiliger Schriftzug
        header = tk.Canvas(self, height=86, bg=t["header"], highlightthickness=0)
        header.pack(fill="x")
        self.header = header
        self.link_bar = ttk.Frame(header, style="Header.TFrame")
        self.play_btn = ttk.Button(self.link_bar, text="▶  Spielen", style="Play.TButton",
                                   cursor="hand2", command=self.start_wow)
        self.play_btn.pack(side="left", padx=(0, 10))
        for text, url in HEADER_LINKS:
            ttk.Button(self.link_bar, text=f"{text}  ↗", style="Link.TButton", cursor="hand2",
                       command=lambda u=url: self.open_link(u)).pack(side="left", padx=(6, 0))
        header.bind("<Configure>", lambda e: self.draw_header())

        content = ttk.Frame(self, padding=(20, 14, 20, 8))
        content.pack(fill="both", expand=True)

        # Kasten: Installation + Aktionen
        ttk.Label(content, text=spaced("Installation"), style="Section.TLabel").pack(anchor="w")
        box = tk.Frame(content, bg=t["panel"], highlightthickness=1,
                       highlightbackground=t["border"], highlightcolor=t["border"])
        box.pack(fill="x", pady=(6, 14))
        inner = ttk.Frame(box, style="Panel.TFrame", padding=(14, 12))
        inner.pack(fill="x")

        top = ttk.Frame(inner, style="Panel.TFrame")
        top.pack(fill="x")
        ttk.Label(top, text="WoW-Ordner", style="Panel.TLabel").pack(side="left", padx=(0, 8))
        self.root_var = tk.StringVar(value=self.cfg["wow_root"])
        root_entry = ttk.Entry(top, textvariable=self.root_var, width=55)
        root_entry.pack(side="left", fill="x", expand=True)
        root_entry.bind("<Return>", lambda e: self._load_flavors())
        root_entry.bind("<FocusOut>", lambda e: self._load_flavors()
                        if self.root_path != self.cfg.get("wow_root") else None)
        ttk.Button(top, text="Durchsuchen…", command=self.browse_root).pack(side="left", padx=(8, 18))
        ttk.Label(top, text="Version", style="Panel.TLabel").pack(side="left", padx=(0, 8))
        self.flavor_var = tk.StringVar(value=self.cfg["flavor"])
        self.flavor_cb = ttk.Combobox(top, textvariable=self.flavor_var, state="readonly", width=24)
        self.flavor_cb.pack(side="left")
        self.flavor_cb.bind("<<ComboboxSelected>>", lambda e: self.on_flavor_change())

        P = "Primary.TButton"
        rows = [
            ([("ZIP installieren", self.install_zip_dialog, P),
              ("Von GitHub…", self.install_github_dialog, P),
              ("Paket importieren…", self.import_pack_dialog, P),
              ("Updates prüfen", self.check_updates, None),
              ("Alle aktualisieren", self.update_all, None)],
             [("Neu laden", self.refresh, None)]),
            ([("Alle auswählen", self.select_all, None),
              ("Aktivieren/Deaktivieren", self.toggle_selected, None),
              ("Entfernen", self.remove_selected, None),
              ("Auswahl exportieren…", self.export_pack_dialog, None)],
             [("WTF sichern", self.backup, None),
              ("Wiederherstellen…", self.restore_dialog, None),
              ("Ordner öffnen", self.open_folder, None)]),
        ]
        for i, (left, right) in enumerate(rows):
            bar = ttk.Frame(inner, style="Panel.TFrame")
            bar.pack(fill="x", pady=(12 if i == 0 else 6, 0))
            for text, cmd, st in left:
                ttk.Button(bar, text=text, command=cmd, style=st or "TButton").pack(side="left", padx=(0, 6))
            for text, cmd, st in reversed(right):
                ttk.Button(bar, text=text, command=cmd, style=st or "TButton").pack(side="right", padx=(6, 0))

        # Sets + Einstellungen
        tk.Frame(inner, height=1, bg=t["line"]).pack(fill="x", pady=(12, 10))
        sets = ttk.Frame(inner, style="Panel.TFrame")
        sets.pack(fill="x")
        ttk.Label(sets, text="Addon-Set", style="Panel.TLabel").pack(side="left", padx=(0, 8))
        self.set_var = tk.StringVar()
        self.set_cb = ttk.Combobox(sets, textvariable=self.set_var, state="readonly", width=24)
        self.set_cb.pack(side="left")
        ttk.Button(sets, text="Anwenden", command=self.apply_set,
                   style=P).pack(side="left", padx=(8, 6))
        ttk.Button(sets, text="Aktive als Set speichern…", command=self.save_set).pack(side="left", padx=(0, 6))
        ttk.Button(sets, text="Set löschen", command=self.delete_set).pack(side="left")

        ttk.Button(sets, text="Log öffnen", command=lambda: open_in_explorer(LOG_FILE)
                   if LOG_FILE.exists() else None).pack(side="right")

        opts = ttk.Frame(inner, style="Panel.TFrame")
        opts.pack(fill="x", pady=(10, 0))
        self.auto_backup_var = tk.BooleanVar(value=bool(self.cfg.get("auto_backup")))
        self.check_start_var = tk.BooleanVar(value=bool(self.cfg.get("check_on_start")))
        ttk.Checkbutton(opts, text="Beim Start auf Updates prüfen", variable=self.check_start_var,
                        command=self.save_options).pack(side="left")
        ttk.Checkbutton(opts, text="Vor Updates automatisch WTF sichern", variable=self.auto_backup_var,
                        command=self.save_options).pack(side="left", padx=(24, 0))
        self.self_update_var = tk.BooleanVar(value=bool(self.cfg.get("self_update_check")))
        ttk.Checkbutton(opts, text="Beim Start nach Manager-Updates suchen", variable=self.self_update_var,
                        command=self.save_options).pack(side="left", padx=(24, 0))
        ttk.Button(opts, text="Manager-Update suchen", command=self.check_manager_update).pack(side="right")

        # Liste
        head = ttk.Frame(content)
        head.pack(fill="x")
        ttk.Label(head, text=spaced("Installierte Addons"), style="Section.TLabel").pack(side="left")
        if HAS_DND:
            ttk.Label(head, text="   ZIP- oder .faddons-Dateien einfach ins Fenster ziehen",
                      style="Hint.TLabel").pack(side="left")
        self.count_var = tk.StringVar()
        ttk.Label(head, textvariable=self.count_var, style="Count.TLabel").pack(side="right")
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *a: self.render())
        ttk.Entry(head, textvariable=self.filter_var, width=24).pack(side="right", padx=(0, 14))
        ttk.Label(head, text="Filter").pack(side="right", padx=(0, 8))
        self.group_var = tk.BooleanVar(value=bool(self.cfg.get("group_addons", True)))
        ttk.Checkbutton(head, text="Gruppieren", variable=self.group_var, style="Plain.TCheckbutton",
                        command=self._toggle_grouping).pack(side="right", padx=(0, 18))

        body = tk.Frame(content, bg=t["border_soft"], highlightthickness=0)
        body.pack(fill="both", expand=True, pady=(8, 0))
        self.tree = ttk.Treeview(body, columns=self.COLUMNS, show="tree headings", selectmode="extended")
        self.tree.column("#0", width=30, minwidth=30, stretch=False)
        self.tree.heading("#0", text="")
        self.open_groups: set[str] = set()
        self.tree.bind("<<TreeviewOpen>>", lambda e: self._remember_open(True))
        self.tree.bind("<<TreeviewClose>>", lambda e: self._remember_open(False))
        widths = {"status": 170, "name": 160, "title": 240, "version": 100,
                  "interface": 115, "source": 200, "update": 110}
        for c in self.COLUMNS:
            self.tree.heading(c, text=spaced(self.HEADINGS[c]), anchor="w",
                              command=lambda c=c: self.sort_by(c))
            self.tree.column(c, width=widths[c], anchor="w", stretch=c in ("title", "source"))
        vsb = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True, padx=(1, 0), pady=1)
        vsb.pack(side="right", fill="y", padx=(0, 1), pady=1)
        self.tree.tag_configure("odd", background=t["row_b"])
        self.tree.tag_configure("group", font=self.fonts["bold"])
        self.tree.tag_configure("child", foreground=t["text_dim"])
        self.tree.tag_configure("disabled", foreground=t["text_faint"])
        self.tree.tag_configure("warn", foreground=t["warn"])
        self.tree.tag_configure("old", foreground=t["old"])
        self.tree.tag_configure("update", foreground=t["update"])
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<Control-a>", lambda e: (self.select_all(force=True), "break")[1])
        self.tree.bind("<Control-A>", lambda e: (self.select_all(force=True), "break")[1])

        # Statusleiste
        self.status_var = tk.StringVar(value="Bereit.")
        ttk.Label(self, textvariable=self.status_var, style="Status.TLabel",
                  anchor="w").pack(fill="x", side="bottom", before=content)
        tk.Frame(self, height=1, bg=t["line"]).pack(fill="x", side="bottom", before=content)
        self.sort_col, self.sort_rev = "name", False

    def draw_header(self):
        t, h = THEME, self.header
        h.delete("all")
        w = h.winfo_width()
        draw_emblem(h, 20, 11, 62)
        h.create_text(96, 26, anchor="w", fill=t["gold"], font=self.fonts["brand"],
                      text="–  " + spaced("Forever Addon") + "  –")
        h.create_text(94, 54, anchor="w", fill=t["gold"], font=self.fonts["title"], text="MANAGER")
        info = f"v{APP_VERSION}"
        if self.game:
            info = f"Spielversion {self.game[0]}  ·  Interface {self.game[1]}     " + info
        h.create_text(w - 20, 26, anchor="e", fill=t["text_dim"], font=self.fonts["small"], text=info)
        h.create_window(w - 20, 58, anchor="e", window=self.link_bar)
        h.create_line(0, 85, w, 85, fill=t["line"])

    # ----- Hilfen ---------------------------------------------------------- #
    @property
    def root_path(self) -> str:
        return self.root_var.get().strip()

    @property
    def flavor(self) -> str:
        # Auswahlfeld zeigt evtl. "_classic_beta_  (Forever)" – nur den Ordnernamen nehmen
        parts = self.flavor_var.get().split()
        return parts[0] if parts else ""

    def sources(self) -> dict:
        return self.cfg["sources"].setdefault(self.flavor, {})

    def sets(self) -> dict:
        return self.cfg["sets"].setdefault(self.flavor, {})

    def folder_source(self, folder: str) -> tuple[str, dict] | tuple[None, None]:
        for sid, s in self.sources().items():
            if folder in s.get("folders", []):
                return sid, s
        return None, None

    def start_wow(self):
        if not self.root_path or not self.flavor:
            messagebox.showwarning(APP_NAME, "Bitte zuerst WoW-Ordner und Version wählen.")
            return
        exe = find_wow_exe(self.root_path, self.flavor)
        if not exe:
            messagebox.showerror(APP_NAME, f"In {Path(self.root_path) / self.flavor} wurde keine "
                                           "WoW-.exe gefunden.\n\nIst diese Version vollständig installiert?")
            return
        running = running_wow(self.root_path, self.flavor)
        if running:
            messagebox.showinfo(APP_NAME, f"WoW läuft bereits ({', '.join(running)}).")
            return
        if self.busy:
            messagebox.showinfo(APP_NAME, "Bitte warten, bis der laufende Vorgang fertig ist.")
            return
        try:
            flags = 0x00000008 | 0x00000200 if sys.platform.startswith("win") else 0  # losgelöst
            subprocess.Popen([str(exe)], cwd=str(exe.parent), close_fds=True, creationflags=flags)
        except OSError as e:
            messagebox.showerror(APP_NAME, f"WoW konnte nicht gestartet werden:\n{e}")
            return
        log.info("WoW gestartet: %s", exe)
        self.set_status(f"{exe.name} gestartet. Falls WoW nach dem Login fragt: Battle.net-App öffnen.")
        self.play_btn.state(["disabled"])
        self.after(15000, lambda: self.play_btn.state(["!disabled"]))   # Doppelstarts vermeiden

    def open_link(self, url: str):
        try:
            webbrowser.open(url, new=2)
            self.set_status(f"Geöffnet: {url}")
        except Exception as e:  # noqa
            messagebox.showerror(APP_NAME, f"Link konnte nicht geöffnet werden:\n{url}\n\n{e}")

    def set_status(self, text: str):
        self.status_var.set(text)

    def save_options(self):
        self.cfg["auto_backup"] = bool(self.auto_backup_var.get())
        self.cfg["check_on_start"] = bool(self.check_start_var.get())
        self.cfg["self_update_check"] = bool(self.self_update_var.get())
        save_config(self.cfg)

    def ready(self, modifies: bool = True, quiet: bool = False) -> bool:
        """Prüft, ob eine Aktion starten darf. modifies=True warnt zusätzlich, wenn WoW läuft."""
        if self.busy:
            if not quiet:
                messagebox.showinfo(APP_NAME, "Bitte warten, ein Vorgang läuft noch.")
            return False
        if not self.root_path or not self.flavor:
            if not quiet:
                messagebox.showwarning(APP_NAME, "Bitte zuerst WoW-Ordner und Version wählen.")
            return False
        if modifies:
            running = running_wow(self.root_path, self.flavor)
            if running and not messagebox.askyesno(
                    APP_NAME, f"WoW läuft gerade ({', '.join(running)}).\n\n"
                              "Änderungen an Addons wirken erst nach /reload bzw. Neustart, und "
                              "beim Ausloggen kann WoW Einstellungen überschreiben.\n\n"
                              "Trotzdem fortfahren?", icon="warning"):
                return False
        return True

    def run_bg(self, label: str, func, on_done=None, on_error=None):
        """Führt func im Hintergrund aus; on_done(result) läuft im GUI-Thread."""
        self.busy = True
        self.set_status(label + " …")
        self.config(cursor="watch")

        def worker():
            try:
                res = func()
                self.jobs.put((on_done, on_error, res, None))
            except Exception as e:  # noqa
                self.jobs.put((on_done, on_error, None, e))

        threading.Thread(target=worker, daemon=True).start()

    def _poll_jobs(self):
        try:
            while True:
                cb, err_cb, res, err = self.jobs.get_nowait()
                self.busy = False
                self.config(cursor="")
                if err:
                    if not isinstance(err, NoReleaseYet):
                        log.error("%s", err)
                    if err_cb:
                        err_cb(err)
                    else:
                        self.set_status(f"Fehler: {err}")
                        messagebox.showerror(APP_NAME, str(err))
                elif cb:
                    cb(res)
        except queue.Empty:
            pass
        self.after(100, self._poll_jobs)

    def selected_addons(self) -> list[Addon]:
        """Ausgewählte Addons; eine ausgewählte Gruppe zählt mit allen ihren Ordnern."""
        names = set()
        for iid in self.tree.selection():
            if iid.startswith("grp:"):
                names.update(self.tree.get_children(iid))
                names.update(self.group_members.get(iid, []))
            else:
                names.add(iid)
        return [a for a in self.addons if a.name in names]

    def _remember_open(self, is_open: bool):
        iid = self.tree.focus()
        if iid.startswith("grp:"):
            (self.open_groups.add if is_open else self.open_groups.discard)(iid)

    def _toggle_grouping(self):
        self.cfg["group_addons"] = bool(self.group_var.get())
        save_config(self.cfg)
        self.render()

    def _on_double_click(self, event):
        iid = self.tree.identify_row(event.y)
        if iid.startswith("grp:"):
            return            # Gruppen klappt Treeview selbst auf/zu
        if iid:
            self.tree.selection_set(iid)
            self.show_details()

    def bundles(self) -> list:
        return self.cfg["bundles"].setdefault(self.flavor, [])

    def remember_bundle(self, folders: list[str]):
        if len(folders) < 2:
            return
        new = set(f.lower() for f in folders)
        kept = [b for b in self.bundles() if not new & {x.lower() for x in b}]
        kept.append(sorted(folders))
        self.cfg["bundles"][self.flavor] = kept
        save_config(self.cfg)

    def remember_source(self, repo: str, info: dict, asset: dict, folders: list[str]):
        self.sources()[repo] = {"type": "github", "tag": info["tag"], "asset": asset["name"],
                                "asset_key": asset_key(asset["name"]) if asset["name"] != "zipball" else "",
                                "folders": folders}

    # ----- Pfade / Versionen ----------------------------------------------- #
    def browse_root(self):
        d = filedialog.askdirectory(title="World of Warcraft-Ordner wählen",
                                    initialdir=self.root_path or None)
        if d:
            self.root_var.set(d)
            self._load_flavors()

    def _load_flavors(self):
        # Wurde direkt ein Versionsordner (z. B. ..\_classic_beta_) oder ein Unterordner
        # davon (Interface, AddOns) gewählt? Dann auf den WoW-Hauptordner korrigieren.
        preselect = ""
        p = Path(self.root_path) if self.root_path else None
        if p is not None:
            for cand in (p, *p.parents):
                if FLAVOR_RE.match(cand.name):
                    preselect = cand.name
                    self.root_var.set(str(cand.parent))
                    break
        if preselect:
            self.flavor_var.set(preselect)
        flavors = list_flavors(self.root_path)
        self.flavor_cb["values"] = [flavor_label(f) for f in flavors]
        current = self.flavor if self.flavor in flavors else (flavors[0] if flavors else "")
        self.flavor_var.set(flavor_label(current) if current else "")
        if self.root_path and not flavors:
            self.set_status("Keine Spielversion gefunden – stimmt der WoW-Ordner?")
        self.on_flavor_change()

    def on_flavor_change(self):
        self.cfg["wow_root"] = self.root_path
        self.cfg["flavor"] = self.flavor
        save_config(self.cfg)
        self.updates.clear()
        self.update_info.clear()
        self.game = read_game_version(self.root_path, self.flavor) if self.flavor else None
        self.draw_header()
        self.reload_sets()
        self.refresh()

    # ----- Anzeige --------------------------------------------------------- #
    def refresh(self):
        if self.root_path and self.flavor:
            self.addons = scan_addons(self.root_path, self.flavor, self.game[1] if self.game else None)
        else:
            self.addons = []
        # Quellen aufräumen, deren Ordner nicht mehr existieren
        present = {a.name for a in self.addons}
        for sid in list(self.sources()):
            s = self.sources()[sid]
            s["folders"] = [f for f in s.get("folders", []) if f in present]
            if not s["folders"]:
                del self.sources()[sid]
        save_config(self.cfg)
        self.render()
        if not self.busy:
            self.set_status(f"{len(self.addons)} Addons in {self.flavor or '–'} gefunden.")

    def is_outdated(self, a: Addon) -> bool:
        return bool(self.game and a.interfaces and self.game[1] not in a.interfaces)

    def row_values(self, a: Addon):
        enabled_names = {x.name.lower() for x in self.addons if x.enabled}
        missing = [d for d in a.deps if d.lower() not in enabled_names]
        outdated = self.is_outdated(a)
        if not a.valid:
            status = "keine .toc"
        elif not a.enabled:
            status = "deaktiviert"
        elif missing:
            status = "fehlt: " + ", ".join(missing)
        elif outdated:
            status = "veraltet"
        else:
            status = "aktiv"
        sid, src = self.folder_source(a.name)
        source = f"GitHub: {sid}" if src else "manuell"
        upd = ""
        has_update = bool(src and self.updates.get(sid) and self.updates[sid] != src.get("tag"))
        if src:
            upd = f"→ {self.updates[sid]}" if has_update else src.get("tag", "")
        tags = []
        if not a.enabled:
            tags.append("disabled")
        elif missing or not a.valid:
            tags.append("warn")
        elif outdated:
            tags.append("old")
        if has_update:
            tags.append("update")
        return (status, a.name, a.title, a.version, a.interface, source, upd), tags

    def group_row(self, g: dict, child_rows: list):
        """Werte und Tags für die Zeile einer Gruppe."""
        members = g["members"]
        on = sum(1 for m in members if m.enabled)
        n = len(members)
        status = "aktiv" if on == n else ("deaktiviert" if on == 0 else f"{on}/{n} aktiv")
        child_tags = [t for _, tags, _ in child_rows for t in tags]
        if "warn" in child_tags:
            status += "  ·  Hinweis"
        elif "old" in child_tags:
            status += "  ·  veraltet"
        root = g["root"]
        ref = root or members[0]
        rvals, _ = self.row_values(ref)
        same = lambda i: rvals[i] if all(r[0][i] == rvals[i] for r in child_rows) else ""  # noqa
        title = root.title if root else g["title"]
        values = (status, f"{g['title']}  ({n})", title,
                  rvals[3] if root else same(3), rvals[4] if root else same(4),
                  same(5) or (rvals[5] if root else "gemischt"), same(6) or rvals[6])
        tags = ["group"]
        if on == 0:
            tags.append("disabled")
        elif "warn" in child_tags:
            tags.append("warn")
        elif "old" in child_tags:
            tags.append("old")
        if "update" in child_tags:
            tags.append("update")
        return values, tags

    def render(self):
        sel_before = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        self.group_members: dict[str, list[str]] = {}
        q = self.filter_var.get().lower().strip()
        idx = self.COLUMNS.index(self.sort_col)
        grouping = bool(self.group_var.get())

        def matches(vals):
            return not q or any(q in str(v).lower() for v in vals)

        if grouping:
            src_bundles = [s.get("folders", []) for s in self.sources().values()]
            groups = compute_groups(self.addons, self.bundles() + src_bundles)
        else:
            groups = [{"title": a.name, "root": None, "members": [a]} for a in self.addons]

        entries = []   # (sortkey, kind, payload)
        for g in groups:
            child_rows = [(*self.row_values(m), m) for m in g["members"]]
            if len(g["members"]) == 1:
                vals, tags, a = child_rows[0]
                if matches(vals):
                    entries.append((str(vals[idx]).lower(), "single", (vals, tags, a)))
                continue
            gvals, gtags = self.group_row(g, child_rows)
            if not (matches(gvals) or any(matches(r[0]) for r in child_rows)):
                continue
            entries.append((str(gvals[idx]).lower(), "group", (g, gvals, gtags, child_rows)))
        entries.sort(key=lambda e: e[0], reverse=self.sort_rev)

        for i, (_, kind, payload) in enumerate(entries):
            stripe = ["odd"] if i % 2 else []
            if kind == "single":
                vals, tags, a = payload
                self.tree.insert("", "end", iid=a.name, values=vals, tags=tags + stripe)
                continue
            g, gvals, gtags, child_rows = payload
            gid = f"grp:{g['title'].lower()}"
            self.group_members[gid] = [m.name for m in g["members"]]
            self.tree.insert("", "end", iid=gid, values=gvals, tags=gtags + stripe,
                             open=gid in self.open_groups)
            for vals, tags, a in child_rows:
                vals = (vals[0], "   " + vals[1], *vals[2:])
                self.tree.insert(gid, "end", iid=a.name, values=vals,
                                 tags=tags + ["child"] + stripe)

        keep = [iid for iid in sel_before if self.tree.exists(iid)]
        if keep:
            self.tree.selection_set(keep)
        active = sum(1 for a in self.addons if a.enabled)
        old = sum(1 for a in self.addons if a.enabled and self.is_outdated(a))
        upd = sum(1 for r, tg in self.updates.items()
                  if r in self.sources() and tg != self.sources()[r].get("tag"))
        parts = [f"{active} aktiv / {len(self.addons)} gesamt"]
        if old:
            parts.append(f"{old} veraltet")
        if upd:
            parts.append(f"{upd} Update(s)")
        self.count_var.set("  ·  ".join(parts))

    def sort_by(self, col):
        self.sort_rev = not self.sort_rev if self.sort_col == col else False
        self.sort_col = col
        self.render()

    def show_details(self):
        sel = self.selected_addons()
        if not sel:
            return
        a = sel[0]
        lines = [f"Titel: {a.title}", f"Ordner: {a.folder}", f"Version: {a.version or '–'}",
                 f"Interface: {a.interface or '–'}", f"Autor: {a.author or '–'}",
                 f"Abhängigkeiten: {', '.join(a.deps) or '–'}",
                 f"Gelesene Datei: {a.toc_name or '–'}"]
        if self.is_outdated(a):
            lines.append(f"\nDas Addon ist für Interface {a.interface} gebaut, das Spiel nutzt "
                         f"{self.game[1]}. WoW lädt es nur mit der Option "
                         "„Veraltete AddOns laden“.")
        notes = clean_wow_text(a.meta.get("notes", ""))
        if notes:
            lines.append(f"\n{notes}")
        messagebox.showinfo(a.name, "\n".join(lines))

    # ----- Installieren ---------------------------------------------------- #
    def install_zip_dialog(self, files=None):
        if not self.ready():
            return
        if not files:
            files = filedialog.askopenfilenames(title="Addon-ZIP wählen",
                                                filetypes=[("ZIP-Archive", "*.zip")])
        if not files:
            return
        root, flavor = self.root_path, self.flavor

        def job():
            return [install_zip(Path(f), root, flavor) for f in files]

        def done(res):
            names = [n for part in res for n in part]
            for part in res:
                self.remember_bundle(part)
            log.info("ZIP installiert: %s", ", ".join(names))
            self.refresh()
            self.set_status(f"Installiert: {', '.join(names)}")

        self.run_bg("Installiere ZIP", job, done)

    def install_github_dialog(self):
        if not self.ready():
            return
        text = simpledialog.askstring(APP_NAME, "GitHub-Repo (owner/repo oder URL):", parent=self)
        if not text:
            return
        try:
            repo = parse_repo(text)
        except ValueError as e:
            messagebox.showerror(APP_NAME, str(e))
            return
        token = self.cfg.get("github_token", "")
        self.run_bg(f"Lese Release von {repo}", lambda: github_latest(repo, token),
                    lambda info: self._pick_and_install(info))

    def _pick_and_install(self, info: dict):
        best = choose_asset(info, self.flavor)
        if not best:
            messagebox.showerror(APP_NAME, f"{info['repo']}: Das Release enthält keine Dateien.")
            return
        asset = best
        if len(info["assets"]) > 1:
            asset = AssetDialog(self, info, best).show()
            if not asset:
                self.set_status("Abgebrochen.")
                return
        root, flavor, token = self.root_path, self.flavor, self.cfg.get("github_token", "")

        def done(folders):
            self.remember_source(info["repo"], info, asset, folders)
            self.remember_bundle(folders)
            self.updates.pop(info["repo"], None)
            save_config(self.cfg)
            log.info("GitHub installiert: %s %s (%s) -> %s", info["repo"], info["tag"],
                     asset["name"], ", ".join(folders))
            self.refresh()
            self.set_status(f"{info['repo']} {info['tag']} installiert: {', '.join(folders)}")

        self.run_bg(f"Lade {asset['name']}",
                    lambda: github_install_asset(asset, root, flavor, token), done)

    # ----- Updates --------------------------------------------------------- #
    def check_updates(self, silent: bool = False):
        if not self.ready(modifies=False, quiet=silent):
            return
        repos = list(self.sources().keys())
        if not repos:
            if not silent:
                messagebox.showinfo(APP_NAME, "Keine GitHub-Addons in dieser Version installiert.")
            return
        token = self.cfg.get("github_token", "")

        def job():
            res, errors = {}, []
            for r in repos:
                try:
                    res[r] = github_latest(r, token)
                except Exception as e:  # noqa
                    errors.append(f"{r}: {e}")
            return res, errors

        def done(out):
            res, errors = out
            for r, info in res.items():
                self.updates[r] = info["tag"]
                self.update_info[r] = info
            self.render()
            n = sum(1 for r, info in res.items() if info["tag"] != self.sources()[r].get("tag"))
            msg = f"{n} Update(s) verfügbar." if n else "Alle GitHub-Addons sind aktuell."
            if errors:
                msg += f"  ({len(errors)} Fehler)"
                log.warning("Update-Prüfung: %s", "; ".join(errors))
            self.set_status(msg)
            if errors and not silent:
                messagebox.showwarning(APP_NAME, "\n".join(errors))

        def failed(err):
            self.set_status(f"Update-Prüfung fehlgeschlagen: {err}")
            if not silent:
                messagebox.showerror(APP_NAME, str(err))

        self.run_bg("Prüfe Updates", job, done, failed)

    def update_all(self):
        pending = [r for r, tg in self.updates.items()
                   if r in self.sources() and tg != self.sources()[r].get("tag")]
        if not pending:
            messagebox.showinfo(APP_NAME, "Keine Updates bekannt. Erst „Updates prüfen“ ausführen.")
            return
        if not self.ready():
            return
        items = [{"repo": r, "old": self.sources()[r].get("tag"), "new": self.updates[r],
                  "notes": self.update_info.get(r, {}).get("notes", ""),
                  "published": self.update_info.get(r, {}).get("published", "")} for r in pending]
        if not UpdateDialog(self, items, bool(self.cfg.get("auto_backup"))).show():
            return
        root, flavor, token = self.root_path, self.flavor, self.cfg.get("github_token", "")
        plan = []
        for r in pending:
            info = self.update_info[r]
            asset = choose_asset(info, flavor, self.sources()[r].get("asset_key", ""))
            if asset:
                plan.append((r, info, asset))

        want_backup = bool(self.cfg.get("auto_backup"))

        def job():
            if want_backup:      # im Hintergrund-Thread nur mit lokalen Werten arbeiten
                try:
                    log.info("Auto-Backup: %s", backup_wtf(root, flavor, "auto"))
                except FileNotFoundError:
                    pass
            return [(r, info, asset, github_install_asset(asset, root, flavor, token))
                    for r, info, asset in plan]

        def done(results):
            for repo, info, asset, folders in results:
                self.remember_source(repo, info, asset, folders)
                log.info("Aktualisiert: %s -> %s (%s)", repo, info["tag"], asset["name"])
            save_config(self.cfg)
            self.refresh()
            self.set_status(f"{len(results)} Addon(s) aktualisiert.")

        self.run_bg("Aktualisiere", job, done)

    # ----- Auswahl / Verwalten -------------------------------------------- #
    def select_all(self, force: bool = False):
        """Wählt alle sichtbaren Addons aus; ein zweiter Klick hebt die Auswahl auf."""
        items = self.tree.get_children()
        if not force and items and set(self.tree.selection()) == set(items):
            self.tree.selection_remove(*items)
            self.set_status("Auswahl aufgehoben.")
        else:
            self.tree.selection_set(items)
            self.set_status(f"{len(self.selected_addons())} Addons ausgewählt.")
        self.tree.focus_set()

    def toggle_selected(self):
        sel = self.selected_addons()
        if not sel or not self.ready():
            return
        # Einheitlich schalten: ist etwas aus, wird alles eingeschaltet – sonst alles aus.
        # So bleiben Gruppen wie Bagnon immer vollständig an oder aus.
        target = not all(a.enabled for a in sel)
        errors = []
        for a in sel:
            if a.enabled == target:
                continue
            try:
                set_enabled(a, self.root_path, self.flavor, target)
                log.info("%s: %s", "Aktiviert" if target else "Deaktiviert", a.name)
            except Exception as e:  # noqa
                errors.append(f"{a.name}: {e}")
        self.refresh()
        if errors:
            messagebox.showerror(APP_NAME, "\n".join(errors))

    def remove_selected(self):
        sel = self.selected_addons()
        if not sel or not self.ready():
            return
        names = ", ".join(a.name for a in sel)
        if not messagebox.askyesno(APP_NAME, f"Wirklich entfernen?\n\n{names}"):
            return
        for a in sel:
            shutil.rmtree(a.folder, ignore_errors=True)
        log.info("Entfernt: %s", names)
        self.refresh()
        self.set_status(f"Entfernt: {names}")

    def open_folder(self):
        if not self.root_path or not self.flavor:
            return
        sel = self.selected_addons()
        path = sel[0].folder if sel else addons_dir(self.root_path, self.flavor)
        path.mkdir(parents=True, exist_ok=True)
        open_in_explorer(path)

    # ----- Sets ------------------------------------------------------------ #
    def reload_sets(self, select: str = ""):
        names = [SET_ALL_OFF, SET_ALL_ON] + sorted(self.sets(), key=str.lower)
        self.set_cb["values"] = names
        if select:
            self.set_var.set(select)
        elif self.set_var.get() not in names:
            self.set_var.set(sorted(self.sets(), key=str.lower)[0] if self.sets() else SET_ALL_OFF)

    def save_set(self):
        if not self.root_path or not self.flavor:
            return
        active = sorted(a.name for a in self.addons if a.enabled)
        if not active:
            messagebox.showinfo(APP_NAME, "Es sind keine Addons aktiv.")
            return
        cur = self.set_var.get()
        name = simpledialog.askstring(APP_NAME, f"Name für das Set ({len(active)} aktive Addons):",
                                      initialvalue="" if cur in (SET_ALL_OFF, SET_ALL_ON) else cur,
                                      parent=self)
        if not name or not name.strip():
            return
        name = name.strip()
        if name in (SET_ALL_OFF, SET_ALL_ON):
            return
        if name in self.sets() and not messagebox.askyesno(APP_NAME, f"Set „{name}“ überschreiben?"):
            return
        self.sets()[name] = active
        save_config(self.cfg)
        log.info("Set gespeichert: %s (%d Addons)", name, len(active))
        self.reload_sets(name)
        self.set_status(f"Set „{name}“ mit {len(active)} Addons gespeichert.")

    def delete_set(self):
        name = self.set_var.get()
        if name not in self.sets():
            return
        if messagebox.askyesno(APP_NAME, f"Set „{name}“ löschen?\n(Die Addons selbst bleiben erhalten.)"):
            del self.sets()[name]
            save_config(self.cfg)
            log.info("Set gelöscht: %s", name)
            self.reload_sets()

    def apply_set(self):
        name = self.set_var.get()
        if not name or not self.ready():
            return
        if name == SET_ALL_OFF:
            wanted = set()
        elif name == SET_ALL_ON:
            wanted = {a.name.lower() for a in self.addons}
        else:
            wanted = {n.lower() for n in self.sets().get(name, [])}
        changes, errors = 0, []
        for a in self.addons:
            should = a.name.lower() in wanted
            if a.enabled != should:
                try:
                    set_enabled(a, self.root_path, self.flavor, should)
                    changes += 1
                except Exception as e:  # noqa
                    errors.append(f"{a.name}: {e}")
        installed = {a.name.lower() for a in self.addons}
        missing = sorted(n for n in self.sets().get(name, []) if n.lower() not in installed)
        log.info("Set angewendet: %s (%d Änderungen)", name, changes)
        self.refresh()
        msg = f"Set „{name}“ angewendet: {changes} Änderung(en)."
        if missing:
            msg += f"  Nicht installiert: {', '.join(missing)}"
        self.set_status(msg)
        if errors:
            messagebox.showerror(APP_NAME, "\n".join(errors))

    # ----- Teilen ---------------------------------------------------------- #
    def export_pack_dialog(self):
        if not self.ready(modifies=False):
            return
        sel = self.selected_addons()
        if not sel:
            messagebox.showinfo(APP_NAME, "Bitte zuerst die Addons auswählen, die du teilen willst.\n"
                                          "(Tipp: „Alle auswählen“ oder Strg+Klick)")
            return
        by_name = {a.name.lower(): a for a in self.addons}
        chosen = {a.name.lower() for a in sel}
        missing = sorted({d for a in sel for d in a.deps
                          if d.lower() in by_name and d.lower() not in chosen}, key=str.lower)
        if missing and messagebox.askyesno(
                APP_NAME, "Diese Addons werden von deiner Auswahl benötigt:\n\n"
                          + "\n".join(missing) + "\n\nMit ins Paket nehmen?"):
            sel += [by_name[d.lower()] for d in missing]

        stamp = datetime.now().strftime("%Y-%m-%d")
        dest = filedialog.asksaveasfilename(
            title="Addon-Paket speichern", defaultextension=PACK_EXT,
            initialfile=f"Addons_{stamp}{PACK_EXT}",
            filetypes=[("Addon-Paket", f"*{PACK_EXT}")])
        if not dest:
            return
        sources, flavor = dict(self.sources()), self.flavor

        def done(path):
            size = path.stat().st_size / 1024 / 1024
            log.info("Paket exportiert: %s (%d Addons)", path, len(sel))
            self.set_status(f"{len(sel)} Addons exportiert ({size:.1f} MB): {path}")
            if messagebox.askyesno(APP_NAME, f"Paket mit {len(sel)} Addons erstellt "
                                             f"({size:.1f} MB).\n\n{path}\n\nOrdner öffnen?"):
                open_in_explorer(path.parent)

        self.run_bg("Exportiere Paket", lambda: export_pack(sel, sources, flavor, Path(dest)), done)

    def import_pack_dialog(self, path=None):
        if not self.ready():
            return
        if not path:
            path = filedialog.askopenfilename(title="Addon-Paket öffnen",
                                              filetypes=[("Addon-Paket", f"*{PACK_EXT}"),
                                                         ("Alle Dateien", "*.*")])
        if not path:
            return
        try:
            manifest = read_pack(Path(path))
        except ValueError as e:
            messagebox.showerror(APP_NAME, str(e))
            return

        entries = manifest["addons"]
        present = {a.name.lower(): a for a in self.addons}
        lines = []
        for e in entries:
            old = present.get(e["folder"].lower())
            ver = e.get("version") or "?"
            note = f"ersetzt deine Version {old.version or '?'}" if old else "neu"
            lines.append(f"• {e.get('title') or e['folder']}  ({ver}) – {note}")
        text = (f"Paket vom {manifest.get('created', '?')[:10]} "
                f"({manifest.get('flavor', '?')}), {len(entries)} Addons:\n\n"
                + "\n".join(lines[:25]) + ("\n…" if len(lines) > 25 else ""))
        if manifest.get("flavor") and manifest["flavor"] != self.flavor:
            text += (f"\n\nHinweis: Das Paket wurde für {manifest['flavor']} erstellt, "
                     f"du installierst es in {self.flavor}.")
        text += "\n\nJetzt installieren?"
        if not messagebox.askyesno(APP_NAME, text):
            return

        root, flavor = self.root_path, self.flavor
        folders = [e["folder"] for e in entries]

        def done(installed):
            for e in entries:
                src = e.get("source")
                if src and src.get("type") == "github" and e["folder"] in installed:
                    try:
                        repo = parse_repo(src.get("repo", ""))
                    except ValueError:
                        continue
                    entry = self.sources().setdefault(
                        repo, {"type": "github", "tag": src.get("tag", ""),
                               "asset": "", "asset_key": "", "folders": []})
                    if e["folder"] not in entry["folders"]:
                        entry["folders"].append(e["folder"])
            save_config(self.cfg)
            log.info("Paket importiert: %s -> %s", path, ", ".join(installed))
            self.refresh()
            self.set_status(f"{len(installed)} Addons aus Paket installiert.")

        self.run_bg("Importiere Paket", lambda: import_pack(Path(path), root, flavor, folders), done)

    # ----- Backups --------------------------------------------------------- #
    def backup(self):
        if not self.ready(modifies=False):
            return
        root, flavor = self.root_path, self.flavor

        def done(path):
            log.info("Backup erstellt: %s", path)
            self.set_status(f"Backup erstellt: {path}")
            if messagebox.askyesno(APP_NAME, f"Backup erstellt:\n{path}\n\nOrdner öffnen?"):
                open_in_explorer(path.parent)

        self.run_bg("Sichere WTF", lambda: backup_wtf(root, flavor), done)

    def restore_dialog(self):
        if not self.root_path or not self.flavor:
            return
        backups = list_backups(self.flavor)
        if not backups:
            messagebox.showinfo(APP_NAME, f"Für {self.flavor} gibt es noch keine Backups.\n"
                                          "Lege eins mit „WTF sichern“ an.")
            return
        chosen = BackupDialog(self, backups).show()
        if not chosen or not self.ready():
            return
        root, flavor = self.root_path, self.flavor

        def done(safety):
            log.info("Backup wiederhergestellt: %s (vorher gesichert: %s)", chosen["path"], safety)
            self.set_status(f"Stand vom {chosen['when']:%d.%m.%Y %H:%M} wiederhergestellt.")
            messagebox.showinfo(APP_NAME, f"Der Stand vom {chosen['when']:%d.%m.%Y %H:%M} ist "
                                          "wiederhergestellt.\n\nDein vorheriger Stand wurde als "
                                          "„vor Wiederherstellung“ gesichert.")

        self.run_bg("Stelle Backup wieder her",
                    lambda: restore_backup(chosen["path"], root, flavor), done)

    # ----- Selbst-Update --------------------------------------------------- #
    def check_manager_update(self, silent: bool = False):
        if self.busy:
            if silent:                       # z. B. während der Addon-Prüfung beim Start
                self.after(2000, lambda: self.check_manager_update(silent=True))
            else:
                messagebox.showinfo(APP_NAME, "Bitte warten, ein Vorgang läuft noch.")
            return
        token = self.cfg.get("github_token", "")

        def done(info):
            if not info:
                self.set_status(f"Der Manager ist aktuell (v{APP_VERSION}).")
                if not silent:
                    messagebox.showinfo(APP_NAME, f"Du nutzt bereits die neueste Version (v{APP_VERSION}).")
                return
            if silent and info["tag"] == self.cfg.get("skip_manager_version"):
                self.set_status(f"Manager-Update {info['tag']} verfügbar (übersprungen).")
                return
            self.offer_manager_update(info, silent)

        def failed(err):
            if isinstance(err, NoReleaseYet):
                log.info("Manager-Update: noch kein Release in %s", UPDATE_REPO)
                self.set_status("Für den Manager gibt es noch kein Release auf GitHub.")
                if not silent and messagebox.askyesno(
                        APP_NAME, f"Im Repository {UPDATE_REPO} wurde noch kein Release "
                                  "veröffentlicht – deshalb gibt es auch nichts zu aktualisieren.\n\n"
                                  "Release-Seite auf GitHub öffnen?"):
                    self.open_link(f"https://github.com/{UPDATE_REPO}/releases")
                return
            log.warning("Manager-Update-Prüfung fehlgeschlagen: %s", err)
            self.set_status("Manager-Update-Prüfung fehlgeschlagen.")
            if not silent:
                msg = str(err)
                if isinstance(err, urllib.error.HTTPError) and err.code == 403:
                    msg = "GitHub hat die Anfrage abgelehnt (Limit erreicht?). Bitte in einer Stunde erneut versuchen."
                elif isinstance(err, urllib.error.URLError):
                    msg = f"Keine Verbindung zu GitHub ({err.reason})."
                messagebox.showerror(APP_NAME, f"Update-Prüfung fehlgeschlagen:\n{msg}")

        self.run_bg("Suche Manager-Update", lambda: check_self_update(token), done, failed)

    def offer_manager_update(self, info: dict, silent: bool):
        frozen = is_frozen()
        has_exe = self_update_asset(info) is not None
        if frozen and has_exe:
            hint = "Der Manager lädt die neue Version, beendet sich und startet automatisch neu."
        elif frozen:
            hint = "Die .exe für dieses Release wird gerade noch gebaut. Versuche es in ein paar Minuten erneut."
        else:
            hint = "Du startest den Manager als Python-Skript. Die neue Version lädst du von der Release-Seite."
        item = {"repo": f"{APP_NAME}", "old": f"v{APP_VERSION}", "new": info["tag"],
                "notes": info.get("notes", ""), "published": info.get("published", "")}
        ok = UpdateDialog(self, [item], False, heading="Neue Manager-Version",
                          ok_text="Jetzt aktualisieren" if frozen and has_exe else "Release-Seite öffnen",
                          cancel_text="Diese Version überspringen" if silent else "Später",
                          hint=hint).show()
        if not ok:
            if silent:
                self.cfg["skip_manager_version"] = info["tag"]
                save_config(self.cfg)
            return
        if not (frozen and has_exe):
            self.open_link(info.get("page") or f"https://github.com/{UPDATE_REPO}/releases")
            return
        token = self.cfg.get("github_token", "")

        def done(new_exe):
            log.info("Manager-Update %s geladen, starte neu.", info["tag"])
            try:
                launch_replacer(new_exe)
            except Exception as e:  # noqa
                messagebox.showerror(APP_NAME, f"Update konnte nicht gestartet werden:\n{e}")
                return
            self.destroy()

        def failed(err):
            log.error("Manager-Update fehlgeschlagen: %s", err)
            if messagebox.askyesno(APP_NAME, f"Das Update ist fehlgeschlagen:\n{err}\n\n"
                                             "Release-Seite im Browser öffnen?"):
                self.open_link(info.get("page") or f"https://github.com/{UPDATE_REPO}/releases")

        self.run_bg(f"Lade Manager {info['tag']}", lambda: download_self_update(info, token), done, failed)

    # ----- Drag & Drop ----------------------------------------------------- #
    def on_drop(self, event):
        paths = [Path(p) for p in self.tk.splitlist(event.data)]
        zips = [str(p) for p in paths if p.suffix.lower() == ".zip"]
        packs = [str(p) for p in paths if p.suffix.lower() == PACK_EXT]
        if not zips and not packs:
            self.set_status("Nur .zip- und .faddons-Dateien können abgelegt werden.")
            return
        if packs:
            self.after(50, lambda: self.import_pack_dialog(packs[0]))
        elif zips:
            self.after(50, lambda: self.install_zip_dialog(zips))


def main():
    setup_logging()
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
