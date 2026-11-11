"""Dateioperationen: ZIPs installieren, aktivieren/deaktivieren, WTF-Backups, Rollback nach Updates."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from .constants import APP_NAME, APP_VERSION
from .i18n import tr
from .config import AUTO_BACKUPS_KEEP, BACKUP_DIR, config_dir
from .wow import Addon, clean_wow_text


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
    from .sharing import SAFE_NAME_RE
    return bool(name) and name not in (".", "..") and SAFE_NAME_RE.match(name) is not None


def export_pack(addons: list, sources: dict, flavor: str, dest: Path) -> Path:
    """Schreibt die gewählten Addons inkl. Quellinfos in eine .lzpack-Datei."""
    from .sharing import PACK_FORMAT
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
    from .sharing import PACK_FORMAT, PACK_MAX_BYTES
    try:
        with zipfile.ZipFile(path) as zf:
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            total = sum(i.file_size for i in zf.infolist())
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError, UnicodeDecodeError):
        raise ValueError(tr("Das ist kein gültiges Addon-Paket.")) from None
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
