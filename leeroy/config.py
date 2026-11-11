"""Datenordner (portabel neben der .exe), Einstellungen und Logdatei."""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import shutil
import sys
import time
from pathlib import Path

from . import vault


# --------------------------------------------------------------------------- #
# Konfiguration
# --------------------------------------------------------------------------- #

PORTABLE_DIR_NAME = "Leeroyzan-Data"
LEGACY_COPY_LIMIT = 500 * 1024 * 1024     # alte Backups nur bis zu dieser Größe mitnehmen


def app_dir() -> Path:
    """Ordner, in dem Leeroyzan.exe (bzw. leeroyzan.py) liegt."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


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
    if not isinstance(cfg, dict):
        cfg = {}
    SECRETS_LOST.clear()
    for key in SECRET_KEYS:
        value = cfg.get(key)
        if isinstance(value, dict):
            try:
                cfg[key] = vault.unseal(value)
            except Exception as e:  # noqa – anderer PC oder anderes Windows-Konto
                log.warning("%s ließ sich nicht entschlüsseln: %s", key, e)
                cfg[key] = ""
                SECRETS_LOST.append(key)
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


def write_text_atomic(path: Path, text: str, newline: str | None = None) -> None:
    """Schreibt über eine Zwischendatei: Ein Abbruch mittendrin hinterlässt nie eine halbe Datei."""
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline=newline) as f:
        f.write(text)
    os.replace(tmp, path)


SECRET_KEYS = ("github_token", "curseforge_key", "discord_webhook")
SECRETS_LOST: list[str] = []          # Schlüssel, die auf diesem PC nicht entschlüsselt werden konnten
_LAST_SAVED: dict[str, str] = {}


def save_config(cfg: dict) -> None:
    """Speichert nur, wenn sich etwas geändert hat. Schlüssel und Tokens landen unter Windows verschlüsselt
    (an dein Windows-Konto gebunden) in der Datei, im Programm bleiben sie lesbar."""
    plain = json.dumps(cfg, indent=2, ensure_ascii=False)
    if _LAST_SAVED.get(str(CONFIG_FILE)) == plain and CONFIG_FILE.exists():
        return
    data = dict(cfg)
    for key in SECRET_KEYS:
        if data.get(key):
            data[key] = vault.seal(data[key])
    write_text_atomic(CONFIG_FILE, json.dumps(data, indent=2, ensure_ascii=False))
    _LAST_SAVED[str(CONFIG_FILE)] = plain


SOURCE_PARK_DAYS = 30


def reconcile_sources(cfg: dict, flavor: str, present: set[str], healthy: bool, now: float | None = None) -> bool:
    """Gleicht die Quellen (Zuordnung Ordner -> CurseForge/GitHub) mit den gefundenen Ordnern ab.

    Nur bei einem gesunden Scan (AddOns-Ordner da, Addons gefunden) – ein nicht erreichbares Laufwerk löscht
    so nichts mehr. Quellen ohne Ordner werden geparkt statt gelöscht und kommen zurück, sobald ihr Ordner
    wieder auftaucht; nach SOURCE_PARK_DAYS Tagen verfallen sie. Gibt zurück, ob sich etwas geändert hat."""
    if not healthy:
        return False
    now = time.time() if now is None else now
    sources = cfg.setdefault("sources", {}).setdefault(flavor, {})
    parked = cfg.setdefault("parked_sources", {}).setdefault(flavor, {})
    changed = False
    for sid in list(parked):
        entry = parked[sid]
        if sid not in sources and any(f in present for f in entry.get("folders", [])):
            sources[sid] = {k: v for k, v in entry.items() if k != "parked"}
            del parked[sid]
            changed = True
            log.info("Quelle %s wieder zugeordnet (Ordner ist zurück)", sid)
        elif sid in sources or now - entry.get("parked", now) > SOURCE_PARK_DAYS * 86400:
            del parked[sid]
            changed = True
    for sid in list(sources):
        src = sources[sid]
        folders = src.get("folders", [])
        keep = [f for f in folders if f in present]
        if keep == folders:
            continue
        changed = True
        if keep:
            src["folders"] = keep
        else:
            parked[sid] = {**src, "parked": int(now)}
            del sources[sid]
            log.info("Quelle %s geparkt: Ordner %s nicht gefunden", sid, ", ".join(folders))
    return changed
