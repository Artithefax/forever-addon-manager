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
  - Spielsitzungen aus dem Begleit-Addon Leeroyzan Link übernehmen, dauerhaft speichern und zusammenfassen
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
APP_VERSION = "1.3.1"
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
# Sprachen – Deutsch ist die Ausgangssprache, die Übersetzungen stehen am Dateiende (TRANSLATIONS)
# --------------------------------------------------------------------------- #

LANGUAGES = {"de": "Deutsch", "en": "English"}
LANG = "de"
TRANSLATIONS: dict[str, dict[str, str]] = {}


def tr(text: str, *args) -> str:
    """Übersetzt einen Oberflächentext in die gewählte Sprache; {0}, {1} … werden mit args gefüllt."""
    out = TRANSLATIONS.get(LANG, {}).get(text, text) if LANG != "de" else text
    if args:
        try:
            return out.format(*args)
        except (IndexError, KeyError, ValueError):
            return text.format(*args)
    return out


def set_language(code: str) -> None:
    global LANG
    LANG = code if code in LANGUAGES else "en"


def detect_language() -> str:
    """Sprache beim ersten Start: die Windows-Anzeigesprache (Deutsch oder Englisch), sonst Englisch."""
    code = ""
    if os.name == "nt":
        try:
            import ctypes
            primary = ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF
            code = {0x07: "de", 0x09: "en"}.get(primary, "")
        except Exception:  # noqa
            code = ""
    if not code:
        import locale
        loc = (locale.getlocale()[0] or os.environ.get("LANG", "") or "").lower()
        code = next((c for c in LANGUAGES if loc.startswith(c)), "")
    return code or "en"


# --------------------------------------------------------------------------- #
# Konfiguration
# --------------------------------------------------------------------------- #

PORTABLE_DIR_NAME = "Leeroyzan-Data"
LEGACY_COPY_LIMIT = 500 * 1024 * 1024     # alte Backups nur bis zu dieser Größe mitnehmen


def app_dir() -> Path:
    """Ordner, in dem Leeroyzan.exe (bzw. leeroyzan.py) liegt."""
    return (Path(sys.executable) if getattr(sys, "frozen", False) else Path(__file__)).resolve().parent


def legacy_dir() -> Path:
    """Datenordner früherer Versionen (bis 1.1): %APPDATA%\\Leeroyzan."""
    return Path(os.environ.get("APPDATA") or str(Path.home() / ".config")) / "Leeroyzan"


def _adopt_legacy_data(dest: Path) -> None:
    """Erster Start der portablen Version: Einstellungen (und, wenn nicht zu groß, Backups) aus
    %APPDATA% übernehmen. Es wird nur kopiert – der alte Ordner bleibt unverändert."""
    old = legacy_dir()
    try:
        if not (old / "config.json").is_file() or (dest / "config.json").exists():
            return
        shutil.copy2(old / "config.json", dest / "config.json")
        backups = old / "backups"
        if backups.is_dir():
            size = sum(f.stat().st_size for f in backups.rglob("*") if f.is_file())
            if size <= LEGACY_COPY_LIMIT:
                shutil.copytree(backups, dest / "backups", dirs_exist_ok=True)
    except OSError:
        pass


def portable_dir() -> Path | None:
    """Leeroyzan ist portabel: Alle Daten (Einstellungen, Logs, Backups) liegen im Ordner
    „Leeroyzan-Data“ neben der .exe. Als Python-Skript nur, wenn dieser Ordner schon existiert."""
    try:
        d = app_dir() / PORTABLE_DIR_NAME
        if d.is_dir():
            return d
        if getattr(sys, "frozen", False):
            d.mkdir()
            _adopt_legacy_data(d)
            return d
    except OSError:
        pass        # Ordner der .exe nicht beschreibbar (z. B. „Programme“) – dann %APPDATA%
    return None


def config_dir() -> Path:
    d = portable_dir() or legacy_dir()
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
    cfg.setdefault("held", {})           # flavor -> [Quellen-IDs], die keine Updates bekommen
    cfg.setdefault("window", "")         # letzte Fenstergröße/-position
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
            raise ValueError(tr("Unsicherer Pfad im Archiv: {0}", member.filename))
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
            raise ValueError(tr("Im Archiv wurde kein Addon (Ordner mit passender .toc) gefunden."))
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
        raise FileExistsError(tr("{0} existiert bereits.", dst))
    shutil.move(str(src), str(dst))


BACKUP_RE_TMPL = r"^WTF{flavor}(\d{{8}}_\d{{6}})(?:_(auto|vorher))?\.zip$"


def backup_wtf(root: str, flavor: str, kind: str = "") -> Path:
    """Sichert den WTF-Ordner. kind: '' (manuell), 'auto' (vor Updates), 'vorher' (vor Wiederherstellung)."""
    wtf = Path(root) / flavor / "WTF"
    if not wtf.is_dir():
        raise FileNotFoundError(tr("Kein WTF-Ordner gefunden: {0}", wtf))
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
            raise ValueError(tr("Das Backup enthält keinen WTF-Ordner."))
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


# --------------------------------------------------------------------------- #
# Rückgängig nach Update: die vorherige Version eines Addons wird aufbewahrt
# --------------------------------------------------------------------------- #

def rollback_dir(flavor: str, sid: str) -> Path:
    return config_dir() / "rollback" / (flavor.strip("_") or "wow") / re.sub(r"[^\w.-]", "_", sid)


def save_rollback(root: str, flavor: str, sid: str, src: dict) -> bool:
    """Kopiert die installierten Ordner einer Quelle vor einem Update weg (je Addon nur die letzte Version)."""
    found = []
    for name in src.get("folders", []):
        if not valid_addon_name(name):
            continue
        for base, on in ((addons_dir(root, flavor), True), (disabled_dir(root, flavor), False)):
            if (base / name).is_dir():
                found.append((base / name, on))
                break
    if not found:
        return False
    dest = rollback_dir(flavor, sid)
    tmp = dest.with_name(dest.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    (tmp / "AddOns").mkdir(parents=True)
    for path, _ in found:
        shutil.copytree(path, tmp / "AddOns" / path.name)
    meta = {"sid": sid, "source": src, "saved": datetime.now().isoformat(timespec="seconds"),
            "enabled": {path.name: on for path, on in found}}
    (tmp / "rollback.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    shutil.rmtree(dest, ignore_errors=True)
    tmp.rename(dest)
    return True


def read_rollback(flavor: str, sid: str) -> dict | None:
    try:
        meta = json.loads((rollback_dir(flavor, sid) / "rollback.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return meta if isinstance(meta, dict) and isinstance(meta.get("source"), dict) else None


def restore_rollback(root: str, flavor: str, sid: str, current: list[str]) -> dict:
    """Ersetzt die installierte Version durch die gesicherte und gibt deren Daten zurück."""
    d = rollback_dir(flavor, sid)
    meta = read_rollback(flavor, sid)
    saved = [p for p in (d / "AddOns").iterdir() if p.is_dir() and valid_addon_name(p.name)] \
        if meta and (d / "AddOns").is_dir() else []
    if not saved:
        raise FileNotFoundError(tr("Für dieses Addon ist keine vorherige Version gespeichert."))
    for name in {*current, *(p.name for p in saved)}:
        if not valid_addon_name(name):
            continue
        for base in (addons_dir(root, flavor), disabled_dir(root, flavor)):
            if (base / name).is_dir():
                shutil.rmtree(base / name)
    enabled = meta.get("enabled") or {}
    for p in saved:
        base = addons_dir(root, flavor) if enabled.get(p.name, True) else disabled_dir(root, flavor)
        base.mkdir(parents=True, exist_ok=True)
        shutil.copytree(p, base / p.name)
    shutil.rmtree(d, ignore_errors=True)
    return meta


def plain_key(name: str) -> str:
    """Vergleichsschlüssel für Addon-Namen: LibDBIcon-1.0 -> libdbicon10."""
    return re.sub(r"[^a-z0-9]", "", clean_wow_text(name).lower())


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
        raise ValueError(tr("Das ist kein gültiges Addon-Paket."))
    if manifest.get("format") != PACK_FORMAT:
        raise ValueError(tr("Paket stammt aus einer anderen Manager-Version (Format {0}). Bitte Manager aktualisieren.", manifest.get('format')))
    if total > PACK_MAX_BYTES:
        raise ValueError(tr("Paket ist zu groß (über 1 GB entpackt)."))
    addons = manifest.get("addons")
    if not isinstance(addons, list) or not addons:
        raise ValueError(tr("Paket enthält keine Addons."))
    for e in addons:
        if not isinstance(e, dict) or not valid_addon_name(str(e.get("folder", ""))):
            raise ValueError(tr("Ungültiger Addon-Name im Paket: {0!r}", e))
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
                    raise ValueError(tr("Unsicherer Pfad im Paket: {0}", m.filename))
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
    raise ValueError(tr("Bitte 'owner/repo' oder eine GitHub-URL angeben."))


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
        return tr("{0} Mio", f"{n / 1_000_000:.1f}".replace(".", ",") if LANG == "de" else f"{n / 1_000_000:.1f}")
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
    lines = [tr("Addon-Liste für WoW {0} – {1} Addons (Leeroyzan)", (payload.get('fl') or '').strip('_'), len(payload['a']))]
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
            raise ListError(tr("Die Teile gehören zu verschiedenen Listen."))
        total = tot
        parts[idx] = m.group(3).upper()
    if not parts:
        raise ListError(tr("Keine Download-Liste erkannt. Der Code beginnt mit „LZ1:“."))
    missing = [i for i in range(1, total + 1) if i not in parts]
    if missing:
        raise ListError(tr("Es fehlt Teil {0} von {1}.", ', '.join(map(str, missing)), total))
    body = "".join(parts[i] for i in range(1, total + 1))
    try:
        d = zlib.decompressobj()
        raw = d.decompress(base64.b32decode(body + "=" * (-len(body) % 8)), 2_000_000)
        if d.unconsumed_tail:
            raise ListError(tr("Die Liste ist ungewöhnlich groß und wird nicht geladen."))
        return json.loads(raw.decode("utf-8"))
    except ListError:
        raise
    except Exception:  # noqa – beschädigter Code
        raise ListError(tr("Der Code ist unvollständig oder beschädigt. Bitte alle Teile erneut kopieren.")) from None


def parse_list_text(text: str) -> dict:
    """Liest eine Liste aus Dateiinhalt (JSON) oder Chat-Text (LZ1-Code) und prüft sie streng."""
    text = text.replace("`", "").strip()
    try:
        data = json.loads(text) if text.startswith("{") else _decode_code(text)
    except json.JSONDecodeError:
        raise ListError(tr("Die Datei ist keine gültige Download-Liste.")) from None
    if not isinstance(data, dict) or data.get("v") != 1 or not isinstance(data.get("a"), list):
        raise ListError(tr("Unbekanntes oder zu neues Listenformat – bitte den Manager aktualisieren."))
    if len(data["a"]) > LIST_MAX_ENTRIES:
        raise ListError(tr("Die Liste ist zu lang."))
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
        raise ListError(tr("Die Liste enthält keine installierbaren Addons."))
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
        raise ValueError(tr("Das Release enthält noch keine {0}.", EXE_ASSET))
    blob = http_get(asset["url"], token, accept="application/octet-stream")
    if len(blob) < 1_000_000 or blob[:2] != b"MZ":
        raise ValueError(tr("Die heruntergeladene Datei ist keine gültige Windows-Anwendung."))
    sha_asset = next((a for a in info.get("all_assets", [])
                      if a["name"].lower() == (asset["name"] + ".sha256").lower()), None)
    if not sha_asset:
        raise ValueError(tr("Zum Release gehört noch keine Prüfsumme – der Build ist wohl noch nicht fertig."))
    expected = http_get(sha_asset["url"], token, accept="application/octet-stream")
    expected = (expected.decode("ascii", "replace").split() or [""])[0].strip().lower()
    if expected != hashlib.sha256(blob).hexdigest():
        raise ValueError(tr("Prüfsumme stimmt nicht – Download beschädigt. Bitte später erneut versuchen."))
    target = new_exe_path()
    try:
        target.write_bytes(blob)
    except PermissionError:
        raise PermissionError(tr("Keine Schreibrechte in {0}. Verschiebe den Manager z. B. in deinen Benutzerordner oder lade das Update von der Release-Seite.", target.parent))
    return target


def download_script_update(info: dict, token: str = "") -> Path:
    """Python-Variante: lädt leeroyzan.py des Releases und prüft Version und Syntax."""
    url = f"https://raw.githubusercontent.com/{UPDATE_REPO}/{urllib.parse.quote(info['tag'])}/{SCRIPT_ASSET}"
    try:
        text = http_get(url, token, accept="text/plain").decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError(tr("Die geladene Datei ist kein gültiges Skript.")) from None
    m = re.search(r'^APP_VERSION = "([^"]+)"', text, re.M)
    if not m or parse_version(m.group(1)) != parse_version(info["tag"]):
        raise ValueError(tr("Die geladene Datei passt nicht zur Release-Version."))
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
    """Startet ein kleines Skript, das nach dem Beenden die alte .exe ersetzt und neu startet.

    Das Skript läuft in einer eigenen, unsichtbaren Konsole (CREATE_NO_WINDOW). Bis 1.3.0 lief es ganz ohne
    Konsole (DETACHED_PROCESS): Dann öffnete jedes Konsolenprogramm darin (tasklist, find, ping) ein eigenes
    Fenster, und „find“ wartete dort auf eine Tastatureingabe, statt die Prozessliste zu lesen – das Update
    blieb hängen. Deshalb jetzt außerdem ohne Pipe (Prozessliste über eine Datei) und mit Zeitlimit."""
    old_exe = Path(sys.executable)
    pid = os.getpid()
    tmp = Path(tempfile.gettempdir())
    bat = tmp / f"leeroyzan_update_{pid}.bat"
    plist = tmp / f"leeroyzan_update_{pid}.txt"
    ulog = config_dir() / "update.log"
    script = f"""@echo off
chcp 65001 >nul
set "PID={pid}"
>>"{ulog}" echo %date% %time% Update gestartet: "{new_exe}" -^> "{old_exe}"
set /a w=0
:wait
tasklist /FI "PID eq %PID%" /NH >"{plist}" 2>nul
find "%PID%" "{plist}" >nul 2>&1 || goto ready
set /a w+=1
if %w% GEQ 60 goto ready
ping -n 2 127.0.0.1 >nul
goto wait
:ready
del "{plist}" >nul 2>&1
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
    # CREATE_NO_WINDOW: eigene, unsichtbare Konsole für das Skript und alles, was es aufruft
    subprocess.Popen(["cmd", "/c", str(bat)], close_fds=True, creationflags=0x08000000)


def cleanup_self_update() -> None:
    """Räumt ein liegengebliebenes .new.exe nach einem abgebrochenen Update weg."""
    if is_frozen():
        new_exe_path().unlink(missing_ok=True)
    else:
        Path(__file__).resolve().with_suffix(".new.py").unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# Leeroyzan Link: Spielsitzungen aus dem Begleit-Addon übernehmen und zusammenfassen
#
# Das Addon schreibt jede Sitzung in seine SavedVariables (LeeroyzanLinkDB.global.sessionLog,
# die neuesten 30). WoW speichert die Datei beim Ausloggen und bei /reload. Leeroyzan liest sie
# nur (schreibt nie hinein) und legt jede Sitzung dauerhaft als JSON in Leeroyzan-Data/sessions ab.
# --------------------------------------------------------------------------- #

LINK_ADDON = "LeeroyzanLink"
LINK_DB = "LeeroyzanLinkDB"
SESSION_FORMAT = 2
YARD_M = 0.9144


class LuaParseError(ValueError):
    pass


_LUA_NUM = re.compile(r"-?(?:0[xX][0-9a-fA-F]+|(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)")
_LUA_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_LUA_ESC = {"n": 10, "t": 9, "r": 13, "a": 7, "b": 8, "f": 12, "v": 11, "\\": 92, '"': 34, "'": 39, "\n": 10}


class _LuaReader:
    """Liest Lua-Tabellen, wie WoW sie in SavedVariables schreibt (nur Daten, kein Code)."""

    def __init__(self, text: str):
        self.s, self.i, self.n = text, 0, len(text)

    def fail(self, what: str):
        line = self.s.count("\n", 0, self.i) + 1
        raise LuaParseError(f"{what} (Zeile {line})")

    def ws(self):
        s, i, n = self.s, self.i, self.n
        while i < n:
            c = s[i]
            if c in " \t\r\n﻿":
                i += 1
            elif s.startswith("--", i):
                if s.startswith("--[[", i):
                    end = s.find("]]", i + 4)
                    i = n if end < 0 else end + 2
                else:
                    end = s.find("\n", i)
                    i = n if end < 0 else end + 1
            else:
                break
        self.i = i

    def peek(self) -> str:
        return self.s[self.i] if self.i < self.n else ""

    def expect(self, c: str):
        self.ws()
        if self.peek() != c:
            self.fail(f"'{c}' erwartet")
        self.i += 1

    def string(self) -> str:
        s, q = self.s, self.s[self.i]
        i = self.i + 1
        out = bytearray()
        start = i
        while True:
            if i >= self.n:
                self.fail("Text ohne Ende")
            c = s[i]
            if c == q:
                out += s[start:i].encode("utf-8")
                self.i = i + 1
                return out.decode("utf-8", "replace")
            if c == "\\":
                out += s[start:i].encode("utf-8")
                e = s[i + 1] if i + 1 < self.n else ""
                if e.isdigit():
                    j = i + 1
                    while j < self.n and j < i + 4 and s[j].isdigit():
                        j += 1
                    out.append(int(s[i + 1:j]) & 0xFF)
                    i = j
                else:
                    out.append(_LUA_ESC.get(e, ord(e) if e and ord(e) < 128 else 63))
                    i += 2
                start = i
            else:
                i += 1

    def value(self):
        self.ws()
        c = self.peek()
        if c == "{":
            return self.table()
        if c in "\"'":
            return self.string()
        m = _LUA_NUM.match(self.s, self.i)
        if m and m.group():
            self.i = m.end()
            t = m.group()
            if t.lower().startswith(("0x", "-0x")):
                return int(t, 16)
            if re.fullmatch(r"-?\d+", t):
                return int(t)
            return float(t)
        m = _LUA_NAME.match(self.s, self.i)
        if m:
            self.i = m.end()
            word = m.group()
            if word in ("true", "false"):
                return word == "true"
            if word == "nil":
                return None
            if word in ("inf", "nan"):
                return 0
        self.fail("Wert erwartet")

    def table(self):
        self.i += 1
        d: dict = {}
        pos = 1
        while True:
            self.ws()
            c = self.peek()
            if c == "}":
                self.i += 1
                break
            if c == "":
                self.fail("Tabelle ohne Ende")
            if c == "[":
                self.i += 1
                k = self.value()
                self.expect("]")
                self.expect("=")
                d[k] = self.value()
            else:
                m = _LUA_NAME.match(self.s, self.i)
                j = m.end() if m else self.i
                while j < self.n and self.s[j] in " \t\r\n":
                    j += 1
                if m and m.group() not in ("true", "false", "nil") and self.s.startswith("=", j) \
                        and not self.s.startswith("==", j):
                    self.i = j + 1
                    d[m.group()] = self.value()
                else:
                    d[pos] = self.value()
                    pos += 1
            self.ws()
            if self.peek() in (",", ";"):
                self.i += 1
        if d and all(isinstance(k, int) for k in d) and sorted(d) == list(range(1, len(d) + 1)):
            return [d[k] for k in range(1, len(d) + 1)]
        return d


def parse_lua_savedvariables(text: str) -> dict:
    """Liefert {Variablenname: Wert} aus einer SavedVariables-Datei."""
    r = _LuaReader(text)
    out = {}
    while True:
        r.ws()
        if r.i >= r.n:
            return out
        m = _LUA_NAME.match(r.s, r.i)
        if not m:
            r.fail("Variablenname erwartet")
        r.i = m.end()
        r.expect("=")
        out[m.group()] = r.value()


# ----- Werte robust lesen ------------------------------------------------- #
def _num(v, default=0):
    if isinstance(v, bool):
        return default
    if isinstance(v, (int, float)):
        return v
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _int(v, default=0) -> int:
    return int(round(_num(v, default)))


def _text(v) -> str:
    return v if isinstance(v, str) else ("" if v is None else str(v))


def _map(v) -> dict:
    if isinstance(v, dict):
        return {str(k): x for k, x in v.items()}
    if isinstance(v, list):
        return {str(i + 1): x for i, x in enumerate(v)}
    return {}


def _list(v) -> list:
    if isinstance(v, list):
        return v
    if isinstance(v, dict):
        try:
            return [v[k] for k in sorted(v, key=lambda k: _num(k))]
        except TypeError:
            return list(v.values())
    return []


def _counts(v) -> dict:
    return {k: _num(x) for k, x in _map(v).items() if _num(x)}


def _items(v) -> dict:
    out = {}
    for k, e in _map(v).items():
        e = _map(e)
        if _int(e.get("n")) <= 0:
            continue
        out[k] = {"name": _text(e.get("name")) or f"#{k}", "q": _int(e.get("q"), 1), "n": _int(e.get("n")),
                  "v": _int(e.get("v")), "p": _int(e.get("p"))}
    return out


def normalize_session(raw, flavor: str = "", account: str = "") -> dict | None:
    """Bringt eine Sitzung aus dem Addon in eine feste Form (JSON-tauglich, alle Felder vorhanden)."""
    r = _map(raw)
    char = _text(r.get("char"))
    start = _int(r.get("start"))
    if not char or start <= 0:
        return None
    g = lambda key: _map(r.get(key))   # noqa: E731
    tm, lv, xp, mo, qu, lo, cb, ki, mv, pr, ex = (g(k) for k in (
        "time", "level", "xp", "money", "quests", "loot", "combat", "kills", "move", "prof", "explore"))
    name, _, realm = char.partition("-")
    s = {
        "format": SESSION_FORMAT,
        "id": _text(r.get("id")) or f"{char}:{start}",
        "char": char, "name": _text(r.get("name")) or name, "realm": _text(r.get("realm")) or realm,
        "class": _text(r.get("class")), "race": _text(r.get("race")), "faction": _text(r.get("faction")),
        "addon": _text(r.get("addon")), "interface": _int(r.get("interface")),
        "flavor": flavor, "account": account,
        "start": start, "stop": max(start, _int(r.get("stop"), start)), "saved": _int(r.get("saved")),
        "played": _int(r.get("played")), "resumes": _int(r.get("resumes")),
        "playedTotal": _int(r.get("playedTotal")),
        "time": {k: _int(tm.get(k)) for k in ("combat", "afk", "rest", "dead", "taxi", "instance")},
        "level": {"from": _int(lv.get("from")), "to": _int(lv.get("to")),
                  "ups": [{"l": _int(_map(u).get("l")), "t": _int(_map(u).get("t"))} for u in _list(lv.get("ups"))]},
        "xp": {k: _int(xp.get(k)) for k in ("total", "kills", "quests", "explore", "other", "killsN")},
        "money": {"start": _int(mo.get("start")), "stop": _int(mo.get("stop")), "income": _int(mo.get("income")),
                  "spent": _int(mo.get("spent")), "gain": _counts(mo.get("gain")), "spend": _counts(mo.get("spend"))},
        "quests": {
            "accepted": [{"id": _int(_map(q).get("id")), "title": _text(_map(q).get("title")),
                          "lvl": _int(_map(q).get("lvl")), "t": _int(_map(q).get("t"))} for q in _list(qu.get("accepted"))],
            "done": [{"id": _int(_map(q).get("id")), "title": _text(_map(q).get("title")),
                      "xp": _int(_map(q).get("xp")), "money": _int(_map(q).get("money")),
                      "t": _int(_map(q).get("t")), "zone": _text(_map(q).get("zone"))} for q in _list(qu.get("done"))],
            "abandoned": [{"title": _text(_map(q).get("title")), "t": _int(_map(q).get("t"))}
                          for q in _list(qu.get("abandoned"))],
        },
        "loot": {"n": _int(lo.get("n")), "value": _int(lo.get("value")),
                 "q": {str(_int(k) if str(k).lstrip("-").isdigit() else k): _int(v) for k, v in _map(lo.get("q")).items()},
                 "items": _items(lo.get("items")),
                 "notable": [{"id": _int(_map(e).get("id")), "name": _text(_map(e).get("name")),
                              "q": _int(_map(e).get("q"), 2), "n": _int(_map(e).get("n"), 1),
                              "t": _int(_map(e).get("t")), "zone": _text(_map(e).get("zone")),
                              "src": _text(_map(e).get("src"))} for e in _list(lo.get("notable"))]},
        "combat": {
            **{k: _int(cb.get(k)) for k in ("dmg", "dmgPet", "hits", "crits", "maxHit", "heal", "over", "maxHeal",
                                             "taken", "maxTaken", "healTaken", "castN", "buffN", "debuffN",
                                             "buffsGiven", "interrupts", "dispels")},
            **{k: _text(cb.get(k)) for k in ("maxHitSpell", "maxHealSpell", "maxTakenBy", "maxTakenSpell", "err")},
            "maxHitCrit": bool(cb.get("maxHitCrit")),
            "spells": {k: [_int(x) for x in (_list(v) + [0, 0, 0])[:3]] for k, v in _map(cb.get("spells")).items()},
            "heals": {k: [_int(x) for x in (_list(v) + [0, 0, 0])[:3]] for k, v in _map(cb.get("heals")).items()},
            "casts": _counts(cb.get("casts")), "buffs": _counts(cb.get("buffs")), "debuffs": _counts(cb.get("debuffs")),
            "takenBy": _counts(cb.get("takenBy")), "env": _counts(cb.get("env")),
            "swings": {k: _int(_map(cb.get("swings")).get(k))
                       for k in ("n", "hit", "crit", "miss", "dodge", "parry", "block", "glance")},
            "swingsTaken": {k: _int(_map(cb.get("swingsTaken")).get(k))
                            for k in ("n", "hit", "crit", "miss", "dodge", "parry", "block")},
        },
        "kills": {**{k: _int(ki.get(k)) for k in ("n", "normal", "elite", "rare", "rareelite", "boss", "players", "blows")},
                  "mobs": {k: {"name": _text(_map(e).get("name")) or "?", "n": _int(_map(e).get("n")),
                               "c": _text(_map(e).get("c")) or "normal"} for k, e in _map(ki.get("mobs")).items()}},
        "bosses": [{"name": _text(_map(b).get("name")), "t": _int(_map(b).get("t")), "inst": _text(_map(b).get("inst")),
                    "sec": _int(_map(b).get("sec"))} for b in _list(r.get("bosses"))],
        "wipes": _int(r.get("wipes")),
        "deaths": [{k: (_int(_map(d).get(k)) if k in ("t", "lvl") else _text(_map(d).get(k)))
                    for k in ("t", "zone", "sub", "by", "spell", "lvl", "inst")} for d in _list(r.get("deaths"))],
        "deathN": _int(r.get("deathN")),
        "move": {k: _num(mv.get(k)) for k in ("foot", "mount", "swim", "taxi", "ghost", "steps", "jumps")},
        "prof": {
            "skills": {k: {"h": _text(_map(e).get("h")), "a": _int(_map(e).get("a")), "b": _int(_map(e).get("b")),
                           "m": _int(_map(e).get("m"))} for k, e in _map(pr.get("skills")).items()},
            "ups": _int(pr.get("ups")),
            "crafted": _items(pr.get("crafted")),
            "gathered": {k: _items(v) for k, v in _map(pr.get("gathered")).items() if _items(v)},
            "learned": [_text(_map(e).get("name")) for e in _list(pr.get("learned")) if _map(e).get("name")],
        },
        "spellsLearned": [_text(_map(e).get("name")) for e in _list(r.get("spellsLearned")) if _map(e).get("name")],
        "explore": {
            "found": [{"name": _text(_map(e).get("name")), "xp": _int(_map(e).get("xp")), "zone": _text(_map(e).get("zone")),
                       "t": _int(_map(e).get("t"))} for e in _list(ex.get("found"))],
            "zones": {k: {"sec": _int(_map(z).get("sec")), "subs": sorted(str(x) for x in _map(_map(z).get("subs")))}
                      for k, z in _map(ex.get("zones")).items()},
        },
        "runs": [{"name": _text(_map(x).get("name")), "kind": _text(_map(x).get("kind")), "diff": _text(_map(x).get("diff")),
                  "size": _int(_map(x).get("size")), "start": _int(_map(x).get("start")), "stop": _int(_map(x).get("stop")),
                  "kills": _int(_map(x).get("kills")), "bosses": [_text(b) for b in _list(_map(x).get("bosses"))],
                  "wipes": _int(_map(x).get("wipes")), "deaths": _int(_map(x).get("deaths")), "xp": _int(_map(x).get("xp")),
                  "money": _int(_map(x).get("money")), "loot": _int(_map(x).get("loot")), "group": _int(_map(x).get("group"))}
                 for x in _list(r.get("runs"))],
        "rep": _counts(r.get("rep")),
        "legacy": False,
    }
    # Spielzeit fehlt (z. B. Absturz vor dem ersten Speichern): Zeitraum nehmen
    if s["played"] <= 0:
        s["played"] = max(0, s["stop"] - s["start"])
    return s


def legacy_session(raw, flavor: str = "", account: str = "") -> dict | None:
    """Sitzungen aus Leeroyzan Link 0.1 (nur Dauer, Gold, XP, Kills)."""
    r = _map(raw)
    s = normalize_session({"char": r.get("char"), "start": r.get("start"),
                           "stop": _int(r.get("start")) + _int(r.get("duration")),
                           "saved": _int(r.get("start")) + _int(r.get("duration")),
                           "played": r.get("duration"), "level": {"from": r.get("level"), "to": r.get("level")},
                           "xp": {"total": r.get("xp")}, "kills": {"n": r.get("kills"), "normal": r.get("kills")}},
                          flavor, account)
    if s:
        s["legacy"] = True
        s["money"]["stop"] = s["money"]["start"] + _int(r.get("money"))
        s["money"]["net"] = _int(r.get("money"))
    return s


def session_net_money(s: dict) -> int:
    m = s.get("money", {})
    if "net" in m:
        return _int(m["net"])
    return _int(m.get("stop")) - _int(m.get("start"))


# ----- Dateien ------------------------------------------------------------ #
def link_savedvariables(root: str, flavor: str) -> list[Path]:
    """Alle SavedVariables-Dateien des Addons in dieser Spielversion (eine pro WoW-Account)."""
    base = Path(root) / flavor / "WTF" / "Account"
    if not root or not flavor or not base.is_dir():
        return []
    return sorted(p for p in base.glob(f"*/SavedVariables/{LINK_ADDON}.lua") if p.is_file())


def link_signature(root: str, flavor: str) -> tuple:
    out = []
    for p in link_savedvariables(root, flavor):
        try:
            st = p.stat()
            out.append((str(p), st.st_size, st.st_mtime_ns))
        except OSError:
            pass
    return tuple(out)


def sessions_dir(flavor: str) -> Path:
    name = re.sub(r"[^A-Za-z0-9]+", "", flavor) or "wow"
    return config_dir() / "sessions" / name


def _safe_name(text: str) -> str:
    return re.sub(r"[^\w.-]+", "_", text, flags=re.UNICODE).strip("_")[:60] or "char"


def session_path(flavor: str, s: dict) -> Path:
    stamp = datetime.fromtimestamp(s["start"]).strftime("%Y%m%d-%H%M%S")
    return sessions_dir(flavor) / f"{stamp}_{_safe_name(s['char'])}.json"


def read_link_db(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    db = parse_lua_savedvariables(text).get(LINK_DB)
    return db if isinstance(db, dict) else {}


def sessions_in_db(db: dict, flavor: str = "", account: str = "") -> list[dict]:
    g = _map(db.get("global"))
    out = []
    for raw in _list(g.get("sessionLog")):
        if _int(_map(raw).get("v")) >= SESSION_FORMAT:
            s = normalize_session(raw, flavor, account)
            if s:
                out.append(s)
    for raw in _list(g.get("sessions")):          # alte Version 0.1
        s = legacy_session(raw, flavor, account)
        if s:
            out.append(s)
    return out


_SESSION_CACHE: dict[str, tuple[int, dict]] = {}     # Pfad -> (Änderungszeit, Sitzung)


def load_sessions(flavor: str) -> list[dict]:
    """Alle gespeicherten Sitzungen dieser Spielversion, neueste zuerst (unveränderte Dateien aus dem Cache)."""
    out = []
    d = sessions_dir(flavor)
    if not d.is_dir():
        return out
    for p in d.glob("*.json"):
        key = str(p)
        try:
            mtime = p.stat().st_mtime_ns
            cached = _SESSION_CACHE.get(key)
            if cached and cached[0] == mtime:
                s = cached[1]
            else:
                s = json.loads(p.read_text(encoding="utf-8"))
                _SESSION_CACHE[key] = (mtime, s)
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(s, dict) and s.get("char") and s.get("start"):
            s["_file"] = key
            out.append(s)
    out.sort(key=lambda s: s.get("start", 0), reverse=True)
    return out


def save_session(flavor: str, s: dict) -> Path:
    p = session_path(flavor, s)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    data = {k: v for k, v in s.items() if not k.startswith("_")}
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)
    return p


def _deleted_file(flavor: str) -> Path:
    return sessions_dir(flavor) / "deleted.txt"


def deleted_session_ids(flavor: str) -> set[str]:
    try:
        return {l.strip() for l in _deleted_file(flavor).read_text(encoding="utf-8").splitlines() if l.strip()}
    except OSError:
        return set()


def delete_session(flavor: str, s: dict) -> None:
    """Entfernt eine Sitzung aus dem Archiv und merkt sich die ID, damit sie nicht wieder importiert wird."""
    p = Path(s.get("_file") or session_path(flavor, s))
    p.unlink(missing_ok=True)
    f = _deleted_file(flavor)
    f.parent.mkdir(parents=True, exist_ok=True)
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(s["id"] + "\n")


def import_link_sessions(root: str, flavor: str) -> dict:
    """Übernimmt neue und fortgesetzte Sitzungen aus allen Accounts. Schreibt nie in WoW-Dateien."""
    result = {"new": [], "updated": [], "errors": [], "files": 0}
    deleted = deleted_session_ids(flavor)
    for path in link_savedvariables(root, flavor):
        account = path.parent.parent.name
        try:
            db = read_link_db(path)
        except (OSError, LuaParseError) as e:
            result["errors"].append(f"{path.name} ({account}): {e}")
            continue
        result["files"] += 1
        for s in sessions_in_db(db, flavor, account):
            if s["id"] in deleted:
                continue
            target = session_path(flavor, s)
            old = None
            if target.is_file():
                try:
                    old = json.loads(target.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    old = None
            if old is not None:
                if s["legacy"] and not old.get("legacy"):
                    continue                      # nie eine volle Sitzung durch die alte Kurzform ersetzen
                if (s["saved"], s["played"]) <= (_int(old.get("saved")), _int(old.get("played"))):
                    continue
            s["imported"] = int(time.time())
            if old is not None and old.get("imported_first"):
                s["imported_first"] = old["imported_first"]
            else:
                s["imported_first"] = s["imported"]
            save_session(flavor, s)
            (result["updated"] if old is not None else result["new"]).append(s)
    return result


# ----- Addon mitliefern und installieren ---------------------------------- #
def bundled_link_dir() -> Path | None:
    """Mitgeliefertes Addon: in der .exe (PyInstaller) oder neben leeroyzan.py."""
    bases = [Path(getattr(sys, "_MEIPASS", "")), app_dir()] if getattr(sys, "frozen", False) else [Path(__file__).resolve().parent]
    for base in bases:
        d = base / LINK_ADDON
        if (d / f"{LINK_ADDON}.toc").is_file():
            return d
    return None


def link_version(folder: Path | None) -> str:
    if not folder:
        return ""
    meta = parse_toc(folder / f"{LINK_ADDON}.toc") if (folder / f"{LINK_ADDON}.toc").is_file() else {}
    return clean_wow_text(meta.get("version", ""))


def installed_link_dir(root: str, flavor: str) -> Path | None:
    for base in (addons_dir(root, flavor), disabled_dir(root, flavor)):
        d = base / LINK_ADDON
        if (d / f"{LINK_ADDON}.toc").is_file():
            return d
    return None


def install_link_addon(root: str, flavor: str) -> str:
    """Kopiert das mitgelieferte Addon in den AddOns-Ordner. Data.lua (schreibt Leeroyzan) bleibt erhalten."""
    src = bundled_link_dir()
    if not src:
        raise RuntimeError(tr("Diese Leeroyzan-Version enthält das Addon nicht."))
    current = installed_link_dir(root, flavor)
    dest = current or (addons_dir(root, flavor) / LINK_ADDON)
    dest.parent.mkdir(parents=True, exist_ok=True)
    keep = None
    if (dest / "Data.lua").is_file():
        keep = (dest / "Data.lua").read_bytes()
    tmp = dest.parent / f".{LINK_ADDON}.new"
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.copytree(src, tmp)
    if keep is not None:
        (tmp / "Data.lua").write_bytes(keep)
    old = dest.parent / f".{LINK_ADDON}.old"
    shutil.rmtree(old, ignore_errors=True)
    if dest.exists():
        dest.rename(old)
    tmp.rename(dest)
    shutil.rmtree(old, ignore_errors=True)
    return link_version(dest)


# ----- Formatieren --------------------------------------------------------- #
def fmt_int(n) -> str:
    v = _int(n)
    out = f"{abs(v):,}".replace(",", "." if LANG == "de" else ",")
    return ("-" if v < 0 else "") + out


def fmt_money(copper, sign: bool = False) -> str:
    c = _int(copper)
    neg = c < 0
    c = abs(c)
    g, rest = divmod(c, 10000)
    sv, k = divmod(rest, 100)
    unit_c = "k" if LANG == "de" else "c"
    parts = [f"{fmt_int(g)}g" if g else "", f"{sv}s" if sv else "", f"{k}{unit_c}" if k else ""]
    out = " ".join(p for p in parts if p) or f"0{unit_c}"
    if neg:
        return "−" + out
    return ("+" + out) if sign and c else out


def fmt_dur(sec) -> str:
    sec = max(0, _int(sec))
    h, rest = divmod(sec, 3600)
    m = rest // 60
    if h:
        return tr("{0} h {1:02d} min", h, m)
    if m:
        return tr("{0} min", m)
    return tr("{0} s", sec)


def fmt_dist(yards) -> str:
    m = _num(yards) * YARD_M
    if m < 1000:
        return f"{fmt_int(m)} m"
    v = f"{m / 1000:.1f}"
    return (v.replace(".", ",") if LANG == "de" else v) + " km"


def plural(n, one: str, many: str) -> str:
    """tr(one) für genau 1, sonst tr(many, n) – one/many sind deutsche Texte mit {0}."""
    return tr(one, fmt_int(n)) if _int(n) == 1 else tr(many, fmt_int(n))


def fmt_pct(part, whole) -> str:
    whole = _num(whole)
    return f"{round(100 * _num(part) / whole)} %" if whole > 0 else "–"


WEEKDAYS = {"de": ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"],
            "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]}


def fmt_day(ts) -> str:
    d = datetime.fromtimestamp(_int(ts))
    return f"{WEEKDAYS.get(LANG, WEEKDAYS['en'])[d.weekday()]}, {d.strftime(tr('%d.%m.%Y'))}"


def fmt_clock(ts) -> str:
    return datetime.fromtimestamp(_int(ts)).strftime("%H:%M")


QUALITY_NAMES = ["Schlecht", "Gewöhnlich", "Ungewöhnlich", "Selten", "Episch", "Legendär", "Artefakt"]
QUALITY_COLORS = ["#9d9d9d", "#f3f0e8", "#1eff00", "#3d9bff", "#c27cff", "#ff8000", "#e6cc80"]
MONEY_SOURCES = {"loot": "Beute", "quest": "Quests", "vendor": "Händler", "mail": "Post", "auction": "Auktionshaus",
                 "trainer": "Lehrer", "taxi": "Flugmeister", "trade": "Handel", "other": "Sonstiges"}
GATHER_KINDS = {"herb": "Kräuterkunde", "mining": "Bergbau", "skinning": "Kürschnerei", "fishing": "Angeln"}
INSTANCE_KINDS = {"party": "Dungeon", "raid": "Schlachtzug", "pvp": "Schlachtfeld", "arena": "Arena",
                  "scenario": "Szenario"}
ENV_KINDS = {"Falling": "Sturz", "Drowning": "Ertrinken", "Fatigue": "Erschöpfung", "Fire": "Feuer",
             "Lava": "Lava", "Slime": "Schleim"}
CLASS_NAMES = {"WARRIOR": "Krieger", "PALADIN": "Paladin", "HUNTER": "Jäger", "ROGUE": "Schurke", "PRIEST": "Priester",
               "SHAMAN": "Schamane", "MAGE": "Magier", "WARLOCK": "Hexenmeister", "DRUID": "Druide",
               "DEATHKNIGHT": "Todesritter", "MONK": "Mönch", "DEMONHUNTER": "Dämonenjäger", "EVOKER": "Rufer"}


def skill_group(header: str) -> str:
    """prof | weapon | other – aus der (deutschen oder englischen) Überschrift im Fertigkeitenfenster."""
    h = header.lower()
    if h == "prof" or "beruf" in h or "profession" in h or "sekund" in h or "secondary" in h or "trade" in h:
        return "prof"
    if "waffe" in h or "weapon" in h:
        return "weapon"
    return "other"


def _top(d: dict, n: int, key=lambda v: v):
    return sorted(d.items(), key=lambda kv: key(kv[1]), reverse=True)[:n]


def session_title(s: dict) -> str:
    return tr("{0} · {1} – {2}", fmt_day(s["start"]), fmt_clock(s["start"]), fmt_clock(s["stop"]))


def session_headline(s: dict) -> str:
    """Kurzfassung für Hinweise: Charakter · Dauer · XP · Gold · Kills."""
    parts = [s.get("name") or s["char"], fmt_dur(s["played"])]
    if s["xp"]["total"]:
        parts.append(tr("{0} XP", fmt_int(s["xp"]["total"])))
    parts.append(fmt_money(session_net_money(s), sign=True))
    if s["kills"]["n"]:
        parts.append(plural(s["kills"]["n"], "{0} Kill", "{0} Kills"))
    if s["quests"]["done"]:
        parts.append(plural(len(s["quests"]["done"]), "{0} Quest", "{0} Quests"))
    return " · ".join(parts)


def _summary_sentence(s: dict) -> str:
    """Die Sitzung in zwei, drei Sätzen."""
    x, k, mv = s["xp"], s["kills"], s["move"]
    progress = []
    if x["total"]:
        progress.append(tr("{0} Erfahrung gesammelt", fmt_int(x["total"])))
    net = session_net_money(s)
    if net > 0:
        progress.append(tr("{0} verdient", fmt_money(net)))
    elif net < 0:
        progress.append(tr("{0} ausgegeben", fmt_money(-net)))
    if s["quests"]["done"]:
        n = len(s["quests"]["done"])
        progress.append(tr("1 Quest abgeschlossen") if n == 1 else tr("{0} Quests abgeschlossen", n))
    if progress:
        out = [tr("In {0} Spielzeit hast du {1}.", fmt_dur(s["played"]), _join_and(progress))]
    else:
        out = [tr("Du warst {0} im Spiel.", fmt_dur(s["played"]))]

    action = []
    if k["n"]:
        special = []
        if k["elite"] + k["rareelite"]:
            special.append(tr("{0} Elite", k["elite"] + k["rareelite"]))
        if k["rare"] + k["rareelite"]:
            special.append(tr("{0} seltene", k["rare"] + k["rareelite"]))
        bosses = max(k["boss"], len(s["bosses"]))
        if bosses:
            special.append(tr("1 Boss") if bosses == 1 else tr("{0} Bosse", bosses))
        action.append(tr("{0} Gegner besiegt", fmt_int(k["n"])) +
                      (tr(" (darunter {0})", _join_and(special)) if special else ""))
    walked = mv["foot"] + mv["ghost"]
    if walked * YARD_M >= 50:
        action.append(tr("{0} zu Fuß zurückgelegt (≈ {1} Schritte)", fmt_dist(walked), fmt_int(mv["steps"])))
    if s["explore"]["found"]:
        n = len(s["explore"]["found"])
        action.append(tr("1 Gebiet entdeckt") if n == 1 else tr("{0} Gebiete entdeckt", n))
    if s["runs"]:
        n = len(s["runs"])
        action.append(tr("1 Instanz besucht") if n == 1 else tr("{0} Instanzen besucht", n))
    if action:
        out.append(tr("Du hast {0}.", _join_and(action)))

    lv = s["level"]
    if lv["to"] > lv["from"] > 0:
        out.append(tr("Du bist von Stufe {0} auf {1} aufgestiegen.", lv["from"], lv["to"]))
    if s["deathN"] == 1:
        out.append(tr("Du bist einmal gestorben."))
    elif s["deathN"] > 1:
        out.append(tr("Du bist {0}-mal gestorben.", s["deathN"]))
    return " ".join(out)


def _join_and(parts: list[str]) -> str:
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + tr(" und ") + parts[-1]


def session_report(s: dict) -> list[tuple[str, str]]:
    """Ausführliche Zusammenfassung einer Sitzung als (Text, Tag)-Teile für ein Text-Feld."""
    P: list[tuple[str, str]] = []
    add = lambda text, tag="": P.append((text, tag))   # noqa: E731

    def head(text):
        add("\n" + text + "\n", "head")

    def line(text, tag=""):
        add(text + "\n", tag)

    def pairs(items, tag=""):
        items = [i for i in items if i]
        if items:
            line(" · ".join(items), tag)

    cls = tr(CLASS_NAMES.get(s["class"], s["class"].title())) if s["class"] else ""
    add(session_title(s) + "\n", "title")
    who = s["name"] or s["char"]
    lv = s["level"]
    level = (tr("Stufe {0}", lv["to"]) if lv["from"] in (0, lv["to"]) else tr("Stufe {0} → {1}", lv["from"], lv["to"]))
    pairs([who, cls, level, s["realm"]])
    if s["legacy"]:
        line(tr("Aufgezeichnet mit Leeroyzan Link 0.1 – nur Dauer, Erfahrung, Gold und Kills."), "dim")
    if s["resumes"]:
        line(tr("Fortgesetzt nach {0}× /reload oder kurzem Ausloggen.", s["resumes"]), "dim")

    head(tr("Zusammenfassung"))
    line(_summary_sentence(s))
    hl = _highlights(s)
    if hl:
        for h in hl:
            add("★ ", "hl")
            line(h)

    head(tr("Zeit"))
    t = s["time"]
    pairs([tr("Spielzeit {0}", fmt_dur(s["played"])), tr("im Kampf {0}", fmt_dur(t["combat"])) if t["combat"] else "",
           tr("AFK {0}", fmt_dur(t["afk"])) if t["afk"] else "", tr("ausgeruht {0}", fmt_dur(t["rest"])) if t["rest"] else "",
           tr("in Instanzen {0}", fmt_dur(t["instance"])) if t["instance"] else "",
           tr("Flugroute {0}", fmt_dur(t["taxi"])) if t["taxi"] else "", tr("tot {0}", fmt_dur(t["dead"])) if t["dead"] else ""])
    if s["playedTotal"]:
        line(tr("Gesamtspielzeit des Charakters: {0}", fmt_dur(s["playedTotal"])), "dim")

    x = s["xp"]
    if x["total"] or lv["ups"]:
        head(tr("Erfahrung"))
        per_h = x["total"] * 3600 / s["played"] if s["played"] >= 300 else 0
        pairs([tr("{0} XP", fmt_int(x["total"])), tr("{0} pro Stunde", fmt_int(per_h)) if per_h else ""])
        pairs([tr("Kills {0} ({1})", fmt_int(x["kills"]), fmt_pct(x["kills"], x["total"])) if x["kills"] else "",
               tr("Quests {0} ({1})", fmt_int(x["quests"]), fmt_pct(x["quests"], x["total"])) if x["quests"] else "",
               tr("Entdecken {0} ({1})", fmt_int(x["explore"]), fmt_pct(x["explore"], x["total"])) if x["explore"] else "",
               tr("Sonstiges {0}", fmt_int(x["other"])) if x["other"] else ""], "dim")
        if lv["ups"]:
            line(tr("Stufenaufstiege: {0}", ", ".join(tr("{0} um {1}", u["l"], fmt_clock(u["t"])) for u in lv["ups"])))

    m = s["money"]
    net = session_net_money(s)
    head(tr("Gold"))
    if s["legacy"]:
        line(tr("netto {0}", fmt_money(net, sign=True)))
    else:
        pairs([tr("{0} → {1}", fmt_money(m["start"]), fmt_money(m["stop"])), tr("netto {0}", fmt_money(net, sign=True))])
        if m["gain"]:
            line(tr("Einnahmen {0}: {1}", fmt_money(m["income"]), ", ".join(
                f"{tr(MONEY_SOURCES.get(k, k))} {fmt_money(v)}" for k, v in _top(m["gain"], 9))), "dim")
        if m["spend"]:
            line(tr("Ausgaben {0}: {1}", fmt_money(m["spent"]), ", ".join(
                f"{tr(MONEY_SOURCES.get(k, k))} {fmt_money(v)}" for k, v in _top(m["spend"], 9))), "dim")

    q = s["quests"]
    if q["done"] or q["accepted"] or q["abandoned"]:
        head(tr("Quests"))
        pairs([tr("{0} abgeschlossen", len(q["done"])), tr("{0} angenommen", len(q["accepted"])),
               tr("{0} abgebrochen", len(q["abandoned"])) if q["abandoned"] else ""])
        for e in q["done"]:
            add("✓ ", "ok")
            reward = [tr("{0} XP", fmt_int(e["xp"])) if e["xp"] else "", fmt_money(e["money"]) if e["money"] else ""]
            reward = [r for r in reward if r]
            line((e["title"] or tr("Quest {0}", e["id"])) + (f"  ({', '.join(reward)})" if reward else ""))
        open_q = [e for e in q["accepted"] if e["id"] not in {d["id"] for d in q["done"]}]
        if open_q:
            line(tr("Neu angenommen: {0}", ", ".join(e["title"] or tr("Quest {0}", e["id"]) for e in open_q)), "dim")
        if q["abandoned"]:
            line(tr("Abgebrochen: {0}", ", ".join(e["title"] for e in q["abandoned"])), "dim")

    lo = s["loot"]
    if lo["n"]:
        head(tr("Beute"))
        pairs([plural(lo["n"], "{0} Gegenstand", "{0} Gegenstände"), tr("Händlerwert {0}", fmt_money(lo["value"])) if lo["value"] else ""])
        by_q = []
        for qi in range(len(QUALITY_NAMES)):
            n = lo["q"].get(str(qi))
            if n:
                by_q.append(f"{fmt_int(n)} {tr(QUALITY_NAMES[qi]).lower()}")
        if by_q:
            line(", ".join(by_q), "dim")
        if lo["notable"]:
            add(tr("Besondere Beute: "))
            for i, e in enumerate(lo["notable"][-12:]):
                if i:
                    add(", ")
                add(f"[{e['name']}]", f"q{min(max(e['q'], 0), 6)}")
                if e["n"] > 1:
                    add(f" ×{e['n']}")
            add("\n")
        common = _top(lo["items"], 8, key=lambda e: e["n"])
        if common:
            add(tr("Am häufigsten: "), "dim")
            for i, (_, e) in enumerate(common):
                if i:
                    add(", ", "dim")
                add(e["name"], f"q{min(max(e['q'], 0), 6)}")
                add(f" ×{fmt_int(e['n'])}", "dim")
            add("\n")

    cb = s["combat"]
    k = s["kills"]
    if k["n"] or cb["dmg"] or cb["taken"] or s["deathN"]:
        head(tr("Gegner"))
        bosses = max(k["boss"], len(s["bosses"]))
        pairs([tr("{0} besiegt", fmt_int(k["n"])), tr("{0} normal", fmt_int(k["normal"])),
               tr("{0} Elite", fmt_int(k["elite"] + k["rareelite"])) if k["elite"] + k["rareelite"] else "",
               tr("{0} selten", fmt_int(k["rare"] + k["rareelite"])) if k["rare"] + k["rareelite"] else "",
               plural(bosses, "{0} Boss", "{0} Bosse") if bosses else "",
               tr("{0} Spieler", k["players"]) if k["players"] else "",
               tr("{0} Todesstöße", fmt_int(k["blows"])) if k["blows"] else ""])
        if s["bosses"]:
            line(tr("Bosse: {0}", ", ".join(b["name"] for b in s["bosses"])))
        mobs = _top(k["mobs"], 10, key=lambda e: e["n"])
        if mobs:
            labels = {"elite": tr("Elite"), "rare": tr("selten"), "rareelite": tr("seltene Elite"), "boss": tr("Boss")}
            line(tr("Am häufigsten: {0}", ", ".join(
                f"{e['name']} ×{e['n']}" + (f" ({labels[e['c']]})" if e["c"] in labels else "") for _, e in mobs)), "dim")

        if not cb["err"] and (cb["dmg"] or cb["taken"] or cb["heal"]):
            head(tr("Kampf"))
            dps = cb["dmg"] / s["time"]["combat"] if s["time"]["combat"] >= 10 else 0
            pairs([tr("Schaden {0}", fmt_int(cb["dmg"])) + (tr(" (davon Begleiter {0})", fmt_int(cb["dmgPet"])) if cb["dmgPet"] else ""),
                   tr("{0} DPS im Kampf", fmt_int(dps)) if dps else "",
                   tr("Krit-Rate {0}", fmt_pct(cb["crits"], cb["hits"])) if cb["hits"] else ""])
            if cb["maxHit"]:
                line(tr("Größter Treffer: {0} mit {1}{2}", fmt_int(cb["maxHit"]), cb["maxHitSpell"] or "?",
                        tr(" (kritisch)") if cb["maxHitCrit"] else ""), "dim")
            if cb["heal"] or cb["over"]:
                pairs([tr("Heilung {0}", fmt_int(cb["heal"])), tr("Überheilung {0}", fmt_int(cb["over"])) if cb["over"] else "",
                       tr("größte Heilung {0} ({1})", fmt_int(cb["maxHeal"]), cb["maxHealSpell"]) if cb["maxHeal"] else ""])
            pairs([tr("erlittener Schaden {0}", fmt_int(cb["taken"])), tr("erhaltene Heilung {0}", fmt_int(cb["healTaken"])) if cb["healTaken"] else "",
                   tr("härtester Treffer {0} von {1}", fmt_int(cb["maxTaken"]), cb["maxTakenBy"] or tr(ENV_KINDS.get(cb["maxTakenSpell"], cb["maxTakenSpell"]) or "?"))
                   if cb["maxTaken"] else ""])
            if cb["env"]:
                line(tr("Umgebung: {0}", ", ".join(f"{tr(ENV_KINDS.get(kk, kk))} {fmt_int(v)}" for kk, v in _top(cb["env"], 6))), "dim")
            sw, st = cb["swings"], cb["swingsTaken"]
            if sw["n"]:
                pairs([tr("{0} Hiebe", fmt_int(sw["n"])), tr("{0} Treffer", fmt_int(sw["hit"])), tr("{0} kritisch", fmt_int(sw["crit"])),
                       tr("{0} verfehlt", fmt_int(sw["miss"])) if sw["miss"] else "", tr("{0} ausgewichen", fmt_int(sw["dodge"])) if sw["dodge"] else "",
                       tr("{0} pariert", fmt_int(sw["parry"])) if sw["parry"] else "", tr("{0} geblockt", fmt_int(sw["block"])) if sw["block"] else "",
                       tr("{0} gestreift", fmt_int(sw["glance"])) if sw["glance"] else ""])
            if st["n"]:
                avoided = st["miss"] + st["dodge"] + st["parry"]
                pairs([tr("{0} Hiebe eingesteckt", fmt_int(st["n"])), tr("{0} abgewehrt ({1})", fmt_int(avoided), fmt_pct(avoided, st["n"])) if avoided else "",
                       tr("{0} kritisch", fmt_int(st["crit"])) if st["crit"] else ""], )
            top_dmg = _top(cb["spells"], 6, key=lambda v: v[0])
            if top_dmg:
                line(tr("Schaden nach Fähigkeit: {0}", ", ".join(
                    f"{name} {fmt_int(v[0])} ({fmt_pct(v[0], cb['dmg'])})" for name, v in top_dmg)), "dim")
            if cb["castN"]:
                line(plural(cb["castN"], "1 Zauber gewirkt", "{0} Zauber gewirkt") + ": " + ", ".join(
                    f"{name} ×{fmt_int(n)}" for name, n in _top(cb["casts"], 8)), "dim")
            if cb["buffN"]:
                line(plural(cb["buffN"], "{0} Buff erhalten", "{0} Buffs erhalten") + ": " + ", ".join(
                    f"{name} ×{fmt_int(n)}" for name, n in _top(cb["buffs"], 8)), "dim")
            if cb["debuffN"]:
                line(plural(cb["debuffN"], "{0} Schwächung erlitten", "{0} Schwächungen erlitten") + ": " + ", ".join(
                    f"{name} ×{fmt_int(n)}" for name, n in _top(cb["debuffs"], 6)), "dim")
            pairs([tr("{0} Buffs auf andere", fmt_int(cb["buffsGiven"])) if cb["buffsGiven"] else "",
                   tr("{0} Unterbrechungen", cb["interrupts"]) if cb["interrupts"] else "",
                   tr("{0} Bannungen", cb["dispels"]) if cb["dispels"] else ""], )
        elif cb["err"]:
            line(tr("Kampfdaten waren im Spiel nicht verfügbar ({0}).", cb["err"]), "dim")
        if s["deathN"]:
            line(tr("Tode: {0}", s["deathN"]), "warn")
            for d in s["deaths"]:
                where = d["inst"] or d["zone"] or "?"
                if d["sub"] and not d["inst"]:
                    where += f" ({d['sub']})"
                cause = d["by"] or tr(ENV_KINDS.get(d["spell"], d["spell"] or "")) or tr("unbekannt")
                if d["by"] and d["spell"]:
                    cause += f" – {d['spell']}"
                line(tr("✝ {0}  {1}, durch {2}", fmt_clock(d["t"]), where, cause), "dim")

    mv = s["move"]
    if any(mv[kk] for kk in ("foot", "mount", "swim", "taxi", "ghost", "jumps")):
        head(tr("Unterwegs"))
        walked = mv["foot"] + mv["ghost"]
        pairs([tr("{0} zu Fuß", fmt_dist(walked)) + (tr(" (≈ {0} Schritte)", fmt_int(mv["steps"])) if mv["steps"] else "") if walked else "",
               tr("{0} beritten", fmt_dist(mv["mount"])) if mv["mount"] else "",
               tr("{0} geschwommen", fmt_dist(mv["swim"])) if mv["swim"] else "",
               tr("{0} Flugroute", fmt_dist(mv["taxi"])) if mv["taxi"] else "",
               tr("{0} Sprünge", fmt_int(mv["jumps"])) if mv["jumps"] else ""])
        if mv["ghost"]:
            line(tr("Davon als Geist: {0}", fmt_dist(mv["ghost"])), "dim")

    pr = s["prof"]
    profs = [(n, e) for n, e in pr["skills"].items() if skill_group(e["h"]) == "prof"]
    raised = [(n, e) for n, e in pr["skills"].items() if e["b"] > e["a"]]
    if profs or raised or pr["crafted"] or pr["gathered"] or pr["learned"]:
        head(tr("Berufe und Fertigkeiten"))
        for n, e in sorted(raised, key=lambda kv: kv[1]["b"] - kv[1]["a"], reverse=True):
            add(f"{n} {e['a']} → {e['b']}", "")
            add(f"  (+{e['b'] - e['a']}{'/' + str(e['m']) if e['m'] else ''})\n", "ok")
        rest = [f"{n} {e['b']}/{e['m']}" for n, e in sorted(profs) if e["b"] <= e["a"]]
        if rest:
            line(tr("Unverändert: {0}", ", ".join(rest)), "dim")
        if pr["crafted"]:
            line(tr("Hergestellt: {0}", ", ".join(f"{e['name']} ×{fmt_int(e['n'])}" for _, e in _top(pr["crafted"], 10, key=lambda e: e["n"]))))
        for kind, items in pr["gathered"].items():
            line(tr("{0}: {1}", tr(GATHER_KINDS.get(kind, kind)),
                    ", ".join(f"{e['name']} ×{fmt_int(e['n'])}" for _, e in _top(items, 10, key=lambda e: e["n"]))))
        if pr["learned"]:
            line(tr("Neue Rezepte: {0}", ", ".join(pr["learned"])), "dim")
    if s["spellsLearned"]:
        line(tr("Neue Zauber und Fähigkeiten: {0}", ", ".join(s["spellsLearned"])), "dim")

    ex = s["explore"]
    if ex["found"] or ex["zones"]:
        head(tr("Entdeckt und bereist"))
        if ex["found"]:
            xp_found = sum(e["xp"] for e in ex["found"])
            line(plural(len(ex["found"]), "{0} neues Gebiet", "{0} neue Gebiete") + (tr(" (+{0} XP)", fmt_int(xp_found)) if xp_found else "") + ": "
                 + ", ".join(e["name"] for e in ex["found"]))
        zones = _top(ex["zones"], 10, key=lambda z: z["sec"])
        if zones:
            line(tr("Zonen: {0}", ", ".join(f"{name} {fmt_dur(z['sec'])}" for name, z in zones if z["sec"] >= 30) or "–"), "dim")

    if s["runs"]:
        head(tr("Dungeons und Schlachtzüge"))
        for r in s["runs"]:
            kind = tr(INSTANCE_KINDS.get(r["kind"], r["kind"] or "Instanz"))
            dur = fmt_dur((r["stop"] or s["stop"]) - r["start"]) if r["start"] else ""
            details = [kind + (f", {r['diff']}" if r["diff"] else "") + (tr(", {0} Spieler", r["group"]) if r["group"] > 1 else ""),
                       f"{fmt_clock(r['start'])}–{fmt_clock(r['stop'] or s['stop'])}" if r["start"] else "", dur]
            add(r["name"] or "?", "hl")
            line("  " + " · ".join(d for d in details if d), "dim")
            pairs([plural(r["kills"], "{0} Kill", "{0} Kills"), tr("Bosse: {0}", ", ".join(r["bosses"])) if r["bosses"] else "",
                   plural(r["wipes"], "{0} Wipe", "{0} Wipes") if r["wipes"] else "",
                   plural(r["deaths"], "{0} Tod", "{0} Tode") if r["deaths"] else "",
                   tr("{0} XP", fmt_int(r["xp"])) if r["xp"] else "",
                   plural(r["loot"], "{0} Gegenstand", "{0} Gegenstände") if r["loot"] else ""])

    if s["rep"]:
        head(tr("Ruf"))
        line(", ".join(f"{name} {'+' if v > 0 else ''}{fmt_int(v)}" for name, v in _top(s["rep"], 12, key=abs)))
    return P


def _highlights(s: dict) -> list[str]:
    out = []
    cb, k, lo = s["combat"], s["kills"], s["loot"]
    if lo["notable"]:
        best = max(lo["notable"], key=lambda e: (e["q"], e["t"]))
        if best["q"] >= 3:
            out.append(tr("Beste Beute: [{0}] ({1})", best["name"], tr(QUALITY_NAMES[min(best["q"], 6)])))
    if s["bosses"]:
        out.append(tr("Boss besiegt: {0}", ", ".join(dict.fromkeys(b["name"] for b in s["bosses"]))))
    if s["level"]["ups"]:
        out.append(tr("Stufe {0} erreicht", s["level"]["ups"][-1]["l"]))
    if cb["maxHit"] >= 100:
        out.append(tr("Größter Treffer {0} ({1})", fmt_int(cb["maxHit"]), cb["maxHitSpell"] or "?"))
    if k["mobs"]:
        name, e = max(k["mobs"].items(), key=lambda kv: kv[1]["n"])
        if e["n"] >= 5:
            out.append(tr("Lieblingsgegner: {0} ×{1}", e["name"], e["n"]))
    return out[:4]


# ----- Mehrere Sitzungen zusammenfassen ----------------------------------- #
def aggregate_sessions(sessions: list[dict]) -> dict:
    tot = {"n": len(sessions), "played": 0, "xp": 0, "net": 0, "income": 0, "spent": 0, "quests": 0, "kills": 0,
           "elite": 0, "rare": 0, "boss": 0, "blows": 0, "deaths": 0, "dmg": 0, "heal": 0, "taken": 0, "loot": 0,
           "value": 0, "walked": 0, "mount": 0, "swim": 0, "taxi": 0, "steps": 0, "jumps": 0, "found": 0, "runs": 0,
           "levels": 0, "skillups": 0, "combat": 0, "afk": 0, "first": 0, "last": 0,
           "chars": {}, "mobs": {}, "zones": {}, "items": {}, "bosses": {}, "days": {}}
    for s in sessions:
        k, cb, mv, lo = s["kills"], s["combat"], s["move"], s["loot"]
        tot["played"] += s["played"]
        tot["xp"] += s["xp"]["total"]
        net = session_net_money(s)
        tot["net"] += net
        tot["income"] += s["money"]["income"]
        tot["spent"] += s["money"]["spent"]
        tot["quests"] += len(s["quests"]["done"])
        tot["kills"] += k["n"]
        tot["elite"] += k["elite"] + k["rareelite"]
        tot["rare"] += k["rare"] + k["rareelite"]
        tot["boss"] += max(k["boss"], len(s["bosses"]))
        tot["blows"] += k["blows"]
        tot["deaths"] += s["deathN"]
        tot["dmg"] += cb["dmg"]
        tot["heal"] += cb["heal"]
        tot["taken"] += cb["taken"]
        tot["loot"] += lo["n"]
        tot["value"] += lo["value"]
        tot["walked"] += mv["foot"] + mv["ghost"]
        tot["mount"] += mv["mount"]
        tot["swim"] += mv["swim"]
        tot["taxi"] += mv["taxi"]
        tot["steps"] += mv["steps"]
        tot["jumps"] += mv["jumps"]
        tot["found"] += len(s["explore"]["found"])
        tot["runs"] += len(s["runs"])
        tot["levels"] += max(0, s["level"]["to"] - s["level"]["from"]) if s["level"]["from"] else 0
        tot["skillups"] += sum(max(0, e["b"] - e["a"]) for e in s["prof"]["skills"].values())
        tot["combat"] += s["time"]["combat"]
        tot["afk"] += s["time"]["afk"]
        tot["first"] = min(tot["first"] or s["start"], s["start"])
        tot["last"] = max(tot["last"], s["stop"])
        c = tot["chars"].setdefault(s["char"], {"n": 0, "played": 0, "xp": 0, "net": 0, "kills": 0, "level": 0,
                                                "class": s["class"], "name": s["name"] or s["char"]})
        c["n"] += 1
        c["played"] += s["played"]
        c["xp"] += s["xp"]["total"]
        c["net"] += net
        c["kills"] += k["n"]
        c["level"] = max(c["level"], s["level"]["to"])
        for key, e in k["mobs"].items():
            m = tot["mobs"].setdefault(e["name"], 0)
            tot["mobs"][e["name"]] = m + e["n"]
        for name, z in s["explore"]["zones"].items():
            tot["zones"][name] = tot["zones"].get(name, 0) + z["sec"]
        for key, e in lo["items"].items():
            it = tot["items"].setdefault(key, {"name": e["name"], "q": e["q"], "n": 0})
            it["n"] += e["n"]
        for b in s["bosses"]:
            tot["bosses"][b["name"]] = tot["bosses"].get(b["name"], 0) + 1
        day = datetime.fromtimestamp(s["start"]).strftime("%Y-%m-%d")
        tot["days"][day] = tot["days"].get(day, 0) + s["played"]
    return tot


def overview_report(sessions: list[dict], label: str) -> list[tuple[str, str]]:
    P: list[tuple[str, str]] = []
    add = lambda text, tag="": P.append((text, tag))   # noqa: E731

    def head(text):
        add("\n" + text + "\n", "head")

    def line(text, tag=""):
        add(text + "\n", tag)

    def pairs(items, tag=""):
        items = [i for i in items if i]
        if items:
            line(" · ".join(items), tag)

    a = aggregate_sessions(sessions)
    add(tr("Gesamt – {0}", label) + "\n", "title")
    if not sessions:
        line(tr("Noch keine Sitzungen."), "dim")
        return P
    pairs([plural(a["n"], "{0} Sitzung", "{0} Sitzungen"), tr("{0} bis {1}", datetime.fromtimestamp(a["first"]).strftime(tr("%d.%m.%Y")),
                                              datetime.fromtimestamp(a["last"]).strftime(tr("%d.%m.%Y"))),
           plural(len(a["days"]), "{0} Spieltag", "{0} Spieltage")], "dim")

    head(tr("Zeit"))
    avg = a["played"] / a["n"]
    pairs([tr("Spielzeit {0}", fmt_dur(a["played"])), tr("Ø {0} pro Sitzung", fmt_dur(avg)),
           tr("im Kampf {0}", fmt_dur(a["combat"])) if a["combat"] else "", tr("AFK {0}", fmt_dur(a["afk"])) if a["afk"] else ""])
    if a["days"]:
        best_day, sec = max(a["days"].items(), key=lambda kv: kv[1])
        line(tr("Längster Spieltag: {0} mit {1}", fmt_day(datetime.strptime(best_day, "%Y-%m-%d").timestamp()), fmt_dur(sec)), "dim")

    head(tr("Fortschritt"))
    hours = a["played"] / 3600 if a["played"] >= 600 else 0
    pairs([tr("{0} XP", fmt_int(a["xp"])) + (tr(" ({0}/h)", fmt_int(a["xp"] / hours)) if hours else ""),
           plural(a["levels"], "{0} Stufe", "{0} Stufen") if a["levels"] else "",
           plural(a["quests"], "{0} Quest", "{0} Quests"),
           tr("{0} Fertigkeitspunkte", fmt_int(a["skillups"])) if a["skillups"] else "",
           plural(a["found"], "{0} neues Gebiet", "{0} neue Gebiete") if a["found"] else ""])
    pairs([tr("Gold netto {0}", fmt_money(a["net"], sign=True)) + (tr(" ({0}/h)", fmt_money(a["net"] / hours)) if hours and a["net"] > 0 else ""),
           tr("eingenommen {0}", fmt_money(a["income"])) if a["income"] else "", tr("ausgegeben {0}", fmt_money(a["spent"])) if a["spent"] else ""])

    head(tr("Kampf"))
    pairs([tr("{0} besiegt", fmt_int(a["kills"])), tr("{0} Elite", fmt_int(a["elite"])) if a["elite"] else "",
           tr("{0} selten", a["rare"]) if a["rare"] else "",
           plural(a["boss"], "{0} Boss", "{0} Bosse") if a["boss"] else "",
           plural(a["deaths"], "{0} Tod", "{0} Tode")])
    pairs([tr("Schaden {0}", fmt_int(a["dmg"])) if a["dmg"] else "", tr("Heilung {0}", fmt_int(a["heal"])) if a["heal"] else "",
           tr("erlitten {0}", fmt_int(a["taken"])) if a["taken"] else "",
           plural(a["runs"], "{0} Instanz", "{0} Instanzen") if a["runs"] else ""], "dim")
    if a["mobs"]:
        line(tr("Am häufigsten: {0}", ", ".join(f"{n} ×{fmt_int(c)}" for n, c in _top(a["mobs"], 8))), "dim")
    if a["bosses"]:
        line(tr("Bosse: {0}", ", ".join(f"{n} ×{c}" if c > 1 else n for n, c in _top(a["bosses"], 12))), "dim")

    head(tr("Beute und Wege"))
    pairs([tr("{0} Gegenstände", fmt_int(a["loot"])), tr("Händlerwert {0}", fmt_money(a["value"])) if a["value"] else ""])
    pairs([tr("{0} zu Fuß (≈ {1} Schritte)", fmt_dist(a["walked"]), fmt_int(a["steps"])) if a["walked"] else "",
           tr("{0} beritten", fmt_dist(a["mount"])) if a["mount"] else "", tr("{0} geschwommen", fmt_dist(a["swim"])) if a["swim"] else "",
           tr("{0} Flugroute", fmt_dist(a["taxi"])) if a["taxi"] else "", tr("{0} Sprünge", fmt_int(a["jumps"])) if a["jumps"] else ""])
    if a["zones"]:
        line(tr("Zonen: {0}", ", ".join(f"{n} {fmt_dur(sec)}" for n, sec in _top(a["zones"], 8))), "dim")

    if len(a["chars"]) > 1 or sessions:
        head(tr("Charaktere"))
        for char, c in sorted(a["chars"].items(), key=lambda kv: kv[1]["played"], reverse=True):
            cls = tr(CLASS_NAMES.get(c["class"], c["class"].title())) if c["class"] else ""
            add(c["name"], "hl")
            line("  " + " · ".join(x for x in (cls, tr("Stufe {0}", c["level"]) if c["level"] else "",
                                               plural(c["n"], "{0} Sitzung", "{0} Sitzungen"), fmt_dur(c["played"]),
                                               tr("{0} XP", fmt_int(c["xp"])), fmt_money(c["net"], sign=True),
                                               tr("{0} Kills", fmt_int(c["kills"]))) if x), "dim")
    return P


def report_plain_text(parts: list[tuple[str, str]]) -> str:
    return "".join(t for t, _ in parts).strip() + "\n"


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
    style.configure("Wrap.TCheckbutton", wraplength=330, justify="left")   # lange Texte umbrechen
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
        self._restore_geometry()
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
        if self.cfg.get("language") not in LANGUAGES:
            self.cfg["language"] = detect_language()
            save_config(self.cfg)
        set_language(self.cfg["language"])

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
        self.after(10000, self._watch_sessions)
        self.after(900, self._report_self_update)
        if self.cfg.get("self_update_mode") != "off":
            self.after(1500, lambda: self.check_manager_update(silent=True))
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        for seq, fn in (("<Control-f>", self._key_search), ("<Control-F>", self._key_search),
                        ("<F5>", self._key_refresh), ("<Delete>", self._key_delete)):
            self.bind(seq, fn)
        log.info("Gestartet: %s %s, Ordner=%s, Version=%s, Daten=%s", APP_NAME, APP_VERSION,
                 self.root_path, self.flavor, config_dir())

    # ----- Fenster und Tastatur ---------------------------------------------- #
    def _restore_geometry(self):
        """Letzte Fenstergröße und -position – nur, wenn das Fenster damit sichtbar bleibt."""
        m = re.fullmatch(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", str(self.cfg.get("window") or ""))
        if m:
            w, h, x, y = (int(v) for v in m.groups())
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
            w, h = max(1080, min(w, sw)), max(600, min(h, sh))
            if not (-50 <= x <= sw - 200 and 0 <= y <= sh - 120):
                x, y = max(0, (sw - w) // 2), max(0, (sh - h) // 3)
            self.geometry(f"{w}x{h}+{x}+{y}")
        if self.cfg.get("window_zoomed"):
            try:
                self.state("zoomed")
            except tk.TclError:
                pass

    def on_close(self):
        try:
            zoomed = self.state() == "zoomed"
            self.cfg["window_zoomed"] = zoomed
            if not zoomed:
                self.cfg["window"] = self.geometry()
            save_config(self.cfg)
        except tk.TclError:
            pass
        self.destroy()

    def _typing(self) -> bool:
        try:
            w = self.focus_get()
        except (KeyError, tk.TclError):
            return False
        return isinstance(w, (tk.Entry, ttk.Entry, tk.Text))

    def _key_search(self, event=None):
        """Strg+F: Suchfeld der aktuellen Seite (auf „Entdecken“ die CurseForge-Suche)."""
        if self.current_page != "discover":
            self.show_page("addons")
        entry = getattr(self, "cf_entry" if self.current_page == "discover" else "filter_entry", None)
        if entry is not None and entry.winfo_exists():
            entry.focus_set()
            entry.select_range(0, "end")
        return "break"

    def _key_refresh(self, event=None):
        """F5: auf „Updates“ nach Updates suchen, sonst die Addon-Liste neu einlesen."""
        if self.asking or self.busy:
            return "break"
        if self.current_page == "updates":
            self.check_updates()
        elif self.current_page == "sessions":
            self.import_sessions(manual=True)
        else:
            self.refresh()
        return "break"

    def _key_delete(self, event=None):
        """Entf: gewählte Addons entfernen (mit Rückfrage) – nicht beim Tippen in einem Feld."""
        if self.current_page == "addons" and not self._typing() and not self.asking:
            self.remove_selected()
            return "break"
        return None

    # ----- Aufbau ---------------------------------------------------------- #
    PAGES = [("addons", "Meine Addons"), ("discover", "Entdecken"), ("updates", "Updates"),
             ("sessions", "Sitzungen"), ("share", "Teilen"), ("sets", "Sets & Backups"),
             ("settings", "Einstellungen")]
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
        self.self_update_var = tk.StringVar(value=tr(self.UPDATE_MODES.get(self.cfg.get("self_update_mode"),
                                                                            self.UPDATE_MODES["auto"])))
        self.count_var = tk.StringVar()
        self.upd_count_var = tk.StringVar()
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *a: self.render())
        self.group_var = tk.BooleanVar(value=bool(self.cfg.get("group_addons", True)))
        self.status_var = tk.StringVar(value=tr("Bereit."))
        self.game_info_var = tk.StringVar()
        self.view = self.cfg.get("addon_view", "list") if self.cfg.get("addon_view") in ("list", "cards") else "list"
        # Entdecken
        self.cf_link_var = tk.StringVar()
        self.gh_var = tk.StringVar()
        self.zip_var = tk.StringVar()
        self.cf_q = tk.StringVar()
        self.cf_sort = tk.StringVar(value=tr("Beliebtheit"))
        self.cf_fit = tk.BooleanVar(value=True)
        self.cf_count = tk.StringVar()
        self.cf_file_var = tk.StringVar()
        # Teilen
        self.share_scope = tk.StringVar(value="all")
        self.share_exact = tk.BooleanVar(value=True)
        self.share_sets = tk.BooleanVar(value=True)
        self.share_size = tk.StringVar(value=tr(next(iter(LIST_PART_SIZES))))
        self.share_info = tk.StringVar()
        self.import_preview = tk.StringVar()
        self.recv_file_var = tk.StringVar()
        # Sitzungen
        self.sess_char_var = tk.StringVar()
        self.sess_period_var = tk.StringVar()
        self.sess_period = "all"
        self.link_state_var = tk.StringVar()
        self.sessions_all: list[dict] = []
        self.sessions_shown: list[dict] = []
        self._sess_parts: list = []
        self._sess_rows: list[dict] = []
        self._sess_sig = None
        self._sess_pending: dict[str, dict] = {}
        self._sess_importing = False
        self._link_hint_shown = False
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
        import tkinter.font as tkfont
        lf = tkfont.Font(root=self, font=self.fonts["label"])
        tagline = next((x for x in (spaced(tr("Addon Manager")), tr("Addon Manager").upper(), spaced("Addon Manager"))
                        if lf.measure(x) <= 144), "ADDON MANAGER")
        brand.create_text(64, 20, anchor="w", fill=t["gold"], font=self.fonts["label"], text=tagline)
        brand.create_text(63, 44, anchor="w", fill=t["gold"], font=self.fonts["side"], text="LEEROYZAN")
        ttk.Label(side, text=spaced(tr("Spielversion")), style="SideKicker.TLabel").pack(anchor="w", pady=(18, 6))
        self._w_flavor(side, width=22).pack(fill="x")
        nav = ttk.Frame(side, style="Side.TFrame")
        nav.pack(fill="x", pady=(18, 0))
        for key, label in self.PAGES:
            b = ttk.Button(nav, text=tr(label), style="Nav.TButton", cursor="hand2",
                           command=lambda k=key: self.show_page(k))
            b.pack(fill="x", pady=1)
            self.nav_buttons[key] = b
        bottom = ttk.Frame(side, style="Side.TFrame")
        bottom.pack(side="bottom", fill="x")
        tk.Frame(bottom, height=1, bg=t["line"]).pack(fill="x", pady=(0, 12))
        self._w_play(bottom, style="BigPlay.TButton", text=tr("▶   SPIELEN")).pack(fill="x")
        ttk.Label(bottom, textvariable=self.game_info_var, style="Side.TLabel", anchor="center",
                  justify="center", wraplength=200).pack(fill="x", pady=(8, 0))
        ttk.Label(bottom, text=tr("Leeroyzan v{0}", APP_VERSION), style="Side.TLabel",
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
        self._page_sessions()
        self._page_share()
        self._page_sets()
        self._page_settings()
        self._fit_wraps(self.page_host)
        self.show_page(self.cfg.get("last_page", "addons"))

    def _fit_wraps(self, widget):
        """Längere Fließtexte an die tatsächliche Kastenbreite anpassen – sonst werden lange
        Übersetzungen am rechten Rand abgeschnitten."""
        for child in widget.winfo_children():
            if isinstance(child, ttk.Label):
                try:
                    wrap = int(str(child.cget("wraplength")) or 0)
                except (ValueError, tk.TclError):
                    wrap = 0
                if wrap >= 300:
                    child.master.bind("<Configure>", lambda e, lbl=child: lbl.winfo_exists() and lbl.configure(
                        wraplength=max(200, e.width - 40)), add="+")
            self._fit_wraps(child)

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
        self.set_status(tr("Fehler: {0}", text.split("\n")[0]))
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

    def ask(self, text: str, yes: str | None = None, no: str | None = None, **kw) -> bool:
        return bool(self._wait_inline(text, "ask", [(yes or tr("Ja"), True), (no or tr("Nein"), False)],
                                      default=False))

    def ask_text(self, text: str, initial: str = "", ok: str = "OK") -> str | None:
        var = tk.StringVar(value=initial)
        holder = {}

        def extra(body, bg):
            e = ttk.Entry(body, textvariable=var)
            e.pack(fill="x", pady=(8, 0))
            holder["e"] = e
            self.after(50, lambda: (e.focus_set(), e.icursor("end")))
        res = self._wait_inline(text, "ask", [(ok, lambda: var.get().strip() or None), (tr("Abbrechen"), None)],
                                extra=extra)
        return res or None

    def ask_choice(self, text: str, options: list[str], default: int = 0, ok: str | None = None) -> int | None:
        ok = ok or tr("Auswählen")
        var = tk.StringVar(value=options[default] if options else "")

        def extra(body, bg):
            cb = ttk.Combobox(body, textvariable=var, values=options, state="readonly")
            cb.pack(fill="x", pady=(8, 0))
        res = self._wait_inline(text, "ask", [(ok, lambda: options.index(var.get()) if var.get() in options else None),
                                              (tr("Abbrechen"), None)], extra=extra)
        return res

    # ----- Gemeinsame Bausteine ------------------------------------------- #
    def _w_path(self, parent, frame_style="Panel.TFrame", label_style="Panel.TLabel", with_version=False):
        row = ttk.Frame(parent, style=frame_style)
        ttk.Label(row, text=tr("WoW-Ordner"), style=label_style).pack(side="left", padx=(0, 8))
        entry = ttk.Entry(row, textvariable=self.root_var, width=50)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda e: self._load_flavors())
        entry.bind("<FocusOut>", lambda e: self._load_flavors()
                   if self.root_path != self.cfg.get("wow_root") else None)
        ttk.Button(row, text=tr("Durchsuchen…"), command=self.browse_root).pack(side="left", padx=(8, 0))
        return row

    def _w_flavor(self, parent, width=24):
        cb = ttk.Combobox(parent, textvariable=self.flavor_var, state="readonly", width=width)
        cb.bind("<<ComboboxSelected>>", lambda e: self.on_flavor_change())
        self.flavor_cb = cb
        return cb

    def _w_check(self, parent, text, var, panel=True):
        return ttk.Checkbutton(parent, text=text, variable=var, command=self.save_options,
                               style="Wrap.TCheckbutton" if panel else "Plain.TCheckbutton")

    def _w_update_mode(self, parent, label_style="Panel.TLabel"):
        row = ttk.Frame(parent, style="Panel.TFrame" if label_style == "Panel.TLabel" else "TFrame")
        ttk.Label(row, text=tr("Leeroyzan-Updates"), style=label_style).pack(side="left", padx=(0, 8))
        cb = ttk.Combobox(row, textvariable=self.self_update_var, state="readonly", width=21,
                          values=[tr(v) for v in self.UPDATE_MODES.values()])
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda e: self.save_options())
        return row

    def _w_filter(self, parent, width=24):
        return ttk.Entry(parent, textvariable=self.filter_var, width=width)

    def _w_play(self, parent, style="Play.TButton", text=None):
        text = text or tr("▶  Spielen")
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
        import tkinter.font as tkfont
        hf = tkfont.Font(root=self, font=ttk.Style(self).lookup("Treeview.Heading", "font") or "TkHeadingFont")
        for (cid, label), w in zip(columns, widths):
            head = spaced(label) if label else ""
            if head and hf.measure(head) > w - 14:      # zu breit: ohne Sperrung
                head = label.upper()
            tv.heading(cid, text=head, anchor="w")
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
            self.tree.heading(c, text=spaced(tr(self.HEADINGS[c])), anchor="w",
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
        text = tr("Spielversion {0}  ·  Interface {1}", self.game[0], self.game[1]) if self.game else tr("Spielversion unbekannt")
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
        elif key == "sessions":
            self.refresh_sessions_page()
            if not self._sess_importing and self.root_path and self.flavor \
                    and link_signature(self.root_path, self.flavor) != self._sess_sig:
                self.import_sessions()

    # --- Meine Addons --------------------------------------------------------- #
    def _page_addons(self):
        t = THEME
        page, head = self._page("addons", tr("Addons & UI"), tr("Meine Addons"))
        right = ttk.Frame(head)
        right.pack(side="right", anchor="s")
        ttk.Button(right, text=tr("+  Addons hinzufügen"), style="Primary.TButton",
                   command=lambda: self.show_page("discover")).pack(side="right")
        self.view_btns = {}
        seg = ttk.Frame(right)
        seg.pack(side="right", padx=(0, 14))
        for key, label in (("list", tr("Liste")), ("cards", tr("Kacheln"))):
            b = ttk.Button(seg, text=label, width=8, command=lambda k=key: self.set_view(k))
            b.pack(side="left")
            self.view_btns[key] = b
        ttk.Checkbutton(right, text=tr("Gruppieren"), variable=self.group_var, style="Plain.TCheckbutton",
                        command=self._toggle_grouping).pack(side="right", padx=(0, 16))
        self.filter_entry = self._w_filter(right, width=22)
        self.filter_entry.pack(side="right", padx=(0, 16), ipady=3)
        ttk.Label(right, text=tr("Suche")).pack(side="right", padx=(0, 8))

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
        for text, cmd in ((tr("Alle auswählen"), self.select_all), (tr("An / Aus"), self.toggle_selected),
                          (tr("Teilen …"), lambda: self.share_selection())):
            ttk.Button(barin, text=text, command=cmd).pack(side="left", padx=(0, 6))
        ttk.Button(barin, text=tr("Entfernen"), command=self.remove_selected, style="Danger.TButton").pack(side="left")
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
        for i, (key, label) in enumerate((("version", tr("Version")), ("release", tr("Quelle")),
                                           ("iface", "Interface"), ("folders", tr("Ordner")),
                                           ("deps", tr("Benötigt")))):
            ttk.Label(grid, text=label, style="DetailKey.TLabel").grid(row=i, column=0, sticky="nw", pady=4)
            v = ttk.Label(grid, style="DetailVal.TLabel", wraplength=170, justify="left")
            v.grid(row=i, column=1, sticky="nw", padx=(12, 0), pady=4)
            self.detail[key] = v
        btns = ttk.Frame(parent, style="Detail.TFrame")
        btns.pack(fill="x", side="bottom")
        btns.columnconfigure((0, 1), weight=1, uniform="detail")
        d = self.detail
        d["upd_btn"] = ttk.Button(btns, text=tr("{0}  Jetzt aktualisieren", UPD_MARK), style="Primary.TButton",
                                  command=self.update_selected)
        d["dl_btn"] = ttk.Button(btns, text=tr("Bei CurseForge herunterladen ↗"), command=self.open_cf_update)
        d["rb_btn"] = ttk.Button(btns, text="", command=self.rollback_selected)
        d["link_btn"] = ttk.Button(btns, text=tr("Mit CurseForge verknüpfen"), command=self.link_curseforge_dialog)
        d["page_btn"] = ttk.Button(btns, text=tr("Quelle ↗"), command=self._detail_release)
        d["folder_btn"] = ttk.Button(btns, text=tr("Ordner"), command=self.open_folder)
        d["hold_btn"] = ttk.Button(btns, text=tr("Festhalten"), command=self.toggle_hold)
        d["rm_btn"] = ttk.Button(btns, text=tr("Entfernen"), command=self.remove_selected, style="Danger.TButton")
        self.update_detail()

    DETAIL_BTNS = ("upd_btn", "dl_btn", "rb_btn", "link_btn", "page_btn", "folder_btn", "hold_btn", "rm_btn")

    def update_detail(self):
        if not self.detail:
            return
        d = self.detail
        try:
            sel = self.selected_addons()
        except tk.TclError:
            return
        for k in self.DETAIL_BTNS:
            d[k].grid_forget()
        if not sel:
            d["kicker"].configure(text=spaced(tr("Details")))
            d["title"].configure(text=tr("Kein Addon gewählt"))
            d["note"].configure(text=tr("Wähle links ein Addon oder eine Gruppe aus.") if self.addons else
                                tr("Noch keine Addons installiert – über „Addons hinzufügen“ geht es los."))
            for k in ("version", "release", "iface", "folders", "deps"):
                d[k].configure(text="–", foreground=THEME["text"])
            return
        names = {a.name for a in sel}
        main = self._group_main(sel)
        sid, src = self.folder_source(main.name)
        d["kicker"].configure(text=spaced(self.source_label(sid, src).replace(": ", " · "))
                              if src else spaced(tr("Manuell installiert")))
        d["title"].configure(text=main.title if len(sel) == 1 else tr("{0}  ({1} Ordner)", main.title, len(sel)))
        d["note"].configure(text=clean_wow_text(main.meta.get("notes", "")) or tr("Keine Beschreibung in der .toc."))
        d["version"].configure(text=main.version or "–")
        pending = self.has_update(sid)
        if src:
            if pending:
                d["release"].configure(text=tr("{0} Update: {1}", UPD_MARK, self.updates[sid]), foreground=THEME["update"])
            elif self.is_held(sid):
                newer = self.held_update(sid)
                d["release"].configure(text=src.get("tag", "–") + tr(" · festgehalten")
                                       + (tr(" (neu: {0})", newer) if newer else ""), foreground=THEME["text_dim"])
            else:
                d["release"].configure(text=src.get("tag", "–") + (tr("  · aktuell") if sid in self.updates else ""),
                                       foreground=THEME["text"])
        else:
            d["release"].configure(text=tr("manuell – keine Update-Prüfung"), foreground=THEME["text_dim"])
        if self.is_outdated(main):
            d["iface"].configure(text=tr("{0} · veraltet", main.interface), foreground=THEME["old"])
        else:
            d["iface"].configure(text=(main.interface or "–") + (tr(" · passt") if self.game and main.interfaces else ""),
                                 foreground=THEME["text"])
        d["folders"].configure(text=", ".join(sorted(names)), foreground=THEME["text"])
        deps = sorted({x for a in sel for x in a.deps if x not in names})
        d["deps"].configure(text=", ".join(deps) or "–", foreground=THEME["text"])
        full, half = [], []
        if pending:
            full.append("upd_btn")
            if self.cf_update_link(sel)[1]:
                full.append("dl_btn")
        rb = self.rollback_info(sid)
        if rb:
            d["rb_btn"].configure(text=tr("↶ Zurück auf {0}", rb["source"].get("tag") or "?"))
            full.append("rb_btn")
        if not src:
            full.append("link_btn")
        else:
            half.append("page_btn")
        half.append("folder_btn")
        if src:
            d["hold_btn"].configure(text=tr("Freigeben") if self.is_held(sid) else tr("Festhalten"))
            half.append("hold_btn")
        half.append("rm_btn")
        for row, k in enumerate(full):
            d[k].grid(row=row, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        for i, k in enumerate(half):
            d[k].grid(row=len(full) + i // 2, column=i % 2, sticky="ew", pady=(0, 6),
                      padx=(0, 3) if i % 2 == 0 else (3, 0))

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
        page, _ = self._page("discover", tr("Entdecken"), tr("Addons finden und installieren"))
        quick = self._card_box(page, pady=(14, 0), title=tr("Direkt installieren"))
        grid = ttk.Frame(quick, style="Panel.TFrame")
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)
        rows = ((tr("CurseForge-Link"), self.cf_link_var, self.install_cf_link,
                 tr("z. B. {0} oder nur leatrix-plus", "https://www.curseforge.com/wow/addons/leatrix-plus")),
                ("GitHub", self.gh_var, self.install_github_dialog, tr("owner/repo oder Link zum Repository")))
        for r, (label, var, cmd, hint) in enumerate(rows):
            ttk.Label(grid, text=label, style="Panel.TLabel", width=16).grid(row=r, column=0, sticky="w", pady=4)
            e = ttk.Entry(grid, textvariable=var)
            e.grid(row=r, column=1, sticky="ew", pady=4)
            e.bind("<Return>", lambda ev, c=cmd: c())
            Placeholder(e, var, hint)
            ttk.Button(grid, text=tr("Installieren"), width=14, command=cmd).grid(row=r, column=2, sticky="e",
                                                                               padx=(8, 0), pady=4)
        ttk.Label(grid, text=tr("ZIP-Datei"), style="Panel.TLabel", width=16).grid(row=2, column=0, sticky="w", pady=4)
        self.zip_cb = ttk.Combobox(grid, textvariable=self.zip_var, state="readonly")
        self.zip_cb.grid(row=2, column=1, sticky="ew", pady=4)
        zrow = ttk.Frame(grid, style="Panel.TFrame")
        zrow.grid(row=2, column=2, sticky="e", padx=(8, 0), pady=4)
        ttk.Button(zrow, text=tr("Installieren"), width=14, command=self.install_zip_choice).pack(side="left")
        ttk.Button(zrow, text=tr("Andere …"), command=self.install_zip_dialog).pack(side="left", padx=(8, 0))
        ttk.Label(quick, style="Panel.TLabel", foreground=t["text_faint2"],
                  text=tr("Tipp: ZIP-, .lzpack- und .lzlist-Dateien kannst du auch einfach ins Fenster ziehen.")
                  if HAS_DND else tr("Die Liste zeigt die neuesten ZIP-Dateien aus deinem Download-Ordner.")).pack(
            anchor="w", pady=(6, 0))

        box = self._card_box(page, pady=(12, 0), fill="both", expand=True, title=tr("CurseForge durchsuchen · nur WoW"))
        top = ttk.Frame(box, style="Panel.TFrame")
        top.pack(fill="x")
        e = self.cf_entry = ttk.Entry(top, textvariable=self.cf_q)
        e.pack(side="left", fill="x", expand=True, ipady=2)
        Placeholder(e, self.cf_q, tr("Addon-Name, z. B. Details, Questie, Bagnon …"))
        e.bind("<Return>", lambda ev: self.cf_search())
        ttk.Button(top, text=tr("Suchen"), style="Primary.TButton", command=self.cf_search).pack(side="left", padx=(8, 0))
        cb = ttk.Combobox(top, textvariable=self.cf_sort, values=[tr(k) for k in CF_SORTS], state="readonly",
                          width=19)
        cb.pack(side="right")
        cb.bind("<<ComboboxSelected>>", lambda ev: self.cf_search())
        ttk.Label(top, text=tr("Sortierung"), style="Panel.TLabel").pack(side="right", padx=(16, 8))
        self.cf_fit_chk = ttk.Checkbutton(box, variable=self.cf_fit, command=self.cf_show_rows,
                                          text=tr("Nur Addons für meine Spielversion"))
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
        ttk.Label(dbtn, text=tr("Datei"), style="DetailKey.TLabel").pack(anchor="w")
        self.cf_file_cb = ttk.Combobox(dbtn, textvariable=self.cf_file_var, state="readonly")
        self.cf_file_cb.pack(fill="x", pady=(2, 10))
        self.cf_install_btn = ttk.Button(dbtn, text=tr("Installieren"), style="Primary.TButton", command=self.cf_install)
        self.cf_install_btn.pack(fill="x", pady=(0, 6))
        self.cf_page_btn = ttk.Button(dbtn, text=tr("Auf CurseForge ansehen ↗"), command=self.cf_open_page)
        self.cf_page_btn.pack(fill="x")

        lst = ttk.Frame(body, style="Panel.TFrame")
        lst.pack(side="left", fill="both", expand=True)
        foot = ttk.Frame(lst, style="Panel.TFrame")
        foot.pack(side="bottom", fill="x", pady=(8, 0))
        ttk.Label(foot, textvariable=self.cf_count, style="Panel.TLabel").pack(side="left")
        self.cf_more_btn = ttk.Button(foot, text=tr("Mehr laden"), command=lambda: self.cf_search(more=True))
        self.cf_more_btn.pack(side="right")
        frame, self.cf_tree = self._list(lst, (("name", tr("Addon")), ("author", tr("Autor")), ("downloads", tr("Downloads")),
                                               ("updated", tr("Aktualisiert")), ("versions", tr("Versionen"))),
                                         (160, 95, 122, 112, 110), stretch=("name", "versions"),
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
        page, head = self._page("updates", tr("Updates"), tr("Addons aktuell halten"))
        right = ttk.Frame(head)
        right.pack(side="right", anchor="s")
        ttk.Button(right, text=tr("{0}  Alle aktualisieren", UPD_MARK), style="Primary.TButton",
                   command=self.update_all).pack(side="right")
        ttk.Button(right, text=tr("Jetzt prüfen"), command=self.check_updates).pack(side="right", padx=(0, 8))
        ttk.Label(right, textvariable=self.upd_count_var).pack(side="right", padx=(0, 16))

        opts = ttk.Frame(page)
        opts.pack(fill="x", pady=(8, 0))
        self._w_check(opts, tr("Beim Start auf Updates prüfen"), self.check_start_var, panel=False).pack(side="left")
        self._w_check(opts, tr("Vor Updates automatisch WTF sichern"), self.auto_backup_var, panel=False).pack(
            side="left", padx=(20, 0))

        self.browser_box_outer = ttk.Frame(page)
        self.browser_box_outer.pack(fill="x")
        self.browser_box = None

        body = ttk.Frame(page)
        body.pack(fill="both", expand=True, pady=(12, 0))
        side = ttk.Frame(body, width=360)
        side.pack(side="right", fill="y", padx=(14, 0))
        side.pack_propagate(False)
        nbox = self._card_box(side, pady=(0, 0), fill="both", expand=True, title=tr("Was ist neu?"))
        nf, self.upd_notes = self._text(nbox)
        nf.pack(fill="both", expand=True)
        self.upd_one_btn = ttk.Button(nbox, text=tr("{0}  Dieses aktualisieren", UPD_MARK), style="Primary.TButton",
                                      command=self.update_selected_in_list)
        self.upd_one_btn.pack(fill="x", pady=(10, 0))
        nb = ttk.Frame(nbox, style="Panel.TFrame")
        nb.pack(fill="x", pady=(6, 0))
        nb.columnconfigure(0, weight=1, uniform="b")
        nb.columnconfigure(1, weight=1, uniform="b")
        self.upd_dl_btn = ttk.Button(nb, text=tr("Im Browser laden ↗"), command=self._upd_open_download)
        self.upd_dl_btn.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        self.upd_page_btn = ttk.Button(nb, text=tr("Projektseite ↗"), command=self._upd_open_page)
        self.upd_page_btn.grid(row=0, column=1, sticky="ew", padx=(3, 0))

        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True)
        frame, self.upd_tree = self._list(left, (("name", tr("Addon")), ("old", tr("Installiert")), ("new", tr("Neu")),
                                                 ("hint", tr("Hinweis"))),
                                          (170, 140, 160, 120), stretch=("name", "new"))
        frame.pack(fill="both", expand=True)
        self.upd_tree.bind("<<TreeviewSelect>>", lambda e: self._upd_show())

        low = ttk.Frame(page)
        low.pack(fill="x", pady=(12, 0))
        b1 = self._card_box(low, pady=(0, 0), side="left", fill="both", expand=True, title=tr("Manuell installierte Addons"))
        ttk.Label(b1, style="Panel.TLabel", wraplength=420, justify="left",
                  text=tr("Verknüpfe sie mit CurseForge, dann prüft Leeroyzan auch diese auf Updates.")).pack(anchor="w")
        ttk.Button(b1, text=tr("Mit CurseForge verknüpfen"), command=self.link_curseforge_dialog).pack(anchor="w", pady=(8, 0))
        b2 = self._card_box(low, pady=(0, 0), side="left", fill="both", expand=True, title=tr("Leeroyzan selbst"),
                            padx=(12, 0))
        self.manager_state = tk.StringVar(value=tr("Installiert: v{0}", APP_VERSION))
        ttk.Label(b2, textvariable=self.manager_state, style="Panel.TLabel", wraplength=420,
                  justify="left").pack(anchor="w")
        mrow = ttk.Frame(b2, style="Panel.TFrame")
        mrow.pack(fill="x", pady=(8, 0))
        ttk.Button(mrow, text=tr("Nach Update suchen"), command=self.check_manager_update).pack(side="right")
        self._w_update_mode(mrow).pack(side="left")

    # --- Teilen ------------------------------------------------------------------ #
    def _page_share(self):
        t = THEME
        page, _ = self._page("share", tr("Teilen"), tr("Addons mit Freunden teilen"),
                             tr("Eine Download-Liste enthält nur Verweise (CurseForge/GitHub) und passt als Text in "
                             "Discord oder eine Kurznachricht. Ein Paket (.lzpack) enthält zusätzlich die Dateien "
                             "von ZIP- und GitHub-Addons."))
        cols = ttk.Frame(page)
        cols.pack(fill="both", expand=True, pady=(12, 0))
        cols.columnconfigure(0, weight=1, uniform="c")
        cols.columnconfigure(1, weight=1, uniform="c")
        cols.rowconfigure(0, weight=1)
        lf = ttk.Frame(cols)
        lf.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        rf = ttk.Frame(cols)
        rf.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        box = self._card_box(lf, pady=(0, 0), fill="both", expand=True, title=tr("Senden"))
        scope = ttk.Frame(box, style="Panel.TFrame")
        scope.pack(fill="x")
        self.share_sel_rb = ttk.Radiobutton(scope, text=tr("Ausgewählte Addons"), value="sel",
                                            variable=self.share_scope, command=self.rebuild_share)
        self.share_sel_rb.pack(side="left")
        self.share_all_rb = ttk.Radiobutton(scope, text=tr("Alle Addons"), value="all",
                                            variable=self.share_scope, command=self.rebuild_share)
        self.share_all_rb.pack(side="left", padx=(16, 0))
        opt = ttk.Frame(box, style="Panel.TFrame")
        opt.pack(fill="x", pady=(8, 0))
        ttk.Checkbutton(opt, text=tr("Genau meine Versionen"), variable=self.share_exact,
                        command=self.rebuild_share).pack(side="left")
        ttk.Checkbutton(opt, text=tr("Addon-Sets mitsenden"), variable=self.share_sets,
                        command=self.rebuild_share).pack(side="left", padx=(16, 0))
        szr = ttk.Frame(box, style="Panel.TFrame")
        szr.pack(fill="x", pady=(8, 8))
        ttk.Label(szr, text=tr("Nachrichtenlänge"), style="Panel.TLabel").pack(side="left", padx=(0, 8))
        cb = ttk.Combobox(szr, textvariable=self.share_size, values=[tr(k) for k in LIST_PART_SIZES],
                          state="readonly", width=28)
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda e: self.rebuild_share())
        sf, self.share_txt = self._text(box)
        sf.pack(fill="both", expand=True)
        ttk.Label(box, textvariable=self.share_info, style="Panel.TLabel", wraplength=470,
                  justify="left").pack(anchor="w", pady=(6, 0))
        brow = ttk.Frame(box, style="Panel.TFrame")
        brow.pack(fill="x", pady=(8, 0))
        ttk.Button(brow, text=tr("Code kopieren"), style="Primary.TButton", command=self.copy_share_code).pack(side="left")
        ttk.Button(brow, text=tr("Lesbare Liste kopieren"), command=self.copy_share_readable).pack(side="left", padx=(6, 0))
        prow = ttk.Frame(box, style="Panel.TFrame")
        prow.pack(fill="x", pady=(6, 0))
        ttk.Button(prow, text=tr("Als .lzlist speichern"), command=self.save_share_file).pack(side="left")
        ttk.Button(prow, text=tr("Als Paket (.lzpack) speichern"), command=self.export_share_pack).pack(side="left",
                                                                                                  padx=(6, 0))

        rbox = self._card_box(rf, pady=(0, 0), fill="both", expand=True, title=tr("Empfangen"))
        ttk.Label(rbox, style="Panel.TLabel", wraplength=470, justify="left",
                  text=tr("Code aus Discord/Chat hier einfügen (alle Teile, Reihenfolge egal):")).pack(anchor="w")
        rf2, self.import_txt = self._text(rbox, readonly=False)
        rf2.pack(fill="both", expand=True, pady=(6, 0))
        self.import_txt.bind("<<Modified>>", self._import_modified)
        irow = ttk.Frame(rbox, style="Panel.TFrame")
        irow.pack(fill="x", pady=(8, 0))
        ttk.Button(irow, text=tr("Aus Zwischenablage einfügen"), command=self.paste_import).pack(side="left")
        ttk.Button(irow, text=tr("Leeren"), command=lambda: self.import_txt.delete("1.0", "end")).pack(side="left", padx=(6, 0))
        ttk.Label(rbox, textvariable=self.import_preview, style="Panel.TLabel", wraplength=470,
                  justify="left").pack(anchor="w", pady=(8, 0))
        self.import_btn = ttk.Button(rbox, text=tr("Installieren"), style="Primary.TButton", command=self.import_list_now)
        self.import_btn.pack(anchor="w", pady=(6, 0))
        self.import_btn.state(["disabled"])
        tk.Frame(rbox, height=1, bg=t["line"]).pack(fill="x", pady=(12, 10))
        ttk.Label(rbox, text=tr("Oder eine Datei aus deinem Download-Ordner (.lzlist / .lzpack):"),
                  style="Panel.TLabel").pack(anchor="w")
        frow = ttk.Frame(rbox, style="Panel.TFrame")
        frow.pack(fill="x", pady=(6, 0))
        self.recv_cb = ttk.Combobox(frow, textvariable=self.recv_file_var, state="readonly")
        self.recv_cb.pack(side="left", fill="x", expand=True)
        ttk.Button(frow, text=tr("Öffnen"), command=self.open_recv_file).pack(side="left", padx=(8, 0))
        ttk.Button(frow, text=tr("Andere …"), command=self.open_other_share_file).pack(side="left", padx=(8, 0))
        self._import_data = None

    # --- Sets & Backups ------------------------------------------------------------ #
    def _page_sets(self):
        page, _ = self._page("sets", tr("Sets & Backups"), tr("Addon-Gruppen und Sicherungen"))
        cols = ttk.Frame(page)
        cols.pack(fill="both", expand=True, pady=(12, 0))
        cols.columnconfigure(0, weight=1, uniform="c")
        cols.columnconfigure(1, weight=1, uniform="c")
        cols.rowconfigure(0, weight=1)
        lf = ttk.Frame(cols)
        lf.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        rf = ttk.Frame(cols)
        rf.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        box = self._card_box(lf, pady=(0, 0), fill="both", expand=True, title=tr("Addon-Sets"))
        ttk.Label(box, style="Panel.TLabel", wraplength=460, justify="left",
                  text=tr("Ein Set merkt sich, welche Addons aktiv sind. „Anwenden“ schaltet genau diese ein "
                       "und alle anderen aus – z. B. für Leveln, Raid oder einen Login ohne Addons.")).pack(anchor="w")
        frame, self.sets_tree = self._list(box, (("name", tr("Set")), ("count", tr("Addons"))), (260, 80),
                                           stretch=("name",), height=4)
        frame.pack(fill="both", expand=True, pady=(10, 0))
        self.sets_tree.bind("<<TreeviewSelect>>", lambda e: self._set_selected())
        self.sets_tree.bind("<Double-1>", lambda e: self.apply_set())
        self.set_members = ttk.Label(box, style="Panel.TLabel", wraplength=460, justify="left")
        self.set_members.pack(anchor="w", pady=(8, 0))
        row = ttk.Frame(box, style="Panel.TFrame")
        row.pack(fill="x", pady=(8, 0))
        ttk.Button(row, text=tr("Anwenden"), style="Primary.TButton", command=self.apply_set).pack(side="left")
        ttk.Button(row, text=tr("Löschen"), style="Danger.TButton", command=self.delete_set).pack(side="left", padx=(6, 0))
        srow = ttk.Frame(box, style="Panel.TFrame")
        srow.pack(fill="x", pady=(12, 0))
        e = ttk.Entry(srow, textvariable=self.set_name_var)
        e.pack(side="left", fill="x", expand=True)
        Placeholder(e, self.set_name_var, tr("Name für ein neues Set"))
        e.bind("<Return>", lambda ev: self.save_set())
        ttk.Button(srow, text=tr("Aktive als Set speichern"), command=self.save_set).pack(side="left", padx=(8, 0))

        bbox = self._card_box(rf, pady=(0, 0), fill="both", expand=True, title=tr("Backups · WTF-Ordner"))
        ttk.Label(bbox, style="Panel.TLabel", wraplength=460, justify="left",
                  text=tr("Gesichert werden alle Addon-Einstellungen (SavedVariables) dieser Spielversion.")).pack(anchor="w")
        frame, self.backup_tree = self._list(bbox, (("when", tr("Zeitpunkt")), ("kind", tr("Art"))), (170, 200),
                                             stretch=("kind",), height=4)
        frame.pack(fill="both", expand=True, pady=(10, 0))
        brow = ttk.Frame(bbox, style="Panel.TFrame")
        brow.pack(fill="x", pady=(8, 0))
        ttk.Button(brow, text=tr("Jetzt sichern"), style="Primary.TButton", command=self.backup).pack(side="left")
        ttk.Button(brow, text=tr("Wiederherstellen"), command=self.restore_dialog).pack(side="left", padx=(6, 0))
        ttk.Button(brow, text=tr("Ordner öffnen"),
                   command=lambda: (BACKUP_DIR.mkdir(parents=True, exist_ok=True),
                                    open_in_explorer(BACKUP_DIR))).pack(side="right")
        self._w_check(bbox, tr("Vor Updates automatisch sichern"), self.auto_backup_var).pack(anchor="w", pady=(10, 0))

    # --- Sitzungen -------------------------------------------------------------- #
    SESSION_PERIODS = [("all", "Alle Zeiträume"), ("today", "Heute"), ("week", "Letzte 7 Tage"),
                       ("month", "Letzte 30 Tage")]

    def _page_sessions(self):
        t = THEME
        page, head = self._page("sessions", tr("Leeroyzan Link"), tr("Spielsitzungen"))
        right = ttk.Frame(head)
        right.pack(side="right", anchor="s")
        ttk.Button(right, text=tr("Jetzt einlesen"), command=lambda: self.import_sessions(manual=True)).pack(side="right")
        pcb = ttk.Combobox(right, textvariable=self.sess_period_var, state="readonly", width=16,
                           values=[tr(label) for _, label in self.SESSION_PERIODS])
        pcb.pack(side="right", padx=(0, 8))
        pcb.bind("<<ComboboxSelected>>", lambda e: self._sess_filter_changed())
        self.sess_period_var.set(tr(dict(self.SESSION_PERIODS)[self.sess_period]))
        self.sess_char_cb = ttk.Combobox(right, textvariable=self.sess_char_var, state="readonly", width=22)
        self.sess_char_cb.pack(side="right", padx=(0, 8))
        self.sess_char_cb.bind("<<ComboboxSelected>>", lambda e: self.refresh_sessions_page())

        link = self._card_box(page, pady=(12, 0))
        row = ttk.Frame(link, style="Panel.TFrame")
        row.pack(fill="x")
        # wraplength < 300: _fit_wraps lässt das Feld in Ruhe, die Breite rechnet _wrap_link_label (Knopf daneben)
        self.link_label = ttk.Label(row, textvariable=self.link_state_var, style="Panel.TLabel", wraplength=280,
                                    justify="left")
        self.link_label.pack(side="left", fill="x", expand=True)
        self.link_btn = ttk.Button(row, style="Primary.TButton", command=self.install_link)
        row.bind("<Configure>", lambda e: self._wrap_link_label(e.width))

        body = ttk.Frame(page)
        body.pack(fill="both", expand=True, pady=(12, 0))
        left = ttk.Frame(body, width=496)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        frame, self.sess_tree = self._list(left, (("when", tr("Datum")), ("char", tr("Charakter")), ("dur", tr("Dauer")),
                                                  ("xp", tr("XP")), ("gold", tr("Gold"))),
                                           (134, 104, 78, 66, 92), stretch=("char",))
        frame.pack(fill="both", expand=True)
        self.sess_tree.tag_configure("total", foreground=t["gold"], font=self.fonts["bold"])
        self.sess_tree.bind("<<TreeviewSelect>>", lambda e: self._sess_show())
        brow = ttk.Frame(left)
        brow.pack(fill="x", pady=(8, 0))
        ttk.Button(brow, text=tr("Text kopieren"), command=self.copy_session_text).pack(side="left")
        ttk.Button(brow, text=tr("Löschen"), style="Danger.TButton", command=self.delete_selected_session).pack(
            side="left", padx=(6, 0))
        ttk.Button(brow, text=tr("Ordner öffnen"), command=self.open_sessions_folder).pack(side="right")

        box = self._card_box(body, pady=(0, 0), side="left", fill="both", expand=True, padx=(14, 0))
        tf, txt = self._text(box)
        tf.pack(fill="both", expand=True)
        txt.configure(spacing1=1, spacing3=1, padx=14, pady=10)
        txt.tag_configure("title", foreground=t["gold"], font=self.fonts["card"], spacing3=2)
        txt.tag_configure("hl", foreground=t["gold"])
        txt.tag_configure("ok", foreground=t["ok"])
        txt.tag_configure("warn", foreground=t["warn"])
        for i, color in enumerate(QUALITY_COLORS):
            txt.tag_configure(f"q{i}", foreground=color)
        self.sess_text = txt

    def _wrap_link_label(self, width: int | None = None):
        try:
            width = width or self.link_label.master.winfo_width()
            btn = self.link_btn.winfo_reqwidth() + 16 if self.link_btn.winfo_manager() else 0
            self.link_label.configure(wraplength=max(240, width - btn - 8))
        except tk.TclError:
            pass

    def _char_label(self, char: str) -> str:
        """Name, bei gleichen Namen auf mehreren Realms mit Realm."""
        name, _, realm = char.partition("-")
        same = [c for c in {s["char"] for s in self.sessions_all} if c.partition("-")[0] == name]
        return f"{name} ({realm})" if len(same) > 1 and realm else name

    def _sess_filter_changed(self):
        label = self.sess_period_var.get()
        self.sess_period = next((k for k, l in self.SESSION_PERIODS if tr(l) == label), "all")
        self.refresh_sessions_page()

    def refresh_sessions_page(self, select: str = ""):
        """Liste neu aufbauen. select: Sitzungs-ID oder "total"."""
        if getattr(self, "sess_tree", None) is None or not self.sess_tree.winfo_exists():
            return
        self.sessions_all = load_sessions(self.flavor) if self.flavor else []
        chars = sorted({s["char"] for s in self.sessions_all}, key=str.lower)
        labels = {c: self._char_label(c) for c in chars}
        all_label = tr("Alle Charaktere")
        self.sess_char_cb["values"] = [all_label] + [labels[c] for c in chars]
        if self.sess_char_var.get() not in self.sess_char_cb["values"]:
            self.sess_char_var.set(all_label)
        want = next((c for c in chars if labels[c] == self.sess_char_var.get()), "")
        now = datetime.now()
        cutoff = {"today": now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp(),
                  "week": now.timestamp() - 7 * 86400, "month": now.timestamp() - 30 * 86400}.get(self.sess_period, 0)
        shown = [s for s in self.sessions_all if (not want or s["char"] == want) and s["start"] >= cutoff]
        self.sessions_shown = shown

        tree = self.sess_tree
        if not select:
            cur = tree.selection()
            if cur:
                select = "total" if cur[0] == "total" else next(
                    (s["id"] for i, s in enumerate(self._sess_rows) if f"s{i}" == cur[0]), "")
        self._sess_rows = shown
        tree.delete(*tree.get_children())
        tree.insert("", "end", iid="total", tags=("total",), values=(
            tr("Σ Gesamt"), plural(len(shown), "{0} Sitzung", "{0} Sitzungen"),
            fmt_dur(sum(s["played"] for s in shown)), fmt_int(sum(s["xp"]["total"] for s in shown)),
            fmt_money(sum(session_net_money(s) for s in shown), sign=True)))
        target = "total"
        for i, s in enumerate(shown):
            tags = ["odd"] if i % 2 else []
            if s.get("legacy"):
                tags.append("dim")
            tree.insert("", "end", iid=f"s{i}", tags=tuple(tags), values=(
                datetime.fromtimestamp(s["start"]).strftime(tr("%d.%m.%Y  %H:%M")), labels.get(s["char"], s["char"]),
                fmt_dur(s["played"]), fmt_int(s["xp"]["total"]), fmt_money(session_net_money(s), sign=True)))
            if select == s["id"]:
                target = f"s{i}"
        if not select and shown:
            target = "s0"                      # ohne Auswahl: die neueste Sitzung
        tree.selection_set(target)
        tree.see(target)
        self._sess_show()

    def _sess_selected(self) -> dict | None:
        sel = self.sess_tree.selection()
        if not sel or sel[0] == "total":
            return None
        try:
            return self._sess_rows[int(sel[0][1:])]
        except (ValueError, IndexError):
            return None

    def _sess_show(self):
        if not self.sessions_all:
            parts = [(tr("Noch keine Sitzungen") + "\n", "title"),
                     ("\n" + tr("So funktioniert es") + "\n", "head"),
                     (tr("Leeroyzan Link zeichnet im Spiel jede Sitzung auf: Spielzeit, Erfahrung, Gold, Quests, Beute, "
                         "Kampf (Schaden, Heilung, Hiebe, Zauber, Buffs, Tode), Strecke und Schritte, Berufe, entdeckte "
                         "Gebiete, Dungeons und Schlachtzüge sowie besiegte Gegner, Elite und Bosse.") + "\n\n", ""),
                     (tr("Sobald du dich ausloggst, schreibt WoW die Daten. Leeroyzan übernimmt sie dann automatisch, "
                         "speichert sie dauerhaft und fasst jede Sitzung hier zusammen.") + "\n\n", ""),
                     (tr("Ein /reload oder kurzes Ausloggen (unter 15 Minuten) mit demselben Charakter zählt zur selben "
                         "Sitzung. Im Spiel zeigt /lz sitzung die laufende Sitzung.") + "\n", "dim")]
        else:
            s = self._sess_selected()
            if s is None:
                who = self.sess_char_var.get() or tr("Alle Charaktere")
                period = tr(dict(self.SESSION_PERIODS)[self.sess_period])
                parts = overview_report(self.sessions_shown, f"{who}, {period}")
            else:
                parts = session_report(s)
        self._sess_parts = parts
        self._set_text(self.sess_text, parts=parts)
        self.sess_text.yview_moveto(0)

    def copy_session_text(self):
        if self._sess_parts:
            self._clip(report_plain_text(self._sess_parts), tr("Zusammenfassung kopiert."))

    def delete_selected_session(self):
        s = self._sess_selected()
        if s is None:
            self.set_status(tr("Bitte eine einzelne Sitzung auswählen."))
            return
        if not self.ask(tr("Sitzung vom {0} ({1}) aus Leeroyzan löschen? Sie wird danach auch nicht mehr aus dem "
                           "Spiel übernommen.", fmt_day(s["start"]) + " " + fmt_clock(s["start"]), s["name"] or s["char"]),
                        yes=tr("Löschen"), no=tr("Abbrechen")):
            return
        try:
            delete_session(self.flavor, s)
        except OSError as e:
            self.error(tr("Konnte nicht gelöscht werden:\n{0}", e))
            return
        self.refresh_sessions_page(select="total")
        self.set_status(tr("Sitzung gelöscht."))

    def open_sessions_folder(self):
        if not self.flavor:
            return
        d = sessions_dir(self.flavor)
        d.mkdir(parents=True, exist_ok=True)
        open_in_explorer(d)

    def show_session(self, sid: str):
        self.sess_char_var.set("")
        self.sess_period = "all"
        self.sess_period_var.set(tr(dict(self.SESSION_PERIODS)["all"]))
        self.show_page("sessions")
        self.refresh_sessions_page(select=sid)

    # Übernahme aus dem Spiel ----------------------------------------------------- #
    def import_sessions(self, manual: bool = False):
        """Liest die SavedVariables des Addons (im Hintergrund) und übernimmt neue Sitzungen."""
        if not self.root_path or not self.flavor:
            if manual:
                self.ready(modifies=False)
            return
        if self._sess_importing:
            return
        self._sess_importing = True
        root, flavor = self.root_path, self.flavor
        sig = link_signature(root, flavor)
        pending_before = bool(self._sess_pending)

        def work():
            res = import_link_sessions(root, flavor)
            res["running"] = bool(running_wow(root, flavor)) if (res["new"] or res["updated"] or pending_before) else False
            return res

        def done(res, err):
            self._sess_importing = False
            if err:
                log.error("Sitzungen einlesen: %s", err)
                if manual:
                    self.error(tr("Sitzungen konnten nicht eingelesen werden:\n{0}", err))
                return
            if flavor != self.flavor:
                return
            self._sess_sig = sig
            changed = res["new"] + res["updated"]
            for s in changed:
                self._sess_pending[s["id"]] = s
            if res["errors"]:
                log.warning("Leeroyzan Link: %s", "; ".join(res["errors"]))
            if changed:
                log.info("Sitzungen übernommen: %d neu, %d aktualisiert", len(res["new"]), len(res["updated"]))
                if self.current_page == "sessions":
                    self.refresh_sessions_page()
            if self._sess_pending and not res["running"]:
                self._announce_sessions()
            elif manual:
                if res["errors"] and not changed:
                    self.warn(tr("Die Daten von Leeroyzan Link konnten nicht gelesen werden:\n{0}", "\n".join(res["errors"])))
                elif res["files"] == 0:
                    self.info(tr("Noch keine Daten von Leeroyzan Link gefunden. WoW schreibt sie beim Ausloggen und bei /reload."))
                elif not changed:
                    self.info(tr("Keine neuen Sitzungen."))
                else:
                    self.info(tr("{0} übernommen. WoW läuft noch – die laufende Sitzung wird nach dem Ausloggen ergänzt.",
                                 plural(len(changed), "{0} Sitzung", "{0} Sitzungen")))

        self.run_quiet(work, done)

    def _watch_sessions(self):
        """Alle 10 s: Hat WoW die Datei neu geschrieben? Ist das Spiel inzwischen beendet?"""
        try:
            if self.root_path and self.flavor and not self._sess_importing:
                if link_signature(self.root_path, self.flavor) != self._sess_sig:
                    self.import_sessions()
                elif self._sess_pending:
                    root, flavor = self.root_path, self.flavor

                    def check(running, err):
                        if not err and not running and flavor == self.flavor:
                            self._announce_sessions()
                    self.run_quiet(lambda: running_wow(root, flavor), check)
        except (OSError, tk.TclError) as e:
            log.warning("Sitzungen beobachten: %s", e)
        finally:
            self.after(10000, self._watch_sessions)

    def _announce_sessions(self):
        """Hinweis nach Spielende: Die Sitzung ist gespeichert und zusammengefasst."""
        pending = sorted(self._sess_pending.values(), key=lambda s: s["start"])
        self._sess_pending.clear()
        if not pending:
            return
        last = pending[-1]
        if len(pending) == 1:
            text = tr("Sitzung gespeichert: {0}", session_headline(last))
        else:
            text = tr("{0} Sitzungen gespeichert. Zuletzt: {1}", len(pending), session_headline(last))
        self.notify(text, "ok", [(tr("Zusammenfassung ansehen"), lambda sid=last["id"]: self.show_session(sid))],
                    timeout=180000)
        self.set_status(text)

    # Addon installieren ------------------------------------------------------------ #
    def update_link_state(self) -> bool:
        """Zeigt, ob Leeroyzan Link installiert und aktuell ist. True = installiert, aber veraltet."""
        if getattr(self, "link_btn", None) is None or not self.link_btn.winfo_exists():
            return False
        bundled = link_version(bundled_link_dir())
        btn = ""
        outdated = False
        if not self.root_path or not self.flavor:
            text = tr("Wähle zuerst den WoW-Ordner – dann zeigt Leeroyzan hier deine Spielsitzungen.")
        else:
            inst = installed_link_dir(self.root_path, self.flavor)
            have = link_version(inst)
            newer = bool(bundled) and (not have or parse_version(bundled) > parse_version(have))
            if not inst:
                text = tr("Leeroyzan Link ist in dieser Spielversion nicht installiert. Das Addon zeichnet deine "
                          "Sitzungen im Spiel auf – ohne es bleibt diese Seite leer.")
                btn = tr("Leeroyzan Link {0} installieren", bundled) if bundled else ""
            elif newer:
                outdated = True
                text = tr("Leeroyzan Link {0} ist installiert. Version {1} liegt Leeroyzan bei und zeichnet die ganze "
                          "Sitzung auf (Quests, Beute, Kampf, Strecke, Berufe, Instanzen …).", have or "?", bundled)
                btn = tr("Auf {0} aktualisieren", bundled)
            else:
                text = tr("Leeroyzan Link {0} ist installiert. Nach dem Ausloggen übernimmt Leeroyzan jede Sitzung "
                          "automatisch und fasst sie hier zusammen.", have or "?")
                times = []
                for f in link_savedvariables(self.root_path, self.flavor):
                    try:
                        times.append(f.stat().st_mtime)
                    except OSError:
                        pass
                if times:
                    text += " " + tr("Daten zuletzt geschrieben: {0}.",
                                     datetime.fromtimestamp(max(times)).strftime(tr("%d.%m.%Y  %H:%M")))
            if inst is not None and inst.parent == disabled_dir(self.root_path, self.flavor):
                text += " " + tr("Das Addon ist zurzeit deaktiviert – aktiviere es unter „Meine Addons“.")
        self.link_state_var.set(text)
        if btn:
            self.link_btn.configure(text=btn)
            self.link_btn.pack(side="right", padx=(12, 0), before=self.link_label)
        else:
            self.link_btn.pack_forget()
        self.after_idle(self._wrap_link_label)
        return outdated

    def install_link(self):
        if not self.ready(modifies=True):
            return
        root, flavor = self.root_path, self.flavor

        def done(version):
            log.info("Leeroyzan Link %s installiert (%s)", version, flavor)
            self.dismiss("link")
            self.refresh()
            self.update_link_state()
            self.notify(tr("Leeroyzan Link {0} ist installiert. Starte WoW neu (oder logge dich neu ein), damit das "
                           "Addon geladen wird.", version), "ok", timeout=30000)

        self.run_bg(tr("Installiere Leeroyzan Link"), lambda: install_link_addon(root, flavor), done)

    def _sessions_flavor_changed(self):
        self._sess_sig = None
        self._sess_pending.clear()
        outdated = self.update_link_state()
        if outdated and not self._link_hint_shown:
            self._link_hint_shown = True
            inst = installed_link_dir(self.root_path, self.flavor)
            self.notify(tr("Leeroyzan Link {0} ist verfügbar (installiert: {1}). Die neue Version zeichnet ganze "
                           "Spielsitzungen für Leeroyzan auf.", link_version(bundled_link_dir()), link_version(inst) or "?"),
                        "update", [(tr("Aktualisieren"), self.install_link),
                                   (tr("Sitzungen"), lambda: self.show_page("sessions"))], key="link")
        if self.current_page == "sessions":
            self.refresh_sessions_page()
        self.import_sessions()

    # --- Einstellungen ---------------------------------------------------------------- #
    def _page_settings(self):
        page, _ = self._page("settings", tr("Einstellungen"), tr("Leeroyzan einrichten"))
        cols = ttk.Frame(page)
        cols.pack(fill="both", expand=True, pady=(12, 0))
        cols.columnconfigure(0, weight=3, uniform="c")
        cols.columnconfigure(1, weight=2, uniform="c")
        lf = ttk.Frame(cols)
        lf.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        rf = ttk.Frame(cols)
        rf.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        box = self._card_box(lf, pady=(0, 0), title=tr("World of Warcraft"))
        self._w_path(box).pack(fill="x")
        ttk.Label(box, style="Panel.TLabel", foreground=THEME["text_faint2"],
                  text=tr("Die Spielversion wählst du links oben.")).pack(anchor="w", pady=(6, 0))
        cbox = self._card_box(lf, pady=(12, 0), title="CurseForge")
        self.cf_key_widget = self._w_cf_key(cbox, panel=True)
        self.cf_key_widget.pack(fill="x")
        gbox = self._card_box(rf, pady=(0, 0), title=tr("Allgemein"))
        lrow = ttk.Frame(gbox, style="Panel.TFrame")
        lrow.pack(fill="x", pady=(0, 12))
        ttk.Label(lrow, text="Language" if LANG == "en" else f"{tr('Sprache')} · Language",
                  style="Panel.TLabel").pack(side="left", padx=(0, 8))
        self.lang_var = tk.StringVar(value=LANGUAGES.get(LANG, "English"))
        lcb = ttk.Combobox(lrow, textvariable=self.lang_var, values=list(LANGUAGES.values()), state="readonly",
                           width=14)
        lcb.pack(side="left")
        lcb.bind("<<ComboboxSelected>>", lambda e: self.change_language(
            next((k for k, v in LANGUAGES.items() if v == self.lang_var.get()), "en")))
        self._w_check(gbox, tr("Beim Start auf Addon-Updates prüfen"), self.check_start_var).pack(anchor="w")
        self._w_check(gbox, tr("Vor Updates automatisch WTF sichern"), self.auto_backup_var).pack(anchor="w", pady=(6, 0))
        self._w_update_mode(gbox).pack(anchor="w", pady=(12, 0))
        ttk.Button(gbox, text=tr("Nach Leeroyzan-Update suchen"), command=self.check_manager_update).pack(
            anchor="w", pady=(10, 0))
        if portable_dir():
            where = tr("Portabel – Einstellungen, Logs und Backups liegen im Ordner {0} neben der .exe.",
                       PORTABLE_DIR_NAME)
        else:
            where = tr("Einstellungen, Logs und Backups liegen in {0}.", config_dir())
        ttk.Label(gbox, text=where, style="Panel.TLabel", wraplength=360, justify="left").pack(anchor="w", pady=(10, 0))
        hbox = self._card_box(rf, pady=(12, 0), title=tr("Hilfe"))
        for text, cmd in ((tr("Log öffnen"), self.open_log), (tr("Datenordner öffnen"), lambda: open_in_explorer(config_dir())),
                          (tr("Addon-Ordner öffnen"), lambda: self.open_folder([])), (tr("Neu einlesen"), self.refresh)):
            ttk.Button(hbox, text=text, command=cmd).pack(anchor="w", pady=(0, 6), fill="x")
        links = ttk.Frame(hbox, style="Panel.TFrame")
        links.pack(fill="x", pady=(6, 0))
        for i, (text, url) in enumerate(HEADER_LINKS):
            links.columnconfigure(i, weight=1, uniform="l")
            ttk.Button(links, text=f"{tr(text)} ↗", command=lambda u=url: self.open_link(u)).grid(
                row=0, column=i, sticky="ew", padx=(0 if i == 0 else 6, 0))

    def _w_cf_key(self, parent, panel: bool = False):
        """Eingabe des eigenen CurseForge-API-Keys (nur lokal gespeichert) mit Verbindungstest."""
        fs, ls = ("Panel.TFrame", "Panel.TLabel") if panel else ("TFrame", "TLabel")
        f = ttk.Frame(parent, style=fs)
        ttk.Label(f, text=tr("Dein API-Key (bleibt nur auf diesem Rechner):"), style=ls).pack(anchor="w")
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
                status.set(tr("Bitte zuerst den Key einfügen."))
                return
            status.set(tr("Teste …"))

            def work():
                try:
                    cf_request(f"/games/{CF_GAME_ID}", key)
                    return tr("✓ Verbindung steht – der Key funktioniert.")
                except Exception as e:  # noqa
                    return str(e)
            self.run_quiet(work, lambda res, err: status.set(res or str(err)))
        ttk.Button(row, text=tr("Testen"), command=test).pack(side="left", padx=(8, 0))
        ttk.Button(row, text=tr("Key holen ↗"), command=lambda: self.open_link(CF_KEY_URL)).pack(side="left", padx=(8, 0))
        ttk.Label(f, textvariable=status, style=ls, wraplength=520).pack(anchor="w", pady=(4, 0))
        ttk.Label(f, text=tr("Download-Ordner deines Browsers:"), style=ls).pack(anchor="w", pady=(10, 0))
        drow = ttk.Frame(f, style=fs)
        drow.pack(fill="x", pady=(4, 0))
        dvar = tk.StringVar(value=str(self.download_dir()))

        def dstore(*_):
            self.cfg["download_dir"] = dvar.get().strip()
            save_config(self.cfg)
        dvar.trace_add("write", dstore)
        ttk.Entry(drow, textvariable=dvar).pack(side="left", fill="x", expand=True)

        def browse():
            d = filedialog.askdirectory(title=tr("Download-Ordner wählen"), initialdir=dvar.get() or None)
            if d:
                dvar.set(d)
        ttk.Button(drow, text=tr("Durchsuchen…"), command=browse).pack(side="left", padx=(8, 0))
        ttk.Label(f, style=ls, wraplength=520, justify="left",
                  text=tr("Dort sucht Leeroyzan nach ZIP-Dateien, Listen und Paketen – und nach Downloads, die du "
                       "für gesperrte Addons im Browser lädst.")).pack(anchor="w", pady=(4, 0))
        return f

    def change_language(self, code: str):
        """Sprache umschalten – die Oberfläche wird sofort neu aufgebaut, ohne Neustart."""
        if code == LANG or code not in LANGUAGES:
            return
        self.cfg["language"] = code
        save_config(self.cfg)
        set_language(code)
        self.reload_ui()
        self.notify(tr("Sprache geändert: {0}", LANGUAGES[code]), "ok", timeout=6000)

    def reload_ui(self):
        page = self.current_page
        mode = self.cfg.get("self_update_mode", "auto")
        self.cf_sort.set(tr("Beliebtheit"))
        self.share_size.set(tr(next(iter(LIST_PART_SIZES))))
        self.self_update_var.set(tr(self.UPDATE_MODES.get(mode, self.UPDATE_MODES["auto"])))
        try:
            self.ui.destroy()
        except tk.TclError:
            pass
        self.cfg["last_page"] = page
        self._build_layout()
        flavors = list_flavors(self.root_path)
        self.flavor_cb["values"] = [flavor_label(f) for f in flavors]
        self.update_game_info()
        self.render()
        self.reload_sets()
        self.sess_char_var.set("")
        self.update_link_state()
        self.show_page(page)

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
            ttk.Label(self.cards_inner, text=tr("Keine Addons gefunden."), style="Body.TLabel").grid(
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
            ttk.Button(top, text=tr("AN") if on else tr("AUS"), style="ToggleOn.TButton" if on else "ToggleOff.TButton",
                       cursor="hand2", command=lambda ms=members: self.toggle_selected(ms)).pack(side="right")
            name = ttk.Label(top, text=title, style="CardTitle.TLabel", cursor="hand2")
            name.pack(side="left")
            folders = tr("{0} Ordner", len(members)) if len(members) > 1 else tr("1 Ordner")
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
                upd_chip = f"{UPD_MARK} Update {v.group(0)}" if v else tr("{0} Update verfügbar", UPD_MARK)
            for text, fg, bg in ((f"v{vals[3]}" if vals[3] else tr("ohne Version"), t["text"], t["pill"]),
                                 (vals[5].split(":")[0] or tr("manuell"), t["text_dim"], t["pill"]),
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
                acts.append((tr("{0} Aktualisieren", UPD_MARK), lambda ms=members: self.update_selected(ms)))
            acts += [(tr("Details"), lambda ms=members: self.select_addons(ms)),
                     (tr("Ordner"), lambda ms=members: self.open_folder(ms)),
                     (tr("Entfernen"), lambda ms=members: self.remove_selected(list(ms)))]
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
            self.notify(tr("Bitte zuerst den WoW-Ordner wählen."), "warn", [(tr("WoW-Ordner wählen"), self.browse_root), (tr("Einstellungen"), lambda: self.show_page("settings"))], key="noroot")
            return
        exe = find_wow_exe(self.root_path, self.flavor)
        if not exe:
            self.error(tr("In {0} wurde keine WoW-.exe gefunden.\n\nIst diese Version vollständig installiert?", Path(self.root_path) / self.flavor))
            return
        running = running_wow(self.root_path, self.flavor)
        if running:
            self.info(tr("WoW läuft bereits ({0}).", ', '.join(running)))
            return
        if self.busy:
            self.info(tr("Bitte warten, bis der laufende Vorgang fertig ist."))
            return
        try:
            flags = 0x00000008 | 0x00000200 if sys.platform.startswith("win") else 0  # losgelöst
            subprocess.Popen([str(exe)], cwd=str(exe.parent), close_fds=True, creationflags=flags)
        except OSError as e:
            self.error(tr("WoW konnte nicht gestartet werden:\n{0}", e))
            return
        log.info("WoW gestartet: %s", exe)
        self.set_status(tr("{0} gestartet. Falls WoW nach dem Login fragt: Battle.net-App öffnen.", exe.name))
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
            self.set_status(tr("Geöffnet: {0}", url))
        except Exception as e:  # noqa
            self.error(tr("Link konnte nicht geöffnet werden:\n{0}\n\n{1}", url, e))

    def set_status(self, text: str):
        self.status_var.set(text)

    def save_options(self):
        self.cfg["auto_backup"] = bool(self.auto_backup_var.get())
        self.cfg["check_on_start"] = bool(self.check_start_var.get())
        label = self.self_update_var.get()
        mode = next((k for k, v in self.UPDATE_MODES.items() if tr(v) == label), "auto")
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
                self.set_status(tr("Bitte zuerst die Frage oben beantworten."))
            return False
        if self.busy:
            if not quiet:
                self.info(tr("Bitte warten, ein Vorgang läuft noch."))
            return False
        if not self.root_path or not self.flavor:
            if not quiet:
                self.notify(tr("Bitte zuerst den WoW-Ordner wählen."), "warn", [(tr("WoW-Ordner wählen"), self.browse_root), (tr("Einstellungen"), lambda: self.show_page("settings"))], key="noroot")
            return False
        if modifies:
            running = running_wow(self.root_path, self.flavor)
            if running and not self.ask(
                    tr("WoW läuft gerade ({0}). Änderungen an Addons wirken erst nach /reload, und beim Ausloggen kann WoW Einstellungen überschreiben. Trotzdem fortfahren?", ', '.join(running)),
                    yes=tr("Fortfahren"), no=tr("Abbrechen")):
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
                        self.set_status(tr("Fehler: {0}", err))
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
            if self.has_update(sid) and (self.update_info.get(sid) or {}).get("best"):
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
        self.set_status(tr("CurseForge-Download für {0} geöffnet – sobald die Datei in {1} liegt, installiert Leeroyzan sie automatisch.", self.source_name(sid), self.download_dir()))

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
                self.set_status(tr("{0} passt nicht zur erwarteten Version {1} – bitte über „Update bei CurseForge herunterladen“ laden.", f.name, w['asset']['name']))
            self._dl_checked[key] = sig
        return None

    def _install_download(self, sid: str, path: Path):
        w = self.dl_watch.pop(sid)
        info, asset = w["info"], w["asset"]
        root, flavor = self.root_path, self.flavor
        before = dict(self.sources()[sid]) if sid in self.sources() else None

        def job():
            if before:
                try:
                    save_rollback(root, flavor, sid, before)
                except Exception as e:  # noqa
                    log.warning("Vorversion von %s nicht gesichert: %s", sid, e)
            return install_zip(path, root, flavor)

        def done(folders):
            self.remember_cf_source(info, asset, folders)
            self.remember_bundle(folders)
            self.updates.pop(sid, None)
            save_config(self.cfg)
            log.info("Aus dem Download-Ordner aktualisiert: %s (%s) <- %s", info.get("repo"), asset["name"], path)
            self.refresh()
            msg = tr("{0} aus deinem Download-Ordner installiert ({1}).", info.get('repo') or sid, asset['name'])
            if running_wow(root, flavor):
                msg += tr("  WoW läuft – wirkt nach /reload.")
            self.set_status(msg)
            self.notify(msg, "ok", [(tr("↶ Rückgängig"), lambda: self.rollback(sid))] if self.rollback_info(sid) else [],
                        timeout=12000)
            self.refresh_browser_list()
            self.install_dependencies([asset])

        def failed(err):
            self._dl_checked[str(path)] = self._dl_seen.get(str(path))
            self.dl_watch.setdefault(sid, w)          # weiter auf einen neuen Download warten
            if not self._dl_job:
                self._dl_job = self.after(self.DL_POLL_MS, self._poll_downloads)
            log.error("Download %s konnte nicht installiert werden: %s", path, err)
            self.set_status(tr("{0} konnte nicht installiert werden: {1}", path.name, err))

        self.run_bg(tr("Installiere {0}", path.name), job, done, failed)

    def _hint_update_link(self):
        try:
            sel = self.selected_addons()
        except tk.TclError:
            return
        if sel and len(self.tree.selection()) == 1:
            name, url = self.cf_update_link(sel)
            if url:
                self.set_status(tr("{0} Update für {1} verfügbar – „Jetzt aktualisieren“ rechts oder Rechtsklick.", UPD_MARK, name))

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
            items.append((tr("{0} Jetzt aktualisieren", UPD_MARK), lambda: self.update_selected(sel)))
        _, url = self.cf_update_link(sel)
        if url:
            items.append((tr("Bei CurseForge herunterladen ↗"), lambda: self.open_cf_update(sel)))
        sid, src = self.folder_source(main.name)
        if src:
            label = tr("CurseForge-Seite öffnen ↗") if src.get("type") == "curseforge" else tr("Release-Seite öffnen ↗")
            items.append((label, lambda: self.open_link(self.source_page(sid, src))))
            rb = self.rollback_info(sid)
            if rb:
                items.append((tr("↶ Zurück auf {0}", rb["source"].get("tag") or "?"), lambda: self.rollback(sid)))
            items.append((tr("Updates wieder erlauben") if self.is_held(sid) else tr("Festhalten (keine Updates)"),
                          lambda: self.toggle_hold(sid=sid)))
        if items:
            items.append(None)
        items += [(tr("Details"), lambda: self.show_details(main)),
                  (tr("Aktivieren / Deaktivieren"), lambda: self.toggle_selected(sel)),
                  (tr("Ordner öffnen"), lambda: self.open_folder(sel))]
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
        d = filedialog.askdirectory(title=tr("World of Warcraft-Ordner wählen"),
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
            self.set_status(tr("Keine Spielversion gefunden – stimmt der WoW-Ordner?"))
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
        self._sessions_flavor_changed()

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
            self.set_status(tr("{0} Addons in {1} gefunden.", len(self.addons), self.flavor or '–'))

    def is_outdated(self, a: Addon) -> bool:
        return bool(self.game and a.interfaces and self.game[1] not in a.interfaces)

    def row_values(self, a: Addon):
        enabled_names = {x.name.lower() for x in self.addons if x.enabled}
        missing = [d for d in a.deps if d.lower() not in enabled_names]
        outdated = self.is_outdated(a)
        if not a.valid:
            status = tr("keine .toc")
        elif not a.enabled:
            status = tr("deaktiviert")
        elif missing:
            status = tr("fehlt: {0}", ", ".join(missing))
        elif outdated:
            status = tr("veraltet")
        else:
            status = tr("aktiv")
        sid, src = self.folder_source(a.name)
        source = self.source_label(sid, src) if src else tr("manuell")
        upd = ""
        has_update = self.has_update(sid)
        if src:
            upd = (f"{UPD_MARK} {self.updates[sid]}" + ("  (Browser)" if src.get("allow_dl") is False else "")
                   if has_update else src.get("tag", "") + (tr(" · festgehalten") if self.is_held(sid) else ""))
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
        status = tr("aktiv") if on == n else (tr("deaktiviert") if on == 0 else tr("{0}/{1} aktiv", on, n))
        child_tags = [t for _, tags, _ in child_rows for t in tags]
        if "warn" in child_tags:
            status += tr("  ·  Hinweis")
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
        upd = len(self.pending_updates())
        held_n = sum(1 for r in self.held() if self.held_update(r))
        parts = [tr("{0} aktiv / {1} gesamt", active, len(self.addons))]
        if old:
            parts.append(tr("{0} veraltet", old))
        if upd:
            parts.append(tr("{0} Update(s)", upd))
        self.count_var.set("  ·  ".join(parts))
        if self.nav_buttons:
            self.nav_buttons["addons"].configure(text=tr("Meine Addons   {0}", len(self.addons)))
            self.nav_buttons["updates"].configure(text=tr("Updates   {0} {1}", UPD_MARK, upd) if upd else tr("Updates"))
        self.upd_count_var.set((tr("{0} Update(s) verfügbar", upd) if upd else
                                (tr("Alles aktuell") if self.updates else tr("Noch nicht geprüft")))
                               + (tr("  ·  {0} festgehalten", held_n) if held_n else ""))
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
            self.info(tr("Im Download-Ordner ({0}) liegt keine ZIP-Datei. Wähle eine mit „Andere …“ oder zieh sie ins Fenster.", self.download_dir()))
            return
        self.install_zip_dialog([str(self._zip_files[labels.index(self.zip_var.get())])])

    def install_cf_link(self):
        """CurseForge-Link (oder Name/ID) aus dem Feld auf „Entdecken“ installieren."""
        text = self.cf_link_var.get().strip()
        if not text:
            self.info(tr("Füge zuerst den Link zu einem CurseForge-Addon ein."))
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
                raise CFError(tr("{0}: keine ZIP-Dateien bei CurseForge gefunden.", mod.get('name', ref)))
            return cf_info(mod, files, flavor, gv)

        def done(info):
            self.cf_link_var.set("")
            self._pick_and_install_cf(info)
        self.run_bg(tr("Suche auf CurseForge"), job, done)

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
            self.cf_count.set(tr("Für die Suche brauchst du deinen CurseForge-API-Key (Einstellungen)."))
            self.need_cf_key()
            return
        self.cf_req += 1
        req, text = self.cf_req, self.cf_q.get().strip()
        sort = next((k for k in CF_SORTS if tr(k) == self.cf_sort.get()), "Beliebtheit")
        index = len(self.cf_results) if more else 0
        self.cf_count.set(tr("Suche bei CurseForge …"))
        self.cf_more_btn.state(["disabled"])
        self.cf_searching = True

        def done(res, err):
            if req != self.cf_req:
                return                                   # veraltete Antwort
            self.cf_searching = False
            if err:
                self.cf_count.set(tr("Fehler: {0}", err))
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
        self.cf_fit_chk.configure(text=tr("Nur Addons für meine Spielversion ({0})", fam) if fam
                                  else tr("Nur Addons für meine Spielversion (unbekannt)"))
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
                      values=(("✓ " if inst else "") + m.get("name", ""), authors, fmt_count(m.get("downloadCount")),
                              str(m.get("dateModified", ""))[:10], ", ".join(fams) or "–"))
            shown += 1
        keep = [i for i in sel if tv.exists(i)]
        if keep:
            tv.selection_set(keep)
        hidden = len(self.cf_results) - shown
        text = tr("{0} angezeigt", shown)
        if hidden:
            text += tr(" · {0} für andere Spielversionen ausgeblendet", hidden)
        text += tr(" · {0} Treffer", f"{self.cf_total:,}".replace(",", "." if LANG == "de" else ","))
        if not self.cf_results:
            text = tr("Nichts gefunden.") if self.cf_req else ""
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
            self.cf_det_title.configure(text=tr("Kein Addon gewählt"))
            self.cf_det_meta.configure(text="")
            self.cf_det_text.configure(text=tr("Suche oben nach einem Addon und wähle es in der Liste aus. "
                                            "Mit Strg oder Umschalt markierst du mehrere."))
            self.cf_install_btn.configure(text=tr("Installieren"))
            self.cf_install_btn.state(["disabled"])
            self.cf_page_btn.state(["disabled"])
            self.cf_file_cb.state(["disabled"])
            return
        installed = self._cf_installed()
        todo = [m for m in sel if m.get("id") not in installed]
        self.cf_page_btn.state(["!disabled"])
        if len(sel) > 1:
            self.cf_det_title.configure(text=tr("{0} Addons ausgewählt", len(sel)))
            self.cf_det_meta.configure(text="")
            self.cf_det_text.configure(text=tr("Für jedes Addon wird automatisch die passende Datei gewählt.")
                                       + (tr("\n{0} davon sind schon installiert.", len(sel) - len(todo))
                                          if len(todo) < len(sel) else ""))
            self.cf_install_btn.configure(text=tr("{0} installieren", len(todo)))
            self.cf_install_btn.state(["!disabled"] if todo else ["disabled"])
            self.cf_file_cb.state(["disabled"])
            return
        m = sel[0]
        authors = ", ".join(a.get("name", "") for a in m.get("authors") or [] if a.get("name"))
        self.cf_det_title.configure(text=m.get("name", ""))
        self.cf_det_meta.configure(text=(tr("von {0}  ·  ", authors) if authors else "")
                                   + tr("{0} Downloads  ·  ", fmt_count(m.get('downloadCount')))
                                   + str(m.get("dateModified", ""))[:10])
        summary = m.get("summary", "").strip()
        lines = [summary[:240] + ("…" if len(summary) > 240 else "")]
        if m.get("allowModDistribution") is False:
            lines.append(tr("Der Autor erlaubt keine Programm-Downloads – Leeroyzan öffnet dafür den Browser "
                         "und installiert die Datei danach automatisch."))
        self.cf_det_text.configure(text="\n\n".join(x for x in lines if x))
        inst = m.get("id") in installed
        self.cf_install_btn.configure(text=tr("✓ installiert") if inst else tr("Installieren"))
        self.cf_install_btn.state(["disabled"] if inst else ["!disabled"])
        self.cf_file_cb.state(["disabled"])
        if inst:
            return
        cached = self.cf_files_cache.get(m["id"])
        if cached:
            self._cf_fill_files(m["id"])
            return
        self.cf_file_var.set(tr("Dateien werden geladen …"))
        key, flavor, gv = self.cf_key(), self.flavor, (self.game[0] if self.game else "")

        def load():
            files = cf_files(m["id"], key)
            if not files:
                raise CFError(tr("keine ZIP-Dateien bei CurseForge gefunden"))
            return cf_info(m, files, flavor, gv)

        def done(info, err):
            if err:
                if self._cf_selected() == [m]:
                    self.cf_file_var.set(tr("Fehler: {0}", err))
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
                                   for m in sel], {}, what=tr("von CurseForge"))
            return
        info = self.cf_files_cache.get(sel[0]["id"])
        if not info:
            self.info(tr("Die Dateiliste wird noch geladen – einen Moment."))
            return
        labels = list(self.cf_file_cb["values"])
        asset = (info["assets"][labels.index(self.cf_file_var.get())] if self.cf_file_var.get() in labels
                 else info["best"])
        self._install_cf_asset(info, asset)

    # ----- Updates-Seite --------------------------------------------------------- #
    def pending_updates(self) -> list[str]:
        return [r for r in self.updates if self.has_update(r)]

    # ----- Festhalten / Rückgängig ------------------------------------------------ #
    def held(self) -> list:
        return self.cfg["held"].setdefault(self.flavor, [])

    def is_held(self, sid: str) -> bool:
        return bool(sid) and sid in self.cfg.get("held", {}).get(self.flavor, [])

    def held_update(self, sid: str) -> str:
        """Neuere Version eines festgehaltenen Addons ("" wenn keine)."""
        src, new = self.sources().get(sid), self.updates.get(sid)
        return new if src and new and new != src.get("tag") and self.is_held(sid) else ""

    def has_update(self, sid: str) -> bool:
        src, new = self.sources().get(sid), self.updates.get(sid)
        return bool(src and new and new != src.get("tag") and not self.is_held(sid))

    def toggle_hold(self, addons=None, sid: str = ""):
        if not sid:
            sel = list(addons) if addons is not None else self.selected_addons()
            if not sel:
                return
            sid = self.folder_source(self._group_main(sel).name)[0]
        if not sid or sid not in self.sources():
            return
        name, held = self.source_name(sid), self.held()
        if sid in held:
            held.remove(sid)
            msg = tr("{0}: Updates sind wieder erlaubt.", name)
        else:
            held.append(sid)
            self.dl_watch.pop(sid, None)
            msg = tr("{0} ist festgehalten und bekommt keine Updates, bis du es wieder freigibst.", name)
        save_config(self.cfg)
        log.info("Festhalten %s: %s", sid, sid in held)
        self.render()
        self.info(msg)

    def rollback_info(self, sid: str) -> dict | None:
        """Gespeicherte Vorversion – nur wenn sie sich von der installierten unterscheidet."""
        src = self.sources().get(sid) if sid else None
        rb = read_rollback(self.flavor, sid) if src else None
        return rb if rb and rb["source"].get("tag") != src.get("tag") else None

    def rollback_selected(self, addons=None):
        sel = list(addons) if addons is not None else self.selected_addons()
        if sel:
            self.rollback(self.folder_source(self._group_main(sel).name)[0])

    def rollback(self, sid: str):
        rb = self.rollback_info(sid)
        if not rb or not self.ready():
            return
        name, old_tag = self.source_name(sid), rb["source"].get("tag") or "?"
        if not self.ask(tr("{0} auf die vorherige Version {1} zurücksetzen? Danach ist das Addon festgehalten, "
                           "damit es nicht gleich wieder aktualisiert wird.", name, old_tag),
                        yes=tr("Zurücksetzen"), no=tr("Abbrechen")):
            return
        root, flavor = self.root_path, self.flavor
        current = list(self.sources()[sid].get("folders", []))

        def done(meta):
            old = dict(meta["source"])
            self._claim_folders(sid, old.get("folders", []))
            self.sources()[sid] = old
            if sid not in self.held():
                self.held().append(sid)
            self.dl_watch.pop(sid, None)
            save_config(self.cfg)
            log.info("Rückgängig: %s -> %s", sid, old.get("tag"))
            self.refresh()
            msg = tr("{0} ist zurück auf {1} und festgehalten.", name, old_tag)
            if running_wow(root, flavor):
                msg += tr("  WoW läuft – wirkt nach /reload.")
            self.set_status(msg)
            self.notify(msg, "ok", [(tr("Freigeben"), lambda: self.toggle_hold(sid=sid))], timeout=12000)

        def failed(err):
            self.set_status(tr("Fehler: {0}", err))
            self.error(str(err))

        self.run_bg(tr("Setze {0} zurück", name), lambda: restore_rollback(root, flavor, sid, current), done, failed)

    # ----- Abhängigkeiten ------------------------------------------------------------ #
    def install_dependencies(self, assets: list[dict]):
        """Installiert Addons nach, die laut CurseForge zwingend gebraucht werden und noch fehlen."""
        want = []
        for a in assets:
            for mid in (a or {}).get("deps") or []:
                if mid not in want and CF_PREFIX + str(mid) not in self.sources():
                    want.append(mid)
        key = self.cf_key()
        if not want or not key or not self.root_path or not self.flavor:
            return
        root, flavor = self.root_path, self.flavor
        gv = self.game[0] if self.game else ""
        have = {plain_key(a.name) for a in self.addons} | {plain_key(a.title) for a in self.addons}
        known = set(self.sources())

        def job():
            ok, manual, errors, seen = [], [], [], set()
            todo = list(want)
            while todo and len(seen) < 25:
                mid = todo.pop(0)
                if mid in seen or CF_PREFIX + str(mid) in known:
                    continue
                seen.add(mid)
                info, label = None, str(mid)
                try:
                    mod = cf_get_mod(mid, key)
                    label = mod.get("name") or label
                    if {plain_key(mod.get("name", "")), plain_key(mod.get("slug", ""))} & have - {""}:
                        continue                         # schon da (z. B. von Hand installiert)
                    info = cf_info(mod, cf_files(mid, key), flavor, gv)
                    if not info["best"]:
                        raise CFError(tr("keine passende Datei gefunden"))
                    got = cf_install_asset(info["best"], info, root, flavor, key)
                    ok.append((info, info["best"], got))
                    have.update(plain_key(g) for g in got)
                    todo += [d for d in info["best"].get("deps", []) if d not in seen]
                except ManualDownload:
                    manual.append((info, info["best"]))
                except Exception as e:  # noqa
                    log.error("Abhängigkeit %s: %s", label, e)
                    errors.append(f"{label}: {e}")
            return ok, manual, errors

        def done(out):
            ok, manual, errors = out
            for info, asset, got in ok:
                self.remember_cf_source(info, asset, got)
                self.remember_bundle(got)
            if ok:
                save_config(self.cfg)
                self.refresh()
                names = ", ".join(info["repo"] for info, _, _ in ok)
                log.info("Abhängigkeiten installiert: %s", names)
                self.set_status(tr("Benötigte Addons mitinstalliert: {0}", names))
                self.notify(tr("Benötigte Addons mitinstalliert: {0}", names), "ok", timeout=10000)
            if errors:
                self.warn(tr("Benötigte Addons nicht installiert:") + "\n" + "\n".join(errors))
            if manual:
                self.run_download_queue([cf_queue_item(i, a) for i, a in manual],
                                        tr("Benötigte Addons – im Browser laden:"))

        self.run_bg(tr("Installiere benötigte Addons"), job, done)

    def refresh_updates_page(self):
        if not getattr(self, "upd_tree", None):
            return
        tv = self.upd_tree
        sel = tv.selection()
        tv.delete(*tv.get_children())
        pend = self.pending_updates()
        for i, r in enumerate(sorted(pend, key=lambda r: self.source_name(r).lower())):
            src, info = self.sources()[r], self.update_info.get(r, {})
            hint = tr("Browser-Download") if r.startswith(CF_PREFIX) and info.get("allow_dl") is False else \
                ("CurseForge" if r.startswith(CF_PREFIX) else "GitHub")
            tv.insert("", "end", iid=r, tags=["update"] + (["odd"] if i % 2 else []),
                      values=(self.source_name(r), src.get("tag", "?"), f"{UPD_MARK} {self.updates[r]}", hint))
        keep = [i for i in sel if tv.exists(i)]
        if keep:
            tv.selection_set(keep)
        elif pend:
            tv.selection_set(tv.get_children()[0])
        if not pend:
            self._set_text(self.upd_notes, parts=[(tr("Alles aktuell.") + "\n", "head"),
                                                  (tr("Keine Updates bekannt.") if self.updates else
                                                   tr("Noch nicht geprüft – „Jetzt prüfen“."), "dim")])
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
        notes = (info.get("notes") or "").strip() or tr("Keine Änderungshinweise vorhanden.")
        parts = [(f"{self.source_name(r)}\n", "head"),
                 (f"{src.get('tag', '?')}  →  {self.updates.get(r, '?')}"
                  + (f"   ·   {info['published']}" if info.get("published") else "") + "\n\n", "dim")]
        if r.startswith(CF_PREFIX) and info.get("allow_dl") is False:
            parts.append((tr("Der Autor sperrt Downloads über Programme: Leeroyzan öffnet die Download-Seite und "
                          "installiert die Datei, sobald sie in deinem Download-Ordner liegt.\n\n"), "dim"))
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
                             title=tr("Im Browser laden · Leeroyzan wartet auf {0} Datei(en)", len(waiting)))
        self.browser_box = box
        ttk.Label(box, style="Panel.TLabel", wraplength=900, justify="left",
                  text=tr("Sobald die Datei in {0} liegt, wird sie automatisch installiert.", self.download_dir())).pack(
            anchor="w", pady=(0, 6))
        for sid, w in waiting:
            row = ttk.Frame(box, style="Panel.TFrame")
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=f"{w['info'].get('repo') or sid}  ·  {w['asset'].get('file_name', '')}",
                      style="Panel.TLabel").pack(side="left")
            ttk.Button(row, text=tr("Nicht mehr warten"),
                       command=lambda s=sid: (self.dl_watch.pop(s, None), self.refresh_browser_list())).pack(side="right")
            ttk.Button(row, text=tr("Download-Seite öffnen ↗"),
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
        self.share_sel_rb.configure(text=tr("Ausgewählte Addons ({0})", n_sel))
        self.share_all_rb.configure(text=tr("Alle Addons ({0})", len(self.addons)))
        if not n_sel and self.share_scope.get() == "sel":
            self.share_scope.set("all")
        self.share_sel_rb.state(["!disabled"] if n_sel else ["disabled"])
        addons = self._share_addons()
        self._share_payload, unlinked = build_list(addons, self.sources(), self.flavor, self.sets(),
                                                   self.share_exact.get(), self.share_sets.get())
        self._share_unlinked = unlinked
        if not self._share_payload["a"]:
            self._share_parts = []
            self._set_text(self.share_txt, tr("Keines dieser Addons hat eine Download-Quelle (CurseForge oder GitHub). "
                                           "Verknüpfe sie mit CurseForge oder teile sie als Paket."))
            self.share_info.set("")
            return
        size = next((v for k, v in LIST_PART_SIZES.items() if tr(k) == self.share_size.get()), 1800)
        self._share_parts = list_to_code(self._share_payload, size)
        parts = []
        for i, p in enumerate(self._share_parts, 1):
            if len(self._share_parts) > 1:
                parts.append((tr("Teil {0} von {1} – als eigene Nachricht senden:\n", i, len(self._share_parts)), "dim"))
            parts.append((p + "\n\n", ""))
        self._set_text(self.share_txt, parts=parts)
        n_c = sum(1 for e in self._share_payload["a"] if e["t"] == "c")
        info = (tr("{0} Addons ({1} CurseForge, {2} GitHub) · {3} Teil(e), {4} Zeichen.", len(self._share_payload['a']), n_c, len(self._share_payload['a']) - n_c, len(self._share_parts), sum(len(x) for x in self._share_parts)))
        if unlinked:
            info += "\n" + tr("Ohne Download-Quelle, nicht enthalten: {0} – dafür „Als Paket speichern“.",
                                 ', '.join(unlinked[:6]) + (" …" if len(unlinked) > 6 else ""))
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
                   tr("Code kopiert – einfach in Discord oder eine Nachricht einfügen.") if n == 1 else
                   tr("Alle {0} Teile kopiert (getrennt durch Leerzeilen). Sende jeden Teil als eigene Nachricht.", n))

    def copy_share_readable(self):
        if getattr(self, "_share_payload", None) and self._share_payload["a"]:
            self._clip(list_readable(self._share_payload), tr("Lesbare Liste mit Links kopiert."))

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
        self.notify(tr("Liste gespeichert: {0}", p), "ok", [(tr("Ordner öffnen"), lambda: open_in_explorer(p.parent))],
                    key="saved", timeout=20000)

    def export_share_pack(self):
        addons = self._share_addons()
        if not addons:
            self.info(tr("Es sind keine Addons installiert."))
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
            self.info(tr("Die Zwischenablage ist leer."))

    def preview_import(self):
        self._imp_job = None
        text = self.import_txt.get("1.0", "end").strip()
        self._import_data = None
        self.import_btn.state(["disabled"])
        if not text:
            self.import_preview.set("")
            self.import_btn.configure(text=tr("Installieren"))
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
                lines.append(tr("✓ {0} (schon da)", name))
            else:
                todo.append(e)
                lines.append(f"• {name}")
        head = tr("Liste mit {0} Addons ({1})", len(data['a']), data['fl'].strip('_') or '?')
        if data["fl"] and data["fl"] != self.flavor:
            head += tr(" – du installierst in {0}", self.flavor.strip('_'))
        if data["s"]:
            head += tr(" · {0} Set(s)", len(data['s']))
        self.import_preview.set(head + ":\n" + "\n".join(lines[:12]) + ("\n…" if len(lines) > 12 else ""))
        self._import_data = (todo, data["s"])
        self.import_btn.configure(text=tr("{0} Addon(s) installieren", len(todo)) if todo else tr("Alles schon installiert"))
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
            self.info(tr("Im Download-Ordner liegt keine .lzlist- oder .lzpack-Datei."))
            return
        self._open_share_file(self._recv_files[labels.index(self.recv_file_var.get())])

    def open_other_share_file(self):
        f = filedialog.askopenfilename(title=tr("Liste oder Paket öffnen"),
                                       filetypes=[(tr("Leeroyzan-Liste/-Paket"), f"*{LIST_EXT} *{PACK_EXT}"),
                                                  (tr("Alle Dateien"), "*.*")])
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
            self.error(tr("Datei konnte nicht gelesen werden: {0}", e))
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
        tv.insert("", "end", iid=SET_ALL_OFF, values=(tr(SET_ALL_OFF), "0"), tags=["dim"])
        tv.insert("", "end", iid=SET_ALL_ON, values=(tr(SET_ALL_ON), str(len(self.addons))), tags=["dim"])
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
            self.set_members.configure(text=tr("Schaltet alle Addons aus."))
        elif name == SET_ALL_ON:
            self.set_members.configure(text=tr("Schaltet alle Addons ein."))
        else:
            self.set_members.configure(text=tr("Wähle ein Set aus – oder speichere die gerade aktiven Addons "
                                            "als neues Set."))

    def refresh_backup_list(self):
        tv = getattr(self, "backup_tree", None)
        if tv is None:
            return
        tv.delete(*tv.get_children())
        self._backups = list_backups(self.flavor) if self.flavor else []
        for i, b in enumerate(self._backups):
            tv.insert("", "end", iid=str(i), values=(b["when"].strftime(tr("%d.%m.%Y  %H:%M")),
                                                     tr(BACKUP_KINDS.get(b["kind"], b["kind"]))),
                      tags=["odd"] if i % 2 else [])
        if self._backups:
            tv.selection_set("0")

    # ----- Installieren ---------------------------------------------------- #
    def install_zip_dialog(self, files=None):
        if not self.ready():
            return
        if not files:
            files = filedialog.askopenfilenames(title=tr("Addon-ZIP wählen"),
                                                filetypes=[(tr("ZIP-Archive"), "*.zip")])
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
            self.set_status(tr("Installiert: {0}", ', '.join(names)))

        self.run_bg(tr("Installiere ZIP"), job, done)

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
            self.info(tr("Gib das GitHub-Repository ein (owner/repo oder Link) und klicke auf „Installieren“."))
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
        self.run_bg(tr("Lese Release von {0}", repo), lambda: github_latest(repo, token), done)

    def need_cf_key(self) -> str:
        if not self.cf_key():
            self.notify(tr("Für CurseForge brauchst du deinen eigenen, kostenlosen API-Key. Er bleibt nur auf "
                        "diesem Rechner."), "warn", [(tr("Key eintragen"), self.settings_dialog),
                                                    (tr("Key holen ↗"), lambda: self.open_link(CF_KEY_URL))],
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
        more = tr(" Die übrigen Download-Seiten findest du unter „Updates“.") if len(items) > 1 else ""
        self.notify(tr("{0} {1}. Klicke im Browser auf den Download – sobald die Datei in {2} liegt, installiert Leeroyzan sie automatisch.{3}", intro, names, self.download_dir(), more), "warn",
                    [(tr("Zur Liste"), lambda: self.show_page("updates"))], key="browser")
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
                text = self.ask_text(tr("CurseForge-Link zu „{0}“ einfügen:", names), ok=tr("Verknüpfen"))
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
                self.info(tr("Bei den manuell installierten Addons ist keine CurseForge-Kennung "
                                              "hinterlegt.\n\nTipp: Wähle ein Addon in der Liste aus und klicke "
                                              "erneut – dann kannst du den CurseForge-Link einfügen."))
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
                        raise CFError(tr("keine ZIP-Dateien bei CurseForge gefunden"))
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
                names = "\n".join(tr("• {0}  (installiert: {1}, neueste Datei: {2})", i['repo'],
                                     ', '.join(v for v in vers if v) or '?', i['tag']) for i, _, _, vers in unsure)
                current = self.ask(tr("Bei diesen Addons lässt sich die installierte Version keiner CurseForge-Datei zuordnen:\n\n{0}\n\nSollen sie als aktuell gelten?\n\nJa = die neueste Datei gilt als installiert.\nNein = sie werden als veraltet behandelt, das nächste Update installiert die neueste Datei.", names))
                for info, _, folders, _ in unsure:
                    self.remember_cf_source(info, info["best"] if current else dict(info["best"], name="", file_id=None),
                                            folders)
                    linked += 1
            save_config(self.cfg)
            self.refresh()
            self.set_status(tr("{0} Addon(s) mit CurseForge verknüpft.", linked))
            if errors:
                self.warn(tr("Nicht verknüpft:") + "\n\n" + "\n".join(errors))
            if linked:
                self.after(200, self.check_updates)

        self.run_bg(tr("Verknüpfe mit CurseForge"), job, done)

    def _pick_and_install_cf(self, info: dict):
        best = info["best"] or info["assets"][0]
        asset = best
        if len(info["assets"]) > 1:
            labels = [a.get("label", a["name"]) for a in info["assets"]]
            idx = self.ask_choice(tr("{0} von {1} bietet mehrere Dateien an. Welche passt zu deiner Spielversion? (Die Wahl gilt auch für spätere Updates.)", info['repo'], info['author'] or 'unbekannt'),
                                  labels, info["assets"].index(best), ok=tr("Installieren"))
            if idx is None:
                self.set_status(tr("Abgebrochen."))
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
            self.set_status(tr("{0} von {1} installiert: {2}  ·  Quelle: {3}", info['repo'], info['author'] or '?', ', '.join(folders), info['page']))
            self.notify(tr("{0} von {1} ist installiert.", info['repo'], info['author'] or '?'), "ok",
                        [(tr("Projektseite ↗"), lambda: self.open_link(info["page"]))], timeout=12000)
            if getattr(self, "cf_tree", None):
                self.cf_show_rows()
            self.install_dependencies([asset])

        def failed(err):
            if isinstance(err, ManualDownload):
                self.set_status(tr("Download über den Browser nötig."))
                self.run_download_queue([cf_queue_item(info, asset)], f"{info['repo']}: {err.reason}")
            else:
                self.set_status(tr("Fehler: {0}", err))
                self.error(str(err))

        self.run_bg(tr("Lade {0}", asset['name']), lambda: cf_install_asset(asset, info, root, flavor, key),
                    done, failed)

    def _pick_and_install(self, info: dict):
        best = choose_asset(info, self.flavor)
        if not best:
            self.error(tr("{0}: Das Release enthält keine Dateien.", info['repo']))
            return
        asset = best
        if len(info["assets"]) > 1:
            labels = [a.get("label", a["name"]) for a in info["assets"]]
            idx = self.ask_choice(tr("{0} {1} bietet mehrere Dateien an. Welche passt zu deiner Spielversion? (Die Wahl gilt auch für spätere Updates.)", info['repo'], info['tag']),
                                  labels, info["assets"].index(best) if best in info["assets"] else 0,
                                  ok=tr("Installieren"))
            if idx is None:
                self.set_status(tr("Abgebrochen."))
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
            self.set_status(tr("{0} {1} installiert: {2}", info['repo'], info['tag'], ', '.join(folders)))

        self.run_bg(tr("Lade {0}", asset['name']),
                    lambda: github_install_asset(asset, root, flavor, token), done)

    # ----- Updates --------------------------------------------------------- #
    def check_updates(self, silent: bool = False):
        if not self.ready(modifies=False, quiet=silent):
            return
        repos = list(self.sources().keys())
        if not repos:
            if not silent:
                self.info(tr("Keine Addons mit Update-Quelle (GitHub/CurseForge) "
                                              "in dieser Version installiert."))
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
                                errors.append(tr("{0}: CurseForge-API-Key fehlt (Einstellungen).", s.get('name', r)))
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
                if r.startswith(CF_PREFIX) and info.get("best") and self.has_update(r):
                    self.watch_download(r)
            n = sum(1 for r in res if self.has_update(r))
            msg = tr("{0} Update(s) verfügbar.", n) if n else tr("Alle Addons sind aktuell.")
            if errors:
                msg += tr("  ({0} Fehler)", len(errors))
                log.warning("Update-Prüfung: %s", "; ".join(errors))
            self.set_status(msg)
            if errors and not silent:
                self.warn("\n".join(errors))

        def failed(err):
            self.set_status(tr("Update-Prüfung fehlgeschlagen: {0}", err))
            if not silent:
                self.error(str(err))

        self.run_bg(tr("Prüfe Updates"), job, done, failed)

    def update_all(self, only: list[str] | None = None):
        pending = [r for r in self.pending_updates() if only is None or r in only]
        if not pending:
            self.info(tr("Keine Updates bekannt – erst „Jetzt prüfen“.") if not self.updates else
                      tr("Alle Addons sind aktuell."))
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
        before = {r: dict(self.sources()[r]) for r, _, _ in plan}

        def job():
            if want_backup:      # im Hintergrund-Thread nur mit lokalen Werten arbeiten
                try:
                    log.info("Auto-Backup: %s", backup_wtf(root, flavor, "auto"))
                except FileNotFoundError:
                    pass
            ok, manual, errors = [], [], []
            for r, info, asset in plan:
                try:
                    if info.get("allow_dl", True) or not r.startswith(CF_PREFIX):
                        try:
                            save_rollback(root, flavor, r, before[r])       # für „Rückgängig“
                        except Exception as e:  # noqa – Rückgängig ist ein Extra, das Update geht vor
                            log.warning("Vorversion von %s nicht gesichert: %s", r, e)
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
            msg = tr("{0} Addon(s) aktualisiert.", len(results))
            if manual or errors:
                msg += tr("  ({0} nicht automatisch)", len(manual) + len(errors))
            self.set_status(msg)
            if results:
                acts = [(tr("↶ Rückgängig"), lambda r=results[0][0]: self.rollback(r))] \
                    if len(results) == 1 and self.rollback_info(results[0][0]) else []
                self.notify(msg, "ok", acts, timeout=12000)
            if errors:
                self.warn(tr("Nicht aktualisiert:") + "\n" + "\n".join(errors))
            if manual:
                self.run_download_queue([cf_queue_item(i, a) for i, a, _ in manual],
                                        tr("Im Browser laden (der Autor sperrt Programm-Downloads):"))
            self.install_dependencies([a for r, _, a, _ in results if r.startswith(CF_PREFIX)])

        self.run_bg(tr("Aktualisiere"), job, done)

    # ----- Auswahl / Verwalten -------------------------------------------- #
    def select_all(self, force: bool = False):
        """Wählt alle sichtbaren Addons aus; ein zweiter Klick hebt die Auswahl auf."""
        if self.tree is None:
            return
        items = self.tree.get_children()
        if not force and items and set(self.tree.selection()) == set(items):
            self.tree.selection_remove(*items)
            self.set_status(tr("Auswahl aufgehoben."))
        else:
            self.tree.selection_set(items)
            self.set_status(tr("{0} Addons ausgewählt.", len(self.selected_addons())))
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
        if not self.ask(tr("{0} wirklich entfernen? Die Ordner werden gelöscht.", names), yes=tr("Entfernen"), no=tr("Abbrechen")):
            return
        for a in sel:
            shutil.rmtree(a.folder, ignore_errors=True)
        log.info("Entfernt: %s", names)
        self.refresh()
        self.set_status(tr("Entfernt: {0}", names))

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
            self.info(tr("Es sind keine Addons aktiv."))
            return
        name = self.set_name_var.get()
        if not name or not name.strip():
            self.show_page("sets")
            self.info(tr("Gib unten einen Namen für das neue Set ein ({0} aktive Addons).", len(active)))
            return
        name = name.strip()
        if name in (SET_ALL_OFF, SET_ALL_ON):
            return
        if name in self.sets() and not self.ask(tr("Set „{0}“ überschreiben?", name)):
            return
        self.sets()[name] = active
        save_config(self.cfg)
        self.set_name_var.set("")
        log.info("Set gespeichert: %s (%d Addons)", name, len(active))
        self.reload_sets(name)
        self.refresh_sets_overview()
        self.set_status(tr("Set „{0}“ mit {1} Addons gespeichert.", name, len(active)))

    def delete_set(self):
        name = self.set_var.get()
        if name not in self.sets():
            return
        if self.ask(tr("Set „{0}“ löschen? Die Addons selbst bleiben erhalten.", name), yes=tr("Löschen"), no=tr("Abbrechen")):
            del self.sets()[name]
            save_config(self.cfg)
            log.info("Set gelöscht: %s", name)
            self.reload_sets()
            self.refresh_sets_overview()

    def apply_set(self):
        name = self.set_var.get()
        if not name:
            self.info(tr("Wähle zuerst ein Set aus."))
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
        msg = tr("Set „{0}“ angewendet: {1} Änderung(en).", name, changes)
        if missing:
            msg += tr("  Nicht installiert: {0}", ', '.join(missing))
        self.set_status(msg)
        if errors:
            self.error("\n".join(errors))

    # ----- Teilen ---------------------------------------------------------- #
    def export_pack_dialog(self, addons=None):
        if not self.ready(modifies=False):
            return
        sel = list(addons) if addons is not None else self.selected_addons()
        if not sel:
            self.info(tr("Bitte zuerst die Addons auswählen, die du teilen willst (Strg+Klick oder „Alle auswählen“)."))
            return
        by_name = {a.name.lower(): a for a in self.addons}
        chosen = {a.name.lower() for a in sel}
        missing = sorted({d for a in sel for d in a.deps
                          if d.lower() in by_name and d.lower() not in chosen}, key=str.lower)
        if missing and self.ask(tr("Diese Addons werden von deiner Auswahl benötigt:") + "\n\n"
                          + "\n".join(missing) + "\n\n" + tr("Mit ins Paket nehmen?")):
            sel += [by_name[d.lower()] for d in missing]

        dest = self._unique_path(f"Addons_{datetime.now():%Y-%m-%d}", PACK_EXT)
        sources, flavor = dict(self.sources()), self.flavor

        n_cf = sum(1 for a in sel if any(a.name in v.get("folders", []) and v.get("type") == "curseforge"
                                         for v in sources.values()))

        def done(path):
            size = path.stat().st_size / 1024 / 1024
            log.info("Paket exportiert: %s (%d Addons)", path, len(sel))
            self.set_status(tr("{0} Addons exportiert ({1:.1f} MB): {2}", len(sel), size, path))
            extra = (tr(" {0} CurseForge-Addon(s) sind nur als Verweis enthalten – dein Freund lädt sie mit seinem eigenen API-Key.", n_cf) if n_cf else "")
            self.notify(tr("Paket mit {0} Addons gespeichert ({1:.1f} MB): {2}.{3}", len(sel), size, path, extra), "ok",
                        [(tr("Ordner öffnen"), lambda: open_in_explorer(path.parent))], key="saved", timeout=25000)

        self.run_bg(tr("Exportiere Paket"), lambda: export_pack(sel, sources, flavor, Path(dest)), done)

    def _run_list_import(self, todo: list[dict], sets: dict, what: str | None = None):
        what = what or tr("aus der Liste")
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
                            raise ValueError(tr("Das Release enthält keine Dateien."))
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
                    target = tr("{0} (importiert)", name)
                self.sets()[target] = members
            save_config(self.cfg)
            log.info("Download-Liste importiert: %s", ", ".join(installed))
            self.refresh()
            self.reload_sets()
            self.refresh_sets_overview()
            msg = tr("{0} Addon(s) {1} installiert.", len(ok), what)
            if sets:
                msg += tr("  {0} Set(s) übernommen.", len(sets))
            self.set_status(msg)
            if ok:
                self.notify(msg, "ok", timeout=10000)
            if getattr(self, "cf_tree", None):
                self.cf_show_rows()
            if errors:
                self.warn(tr("Nicht installiert:") + "\n" + "\n".join(errors))
            if manual:
                self.run_download_queue([cf_queue_item(i, a) for i, a, _ in manual],
                                        tr("Im Browser laden (der Autor sperrt Programm-Downloads):"))
            self.install_dependencies([a for repo, _, a, _ in ok if repo is None])

        self.run_bg(tr("Lade Addons {0}", what), job, done)

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
            note = tr("ersetzt deine Version {0}", old.version or '?') if old else tr("neu")
            if (e.get("source") or {}).get("type") == "curseforge":
                note += tr(", wird von CurseForge geladen")
            lines.append(f"• {e.get('title') or e['folder']}  ({ver}) – {note}")
        text = (tr("Paket vom {0} ({1}), {2} Addons:", manifest.get('created', '?')[:10], manifest.get('flavor', '?'), len(entries)) + "\n"
                + "\n".join(lines[:10]) + ("\n…" if len(lines) > 10 else ""))
        if manifest.get("flavor") and manifest["flavor"] != self.flavor:
            text += (tr("\n\nHinweis: Das Paket wurde für {0} erstellt, du installierst es in {1}.", manifest['flavor'], self.flavor))
        if not self.ask(text, yes=tr("Installieren"), no=tr("Abbrechen")):
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
            self.set_status(tr("{0} Addons aus Paket installiert.", len(installed)))
            if errors:
                self.warn(tr("Nicht installiert:") + "\n\n" + "\n".join(errors))
            if manual:
                self.run_download_queue([cf_queue_item(i, a) for i, a, _ in manual],
                                        tr("Im Browser laden (der Autor sperrt Programm-Downloads):"))

        self.run_bg(tr("Importiere Paket"), job, done)

    # ----- Backups --------------------------------------------------------- #
    def backup(self):
        if not self.ready(modifies=False):
            return
        root, flavor = self.root_path, self.flavor

        def done(path):
            log.info("Backup erstellt: %s", path)
            self.refresh_backup_list()
            self.update_stats()
            self.set_status(tr("Backup erstellt: {0}", path))
            self.notify(tr("Backup erstellt: {0}", path.name), "ok", [(tr("Ordner öffnen"), lambda: open_in_explorer(path.parent))],
                        key="backup", timeout=15000)

        self.run_bg(tr("Sichere WTF"), lambda: backup_wtf(root, flavor), done)

    def restore_dialog(self):
        if not self.root_path or not self.flavor:
            return
        backups = list_backups(self.flavor)
        if not backups:
            self.info(tr("Für {0} gibt es noch keine Backups – lege eins mit „Jetzt sichern“ an.", self.flavor))
            return
        self.show_page("sets")
        sel = self.backup_tree.selection()
        if not sel:
            self.info(tr("Wähle in der Liste das Backup aus, das du zurückholen willst."))
            return
        chosen = self._backups[int(sel[0])]
        if not self.ask(tr("Stand vom {0:%d.%m.%Y %H:%M} wiederherstellen? Dein aktueller Stand wird vorher automatisch gesichert.", chosen['when']), yes=tr("Wiederherstellen"), no=tr("Abbrechen")):
            return
        if not self.ready():
            return
        root, flavor = self.root_path, self.flavor

        def done(safety):
            log.info("Backup wiederhergestellt: %s (vorher gesichert: %s)", chosen["path"], safety)
            self.refresh_backup_list()
            self.update_stats()
            self.set_status(tr("Stand vom {0:%d.%m.%Y %H:%M} wiederhergestellt.", chosen['when']))
            self.notify(tr("Der Stand vom {0:%d.%m.%Y %H:%M} ist wiederhergestellt. Dein vorheriger Stand wurde als „vor Wiederherstellung“ gesichert.", chosen['when']), "ok", timeout=15000)

        self.run_bg(tr("Stelle Backup wieder her"),
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
            self.set_status(tr("✓ Leeroyzan wurde auf v{0} aktualisiert.", APP_VERSION))
            self.notify(tr("Leeroyzan wurde auf v{0} aktualisiert.", APP_VERSION), "ok", timeout=12000)
            return
        log.error("Manager-Update auf %s wurde nicht eingesetzt (läuft weiter v%s)", pending.get("to"), APP_VERSION)
        if self.ask(tr("Das Update auf {0} wurde geladen, konnte aber nicht eingesetzt werden – es läuft weiter v{1}.\n\nLeeroyzan liegt hier:\n{2}\n\nHäufige Ursachen: ein geschützter Ordner (z. B. „Programme“), ein Virenscanner oder OneDrive sperrt die Datei. Lege Leeroyzan.exe am besten in einen eigenen Ordner, z. B. Dokumente\\Leeroyzan.\n\nDetails stehen in update.log.\n\nRelease-Seite öffnen, um die neue Version einmalig von Hand zu laden?", pending.get('to'), APP_VERSION, Path(sys.executable).parent), icon="warning"):
            self.open_link(f"https://github.com/{UPDATE_REPO}/releases/latest")

    def check_manager_update(self, silent: bool = False):
        if self.busy:
            if silent:                       # z. B. während der Addon-Prüfung beim Start
                self.after(2000, lambda: self.check_manager_update(silent=True))
            else:
                self.info(tr("Bitte warten, ein Vorgang läuft noch."))
            return
        token = self.cfg.get("github_token", "")

        def done(info):
            if not info:
                self.set_status(tr("Der Manager ist aktuell (v{0}).", APP_VERSION))
                self.manager_state.set(tr("Installiert: v{0}  ·  das ist die neueste Version.", APP_VERSION))
                if not silent:
                    self.notify(tr("Du nutzt bereits die neueste Version (v{0}).", APP_VERSION), "ok", timeout=8000)
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
                self.set_status(tr("Manager-Update {0} verfügbar – „git pull“ in deiner Kopie.", info['tag']))
                return
            if silent and info["tag"] == self.cfg.get("skip_manager_version"):
                self.set_status(tr("Manager-Update {0} verfügbar (übersprungen).", info['tag']))
                return
            self.offer_manager_update(info, silent)

        def failed(err):
            if isinstance(err, NoReleaseYet):
                log.info("Manager-Update: noch kein Release in %s", UPDATE_REPO)
                self.set_status(tr("Für den Manager gibt es noch kein Release auf GitHub."))
                if not silent:
                    self.info(tr("Im Repository {0} wurde noch kein Release veröffentlicht.", UPDATE_REPO))
                return
            log.warning("Manager-Update-Prüfung fehlgeschlagen: %s", err)
            self.set_status(tr("Manager-Update-Prüfung fehlgeschlagen."))
            if not silent:
                msg = str(err)
                if isinstance(err, urllib.error.HTTPError) and err.code == 403:
                    msg = tr("GitHub hat die Anfrage abgelehnt (Limit erreicht?). Bitte in einer Stunde erneut versuchen.")
                elif isinstance(err, urllib.error.URLError):
                    msg = tr("Keine Verbindung zu GitHub ({0}).", err.reason)
                self.error(tr("Update-Prüfung fehlgeschlagen:\n{0}", msg))

        self.run_bg(tr("Suche Manager-Update"), lambda: check_self_update(token), done, failed)

    def offer_manager_update(self, info: dict, silent: bool):
        frozen, ready = is_frozen(), update_ready(info)
        self.manager_info = info
        notes = (info.get("notes") or "").strip()
        self.manager_state.set(tr("Installiert: v{0}  ·  neu: {1}", APP_VERSION, info['tag'])
                               + (tr(" vom {0}", info['published']) if info.get("published") else "")
                               + (f"\n\n{notes[:600]}" + ("…" if len(notes) > 600 else "") if notes else ""))
        if ready:
            actions = [(tr("Jetzt installieren"), lambda: self.install_manager_update(info))]
            text = tr("Leeroyzan {0} ist da (du hast v{1}). Installieren dauert ein paar Sekunden – danach startet Leeroyzan neu.", info['tag'], APP_VERSION)
        elif frozen:
            actions = [(tr("Installieren, sobald fertig"), lambda: self.watch_manager_build(info["tag"], manual=True))]
            text = (tr("Leeroyzan {0} ist veröffentlicht – GitHub baut die .exe gerade noch (meist 3–5 Minuten).", info['tag']))
        else:
            actions = [(tr("Release-Seite ↗"), lambda: self.open_link(info.get("page") or
                                                                  f"https://github.com/{UPDATE_REPO}/releases"))]
            text = tr("Leeroyzan {0} ist da – in deiner Git-Kopie mit „git pull“ holen.", info['tag'])
        actions.append((tr("Was ist neu?"), lambda: (self.show_page("updates"), self.after(
            100, lambda: self.offer_manager_update(info, silent)))))
        if silent:
            def skip():
                self.cfg["skip_manager_version"] = info["tag"]
                save_config(self.cfg)
            actions.append((tr("Überspringen"), skip))
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
            self.set_status(tr("Für {0} ist auf GitHub keine Leeroyzan.exe erschienen.", tag))
            if self._watch_manual:
                self.notify(tr("Für {0} ist auch nach 30 Minuten keine Leeroyzan.exe am Release – wahrscheinlich ist der automatische Build auf GitHub fehlgeschlagen (rotes X unter „Actions“, dort „Re-run jobs“).", tag), "warn",
                            [(tr("Actions-Seite ↗"), lambda: self.open_link(f"https://github.com/{UPDATE_REPO}/actions"))],
                            key="manager")
            return
        mins = tries * self.BUILD_POLL_MS // 60_000
        self.manager_state.set(tr("Installiert: v{0}  ·  {1} wird auf GitHub noch gebaut", APP_VERSION, tag)
                               + (tr(" (seit {0} min)", mins) if mins else "") + tr(" – Installation folgt automatisch."))
        self.set_status(tr("{0} Manager-Update {1}: GitHub baut die .exe noch", UPD_MARK, tag)
                        + (tr(" (wartet seit {0} min)", mins) if mins else "")
                        + tr(" – wird automatisch installiert, sobald sie fertig ist."))
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
                self.error(tr("Update konnte nicht gestartet werden:\n{0}", e))
                return
            self.destroy()

        def failed(err):
            log.error("Manager-Update fehlgeschlagen: %s", err)
            if isinstance(err, PermissionError):
                err = (tr("Keine Schreibrechte im Ordner {0}.\nLege Leeroyzan.exe in einen eigenen Ordner, z. B. Dokumente\\Leeroyzan.", Path(sys.executable).parent))
            if automatic:
                self.set_status(tr("Automatisches Manager-Update fehlgeschlagen – siehe Log."))
                if self.cfg.get("self_update_failed_tag") == info["tag"]:
                    return                    # pro Version nur einmal melden
                self.cfg["self_update_failed_tag"] = info["tag"]
                save_config(self.cfg)
            self.notify(tr("Das Leeroyzan-Update ist fehlgeschlagen: {0}", err), "error",
                        [(tr("Release-Seite ↗"), lambda: self.open_link(info.get("page") or
                                                                    f"https://github.com/{UPDATE_REPO}/releases"))],
                        key="manager")

        label = (tr("Neue Version {0} gefunden – wird installiert, der Manager startet gleich neu", info['tag'])
                 if automatic else tr("Lade Manager {0}", info['tag']))
        fetch = download_self_update if is_frozen() else download_script_update
        self.run_bg(label, lambda: fetch(info, token), done, failed)

    # ----- Drag & Drop ----------------------------------------------------- #
    def on_drop(self, event):
        paths = [Path(p) for p in self.tk.splitlist(event.data)]
        zips = [str(p) for p in paths if p.suffix.lower() == ".zip"]
        packs = [str(p) for p in paths if p.suffix.lower() == PACK_EXT]
        lists = [str(p) for p in paths if p.suffix.lower() == LIST_EXT]
        if not zips and not packs and not lists:
            self.set_status(tr("Nur .zip-, .lzpack- und .lzlist-Dateien können abgelegt werden."))
            return
        if lists:
            self.after(50, lambda: self.import_list_dialog(lists[0]))
        elif packs:
            self.after(50, lambda: self.import_pack_dialog(packs[0]))
        elif zips:
            self.after(50, lambda: self.install_zip_dialog(zips))


# --------------------------------------------------------------------------- #
# Übersetzungen (Englisch)
# Schlüssel ist der deutsche Text aus tr("…"); fehlt ein Eintrag, bleibt es Deutsch.
# --------------------------------------------------------------------------- #

# Übersetzungen: deutscher Originaltext -> English
TRANSLATIONS["en"] = {
    "  ·  {0} festgehalten":
        "  ·  {0} held",
    " (neu: {0})":
        " (new: {0})",
    " · festgehalten":
        " · held",
    "%d.%m.%Y  %H:%M":
        "%Y-%m-%d  %H:%M",
    "Benötigte Addons mitinstalliert: {0}":
        "Required addons installed as well: {0}",
    "Benötigte Addons nicht installiert:":
        "Required addons not installed:",
    "Benötigte Addons – im Browser laden:":
        "Required addons – download in browser:",
    "Datenordner öffnen":
        "Open data folder",
    "Einstellungen, Logs und Backups liegen in {0}.":
        "Settings, logs and backups are stored in {0}.",
    "Festhalten":
        "Hold",
    "Festhalten (keine Updates)":
        "Hold (no updates)",
    "Freigeben":
        "Allow updates",
    "Für dieses Addon ist keine vorherige Version gespeichert.":
        "No previous version is stored for this addon.",
    "Installiere benötigte Addons":
        "Installing required addons",
    "Portabel – Einstellungen, Logs und Backups liegen im Ordner {0} neben der .exe.":
        "Portable – settings, logs and backups are stored in the {0} folder next to the .exe.",
    "Quelle ↗":
        "Source ↗",
    "Setze {0} zurück":
        "Rolling back {0}",
    "Updates wieder erlauben":
        "Allow updates again",
    "Zurücksetzen":
        "Roll back",
    "fehlt: {0}":
        "missing: {0}",
    "{0} auf die vorherige Version {1} zurücksetzen? Danach ist das Addon festgehalten, damit es nicht gleich wieder aktualisiert wird.":
        "Roll {0} back to the previous version {1}? The addon will then be held so it isn't updated again right away.",
    "{0} ist festgehalten und bekommt keine Updates, bis du es wieder freigibst.":
        "{0} is held and gets no updates until you allow them again.",
    "{0} ist zurück auf {1} und festgehalten.":
        "{0} is back on {1} and held.",
    "{0}: Updates sind wieder erlaubt.":
        "{0}: updates are allowed again.",
    "↶ Rückgängig":
        "↶ Undo",
    "↶ Zurück auf {0}":
        "↶ Back to {0}",
    "Bitte 'owner/repo' oder eine GitHub-URL angeben.":
        "Please enter 'owner/repo' or a GitHub URL.",
    "Bitte den CurseForge-Link (…/wow/addons/name) oder den Namen des Addons angeben.":
        "Please enter the CurseForge link (…/wow/addons/name) or the addon's name.",
    "Addon-Liste für WoW {0} – {1} Addons (Leeroyzan)":
        "Addon list for WoW {0} – {1} addons (Leeroyzan)",
    "{0} Addons ({1} CurseForge, {2} GitHub) · {3} Teil(e), {4} Zeichen.":
        "{0} addons ({1} CurseForge, {2} GitHub) · {3} part(s), {4} characters.",
    "Liste mit {0} Addons ({1})":
        "List with {0} addons ({1})",
    "Set „{0}“ angewendet: {1} Änderung(en).":
        "Set “{0}” applied: {1} change(s).",
    "Kein WTF-Ordner gefunden: {0}":
        "No WTF folder found: {0}",
    "Paket stammt aus einer anderen Manager-Version (Format {0}). Bitte Manager aktualisieren.":
        "This package comes from a different manager version (format {0}). Please update the manager.",
    "Paket ist zu groß (über 1 GB entpackt).":
        "The package is too large (over 1 GB unpacked).",
    "Paket enthält keine Addons.":
        "The package contains no addons.",
    "Kein CurseForge-API-Key hinterlegt (Einstellungen).":
        "No CurseForge API key set (Settings).",
    "Kein WoW-Addon „{0}“ bei CurseForge gefunden.":
        "No WoW addon “{0}” found on CurseForge.",
    "keine passende Datei gefunden":
        "no matching file found",
    "Der Autor erlaubt keinen Download über Programme wie diesen.":
        "The author does not allow downloads through programs like this one.",
    "Für diese Datei ist kein direkter Download freigegeben.":
        "No direct download is available for this file.",
    "Unerwartete Download-Adresse ({0}) – Abbruch.":
        "Unexpected download address ({0}) – aborted.",
    "Prüfsumme der heruntergeladenen Datei stimmt nicht – Abbruch.":
        "Checksum of the downloaded file does not match – aborted.",
    "Keine Download-Liste erkannt. Der Code beginnt mit „LZ1:“.":
        "No download list detected. The code starts with “LZ1:”.",
    "Es fehlt Teil {0} von {1}.":
        "Part {0} of {1} is missing.",
    "Unbekanntes oder zu neues Listenformat – bitte den Manager aktualisieren.":
        "Unknown or newer list format – please update the manager.",
    "Die Liste ist zu lang.":
        "The list is too long.",
    "Die Liste enthält keine installierbaren Addons.":
        "The list contains no installable addons.",
    "Das Release enthält noch keine {0}.":
        "The release does not contain {0} yet.",
    "Die heruntergeladene Datei ist keine gültige Windows-Anwendung.":
        "The downloaded file is not a valid Windows application.",
    "Zum Release gehört noch keine Prüfsumme – der Build ist wohl noch nicht fertig.":
        "The release has no checksum yet – the build is probably not finished.",
    "Prüfsumme stimmt nicht – Download beschädigt. Bitte später erneut versuchen.":
        "Checksum does not match – download damaged. Please try again later.",
    "Die geladene Datei passt nicht zur Release-Version.":
        "The downloaded file does not match the release version.",
    "Auswählen":
        "Select",
    "▶  Spielen":
        "▶  Play",
    "Spielversion {0}  ·  Interface {1}":
        "Game version {0}  ·  Interface {1}",
    "Spielversion unbekannt":
        "Game version unknown",
    "Addons & UI":
        "Addons & UI",
    "Meine Addons":
        "My addons",
    "Entdecken":
        "Discover",
    "Addons finden und installieren":
        "Find and install addons",
    "Addon-Name, z. B. Details, Questie, Bagnon …":
        "Addon name, e.g. Details, Questie, Bagnon …",
    "Updates":
        "Updates",
    "Addons aktuell halten":
        "Keep addons up to date",
    "Teilen":
        "Share",
    "Addons mit Freunden teilen":
        "Share addons with friends",
    "Eine Download-Liste enthält nur Verweise (CurseForge/GitHub) und passt als Text in Discord oder eine Kurznachricht. Ein Paket (.lzpack) enthält zusätzlich die Dateien von ZIP- und GitHub-Addons.":
        "A download list contains only references (CurseForge/GitHub) and fits as text into Discord or a short message. A package (.lzpack) also contains the files of ZIP and GitHub addons.",
    "Sets & Backups":
        "Sets & backups",
    "Addon-Gruppen und Sicherungen":
        "Addon groups and backups",
    "Name für ein neues Set":
        "Name for a new set",
    "Einstellungen":
        "Settings",
    "Sprache geändert: {0}":
        "Language changed: {0}",
    "Beliebtheit":
        "Popularity",
    "{0} gestartet. Falls WoW nach dem Login fragt: Battle.net-App öffnen.":
        "{0} started. If WoW asks for a login: open the Battle.net app.",
    "CurseForge-Download für {0} geöffnet – sobald die Datei in {1} liegt, installiert Leeroyzan sie automatisch.":
        "CurseForge download for {0} opened – as soon as the file is in {1}, Leeroyzan installs it automatically.",
    "{0} aus deinem Download-Ordner installiert ({1}).":
        "{0} installed from your download folder ({1}).",
    "Installiere {0}":
        "Installing {0}",
    "keine .toc":
        "no .toc",
    "manuell":
        "manual",
    "aktiv":
        "active",
    "  ·  Hinweis":
        "  ·  Note",
    "{0} aktiv / {1} gesamt":
        "{0} active / {1} total",
    "Suche auf CurseForge":
        "Search on CurseForge",
    "Suche bei CurseForge …":
        "Searching CurseForge …",
    " · {0} für andere Spielversionen ausgeblendet":
        " · {0} hidden for other game versions",
    "Keine Änderungshinweise vorhanden.":
        "No change notes available.",
    "Liste gespeichert: {0}":
        "List saved: {0}",
    " – du installierst in {0}":
        " – you are installing into {0}",
    " · {0} Set(s)":
        " · {0} set(s)",
    "Installiere ZIP":
        "Installing ZIP",
    "Lese Release von {0}":
        "Reading release of {0}",
    " Die übrigen Download-Seiten findest du unter „Updates“.":
        " You'll find the remaining download pages under “Updates”.",
    "{0} {1}. Klicke im Browser auf den Download – sobald die Datei in {2} liegt, installiert Leeroyzan sie automatisch.{3}":
        "{0} {1}. Click the download in your browser – as soon as the file is in {2}, Leeroyzan installs it automatically.{3}",
    "Verknüpfe mit CurseForge":
        "Linking with CurseForge",
    "Prüfe Updates":
        "Checking updates",
    "{0} Addon(s) aktualisiert.":
        "{0} addon(s) updated.",
    "Aktualisiere":
        "Updating",
    "Entfernt: {0}":
        "Removed: {0}",
    "Set „{0}“ mit {1} Addons gespeichert.":
        "Set “{0}” saved with {1} addons.",
    "Set „{0}“ löschen? Die Addons selbst bleiben erhalten.":
        "Delete set “{0}”? The addons themselves are kept.",
    "  Nicht installiert: {0}":
        "  Not installed: {0}",
    "Exportiere Paket":
        "Exporting package",
    "aus der Liste":
        "from the list",
    "{0} Addon(s) {1} installiert.":
        "{0} addon(s) installed {1}.",
    "Lade Addons {0}":
        "Downloading addons {0}",
    "\n\nHinweis: Das Paket wurde für {0} erstellt, du installierst es in {1}.":
        "\n\nNote: the package was created for {0}, you are installing it into {1}.",
    "Importiere Paket":
        "Importing package",
    "Stelle Backup wieder her":
        "Restoring backup",
    "Das Update auf {0} wurde geladen, konnte aber nicht eingesetzt werden – es läuft weiter v{1}.\n\nLeeroyzan liegt hier:\n{2}\n\nHäufige Ursachen: ein geschützter Ordner (z. B. „Programme“), ein Virenscanner oder OneDrive sperrt die Datei. Lege Leeroyzan.exe am besten in einen eigenen Ordner, z. B. Dokumente\\Leeroyzan.\n\nDetails stehen in update.log.\n\nRelease-Seite öffnen, um die neue Version einmalig von Hand zu laden?":
        "The update to {0} was downloaded but could not be applied – v{1} keeps running.\n\nLeeroyzan is located here:\n{2}\n\nCommon causes: a protected folder (e.g. “Program Files”), an antivirus or OneDrive is locking the file. It's best to put Leeroyzan.exe in a folder of its own, e.g. Documents\\Leeroyzan.\n\nDetails are in update.log.\n\nOpen the release page to download the new version manually once?",
    "Suche Manager-Update":
        "Checking for manager update",
    "Leeroyzan {0} ist da (du hast v{1}). Installieren dauert ein paar Sekunden – danach startet Leeroyzan neu.":
        "Leeroyzan {0} is available (you have v{1}). Installing takes a few seconds – then Leeroyzan restarts.",
    "Neue Version {0} gefunden – wird installiert, der Manager startet gleich neu":
        "New version {0} found – installing, the manager will restart shortly",
    "Unsicherer Pfad im Archiv: {0}":
        "Unsafe path in archive: {0}",
    "Im Archiv wurde kein Addon (Ordner mit passender .toc) gefunden.":
        "No addon (folder with a matching .toc) was found in the archive.",
    "Das Backup enthält keinen WTF-Ordner.":
        "The backup contains no WTF folder.",
    "Das ist kein gültiges Addon-Paket.":
        "This is not a valid addon package.",
    "Ungültiger Addon-Name im Paket: {0!r}":
        "Invalid addon name in package: {0!r}",
    "CurseForge lehnt den API-Key ab. Bitte den Key in den Einstellungen prüfen.":
        "CurseForge rejects the API key. Please check the key in Settings.",
    "CurseForge verweigert den Zugriff (API-Key ungültig oder Download nicht erlaubt).":
        "CurseForge denies access (invalid API key or download not allowed).",
    "Bei CurseForge nicht gefunden.":
        "Not found on CurseForge.",
    "Zu viele Anfragen an CurseForge – bitte in ein paar Minuten erneut versuchen.":
        "Too many requests to CurseForge – please try again in a few minutes.",
    "CurseForge nicht erreichbar: {0}":
        "CurseForge not reachable: {0}",
    "Unerwartete Antwort von CurseForge.":
        "Unexpected response from CurseForge.",
    "Die Teile gehören zu verschiedenen Listen.":
        "The parts belong to different lists.",
    "Die Liste ist ungewöhnlich groß und wird nicht geladen.":
        "The list is unusually large and will not be loaded.",
    "Der Code ist unvollständig oder beschädigt. Bitte alle Teile erneut kopieren.":
        "The code is incomplete or damaged. Please copy all parts again.",
    "Die Datei ist keine gültige Download-Liste.":
        "The file is not a valid download list.",
    "Keine Schreibrechte in {0}. Verschiebe den Manager z. B. in deinen Benutzerordner oder lade das Update von der Release-Seite.":
        "No write permission in {0}. Move the manager, e.g. into your user folder, or download the update from the release page.",
    "Die geladene Datei ist kein gültiges Skript.":
        "The downloaded file is not a valid script.",
    "Liste":
        "List",
    "Kacheln":
        "Tiles",
    "Alle auswählen":
        "Select all",
    "An / Aus":
        "On / Off",
    "{0}  Jetzt aktualisieren":
        "{0}  Update now",
    "Bei CurseForge herunterladen ↗":
        "Download from CurseForge ↗",
    "Mit CurseForge verknüpfen":
        "Link with CurseForge",
    "Ordner öffnen":
        "Open folder",
    "Entfernen":
        "Remove",
    "Direkt installieren":
        "Install directly",
    "CurseForge-Link":
        "CurseForge link",
    "z. B. {0} oder nur leatrix-plus":
        "e.g. {0} or just leatrix-plus",
    "owner/repo oder Link zum Repository":
        "owner/repo or link to the repository",
    "CurseForge durchsuchen · nur WoW":
        "Search CurseForge · WoW only",
    "Nur Addons für meine Spielversion":
        "Only addons for my game version",
    "Installieren":
        "Install",
    "Auf CurseForge ansehen ↗":
        "View on CurseForge ↗",
    "Mehr laden":
        "Load more",
    "Was ist neu?":
        "What's new?",
    "{0}  Dieses aktualisieren":
        "{0}  Update this one",
    "Im Browser laden ↗":
        "Get in browser ↗",
    "Projektseite ↗":
        "Project page ↗",
    "Manuell installierte Addons":
        "Manually installed addons",
    "Leeroyzan selbst":
        "Leeroyzan itself",
    "Senden":
        "Send",
    "Ausgewählte Addons":
        "Selected addons",
    "Alle Addons":
        "All addons",
    "Empfangen":
        "Receive",
    "Addon-Sets":
        "Addon sets",
    "Backups · WTF-Ordner":
        "Backups · WTF folder",
    "World of Warcraft":
        "World of Warcraft",
    "Allgemein":
        "General",
    "Hilfe":
        "Help",
    "Log öffnen":
        "Open log",
    "Addon-Ordner öffnen":
        "Open addon folder",
    "Neu einlesen":
        "Rescan",
    "{0} Ordner":
        "{0} folders",
    "1 Ordner":
        "1 folder",
    "Bitte zuerst den WoW-Ordner wählen.":
        "Please choose the WoW folder first.",
    "In {0} wurde keine WoW-.exe gefunden.\n\nIst diese Version vollständig installiert?":
        "No WoW .exe was found in {0}.\n\nIs this version fully installed?",
    "WoW läuft bereits ({0}).":
        "WoW is already running ({0}).",
    "Bitte warten, bis der laufende Vorgang fertig ist.":
        "Please wait until the current task has finished.",
    "Geöffnet: {0}":
        "Opened: {0}",
    "  WoW läuft – wirkt nach /reload.":
        "  WoW is running – takes effect after /reload.",
    "{0} konnte nicht installiert werden: {1}":
        "{0} could not be installed: {1}",
    "CurseForge-Seite öffnen ↗":
        "Open CurseForge page ↗",
    "Release-Seite öffnen ↗":
        "Open release page ↗",
    "Details":
        "Details",
    "World of Warcraft-Ordner wählen":
        "Choose World of Warcraft folder",
    "Keine Spielversion gefunden – stimmt der WoW-Ordner?":
        "No game version found – is the WoW folder correct?",
    "{0} Addons in {1} gefunden.":
        "{0} addons found in {1}.",
    "deaktiviert":
        "disabled",
    "{0}/{1} aktiv":
        "{0}/{1} active",
    "{0} Update(s) verfügbar":
        "{0} update(s) available",
    "Im Download-Ordner ({0}) liegt keine ZIP-Datei. Wähle eine mit „Andere …“ oder zieh sie ins Fenster.":
        "There is no ZIP file in the download folder ({0}). Choose one with “Other …” or drag it into the window.",
    "Füge zuerst den Link zu einem CurseForge-Addon ein.":
        "First paste the link to a CurseForge addon.",
    "Für die Suche brauchst du deinen CurseForge-API-Key (Einstellungen).":
        "To search you need your CurseForge API key (Settings).",
    "Nichts gefunden.":
        "Nothing found.",
    "Der Autor erlaubt keine Programm-Downloads – Leeroyzan öffnet dafür den Browser und installiert die Datei danach automatisch.":
        "The author doesn't allow program downloads – Leeroyzan opens the browser for it and then installs the file automatically.",
    "Die Dateiliste wird noch geladen – einen Moment.":
        "The file list is still loading – one moment.",
    "Browser-Download":
        "Browser download",
    "Im Browser laden · Leeroyzan wartet auf {0} Datei(en)":
        "Download in browser · Leeroyzan is waiting for {0} file(s)",
    "Ausgewählte Addons ({0})":
        "Selected addons ({0})",
    "Alle Addons ({0})":
        "All addons ({0})",
    "Keines dieser Addons hat eine Download-Quelle (CurseForge oder GitHub). Verknüpfe sie mit CurseForge oder teile sie als Paket.":
        "None of these addons has a download source (CurseForge or GitHub). Link them with CurseForge or share them as a package.",
    "Code kopiert – einfach in Discord oder eine Nachricht einfügen.":
        "Code copied – just paste it into Discord or a message.",
    "Alle {0} Teile kopiert (getrennt durch Leerzeilen). Sende jeden Teil als eigene Nachricht.":
        "All {0} parts copied (separated by blank lines). Send each part as a separate message.",
    "Lesbare Liste mit Links kopiert.":
        "Readable list with links copied.",
    "Es sind keine Addons installiert.":
        "No addons are installed.",
    "Im Download-Ordner liegt keine .lzlist- oder .lzpack-Datei.":
        "There is no .lzlist or .lzpack file in the download folder.",
    "Liste oder Paket öffnen":
        "Open list or package",
    "Installiert: {0}":
        "Installed: {0}",
    "Gib das GitHub-Repository ein (owner/repo oder Link) und klicke auf „Installieren“.":
        "Enter the GitHub repository (owner/repo or link) and click “Install”.",
    "Für CurseForge brauchst du deinen eigenen, kostenlosen API-Key. Er bleibt nur auf diesem Rechner.":
        "For CurseForge you need your own free API key. It stays on this computer only.",
    "{0} Addon(s) mit CurseForge verknüpft.":
        "{0} addon(s) linked with CurseForge.",
    "{0} von {1} bietet mehrere Dateien an. Welche passt zu deiner Spielversion? (Die Wahl gilt auch für spätere Updates.)":
        "{0} by {1} offers several files. Which one fits your game version? (The choice also applies to later updates.)",
    "{0} von {1} installiert: {2}  ·  Quelle: {3}":
        "{0} by {1} installed: {2}  ·  Source: {3}",
    "{0} von {1} ist installiert.":
        "{0} by {1} is installed.",
    "{0}: Das Release enthält keine Dateien.":
        "{0}: The release contains no files.",
    "{0} {1} bietet mehrere Dateien an. Welche passt zu deiner Spielversion? (Die Wahl gilt auch für spätere Updates.)":
        "{0} {1} offers several files. Which one fits your game version? (The choice also applies to later updates.)",
    "{0} {1} installiert: {2}":
        "{0} {1} installed: {2}",
    "{0} Update(s) verfügbar.":
        "{0} update(s) available.",
    "Alle Addons sind aktuell.":
        "All addons are up to date.",
    "  ({0} Fehler)":
        "  ({0} errors)",
    "Update-Prüfung fehlgeschlagen: {0}":
        "Update check failed: {0}",
    "  ({0} nicht automatisch)":
        "  ({0} not automatic)",
    "Auswahl aufgehoben.":
        "Selection cleared.",
    "{0} Addons ausgewählt.":
        "{0} addons selected.",
    "{0} wirklich entfernen? Die Ordner werden gelöscht.":
        "Really remove {0}? The folders will be deleted.",
    "Es sind keine Addons aktiv.":
        "No addons are active.",
    "Gib unten einen Namen für das neue Set ein ({0} aktive Addons).":
        "Enter a name for the new set below ({0} active addons).",
    "Löschen":
        "Delete",
    "Abbrechen":
        "Cancel",
    "Wähle zuerst ein Set aus.":
        "Choose a set first.",
    "Bitte zuerst die Addons auswählen, die du teilen willst (Strg+Klick oder „Alle auswählen“).":
        "Please first select the addons you want to share (Ctrl+click or “Select all”).",
    "{0} Addons exportiert ({1:.1f} MB): {2}":
        "{0} addons exported ({1:.1f} MB): {2}",
    " {0} CurseForge-Addon(s) sind nur als Verweis enthalten – dein Freund lädt sie mit seinem eigenen API-Key.":
        " {0} CurseForge addon(s) are only included as a reference – your friend downloads them with their own API key.",
    "Paket mit {0} Addons gespeichert ({1:.1f} MB): {2}.{3}":
        "Package with {0} addons saved ({1:.1f} MB): {2}.{3}",
    "  {0} Set(s) übernommen.":
        "  {0} set(s) imported.",
    "ersetzt deine Version {0}":
        "replaces your version {0}",
    ", wird von CurseForge geladen":
        ", downloaded from CurseForge",
    "{0} Addons aus Paket installiert.":
        "{0} addons installed from package.",
    "Backup erstellt: {0}":
        "Backup created: {0}",
    "Für {0} gibt es noch keine Backups – lege eins mit „Jetzt sichern“ an.":
        "There are no backups for {0} yet – create one with “Back up now”.",
    "Wähle in der Liste das Backup aus, das du zurückholen willst.":
        "Select the backup you want to restore in the list.",
    "Stand vom {0:%d.%m.%Y %H:%M} wiederherstellen? Dein aktueller Stand wird vorher automatisch gesichert.":
        "Restore the state from {0:%Y-%m-%d %H:%M}? Your current state is backed up automatically first.",
    "Stand vom {0:%d.%m.%Y %H:%M} wiederhergestellt.":
        "State from {0:%Y-%m-%d %H:%M} restored.",
    "Der Stand vom {0:%d.%m.%Y %H:%M} ist wiederhergestellt. Dein vorheriger Stand wurde als „vor Wiederherstellung“ gesichert.":
        "The state from {0:%Y-%m-%d %H:%M} has been restored. Your previous state was backed up as “before restore”.",
    "✓ Leeroyzan wurde auf v{0} aktualisiert.":
        "✓ Leeroyzan has been updated to v{0}.",
    "Leeroyzan wurde auf v{0} aktualisiert.":
        "Leeroyzan has been updated to v{0}.",
    "Manager-Update-Prüfung fehlgeschlagen.":
        "Manager update check failed.",
    "Leeroyzan {0} ist veröffentlicht – GitHub baut die .exe gerade noch (meist 3–5 Minuten).":
        "Leeroyzan {0} has been released – GitHub is still building the .exe (usually 3–5 minutes).",
    "Leeroyzan {0} ist da – in deiner Git-Kopie mit „git pull“ holen.":
        "Leeroyzan {0} is available – get it with “git pull” in your Git copy.",
    "Für {0} ist auf GitHub keine Leeroyzan.exe erschienen.":
        "No Leeroyzan.exe appeared on GitHub for {0}.",
    "Keine Schreibrechte im Ordner {0}.\nLege Leeroyzan.exe in einen eigenen Ordner, z. B. Dokumente\\Leeroyzan.":
        "No write permission in the folder {0}.\nPut Leeroyzan.exe in a folder of its own, e.g. Documents\\Leeroyzan.",
    "Das Leeroyzan-Update ist fehlgeschlagen: {0}":
        "The Leeroyzan update failed: {0}",
    "Nur .zip-, .lzpack- und .lzlist-Dateien können abgelegt werden.":
        "Only .zip, .lzpack and .lzlist files can be dropped.",
    "Addon Manager":
        "Addon Manager",
    "Version":
        "Version",
    "Quelle":
        "Source",
    "Ordner":
        "Folder",
    "Benötigt":
        "Requires",
    "Kein Addon gewählt":
        "No addon selected",
    "{0}  ({1} Ordner)":
        "{0}  ({1} folders)",
    "Keine Beschreibung in der .toc.":
        "No description in the .toc.",
    "manuell – keine Update-Prüfung":
        "manual – no update check",
    "{0} · veraltet":
        "{0} · outdated",
    "Addon":
        "Addon",
    "Autor":
        "Author",
    "Downloads":
        "Downloads",
    "Aktualisiert":
        "Updated",
    "Sprache":
        "Language",
    "Versionen":
        "Versions",
    "Beim Start auf Updates prüfen":
        "Check for updates on startup",
    "Installiert":
        "Installed",
    "Neu":
        "New",
    "Hinweis":
        "Note",
    "Set":
        "Set",
    "Addons":
        "Addons",
    "Zeitpunkt":
        "Date",
    "Art":
        "Type",
    "Beim Start auf Addon-Updates prüfen":
        "Check for addon updates on startup",
    "Bitte zuerst den Key einfügen.":
        "Please paste the key first.",
    "✓ Verbindung steht – der Key funktioniert.":
        "✓ Connected – the key works.",
    "Download-Ordner wählen":
        "Choose download folder",
    "{0} Update verfügbar":
        "{0} Update available",
    "WoW konnte nicht gestartet werden:\n{0}":
        "WoW could not be started:\n{0}",
    "Link konnte nicht geöffnet werden:\n{0}\n\n{1}":
        "Link could not be opened:\n{0}\n\n{1}",
    "Bitte zuerst die Frage oben beantworten.":
        "Please answer the question above first.",
    "Bitte warten, ein Vorgang läuft noch.":
        "Please wait, a task is still running.",
    "{0} passt nicht zur erwarteten Version {1} – bitte über „Update bei CurseForge herunterladen“ laden.":
        "{0} does not match the expected version {1} – please download it via “Download update from CurseForge”.",
    "{0} Update für {1} verfügbar – „Jetzt aktualisieren“ rechts oder Rechtsklick.":
        "{0} Update for {1} available – “Update now” on the right or right-click.",
    "{0} Jetzt aktualisieren":
        "{0} Update now",
    "Meine Addons   {0}":
        "My addons   {0}",
    "Noch nicht geprüft":
        "Not checked yet",
    "{0}: keine ZIP-Dateien bei CurseForge gefunden.":
        "{0}: no ZIP files found on CurseForge.",
    "Fehler: {0}":
        "Error: {0}",
    "Nur Addons für meine Spielversion ({0})":
        "Only addons for my game version ({0})",
    "Nur Addons für meine Spielversion (unbekannt)":
        "Only addons for my game version (unknown)",
    "Suche oben nach einem Addon und wähle es in der Liste aus. Mit Strg oder Umschalt markierst du mehrere.":
        "Search for an addon above and select it in the list. Use Ctrl or Shift to select several.",
    "{0} Addons ausgewählt":
        "{0} addons selected",
    "{0} installieren":
        "Install {0}",
    "✓ installiert":
        "✓ installed",
    "keine ZIP-Dateien bei CurseForge gefunden":
        "no ZIP files found on CurseForge",
    "von CurseForge":
        "from CurseForge",
    "Der Autor sperrt Downloads über Programme: Leeroyzan öffnet die Download-Seite und installiert die Datei, sobald sie in deinem Download-Ordner liegt.\n\n":
        "The author blocks downloads through programs: Leeroyzan opens the download page and installs the file as soon as it is in your download folder.\n\n",
    "Die Zwischenablage ist leer.":
        "The clipboard is empty.",
    "{0} Addon(s) installieren":
        "Install {0} addon(s)",
    "Alles schon installiert":
        "Everything already installed",
    "Datei konnte nicht gelesen werden: {0}":
        "File could not be read: {0}",
    "Addon-ZIP wählen":
        "Choose addon ZIP",
    "Zur Liste":
        "Go to list",
    "CurseForge-Link zu „{0}“ einfügen:":
        "Paste the CurseForge link for “{0}”:",
    "Bei den manuell installierten Addons ist keine CurseForge-Kennung hinterlegt.\n\nTipp: Wähle ein Addon in der Liste aus und klicke erneut – dann kannst du den CurseForge-Link einfügen.":
        "The manually installed addons have no CurseForge ID stored.\n\nTip: select an addon in the list and click again – then you can paste the CurseForge link.",
    "Bei diesen Addons lässt sich die installierte Version keiner CurseForge-Datei zuordnen:\n\n{0}\n\nSollen sie als aktuell gelten?\n\nJa = die neueste Datei gilt als installiert.\nNein = sie werden als veraltet behandelt, das nächste Update installiert die neueste Datei.":
        "For these addons, the installed version cannot be matched to a CurseForge file:\n\n{0}\n\nShould they count as up to date?\n\nYes = the newest file counts as installed.\nNo = they are treated as outdated, the next update installs the newest file.",
    "Download über den Browser nötig.":
        "Download via browser required.",
    "Keine Addons mit Update-Quelle (GitHub/CurseForge) in dieser Version installiert.":
        "No addons with an update source (GitHub/CurseForge) installed in this version.",
    "Keine Updates bekannt – erst „Jetzt prüfen“.":
        "No updates known – click “Check now” first.",
    "Im Browser laden (der Autor sperrt Programm-Downloads):":
        "Download in browser (the author blocks program downloads):",
    "Set „{0}“ überschreiben?":
        "Overwrite set “{0}”?",
    "Wiederherstellen":
        "Restore",
    "Der Manager ist aktuell (v{0}).":
        "The manager is up to date (v{0}).",
    "Installiert: v{0}  ·  das ist die neueste Version.":
        "Installed: v{0}  ·  this is the latest version.",
    "Manager-Update {0} verfügbar – „git pull“ in deiner Kopie.":
        "Manager update {0} available – run “git pull” in your copy.",
    "Manager-Update {0} verfügbar (übersprungen).":
        "Manager update {0} available (skipped).",
    "Für den Manager gibt es noch kein Release auf GitHub.":
        "There is no release of the manager on GitHub yet.",
    "GitHub hat die Anfrage abgelehnt (Limit erreicht?). Bitte in einer Stunde erneut versuchen.":
        "GitHub rejected the request (limit reached?). Please try again in an hour.",
    "Update-Prüfung fehlgeschlagen:\n{0}":
        "Update check failed:\n{0}",
    "Jetzt installieren":
        "Install now",
    "Überspringen":
        "Skip",
    "Für {0} ist auch nach 30 Minuten keine Leeroyzan.exe am Release – wahrscheinlich ist der automatische Build auf GitHub fehlgeschlagen (rotes X unter „Actions“, dort „Re-run jobs“).":
        "For {0}, there is still no Leeroyzan.exe on the release after 30 minutes – the automatic build on GitHub probably failed (red X under “Actions”, use “Re-run jobs” there).",
    "Automatisches Manager-Update fehlgeschlagen – siehe Log.":
        "Automatic manager update failed – see log.",
    "Unsicherer Pfad im Paket: {0}":
        "Unsafe path in package: {0}",
    "▶   SPIELEN":
        "▶   PLAY",
    "Leeroyzan v{0}":
        "Leeroyzan v{0}",
    "WoW-Ordner":
        "WoW folder",
    "Durchsuchen…":
        "Browse…",
    "Leeroyzan-Updates":
        "Leeroyzan updates",
    "+  Addons hinzufügen":
        "+  Add addons",
    "Gruppieren":
        "Group",
    "Suche":
        "Search",
    "Wähle links ein Addon oder eine Gruppe aus.":
        "Select an addon or a group on the left.",
    "Noch keine Addons installiert – über „Addons hinzufügen“ geht es los.":
        "No addons installed yet – get started with “Add addons”.",
    "Manuell installiert":
        "Manually installed",
    "{0} Update: {1}":
        "{0} Update: {1}",
    "ZIP-Datei":
        "ZIP file",
    "Andere …":
        "Other …",
    "Suchen":
        "Search",
    "Sortierung":
        "Sort by",
    "Datei":
        "File",
    "{0}  Alle aktualisieren":
        "{0}  Update all",
    "Jetzt prüfen":
        "Check now",
    "Verknüpfe sie mit CurseForge, dann prüft Leeroyzan auch diese auf Updates.":
        "Link them with CurseForge, then Leeroyzan checks these for updates too.",
    "Nach Update suchen":
        "Check for update",
    "Genau meine Versionen":
        "Exactly my versions",
    "Addon-Sets mitsenden":
        "Include addon sets",
    "Nachrichtenlänge":
        "Message length",
    "Code kopieren":
        "Copy code",
    "Lesbare Liste kopieren":
        "Copy readable list",
    "Als .lzlist speichern":
        "Save as .lzlist",
    "Als Paket (.lzpack) speichern":
        "Save as package (.lzpack)",
    "Code aus Discord/Chat hier einfügen (alle Teile, Reihenfolge egal):":
        "Paste the code from Discord/chat here (all parts, any order):",
    "Aus Zwischenablage einfügen":
        "Paste from clipboard",
    "Leeren":
        "Clear",
    "Oder eine Datei aus deinem Download-Ordner (.lzlist / .lzpack):":
        "Or a file from your download folder (.lzlist / .lzpack):",
    "Öffnen":
        "Open",
    "Ein Set merkt sich, welche Addons aktiv sind. „Anwenden“ schaltet genau diese ein und alle anderen aus – z. B. für Leveln, Raid oder einen Login ohne Addons.":
        "A set remembers which addons are active. “Apply” turns exactly these on and all others off – e.g. for levelling, raiding or a login without addons.",
    "Anwenden":
        "Apply",
    "Aktive als Set speichern":
        "Save active as set",
    "Gesichert werden alle Addon-Einstellungen (SavedVariables) dieser Spielversion.":
        "All addon settings (SavedVariables) of this game version are backed up.",
    "Jetzt sichern":
        "Back up now",
    "Die Spielversion wählst du links oben.":
        "You choose the game version at the top left.",
    "Nach Leeroyzan-Update suchen":
        "Check for Leeroyzan update",
    "Dein API-Key (bleibt nur auf diesem Rechner):":
        "Your API key (stays on this computer only):",
    "Testen":
        "Test",
    "Key holen ↗":
        "Get key ↗",
    "Download-Ordner deines Browsers:":
        "Your browser's download folder:",
    "Dort sucht Leeroyzan nach ZIP-Dateien, Listen und Paketen – und nach Downloads, die du für gesperrte Addons im Browser lädst.":
        "Leeroyzan looks there for ZIP files, lists and packages – and for downloads you get in the browser for blocked addons.",
    "ohne Version":
        "no version",
    "WoW-Ordner wählen":
        "Choose WoW folder",
    "WoW läuft gerade ({0}). Änderungen an Addons wirken erst nach /reload, und beim Ausloggen kann WoW Einstellungen überschreiben. Trotzdem fortfahren?":
        "WoW is currently running ({0}). Changes to addons only take effect after /reload, and WoW may overwrite settings when you log out. Continue anyway?",
    "veraltet":
        "outdated",
    "Updates   {0} {1}":
        "Updates   {0} {1}",
    "Sobald die Datei in {0} liegt, wird sie automatisch installiert.":
        "As soon as the file is in {0}, it will be installed automatically.",
    "Teil {0} von {1} – als eigene Nachricht senden:\n":
        "Part {0} of {1} – send as a separate message:\n",
    "Schaltet alle Addons aus.":
        "Turns off all addons.",
    "Key eintragen":
        "Enter key",
    "Verknüpfen":
        "Link",
    "Du nutzt bereits die neueste Version (v{0}).":
        "You are already using the latest version (v{0}).",
    "Im Repository {0} wurde noch kein Release veröffentlicht.":
        "No release has been published in the repository {0} yet.",
    "Keine Verbindung zu GitHub ({0}).":
        "No connection to GitHub ({0}).",
    "Installieren, sobald fertig":
        "Install when ready",
    "Release-Seite ↗":
        "Release page ↗",
    " (wartet seit {0} min)":
        " (waiting for {0} min)",
    "Update konnte nicht gestartet werden:\n{0}":
        "Update could not be started:\n{0}",
    "Spielversion":
        "Game version",
    "Ja":
        "Yes",
    "Nein":
        "No",
    " · passt":
        " · matches",
    "Tipp: ZIP-, .lzpack- und .lzlist-Dateien kannst du auch einfach ins Fenster ziehen.":
        "Tip: you can also simply drag ZIP, .lzpack and .lzlist files into the window.",
    "Die Liste zeigt die neuesten ZIP-Dateien aus deinem Download-Ordner.":
        "The list shows the newest ZIP files from your download folder.",
    "Keine Addons gefunden.":
        "No addons found.",
    "Fortfahren":
        "Continue",
    "\n{0} davon sind schon installiert.":
        "\n{0} of them are already installed.",
    "von {0}  ·  ":
        "by {0}  ·  ",
    "Nicht mehr warten":
        "Stop waiting",
    "Download-Seite öffnen ↗":
        "Open download page ↗",
    "Schaltet alle Addons ein.":
        "Turns on all addons.",
    "Wähle ein Set aus – oder speichere die gerade aktiven Addons als neues Set.":
        "Choose a set – or save the currently active addons as a new set.",
    "Actions-Seite ↗":
        "Actions page ↗",
    "  · aktuell":
        "  · up to date",
    "AN":
        "ON",
    "AUS":
        "OFF",
    "Keine Updates bekannt.":
        "No updates known.",
    "Noch nicht geprüft – „Jetzt prüfen“.":
        "Not checked yet – “Check now”.",
    "Das Release enthält keine Dateien.":
        "The release contains no files.",
    "{0}: CurseForge-API-Key fehlt (Einstellungen).":
        "{0}: CurseForge API key missing (Settings).",
    "Status":
        "Status",
    "Titel":
        "Title",
    "Interface":
        "Interface",
    "Update":
        "Update",
    "Automatisch installieren":
        "Install automatically",
    "Nur nachfragen":
        "Ask first",
    "Aus":
        "Off",
    "Zuletzt aktualisiert":
        "Last updated",
    "Name":
        "Name",
    "Discord / Chat (1800 Zeichen)":
        "Discord / chat (1800 characters)",
    "Kurznachricht (500 Zeichen)":
        "Short message (500 characters)",
    "SMS (150 Zeichen)":
        "SMS (150 characters)",
    "— Alle deaktivieren —":
        "— Disable all —",
    "— Alle aktivieren —":
        "— Enable all —",
    "automatisch vor Update":
        "automatic, before update",
    "vor Wiederherstellung":
        "before restore",
    "CurseForge":
        "CurseForge",
    "GitHub":
        "GitHub",
    "Projektseite":
        "Project page",
    "{0} existiert bereits.":
        "{0} already exists.",
    "neu":
        "new",
    "CurseForge-Fehler HTTP {0}.":
        "CurseForge error HTTP {0}.",
    "{0} Mio":
        "{0}M",
    "{0} Tsd":
        "{0}K",
    "Bereit.":
        "Ready.",
    "Teilen …":
        "Share …",
    "Vor Updates automatisch WTF sichern":
        "Back up WTF automatically before updates",
    "Installiert: v{0}":
        "Installed: v{0}",
    "Vor Updates automatisch sichern":
        "Back up automatically before updates",
    "Leeroyzan einrichten":
        "Set up Leeroyzan",
    "Teste …":
        "Testing …",
    "{0} Aktualisieren":
        "{0} Update",
    "Aktivieren / Deaktivieren":
        "Enable / Disable",
    "{0} veraltet":
        "{0} outdated",
    "{0} Update(s)":
        "{0} update(s)",
    "Alles aktuell":
        "All up to date",
    "{0} angezeigt":
        "{0} shown",
    " · {0} Treffer":
        " · {0} results",
    "Für jedes Addon wird automatisch die passende Datei gewählt.":
        "The matching file is chosen automatically for each addon.",
    "{0} Downloads  ·  ":
        "{0} downloads  ·  ",
    "Dateien werden geladen …":
        "Loading files …",
    "Alles aktuell.":
        "All up to date.",
    "Ohne Download-Quelle, nicht enthalten: {0} – dafür „Als Paket speichern“.":
        "Without a download source, not included: {0} – use “Save as package” for these.",
    "✓ {0} (schon da)":
        "✓ {0} (already there)",
    "Leeroyzan-Liste/-Paket":
        "Leeroyzan list/package",
    "Alle Dateien":
        "All files",
    "ZIP-Archive":
        "ZIP archives",
    "• {0}  (installiert: {1}, neueste Datei: {2})":
        "• {0}  (installed: {1}, newest file: {2})",
    "Nicht verknüpft:":
        "Not linked:",
    "Abgebrochen.":
        "Cancelled.",
    "Lade {0}":
        "Downloading {0}",
    "Nicht aktualisiert:":
        "Not updated:",
    "Nicht installiert:":
        "Not installed:",
    "Diese Addons werden von deiner Auswahl benötigt:":
        "These addons are required by your selection:",
    "Mit ins Paket nehmen?":
        "Include them in the package?",
    "{0} (importiert)":
        "{0} (imported)",
    "Paket vom {0} ({1}), {2} Addons:":
        "Package from {0} ({1}), {2} addons:",
    " vom {0}":
        " from {0}",
    "Sichere WTF":
        "Backing up WTF",
    "Installiert: v{0}  ·  neu: {1}":
        "Installed: v{0}  ·  new: {1}",
    "Installiert: v{0}  ·  {1} wird auf GitHub noch gebaut":
        "Installed: v{0}  ·  {1} is still being built on GitHub",
    " (seit {0} min)":
        " (for {0} min)",
    " – Installation folgt automatisch.":
        " – will be installed automatically.",
    "{0} Manager-Update {1}: GitHub baut die .exe noch":
        "{0} Manager update {1}: GitHub is still building the .exe",
    " – wird automatisch installiert, sobald sie fertig ist.":
        " – will be installed automatically as soon as it is ready.",
    "Lade Manager {0}":
        "Downloading manager {0}",
    # Leeroyzan Link: Spielsitzungen
    "Diese Leeroyzan-Version enthält das Addon nicht.":
        "This Leeroyzan version does not include the addon.",
    "{0} h {1:02d} min":
        "{0} h {1:02d} min",
    "{0} min":
        "{0} min",
    "{0} s":
        "{0} s",
    "{0} · {1} – {2}":
        "{0} · {1} – {2}",
    "{0} XP":
        "{0} XP",
    "{0} Erfahrung gesammelt":
        "gained {0} experience",
    "{0} verdient":
        "earned {0}",
    "{0} ausgegeben":
        "spent {0}",
    "1 Quest abgeschlossen":
        "completed 1 quest",
    "{0} Quests abgeschlossen":
        "completed {0} quests",
    "In {0} Spielzeit hast du {1}.":
        "In {0} of play you {1}.",
    "Du warst {0} im Spiel.":
        "You played for {0}.",
    "{0} Elite":
        "{0} elite",
    "{0} seltene":
        "{0} rare",
    "1 Boss":
        "1 boss",
    "{0} Bosse":
        "{0} bosses",
    "{0} Gegner besiegt":
        "defeated {0} enemies",
    " (darunter {0})":
        " (including {0})",
    "{0} zu Fuß zurückgelegt (≈ {1} Schritte)":
        "walked {0} (≈ {1} steps)",
    "1 Gebiet entdeckt":
        "discovered 1 area",
    "{0} Gebiete entdeckt":
        "discovered {0} areas",
    "1 Instanz besucht":
        "visited 1 instance",
    "{0} Instanzen besucht":
        "visited {0} instances",
    "Du hast {0}.":
        "You {0}.",
    "Du bist von Stufe {0} auf {1} aufgestiegen.":
        "You went from level {0} to {1}.",
    "Du bist einmal gestorben.":
        "You died once.",
    "Du bist {0}-mal gestorben.":
        "You died {0} times.",
    " und ":
        " and ",
    "Stufe {0}":
        "Level {0}",
    "Stufe {0} → {1}":
        "Level {0} → {1}",
    "Aufgezeichnet mit Leeroyzan Link 0.1 – nur Dauer, Erfahrung, Gold und Kills.":
        "Recorded with Leeroyzan Link 0.1 – duration, experience, gold and kills only.",
    "Fortgesetzt nach {0}× /reload oder kurzem Ausloggen.":
        "Continued after {0}× /reload or a short logout.",
    "Zusammenfassung":
        "Summary",
    "Zeit":
        "Time",
    "Spielzeit {0}":
        "Play time {0}",
    "im Kampf {0}":
        "in combat {0}",
    "AFK {0}":
        "AFK {0}",
    "ausgeruht {0}":
        "resting {0}",
    "in Instanzen {0}":
        "in instances {0}",
    "Flugroute {0}":
        "flight paths {0}",
    "tot {0}":
        "dead {0}",
    "Gesamtspielzeit des Charakters: {0}":
        "Character's total play time: {0}",
    "Erfahrung":
        "Experience",
    "{0} pro Stunde":
        "{0} per hour",
    "Kills {0} ({1})":
        "Kills {0} ({1})",
    "Quests {0} ({1})":
        "Quests {0} ({1})",
    "Entdecken {0} ({1})":
        "Exploring {0} ({1})",
    "Sonstiges {0}":
        "Other {0}",
    "Stufenaufstiege: {0}":
        "Level-ups: {0}",
    "{0} um {1}":
        "{0} at {1}",
    "Gold":
        "Gold",
    "netto {0}":
        "net {0}",
    "{0} → {1}":
        "{0} → {1}",
    "Einnahmen {0}: {1}":
        "Income {0}: {1}",
    "Ausgaben {0}: {1}":
        "Expenses {0}: {1}",
    "Quests":
        "Quests",
    "{0} abgeschlossen":
        "{0} completed",
    "{0} angenommen":
        "{0} accepted",
    "{0} abgebrochen":
        "{0} abandoned",
    "Quest {0}":
        "Quest {0}",
    "Neu angenommen: {0}":
        "Newly accepted: {0}",
    "Abgebrochen: {0}":
        "Abandoned: {0}",
    "Beute":
        "Loot",
    "Händlerwert {0}":
        "vendor value {0}",
    "Besondere Beute: ":
        "Notable loot: ",
    "Am häufigsten: ":
        "Most frequent: ",
    "Gegner":
        "Enemies",
    "{0} besiegt":
        "{0} defeated",
    "{0} normal":
        "{0} normal",
    "{0} selten":
        "{0} rare",
    "{0} Spieler":
        "{0} players",
    "{0} Todesstöße":
        "{0} killing blows",
    "Bosse: {0}":
        "Bosses: {0}",
    "Elite":
        "elite",
    "selten":
        "rare",
    "seltene Elite":
        "rare elite",
    "Boss":
        "boss",
    "Am häufigsten: {0}":
        "Most frequent: {0}",
    "Kampf":
        "Combat",
    "Schaden {0}":
        "Damage {0}",
    " (davon Begleiter {0})":
        " (pet {0})",
    "{0} DPS im Kampf":
        "{0} DPS in combat",
    "Krit-Rate {0}":
        "crit rate {0}",
    "Größter Treffer: {0} mit {1}{2}":
        "Biggest hit: {0} with {1}{2}",
    " (kritisch)":
        " (critical)",
    "Heilung {0}":
        "Healing {0}",
    "Überheilung {0}":
        "overhealing {0}",
    "größte Heilung {0} ({1})":
        "biggest heal {0} ({1})",
    "erlittener Schaden {0}":
        "Damage taken {0}",
    "erhaltene Heilung {0}":
        "healing received {0}",
    "härtester Treffer {0} von {1}":
        "hardest hit {0} by {1}",
    "Umgebung: {0}":
        "Environment: {0}",
    "{0} Hiebe":
        "{0} swings",
    "{0} Treffer":
        "{0} hits",
    "{0} kritisch":
        "{0} critical",
    "{0} verfehlt":
        "{0} missed",
    "{0} ausgewichen":
        "{0} dodged",
    "{0} pariert":
        "{0} parried",
    "{0} geblockt":
        "{0} blocked",
    "{0} gestreift":
        "{0} glancing",
    "{0} Hiebe eingesteckt":
        "{0} swings taken",
    "{0} abgewehrt ({1})":
        "{0} avoided ({1})",
    "Schaden nach Fähigkeit: {0}":
        "Damage by ability: {0}",
    "{0} Buffs auf andere":
        "{0} buffs on others",
    "{0} Unterbrechungen":
        "{0} interrupts",
    "{0} Bannungen":
        "{0} dispels",
    "Kampfdaten waren im Spiel nicht verfügbar ({0}).":
        "Combat data was not available in the game ({0}).",
    "Tode: {0}":
        "Deaths: {0}",
    "unbekannt":
        "unknown",
    "✝ {0}  {1}, durch {2}":
        "✝ {0}  {1}, by {2}",
    "Unterwegs":
        "Travel",
    "{0} zu Fuß":
        "{0} on foot",
    " (≈ {0} Schritte)":
        " (≈ {0} steps)",
    "{0} beritten":
        "{0} mounted",
    "{0} geschwommen":
        "{0} swum",
    "{0} Flugroute":
        "{0} by flight path",
    "{0} Sprünge":
        "{0} jumps",
    "Davon als Geist: {0}":
        "As a ghost: {0}",
    "Berufe und Fertigkeiten":
        "Professions and skills",
    "Unverändert: {0}":
        "Unchanged: {0}",
    "Hergestellt: {0}":
        "Crafted: {0}",
    "{0}: {1}":
        "{0}: {1}",
    "Neue Rezepte: {0}":
        "New recipes: {0}",
    "Neue Zauber und Fähigkeiten: {0}":
        "New spells and abilities: {0}",
    "Entdeckt und bereist":
        "Discovered and visited",
    " (+{0} XP)":
        " (+{0} XP)",
    "Zonen: {0}":
        "Zones: {0}",
    "Dungeons und Schlachtzüge":
        "Dungeons and raids",
    ", {0} Spieler":
        ", {0} players",
    "Ruf":
        "Reputation",
    "Beste Beute: [{0}] ({1})":
        "Best loot: [{0}] ({1})",
    "Boss besiegt: {0}":
        "Boss defeated: {0}",
    "Stufe {0} erreicht":
        "Reached level {0}",
    "Größter Treffer {0} ({1})":
        "Biggest hit {0} ({1})",
    "Lieblingsgegner: {0} ×{1}":
        "Favorite enemy: {0} ×{1}",
    "Gesamt – {0}":
        "Total – {0}",
    "Noch keine Sitzungen.":
        "No sessions yet.",
    "{0} bis {1}":
        "{0} to {1}",
    "%d.%m.%Y":
        "%Y-%m-%d",
    "Ø {0} pro Sitzung":
        "avg. {0} per session",
    "Längster Spieltag: {0} mit {1}":
        "Longest day: {0} with {1}",
    "Fortschritt":
        "Progress",
    " ({0}/h)":
        " ({0}/h)",
    "{0} Stufen":
        "{0} levels",
    "{0} Quests":
        "{0} quests",
    "{0} Fertigkeitspunkte":
        "{0} skill points",
    "Gold netto {0}":
        "Gold net {0}",
    "eingenommen {0}":
        "earned {0}",
    "ausgegeben {0}":
        "spent {0}",
    "erlitten {0}":
        "taken {0}",
    "{0} Instanzen":
        "{0} instances",
    "Beute und Wege":
        "Loot and travel",
    "{0} Gegenstände":
        "{0} items",
    "{0} zu Fuß (≈ {1} Schritte)":
        "{0} on foot (≈ {1} steps)",
    "Charaktere":
        "Characters",
    "{0} Kills":
        "{0} kills",
    "Leeroyzan Link":
        "Leeroyzan Link",
    "Spielsitzungen":
        "Play sessions",
    "Jetzt einlesen":
        "Read now",
    "Datum":
        "Date",
    "Charakter":
        "Character",
    "Dauer":
        "Duration",
    "XP":
        "XP",
    "Text kopieren":
        "Copy text",
    "Alle Charaktere":
        "All characters",
    "Σ Gesamt":
        "Σ Total",
    "Noch keine Sitzungen":
        "No sessions yet",
    "So funktioniert es":
        "How it works",
    "Leeroyzan Link zeichnet im Spiel jede Sitzung auf: Spielzeit, Erfahrung, Gold, Quests, Beute, Kampf (Schaden, Heilung, Hiebe, Zauber, Buffs, Tode), Strecke und Schritte, Berufe, entdeckte Gebiete, Dungeons und Schlachtzüge sowie besiegte Gegner, Elite und Bosse.":
        "Leeroyzan Link records every session in the game: play time, experience, gold, quests, loot, combat (damage, healing, swings, spells, buffs, deaths), distance and steps, professions, discovered areas, dungeons and raids, and defeated enemies, elites and bosses.",
    "Sobald du dich ausloggst, schreibt WoW die Daten. Leeroyzan übernimmt sie dann automatisch, speichert sie dauerhaft und fasst jede Sitzung hier zusammen.":
        "As soon as you log out, WoW writes the data. Leeroyzan then imports it automatically, keeps it and summarizes every session here.",
    "Ein /reload oder kurzes Ausloggen (unter 15 Minuten) mit demselben Charakter zählt zur selben Sitzung. Im Spiel zeigt /lz sitzung die laufende Sitzung.":
        "A /reload or a short logout (under 15 minutes) with the same character counts as the same session. In the game, /lz session shows the current session.",
    "Zusammenfassung kopiert.":
        "Summary copied.",
    "Bitte eine einzelne Sitzung auswählen.":
        "Please select a single session.",
    "Sitzung vom {0} ({1}) aus Leeroyzan löschen? Sie wird danach auch nicht mehr aus dem Spiel übernommen.":
        "Delete the session from {0} ({1}) from Leeroyzan? It will not be imported from the game again.",
    "Konnte nicht gelöscht werden:\n{0}":
        "Could not be deleted:\n{0}",
    "Sitzung gelöscht.":
        "Session deleted.",
    "Sitzungen konnten nicht eingelesen werden:\n{0}":
        "Sessions could not be read:\n{0}",
    "Die Daten von Leeroyzan Link konnten nicht gelesen werden:\n{0}":
        "The Leeroyzan Link data could not be read:\n{0}",
    "Noch keine Daten von Leeroyzan Link gefunden. WoW schreibt sie beim Ausloggen und bei /reload.":
        "No Leeroyzan Link data found yet. WoW writes it when you log out and on /reload.",
    "Keine neuen Sitzungen.":
        "No new sessions.",
    "{0} übernommen. WoW läuft noch – die laufende Sitzung wird nach dem Ausloggen ergänzt.":
        "{0} imported. WoW is still running – the current session will be completed after you log out.",
    "Sitzung gespeichert: {0}":
        "Session saved: {0}",
    "{0} Sitzungen gespeichert. Zuletzt: {1}":
        "{0} sessions saved. Latest: {1}",
    "Zusammenfassung ansehen":
        "View summary",
    "Wähle zuerst den WoW-Ordner – dann zeigt Leeroyzan hier deine Spielsitzungen.":
        "Choose the WoW folder first – then Leeroyzan shows your play sessions here.",
    "Leeroyzan Link ist in dieser Spielversion nicht installiert. Das Addon zeichnet deine Sitzungen im Spiel auf – ohne es bleibt diese Seite leer.":
        "Leeroyzan Link is not installed for this game version. The addon records your sessions in the game – without it this page stays empty.",
    "Leeroyzan Link {0} installieren":
        "Install Leeroyzan Link {0}",
    "Leeroyzan Link {0} ist installiert. Version {1} liegt Leeroyzan bei und zeichnet die ganze Sitzung auf (Quests, Beute, Kampf, Strecke, Berufe, Instanzen …).":
        "Leeroyzan Link {0} is installed. Version {1} comes with Leeroyzan and records the whole session (quests, loot, combat, distance, professions, instances …).",
    "Auf {0} aktualisieren":
        "Update to {0}",
    "Leeroyzan Link {0} ist installiert. Nach dem Ausloggen übernimmt Leeroyzan jede Sitzung automatisch und fasst sie hier zusammen.":
        "Leeroyzan Link {0} is installed. After you log out, Leeroyzan imports every session automatically and summarizes it here.",
    "Daten zuletzt geschrieben: {0}.":
        "Data last written: {0}.",
    "Das Addon ist zurzeit deaktiviert – aktiviere es unter „Meine Addons“.":
        "The addon is currently disabled – enable it under \"My addons\".",
    "Leeroyzan Link {0} ist installiert. Starte WoW neu (oder logge dich neu ein), damit das Addon geladen wird.":
        "Leeroyzan Link {0} is installed. Restart WoW (or log in again) so the addon gets loaded.",
    "Installiere Leeroyzan Link":
        "Installing Leeroyzan Link",
    "Leeroyzan Link {0} ist verfügbar (installiert: {1}). Die neue Version zeichnet ganze Spielsitzungen für Leeroyzan auf.":
        "Leeroyzan Link {0} is available (installed: {1}). The new version records whole play sessions for Leeroyzan.",
    "Aktualisieren":
        "Update",
    "Sitzungen":
        "Sessions",
    "Alle Zeiträume":
        "All time",
    "Heute":
        "Today",
    "Letzte 7 Tage":
        "Last 7 days",
    "Letzte 30 Tage":
        "Last 30 days",
    "{0} Kill":
        "{0} kill",
    "{0} Quest":
        "{0} quest",
    "{0} Gegenstand":
        "{0} item",
    "{0} Boss":
        "{0} boss",
    "1 Zauber gewirkt":
        "1 spell cast",
    "{0} Zauber gewirkt":
        "{0} spells cast",
    "{0} Buff erhalten":
        "{0} buff received",
    "{0} Buffs erhalten":
        "{0} buffs received",
    "{0} Schwächung erlitten":
        "{0} debuff taken",
    "{0} Schwächungen erlitten":
        "{0} debuffs taken",
    "{0} neues Gebiet":
        "{0} new area",
    "{0} neue Gebiete":
        "{0} new areas",
    "{0} Wipe":
        "{0} wipe",
    "{0} Wipes":
        "{0} wipes",
    "{0} Tod":
        "{0} death",
    "{0} Tode":
        "{0} deaths",
    "{0} Sitzung":
        "{0} session",
    "{0} Sitzungen":
        "{0} sessions",
    "{0} Spieltag":
        "{0} day played",
    "{0} Spieltage":
        "{0} days played",
    "{0} Stufe":
        "{0} level",
    "{0} Instanz":
        "{0} instance",
    "Schlecht":
        "Poor",
    "Gewöhnlich":
        "Common",
    "Ungewöhnlich":
        "Uncommon",
    "Selten":
        "Rare",
    "Episch":
        "Epic",
    "Legendär":
        "Legendary",
    "Artefakt":
        "Artifact",
    "Händler":
        "Vendor",
    "Post":
        "Mail",
    "Auktionshaus":
        "Auction house",
    "Lehrer":
        "Trainer",
    "Flugmeister":
        "Flight master",
    "Handel":
        "Trade",
    "Sonstiges":
        "Other",
    "Kräuterkunde":
        "Herbalism",
    "Bergbau":
        "Mining",
    "Kürschnerei":
        "Skinning",
    "Angeln":
        "Fishing",
    "Dungeon":
        "Dungeon",
    "Schlachtzug":
        "Raid",
    "Schlachtfeld":
        "Battleground",
    "Arena":
        "Arena",
    "Szenario":
        "Scenario",
    "Sturz":
        "Falling",
    "Ertrinken":
        "Drowning",
    "Erschöpfung":
        "Fatigue",
    "Feuer":
        "Fire",
    "Lava":
        "Lava",
    "Schleim":
        "Slime",
    "Krieger":
        "Warrior",
    "Paladin":
        "Paladin",
    "Jäger":
        "Hunter",
    "Schurke":
        "Rogue",
    "Priester":
        "Priest",
    "Schamane":
        "Shaman",
    "Magier":
        "Mage",
    "Hexenmeister":
        "Warlock",
    "Druide":
        "Druid",
    "Todesritter":
        "Death Knight",
    "Mönch":
        "Monk",
    "Dämonenjäger":
        "Demon Hunter",
    "Rufer":
        "Evoker",
}


def main():
    setup_logging()
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
