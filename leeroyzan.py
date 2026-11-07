#!/usr/bin/env python3
"""
Leeroyzan
Ein schlanker Addon-Manager mit GUI für World of Warcraft (Forever, Classic, Retail).

Funktionen:
  - WoW-Installation und Spielversionen (_forever_, _classic_beta_, _retail_ ...) erkennen
  - Installierte Addons auflisten (.toc: Titel, Version, Interface, Abhängigkeiten)
  - Interface-Version gegen die installierte Spielversion prüfen (.build.info)
  - Addons aus ZIP-Dateien oder GitHub-Releases installieren (mit Dateiauswahl)
  - GitHub- und CurseForge-Addons auf Updates prüfen (auch beim Start), Changelogs anzeigen
  - Addons von CurseForge installieren und aktualisieren (eigener API-Key, nur lokal gespeichert)
  - Addon-Sets: Gruppen von Addons speichern und mit einem Klick umschalten
  - Addons deaktivieren/aktivieren (Verschieben nach Interface/AddOns_Disabled)
  - Addon-Pakete (.lzpack) exportieren und importieren, um Addons zu teilen
  - Download-Listen (.lzlist oder Textcode für Discord/SMS) teilen: nur Verweise, keine Dateien
  - CurseForge direkt im Manager durchsuchen (nur WoW-Addons) und installieren
  - WTF-Ordner sichern und wiederherstellen, automatisches Backup vor Updates
  - Warnung, wenn WoW läuft; Drag & Drop (mit tkinterdnd2); Logdatei

Läuft mit der Python-Standardbibliothek; tkinterdnd2 ist optional (Drag & Drop).
Arbeitet ausschließlich mit Dateien im Addon-/WTF-Ordner – kein Eingriff in den Spielclient.
"""

import base64
import hashlib
import html
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
import time
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import zipfile
import zlib
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog

try:                                   # optional: Drag & Drop
    from tkinterdnd2 import TkinterDnD, DND_FILES
    BaseTk = TkinterDnD.Tk
    HAS_DND = True
except Exception:                      # noqa – Paket fehlt oder Tk-Erweiterung nicht ladbar
    BaseTk = tk.Tk
    DND_FILES = None
    HAS_DND = False

APP_NAME = "Leeroyzan"
APP_VERSION = "1.1.0"
USER_AGENT = f"Leeroyzan/{APP_VERSION}"

# Selbst-Update über GitHub-Releases dieses Repos
UPDATE_REPO = "Artithefax/leeroyzan"
EXE_ASSET = "Leeroyzan.exe"
SCRIPT_ASSET = "leeroyzan.py"                     # Quelldatei im Repo (für die Python-Variante)

# Link-Buttons in der Kopfzeile (Text, Adresse) – hier einfach anpassbar
HEADER_LINKS = [
    ("CurseForge", "https://www.curseforge.com/wow"),
    ("GitHub", "https://github.com/search?q=wow+forever+addon&type=repositories&s=updated"),
    ("Projektseite", "https://github.com/Artithefax/leeroyzan"),
]

# --------------------------------------------------------------------------- #
# Konfiguration
# --------------------------------------------------------------------------- #

def config_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    d = Path(base) / "Leeroyzan"
    d.mkdir(parents=True, exist_ok=True)
    return d


CONFIG_FILE = config_dir() / "config.json"
BACKUP_DIR = config_dir() / "backups"
LOG_FILE = config_dir() / "manager.log"
AUTO_BACKUPS_KEEP = 10

log = logging.getLogger("leeroyzan")


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
    cfg.setdefault("curseforge_key", "")   # eigener API-Key – bleibt nur in dieser Datei
    cfg.setdefault("download_dir", "")     # Browser-Download-Ordner (leer = ~/Downloads)
    cfg.setdefault("sets", {})      # flavor -> {setname: [ordner, ...]}
    cfg.setdefault("auto_backup", True)
    cfg.setdefault("check_on_start", True)
    cfg.setdefault("self_update_check", True)
    # Modus für Manager-Updates: auto (still installieren), ask (nachfragen), off
    if "self_update_mode" not in cfg:
        cfg["self_update_mode"] = "auto" if cfg.get("self_update_check", True) else "off"
    cfg.setdefault("skip_manager_version", "")
    cfg.setdefault("bundles", {})        # flavor -> [[ordner, ...], ...] gemeinsam installiert
    cfg.setdefault("group_addons", True)
    cfg.setdefault("layout", "klassisch")   # klassisch | seitenleiste | launcher
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
# Addon-Pakete zum Teilen (.lzpack = ZIP mit manifest.json + AddOns/<Ordner>/...)
# --------------------------------------------------------------------------- #

PACK_EXT = ".lzpack"
PACK_FORMAT = 1
PACK_MAX_BYTES = 1024 ** 3          # Schutz gegen übergroße/manipulierte Pakete (1 GB entpackt)
SAFE_NAME_RE = re.compile(r"^[\w .+\-!()&'\[\]]+$")


def valid_addon_name(name: str) -> bool:
    return bool(name) and name not in (".", "..") and SAFE_NAME_RE.match(name) is not None


def export_pack(addons: list, sources: dict, flavor: str, dest: Path) -> Path:
    """Schreibt die gewählten Addons inkl. Quellinfos in eine .lzpack-Datei."""
    entries = []
    for a in addons:
        entry = {"folder": a.name, "title": a.title, "version": a.version, "source": None}
        for repo, s in sources.items():
            if a.name in s.get("folders", []):
                if s.get("type") == "curseforge":
                    # Dateien von CurseForge werden nicht weitergegeben – nur der Verweis
                    entry["source"] = {"type": "curseforge", "mod_id": s.get("mod_id"),
                                       "file_id": s.get("file_id"), "slug": s.get("slug", ""),
                                       "name": s.get("name", ""), "page": s.get("page", ""),
                                       "tag": s.get("tag", ""), "folders": s.get("folders", [])}
                    entry["files"] = False
                else:
                    entry["source"] = {"type": "github", "repo": repo, "tag": s.get("tag", ""),
                                       "folders": s.get("folders", [])}
                break
        entries.append(entry)
    skip = {e["folder"] for e in entries if e.get("files") is False}
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
            if a.name in skip:
                continue
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
        raise CFError("Kein CurseForge-API-Key hinterlegt (Einstellungen).")
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
        msgs = {401: "CurseForge lehnt den API-Key ab. Bitte den Key in den Einstellungen prüfen.",
                403: "CurseForge verweigert den Zugriff (API-Key ungültig oder Download nicht erlaubt).",
                404: "Bei CurseForge nicht gefunden.",
                429: "Zu viele Anfragen an CurseForge – bitte in ein paar Minuten erneut versuchen."}
        raise CFError(msgs.get(e.code, f"CurseForge-Fehler HTTP {e.code}."), e.code) from None
    except urllib.error.URLError as e:
        raise CFError(f"CurseForge nicht erreichbar: {e.reason}") from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise CFError("Unerwartete Antwort von CurseForge.") from None


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
    raise ValueError("Bitte den CurseForge-Link (…/wow/addons/name) oder den Namen des Addons angeben.")


def cf_get_mod(ref, key: str) -> dict:
    if isinstance(ref, int):
        mod = cf_request(f"/mods/{ref}", key).get("data") or {}
    else:
        found = cf_request("/mods/search", key, {"gameId": CF_GAME_ID, "slug": ref}).get("data") or []
        mod = next((m for m in found if str(m.get("slug", "")).lower() == ref), {})
    if not mod or mod.get("gameId", CF_GAME_ID) != CF_GAME_ID:
        raise CFError(f"Kein WoW-Addon „{ref}“ bei CurseForge gefunden.")
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
        return f"{n / 1_000_000:.1f} Mio".replace(".", ",")
    if n >= 1_000:
        return f"{n // 1_000} Tsd"
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
    return {"name": name, "label": "  ·  ".join(p for p in parts if p), "file_name": f.get("fileName", ""),
            "url": f.get("downloadUrl"), "size": f.get("fileLength", 0), "file_id": f.get("id"),
            "mod_id": f.get("modId"), "game_versions": versions, "release_type": rtype,
            "date": str(f.get("fileDate", "")), "sha1": sha1}


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
        raise CFError("keine passende Datei gefunden")
    info["allow_dl"] = bool(info["best"].get("url"))     # ohne Download-Adresse: Autor hat es gesperrt
    if info["tag"] != src.get("tag"):
        info["notes"] = cf_changelog(src["mod_id"], info["best"]["file_id"], key)
    return info


def cf_install_asset(asset: dict, info: dict, root: str, flavor: str, key: str) -> list[str]:
    page = info.get("page", "").rstrip("/") + "/files"
    if not info.get("allow_dl", True):
        raise ManualDownload(page, "Der Autor erlaubt keinen Download über Programme wie diesen.")
    url = asset.get("url")
    if not url:
        try:
            url = cf_request(f"/mods/{asset['mod_id']}/files/{asset['file_id']}/download-url", key).get("data")
        except CFError as e:
            if e.code in (403, 404):
                raise ManualDownload(page, "Für diese Datei ist kein direkter Download freigegeben.") from None
            raise
    if not isinstance(url, str) or not url:
        raise ManualDownload(page, "Für diese Datei ist kein direkter Download freigegeben.")
    host = urllib.parse.urlparse(url).hostname or ""
    if not url.startswith("https://") or not any(host == h or host.endswith("." + h) for h in CF_DL_HOSTS):
        raise CFError(f"Unerwartete Download-Adresse ({host or '?'}) – Abbruch.")
    blob = http_get(url, "", accept="application/octet-stream")     # niemals den API-Key ans CDN senden
    if asset.get("sha1") and hashlib.sha1(blob).hexdigest().lower() != asset["sha1"].lower():
        raise CFError("Prüfsumme der heruntergeladenen Datei stimmt nicht – Abbruch.")
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
        raise CFError("keine passende Datei gefunden")
    return info

# --------------------------------------------------------------------------- #
# Download-Listen: nur Verweise (CurseForge-/GitHub-Quellen), als Datei oder als Textcode
# --------------------------------------------------------------------------- #

LIST_EXT = ".lzlist"
LIST_PREFIX = "LZ1"
LIST_FORMAT = "leeroyzan-list"
LIST_MAX_ENTRIES = 500
LIST_PART_SIZES = {"Discord / Chat (1800 Zeichen)": 1800, "Kurznachricht (500 Zeichen)": 500,
                   "SMS (150 Zeichen)": 150}


class ListError(ValueError):
    pass


def build_list(addons: list, sources: dict, flavor: str, sets: dict,
               exact: bool = True, with_sets: bool = True) -> tuple[dict, list[str]]:
    """Baut die Liste aus den gewählten Addons. Gibt (Nutzdaten, Namen ohne Quelle) zurück."""
    names = {a.name for a in addons}
    entries, linked = [], set()
    for sid, src in sources.items():
        folders = set(src.get("folders", []))
        if not folders & names:
            continue
        linked |= folders
        if src.get("type") == "curseforge":
            if type(src.get("mod_id")) is not int:
                continue
            e = {"t": "c", "i": src["mod_id"], "n": src.get("name", ""), "s": src.get("slug", "")}
            if exact and isinstance(src.get("file_id"), int):
                e["f"] = src["file_id"]
        else:
            e = {"t": "g", "r": sid}
            if src.get("asset_key"):
                e["k"] = src["asset_key"]
        entries.append(e)
    payload = {"v": 1, "fl": flavor, "a": entries}
    if with_sets:
        picked = {n: sorted(m for m in members if m in names) for n, members in sets.items()}
        picked = {n: m for n, m in picked.items() if m}
        if picked:
            payload["s"] = picked
    return payload, sorted(names - linked, key=str.lower)


def list_to_code(payload: dict, part_len: int = 1800) -> list[str]:
    """Komprimiert die Liste und zerlegt sie in Textteile 'LZ1:1/3:…' (Großbuchstaben + Ziffern)."""
    raw = zlib.compress(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"), 9)
    body = base64.b32encode(raw).decode("ascii").rstrip("=")
    n = 1
    while True:
        room = max(20, part_len - len(f"{LIST_PREFIX}:{n}/{n}:"))
        need = max(1, -(-len(body) // room))
        if need <= n:
            break
        n = need
    return [f"{LIST_PREFIX}:{i + 1}/{n}:{body[i * room:(i + 1) * room]}" for i in range(n)]


def list_to_file_text(payload: dict) -> str:
    doc = {"format": LIST_FORMAT, "created": datetime.now().isoformat(timespec="seconds"), **payload}
    return json.dumps(doc, ensure_ascii=False, indent=1)


def list_readable(payload: dict) -> str:
    lines = [f"Addon-Liste für WoW {(payload.get('fl') or '').strip('_')} – {len(payload['a'])} Addons (Leeroyzan)"]
    for e in payload["a"]:
        if e["t"] == "c":
            url = (f"https://www.curseforge.com/wow/addons/{e['s']}" if e.get("s")
                   else f"https://www.curseforge.com/projects/{e['i']}")
            lines.append(f"• {e.get('n') or e.get('s') or e['i']} – {url}")
        else:
            lines.append(f"• {e['r'].split('/')[-1]} – https://github.com/{e['r']}")
    return "\n".join(lines)


def _decode_code(text: str) -> dict:
    parts: dict[int, str] = {}
    total = 0
    for m in re.finditer(rf"{LIST_PREFIX}:(\d+)/(\d+):([A-Za-z2-7]+)", text, re.I):
        idx, tot = int(m.group(1)), int(m.group(2))
        if tot != total and total:
            raise ListError("Die Teile gehören zu verschiedenen Listen.")
        total = tot
        parts[idx] = m.group(3).upper()
    if not parts:
        raise ListError("Keine Download-Liste erkannt. Der Code beginnt mit „LZ1:“.")
    missing = [i for i in range(1, total + 1) if i not in parts]
    if missing:
        raise ListError("Es fehlt Teil " + ", ".join(map(str, missing)) + f" von {total}.")
    body = "".join(parts[i] for i in range(1, total + 1))
    try:
        d = zlib.decompressobj()
        raw = d.decompress(base64.b32decode(body + "=" * (-len(body) % 8)), 2_000_000)
        if d.unconsumed_tail:
            raise ListError("Die Liste ist ungewöhnlich groß und wird nicht geladen.")
        return json.loads(raw.decode("utf-8"))
    except ListError:
        raise
    except Exception:  # noqa – beschädigter Code
        raise ListError("Der Code ist unvollständig oder beschädigt. Bitte alle Teile erneut kopieren.") from None


def parse_list_text(text: str) -> dict:
    """Liest eine Liste aus Dateiinhalt (JSON) oder Chat-Text (LZ1-Code) und prüft sie streng."""
    text = text.replace("`", "").strip()
    try:
        data = json.loads(text) if text.startswith("{") else _decode_code(text)
    except json.JSONDecodeError:
        raise ListError("Die Datei ist keine gültige Download-Liste.") from None
    if not isinstance(data, dict) or data.get("v") != 1 or not isinstance(data.get("a"), list):
        raise ListError("Unbekanntes oder zu neues Listenformat – bitte den Manager aktualisieren.")
    if len(data["a"]) > LIST_MAX_ENTRIES:
        raise ListError("Die Liste ist zu lang.")
    clean = []
    for e in data["a"]:
        if not isinstance(e, dict):
            continue
        if e.get("t") == "c" and type(e.get("i")) is int and e["i"] > 0:
            item = {"t": "c", "i": e["i"], "n": str(e.get("n", ""))[:80],
                    "s": re.sub(r"[^a-z0-9-]", "", str(e.get("s", "")).lower())[:80]}
            if type(e.get("f")) is int and e["f"] > 0:
                item["f"] = e["f"]
            clean.append(item)
        elif e.get("t") == "g":
            try:
                item = {"t": "g", "r": parse_repo(str(e.get("r", "")))}
            except ValueError:
                continue
            if e.get("k"):
                item["k"] = str(e["k"])[:100]
            clean.append(item)
    if not clean:
        raise ListError("Die Liste enthält keine installierbaren Addons.")
    sets: dict[str, list[str]] = {}
    raw_sets = data.get("s") if isinstance(data.get("s"), dict) else {}
    bad = r'[\x00-\x1f\\/:*?"<>|]'
    for name, members in list(raw_sets.items())[:50]:
        if isinstance(members, list) and 1 <= len(str(name)) <= 64 and not re.search(r"[\x00-\x1f]", str(name)):
            ok = [m for m in members[:300] if isinstance(m, str) and 1 <= len(m) <= 100
                  and not re.search(bad, m) and not m.startswith(".")]
            if ok:
                sets[str(name)] = ok
    return {"fl": str(data.get("fl", ""))[:60], "a": clean, "s": sets}

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


BUILD_GRACE_S = 2 * 3600       # so lange gilt ein Release ohne .exe als „wird noch gebaut“


def release_age_s(info: dict) -> float:
    try:
        ts = datetime.fromisoformat(info.get("published_at", "").replace("Z", "+00:00"))
        return time.time() - ts.timestamp()
    except ValueError:
        return float("inf")


def pick_self_update(releases: list[dict]) -> dict | None:
    """Höchste neuere Version, die sich einspielen lässt – oder die gerade noch gebaut wird.
    Releases ohne Leeroyzan.exe (z. B. alte oder fehlgeschlagene) werden übersprungen."""
    newer = sorted((r for r in releases if parse_version(r["tag"]) > parse_version(APP_VERSION)),
                   key=lambda r: parse_version(r["tag"]), reverse=True)
    for r in newer:
        if not is_frozen() or update_ready(r) or release_age_s(r) < BUILD_GRACE_S:
            return r
        log.warning("Release %s hat keine %s – wird übersprungen", r["tag"], EXE_ASSET)
    return None


def check_self_update(token: str = "") -> dict | None:
    """Liefert die Release-Infos, wenn auf GitHub eine neuere Version liegt."""
    try:
        data = json.loads(http_get(f"https://api.github.com/repos/{UPDATE_REPO}/releases?per_page=30", token))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise NoReleaseYet() from e
        raise
    releases = [release_info(UPDATE_REPO, r) for r in data
                if isinstance(r, dict) and not r.get("draft") and not r.get("prerelease")]
    if not releases:
        raise NoReleaseYet()           # noch nichts veröffentlicht
    return pick_self_update(releases)


def self_update_asset(info: dict) -> dict | None:
    for a in info.get("all_assets", []):
        if a["name"].lower() == EXE_ASSET.lower():
            return a
    return None


def script_update_possible() -> bool:
    """Python-Variante: Skript darf sich selbst ersetzen (nicht in einer Git-Arbeitskopie)."""
    p = Path(__file__).resolve()
    return p.suffix == ".py" and not (p.parent / ".git").exists() and os.access(p.parent, os.W_OK)


def update_ready(info: dict) -> bool:
    """Kann diese Installation das Release jetzt selbst einspielen?
    Für die .exe müssen Datei UND Prüfsumme am Release hängen (der Build auf GitHub ist dann fertig)."""
    if is_frozen():
        names = {a["name"].lower() for a in info.get("all_assets", [])}
        return EXE_ASSET.lower() in names and (EXE_ASSET + ".sha256").lower() in names
    return script_update_possible()


def new_exe_path() -> Path:
    exe = Path(sys.executable)
    return exe.with_name(exe.stem + ".new.exe")


def download_self_update(info: dict, token: str = "") -> Path:
    """Lädt die neue .exe neben die laufende und prüft sie (MZ-Header, ggf. SHA-256)."""
    asset = self_update_asset(info)
    if not asset:
        raise ValueError(f"Das Release enthält noch keine {EXE_ASSET}.")
    blob = http_get(asset["url"], token, accept="application/octet-stream")
    if len(blob) < 1_000_000 or blob[:2] != b"MZ":
        raise ValueError("Die heruntergeladene Datei ist keine gültige Windows-Anwendung.")
    sha_asset = next((a for a in info.get("all_assets", [])
                      if a["name"].lower() == (asset["name"] + ".sha256").lower()), None)
    if not sha_asset:
        raise ValueError("Zum Release gehört noch keine Prüfsumme – der Build ist wohl noch nicht fertig.")
    expected = http_get(sha_asset["url"], token, accept="application/octet-stream")
    expected = (expected.decode("ascii", "replace").split() or [""])[0].strip().lower()
    if expected != hashlib.sha256(blob).hexdigest():
        raise ValueError("Prüfsumme stimmt nicht – Download beschädigt. Bitte später erneut versuchen.")
    target = new_exe_path()
    try:
        target.write_bytes(blob)
    except PermissionError:
        raise PermissionError(f"Keine Schreibrechte in {target.parent}. Verschiebe den Manager z. B. "
                              "in deinen Benutzerordner oder lade das Update von der Release-Seite.")
    return target


def download_script_update(info: dict, token: str = "") -> Path:
    """Python-Variante: lädt leeroyzan.py des Releases und prüft Version und Syntax."""
    url = f"https://raw.githubusercontent.com/{UPDATE_REPO}/{urllib.parse.quote(info['tag'])}/{SCRIPT_ASSET}"
    try:
        text = http_get(url, token, accept="text/plain").decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("Die geladene Datei ist kein gültiges Skript.") from None
    m = re.search(r'^APP_VERSION = "([^"]+)"', text, re.M)
    if not m or parse_version(m.group(1)) != parse_version(info["tag"]):
        raise ValueError("Die geladene Datei passt nicht zur Release-Version.")
    compile(text, SCRIPT_ASSET, "exec")          # Syntaxfehler -> Abbruch, das alte Skript bleibt
    target = Path(__file__).resolve().with_suffix(".new.py")
    target.write_text(text, encoding="utf-8")
    return target


def apply_script_update(new_script: Path) -> None:
    """Ersetzt das laufende Skript (alte Fassung bleibt als .py.bak) und startet es neu."""
    cur = Path(__file__).resolve()
    shutil.copy2(cur, cur.with_suffix(".py.bak"))
    os.replace(new_script, cur)
    flags = 0x00000008 if os.name == "nt" else 0          # losgelöst starten
    subprocess.Popen([sys.executable, str(cur), *sys.argv[1:]], close_fds=True, creationflags=flags)


def launch_replacer(new_exe: Path) -> None:
    """Startet ein kleines Skript, das nach dem Beenden die alte .exe ersetzt und neu startet."""
    old_exe = Path(sys.executable)
    bat = Path(tempfile.gettempdir()) / f"leeroyzan_update_{os.getpid()}.bat"
    ulog = config_dir() / "update.log"
    script = f"""@echo off
chcp 65001 >nul
set "PID={os.getpid()}"
>>"{ulog}" echo %date% %time% Update gestartet: "{new_exe}" -^> "{old_exe}"
:wait
tasklist /FI "PID eq %PID%" 2>nul | find "%PID%" >nul && (ping -n 2 127.0.0.1 >nul & goto wait)
set /a n=0
:move
move /Y "{new_exe}" "{old_exe}" >nul 2>&1 && (>>"{ulog}" echo %date% %time% ersetzt & goto start)
set /a n+=1
if %n% GEQ 30 (>>"{ulog}" echo %date% %time% FEHLER: Datei konnte nicht ersetzt werden & goto start)
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
        new_exe_path().unlink(missing_ok=True)
    else:
        Path(__file__).resolve().with_suffix(".new.py").unlink(missing_ok=True)


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
    "text_faint2": "#8e9694",   # Nebentexte mit ausreichend Kontrast
    "btn_primary": "#644e2b",   # Bronze-Button
    "btn_primary_hi": "#7a5f34",
    "btn_text":    "#fff7e6",
    "crest":       "#16242c",   # Wappen-Nachtblau
    "warn":        "#e69633",
    "ok":          "#7ddc6a",   # aktiv / alles in Ordnung
    "update":      "#ffd54a",   # Update verfügbar
    "update_bg":   "#2d2610",
    "old":         "#d6c37a",   # Interface passt nicht zur Spielversion
}


UPD_MARK = "⬇"          # Kennzeichen für Addons mit verfügbarem Update


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
        "page":  pick_font(root, serif, 18, "bold"),
        "side":  pick_font(root, serif, 14, "bold"),
        "hero":  pick_font(root, serif, 26, "bold"),
        "play":  pick_font(root, serif, 15, "bold"),
        "tile":  pick_font(root, serif, 20, "bold"),
        "card":  pick_font(root, serif, 13, "bold"),
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
    style.configure("TRadiobutton", background=t["panel"], foreground=t["text_dim"],
                    indicatorbackground=t["bg"], indicatorforeground=t["gold"],
                    indicatorcolor=t["bg"], bordercolor=t["field_line"], focuscolor=t["panel"],
                    upperbordercolor=t["field_line"], lowerbordercolor=t["field_line"])
    style.map("TRadiobutton", background=[("active", t["panel"])], foreground=[("active", t["gold"])],
              indicatorcolor=[("selected", t["gold"]), ("pressed", t["active"])],
              indicatorbackground=[("selected", t["gold"])])
    style.configure("CardKicker.TLabel", background=t["panel"], foreground=t["gold"], font=fonts["label"])
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

    # --- Ansicht „Seitenleiste“ ---
    style.configure("Side.TFrame", background=t["header"])
    style.configure("Side.TLabel", background=t["header"], foreground=t["text_dim"], font=fonts["small"])
    style.configure("SideKicker.TLabel", background=t["header"], foreground=t["gold"], font=fonts["label"])
    style.configure("Nav.TButton", background=t["header"], foreground=t["text"], font=fonts["base"],
                    bordercolor=t["header"], lightcolor=t["header"], darkcolor=t["header"],
                    anchor="w", padding=(14, 9), relief="flat")
    style.map("Nav.TButton", background=[("active", t["panel"])], foreground=[("active", t["gold"])],
              bordercolor=[("active", t["header"])], lightcolor=[("active", t["panel"])],
              darkcolor=[("active", t["panel"])])
    style.configure("NavActive.TButton", background=t["active"], foreground=t["gold"], font=fonts["bold"],
                    bordercolor=t["gold"], lightcolor=t["active"], darkcolor=t["active"],
                    anchor="w", padding=(14, 9), relief="flat")
    style.map("NavActive.TButton", background=[("active", t["active"])])
    style.configure("Kicker.TLabel", background=t["bg"], foreground=t["gold"], font=fonts["label"])
    style.configure("PageTitle.TLabel", background=t["bg"], foreground=t["text"], font=fonts["page"])
    style.configure("Body.TLabel", background=t["bg"], foreground=t["text_dim"])
    style.configure("Detail.TFrame", background=t["row_a"])
    style.configure("DetailKey.TLabel", background=t["row_a"], foreground=t["text_faint2"])
    style.configure("DetailVal.TLabel", background=t["row_a"], foreground=t["text"])
    style.configure("DetailKicker.TLabel", background=t["row_a"], foreground=t["gold"], font=fonts["label"])
    style.configure("DetailTitle.TLabel", background=t["row_a"], foreground=t["text"], font=fonts["page"])
    style.configure("DetailNote.TLabel", background=t["row_a"], foreground=t["text_dim"])
    style.configure("Danger.TButton", background=t["bg"], foreground=t["warn"], bordercolor="#5a3d1c",
                    lightcolor=t["bg"], darkcolor=t["bg"])
    style.map("Danger.TButton", background=[("active", "#21160c")], bordercolor=[("active", t["warn"])])

    # --- Ansicht „Launcher“ ---
    style.configure("Head.TFrame", background=t["header"])
    style.configure("HeadKicker.TLabel", background=t["header"], foreground=t["gold"], font=fonts["brand"])
    style.configure("HeadTitle.TLabel", background=t["header"], foreground=t["gold"], font=fonts["hero"])
    style.configure("HeadInfo.TLabel", background=t["header"], foreground=t["text_dim"], font=fonts["small"])
    style.configure("BigPlay.TButton", background=t["btn_primary"], foreground=t["btn_text"],
                    bordercolor=t["gold"], lightcolor=t["btn_primary"], darkcolor=t["btn_primary"],
                    font=fonts["play"], padding=(34, 14))
    style.map("BigPlay.TButton",
              background=[("pressed", t["btn_primary"]), ("active", t["btn_primary_hi"]),
                          ("disabled", t["panel"])],
              foreground=[("disabled", t["text_faint"])],
              lightcolor=[("active", t["btn_primary_hi"])], darkcolor=[("active", t["btn_primary_hi"])])
    style.configure("Tile.TFrame", background=t["panel"])
    style.configure("TileKicker.TLabel", background=t["panel"], foreground=t["gold"], font=fonts["label"])
    style.configure("TileValue.TLabel", background=t["panel"], foreground=t["text"], font=fonts["tile"])
    style.configure("TileHint.TLabel", background=t["panel"], foreground=t["text_faint2"], font=fonts["small"])
    style.configure("Card.TFrame", background=t["row_a"])
    style.configure("CardTitle.TLabel", background=t["row_a"], foreground=t["text"], font=fonts["card"])
    style.configure("CardSub.TLabel", background=t["row_a"], foreground=t["text_faint2"], font=fonts["small"])
    style.configure("CardLink.TButton", background=t["row_a"], foreground=t["gold"], font=fonts["small"],
                    bordercolor=t["row_a"], lightcolor=t["row_a"], darkcolor=t["row_a"], padding=(0, 2))
    style.map("CardLink.TButton", foreground=[("active", t["text"])], background=[("active", t["row_a"])],
              bordercolor=[("active", t["row_a"])])
    style.configure("ToggleOn.TButton", background=t["btn_primary"], foreground=t["btn_text"],
                    bordercolor=t["gold"], lightcolor=t["btn_primary"], darkcolor=t["btn_primary"],
                    font=fonts["label"], padding=(10, 3))
    style.map("ToggleOn.TButton", background=[("active", t["btn_primary_hi"])])
    style.configure("ToggleOff.TButton", background=t["pill"], foreground=t["text_dim"],
                    bordercolor=t["border_soft"], lightcolor=t["pill"], darkcolor=t["pill"],
                    font=fonts["label"], padding=(10, 3))
    style.map("ToggleOff.TButton", background=[("active", t["active"])], foreground=[("active", t["gold"])])
    for name, bg, fg, bc, fnt in (("TMenubutton", t["panel"], t["text"], t["border"], fonts["base"]),
                                  ("Primary.TMenubutton", t["btn_primary"], t["btn_text"], t["gold"], fonts["bold"])):
        style.configure(name, background=bg, foreground=fg, bordercolor=bc, lightcolor=bg, darkcolor=bg,
                        arrowcolor=fg, font=fnt, padding=(12, 5), relief="solid")
        style.map(name, background=[("active", t["active"] if name == "TMenubutton" else t["btn_primary_hi"])],
                  foreground=[("active", t["gold"] if name == "TMenubutton" else t["btn_text"])])

    style.configure("Vertical.TScrollbar", background=t["panel"], troughcolor=t["bg"],
                    bordercolor=t["bg"], arrowcolor=t["gold"],
                    lightcolor=t["panel"], darkcolor=t["panel"], gripcount=0)
    style.map("Vertical.TScrollbar", background=[("active", t["border"])])
    return fonts


def draw_emblem(cv, x0: int, y0: int, s: int) -> None:
    """Eigenes Wappen: Schild mit schräg nach vorn stürmendem L und Fahrtlinien."""
    t = THEME

    def p(fx, fy):
        return x0 + fx * s, y0 + fy * s

    def lean(fx, fy):                      # Neigung nach vorn
        return p(fx + 0.18 * (.5 - fy), fy)

    shield = [p(.12, .10), p(.50, .02), p(.88, .10), p(.88, .48),
              p(.72, .78), p(.50, .96), p(.28, .78), p(.12, .48)]
    cv.create_polygon([c for pt in shield for c in pt], fill=t["crest"], outline=t["gold"],
                      width=max(2, s // 24))
    inner = [p(.20, .16), p(.50, .10), p(.80, .16), p(.80, .47),
             p(.67, .72), p(.50, .87), p(.33, .72), p(.20, .47)]
    cv.create_polygon([c for pt in inner for c in pt], fill="", outline=t["border"],
                      width=max(1, s // 40))
    letter = [(.36, .24), (.50, .24), (.50, .62), (.70, .62), (.70, .74), (.36, .74)]
    cv.create_polygon([c for fx, fy in letter for c in lean(fx, fy)], fill=t["gold"],
                      outline=t["gold_deep"], width=max(1, s // 60))
    for fy, ln in ((.36, .12), (.48, .16), (.60, .12)):
        cv.create_line(*lean(.33 - ln, fy), *lean(.33, fy), fill=t["text"],
                       width=max(2, s // 28), capstyle="round")


ICON_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAEAAAABACAYAAACqaXHeAAATMklEQVR42uVbeXBeV3X/nXvv+973vkX7YsnabMdZLDuWA0kmkBDLAduxQwslEkuYFgIDCYQsTJmyBORvoLSBponr0NBCSykpU6QMhZmWLCSREoYwTVLskEhO7NiObMu2dulb3ve95d7TPz7ZiWN5kbwkwJ25MxqP9d47v3PuOb/zu0eEs7iYQUAXoQ8CADb1waRSKXPi32FC3yYJAFgNA6SYCHy2vlGdMWMBQlcXoXWAUL2MsBqGKGWAFAM4YvRk721lIamlrE2bBi5jhhDgZyFpW1TZLxPRJIDwaFC6BPogMDrA6F/GSKWYcGZAofl7lwk9nQLVywijA0ydPfqY//P0Hc6EJ5YY5pUh86UwuEQbc5FlyapkLAIli68PNSObDxAEepyIXhISvyXGc5Ylt5XH4q/Q21PuMc/u7pCH342ObkNEfNYAYGbq6ekUHdXLqA9Ae3sqnO2DpusWNgdaLDeG3x5qfjsbtILQVBKPIGJJCDAMM8JQsxcYPTxZADNQW2HDtqS0pCQhCAaEINRIZ30wsF8Q+qWg54Sg54wRL9SsuexVos5jAO/t7VKrAfSMDnDHKYJCJ/UygNkeNPLk7XWkxTIwXxIavtQwLmbmRYlYJGJHJAQVk0CoDbzA6Kmsz/tG8vTS/owYGEzTS/sy2H0oB2bGogVxXNiYxLLmEr6wIWmaamJcnrTItqRUUoCIYBgo+BoZ1w8E0W5B9IKQ9CyT+a0MrIHqtd8+MJfvn1METP3qi+UhB+ezxiqtzaWG0WbYLHVsK+nYClIQAAOtGYE2Op0N+MB4nnYeyFL/YJq2783QroNZHJrwkMkHCDUgBMFSBALghwxjGEoCiajCgoooFtfFcVFTCVqbk2ZpfZIXVkW5NG6RpaSUkgAIaMPIeyHcQpCVRDuFwDYi8awUvFV52FG2/p6JeUVAd3eH7OzsMXsfue2q0njkrwteuJSIamOOBUsKAAxjDMJQm1xBm0OTHu06mKXtgxka2JumnUNZDI3lMZUL4IcGRAQlBaIRAScikIhKlDgSFXEJImAip5F2NTIFjbxv4AUGQWjAzLCUQFncQn2lg6UL42htLuFlzSW8uC7OC8qjHI9KYSkphBAACIE2cPMBjDEjccfaMZn1v9K0bvNTMzbpU6oC1dXLCAArQReWJKNX5vIZxGzBnhfofVMeXh3Oie17MzQwmBYv78+KvSMuxjM+Cr4Gg2BJQjQiUZawkIxKlMYkyuJFox1LgAjwQsa0G4IZWFQdQdQSYAbygUEmrzHlaky7Gpm8Rs4z6N+bxrbd03jwV0MUjUiqSEbQVOPggoYkWptL+MLGJC9aEDPVZVFYimTeQ00iEa3JuMGFAJ6asWluZdAY42VzvraUMP/0P3usXzx7UB0YL2BkykOuEMIwoGTRqyUxhYZKu2hsTKHEEYjOGFsIGOm8xr4xH5NuiGzBoOAbaFM8mlIQbEsg6QiUxRTK4xJNVRE4lgADKPgG6bzBlBti2tVI5zWmcwGeednD0wMTEASKRxXVlNmiriKKjZfV4cb1LUE25wtjjHcaPIAIgFQS/PBzw3jyd2OoKrURtyXqyyMoiyuUxiSS0WJ4E4oeTLsag2M+ptwQmXwxpLVhEBGEACQVo8RSdIREhJoxlgkxMh2CmSEEIWoJJKIC5fFjQfECg0zeYMrVmMyFSLsak9kAO4eykELgUxtbCICcseH0iBADKIkrxGyJmhKFy5YkigloJlwHx3xM5mY8GxiYkxjLM898PZUhAiwiQL6WmkLNmMiGGE0fC0pZTKI8rrCwwsJ5tTakIDyzKwu3EKIkrk6ZJZ0yEzSGERpG1BIwzHjkd9MINB8xVgpAECGi6LXcehxjTwT0G0FRRFDHBaVQrCaSsH5lKaKWQGiK33TWqDADCEJGqHl2Y88waz8VUIKQEYQ8L248r16AqLjnYqwQJ6EdzDhVx80GCs2T1Cucg8UMZPPm+B7iIjFyIoRzvc4qAERAqIHSGOGLf1KOaETBFHvko8BRkjCR8fGPj2Xgh4A4U63emw4AgEAD9aXADde0wCldAKND0Ovi1TAQtwkPPfUCct40bEuCmf+AIsAwmiotQDlI5wHio1OBYQKbEC8MuvBCwIkwNP+BHIGZjgxLFtiwbRthYCAEvUFGEWDtYc+IdzryxLyXOLvGE5QAFtU6gIgcQwZ4Jvm5bh6Doz6UJJzD6D+9CBBCQClx/GxFQBgaJGygqSYGwwKE8BgvS0EYTbs4MGWKAPw+VAEiQjbnolDIQ5DAGylIsctnRONJLKlRWFARQ2ho1nquiHFgzMV4lmGJ34MIEELAzeexfs2VeO/6NcikM5BSHgUBMyPm2Pj7f/4JquQ+VJTFj3R+bwwTCY3BEReuDyQcmhONfVMAoBkD7UgE9Qtq4JYkIOjYVKJkUaBoqbbgRB3kDEMcQ9cIbDzsPpSHZuDcH4B5AKCNQTwWw08ffgIP/vwXIBKzdDoEIgakgxsvrwVJCzMWHn1pICR8z8PuYf8obvCWzwHMjEQ8BlWSOGGeKHg+mmscGFKvvxo4cv6lIKSzLvaOh7DehApwWlXAGAN9nJAtEiCg1AEaq2PQRoCgj60ABIxOuRhJG1hSvAkH4CzxgMMUuLZEoKosDm1m638IUhjsH8lhOs+Q4s2JgFMGgIiO7OIZP9EuChdNVQrJhAPNZpZ2lSA4xJ7hPLyQIOjMWT+XdHLiI0BMPFPkC34Az/OQL0gEAcHzvFlfRCAoRTBGYkmtjUgkiiDkY5McEXToYfdwAXyaFPhw5Bx+RRDyaxoN8fw1QSLhKyUom8njLz/xfnzr79YjKjxELYkv5WYS1+s/xDAcJ4qdu/bgg5/6KyyqjRYrQMjHJFEhCPl8AXtGg3mHP6HYbJUnFKKWwFROgxlobS6BEgQBEJHw5wzA6tWbNHOKhh4LHp7K0MGIbS9YVZ8z3/rxA+LghIsltVFUJhS2Drqw5EzzTpi5yFA4ODwGJ8Joro1DszwuBR6fzmFoQsNS82QAM8l2RaODockAw9PFqnPj+mZjmGQ2VzjICB8uShCbNJA6NQCIiHt7u1T7e1LjQw/f+s1oPLYl8KcMj/0ODzywHVXVDta0JtG3PYPxTHgUhycwDCksrXNQdxwKDABKMA5NFCmwmocCQlQM9dpShcqEwmMvpuEHBh9b24Km6hgzScHsf7Nh7XfGe3u7VHs7hXNKgqvbU5q5S9Qb/t5UJv+KkJb43AdWmLaVjSjAwVTgYPXKepSVlaKyogyV5cVdVVGOmJNAfZlEeWkMepbYZhAEaQwOu8j5Rel8PhFgGFjRGMOrYz5GpgNc2JjAR9Y0mtBATGcLr9SH/D3mLrG6PaXnXAUI4L4+CNqwxVPA10BElUmLP762Cb4fYvuQi4QN1JRIFLwQxmhorWGMhh9qLKq2EHOcI7L5MU/XAXYdyiM0wFyv9g97v67cQmlMYsfBArQ2+OS1i1BTajMJQQr4Gm3Y4vX1nVhhO2EZbG9Phcxd4qmpA93ZrLc1ZBLXv6tRrzqvHKPpAK+OBljZHAcTQZCAEMWrbAJj8QIb0orMIh0XAQl8D3tG5k+BD3t/94iHkWkfq84rw/VXNeiQSWSz3tanpg50M3eJ2WYZ5sYDelqps7NHk8RX2DAlIwY3bWhA4Bfw210TIO2hJm4wmXHhui48P4StiiIIkzqOCCKQybkYHAvmLIIQFa/TGysjiNsCOw4WQABuum4xSmIKzCCS+EpnZ49GTyudHg8AQJ2duru7Q9a/e/ND+x/7Ql9pZdPqjatr9Ie2C9m/N4NIeRzXNkg8vSODhGNj/6FR5KZH0ViTQHgcEUQJYHzKxWjGIKoExCnq+oZfC6jWBgevDHsYnfbxrhVVuO7yBdqwkDnX72tYt/mh7u4OSZ2d+oz0Ah0d3QAI8da/+Hq0tGo18gV8994bMJEphnDUIhQCjURJCVJ33Y+Hfv7vWFCZgJ5VBCEoobH7YAaD44yIAk7lTocZcGZOVHNVBLZF2HGwANsSuPm6xYgoQX6gAcFfBoCOM9kMEZHm7g5JdcufOPDEl3+WjFvv8/xA33n/8/KR/xtG26IEWqps9A2kcWgih2suKkU8ZhdvhGchL4YBy7Kw8WILTlSBTyKCMAMRizAwpLFzOMTyxhh2HvIwnvax8bIFWNNWrUlI6fmFnzWs+4ffcHeHnG1o67S6wZ7DBngjd7omfl00IsQHr6zgnz65k557KYvqtjIkZB7ZvI/zaktg21HkZqZD3niGvRB4x6qluGrlQoDESV3PJKH0ND569wByvg0lgJ2HCkg4Eje/dzFLQZQvBKFU4k5mUE/PWWiGOjt7NHd3y7prv98favqRUBFx9cpa/WdXNSGdZ+waDbGyJYmoJdBSGwUJC8etPsxgUghkGQKRPOH2RRIcKcWBScarYyHaWhy8dKCAiYyP6y6vwzuWVWghlQhD86O6a+7tR0+H6DxF78+9He7vZ2YQKZly876rDYtPb1jEFUkLOw/m4WvGikYb9ZWzV4Bjs0EIgj7hBgws0tg3kkZ5woakovcrkxHctHExG4Zw875LSqWYQehfNidSMScAKJUyQLeov+buQT8090upRNuSUn39VQ2Yygbo31/A5UuiaKqJIzB0Cj0endIW0Dg0UUBjZRQ7DhUwmQ1w/VUNaFtSqqVUwg/N/fXX3D0IdAs6ySjuaQsimzb1MzOT1OaurOtNhAbik9e28MKqKHYdKiDQGtXlcYT68GjE6W0igu/7IBjkfGD3cAELK6P45LUtHBqIrOtNSG3uYmbatKl/zox6zgCkUinT17dJ1m/YMmqMuVtKKZYuTJiPrmlCJhPACzSisTg0E0gogBQYcl4bkCCh4Ac+Mq7BK4c8TOcC3LCmCUsXJoyUUhhj7q7fsGW0r2+TTM3R+/PWBFevTmnu6hIvue4WgveZWFTVf3xds/nXR/eJC+oiSCgfxpuG1gwiIKLm3u8LAvzAgIWECtP41csu9o17WFwXx8fWNptQQ+QL3lDatbdwV5fA6pSejy3znRDh3l7I9vZvZw48evs3pJL311U45rPvXYT/7NuB7QeeBzOwssnBgckAu4Y9RCOnDgIRUPAZi2ttLKyw8PxgHs/sDpBzPXz8w+ejocphJimA8BsXve/bmd7eLtVOCOerX85PhgII3EX9PVDlZVMvOFHrvIIf8u33vyC7H9+HWMJCQ2UEly2J48X9ebywN4+oRUck8RN9TCFgLG90cHGTg2d25bBvzEcQhvhIeyP+5sbl2rEVuYXglcmpshWtHQhB8x+fn7cqXHxhKy3vTPkM3Bl3IoIA+v7tbebrn7gIljAYnvLw3O4sLl8Sw7qLk4haQDJKKHMIpW/YZQ4h6RBsC3jPigSuOC+G53ZlMTzlQQmDL3/oAmy+eaURBIo5EcHAncs7Uz7QSqczUHJasjhRsVFqWLu5Z2zcvVUpSfmAxR0fOF9/53OrEI9K7B3z8PDz06hKKlx9UQlCzfB1cSBKm+I2DPi62OW968IkakstPPz8NAbHPDgRiS2fXYUvdJ6vvZCFUpLGptxbG9Zu7unu7pCzjc2fkyNwNLHrEkQpM/TI7WttWz5ARNURhXDbrml1y31bsX1fBpXJCK68IAHbEnj8xTS80BT1RBQldCUJa1pLwAw89VIGY2kfFyxM4L5bVuGSpWWhH0Ix87jnhzcsXLv5kcPvfEtcjBClDPd2qYXr7n10KhO8kw1vZUi1vCUZPvjVK7D2klqMpT30DmQwlQuxoa0UJVEJf2a+LxYR2NBWBtczeLw/jdFpD2tWVuPBr16BixeXhAyhmPn59FTunQvXbn6Ee7vUmTAemBlMPRMr9cMnTW9vl3rbum+N/fnGVf8RtcVS27ZWKAH9vivqyPUM/Wb7OIYmAzi2wOXnJTAyHSBiCaxbWYp9Yz6e3pFFLh/iE+sXYfPNbRyLKmPbEZUvBP+1a//wn6744A8O9PZ2qUUnUXnO+RF4/Xr9XP7w459P2RH5tYIfImYL84NHB8WmHw2gEDDamuNY0egAAAaG8ti6JwclCV+94SJ8akOLcT0jHNtCvhD87YJ33/OlNz77LQvAzMUH9fR0is7OHj306G0fti31Lww4MZvCJ7aNqdvv34bBkTwubklACmDbnizqK6K45+aVWHtJjXZ9loLg+YH+dP177v0hc5cANvF8/zDqnANwBIjeLkXtqXDvL2+9NGapnygpFxGZcM9BV91y31b878uTAIBVS0rxnVtW4YLGRKhZKK3NvmzO+3DLxvt+ffgZZ+sbz/pUwmEDXvzvzyyoiUd/HLOtdi8IwoJn5B3ffZ68QGPLZ9qQiKnQsixV8INfT7rBh87fsGX/2Tb+nAAAFP+kjjp7dFfX1eqzV79tixO1bip4vtGGwQxSgthxIiKf9/9teGz/p5d39vhzkbXe8gAAAHd1CWwqnuODT9xxqxLiXqOZmBmRiETg6y/Wvvueu4gAY85MjX/LLWZQb2+XAoChR29bN9L7+anRJz6f3/vIre8/HCnMIPyhL54BYfCh21v3//Jzq17/b380q7u7Q8728x/VYu4SxRr/5q3/B15Msk5/b3cFAAAAAElFTkSuQmCC"
)


def make_icon(root, n: int = 32):
    """Fenstersymbol aus dem eingebetteten PNG (L im Schild)."""
    return tk.PhotoImage(master=root, data=ICON_PNG)


# --------------------------------------------------------------------------- #
# Dialoge im App-Design
# --------------------------------------------------------------------------- #

BACKUP_KINDS = {"": "manuell", "auto": "automatisch vor Update", "vorher": "vor Wiederherstellung"}


class Placeholder:
    """Grauer Hinweistext in einem leeren Eingabefeld – verschwindet, sobald man tippt."""

    def __init__(self, entry, var: tk.StringVar, text: str):
        t = THEME
        self.entry, self.var = entry, var
        self.lbl = tk.Label(entry, text=text, fg=t["text_faint"], bg=t["bg"], cursor="xterm",
                            font=entry.winfo_toplevel().fonts["base"] if hasattr(entry.winfo_toplevel(), "fonts")
                            else None)
        self.lbl.bind("<Button-1>", lambda e: entry.focus_set())
        var.trace_add("write", lambda *a: self.update())
        entry.bind("<FocusIn>", lambda e: self.update(), add="+")
        entry.bind("<FocusOut>", lambda e: self.update(), add="+")
        self.update()

    def update(self):
        try:
            if self.var.get():
                self.lbl.place_forget()
            else:
                self.lbl.place(x=6, rely=0.5, anchor="w")
        except tk.TclError:
            pass


SET_ALL_OFF = "— Alle deaktivieren —"
SET_ALL_ON = "— Alle aktivieren —"


class App(BaseTk):
    COLUMNS = ("status", "name", "title", "version", "interface", "source", "update")
    current_page = "addons"
    UPDATE_MODES = {"auto": "Automatisch installieren", "ask": "Nur nachfragen", "off": "Aus"}
    HEADINGS = {
        "status": "Status", "name": "Ordner", "title": "Titel", "version": "Version",
        "interface": "Interface", "source": "Quelle", "update": "Update",
    }

    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} {APP_VERSION}")
        self.geometry("1280x800")
        self.minsize(1080, 600)

        self.cfg = load_config()
        self.addons: list[Addon] = []
        self.updates: dict[str, str] = {}       # repo -> neuester Tag
        self.update_info: dict[str, dict] = {}  # repo -> Release-Infos
        self.game: tuple[str, int] | None = None
        self.jobs: queue.Queue = queue.Queue()
        self.quiet_jobs: queue.Queue = queue.Queue()
        self.watching_build = ""
        self._watch_manual = False
        self._cf_search_state: dict = {}
        self.dl_watch: dict[str, dict] = {}     # CurseForge-Updates, deren ZIP im Download-Ordner erwartet wird
        self._dl_job = None
        self._dl_seen: dict[str, tuple] = {}     # Pfad -> (Größe, Änderungszeit) beim letzten Blick
        self._dl_checked: dict[str, tuple] = {}  # Pfad -> (Größe, Änderungszeit) schon geprüft, passte nicht
        self.modal_download = False
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
        self.after(900, self._report_self_update)
        if self.cfg.get("self_update_mode") != "off":
            self.after(1500, lambda: self.check_manager_update(silent=True))
        log.info("Gestartet: %s %s, Ordner=%s, Version=%s", APP_NAME, APP_VERSION,
                 self.root_path, self.flavor)

    # ----- Aufbau ---------------------------------------------------------- #
    PAGES = [("addons", "Meine Addons"), ("discover", "Entdecken"), ("updates", "Updates"),
             ("share", "Teilen"), ("sets", "Sets & Backups"), ("settings", "Einstellungen")]
    TREE_SHOWN = ("status", "name", "version", "source", "update")

    def _build_ui(self):
        self.fonts = apply_theme(self)
        try:
            self._icon = make_icon(self)
            self.iconphoto(True, self._icon)
        except tk.TclError:
            pass
        self._init_vars()
        self._build_layout()

    def _init_vars(self):
        self.root_var = tk.StringVar(value=self.cfg["wow_root"])
        self.flavor_var = tk.StringVar(value=self.cfg["flavor"])
        self.set_var = tk.StringVar()
        self.set_name_var = tk.StringVar()
        self.auto_backup_var = tk.BooleanVar(value=bool(self.cfg.get("auto_backup")))
        self.check_start_var = tk.BooleanVar(value=bool(self.cfg.get("check_on_start")))
        self.self_update_var = tk.StringVar(value=self.UPDATE_MODES.get(self.cfg.get("self_update_mode"),
                                                                         self.UPDATE_MODES["auto"]))
        self.count_var = tk.StringVar()
        self.upd_count_var = tk.StringVar()
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *a: self.render())
        self.group_var = tk.BooleanVar(value=bool(self.cfg.get("group_addons", True)))
        self.status_var = tk.StringVar(value="Bereit.")
        self.game_info_var = tk.StringVar()
        self.view = self.cfg.get("addon_view", "list") if self.cfg.get("addon_view") in ("list", "cards") else "list"
        # Entdecken
        self.cf_link_var = tk.StringVar()
        self.gh_var = tk.StringVar()
        self.zip_var = tk.StringVar()
        self.cf_q = tk.StringVar()
        self.cf_sort = tk.StringVar(value="Beliebtheit")
        self.cf_fit = tk.BooleanVar(value=True)
        self.cf_count = tk.StringVar()
        self.cf_file_var = tk.StringVar()
        # Teilen
        self.share_scope = tk.StringVar(value="all")
        self.share_exact = tk.BooleanVar(value=True)
        self.share_sets = tk.BooleanVar(value=True)
        self.share_size = tk.StringVar(value=next(iter(LIST_PART_SIZES)))
        self.share_info = tk.StringVar()
        self.import_preview = tk.StringVar()
        self.recv_file_var = tk.StringVar()
        self.sort_col, self.sort_rev = "name", False
        self.open_groups: set[str] = set()
        self.group_members: dict[str, list[str]] = {}

    def _build_layout(self):
        t = THEME
        self.tree = None
        self.cards_inner = None
        self.detail = None
        self.nav_buttons: dict[str, ttk.Button] = {}
        self.pages: dict[str, ttk.Frame] = {}
        self.banners: dict[str, tk.Frame] = {}
        self.asking = 0
        self.ui = ttk.Frame(self)
        self.ui.pack(fill="both", expand=True)
        root = self.ui
        self._w_statusbar(root)

        side = ttk.Frame(root, style="Side.TFrame", width=238, padding=(14, 18, 14, 16))
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        tk.Frame(root, width=1, bg=t["line"]).pack(side="left", fill="y")
        brand = tk.Canvas(side, height=64, bg=t["header"], highlightthickness=0)
        brand.pack(fill="x")
        draw_emblem(brand, 0, 4, 54)
        brand.create_text(64, 20, anchor="w", fill=t["gold"], font=self.fonts["label"],
                          text=spaced("Addon Manager"))
        brand.create_text(63, 44, anchor="w", fill=t["gold"], font=self.fonts["side"], text="LEEROYZAN")
        ttk.Label(side, text=spaced("Spielversion"), style="SideKicker.TLabel").pack(anchor="w", pady=(18, 6))
        self._w_flavor(side, width=22).pack(fill="x")
        nav = ttk.Frame(side, style="Side.TFrame")
        nav.pack(fill="x", pady=(18, 0))
        for key, label in self.PAGES:
            b = ttk.Button(nav, text=label, style="Nav.TButton", cursor="hand2",
                           command=lambda k=key: self.show_page(k))
            b.pack(fill="x", pady=1)
            self.nav_buttons[key] = b
        bottom = ttk.Frame(side, style="Side.TFrame")
        bottom.pack(side="bottom", fill="x")
        tk.Frame(bottom, height=1, bg=t["line"]).pack(fill="x", pady=(0, 12))
        self._w_play(bottom, style="BigPlay.TButton", text="▶   SPIELEN").pack(fill="x")
        ttk.Label(bottom, textvariable=self.game_info_var, style="Side.TLabel", anchor="center",
                  justify="center", wraplength=200).pack(fill="x", pady=(8, 0))
        ttk.Label(bottom, text=f"Leeroyzan v{APP_VERSION}", style="Side.TLabel",
                  anchor="center").pack(fill="x")

        main = ttk.Frame(root)
        main.pack(side="left", fill="both", expand=True)
        self.notice_host = ttk.Frame(main, padding=(26, 14, 26, 0))
        self.notice_host.bind("<Configure>", lambda e: self._wrap_banners(e.width))
        self.page_host = ttk.Frame(main)
        self.page_host.pack(fill="both", expand=True)
        self._page_addons()
        self._page_discover()
        self._page_updates()
        self._page_share()
        self._page_sets()
        self._page_settings()
        self.show_page(self.cfg.get("last_page", "addons"))

    # ----- Hinweisleiste statt Pop-up-Fenstern ------------------------------ #
    BANNER_STYLE = {   # Akzent, Hintergrund, Symbol
        "info": ("gold", "panel", "ℹ"), "ok": ("ok", "panel", "✓"), "warn": ("warn", "panel", "⚠"),
        "error": ("warn", "panel", "✕"), "ask": ("gold", "active", "?"), "update": ("update", "update_bg", UPD_MARK),
    }

    def notify(self, text: str, kind: str = "info", actions=(), key: str = "", timeout: int = 0,
               closable: bool = True, extra=None) -> str:
        """Zeigt eine Meldung oben im Fenster – mit optionalen Knöpfen. Ersetzt Pop-up-Fenster."""
        t = THEME
        # Erfolgs- und Info-Meldungen teilen sich einen Platz, damit sich nichts stapelt
        if kind in ("ok", "info"):
            key = "toast"          # Erfolgs-/Info-Meldungen teilen sich einen Platz, damit sich nichts stapelt
        key = key or f"{kind}:{text[:80]}"          # gleiche Meldung ersetzt sich selbst
        self.dismiss(key)
        others = [k for k in self.banners if not k.startswith("ask")]
        while len(others) >= 2:
            self.dismiss(others.pop(0))
        accent, bg, icon = self.BANNER_STYLE.get(kind, self.BANNER_STYLE["info"])
        accent, bg = t[accent], t[bg]
        frame = tk.Frame(self.notice_host, bg=bg, highlightthickness=1, highlightbackground=t["border_soft"])
        tk.Frame(frame, width=4, bg=accent).pack(side="left", fill="y")
        body = tk.Frame(frame, bg=bg)
        body.pack(side="left", fill="both", expand=True, padx=(12, 8), pady=10)
        top = tk.Frame(body, bg=bg)
        top.pack(fill="x")
        tk.Label(top, text=icon, bg=bg, fg=accent, font=self.fonts["bold"]).pack(side="left", anchor="n", padx=(0, 10))
        lbl = tk.Label(top, text=text, bg=bg, fg=t["text"], font=self.fonts["base"], justify="left", anchor="w",
                       wraplength=max(300, self.notice_host.winfo_width() - 380))
        lbl.pack(side="left", fill="x", expand=True)
        frame._wrap_label = lbl
        if closable:
            x = tk.Label(top, text="✕", bg=bg, fg=t["text_faint"], cursor="hand2", font=self.fonts["base"])
            x.pack(side="right", anchor="n", padx=(8, 0))
            x.bind("<Button-1>", lambda e: self.dismiss(key))
        btns = tk.Frame(top, bg=bg)
        btns.pack(side="right", anchor="n")
        frame._buttons = []
        for i, (label, cmd) in enumerate(actions):
            def run(c=cmd):
                self.dismiss(key)
                c()
            b = ttk.Button(btns, text=label, style="Primary.TButton" if i == 0 else "TButton", command=run)
            b.pack(side="left", padx=(8, 0))
            frame._buttons.append(b)
        if extra:
            extra(body, bg)
        frame.pack(fill="x", pady=(0, 8))
        self.banners[key] = frame
        if not self.notice_host.winfo_ismapped():
            self.notice_host.pack(fill="x", before=self.page_host)
        if timeout:
            self.after(timeout, lambda: self.dismiss(key) if self.banners.get(key) is frame else None)
        return key

    def dismiss(self, key: str):
        frame = self.banners.pop(key, None)
        if frame is not None:
            try:
                frame.destroy()
            except tk.TclError:
                pass
        if not self.banners and self.notice_host.winfo_ismapped():
            self.notice_host.pack_forget()

    def _wrap_banners(self, width: int):
        for f in self.banners.values():
            try:
                f._wrap_label.configure(wraplength=max(300, width - 380))
            except (tk.TclError, AttributeError):
                pass

    def info(self, text: str, **kw):
        self.set_status(text.split("\n")[0])
        self.notify(text, "info", timeout=12000)

    def warn(self, text: str, **kw):
        self.notify(text, "warn")

    def error(self, text: str, **kw):
        self.set_status("Fehler: " + text.split("\n")[0])
        self.notify(text, "error")

    def _wait_inline(self, text: str, kind: str, buttons, extra=None, default=None):
        """Frage oben im Fenster; wartet (ohne neues Fenster), bis ein Knopf gedrückt wird."""
        var = tk.StringVar(value="")
        key = f"ask{time.time()}"
        answer = {"v": default}

        def choose(v):
            answer["v"] = v() if callable(v) else v
            var.set("x")
        self.notify(text, kind, [(label, (lambda v=v: choose(v))) for label, v in buttons], key=key,
                    closable=False, extra=extra)
        banner = self.banners[key]
        self.show_notice(banner)
        old = (self.bind("<Escape>"), self.bind("<Return>"))
        self.bind("<Escape>", lambda e: choose(buttons[-1][1]))
        if banner._buttons:
            self.bind("<Return>", lambda e: choose(buttons[0][1]))
        self.asking += 1
        try:
            self.wait_variable(var)
        except tk.TclError:
            return default
        finally:
            self.asking -= 1
            try:
                self.bind("<Escape>", old[0] or "")
                self.bind("<Return>", old[1] or "")
            except tk.TclError:
                pass
        self.dismiss(key)
        return answer["v"]

    def show_notice(self, banner):
        """Sorgt dafür, dass eine Frage sichtbar ist (die Leiste liegt über jeder Seite)."""
        try:
            banner.focus_set()
        except tk.TclError:
            pass

    def ask(self, text: str, yes: str = "Ja", no: str = "Nein", **kw) -> bool:
        return bool(self._wait_inline(text, "ask", [(yes, True), (no, False)], default=False))

    def ask_text(self, text: str, initial: str = "", ok: str = "OK") -> str | None:
        var = tk.StringVar(value=initial)
        holder = {}

        def extra(body, bg):
            e = ttk.Entry(body, textvariable=var)
            e.pack(fill="x", pady=(8, 0))
            holder["e"] = e
            self.after(50, lambda: (e.focus_set(), e.icursor("end")))
        res = self._wait_inline(text, "ask", [(ok, lambda: var.get().strip() or None), ("Abbrechen", None)],
                                extra=extra)
        return res or None

    def ask_choice(self, text: str, options: list[str], default: int = 0, ok: str = "Auswählen") -> int | None:
        var = tk.StringVar(value=options[default] if options else "")

        def extra(body, bg):
            cb = ttk.Combobox(body, textvariable=var, values=options, state="readonly")
            cb.pack(fill="x", pady=(8, 0))
        res = self._wait_inline(text, "ask", [(ok, lambda: options.index(var.get()) if var.get() in options else None),
                                              ("Abbrechen", None)], extra=extra)
        return res

    # ----- Gemeinsame Bausteine ------------------------------------------- #
    def _w_path(self, parent, frame_style="Panel.TFrame", label_style="Panel.TLabel", with_version=False):
        row = ttk.Frame(parent, style=frame_style)
        ttk.Label(row, text="WoW-Ordner", style=label_style).pack(side="left", padx=(0, 8))
        entry = ttk.Entry(row, textvariable=self.root_var, width=50)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda e: self._load_flavors())
        entry.bind("<FocusOut>", lambda e: self._load_flavors()
                   if self.root_path != self.cfg.get("wow_root") else None)
        ttk.Button(row, text="Durchsuchen…", command=self.browse_root).pack(side="left", padx=(8, 0))
        return row

    def _w_flavor(self, parent, width=24):
        cb = ttk.Combobox(parent, textvariable=self.flavor_var, state="readonly", width=width)
        cb.bind("<<ComboboxSelected>>", lambda e: self.on_flavor_change())
        self.flavor_cb = cb
        return cb

    def _w_check(self, parent, text, var, panel=True):
        return ttk.Checkbutton(parent, text=text, variable=var, command=self.save_options,
                               style="TCheckbutton" if panel else "Plain.TCheckbutton")

    def _w_update_mode(self, parent, label_style="Panel.TLabel"):
        row = ttk.Frame(parent, style="Panel.TFrame" if label_style == "Panel.TLabel" else "TFrame")
        ttk.Label(row, text="Leeroyzan-Updates", style=label_style).pack(side="left", padx=(0, 8))
        cb = ttk.Combobox(row, textvariable=self.self_update_var, state="readonly", width=21,
                          values=list(self.UPDATE_MODES.values()))
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda e: self.save_options())
        return row

    def _w_filter(self, parent, width=24):
        return ttk.Entry(parent, textvariable=self.filter_var, width=width)

    def _w_play(self, parent, style="Play.TButton", text="▶  Spielen"):
        self.play_btn = ttk.Button(parent, text=text, style=style, cursor="hand2", command=self.start_wow)
        return self.play_btn

    def _w_statusbar(self, parent):
        t = THEME
        ttk.Label(parent, textvariable=self.status_var, style="Status.TLabel",
                  anchor="w").pack(fill="x", side="bottom")
        tk.Frame(parent, height=1, bg=t["line"]).pack(fill="x", side="bottom")

    def _menu(self, parent, items) -> tk.Menu:
        t = THEME
        m = tk.Menu(parent, tearoff=False, bg=t["panel"], fg=t["text"], activebackground=t["active"],
                    activeforeground=t["gold"], bd=0, relief="flat", font=self.fonts["base"])
        for item in items:
            if item is None:
                m.add_separator()
            else:
                m.add_command(label="  " + item[0] + "  ", command=item[1])
        return m

    def _framed(self, parent):
        """Feiner Rahmen um Listen."""
        return tk.Frame(parent, bg=THEME["border_soft"], highlightthickness=0)

    def _list(self, parent, columns, widths, stretch=(), height=4, select="browse"):
        """Treeview mit Theme und Scrollbar (für Suchergebnisse, Updates, Sets, Backups)."""
        t = THEME
        frame = self._framed(parent)
        tv = ttk.Treeview(frame, columns=[c for c, _ in columns], show="headings", selectmode=select, height=height)
        for (cid, label), w in zip(columns, widths):
            tv.heading(cid, text=spaced(label) if label else "", anchor="w")
            tv.column(cid, width=w, anchor="w", stretch=cid in stretch)
        tv.tag_configure("odd", background=t["row_b"])
        tv.tag_configure("dim", foreground=t["text_faint"])
        tv.tag_configure("other", foreground=t["text_dim"])
        tv.tag_configure("update", foreground=t["update"])
        sb = ttk.Scrollbar(frame, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=sb.set)
        tv.pack(side="left", fill="both", expand=True, padx=(1, 0), pady=1)
        sb.pack(side="right", fill="y", padx=(0, 1), pady=1)
        return frame, tv

    def _text(self, parent, height=4, readonly=True):
        t = THEME
        frame = self._framed(parent)
        txt = tk.Text(frame, wrap="word", height=height, bg=t["row_a"], fg=t["text"], insertbackground=t["gold"],
                      highlightthickness=0, borderwidth=0, padx=10, pady=8, font=self.fonts["base"])
        txt.tag_configure("head", foreground=t["gold"], font=self.fonts["bold"], spacing1=4, spacing3=4)
        txt.tag_configure("dim", foreground=t["text_dim"])
        sb = ttk.Scrollbar(frame, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        txt.pack(side="left", fill="both", expand=True, padx=(1, 0), pady=1)
        sb.pack(side="right", fill="y", padx=(0, 1), pady=1)
        if readonly:
            txt.configure(state="disabled")
        return frame, txt

    @staticmethod
    def _set_text(txt: tk.Text, content: str = "", parts=None):
        txt.configure(state="normal")
        txt.delete("1.0", "end")
        for text, tag in (parts or [(content, "")]):
            txt.insert("end", text, tag)
        txt.configure(state="disabled")

    def _w_tree(self, parent):
        t = THEME
        body = self._framed(parent)
        self.tree = ttk.Treeview(body, columns=self.COLUMNS, displaycolumns=self.TREE_SHOWN,
                                 show="tree headings", selectmode="extended")
        self.tree.column("#0", width=30, minwidth=30, stretch=False)
        self.tree.heading("#0", text="")
        self.tree.bind("<<TreeviewOpen>>", lambda e: self._remember_open(True))
        self.tree.bind("<<TreeviewClose>>", lambda e: self._remember_open(False))
        widths = {"status": 95, "name": 170, "title": 200, "version": 95,
                  "interface": 90, "source": 140, "update": 160}
        for c in self.COLUMNS:
            self.tree.heading(c, text=spaced(self.HEADINGS[c]), anchor="w",
                              command=lambda c=c: self.sort_by(c))
            self.tree.column(c, width=widths[c], anchor="w", stretch=c in ("name", "update"))
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
        self.tree.bind("<Button-3>", self._on_right_click)
        self.tree.bind("<Control-a>", lambda e: (self.select_all(force=True), "break")[1])
        self.tree.bind("<Control-A>", lambda e: (self.select_all(force=True), "break")[1])
        self.tree.bind("<Delete>", lambda e: self.remove_selected())
        self.tree.bind("<<TreeviewSelect>>", lambda e: (self.update_detail(), self._hint_update_link()))
        return body

    def _scroll_area(self, parent, bg):
        """Scrollbarer Bereich (Canvas + Frame) mit Mausrad-Unterstützung."""
        wrap = tk.Frame(parent, bg=bg)
        canvas = tk.Canvas(wrap, bg=bg, highlightthickness=0)
        vsb = ttk.Scrollbar(wrap, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg=bg)
        win = canvas.create_window(0, 0, window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: (canvas.itemconfigure(win, width=e.width), self._relayout_cards()))
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        def wheel(e):
            delta = -1 if (getattr(e, "delta", 0) > 0 or getattr(e, "num", 0) == 4) else 1
            canvas.yview_scroll(delta * 2, "units")
        canvas.bind("<Enter>", lambda e: (canvas.bind_all("<MouseWheel>", wheel),
                                          canvas.bind_all("<Button-4>", wheel),
                                          canvas.bind_all("<Button-5>", wheel)))
        canvas.bind("<Leave>", lambda e: (canvas.unbind_all("<MouseWheel>"),
                                          canvas.unbind_all("<Button-4>"),
                                          canvas.unbind_all("<Button-5>")))
        self.cards_canvas = canvas
        return wrap, inner

    def update_game_info(self):
        text = f"Spielversion {self.game[0]}  ·  Interface {self.game[1]}" if self.game else "Spielversion unbekannt"
        self.game_info_var.set(text)

    def open_log(self):
        if LOG_FILE.exists():
            open_in_explorer(LOG_FILE)

    # ----- Seiten ------------------------------------------------------------ #
    def _page(self, key, kicker, title, text=""):
        page = ttk.Frame(self.page_host, padding=(26, 18, 26, 14))
        self.pages[key] = page
        head = ttk.Frame(page)
        head.pack(fill="x")
        left = ttk.Frame(head)
        left.pack(side="left", fill="x", expand=True)
        ttk.Label(left, text=spaced(kicker), style="Kicker.TLabel").pack(anchor="w")
        ttk.Label(left, text=title, style="PageTitle.TLabel").pack(anchor="w", pady=(2, 0))
        if text:
            ttk.Label(page, text=text, style="Body.TLabel", wraplength=900, justify="left").pack(
                anchor="w", pady=(6, 0))
        return page, head

    def _card_box(self, parent, pady=(16, 0), fill="x", expand=False, title="", side=None, padx=(0, 0)):
        t = THEME
        box = tk.Frame(parent, bg=t["panel"], highlightthickness=1,
                       highlightbackground=t["border_soft"], highlightcolor=t["border_soft"])
        if side:
            box.pack(side=side, fill=fill, expand=expand, pady=pady, padx=padx)
        else:
            box.pack(fill=fill, expand=expand, pady=pady, padx=padx)
        inner = ttk.Frame(box, style="Panel.TFrame", padding=(16, 14))
        inner.pack(fill="both", expand=True)
        if title:
            ttk.Label(inner, text=spaced(title), style="CardKicker.TLabel").pack(anchor="w", pady=(0, 10))
        return inner

    def show_page(self, key):
        if key not in self.pages:
            key = "addons"
        for k, p in self.pages.items():
            p.pack_forget()
            self.nav_buttons[k].configure(style="NavActive.TButton" if k == key else "Nav.TButton")
        self.pages[key].pack(fill="both", expand=True)
        self.cfg["last_page"] = key
        self.current_page = key
        if key == "sets":
            self.refresh_sets_overview()
            self.refresh_backup_list()
        elif key == "discover":
            self.refresh_zip_choices()
            if not self.cf_results and self.cf_key() and not self.cf_searching:
                self.cf_search()
        elif key == "share":
            self.rebuild_share()
            self.refresh_recv_files()
        elif key == "updates":
            self.refresh_updates_page()

    # --- Meine Addons --------------------------------------------------------- #
    def _page_addons(self):
        t = THEME
        page, head = self._page("addons", "Addons & UI", "Meine Addons")
        right = ttk.Frame(head)
        right.pack(side="right", anchor="s")
        ttk.Button(right, text="+  Addons hinzufügen", style="Primary.TButton",
                   command=lambda: self.show_page("discover")).pack(side="right")
        self.view_btns = {}
        seg = ttk.Frame(right)
        seg.pack(side="right", padx=(0, 14))
        for key, label in (("list", "Liste"), ("cards", "Kacheln")):
            b = ttk.Button(seg, text=label, width=8, command=lambda k=key: self.set_view(k))
            b.pack(side="left")
            self.view_btns[key] = b
        ttk.Checkbutton(right, text="Gruppieren", variable=self.group_var, style="Plain.TCheckbutton",
                        command=self._toggle_grouping).pack(side="right", padx=(0, 16))
        self._w_filter(right, width=22).pack(side="right", padx=(0, 16), ipady=3)
        ttk.Label(right, text="Suche").pack(side="right", padx=(0, 8))

        ttk.Label(page, textvariable=self.count_var, style="Body.TLabel").pack(anchor="w", pady=(6, 0))
        body = ttk.Frame(page)
        body.pack(fill="both", expand=True, pady=(8, 0))
        detail = ttk.Frame(body, style="Detail.TFrame", width=300, padding=(18, 16))
        detail.pack(side="right", fill="y")
        detail.pack_propagate(False)
        tk.Frame(body, width=1, bg=t["line"]).pack(side="right", fill="y")
        self._build_detail(detail)

        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True, padx=(0, 14))
        bar = tk.Frame(left, bg=t["panel"], highlightthickness=1, highlightbackground=t["border"])
        bar.pack(side="bottom", fill="x", pady=(10, 0))
        barin = ttk.Frame(bar, style="Panel.TFrame", padding=(10, 8))
        barin.pack(fill="x")
        for text, cmd in (("Alle auswählen", self.select_all), ("An / Aus", self.toggle_selected),
                          ("Teilen …", lambda: self.share_selection())):
            ttk.Button(barin, text=text, command=cmd).pack(side="left", padx=(0, 6))
        ttk.Button(barin, text="Entfernen", command=self.remove_selected, style="Danger.TButton").pack(side="left")
        self.list_host = ttk.Frame(left)
        self.list_host.pack(fill="both", expand=True)
        self.tree_frame = self._w_tree(self.list_host)
        self.cards_wrap, self.cards_inner = self._scroll_area(self.list_host, t["bg"])
        self.set_view(self.view, save=False)

    def set_view(self, view: str, save: bool = True):
        self.view = view
        if save:
            self.cfg["addon_view"] = view
            save_config(self.cfg)
        for k, b in self.view_btns.items():
            b.configure(style="Primary.TButton" if k == view else "TButton")
        if view == "cards":
            self.tree_frame.pack_forget()
            self.cards_wrap.pack(fill="both", expand=True)
        else:
            self.cards_wrap.pack_forget()
            self.tree_frame.pack(fill="both", expand=True)
        if save:
            self.render()

    def _build_detail(self, parent):
        self.detail = {
            "kicker": ttk.Label(parent, style="DetailKicker.TLabel"),
            "title": ttk.Label(parent, style="DetailTitle.TLabel", wraplength=260),
            "note": ttk.Label(parent, style="DetailNote.TLabel", wraplength=260, justify="left"),
        }
        self.detail["kicker"].pack(anchor="w")
        self.detail["title"].pack(anchor="w", pady=(4, 0))
        self.detail["note"].pack(anchor="w", pady=(6, 0))
        grid = ttk.Frame(parent, style="Detail.TFrame")
        grid.pack(fill="x", pady=(14, 0))
        for i, (key, label) in enumerate((("version", "Version"), ("release", "Quelle"),
                                           ("iface", "Interface"), ("folders", "Ordner"),
                                           ("deps", "Benötigt"))):
            ttk.Label(grid, text=label, style="DetailKey.TLabel").grid(row=i, column=0, sticky="nw", pady=4)
            v = ttk.Label(grid, style="DetailVal.TLabel", wraplength=170, justify="left")
            v.grid(row=i, column=1, sticky="nw", padx=(12, 0), pady=4)
            self.detail[key] = v
        btns = ttk.Frame(parent, style="Detail.TFrame")
        btns.pack(fill="x", side="bottom")
        d = self.detail
        d["upd_btn"] = ttk.Button(btns, text=f"{UPD_MARK}  Jetzt aktualisieren", style="Primary.TButton",
                                  command=self.update_selected)
        d["dl_btn"] = ttk.Button(btns, text="Bei CurseForge herunterladen ↗", command=self.open_cf_update)
        d["link_btn"] = ttk.Button(btns, text="Mit CurseForge verknüpfen", command=self.link_curseforge_dialog)
        d["page_btn"] = ttk.Button(btns, text="Quelle öffnen ↗", command=self._detail_release)
        d["folder_btn"] = ttk.Button(btns, text="Ordner öffnen", command=self.open_folder)
        d["rm_btn"] = ttk.Button(btns, text="Entfernen", command=self.remove_selected, style="Danger.TButton")
        self.update_detail()

    def update_detail(self):
        if not self.detail:
            return
        d = self.detail
        try:
            sel = self.selected_addons()
        except tk.TclError:
            return
        for k in ("upd_btn", "dl_btn", "link_btn", "page_btn", "folder_btn", "rm_btn"):
            d[k].pack_forget()
        if not sel:
            d["kicker"].configure(text=spaced("Details"))
            d["title"].configure(text="Kein Addon gewählt")
            d["note"].configure(text="Wähle links ein Addon oder eine Gruppe aus." if self.addons else
                                "Noch keine Addons installiert – über „Addons hinzufügen“ geht es los.")
            for k in ("version", "release", "iface", "folders", "deps"):
                d[k].configure(text="–", foreground=THEME["text"])
            return
        names = {a.name for a in sel}
        main = self._group_main(sel)
        sid, src = self.folder_source(main.name)
        d["kicker"].configure(text=spaced(self.source_label(sid, src).replace(": ", " · "))
                              if src else spaced("Manuell installiert"))
        d["title"].configure(text=main.title if len(sel) == 1 else f"{main.title}  ({len(sel)} Ordner)")
        d["note"].configure(text=clean_wow_text(main.meta.get("notes", "")) or "Keine Beschreibung in der .toc.")
        d["version"].configure(text=main.version or "–")
        pending = bool(src and self.updates.get(sid) and self.updates[sid] != src.get("tag"))
        if src:
            if pending:
                d["release"].configure(text=f"{UPD_MARK} Update: {self.updates[sid]}", foreground=THEME["update"])
            else:
                d["release"].configure(text=src.get("tag", "–") + ("  · aktuell" if sid in self.updates else ""),
                                       foreground=THEME["text"])
        else:
            d["release"].configure(text="manuell – keine Update-Prüfung", foreground=THEME["text_dim"])
        if self.is_outdated(main):
            d["iface"].configure(text=f"{main.interface} · veraltet", foreground=THEME["old"])
        else:
            d["iface"].configure(text=(main.interface or "–") + (" · passt" if self.game and main.interfaces else ""),
                                 foreground=THEME["text"])
        d["folders"].configure(text=", ".join(sorted(names)), foreground=THEME["text"])
        deps = sorted({x for a in sel for x in a.deps if x not in names})
        d["deps"].configure(text=", ".join(deps) or "–", foreground=THEME["text"])
        order = []
        if pending:
            order.append("upd_btn")
            if self.cf_update_link(sel)[1]:
                order.append("dl_btn")
        if not src:
            order.append("link_btn")
        else:
            order.append("page_btn")
        order += ["folder_btn", "rm_btn"]
        for k in order:
            d[k].pack(fill="x", pady=(0, 6))

    def _detail_release(self):
        sel = self.selected_addons()
        if not sel:
            return
        sid, src = self.folder_source(self._group_main(sel).name)
        if src:
            self.open_link(self.source_page(sid, src))

    # --- Entdecken ------------------------------------------------------------ #
    def _page_discover(self):
        t = THEME
        page, _ = self._page("discover", "Entdecken", "Addons finden und installieren")
        quick = self._card_box(page, pady=(14, 0), title="Direkt installieren")
        grid = ttk.Frame(quick, style="Panel.TFrame")
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)
        rows = (("CurseForge-Link", self.cf_link_var, self.install_cf_link,
                 "z. B. https://www.curseforge.com/wow/addons/leatrix-plus oder nur leatrix-plus"),
                ("GitHub", self.gh_var, self.install_github_dialog, "owner/repo oder Link zum Repository"))
        for r, (label, var, cmd, hint) in enumerate(rows):
            ttk.Label(grid, text=label, style="Panel.TLabel", width=16).grid(row=r, column=0, sticky="w", pady=4)
            e = ttk.Entry(grid, textvariable=var)
            e.grid(row=r, column=1, sticky="ew", pady=4)
            e.bind("<Return>", lambda ev, c=cmd: c())
            Placeholder(e, var, hint)
            ttk.Button(grid, text="Installieren", width=14, command=cmd).grid(row=r, column=2, sticky="e",
                                                                               padx=(8, 0), pady=4)
        ttk.Label(grid, text="ZIP-Datei", style="Panel.TLabel", width=16).grid(row=2, column=0, sticky="w", pady=4)
        self.zip_cb = ttk.Combobox(grid, textvariable=self.zip_var, state="readonly")
        self.zip_cb.grid(row=2, column=1, sticky="ew", pady=4)
        zrow = ttk.Frame(grid, style="Panel.TFrame")
        zrow.grid(row=2, column=2, sticky="e", padx=(8, 0), pady=4)
        ttk.Button(zrow, text="Installieren", width=14, command=self.install_zip_choice).pack(side="left")
        ttk.Button(zrow, text="Andere …", command=self.install_zip_dialog).pack(side="left", padx=(8, 0))
        ttk.Label(quick, style="Panel.TLabel", foreground=t["text_faint2"],
                  text="Tipp: ZIP-, .lzpack- und .lzlist-Dateien kannst du auch einfach ins Fenster ziehen."
                  if HAS_DND else "Die Liste zeigt die neuesten ZIP-Dateien aus deinem Download-Ordner.").pack(
            anchor="w", pady=(6, 0))

        box = self._card_box(page, pady=(12, 0), fill="both", expand=True, title="CurseForge durchsuchen · nur WoW")
        top = ttk.Frame(box, style="Panel.TFrame")
        top.pack(fill="x")
        e = ttk.Entry(top, textvariable=self.cf_q)
        e.pack(side="left", fill="x", expand=True, ipady=2)
        Placeholder(e, self.cf_q, "Addon-Name, z. B. Details, Questie, Bagnon …")
        e.bind("<Return>", lambda ev: self.cf_search())
        ttk.Button(top, text="Suchen", style="Primary.TButton", command=self.cf_search).pack(side="left", padx=(8, 0))
        cb = ttk.Combobox(top, textvariable=self.cf_sort, values=list(CF_SORTS), state="readonly", width=19)
        cb.pack(side="right")
        cb.bind("<<ComboboxSelected>>", lambda ev: self.cf_search())
        ttk.Label(top, text="Sortierung", style="Panel.TLabel").pack(side="right", padx=(16, 8))
        self.cf_fit_chk = ttk.Checkbutton(box, variable=self.cf_fit, command=self.cf_show_rows,
                                          text="Nur Addons für meine Spielversion")
        self.cf_fit_chk.pack(anchor="w", pady=(8, 8))
        self.cf_q.trace_add("write", lambda *a: self._cf_typed())

        body = ttk.Frame(box, style="Panel.TFrame")
        body.pack(fill="both", expand=True)
        det = ttk.Frame(body, style="Detail.TFrame", width=300, padding=(16, 14))
        det.pack(side="right", fill="y", padx=(12, 0))
        det.pack_propagate(False)
        dbtn = ttk.Frame(det, style="Detail.TFrame")
        dbtn.pack(side="bottom", fill="x")
        self.cf_det_title = ttk.Label(det, style="DetailTitle.TLabel", wraplength=265, font=self.fonts["card"])
        self.cf_det_title.pack(anchor="w")
        self.cf_det_meta = ttk.Label(det, style="DetailNote.TLabel", wraplength=265, foreground=THEME["gold"])
        self.cf_det_meta.pack(anchor="w", pady=(4, 0))
        self.cf_det_text = ttk.Label(det, style="DetailNote.TLabel", wraplength=265, justify="left")
        self.cf_det_text.pack(anchor="w", pady=(8, 0))
        ttk.Label(dbtn, text="Datei", style="DetailKey.TLabel").pack(anchor="w")
        self.cf_file_cb = ttk.Combobox(dbtn, textvariable=self.cf_file_var, state="readonly")
        self.cf_file_cb.pack(fill="x", pady=(2, 10))
        self.cf_install_btn = ttk.Button(dbtn, text="Installieren", style="Primary.TButton", command=self.cf_install)
        self.cf_install_btn.pack(fill="x", pady=(0, 6))
        self.cf_page_btn = ttk.Button(dbtn, text="Auf CurseForge ansehen ↗", command=self.cf_open_page)
        self.cf_page_btn.pack(fill="x")

        lst = ttk.Frame(body, style="Panel.TFrame")
        lst.pack(side="left", fill="both", expand=True)
        foot = ttk.Frame(lst, style="Panel.TFrame")
        foot.pack(side="bottom", fill="x", pady=(8, 0))
        ttk.Label(foot, textvariable=self.cf_count, style="Panel.TLabel").pack(side="left")
        self.cf_more_btn = ttk.Button(foot, text="Mehr laden", command=lambda: self.cf_search(more=True))
        self.cf_more_btn.pack(side="right")
        frame, self.cf_tree = self._list(lst, (("name", "Addon"), ("author", "Autor"), ("downloads", "Downloads"),
                                               ("updated", "Aktualisiert"), ("versions", "Spielversionen"),
                                               ("state", "")),
                                         (210, 120, 100, 100, 160, 100), stretch=("name", "versions"),
                                         select="extended")
        frame.pack(fill="both", expand=True)
        self.cf_tree.column("downloads", anchor="e")
        self.cf_tree.bind("<<TreeviewSelect>>", lambda ev: self.cf_show_details())
        self.cf_tree.bind("<Double-1>", lambda ev: self.cf_open_page())
        self.cf_results: list[dict] = []
        self.cf_total = 0
        self.cf_req = 0
        self.cf_searching = False
        self.cf_direct = False
        self._cf_debounce = None
        self.cf_files_cache: dict[int, dict] = {}
        self.cf_show_details()

    # --- Updates ---------------------------------------------------------------- #
    def _page_updates(self):
        page, head = self._page("updates", "Updates", "Addons aktuell halten")
        right = ttk.Frame(head)
        right.pack(side="right", anchor="s")
        ttk.Button(right, text=f"{UPD_MARK}  Alle aktualisieren", style="Primary.TButton",
                   command=self.update_all).pack(side="right")
        ttk.Button(right, text="Jetzt prüfen", command=self.check_updates).pack(side="right", padx=(0, 8))
        ttk.Label(right, textvariable=self.upd_count_var).pack(side="right", padx=(0, 16))

        opts = ttk.Frame(page)
        opts.pack(fill="x", pady=(8, 0))
        self._w_check(opts, "Beim Start auf Updates prüfen", self.check_start_var, panel=False).pack(side="left")
        self._w_check(opts, "Vor Updates automatisch WTF sichern", self.auto_backup_var, panel=False).pack(
            side="left", padx=(20, 0))

        self.browser_box_outer = ttk.Frame(page)
        self.browser_box_outer.pack(fill="x")
        self.browser_box = None

        body = ttk.Frame(page)
        body.pack(fill="both", expand=True, pady=(12, 0))
        side = ttk.Frame(body, width=360)
        side.pack(side="right", fill="y", padx=(14, 0))
        side.pack_propagate(False)
        nbox = self._card_box(side, pady=(0, 0), fill="both", expand=True, title="Was ist neu?")
        nf, self.upd_notes = self._text(nbox)
        nf.pack(fill="both", expand=True)
        self.upd_one_btn = ttk.Button(nbox, text=f"{UPD_MARK}  Dieses aktualisieren", style="Primary.TButton",
                                      command=self.update_selected_in_list)
        self.upd_one_btn.pack(fill="x", pady=(10, 0))
        nb = ttk.Frame(nbox, style="Panel.TFrame")
        nb.pack(fill="x", pady=(6, 0))
        nb.columnconfigure(0, weight=1, uniform="b")
        nb.columnconfigure(1, weight=1, uniform="b")
        self.upd_dl_btn = ttk.Button(nb, text="Im Browser laden ↗", command=self._upd_open_download)
        self.upd_dl_btn.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        self.upd_page_btn = ttk.Button(nb, text="Projektseite ↗", command=self._upd_open_page)
        self.upd_page_btn.grid(row=0, column=1, sticky="ew", padx=(3, 0))

        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True)
        frame, self.upd_tree = self._list(left, (("name", "Addon"), ("old", "Installiert"), ("new", "Neu"),
                                                 ("hint", "Hinweis")),
                                          (170, 140, 160, 120), stretch=("name", "new"))
        frame.pack(fill="both", expand=True)
        self.upd_tree.bind("<<TreeviewSelect>>", lambda e: self._upd_show())

        low = ttk.Frame(page)
        low.pack(fill="x", pady=(12, 0))
        b1 = self._card_box(low, pady=(0, 0), side="left", fill="both", expand=True, title="Manuell installierte Addons")
        ttk.Label(b1, style="Panel.TLabel", wraplength=420, justify="left",
                  text="Verknüpfe sie mit CurseForge, dann prüft Leeroyzan auch diese auf Updates.").pack(anchor="w")
        ttk.Button(b1, text="Mit CurseForge verknüpfen", command=self.link_curseforge_dialog).pack(anchor="w", pady=(8, 0))
        b2 = self._card_box(low, pady=(0, 0), side="left", fill="both", expand=True, title="Leeroyzan selbst",
                            padx=(12, 0))
        self.manager_state = tk.StringVar(value=f"Installiert: v{APP_VERSION}")
        ttk.Label(b2, textvariable=self.manager_state, style="Panel.TLabel", wraplength=420,
                  justify="left").pack(anchor="w")
        mrow = ttk.Frame(b2, style="Panel.TFrame")
        mrow.pack(fill="x", pady=(8, 0))
        ttk.Button(mrow, text="Nach Update suchen", command=self.check_manager_update).pack(side="right")
        self._w_update_mode(mrow).pack(side="left")

    # --- Teilen ------------------------------------------------------------------ #
    def _page_share(self):
        t = THEME
        page, _ = self._page("share", "Teilen", "Addons mit Freunden teilen",
                             "Eine Download-Liste enthält nur Verweise (CurseForge/GitHub) und passt als Text in "
                             "Discord oder eine Kurznachricht. Ein Paket (.lzpack) enthält zusätzlich die Dateien "
                             "von ZIP- und GitHub-Addons.")
        cols = ttk.Frame(page)
        cols.pack(fill="both", expand=True, pady=(12, 0))
        cols.columnconfigure(0, weight=1, uniform="c")
        cols.columnconfigure(1, weight=1, uniform="c")
        cols.rowconfigure(0, weight=1)
        lf = ttk.Frame(cols)
        lf.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        rf = ttk.Frame(cols)
        rf.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        box = self._card_box(lf, pady=(0, 0), fill="both", expand=True, title="Senden")
        scope = ttk.Frame(box, style="Panel.TFrame")
        scope.pack(fill="x")
        self.share_sel_rb = ttk.Radiobutton(scope, text="Ausgewählte Addons", value="sel",
                                            variable=self.share_scope, command=self.rebuild_share)
        self.share_sel_rb.pack(side="left")
        self.share_all_rb = ttk.Radiobutton(scope, text="Alle Addons", value="all",
                                            variable=self.share_scope, command=self.rebuild_share)
        self.share_all_rb.pack(side="left", padx=(16, 0))
        opt = ttk.Frame(box, style="Panel.TFrame")
        opt.pack(fill="x", pady=(8, 0))
        ttk.Checkbutton(opt, text="Genau meine Versionen", variable=self.share_exact,
                        command=self.rebuild_share).pack(side="left")
        ttk.Checkbutton(opt, text="Addon-Sets mitsenden", variable=self.share_sets,
                        command=self.rebuild_share).pack(side="left", padx=(16, 0))
        szr = ttk.Frame(box, style="Panel.TFrame")
        szr.pack(fill="x", pady=(8, 8))
        ttk.Label(szr, text="Nachrichtenlänge", style="Panel.TLabel").pack(side="left", padx=(0, 8))
        cb = ttk.Combobox(szr, textvariable=self.share_size, values=list(LIST_PART_SIZES), state="readonly", width=28)
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda e: self.rebuild_share())
        sf, self.share_txt = self._text(box)
        sf.pack(fill="both", expand=True)
        ttk.Label(box, textvariable=self.share_info, style="Panel.TLabel", wraplength=470,
                  justify="left").pack(anchor="w", pady=(6, 0))
        brow = ttk.Frame(box, style="Panel.TFrame")
        brow.pack(fill="x", pady=(8, 0))
        ttk.Button(brow, text="Code kopieren", style="Primary.TButton", command=self.copy_share_code).pack(side="left")
        ttk.Button(brow, text="Lesbare Liste kopieren", command=self.copy_share_readable).pack(side="left", padx=(6, 0))
        prow = ttk.Frame(box, style="Panel.TFrame")
        prow.pack(fill="x", pady=(6, 0))
        ttk.Button(prow, text="Als .lzlist speichern", command=self.save_share_file).pack(side="left")
        ttk.Button(prow, text="Als Paket (.lzpack) speichern", command=self.export_share_pack).pack(side="left",
                                                                                                  padx=(6, 0))

        rbox = self._card_box(rf, pady=(0, 0), fill="both", expand=True, title="Empfangen")
        ttk.Label(rbox, style="Panel.TLabel", wraplength=470, justify="left",
                  text="Code aus Discord/Chat hier einfügen (alle Teile, Reihenfolge egal):").pack(anchor="w")
        rf2, self.import_txt = self._text(rbox, readonly=False)
        rf2.pack(fill="both", expand=True, pady=(6, 0))
        self.import_txt.bind("<<Modified>>", self._import_modified)
        irow = ttk.Frame(rbox, style="Panel.TFrame")
        irow.pack(fill="x", pady=(8, 0))
        ttk.Button(irow, text="Aus Zwischenablage einfügen", command=self.paste_import).pack(side="left")
        ttk.Button(irow, text="Leeren", command=lambda: self.import_txt.delete("1.0", "end")).pack(side="left", padx=(6, 0))
        ttk.Label(rbox, textvariable=self.import_preview, style="Panel.TLabel", wraplength=470,
                  justify="left").pack(anchor="w", pady=(8, 0))
        self.import_btn = ttk.Button(rbox, text="Installieren", style="Primary.TButton", command=self.import_list_now)
        self.import_btn.pack(anchor="w", pady=(6, 0))
        self.import_btn.state(["disabled"])
        tk.Frame(rbox, height=1, bg=t["line"]).pack(fill="x", pady=(12, 10))
        ttk.Label(rbox, text="Oder eine Datei aus deinem Download-Ordner (.lzlist / .lzpack):",
                  style="Panel.TLabel").pack(anchor="w")
        frow = ttk.Frame(rbox, style="Panel.TFrame")
        frow.pack(fill="x", pady=(6, 0))
        self.recv_cb = ttk.Combobox(frow, textvariable=self.recv_file_var, state="readonly")
        self.recv_cb.pack(side="left", fill="x", expand=True)
        ttk.Button(frow, text="Öffnen", command=self.open_recv_file).pack(side="left", padx=(8, 0))
        ttk.Button(frow, text="Andere …", command=self.open_other_share_file).pack(side="left", padx=(8, 0))
        self._import_data = None

    # --- Sets & Backups ------------------------------------------------------------ #
    def _page_sets(self):
        page, _ = self._page("sets", "Sets & Backups", "Addon-Gruppen und Sicherungen")
        cols = ttk.Frame(page)
        cols.pack(fill="both", expand=True, pady=(12, 0))
        cols.columnconfigure(0, weight=1, uniform="c")
        cols.columnconfigure(1, weight=1, uniform="c")
        cols.rowconfigure(0, weight=1)
        lf = ttk.Frame(cols)
        lf.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        rf = ttk.Frame(cols)
        rf.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        box = self._card_box(lf, pady=(0, 0), fill="both", expand=True, title="Addon-Sets")
        ttk.Label(box, style="Panel.TLabel", wraplength=460, justify="left",
                  text="Ein Set merkt sich, welche Addons aktiv sind. „Anwenden“ schaltet genau diese ein "
                       "und alle anderen aus – z. B. für Leveln, Raid oder einen Login ohne Addons.").pack(anchor="w")
        frame, self.sets_tree = self._list(box, (("name", "Set"), ("count", "Addons")), (260, 80),
                                           stretch=("name",), height=4)
        frame.pack(fill="both", expand=True, pady=(10, 0))
        self.sets_tree.bind("<<TreeviewSelect>>", lambda e: self._set_selected())
        self.sets_tree.bind("<Double-1>", lambda e: self.apply_set())
        self.set_members = ttk.Label(box, style="Panel.TLabel", wraplength=460, justify="left")
        self.set_members.pack(anchor="w", pady=(8, 0))
        row = ttk.Frame(box, style="Panel.TFrame")
        row.pack(fill="x", pady=(8, 0))
        ttk.Button(row, text="Anwenden", style="Primary.TButton", command=self.apply_set).pack(side="left")
        ttk.Button(row, text="Löschen", style="Danger.TButton", command=self.delete_set).pack(side="left", padx=(6, 0))
        srow = ttk.Frame(box, style="Panel.TFrame")
        srow.pack(fill="x", pady=(12, 0))
        e = ttk.Entry(srow, textvariable=self.set_name_var)
        e.pack(side="left", fill="x", expand=True)
        Placeholder(e, self.set_name_var, "Name für ein neues Set")
        e.bind("<Return>", lambda ev: self.save_set())
        ttk.Button(srow, text="Aktive als Set speichern", command=self.save_set).pack(side="left", padx=(8, 0))

        bbox = self._card_box(rf, pady=(0, 0), fill="both", expand=True, title="Backups · WTF-Ordner")
        ttk.Label(bbox, style="Panel.TLabel", wraplength=460, justify="left",
                  text="Gesichert werden alle Addon-Einstellungen (SavedVariables) dieser Spielversion.").pack(anchor="w")
        frame, self.backup_tree = self._list(bbox, (("when", "Zeitpunkt"), ("kind", "Art")), (170, 200),
                                             stretch=("kind",), height=4)
        frame.pack(fill="both", expand=True, pady=(10, 0))
        brow = ttk.Frame(bbox, style="Panel.TFrame")
        brow.pack(fill="x", pady=(8, 0))
        ttk.Button(brow, text="Jetzt sichern", style="Primary.TButton", command=self.backup).pack(side="left")
        ttk.Button(brow, text="Wiederherstellen", command=self.restore_dialog).pack(side="left", padx=(6, 0))
        ttk.Button(brow, text="Ordner öffnen",
                   command=lambda: (BACKUP_DIR.mkdir(parents=True, exist_ok=True),
                                    open_in_explorer(BACKUP_DIR))).pack(side="right")
        self._w_check(bbox, "Vor Updates automatisch sichern", self.auto_backup_var).pack(anchor="w", pady=(10, 0))

    # --- Einstellungen ---------------------------------------------------------------- #
    def _page_settings(self):
        page, _ = self._page("settings", "Einstellungen", "Leeroyzan einrichten")
        cols = ttk.Frame(page)
        cols.pack(fill="both", expand=True, pady=(12, 0))
        cols.columnconfigure(0, weight=3, uniform="c")
        cols.columnconfigure(1, weight=2, uniform="c")
        lf = ttk.Frame(cols)
        lf.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        rf = ttk.Frame(cols)
        rf.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        box = self._card_box(lf, pady=(0, 0), title="World of Warcraft")
        self._w_path(box).pack(fill="x")
        ttk.Label(box, style="Panel.TLabel", foreground=THEME["text_faint2"],
                  text="Die Spielversion wählst du links oben.").pack(anchor="w", pady=(6, 0))
        cbox = self._card_box(lf, pady=(12, 0), title="CurseForge")
        self.cf_key_widget = self._w_cf_key(cbox, panel=True)
        self.cf_key_widget.pack(fill="x")
        gbox = self._card_box(rf, pady=(0, 0), title="Allgemein")
        self._w_check(gbox, "Beim Start auf Addon-Updates prüfen", self.check_start_var).pack(anchor="w")
        self._w_check(gbox, "Vor Updates automatisch WTF sichern", self.auto_backup_var).pack(anchor="w", pady=(6, 0))
        self._w_update_mode(gbox).pack(anchor="w", pady=(12, 0))
        ttk.Button(gbox, text="Nach Leeroyzan-Update suchen", command=self.check_manager_update).pack(
            anchor="w", pady=(10, 0))
        hbox = self._card_box(rf, pady=(12, 0), title="Hilfe")
        for text, cmd in (("Log öffnen", self.open_log), ("Addon-Ordner öffnen", lambda: self.open_folder([])),
                          ("Neu einlesen", self.refresh)):
            ttk.Button(hbox, text=text, command=cmd).pack(anchor="w", pady=(0, 6), fill="x")
        links = ttk.Frame(hbox, style="Panel.TFrame")
        links.pack(fill="x", pady=(6, 0))
        for i, (text, url) in enumerate(HEADER_LINKS):
            links.columnconfigure(i, weight=1, uniform="l")
            ttk.Button(links, text=f"{text} ↗", command=lambda u=url: self.open_link(u)).grid(
                row=0, column=i, sticky="ew", padx=(0 if i == 0 else 6, 0))

    def _w_cf_key(self, parent, panel: bool = False):
        """Eingabe des eigenen CurseForge-API-Keys (nur lokal gespeichert) mit Verbindungstest."""
        fs, ls = ("Panel.TFrame", "Panel.TLabel") if panel else ("TFrame", "TLabel")
        f = ttk.Frame(parent, style=fs)
        ttk.Label(f, text="Dein API-Key (bleibt nur auf diesem Rechner):", style=ls).pack(anchor="w")
        row = ttk.Frame(f, style=fs)
        row.pack(fill="x", pady=(4, 0))
        var = tk.StringVar(value=self.cfg.get("curseforge_key", ""))
        status = tk.StringVar(value="")

        def store(*_):
            self.cfg["curseforge_key"] = var.get().strip()
            save_config(self.cfg)
            if self.cfg["curseforge_key"]:
                self.dismiss("cfkey")
        var.trace_add("write", store)
        self.cf_key_entry = ttk.Entry(row, textvariable=var, show="•", width=18)
        self.cf_key_entry.pack(side="left", fill="x", expand=True)

        def test():
            key = var.get().strip()
            if not key:
                status.set("Bitte zuerst den Key einfügen.")
                return
            status.set("Teste …")

            def work():
                try:
                    cf_request(f"/games/{CF_GAME_ID}", key)
                    return "✓ Verbindung steht – der Key funktioniert."
                except Exception as e:  # noqa
                    return str(e)
            self.run_quiet(work, lambda res, err: status.set(res or str(err)))
        ttk.Button(row, text="Testen", command=test).pack(side="left", padx=(8, 0))
        ttk.Button(row, text="Key holen ↗", command=lambda: self.open_link(CF_KEY_URL)).pack(side="left", padx=(8, 0))
        ttk.Label(f, textvariable=status, style=ls, wraplength=520).pack(anchor="w", pady=(4, 0))
        ttk.Label(f, text="Download-Ordner deines Browsers:", style=ls).pack(anchor="w", pady=(10, 0))
        drow = ttk.Frame(f, style=fs)
        drow.pack(fill="x", pady=(4, 0))
        dvar = tk.StringVar(value=str(self.download_dir()))

        def dstore(*_):
            self.cfg["download_dir"] = dvar.get().strip()
            save_config(self.cfg)
        dvar.trace_add("write", dstore)
        ttk.Entry(drow, textvariable=dvar).pack(side="left", fill="x", expand=True)

        def browse():
            d = filedialog.askdirectory(title="Download-Ordner wählen", initialdir=dvar.get() or None)
            if d:
                dvar.set(d)
        ttk.Button(drow, text="Durchsuchen…", command=browse).pack(side="left", padx=(8, 0))
        ttk.Label(f, style=ls, wraplength=520, justify="left",
                  text="Dort sucht Leeroyzan nach ZIP-Dateien, Listen und Paketen – und nach Downloads, die du "
                       "für gesperrte Addons im Browser lädst.").pack(anchor="w", pady=(4, 0))
        return f

    def settings_dialog(self):
        """Früher ein eigenes Fenster – jetzt die Seite „Einstellungen“."""
        self.show_page("settings")
        try:
            self.cf_key_entry.focus_set()
        except (tk.TclError, AttributeError):
            pass

    # ----- Kacheln ------------------------------------------------------------ #
    def _relayout_cards(self):
        if self.cards_inner is not None and getattr(self, "_card_widgets", None):
            self._place_cards()

    def render_cards(self, entries):
        """Zeichnet die Addon-Kacheln."""
        t = THEME
        for w in self.cards_inner.winfo_children():
            w.destroy()
        self._card_widgets = []
        if not entries:
            ttk.Label(self.cards_inner, text="Keine Addons gefunden.", style="Body.TLabel").grid(
                row=0, column=0, sticky="w", pady=20)
            return
        sel = {a.name for a in self.selected_addons()}
        for title, members, vals, tags in entries:
            chosen = bool(sel) and {m.name for m in members} <= sel
            outer = tk.Frame(self.cards_inner, bg=t["gold"] if chosen else t["border_soft"])
            card = ttk.Frame(outer, style="Card.TFrame", padding=(16, 12))
            card.pack(fill="both", expand=True, padx=1, pady=1)
            top = ttk.Frame(card, style="Card.TFrame")
            top.pack(fill="x")
            on = all(m.enabled for m in members)
            ttk.Button(top, text="AN" if on else "AUS", style="ToggleOn.TButton" if on else "ToggleOff.TButton",
                       cursor="hand2", command=lambda ms=members: self.toggle_selected(ms)).pack(side="right")
            name = ttk.Label(top, text=title, style="CardTitle.TLabel", cursor="hand2")
            name.pack(side="left")
            folders = f"{len(members)} Ordner" if len(members) > 1 else "1 Ordner"
            if len(members) > 1:
                rest = [m.name for m in members if m.name != title][:3]
                folders += " · " + ", ".join(rest) + (" …" if len(members) - 1 > 3 else "")
            ttk.Label(card, text=folders, style="CardSub.TLabel").pack(anchor="w", pady=(2, 8))
            chips = tk.Frame(card, bg=t["row_a"])
            chips.pack(fill="x")
            status_color = (t["text_faint"] if "disabled" in tags else t["warn"] if "warn" in tags
                            else t["old"] if "old" in tags else t["ok"])
            upd_chip = ""
            if "update" in tags:
                v = re.search(r"\d+(?:\.\d+)+", str(vals[6]).replace("  (Browser)", ""))
                upd_chip = f"{UPD_MARK} Update {v.group(0)}" if v else f"{UPD_MARK} Update verfügbar"
            for text, fg, bg in ((f"v{vals[3]}" if vals[3] else "ohne Version", t["text"], t["pill"]),
                                 (vals[5].split(":")[0] or "manuell", t["text_dim"], t["pill"]),
                                 (upd_chip or vals[0].replace(f"{UPD_MARK} ", ""),
                                  t["update"] if upd_chip else status_color,
                                  t["update_bg"] if upd_chip else t["pill"])):
                tk.Label(chips, text=text, fg=fg, bg=bg, font=self.fonts["small"], padx=9, pady=2).pack(
                    side="left", padx=(0, 6))
            tk.Frame(card, height=1, bg=t["line"]).pack(fill="x", pady=(10, 4))
            links = ttk.Frame(card, style="Card.TFrame")
            links.pack(fill="x")
            acts = []
            if upd_chip:
                acts.append((f"{UPD_MARK} Aktualisieren", lambda ms=members: self.update_selected(ms)))
            acts += [("Details", lambda ms=members: self.select_addons(ms)),
                     ("Ordner", lambda ms=members: self.open_folder(ms)),
                     ("Entfernen", lambda ms=members: self.remove_selected(list(ms)))]
            for text, cb in acts:
                ttk.Button(links, text=text, style="CardLink.TButton", cursor="hand2", width=len(text),
                           command=cb).pack(side="left", padx=(0, 10))
            for w in (card, name):
                w.bind("<Button-1>", lambda e, ms=members: self.select_addons(ms))
            self._card_widgets.append(outer)
        self._place_cards()

    def select_addons(self, members):
        """Markiert Addons (auch aus der Kachel-Ansicht) und zeigt sie im Detailbereich."""
        if self.tree is None:
            return
        names = [m.name for m in members]
        iids = [n for n in names if self.tree.exists(n)]
        gid = next((g for g, ms in self.group_members.items() if set(ms) == set(names)), None)
        if gid and self.tree.exists(gid):
            iids = [gid]
        if iids:
            self.tree.selection_set(iids)
            self.tree.see(iids[0])
        self.update_detail()
        if self.view == "cards":
            self.render()

    def _group_main(self, members):
        names = {m.name for m in members}
        return next((a for a in members if any(o.startswith(a.name) for o in names if o != a.name)), members[0])

    def _place_cards(self):
        width = max(self.cards_canvas.winfo_width(), 600)
        cols = max(1, min(4, width // 320))
        for c in range(4):
            self.cards_inner.columnconfigure(c, weight=1 if c < cols else 0, uniform="card" if c < cols else "")
        for i, w in enumerate(self._card_widgets):
            w.grid(row=i // cols, column=i % cols, sticky="nsew",
                   padx=(0 if i % cols == 0 else 12, 0), pady=(0, 12))

    def update_stats(self):
        """(Früher Kacheln des Launchers – die Zahlen stehen jetzt in der Seitenleiste.)"""

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

    def source_label(self, sid: str, src: dict) -> str:
        if src.get("type") == "curseforge":
            return f"CurseForge: {src.get('name') or sid}"
        return f"GitHub: {sid}"

    def source_name(self, sid: str) -> str:
        src = self.sources().get(sid, {})
        return src.get("name") or sid

    def source_page(self, sid: str, src: dict) -> str:
        if src.get("type") == "curseforge":
            return src.get("page") or f"https://www.curseforge.com/wow/addons/{src.get('slug', '')}"
        return f"https://github.com/{sid}/releases"

    def cf_key(self) -> str:
        return (self.cfg.get("curseforge_key") or "").strip()

    def download_dir(self) -> Path:
        d = (self.cfg.get("download_dir") or "").strip()
        return Path(d) if d else default_download_dir()

    def folder_source(self, folder: str) -> tuple[str, dict] | tuple[None, None]:
        for sid, s in self.sources().items():
            if folder in s.get("folders", []):
                return sid, s
        return None, None

    def start_wow(self):
        if not self.root_path or not self.flavor:
            self.notify("Bitte zuerst den WoW-Ordner wählen.", "warn", [("WoW-Ordner wählen", self.browse_root), ("Einstellungen", lambda: self.show_page("settings"))], key="noroot")
            return
        exe = find_wow_exe(self.root_path, self.flavor)
        if not exe:
            self.error(f"In {Path(self.root_path) / self.flavor} wurde keine "
                                           "WoW-.exe gefunden.\n\nIst diese Version vollständig installiert?")
            return
        running = running_wow(self.root_path, self.flavor)
        if running:
            self.info(f"WoW läuft bereits ({', '.join(running)}).")
            return
        if self.busy:
            self.info("Bitte warten, bis der laufende Vorgang fertig ist.")
            return
        try:
            flags = 0x00000008 | 0x00000200 if sys.platform.startswith("win") else 0  # losgelöst
            subprocess.Popen([str(exe)], cwd=str(exe.parent), close_fds=True, creationflags=flags)
        except OSError as e:
            self.error(f"WoW konnte nicht gestartet werden:\n{e}")
            return
        log.info("WoW gestartet: %s", exe)
        self.set_status(f"{exe.name} gestartet. Falls WoW nach dem Login fragt: Battle.net-App öffnen.")
        btn = self.play_btn
        btn.state(["disabled"])

        def enable():
            try:
                btn.state(["!disabled"])
            except tk.TclError:
                pass          # Ansicht inzwischen gewechselt
        self.after(15000, enable)   # Doppelstarts vermeiden

    def open_link(self, url: str):
        try:
            webbrowser.open(url, new=2)
            self.set_status(f"Geöffnet: {url}")
        except Exception as e:  # noqa
            self.error(f"Link konnte nicht geöffnet werden:\n{url}\n\n{e}")

    def set_status(self, text: str):
        self.status_var.set(text)

    def save_options(self):
        self.cfg["auto_backup"] = bool(self.auto_backup_var.get())
        self.cfg["check_on_start"] = bool(self.check_start_var.get())
        label = self.self_update_var.get()
        mode = next((k for k, v in self.UPDATE_MODES.items() if v == label), "auto")
        changed = mode != self.cfg.get("self_update_mode")
        self.cfg["self_update_mode"] = mode
        self.cfg["self_update_check"] = mode != "off"
        save_config(self.cfg)
        if changed and mode == "auto":
            self.after(300, lambda: self.check_manager_update(silent=True))

    def ready(self, modifies: bool = True, quiet: bool = False) -> bool:
        """Prüft, ob eine Aktion starten darf. modifies=True warnt zusätzlich, wenn WoW läuft."""
        if self.asking:
            if not quiet:
                self.set_status("Bitte zuerst die Frage oben beantworten.")
            return False
        if self.busy:
            if not quiet:
                self.info("Bitte warten, ein Vorgang läuft noch.")
            return False
        if not self.root_path or not self.flavor:
            if not quiet:
                self.notify("Bitte zuerst den WoW-Ordner wählen.", "warn", [("WoW-Ordner wählen", self.browse_root), ("Einstellungen", lambda: self.show_page("settings"))], key="noroot")
            return False
        if modifies:
            running = running_wow(self.root_path, self.flavor)
            if running and not self.ask(
                    f"WoW läuft gerade ({', '.join(running)}). Änderungen an Addons wirken erst nach /reload, "
                    "und beim Ausloggen kann WoW Einstellungen überschreiben. Trotzdem fortfahren?",
                    yes="Fortfahren", no="Abbrechen"):
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

    def run_quiet(self, func, on_done):
        """Wie run_bg, aber unsichtbar: kein Status, kein Sanduhr-Cursor, blockiert nichts."""
        def worker():
            try:
                self.quiet_jobs.put((on_done, func(), None))
            except Exception as e:  # noqa
                self.quiet_jobs.put((on_done, None, e))

        threading.Thread(target=worker, daemon=True).start()

    def _poll_jobs(self):
        try:
            while True:
                cb, res, err = self.quiet_jobs.get_nowait()
                cb(res, err)
        except queue.Empty:
            pass
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
                        self.error(str(err))
                elif cb:
                    cb(res)
        except queue.Empty:
            pass
        self.after(100, self._poll_jobs)

    def selected_addons(self) -> list[Addon]:
        """Ausgewählte Addons; eine ausgewählte Gruppe zählt mit allen ihren Ordnern."""
        if self.tree is None or not self.tree.winfo_exists():
            return []
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

    def _column_at(self, col_id: str) -> str:
        """Spaltenname zur Treeview-Spalten-ID (#1, #2 …) der aktuellen Ansicht."""
        if not col_id or col_id == "#0":
            return ""
        shown = tuple(self.tree["displaycolumns"])
        if not shown or shown[0] == "#all":
            shown = self.COLUMNS
        i = int(col_id[1:]) - 1
        return shown[i] if 0 <= i < len(shown) else ""

    def _pending_cf(self, addons) -> str:
        """Quellen-ID eines offenen CurseForge-Updates dieser Addons ("" wenn keins)."""
        for a in addons:
            sid, src = self.folder_source(a.name)
            if not src or src.get("type") != "curseforge":
                continue
            new, info = self.updates.get(sid), self.update_info.get(sid) or {}
            if new and new != src.get("tag") and info.get("best"):
                return sid
        return ""

    def cf_update_link(self, addons) -> tuple[str, str]:
        """(Name, Download-Adresse) eines verfügbaren CurseForge-Updates dieser Addons – sonst ("", "")."""
        sid = self._pending_cf(addons)
        if not sid:
            return "", ""
        info = self.update_info[sid]
        return self.sources()[sid].get("name") or sid, cf_download_url(info, info["best"])

    def open_cf_update(self, addons=None, sid: str = ""):
        """CurseForge-Download im Browser öffnen – die geladene ZIP wird danach automatisch installiert."""
        if not sid:
            sid = self._pending_cf(list(addons) if addons is not None else self.selected_addons())
        info = self.update_info.get(sid) or {}
        if not sid or not info.get("best"):
            return
        self.open_link(cf_download_url(info, info["best"]))
        self.watch_download(sid, manual=True)
        self.refresh_browser_list()
        self.set_status(f"CurseForge-Download für {self.source_name(sid)} geöffnet – sobald die Datei in "
                        f"{self.download_dir()} liegt, installiert Leeroyzan sie automatisch.")

    # ----- Browser-Downloads automatisch einspielen ----------------------- #
    DL_POLL_MS = 2000

    def watch_download(self, sid: str, since: float | None = None, info: dict | None = None,
                       asset: dict | None = None, manual: bool = False):
        """Erwartet die ZIP eines CurseForge-Addons im Download-Ordner und installiert sie, sobald sie da ist."""
        info = info or self.update_info.get(sid) or {}
        asset = asset or info.get("best")
        if not asset or not asset.get("file_name"):
            return
        since = time.time() - 15 if since is None else since
        old = self.dl_watch.get(sid)
        if old and old["asset"].get("file_id") == asset.get("file_id"):
            old["since"] = min(old["since"], since)
            old["manual"] = old.get("manual") or manual
        else:
            self.dl_watch[sid] = {"info": info, "asset": asset, "since": since, "flavor": self.flavor,
                                  "manual": manual, "new": sid not in self.sources()}
            self._dl_checked.clear()          # schon gesehene Dateien für das neue Ziel erneut prüfen
        if not self._dl_job:
            self._dl_job = self.after(self.DL_POLL_MS, self._poll_downloads)

    def _poll_downloads(self):
        self._dl_job = None
        changed = False
        for sid, w in list(self.dl_watch.items()):          # nur noch Offenes beobachten
            src = self.sources().get(sid)
            if (w["flavor"] != self.flavor or (src and src.get("file_id") == w["asset"].get("file_id"))
                    or (not src and not w.get("new"))):
                del self.dl_watch[sid]
                changed = changed or w.get("manual")
        if changed:
            self.refresh_browser_list()
        if not self.dl_watch:
            return
        if not (self.busy or self.modal_download):
            hit = self._find_download()
            if hit:
                self._install_download(*hit)
        self._dl_job = self.after(self.DL_POLL_MS, self._poll_downloads)

    def _find_download(self):
        try:
            files = list(self.download_dir().glob("*.zip"))
        except OSError:
            return None
        for f in files:
            try:
                st = f.stat()
            except OSError:
                continue
            key, sig = str(f), (st.st_size, st.st_mtime)
            prev, self._dl_seen[key] = self._dl_seen.get(key), sig
            if prev != sig or st.st_size == 0 or self._dl_checked.get(key) == sig:
                continue                              # lädt noch, leer oder schon als unpassend erkannt
            for sid, w in self.dl_watch.items():
                if not zip_name_matches(f.name, w["asset"]["file_name"]):
                    continue
                if download_matches(f, w["asset"], w["since"]):
                    return sid, f
                self.set_status(f"{f.name} passt nicht zur erwarteten Version {w['asset']['name']} – "
                                "bitte über „Update bei CurseForge herunterladen“ laden.")
            self._dl_checked[key] = sig
        return None

    def _install_download(self, sid: str, path: Path):
        w = self.dl_watch.pop(sid)
        info, asset = w["info"], w["asset"]
        root, flavor = self.root_path, self.flavor

        def done(folders):
            self.remember_cf_source(info, asset, folders)
            self.remember_bundle(folders)
            self.updates.pop(sid, None)
            save_config(self.cfg)
            log.info("Aus dem Download-Ordner aktualisiert: %s (%s) <- %s", info.get("repo"), asset["name"], path)
            self.refresh()
            msg = f"{info.get('repo') or sid} aus deinem Download-Ordner installiert ({asset['name']})."
            if running_wow(root, flavor):
                msg += "  WoW läuft – wirkt nach /reload."
            self.set_status(msg)
            self.notify(msg, "ok", timeout=10000)
            self.refresh_browser_list()

        def failed(err):
            self._dl_checked[str(path)] = self._dl_seen.get(str(path))
            self.dl_watch.setdefault(sid, w)          # weiter auf einen neuen Download warten
            if not self._dl_job:
                self._dl_job = self.after(self.DL_POLL_MS, self._poll_downloads)
            log.error("Download %s konnte nicht installiert werden: %s", path, err)
            self.set_status(f"{path.name} konnte nicht installiert werden: {err}")

        self.run_bg(f"Installiere {path.name}", lambda: install_zip(path, root, flavor), done, failed)

    def _hint_update_link(self):
        try:
            sel = self.selected_addons()
        except tk.TclError:
            return
        if sel and len(self.tree.selection()) == 1:
            name, url = self.cf_update_link(sel)
            if url:
                self.set_status(f"{UPD_MARK} Update für {name} verfügbar – „Jetzt aktualisieren“ rechts oder Rechtsklick.")

    def _on_right_click(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        if iid not in self.tree.selection():
            self.tree.selection_set(iid)
        self.tree.focus(iid)
        sel = self.selected_addons()
        if not sel:
            return
        main = self._group_main(sel)
        items = []
        if any(self.folder_source(a.name)[0] in self.pending_updates() for a in sel):
            items.append((f"{UPD_MARK} Jetzt aktualisieren", lambda: self.update_selected(sel)))
        _, url = self.cf_update_link(sel)
        if url:
            items.append(("Bei CurseForge herunterladen ↗", lambda: self.open_cf_update(sel)))
        sid, src = self.folder_source(main.name)
        if src:
            label = "CurseForge-Seite öffnen ↗" if src.get("type") == "curseforge" else "Release-Seite öffnen ↗"
            items.append((label, lambda: self.open_link(self.source_page(sid, src))))
        if items:
            items.append(None)
        items += [("Details", lambda: self.show_details(main)),
                  ("Aktivieren / Deaktivieren", lambda: self.toggle_selected(sel)),
                  ("Ordner öffnen", lambda: self.open_folder(sel))]
        menu = self._menu(self.tree, items)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    def _on_double_click(self, event):
        iid = self.tree.identify_row(event.y)
        if iid and self._column_at(self.tree.identify_column(event.x)) == "update":
            self.tree.selection_set(iid)
            if self.cf_update_link(self.selected_addons())[1]:
                self.open_cf_update()
                return "break"
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

    def _claim_folders(self, sid: str, folders: list[str]):
        """Ein Ordner gehört immer genau einer Quelle (z. B. nach Wechsel GitHub -> CurseForge)."""
        for other, s in self.sources().items():
            if other != sid:
                s["folders"] = [f for f in s.get("folders", []) if f not in folders]

    def remember_source(self, repo: str, info: dict, asset: dict, folders: list[str]):
        if repo.startswith(CF_PREFIX):
            self.remember_cf_source(info, asset, folders)
            return
        self._claim_folders(repo, folders)
        self.sources()[repo] = {"type": "github", "tag": info["tag"], "asset": asset["name"],
                                "asset_key": asset_key(asset["name"]) if asset["name"] != "zipball" else "",
                                "folders": folders}

    def remember_cf_source(self, info: dict, asset: dict, folders: list[str]):
        sid = CF_PREFIX + str(info["mod_id"])
        folders = sorted(set(folders) | set(self.sources().get(sid, {}).get("folders", [])))
        self._claim_folders(sid, folders)
        self.sources()[sid] = {
            "type": "curseforge", "tag": asset["name"], "asset": asset["name"],
            "asset_key": asset_key(asset["name"]), "mod_id": info["mod_id"], "file_id": asset["file_id"],
            "slug": info["slug"], "name": info["repo"], "author": info["author"], "page": info["page"],
            "allow_dl": info["allow_dl"], "game_versions": asset["game_versions"],
            "release_type": asset["release_type"], "folders": folders}

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
        self.update_game_info()
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
        source = self.source_label(sid, src) if src else "manuell"
        upd = ""
        has_update = bool(src and self.updates.get(sid) and self.updates[sid] != src.get("tag"))
        if src:
            upd = (f"{UPD_MARK} {self.updates[sid]}" + ("  (Browser)" if src.get("allow_dl") is False else "")
                   if has_update else src.get("tag", ""))
        if has_update:
            status = f"{UPD_MARK} {status}"
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
        if "update" in child_tags:
            status = f"{UPD_MARK} {status}"
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
        if not hasattr(self, "ui"):
            return
        has_tree = self.tree is not None and self.tree.winfo_exists()
        sel_before = set(self.tree.selection()) if has_tree else set()
        if has_tree:
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

        for i, (_, kind, payload) in enumerate(entries if has_tree else []):
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

        if has_tree:
            keep = [iid for iid in sel_before if self.tree.exists(iid)]
            if keep:
                self.tree.selection_set(keep)
        if self.cards_inner is not None and self.cards_inner.winfo_exists() and self.view == "cards":
            cards = []
            for _, kind, payload in entries:
                if kind == "single":
                    vals, tags, a = payload
                    cards.append((a.title if a.title and len(a.title) < 32 else a.name, [a], vals, tags))
                else:
                    g, gvals, gtags, _ = payload
                    cards.append((g["title"], g["members"], gvals, gtags))
            self.render_cards(cards)
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
        if self.nav_buttons:
            self.nav_buttons["addons"].configure(text=f"Meine Addons   {len(self.addons)}")
            self.nav_buttons["updates"].configure(text=f"Updates   {UPD_MARK} {upd}" if upd else "Updates")
        self.upd_count_var.set(f"{upd} Update(s) verfügbar" if upd else
                               ("Alles aktuell" if self.updates else "Noch nicht geprüft"))
        self.update_detail()
        if self.current_page == "updates":
            self.refresh_updates_page()

    def sort_by(self, col):
        self.sort_rev = not self.sort_rev if self.sort_col == col else False
        self.sort_col = col
        self.render()

    def show_details(self, addon=None):
        """Zeigt ein Addon im Detailbereich der Seite „Meine Addons“ (kein eigenes Fenster)."""
        self.show_page("addons")
        if addon is not None:
            self.select_addons([addon])

    # ----- Entdecken: Direkt installieren -------------------------------------- #
    def recent_files(self, exts: tuple[str, ...], limit: int = 20) -> list[Path]:
        """Neueste Dateien mit diesen Endungen im Download-Ordner."""
        try:
            files = [p for p in self.download_dir().iterdir() if p.is_file() and p.suffix.lower() in exts]
        except OSError:
            return []
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return files[:limit]

    def refresh_zip_choices(self):
        self._zip_files = self.recent_files((".zip",), 25)
        labels = [f"{p.name}   ·   {datetime.fromtimestamp(p.stat().st_mtime):%d.%m. %H:%M}" for p in self._zip_files]
        self.zip_cb["values"] = labels
        self.zip_var.set(labels[0] if labels else "")          # die neueste Datei vorauswählen

    def install_zip_choice(self):
        labels = list(self.zip_cb["values"])
        if self.zip_var.get() not in labels:
            self.info(f"Im Download-Ordner ({self.download_dir()}) liegt keine ZIP-Datei. "
                      "Wähle eine mit „Andere …“ oder zieh sie ins Fenster.")
            return
        self.install_zip_dialog([str(self._zip_files[labels.index(self.zip_var.get())])])

    def install_cf_link(self):
        """CurseForge-Link (oder Name/ID) aus dem Feld auf „Entdecken“ installieren."""
        text = self.cf_link_var.get().strip()
        if not text:
            self.info("Füge zuerst den Link zu einem CurseForge-Addon ein.")
            return
        try:
            ref = parse_cf_ref(text)
        except ValueError as e:
            self.error(str(e))
            return
        if not self.ready():
            return
        key = self.need_cf_key()
        if not key:
            return
        flavor, gv = self.flavor, (self.game[0] if self.game else "")

        def job():
            mod = cf_get_mod(ref, key)
            files = cf_files(mod["id"], key)
            if not files:
                raise CFError(f"{mod.get('name', ref)}: keine ZIP-Dateien bei CurseForge gefunden.")
            return cf_info(mod, files, flavor, gv)

        def done(info):
            self.cf_link_var.set("")
            self._pick_and_install_cf(info)
        self.run_bg("Suche auf CurseForge", job, done)

    def install_curseforge_dialog(self):
        """Früher ein Fenster – jetzt die Seite „Entdecken“."""
        self.show_page("discover")

    # ----- Entdecken: CurseForge-Suche ---------------------------------------- #
    @property
    def cf_family(self) -> str:
        return wow_family(self.game[0]) if self.game else ""

    def _cf_typed(self):
        if self._cf_debounce:
            self.after_cancel(self._cf_debounce)
            self._cf_debounce = None
        text = self.cf_q.get().strip()
        if len(text) >= 3 or not text:
            self._cf_debounce = self.after(700, self.cf_search)

    def cf_search(self, more: bool = False):
        if self._cf_debounce:
            self.after_cancel(self._cf_debounce)
            self._cf_debounce = None
        key = self.cf_key()
        if not key:
            self.cf_count.set("Für die Suche brauchst du deinen CurseForge-API-Key (Einstellungen).")
            self.need_cf_key()
            return
        self.cf_req += 1
        req, text, sort = self.cf_req, self.cf_q.get().strip(), self.cf_sort.get()
        index = len(self.cf_results) if more else 0
        self.cf_count.set("Suche bei CurseForge …")
        self.cf_more_btn.state(["disabled"])
        self.cf_searching = True

        def done(res, err):
            if req != self.cf_req:
                return                                   # veraltete Antwort
            self.cf_searching = False
            if err:
                self.cf_count.set(f"Fehler: {err}")
                return
            mods, total = res
            self.cf_direct = "curseforge.com" in text.lower() or text.isdigit()
            known = {m["id"] for m in self.cf_results} if more else set()
            self.cf_results = (self.cf_results if more else []) + [m for m in mods if m.get("id") not in known]
            self.cf_total = total
            self.cf_show_rows()

        self.run_quiet(lambda: cf_search(text, key, sort, index), done)

    def _cf_installed(self) -> set[int]:
        return {int(sid[len(CF_PREFIX):]) for sid in self.sources()
                if sid.startswith(CF_PREFIX) and sid[len(CF_PREFIX):].isdigit()}

    def cf_show_rows(self):
        fam = self.cf_family
        self.cf_fit_chk.configure(text=f"Nur Addons für meine Spielversion ({fam})" if fam
                                  else "Nur Addons für meine Spielversion (unbekannt)")
        self.cf_fit_chk.state(["!disabled"] if fam else ["disabled"])
        tv = self.cf_tree
        sel = set(tv.selection())
        tv.delete(*tv.get_children())
        installed = self._cf_installed()
        shown = 0
        for m in self.cf_results:
            fams = cf_mod_families(m)
            fits = not fam or not fams or fam in fams
            if self.cf_fit.get() and fam and not fits and not self.cf_direct:
                continue
            inst = m.get("id") in installed
            authors = ", ".join(a.get("name", "") for a in m.get("authors") or [] if a.get("name"))
            tags = ["dim"] if inst else ([] if fits else ["other"])
            if shown % 2:
                tags.append("odd")
            tv.insert("", "end", iid=str(m["id"]), tags=tags,
                      values=(m.get("name", ""), authors, fmt_count(m.get("downloadCount")),
                              str(m.get("dateModified", ""))[:10], ", ".join(fams) or "–",
                              "✓ installiert" if inst else ""))
            shown += 1
        keep = [i for i in sel if tv.exists(i)]
        if keep:
            tv.selection_set(keep)
        hidden = len(self.cf_results) - shown
        text = f"{shown} angezeigt"
        if hidden:
            text += f" · {hidden} für andere Spielversionen ausgeblendet"
        text += f" · {self.cf_total:,} Treffer".replace(",", ".")
        if not self.cf_results:
            text = "Nichts gefunden." if self.cf_req else ""
        self.cf_count.set(text)
        more = len(self.cf_results) < self.cf_total and len(self.cf_results) + CF_PAGE_SIZE <= 10_000
        self.cf_more_btn.state(["!disabled"] if more else ["disabled"])
        self.cf_show_details()

    def _cf_selected(self) -> list[dict]:
        ids = set(self.cf_tree.selection())
        return [m for m in self.cf_results if str(m.get("id")) in ids]

    def cf_show_details(self):
        sel = self._cf_selected()
        self.cf_file_cb["values"] = []
        self.cf_file_var.set("")
        if not sel:
            self.cf_det_title.configure(text="Kein Addon gewählt")
            self.cf_det_meta.configure(text="")
            self.cf_det_text.configure(text="Suche oben nach einem Addon und wähle es in der Liste aus. "
                                            "Mit Strg oder Umschalt markierst du mehrere.")
            self.cf_install_btn.configure(text="Installieren")
            self.cf_install_btn.state(["disabled"])
            self.cf_page_btn.state(["disabled"])
            self.cf_file_cb.state(["disabled"])
            return
        installed = self._cf_installed()
        todo = [m for m in sel if m.get("id") not in installed]
        self.cf_page_btn.state(["!disabled"])
        if len(sel) > 1:
            self.cf_det_title.configure(text=f"{len(sel)} Addons ausgewählt")
            self.cf_det_meta.configure(text="")
            self.cf_det_text.configure(text="Für jedes Addon wird automatisch die passende Datei gewählt."
                                       + (f"\n{len(sel) - len(todo)} davon sind schon installiert."
                                          if len(todo) < len(sel) else ""))
            self.cf_install_btn.configure(text=f"{len(todo)} installieren")
            self.cf_install_btn.state(["!disabled"] if todo else ["disabled"])
            self.cf_file_cb.state(["disabled"])
            return
        m = sel[0]
        authors = ", ".join(a.get("name", "") for a in m.get("authors") or [] if a.get("name"))
        self.cf_det_title.configure(text=m.get("name", ""))
        self.cf_det_meta.configure(text=(f"von {authors}  ·  " if authors else "")
                                   + f"{fmt_count(m.get('downloadCount'))} Downloads  ·  "
                                   + str(m.get("dateModified", ""))[:10])
        summary = m.get("summary", "").strip()
        lines = [summary[:240] + ("…" if len(summary) > 240 else "")]
        if m.get("allowModDistribution") is False:
            lines.append("Der Autor erlaubt keine Programm-Downloads – Leeroyzan öffnet dafür den Browser "
                         "und installiert die Datei danach automatisch.")
        self.cf_det_text.configure(text="\n\n".join(x for x in lines if x))
        inst = m.get("id") in installed
        self.cf_install_btn.configure(text="✓ installiert" if inst else "Installieren")
        self.cf_install_btn.state(["disabled"] if inst else ["!disabled"])
        self.cf_file_cb.state(["disabled"])
        if inst:
            return
        cached = self.cf_files_cache.get(m["id"])
        if cached:
            self._cf_fill_files(m["id"])
            return
        self.cf_file_var.set("Dateien werden geladen …")
        key, flavor, gv = self.cf_key(), self.flavor, (self.game[0] if self.game else "")

        def load():
            files = cf_files(m["id"], key)
            if not files:
                raise CFError("keine ZIP-Dateien bei CurseForge gefunden")
            return cf_info(m, files, flavor, gv)

        def done(info, err):
            if err:
                if self._cf_selected() == [m]:
                    self.cf_file_var.set(f"Fehler: {err}")
                return
            self.cf_files_cache[m["id"]] = info
            if self._cf_selected() == [m]:
                self._cf_fill_files(m["id"])
        self.run_quiet(load, done)

    def _cf_fill_files(self, mod_id: int):
        info = self.cf_files_cache[mod_id]
        labels = [a.get("label", a["name"]) for a in info["assets"]]
        self.cf_file_cb["values"] = labels
        best = info["best"] or (info["assets"][0] if info["assets"] else None)
        if best:
            self.cf_file_var.set(labels[info["assets"].index(best)])
        self.cf_file_cb.state(["readonly", "!disabled"])

    def cf_open_page(self):
        sel = self._cf_selected()
        if sel:
            m = sel[0]
            self.open_link((m.get("links") or {}).get("websiteUrl")
                           or f"https://www.curseforge.com/wow/addons/{m.get('slug', '')}")

    def cf_install(self):
        installed = self._cf_installed()
        sel = [m for m in self._cf_selected() if m.get("id") not in installed]
        if not sel or not self.ready():
            return
        if len(sel) > 1:
            self._run_list_import([{"t": "c", "i": m["id"], "n": m.get("name", ""), "s": m.get("slug", "")}
                                   for m in sel], {}, what="von CurseForge")
            return
        info = self.cf_files_cache.get(sel[0]["id"])
        if not info:
            self.info("Die Dateiliste wird noch geladen – einen Moment.")
            return
        labels = list(self.cf_file_cb["values"])
        asset = (info["assets"][labels.index(self.cf_file_var.get())] if self.cf_file_var.get() in labels
                 else info["best"])
        self._install_cf_asset(info, asset)

    # ----- Updates-Seite --------------------------------------------------------- #
    def pending_updates(self) -> list[str]:
        return [r for r, tg in self.updates.items() if r in self.sources() and tg != self.sources()[r].get("tag")]

    def refresh_updates_page(self):
        if not getattr(self, "upd_tree", None):
            return
        tv = self.upd_tree
        sel = tv.selection()
        tv.delete(*tv.get_children())
        pend = self.pending_updates()
        for i, r in enumerate(sorted(pend, key=lambda r: self.source_name(r).lower())):
            src, info = self.sources()[r], self.update_info.get(r, {})
            hint = "Browser-Download" if r.startswith(CF_PREFIX) and info.get("allow_dl") is False else \
                ("CurseForge" if r.startswith(CF_PREFIX) else "GitHub")
            tv.insert("", "end", iid=r, tags=["update"] + (["odd"] if i % 2 else []),
                      values=(self.source_name(r), src.get("tag", "?"), f"{UPD_MARK} {self.updates[r]}", hint))
        keep = [i for i in sel if tv.exists(i)]
        if keep:
            tv.selection_set(keep)
        elif pend:
            tv.selection_set(tv.get_children()[0])
        if not pend:
            self._set_text(self.upd_notes, parts=[("Alles aktuell.\n", "head"),
                                                  ("Keine Updates bekannt." if self.updates else
                                                   "Noch nicht geprüft – „Jetzt prüfen“.", "dim")])
        self._upd_show()
        self.refresh_browser_list()

    def _upd_selected(self) -> str:
        sel = self.upd_tree.selection() if getattr(self, "upd_tree", None) else ()
        return sel[0] if sel else ""

    def _upd_show(self):
        r = self._upd_selected()
        for b in (self.upd_one_btn, self.upd_page_btn, self.upd_dl_btn):
            b.state(["!disabled"] if r else ["disabled"])
        if not r:
            return
        info = self.update_info.get(r, {})
        src = self.sources().get(r, {})
        notes = (info.get("notes") or "").strip() or "Keine Änderungshinweise vorhanden."
        parts = [(f"{self.source_name(r)}\n", "head"),
                 (f"{src.get('tag', '?')}  →  {self.updates.get(r, '?')}"
                  + (f"   ·   {info['published']}" if info.get("published") else "") + "\n\n", "dim")]
        if r.startswith(CF_PREFIX) and info.get("allow_dl") is False:
            parts.append(("Der Autor sperrt Downloads über Programme: Leeroyzan öffnet die Download-Seite und "
                          "installiert die Datei, sobald sie in deinem Download-Ordner liegt.\n\n", "dim"))
        parts.append((notes, ""))
        self._set_text(self.upd_notes, parts=parts)
        self.upd_dl_btn.state(["!disabled"] if r.startswith(CF_PREFIX) and info.get("best") else ["disabled"])

    def update_selected_in_list(self):
        r = self._upd_selected()
        if r:
            self.update_all(only=[r])

    def _upd_open_page(self):
        r = self._upd_selected()
        if r and r in self.sources():
            self.open_link(self.source_page(r, self.sources()[r]))

    def _upd_open_download(self):
        r = self._upd_selected()
        if r:
            self.open_cf_update(sid=r)

    def update_selected(self, addons=None):
        sel = list(addons) if addons is not None else self.selected_addons()
        sids = []
        for a in sel:
            sid, src = self.folder_source(a.name)
            if sid and sid not in sids and sid in self.pending_updates():
                sids.append(sid)
        if sids:
            self.update_all(only=sids)

    def refresh_browser_list(self):
        """Karte „Im Browser laden“ auf der Updates-Seite: gesperrte Downloads, auf die Leeroyzan wartet."""
        if not getattr(self, "browser_box_outer", None):
            return
        if self.browser_box is not None:
            self.browser_box.master.destroy()
            self.browser_box = None
        waiting = [(sid, w) for sid, w in self.dl_watch.items() if w.get("manual")]
        if not waiting:
            if "browser" in self.banners:
                self.dismiss("browser")
            return
        box = self._card_box(self.browser_box_outer, pady=(12, 0),
                             title=f"Im Browser laden · Leeroyzan wartet auf {len(waiting)} Datei(en)")
        self.browser_box = box
        ttk.Label(box, style="Panel.TLabel", wraplength=900, justify="left",
                  text=f"Sobald die Datei in {self.download_dir()} liegt, wird sie automatisch installiert.").pack(
            anchor="w", pady=(0, 6))
        for sid, w in waiting:
            row = ttk.Frame(box, style="Panel.TFrame")
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=f"{w['info'].get('repo') or sid}  ·  {w['asset'].get('file_name', '')}",
                      style="Panel.TLabel").pack(side="left")
            ttk.Button(row, text="Nicht mehr warten",
                       command=lambda s=sid: (self.dl_watch.pop(s, None), self.refresh_browser_list())).pack(side="right")
            ttk.Button(row, text="Download-Seite öffnen ↗",
                       command=lambda w=w: self.open_link(cf_download_url(w["info"], w["asset"]))).pack(
                side="right", padx=(0, 6))

    # ----- Teilen-Seite ------------------------------------------------------------ #
    def share_selection(self):
        sel = self.selected_addons()
        self.share_scope.set("sel" if sel else "all")
        self.show_page("share")

    def _share_addons(self) -> list:
        if self.share_scope.get() == "sel":
            return self.selected_addons()
        return list(self.addons)

    def rebuild_share(self):
        if not getattr(self, "share_txt", None):
            return
        n_sel = len(self.selected_addons())
        self.share_sel_rb.configure(text=f"Ausgewählte Addons ({n_sel})")
        self.share_all_rb.configure(text=f"Alle Addons ({len(self.addons)})")
        if not n_sel and self.share_scope.get() == "sel":
            self.share_scope.set("all")
        self.share_sel_rb.state(["!disabled"] if n_sel else ["disabled"])
        addons = self._share_addons()
        self._share_payload, unlinked = build_list(addons, self.sources(), self.flavor, self.sets(),
                                                   self.share_exact.get(), self.share_sets.get())
        self._share_unlinked = unlinked
        if not self._share_payload["a"]:
            self._share_parts = []
            self._set_text(self.share_txt, "Keines dieser Addons hat eine Download-Quelle (CurseForge oder GitHub). "
                                           "Verknüpfe sie mit CurseForge oder teile sie als Paket.")
            self.share_info.set("")
            return
        self._share_parts = list_to_code(self._share_payload, LIST_PART_SIZES[self.share_size.get()])
        parts = []
        for i, p in enumerate(self._share_parts, 1):
            if len(self._share_parts) > 1:
                parts.append((f"Teil {i} von {len(self._share_parts)} – als eigene Nachricht senden:\n", "dim"))
            parts.append((p + "\n\n", ""))
        self._set_text(self.share_txt, parts=parts)
        n_c = sum(1 for e in self._share_payload["a"] if e["t"] == "c")
        info = (f"{len(self._share_payload['a'])} Addons ({n_c} CurseForge, {len(self._share_payload['a']) - n_c} "
                f"GitHub) · {len(self._share_parts)} Teil(e), {sum(len(x) for x in self._share_parts)} Zeichen.")
        if unlinked:
            info += (f"\nOhne Download-Quelle, nicht enthalten: {', '.join(unlinked[:6])}"
                     + (" …" if len(unlinked) > 6 else "") + " – dafür „Als Paket speichern“.")
        self.share_info.set(info)

    def _clip(self, text: str, what: str):
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update()
        self.notify(what, "ok", key="clip", timeout=6000)

    def copy_share_code(self):
        if not getattr(self, "_share_parts", None):
            return
        n = len(self._share_parts)
        self._clip("\n\n".join(self._share_parts),
                   "Code kopiert – einfach in Discord oder eine Nachricht einfügen." if n == 1 else
                   f"Alle {n} Teile kopiert (getrennt durch Leerzeilen). Sende jeden Teil als eigene Nachricht.")

    def copy_share_readable(self):
        if getattr(self, "_share_payload", None) and self._share_payload["a"]:
            self._clip(list_readable(self._share_payload), "Lesbare Liste mit Links kopiert.")

    def _unique_path(self, stem: str, ext: str) -> Path:
        d = self.download_dir()
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{stem}{ext}"
        n = 2
        while p.exists():
            p = d / f"{stem} ({n}){ext}"
            n += 1
        return p

    def save_share_file(self):
        if not getattr(self, "_share_payload", None) or not self._share_payload["a"]:
            return
        p = self._unique_path(f"Addonliste_{datetime.now():%Y-%m-%d}", LIST_EXT)
        p.write_text(list_to_file_text(self._share_payload), encoding="utf-8")
        log.info("Download-Liste gespeichert: %s", p)
        self.notify(f"Liste gespeichert: {p}", "ok", [("Ordner öffnen", lambda: open_in_explorer(p.parent))],
                    key="saved", timeout=20000)

    def export_share_pack(self):
        addons = self._share_addons()
        if not addons:
            self.info("Es sind keine Addons installiert.")
            return
        self.export_pack_dialog(addons)

    def _import_modified(self, event=None):
        if not self.import_txt.edit_modified():
            return
        self.import_txt.edit_modified(False)
        if getattr(self, "_imp_job", None):
            self.after_cancel(self._imp_job)
        self._imp_job = self.after(300, self.preview_import)

    def paste_import(self):
        try:
            self.import_txt.insert("end", self.clipboard_get() + "\n")
        except tk.TclError:
            self.info("Die Zwischenablage ist leer.")

    def preview_import(self):
        self._imp_job = None
        text = self.import_txt.get("1.0", "end").strip()
        self._import_data = None
        self.import_btn.state(["disabled"])
        if not text:
            self.import_preview.set("")
            self.import_btn.configure(text="Installieren")
            return
        try:
            data = parse_list_text(text)
        except ListError as e:
            self.import_preview.set(f"⚠ {e}")
            return
        have = self.sources()
        todo, lines = [], []
        for e in data["a"]:
            sid = CF_PREFIX + str(e["i"]) if e["t"] == "c" else e["r"]
            name = (e.get("n") or e.get("s") or sid) if e["t"] == "c" else e["r"]
            if sid in have:
                lines.append(f"✓ {name} (schon da)")
            else:
                todo.append(e)
                lines.append(f"• {name}")
        head = f"Liste mit {len(data['a'])} Addons ({data['fl'].strip('_') or '?'})"
        if data["fl"] and data["fl"] != self.flavor:
            head += f" – du installierst in {self.flavor.strip('_')}"
        if data["s"]:
            head += f" · {len(data['s'])} Set(s)"
        self.import_preview.set(head + ":\n" + "\n".join(lines[:12]) + ("\n…" if len(lines) > 12 else ""))
        self._import_data = (todo, data["s"])
        self.import_btn.configure(text=f"{len(todo)} Addon(s) installieren" if todo else "Alles schon installiert")
        self.import_btn.state(["!disabled"] if todo else ["disabled"])

    def import_list_now(self):
        if not self._import_data or not self.ready():
            return
        todo, sets = self._import_data
        self.import_txt.delete("1.0", "end")
        self._run_list_import(todo, sets)

    def refresh_recv_files(self):
        self._recv_files = self.recent_files((LIST_EXT, PACK_EXT), 20)
        labels = [f"{p.name}   ·   {datetime.fromtimestamp(p.stat().st_mtime):%d.%m. %H:%M}" for p in self._recv_files]
        self.recv_cb["values"] = labels
        self.recv_file_var.set(labels[0] if labels else "")

    def open_recv_file(self):
        labels = list(self.recv_cb["values"])
        if self.recv_file_var.get() not in labels:
            self.info("Im Download-Ordner liegt keine .lzlist- oder .lzpack-Datei.")
            return
        self._open_share_file(self._recv_files[labels.index(self.recv_file_var.get())])

    def open_other_share_file(self):
        f = filedialog.askopenfilename(title="Liste oder Paket öffnen",
                                       filetypes=[("Leeroyzan-Liste/-Paket", f"*{LIST_EXT} *{PACK_EXT}"),
                                                  ("Alle Dateien", "*.*")])
        if f:
            self._open_share_file(Path(f))

    def _open_share_file(self, path: Path):
        if path.suffix.lower() == PACK_EXT:
            self.import_pack_dialog(str(path))
        else:
            self.import_list_dialog(str(path))

    def share_list_dialog(self, addons=None):
        """Früher ein Fenster – jetzt die Seite „Teilen“."""
        if addons is not None:
            self.select_addons(addons)
        self.share_selection()

    def import_list_dialog(self, path=None):
        """Liste aus Datei (oder Drag & Drop) auf der Seite „Teilen“ anzeigen."""
        self.show_page("share")
        if not path:
            self.import_txt.focus_set()
            return
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            self.error(f"Datei konnte nicht gelesen werden: {e}")
            return
        self.import_txt.delete("1.0", "end")
        self.import_txt.insert("1.0", text)
        self.preview_import()

    # ----- Sets & Backups -------------------------------------------------------- #
    def reload_sets(self, select: str = ""):
        self.refresh_sets_overview(select)

    def refresh_sets_overview(self, select: str = ""):
        tv = getattr(self, "sets_tree", None)
        if tv is None:
            return
        cur = select or self.set_var.get()
        tv.delete(*tv.get_children())
        tv.insert("", "end", iid=SET_ALL_OFF, values=(SET_ALL_OFF, "0"), tags=["dim"])
        tv.insert("", "end", iid=SET_ALL_ON, values=(SET_ALL_ON, str(len(self.addons))), tags=["dim"])
        for i, (name, members) in enumerate(sorted(self.sets().items(), key=lambda kv: kv[0].lower())):
            tv.insert("", "end", iid=f"set:{name}", values=(name, str(len(members))), tags=["odd"] if i % 2 else [])
        iid = f"set:{cur}" if cur in self.sets() else (cur if cur in (SET_ALL_OFF, SET_ALL_ON) else "")
        if iid and tv.exists(iid):
            tv.selection_set(iid)
        self._set_selected()

    def _set_selected(self):
        sel = self.sets_tree.selection()
        name = ""
        if sel:
            name = sel[0][4:] if sel[0].startswith("set:") else sel[0]
        self.set_var.set(name)
        if name in self.sets():
            self.set_members.configure(text=", ".join(sorted(self.sets()[name], key=str.lower)))
        elif name == SET_ALL_OFF:
            self.set_members.configure(text="Schaltet alle Addons aus.")
        elif name == SET_ALL_ON:
            self.set_members.configure(text="Schaltet alle Addons ein.")
        else:
            self.set_members.configure(text="Wähle ein Set aus – oder speichere die gerade aktiven Addons "
                                            "als neues Set.")

    def refresh_backup_list(self):
        tv = getattr(self, "backup_tree", None)
        if tv is None:
            return
        tv.delete(*tv.get_children())
        self._backups = list_backups(self.flavor) if self.flavor else []
        for i, b in enumerate(self._backups):
            tv.insert("", "end", iid=str(i), values=(f"{b['when']:%d.%m.%Y  %H:%M}",
                                                     BACKUP_KINDS.get(b["kind"], b["kind"])),
                      tags=["odd"] if i % 2 else [])
        if self._backups:
            tv.selection_set("0")

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
            for zp, part in zip(files, res):
                self.remember_bundle(part)
                self._sync_cf_after_zip(Path(zp).name, part)
            save_config(self.cfg)
            log.info("ZIP installiert: %s", ", ".join(names))
            self.refresh()
            self.set_status(f"Installiert: {', '.join(names)}")

        self.run_bg("Installiere ZIP", job, done)

    def _sync_cf_after_zip(self, zip_name: str, folders: list[str]):
        """Wurde per ZIP genau die bekannte CurseForge-Datei installiert, gilt das Addon als aktuell."""
        for sid, src in list(self.sources().items()):
            if src.get("type") != "curseforge" or not (set(folders) & set(src.get("folders", []))):
                continue
            info = self.update_info.get(sid)
            asset = next((a for a in (info or {}).get("assets", [])
                          if zip_name_matches(zip_name, a.get("file_name", ""))), None)
            if asset:
                self._adopt_cf(sid, asset)
                self.updates.pop(sid, None)

    def install_github_dialog(self):
        if not self.ready():
            return
        text = self.gh_var.get().strip()
        if not text:
            self.show_page("discover")
            self.info("Gib das GitHub-Repository ein (owner/repo oder Link) und klicke auf „Installieren“.")
            return
        try:
            repo = parse_repo(text)
        except ValueError as e:
            self.error(str(e))
            return
        token = self.cfg.get("github_token", "")

        def done(info):
            self.gh_var.set("")
            self._pick_and_install(info)
        self.run_bg(f"Lese Release von {repo}", lambda: github_latest(repo, token), done)

    def need_cf_key(self) -> str:
        if not self.cf_key():
            self.notify("Für CurseForge brauchst du deinen eigenen, kostenlosen API-Key. Er bleibt nur auf "
                        "diesem Rechner.", "warn", [("Key eintragen", self.settings_dialog),
                                                    ("Key holen ↗", lambda: self.open_link(CF_KEY_URL))],
                        key="cfkey")
        return self.cf_key()

    def run_download_queue(self, items: list[dict], intro: str):
        """Gesperrte Addons: Download-Seite öffnen und im Hintergrund auf die Datei warten (kein Fenster)."""
        if not items:
            return
        for it in items:
            self.watch_download(CF_PREFIX + str(it["info"]["mod_id"]), info=it["info"], asset=it["asset"],
                                manual=True)
        self.open_link(items[0]["page"])
        names = ", ".join(i["name"] for i in items[:4]) + (" …" if len(items) > 4 else "")
        more = " Die übrigen Download-Seiten findest du unter „Updates“." if len(items) > 1 else ""
        self.notify(f"{intro} {names}. Klicke im Browser auf den Download – sobald die Datei in "
                    f"{self.download_dir()} liegt, installiert Leeroyzan sie automatisch.{more}", "warn",
                    [("Zur Liste", lambda: self.show_page("updates"))], key="browser")
        self.refresh_browser_list()

    def _adopt_cf(self, sid: str, asset: dict):
        src = self.sources()[sid]
        src.update(tag=asset["name"], asset=asset["name"], asset_key=asset_key(asset["name"]),
                   file_id=asset["file_id"], game_versions=asset["game_versions"],
                   release_type=asset["release_type"])

    def link_curseforge_dialog(self):
        """Manuell installierte Addons mit CurseForge verknüpfen – danach laufen sie bei den Updates mit."""
        if not self.ready(modifies=False):
            return
        key = self.need_cf_key()
        if not key:
            return
        sel = [a for a in self.selected_addons() if not self.folder_source(a.name)[1]]
        targets: list[tuple] = []          # (Projekt-ID oder Slug, [Addons])
        if sel:
            ids = {a.name: toc_curse_id(a.meta) for a in sel}
            if all(ids.values()):
                by_id: dict = {}
                for a in sel:
                    by_id.setdefault(ids[a.name], []).append(a)
                targets = list(by_id.items())
            else:
                names = ", ".join(a.title for a in sel[:4]) + (" …" if len(sel) > 4 else "")
                text = self.ask_text(f"CurseForge-Link zu „{names}“ einfügen:", ok="Verknüpfen")
                if not text:
                    return
                try:
                    targets = [(parse_cf_ref(text), sel)]
                except ValueError as e:
                    self.error(str(e))
                    return
        else:
            by_id = {}
            for a in self.addons:
                cid = toc_curse_id(a.meta)
                if cid and not self.folder_source(a.name)[1]:
                    by_id.setdefault(cid, []).append(a)
            if not by_id:
                self.info("Bei den manuell installierten Addons ist keine CurseForge-Kennung "
                                              "hinterlegt.\n\nTipp: Wähle ein Addon in der Liste aus und klicke "
                                              "erneut – dann kannst du den CurseForge-Link einfügen.")
                return
            targets = list(by_id.items())
        flavor, gv = self.flavor, (self.game[0] if self.game else "")

        def job():
            results, errors, mods = [], [], {}
            ids = [r for r, _ in targets if isinstance(r, int)]
            if len(ids) > 1:
                try:
                    mods = {m["id"]: m for m in cf_get_mods(ids, key)}
                except CFError:
                    mods = {}
            for ref, addons in targets:
                try:
                    mod = mods.get(ref) or cf_get_mod(ref, key)
                    files = cf_files(mod["id"], key)
                    if not files:
                        raise CFError("keine ZIP-Dateien bei CurseForge gefunden")
                    info = cf_info(mod, files, flavor, gv)
                    versions = [a.version for a in addons]
                    results.append((info, cf_match_installed(versions, info["assets"]),
                                    [a.name for a in addons], versions))
                except Exception as e:  # noqa
                    errors.append(f"{addons[0].title}: {e}")
            return results, errors

        def done(out):
            results, errors = out
            unsure = [r for r in results if not r[1]]
            linked = 0
            for info, match, folders, _ in results:
                if match:
                    self.remember_cf_source(info, match, folders)
                    linked += 1
            if unsure:
                names = "\n".join(f"• {i['repo']}  (installiert: {', '.join(v for v in vers if v) or '?'}, "
                                  f"neueste Datei: {i['tag']})" for i, _, _, vers in unsure)
                current = self.ask("Bei diesen Addons lässt sich die installierte Version keiner CurseForge-Datei "
                              f"zuordnen:\n\n{names}\n\nSollen sie als aktuell gelten?\n\n"
                              "Ja = die neueste Datei gilt als installiert.\n"
                              "Nein = sie werden als veraltet behandelt, das nächste Update installiert die neueste Datei.")
                for info, _, folders, _ in unsure:
                    self.remember_cf_source(info, info["best"] if current else dict(info["best"], name="", file_id=None),
                                            folders)
                    linked += 1
            save_config(self.cfg)
            self.refresh()
            self.set_status(f"{linked} Addon(s) mit CurseForge verknüpft.")
            if errors:
                self.warn("Nicht verknüpft:\n\n" + "\n".join(errors))
            if linked:
                self.after(200, self.check_updates)

        self.run_bg("Verknüpfe mit CurseForge", job, done)

    def _pick_and_install_cf(self, info: dict):
        best = info["best"] or info["assets"][0]
        asset = best
        if len(info["assets"]) > 1:
            labels = [a.get("label", a["name"]) for a in info["assets"]]
            idx = self.ask_choice(f"{info['repo']} von {info['author'] or 'unbekannt'} bietet mehrere Dateien an. "
                                  "Welche passt zu deiner Spielversion? (Die Wahl gilt auch für spätere Updates.)",
                                  labels, info["assets"].index(best), ok="Installieren")
            if idx is None:
                self.set_status("Abgebrochen.")
                return
            asset = info["assets"][idx]
        self._install_cf_asset(info, asset)

    def _install_cf_asset(self, info: dict, asset: dict):
        root, flavor, key = self.root_path, self.flavor, self.cf_key()

        def done(folders):
            self.remember_cf_source(info, asset, folders)
            self.remember_bundle(folders)
            self.updates.pop(CF_PREFIX + str(info["mod_id"]), None)
            save_config(self.cfg)
            log.info("CurseForge installiert: %s (%s) -> %s", info["repo"], asset["name"], ", ".join(folders))
            self.refresh()
            self.set_status(f"{info['repo']} von {info['author'] or '?'} installiert: {', '.join(folders)}  "
                            f"·  Quelle: {info['page']}")
            self.notify(f"{info['repo']} von {info['author'] or '?'} ist installiert.", "ok",
                        [("Projektseite ↗", lambda: self.open_link(info["page"]))], timeout=12000)
            if getattr(self, "cf_tree", None):
                self.cf_show_rows()

        def failed(err):
            if isinstance(err, ManualDownload):
                self.set_status("Download über den Browser nötig.")
                self.run_download_queue([cf_queue_item(info, asset)], f"{info['repo']}: {err.reason}")
            else:
                self.set_status(f"Fehler: {err}")
                self.error(str(err))

        self.run_bg(f"Lade {asset['name']}", lambda: cf_install_asset(asset, info, root, flavor, key),
                    done, failed)

    def _pick_and_install(self, info: dict):
        best = choose_asset(info, self.flavor)
        if not best:
            self.error(f"{info['repo']}: Das Release enthält keine Dateien.")
            return
        asset = best
        if len(info["assets"]) > 1:
            labels = [a.get("label", a["name"]) for a in info["assets"]]
            idx = self.ask_choice(f"{info['repo']} {info['tag']} bietet mehrere Dateien an. Welche passt zu "
                                  "deiner Spielversion? (Die Wahl gilt auch für spätere Updates.)",
                                  labels, info["assets"].index(best) if best in info["assets"] else 0,
                                  ok="Installieren")
            if idx is None:
                self.set_status("Abgebrochen.")
                return
            asset = info["assets"][idx]
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
                self.info("Keine Addons mit Update-Quelle (GitHub/CurseForge) "
                                              "in dieser Version installiert.")
            return
        token, cf_key = self.cfg.get("github_token", ""), self.cf_key()
        srcs = {r: dict(self.sources()[r]) for r in repos}
        flavor, gv = self.flavor, (self.game[0] if self.game else "")

        def job():
            res, errors = {}, []
            for r in repos:
                s = srcs[r]
                try:
                    if s.get("type") == "curseforge":
                        if not cf_key:
                            if not silent:
                                errors.append(f"{s.get('name', r)}: CurseForge-API-Key fehlt (Einstellungen).")
                            continue
                        res[r] = cf_check(s, cf_key, flavor, gv)
                    else:
                        res[r] = github_latest(r, token)
                except Exception as e:  # noqa
                    errors.append(f"{s.get('name', r)}: {e}")
            return res, errors

        def done(out):
            res, errors = out
            for r, info in res.items():
                self.updates[r] = info["tag"]
                self.update_info[r] = info
                src = self.sources().get(r)
                if r.startswith(CF_PREFIX) and src:
                    src["allow_dl"] = info.get("allow_dl", True)
                    # Habe ich die neueste Datei schon (z. B. per Browser/ZIP installiert)? Dann kein Update.
                    if info["tag"] != src.get("tag") and info.get("best"):
                        versions = [a.version for a in self.addons if a.name in src.get("folders", [])]
                        if cf_match_installed(versions, [info["best"]]):
                            self._adopt_cf(r, info["best"])
            save_config(self.cfg)
            self.render()
            for r, info in res.items():
                if r.startswith(CF_PREFIX) and info.get("best") and info["tag"] != self.sources()[r].get("tag"):
                    self.watch_download(r)
            n = sum(1 for r, info in res.items() if info["tag"] != self.sources()[r].get("tag"))
            msg = f"{n} Update(s) verfügbar." if n else "Alle Addons sind aktuell."
            if errors:
                msg += f"  ({len(errors)} Fehler)"
                log.warning("Update-Prüfung: %s", "; ".join(errors))
            self.set_status(msg)
            if errors and not silent:
                self.warn("\n".join(errors))

        def failed(err):
            self.set_status(f"Update-Prüfung fehlgeschlagen: {err}")
            if not silent:
                self.error(str(err))

        self.run_bg("Prüfe Updates", job, done, failed)

    def update_all(self, only: list[str] | None = None):
        pending = [r for r in self.pending_updates() if only is None or r in only]
        if not pending:
            self.info("Keine Updates bekannt – erst „Jetzt prüfen“." if not self.updates else
                      "Alle Addons sind aktuell.")
            return
        if not self.ready():
            return
        root, flavor, token = self.root_path, self.flavor, self.cfg.get("github_token", "")
        cf_key = self.cf_key()
        plan = []
        for r in pending:
            info = self.update_info[r]
            if r.startswith(CF_PREFIX):
                asset = info.get("best")
            else:
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
            ok, manual, errors = [], [], []
            for r, info, asset in plan:
                try:
                    if r.startswith(CF_PREFIX):
                        folders = cf_install_asset(asset, info, root, flavor, cf_key)
                    else:
                        folders = github_install_asset(asset, root, flavor, token)
                    ok.append((r, info, asset, folders))
                except ManualDownload as m:
                    manual.append((info, asset, m))
                except Exception as e:  # noqa
                    log.error("Update %s: %s", r, e)
                    errors.append(f"{info.get('repo', r)}: {e}")
            return ok, manual, errors

        def done(out):
            results, manual, errors = out
            for repo, info, asset, folders in results:
                self.remember_source(repo, info, asset, folders)
                log.info("Aktualisiert: %s -> %s (%s)", repo, info["tag"], asset["name"])
            save_config(self.cfg)
            self.refresh()
            msg = f"{len(results)} Addon(s) aktualisiert."
            if manual or errors:
                msg += f"  ({len(manual) + len(errors)} nicht automatisch)"
            self.set_status(msg)
            if results:
                self.notify(msg, "ok", timeout=10000)
            if errors:
                self.warn("Nicht aktualisiert:\n" + "\n".join(errors))
            if manual:
                self.run_download_queue([cf_queue_item(i, a) for i, a, _ in manual],
                                        "Im Browser laden (der Autor sperrt Programm-Downloads):")

        self.run_bg("Aktualisiere", job, done)

    # ----- Auswahl / Verwalten -------------------------------------------- #
    def select_all(self, force: bool = False):
        """Wählt alle sichtbaren Addons aus; ein zweiter Klick hebt die Auswahl auf."""
        if self.tree is None:
            return
        items = self.tree.get_children()
        if not force and items and set(self.tree.selection()) == set(items):
            self.tree.selection_remove(*items)
            self.set_status("Auswahl aufgehoben.")
        else:
            self.tree.selection_set(items)
            self.set_status(f"{len(self.selected_addons())} Addons ausgewählt.")
        self.tree.focus_set()

    def toggle_selected(self, addons=None):
        sel = list(addons) if addons is not None else self.selected_addons()
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
            self.error("\n".join(errors))

    def remove_selected(self, addons=None):
        sel = list(addons) if addons is not None else self.selected_addons()
        if not sel or not self.ready():
            return
        names = ", ".join(a.name for a in sel)
        if not self.ask(f"{names} wirklich entfernen? Die Ordner werden gelöscht.", yes="Entfernen", no="Abbrechen"):
            return
        for a in sel:
            shutil.rmtree(a.folder, ignore_errors=True)
        log.info("Entfernt: %s", names)
        self.refresh()
        self.set_status(f"Entfernt: {names}")

    def open_folder(self, addons=None):
        """Öffnet den Ordner des gewählten Addons; mit addons=[] den AddOns-Ordner selbst."""
        if not self.root_path or not self.flavor:
            return
        sel = list(addons) if addons is not None else self.selected_addons()
        if len(sel) > 1:
            sel = [self._group_main(sel)]
        path = sel[0].folder if sel else addons_dir(self.root_path, self.flavor)
        path.mkdir(parents=True, exist_ok=True)
        open_in_explorer(path)

    # ----- Sets ------------------------------------------------------------ #
    def save_set(self):
        if not self.root_path or not self.flavor:
            return
        active = sorted(a.name for a in self.addons if a.enabled)
        if not active:
            self.info("Es sind keine Addons aktiv.")
            return
        name = self.set_name_var.get()
        if not name or not name.strip():
            self.show_page("sets")
            self.info(f"Gib unten einen Namen für das neue Set ein ({len(active)} aktive Addons).")
            return
        name = name.strip()
        if name in (SET_ALL_OFF, SET_ALL_ON):
            return
        if name in self.sets() and not self.ask(f"Set „{name}“ überschreiben?"):
            return
        self.sets()[name] = active
        save_config(self.cfg)
        self.set_name_var.set("")
        log.info("Set gespeichert: %s (%d Addons)", name, len(active))
        self.reload_sets(name)
        self.refresh_sets_overview()
        self.set_status(f"Set „{name}“ mit {len(active)} Addons gespeichert.")

    def delete_set(self):
        name = self.set_var.get()
        if name not in self.sets():
            return
        if self.ask(f"Set „{name}“ löschen? Die Addons selbst bleiben erhalten.", yes="Löschen", no="Abbrechen"):
            del self.sets()[name]
            save_config(self.cfg)
            log.info("Set gelöscht: %s", name)
            self.reload_sets()
            self.refresh_sets_overview()

    def apply_set(self):
        name = self.set_var.get()
        if not name:
            self.info("Wähle zuerst ein Set aus.")
            return
        if not self.ready():
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
            self.error("\n".join(errors))

    # ----- Teilen ---------------------------------------------------------- #
    def export_pack_dialog(self, addons=None):
        if not self.ready(modifies=False):
            return
        sel = list(addons) if addons is not None else self.selected_addons()
        if not sel:
            self.info("Bitte zuerst die Addons auswählen, die du teilen willst (Strg+Klick oder „Alle auswählen“).")
            return
        by_name = {a.name.lower(): a for a in self.addons}
        chosen = {a.name.lower() for a in sel}
        missing = sorted({d for a in sel for d in a.deps
                          if d.lower() in by_name and d.lower() not in chosen}, key=str.lower)
        if missing and self.ask("Diese Addons werden von deiner Auswahl benötigt:\n\n"
                          + "\n".join(missing) + "\n\nMit ins Paket nehmen?"):
            sel += [by_name[d.lower()] for d in missing]

        dest = self._unique_path(f"Addons_{datetime.now():%Y-%m-%d}", PACK_EXT)
        sources, flavor = dict(self.sources()), self.flavor

        n_cf = sum(1 for a in sel if any(a.name in v.get("folders", []) and v.get("type") == "curseforge"
                                         for v in sources.values()))

        def done(path):
            size = path.stat().st_size / 1024 / 1024
            log.info("Paket exportiert: %s (%d Addons)", path, len(sel))
            self.set_status(f"{len(sel)} Addons exportiert ({size:.1f} MB): {path}")
            extra = (f" {n_cf} CurseForge-Addon(s) sind nur als Verweis enthalten – dein Freund lädt sie "
                     "mit seinem eigenen API-Key." if n_cf else "")
            self.notify(f"Paket mit {len(sel)} Addons gespeichert ({size:.1f} MB): {path}.{extra}", "ok",
                        [("Ordner öffnen", lambda: open_in_explorer(path.parent))], key="saved", timeout=25000)

        self.run_bg("Exportiere Paket", lambda: export_pack(sel, sources, flavor, Path(dest)), done)

    def _run_list_import(self, todo: list[dict], sets: dict, what: str = "aus der Liste"):
        root, flavor = self.root_path, self.flavor
        gv = self.game[0] if self.game else ""
        token, key = self.cfg.get("github_token", ""), self.cf_key()
        if any(e["t"] == "c" for e in todo) and not key:
            self.need_cf_key()
            todo = [e for e in todo if e["t"] != "c"]
            if not todo:
                return

        def job():
            ok, manual, errors = [], [], []
            for e in todo:
                label = (e.get("n") or e.get("s") or str(e["i"])) if e["t"] == "c" else e["r"]
                info = None
                try:
                    if e["t"] == "c":
                        info = cf_pack_item({"mod_id": e["i"], "file_id": e.get("f")}, key, flavor, gv)
                        got = cf_install_asset(info["best"], info, root, flavor, key)
                        ok.append((None, info, info["best"], got))
                    else:
                        info = github_latest(e["r"], token)
                        asset = choose_asset(info, flavor, e.get("k", ""))
                        if not asset:
                            raise ValueError("Das Release enthält keine Dateien.")
                        ok.append((e["r"], info, asset, github_install_asset(asset, root, flavor, token)))
                except ManualDownload as m:
                    manual.append((info, info["best"], m))
                except Exception as ex:  # noqa
                    log.error("Liste: %s: %s", label, ex)
                    errors.append(f"{label}: {ex}")
            return ok, manual, errors

        def done(out):
            ok, manual, errors = out
            installed = []
            for repo, info, asset, got in ok:
                if repo is None:
                    self.remember_cf_source(info, asset, got)
                else:
                    self.remember_source(repo, info, asset, got)
                self.remember_bundle(got)
                installed += got
            for name, members in sets.items():
                if name in (SET_ALL_OFF, SET_ALL_ON):
                    continue
                target = name
                if target in self.sets() and sorted(self.sets()[target]) != sorted(members):
                    target = f"{name} (importiert)"
                self.sets()[target] = members
            save_config(self.cfg)
            log.info("Download-Liste importiert: %s", ", ".join(installed))
            self.refresh()
            self.reload_sets()
            self.refresh_sets_overview()
            msg = f"{len(ok)} Addon(s) {what} installiert."
            if sets:
                msg += f"  {len(sets)} Set(s) übernommen."
            self.set_status(msg)
            if ok:
                self.notify(msg, "ok", timeout=10000)
            if getattr(self, "cf_tree", None):
                self.cf_show_rows()
            if errors:
                self.warn("Nicht installiert:\n" + "\n".join(errors))
            if manual:
                self.run_download_queue([cf_queue_item(i, a) for i, a, _ in manual],
                                        "Im Browser laden (der Autor sperrt Programm-Downloads):")

        self.run_bg(f"Lade Addons {what}", job, done)

    def import_pack_dialog(self, path=None):
        if not self.ready():
            return
        if not path:
            self.show_page("share")
            return
        try:
            manifest = read_pack(Path(path))
        except ValueError as e:
            self.error(str(e))
            return

        entries = manifest["addons"]
        present = {a.name.lower(): a for a in self.addons}
        lines = []
        for e in entries:
            old = present.get(e["folder"].lower())
            ver = e.get("version") or "?"
            note = f"ersetzt deine Version {old.version or '?'}" if old else "neu"
            if (e.get("source") or {}).get("type") == "curseforge":
                note += ", wird von CurseForge geladen"
            lines.append(f"• {e.get('title') or e['folder']}  ({ver}) – {note}")
        text = (f"Paket vom {manifest.get('created', '?')[:10]} "
                f"({manifest.get('flavor', '?')}), {len(entries)} Addons:\n"
                + "\n".join(lines[:10]) + ("\n…" if len(lines) > 10 else ""))
        if manifest.get("flavor") and manifest["flavor"] != self.flavor:
            text += (f"\n\nHinweis: Das Paket wurde für {manifest['flavor']} erstellt, "
                     f"du installierst es in {self.flavor}.")
        if not self.ask(text, yes="Installieren", no="Abbrechen"):
            return

        root, flavor = self.root_path, self.flavor
        gv = self.game[0] if self.game else ""
        cf_entries = [e for e in entries if (e.get("source") or {}).get("type") == "curseforge"]
        folders = [e["folder"] for e in entries if e not in cf_entries]
        cf_todo, seen = [], set()
        for e in cf_entries:
            src = e["source"]
            if isinstance(src.get("mod_id"), int) and src["mod_id"] not in seen:
                seen.add(src["mod_id"])
                cf_todo.append(src)
        cf_key = self.cf_key()
        if cf_todo and not cf_key:
            self.need_cf_key()
            cf_todo = []

        def job():
            installed = import_pack(Path(path), root, flavor, folders) if folders else []
            cf_ok, manual, errors = [], [], []
            for src in cf_todo:
                label = src.get("name") or src.get("slug") or str(src["mod_id"])
                try:
                    info = cf_pack_item(src, cf_key, flavor, gv)
                    got = cf_install_asset(info["best"], info, root, flavor, cf_key)
                    cf_ok.append((info, info["best"], got))
                except ManualDownload as m:
                    manual.append((info, info["best"], m))
                except Exception as e:  # noqa
                    errors.append(f"{label}: {e}")
            return installed, cf_ok, manual, errors

        def done(out):
            installed, cf_ok, manual, errors = out
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
            for info, asset, got in cf_ok:
                self.remember_cf_source(info, asset, got)
                installed += got
            save_config(self.cfg)
            log.info("Paket importiert: %s -> %s", path, ", ".join(installed))
            self.refresh()
            self.set_status(f"{len(installed)} Addons aus Paket installiert.")
            if errors:
                self.warn("Nicht installiert:\n\n" + "\n".join(errors))
            if manual:
                self.run_download_queue([cf_queue_item(i, a) for i, a, _ in manual],
                                        "Im Browser laden (der Autor sperrt Programm-Downloads):")

        self.run_bg("Importiere Paket", job, done)

    # ----- Backups --------------------------------------------------------- #
    def backup(self):
        if not self.ready(modifies=False):
            return
        root, flavor = self.root_path, self.flavor

        def done(path):
            log.info("Backup erstellt: %s", path)
            self.refresh_backup_list()
            self.update_stats()
            self.set_status(f"Backup erstellt: {path}")
            self.notify(f"Backup erstellt: {path.name}", "ok", [("Ordner öffnen", lambda: open_in_explorer(path.parent))],
                        key="backup", timeout=15000)

        self.run_bg("Sichere WTF", lambda: backup_wtf(root, flavor), done)

    def restore_dialog(self):
        if not self.root_path or not self.flavor:
            return
        backups = list_backups(self.flavor)
        if not backups:
            self.info(f"Für {self.flavor} gibt es noch keine Backups – lege eins mit „Jetzt sichern“ an.")
            return
        self.show_page("sets")
        sel = self.backup_tree.selection()
        if not sel:
            self.info("Wähle in der Liste das Backup aus, das du zurückholen willst.")
            return
        chosen = self._backups[int(sel[0])]
        if not self.ask(f"Stand vom {chosen['when']:%d.%m.%Y %H:%M} wiederherstellen? Dein aktueller Stand wird "
                        "vorher automatisch gesichert.", yes="Wiederherstellen", no="Abbrechen"):
            return
        if not self.ready():
            return
        root, flavor = self.root_path, self.flavor

        def done(safety):
            log.info("Backup wiederhergestellt: %s (vorher gesichert: %s)", chosen["path"], safety)
            self.refresh_backup_list()
            self.update_stats()
            self.set_status(f"Stand vom {chosen['when']:%d.%m.%Y %H:%M} wiederhergestellt.")
            self.notify(f"Der Stand vom {chosen['when']:%d.%m.%Y %H:%M} ist wiederhergestellt. Dein vorheriger "
                        "Stand wurde als „vor Wiederherstellung“ gesichert.", "ok", timeout=15000)

        self.run_bg("Stelle Backup wieder her",
                    lambda: restore_backup(chosen["path"], root, flavor), done)

    # ----- Selbst-Update --------------------------------------------------- #
    def _report_self_update(self):
        """Nach einem Neustart durch das Manager-Update: Erfolg melden oder erklären, was schiefging."""
        pending = self.cfg.pop("pending_self_update", None)
        if not pending:
            return
        save_config(self.cfg)
        if parse_version(APP_VERSION) >= parse_version(pending.get("to", "")):
            log.info("Manager-Update erfolgreich: %s -> %s", pending.get("from"), APP_VERSION)
            self.set_status(f"✓ Leeroyzan wurde auf v{APP_VERSION} aktualisiert.")
            self.notify(f"Leeroyzan wurde auf v{APP_VERSION} aktualisiert.", "ok", timeout=12000)
            return
        log.error("Manager-Update auf %s wurde nicht eingesetzt (läuft weiter v%s)", pending.get("to"), APP_VERSION)
        if self.ask(f"Das Update auf {pending.get('to')} wurde geladen, konnte aber nicht eingesetzt "
                          f"werden – es läuft weiter v{APP_VERSION}.\n\n"
                          f"Leeroyzan liegt hier:\n{Path(sys.executable).parent}\n\n"
                          "Häufige Ursachen: ein geschützter Ordner (z. B. „Programme“), ein Virenscanner oder "
                          "OneDrive sperrt die Datei. Lege Leeroyzan.exe am besten in einen eigenen Ordner, z. B. "
                          "Dokumente\\Leeroyzan.\n\nDetails stehen in update.log.\n\n"
                          "Release-Seite öffnen, um die neue Version einmalig von Hand zu laden?", icon="warning"):
            self.open_link(f"https://github.com/{UPDATE_REPO}/releases/latest")

    def check_manager_update(self, silent: bool = False):
        if self.busy:
            if silent:                       # z. B. während der Addon-Prüfung beim Start
                self.after(2000, lambda: self.check_manager_update(silent=True))
            else:
                self.info("Bitte warten, ein Vorgang läuft noch.")
            return
        token = self.cfg.get("github_token", "")

        def done(info):
            if not info:
                self.set_status(f"Der Manager ist aktuell (v{APP_VERSION}).")
                self.manager_state.set(f"Installiert: v{APP_VERSION}  ·  das ist die neueste Version.")
                if not silent:
                    self.notify(f"Du nutzt bereits die neueste Version (v{APP_VERSION}).", "ok", timeout=8000)
                return
            auto = silent and self.cfg.get("self_update_mode") == "auto"
            if auto and update_ready(info):
                self.install_manager_update(info, automatic=True)
                return
            if auto and is_frozen():
                # Release ist veröffentlicht, GitHub baut die .exe aber noch: im Hintergrund warten
                self.watch_manager_build(info["tag"])
                return
            if auto:
                # Python-Skript in einer Git-Arbeitskopie: nur dezent melden
                self.set_status(f"Manager-Update {info['tag']} verfügbar – „git pull“ in deiner Kopie.")
                return
            if silent and info["tag"] == self.cfg.get("skip_manager_version"):
                self.set_status(f"Manager-Update {info['tag']} verfügbar (übersprungen).")
                return
            self.offer_manager_update(info, silent)

        def failed(err):
            if isinstance(err, NoReleaseYet):
                log.info("Manager-Update: noch kein Release in %s", UPDATE_REPO)
                self.set_status("Für den Manager gibt es noch kein Release auf GitHub.")
                if not silent:
                    self.info(f"Im Repository {UPDATE_REPO} wurde noch kein Release veröffentlicht.")
                return
            log.warning("Manager-Update-Prüfung fehlgeschlagen: %s", err)
            self.set_status("Manager-Update-Prüfung fehlgeschlagen.")
            if not silent:
                msg = str(err)
                if isinstance(err, urllib.error.HTTPError) and err.code == 403:
                    msg = "GitHub hat die Anfrage abgelehnt (Limit erreicht?). Bitte in einer Stunde erneut versuchen."
                elif isinstance(err, urllib.error.URLError):
                    msg = f"Keine Verbindung zu GitHub ({err.reason})."
                self.error(f"Update-Prüfung fehlgeschlagen:\n{msg}")

        self.run_bg("Suche Manager-Update", lambda: check_self_update(token), done, failed)

    def offer_manager_update(self, info: dict, silent: bool):
        frozen, ready = is_frozen(), update_ready(info)
        self.manager_info = info
        notes = (info.get("notes") or "").strip()
        self.manager_state.set(f"Installiert: v{APP_VERSION}  ·  neu: {info['tag']}"
                               + (f" vom {info['published']}" if info.get("published") else "")
                               + (f"\n\n{notes[:600]}" + ("…" if len(notes) > 600 else "") if notes else ""))
        if ready:
            actions = [("Jetzt installieren", lambda: self.install_manager_update(info))]
            text = f"Leeroyzan {info['tag']} ist da (du hast v{APP_VERSION}). Installieren dauert ein paar Sekunden – danach startet Leeroyzan neu."
        elif frozen:
            actions = [("Installieren, sobald fertig", lambda: self.watch_manager_build(info["tag"], manual=True))]
            text = (f"Leeroyzan {info['tag']} ist veröffentlicht – GitHub baut die .exe gerade noch (meist 3–5 Minuten).")
        else:
            actions = [("Release-Seite ↗", lambda: self.open_link(info.get("page") or
                                                                  f"https://github.com/{UPDATE_REPO}/releases"))]
            text = f"Leeroyzan {info['tag']} ist da – in deiner Git-Kopie mit „git pull“ holen."
        actions.append(("Was ist neu?", lambda: (self.show_page("updates"), self.after(
            100, lambda: self.offer_manager_update(info, silent)))))
        if silent:
            def skip():
                self.cfg["skip_manager_version"] = info["tag"]
                save_config(self.cfg)
            actions.append(("Überspringen", skip))
        self.notify(text, "update", actions, key="manager")

    BUILD_POLL_MS = 60_000          # jede Minute nachsehen (GitHub erlaubt ohne Token 60 Abfragen/Stunde) …
    BUILD_POLL_TRIES = 30           # … höchstens 30 Minuten lang

    def watch_manager_build(self, tag: str, tries: int = 0, manual: bool = False):
        """Wartet im Hintergrund, bis die .exe des Releases gebaut ist, und installiert sie dann automatisch."""
        if tries == 0:
            if self.watching_build == tag:
                return                               # läuft schon
            self.watching_build = tag
            self._watch_manual = manual
            log.info("Manager-Update %s: warte auf den Build der .exe", tag)
        if self.watching_build != tag:
            return
        if tries >= self.BUILD_POLL_TRIES:
            self.watching_build = ""
            log.warning("Manager-Update %s: .exe nach %d Versuchen nicht fertig", tag, tries)
            self.set_status(f"Für {tag} ist auf GitHub keine Leeroyzan.exe erschienen.")
            if self._watch_manual:
                self.notify(f"Für {tag} ist auch nach 30 Minuten keine Leeroyzan.exe am Release – wahrscheinlich ist "
                            "der automatische Build auf GitHub fehlgeschlagen (rotes X unter „Actions“, dort "
                            "„Re-run jobs“).", "warn",
                            [("Actions-Seite ↗", lambda: self.open_link(f"https://github.com/{UPDATE_REPO}/actions"))],
                            key="manager")
            return
        mins = tries * self.BUILD_POLL_MS // 60_000
        self.manager_state.set(f"Installiert: v{APP_VERSION}  ·  {tag} wird auf GitHub noch gebaut"
                               + (f" (seit {mins} min)" if mins else "") + " – Installation folgt automatisch.")
        self.set_status(f"{UPD_MARK} Manager-Update {tag}: GitHub baut die .exe noch"
                        + (f" (wartet seit {mins} min)" if mins else "")
                        + " – wird automatisch installiert, sobald sie fertig ist.")
        token = self.cfg.get("github_token", "")

        def done(info, err):
            if self.watching_build != tag:
                return
            if err is None and info is None:         # Release zurückgezogen oder schon aktuell
                self.watching_build = ""
                return
            if info and update_ready(info):
                self.watching_build = ""
                self.install_manager_update(info, automatic=True)
                return
            self.after(self.BUILD_POLL_MS, lambda: self.watch_manager_build(tag, tries + 1))

        self.run_quiet(lambda: check_self_update(token), done)

    def install_manager_update(self, info: dict, automatic: bool = False):
        """Lädt die neue .exe, beendet den Manager und lässt sie per Skript einsetzen."""
        if self.busy:                         # laufende Vorgänge nie unterbrechen
            self.after(2000, lambda: self.install_manager_update(info, automatic))
            return
        token = self.cfg.get("github_token", "")

        def done(new_exe):
            log.info("Manager-Update %s geladen (%s), starte neu.", info["tag"],
                     "automatisch" if automatic else "manuell")
            if automatic and self.busy:
                self.after(2000, lambda: done(new_exe))
                return
            try:
                self.cfg["pending_self_update"] = {"from": APP_VERSION, "to": info["tag"]}
                save_config(self.cfg)
                if is_frozen():
                    launch_replacer(new_exe)
                else:
                    apply_script_update(new_exe)
            except Exception as e:  # noqa
                self.cfg.pop("pending_self_update", None)
                save_config(self.cfg)
                self.error(f"Update konnte nicht gestartet werden:\n{e}")
                return
            self.destroy()

        def failed(err):
            log.error("Manager-Update fehlgeschlagen: %s", err)
            if isinstance(err, PermissionError):
                err = (f"Keine Schreibrechte im Ordner {Path(sys.executable).parent}.\n"
                       "Lege Leeroyzan.exe in einen eigenen Ordner, z. B. Dokumente\\Leeroyzan.")
            if automatic:
                self.set_status("Automatisches Manager-Update fehlgeschlagen – siehe Log.")
                if self.cfg.get("self_update_failed_tag") == info["tag"]:
                    return                    # pro Version nur einmal melden
                self.cfg["self_update_failed_tag"] = info["tag"]
                save_config(self.cfg)
            self.notify(f"Das Leeroyzan-Update ist fehlgeschlagen: {err}", "error",
                        [("Release-Seite ↗", lambda: self.open_link(info.get("page") or
                                                                    f"https://github.com/{UPDATE_REPO}/releases"))],
                        key="manager")

        label = (f"Neue Version {info['tag']} gefunden – wird installiert, der Manager startet gleich neu"
                 if automatic else f"Lade Manager {info['tag']}")
        fetch = download_self_update if is_frozen() else download_script_update
        self.run_bg(label, lambda: fetch(info, token), done, failed)

    # ----- Drag & Drop ----------------------------------------------------- #
    def on_drop(self, event):
        paths = [Path(p) for p in self.tk.splitlist(event.data)]
        zips = [str(p) for p in paths if p.suffix.lower() == ".zip"]
        packs = [str(p) for p in paths if p.suffix.lower() == PACK_EXT]
        lists = [str(p) for p in paths if p.suffix.lower() == LIST_EXT]
        if not zips and not packs and not lists:
            self.set_status("Nur .zip-, .lzpack- und .lzlist-Dateien können abgelegt werden.")
            return
        if lists:
            self.after(50, lambda: self.import_list_dialog(lists[0]))
        elif packs:
            self.after(50, lambda: self.import_pack_dialog(packs[0]))
        elif zips:
            self.after(50, lambda: self.install_zip_dialog(zips))


def main():
    setup_logging()
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
