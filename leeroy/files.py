"""Dateioperationen: ZIPs installieren, aktivieren/deaktivieren, WTF-Backups, Rollback nach Updates."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path

from .constants import APP_NAME, APP_VERSION
from .i18n import tr
from .config import AUTO_BACKUPS_KEEP, BACKUP_DIR, config_dir, log, write_text_atomic
from .wow import Addon, clean_wow_text, flush_toc_cache


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
    flush_toc_cache()
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


STAGING_PREFIX = ".leeroyzan-"


def staging_dir(root: str, flavor: str) -> Path:
    """Arbeitsordner auf demselben Laufwerk wie AddOns (Interface/.leeroyzan-…): Einsetzen und Beiseitelegen
    sind dann ein Umbenennen statt Kopieren. WoW lädt nur Interface/AddOns, der Ordner stört das Spiel nicht."""
    base = addons_dir(root, flavor).parent
    try:
        base.mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix=STAGING_PREFIX, dir=base))
    except OSError:
        return Path(tempfile.mkdtemp(prefix=STAGING_PREFIX))


def recover_staging(root: str, flavor: str, min_age: float = 600) -> list[str]:
    """Räumt Arbeitsordner abgebrochener Vorgänge auf. Liegt darin noch ein beiseitegelegtes Addon, das im
    AddOns-Ordner fehlt (Absturz mitten im Tausch), kommt es zurück. Gibt die zurückgeholten Namen zurück."""
    base = addons_dir(root, flavor).parent
    back = []
    try:
        leftovers = [d for d in base.iterdir() if d.is_dir() and d.name.startswith(STAGING_PREFIX)
                     and time.time() - d.stat().st_mtime >= min_age]
    except OSError:
        return back
    for work in leftovers:
        trash = work / "old"
        for d in sorted(trash.iterdir()) if trash.is_dir() else []:
            name = d.name.split("_", 1)[-1]
            if d.is_dir() and valid_addon_name(name) and not (addons_dir(root, flavor) / name).exists() \
                    and not (disabled_dir(root, flavor) / name).exists():
                try:
                    shutil.move(str(d), str(addons_dir(root, flavor) / name))
                    back.append(name)
                except OSError as e:
                    log.warning("Konnte %s nicht zurückholen: %s", name, e)
        shutil.rmtree(work, ignore_errors=True)
    if back:
        log.warning("Nach einem abgebrochenen Vorgang zurückgeholt: %s", ", ".join(back))
    return back


MOVE_TRIES = 8


def move_path(src: Path, dst: Path, tries: int | None = None) -> None:
    """Verschieben (gleiches Laufwerk: Umbenennen) mit kurzen Wiederholungen: Unter Windows halten Virenscanner und
    Indexdienst frisch entpackte Dateien oft einen Moment lang offen – dann scheitert das Umbenennen kurz."""
    tries = tries or MOVE_TRIES
    for attempt in range(tries):
        try:
            shutil.move(str(src), str(dst))
            return
        except PermissionError:
            if attempt == tries - 1 or not src.exists() or dst.exists():
                raise
            time.sleep(0.15 * (attempt + 1))


class FolderSwap:
    """Ordner tauschen mit Rückweg: Altes wird in den Arbeitsordner gelegt, Neues hineinbewegt. Geht ein Schritt
    schief, macht undo() alles rückgängig – ein Addon fehlt nie, weil das Kopieren mittendrin scheiterte."""

    def __init__(self, work: Path):
        self.trash = work / "old"
        self.trash.mkdir(parents=True, exist_ok=True)
        self.steps: list[tuple[Path, Path]] = []      # (wohin zurück, wo es jetzt liegt)

    def take_out(self, path: Path) -> Path:
        dest = self.trash / f"{len(self.steps)}_{path.name}"
        move_path(path, dest)
        self.steps.append((path, dest))
        return dest

    def put_in(self, src: Path, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        move_path(src, dest)
        self.steps.append((src, dest))

    def undo(self) -> None:
        for back, now in reversed(self.steps):
            try:
                if now.exists() and not back.exists():
                    move_path(now, back)
            except OSError as e:
                log.error("Rückgängig fehlgeschlagen (%s -> %s): %s", now, back, e)
        self.steps.clear()


def addon_target_name(folder: Path) -> str:
    """Ordnernamen von GitHub-Zipballs wie „Repo-1.2.3“ bereinigen."""
    name = folder.name
    tocs = [t.stem for t in folder.glob("*.toc")]
    if tocs and not any(t.lower().startswith(name.lower()) for t in tocs):
        name = re.split(r"[_-]", tocs[0])[0]
    return name


def swap_addon_folders(plan: list[tuple[Path, str]], root: str, flavor: str, work: Path) -> dict[str, tuple[Path, bool]]:
    """Setzt die Ordner aus plan (Quelle, Name) in AddOns ein. Gibt die beiseitegelegten alten Fassungen zurück:
    Name -> (Pfad im Arbeitsordner, war aktiviert). Bei einem Fehler ist alles wie vorher."""
    target, disabled = addons_dir(root, flavor), disabled_dir(root, flavor)
    swap = FolderSwap(work)
    olds: dict[str, tuple[Path, bool]] = {}
    try:
        for src, name in plan:
            dest, dis = target / name, disabled / name
            if dest.exists():
                olds[name] = (swap.take_out(dest), True)
            if dis.exists():
                moved = swap.take_out(dis)
                olds.setdefault(name, (moved, False))
            swap.put_in(src, dest)
    except Exception:
        swap.undo()
        raise
    return olds


def install_zip(zip_path: Path, root: str, flavor: str, rollback: tuple[str, dict] | None = None) -> list[str]:
    """Installiert alle Addons aus einem ZIP. rollback=(Quellen-ID, Quelle): die alte Fassung wird für
    „Rückgängig“ aufbewahrt – verschoben, nicht kopiert."""
    addons_dir(root, flavor).mkdir(parents=True, exist_ok=True)
    work = staging_dir(root, flavor)
    try:
        ext = work / "new"
        ext.mkdir()
        with zipfile.ZipFile(zip_path) as zf:
            safe_extract(zf, ext)
        folders = find_addon_folders(ext)
        if not folders:
            raise ValueError(tr("Im Archiv wurde kein Addon (Ordner mit passender .toc) gefunden."))
        plan, seen = [], set()
        for f in folders:
            name = addon_target_name(f)
            if valid_addon_name(name) and name.lower() not in seen:
                seen.add(name.lower())
                plan.append((f, name))
        olds = swap_addon_folders(plan, root, flavor, work)
        if rollback:
            try:
                keep_rollback(root, flavor, rollback[0], rollback[1], olds, {n for _, n in plan})
            except Exception as e:  # noqa – Rückgängig ist ein Extra, das Update ist schon drin
                log.warning("Vorversion von %s nicht aufbewahrt: %s", rollback[0], e)
        return [n for _, n in plan]
    finally:
        shutil.rmtree(work, ignore_errors=True)


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
        old = None
        if wtf.exists():
            old = tmp_p / "WTF.alt"
            wtf.rename(old)                       # gleicher Ordner: Umbenennen, nichts geht verloren
        try:
            new.rename(wtf)
        except OSError:
            if old is not None and not wtf.exists():
                old.rename(wtf)
            raise
    return safety


# --------------------------------------------------------------------------- #
# Rückgängig nach Update: die vorherige Version eines Addons wird aufbewahrt
# --------------------------------------------------------------------------- #

def rollback_dir(flavor: str, sid: str) -> Path:
    return config_dir() / "rollback" / (flavor.strip("_") or "wow") / re.sub(r"[^\w.-]", "_", sid)


def keep_rollback(root: str, flavor: str, sid: str, src: dict, olds: dict[str, tuple[Path, bool]],
                  replaced: set[str]) -> bool:
    """Bewahrt die alte Fassung einer Quelle für „Rückgängig“ auf (je Addon nur die letzte Version).
    olds kommt von swap_addon_folders und wird verschoben; Ordner der Quelle, die das Update nicht
    angefasst hat, werden kopiert, damit „Rückgängig“ wieder den vollständigen Stand herstellt."""
    dest = rollback_dir(flavor, sid)
    tmp = dest.with_name(dest.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    (tmp / "AddOns").mkdir(parents=True)
    enabled: dict[str, bool] = {}
    for name, (path, on) in olds.items():
        shutil.move(str(path), str(tmp / "AddOns" / name))
        enabled[name] = on
    for name in src.get("folders", []):
        if name in enabled or name in replaced or not valid_addon_name(name):
            continue
        for base, on in ((addons_dir(root, flavor), True), (disabled_dir(root, flavor), False)):
            if (base / name).is_dir():
                shutil.copytree(base / name, tmp / "AddOns" / name)
                enabled[name] = on
                break
    if not enabled:
        shutil.rmtree(tmp, ignore_errors=True)
        return False
    meta = {"sid": sid, "source": src, "saved": datetime.now().isoformat(timespec="seconds"), "enabled": enabled}
    write_text_atomic(tmp / "rollback.json", json.dumps(meta, ensure_ascii=False, indent=1))
    shutil.rmtree(dest, ignore_errors=True)
    tmp.rename(dest)
    return True


def save_rollback(root: str, flavor: str, sid: str, src: dict) -> bool:
    """Kopiert die installierten Ordner einer Quelle weg (für Wege, die nicht über install_zip laufen)."""
    return keep_rollback(root, flavor, sid, src, {}, set())


def rollback_root() -> Path:
    return config_dir() / "rollback"


def rollback_usage() -> tuple[int, int]:
    """(Bytes, Anzahl) aller aufbewahrten Vorversionen."""
    size = count = 0
    base = rollback_root()
    if not base.is_dir():
        return 0, 0
    for meta in base.glob("*/*/rollback.json"):
        count += 1
        for f in meta.parent.rglob("*"):
            try:
                if f.is_file():
                    size += f.stat().st_size
            except OSError:
                pass
    return size, count


ROLLBACK_MAX_DAYS = 60


def prune_rollbacks(flavor: str, keep: set[str] | None = None, max_days: int = ROLLBACK_MAX_DAYS,
                    everything: bool = False) -> int:
    """Löscht Vorversionen von Addons, die es nicht mehr gibt (keep = bekannte Quellen-IDs), solche, die älter
    als max_days sind, und Reste abgebrochener Vorgänge. everything=True löscht alle dieser Spielversion."""
    base = rollback_root() / (flavor.strip("_") or "wow")
    if not base.is_dir():
        return 0
    removed = 0
    limit = time.time() - max_days * 86400
    for d in base.iterdir():
        if not d.is_dir():
            continue
        meta = None
        try:
            meta = json.loads((d / "rollback.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        sid = meta.get("sid") if isinstance(meta, dict) else None
        old = (d / "rollback.json").exists() and (d / "rollback.json").stat().st_mtime < limit
        gone = keep is not None and sid is not None and sid not in keep
        if everything or meta is None or old or gone:
            shutil.rmtree(d, ignore_errors=True)
            removed += 1
    return removed


def read_rollback(flavor: str, sid: str) -> dict | None:
    try:
        meta = json.loads((rollback_dir(flavor, sid) / "rollback.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return meta if isinstance(meta, dict) and isinstance(meta.get("source"), dict) else None


def restore_rollback(root: str, flavor: str, sid: str, current: list[str]) -> dict:
    """Ersetzt die installierte Version durch die gesicherte und gibt deren Daten zurück.
    Getauscht wird über einen Arbeitsordner: Scheitert ein Schritt, bleibt die installierte Version."""
    d = rollback_dir(flavor, sid)
    meta = read_rollback(flavor, sid)
    saved = [p for p in (d / "AddOns").iterdir() if p.is_dir() and valid_addon_name(p.name)] \
        if meta and (d / "AddOns").is_dir() else []
    if not saved:
        raise FileNotFoundError(tr("Für dieses Addon ist keine vorherige Version gespeichert."))
    enabled = meta.get("enabled") or {}
    work = staging_dir(root, flavor)
    swap = FolderSwap(work)
    try:
        for name in {*current, *(p.name for p in saved)}:
            if not valid_addon_name(name):
                continue
            for base in (addons_dir(root, flavor), disabled_dir(root, flavor)):
                if (base / name).is_dir():
                    swap.take_out(base / name)
        for p in saved:
            base = addons_dir(root, flavor) if enabled.get(p.name, True) else disabled_dir(root, flavor)
            swap.put_in(p, base / p.name)
    except Exception:
        swap.undo()
        shutil.rmtree(work, ignore_errors=True)
        raise
    shutil.rmtree(work, ignore_errors=True)
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
    addons_dir(root, flavor).mkdir(parents=True, exist_ok=True)
    work = staging_dir(root, flavor)
    try:
        tmp_p = (work / "new").resolve()
        tmp_p.mkdir()
        with zipfile.ZipFile(path) as zf:
            members = []
            for m in zf.infolist():
                parts = m.filename.split("/")
                if len(parts) >= 3 and parts[0] == "AddOns" and parts[1] in wanted:
                    dest = (tmp_p / m.filename).resolve()
                    if tmp_p not in dest.parents:
                        raise ValueError(tr("Unsicherer Pfad im Paket: {0}", m.filename))
                    members.append(m)
            zf.extractall(tmp_p, members=members)
        plan = [(tmp_p / "AddOns" / name, name) for name in folders
                if valid_addon_name(name) and (tmp_p / "AddOns" / name).is_dir()]
        swap_addon_folders(plan, root, flavor, work)
        return [n for _, n in plan]
    finally:
        shutil.rmtree(work, ignore_errors=True)


def open_in_explorer(path: Path) -> None:
    if sys.platform.startswith("win"):
        os.startfile(str(path))  # noqa
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])
