"""Leeroylink mitliefern, installieren und alte Namen übernehmen."""
from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path

from .i18n import tr
from .config import app_dir
from .wow import clean_wow_text, parse_toc, running_wow
from .files import addons_dir, disabled_dir
from .lua import LINK_ADDON, LINK_DB, OLD_LINK_ADDON, OLD_LINK_DB, parse_lua_savedvariables


# ----- Addon mitliefern und installieren ---------------------------------- #
def bundled_link_dir() -> Path | None:
    """Mitgeliefertes Addon: in der .exe (PyInstaller) oder neben leeroyzan.py."""
    bases = [Path(getattr(sys, "_MEIPASS", "")), app_dir()] if getattr(sys, "frozen", False) else [app_dir()]
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


def old_link_dir(root: str, flavor: str) -> Path | None:
    """Das Addon unter seinem alten Namen „Leeroyzan Link“ (bis 0.3.2)."""
    if not root or not flavor:
        return None
    for base in (addons_dir(root, flavor), disabled_dir(root, flavor)):
        d = base / OLD_LINK_ADDON
        if (d / f"{OLD_LINK_ADDON}.toc").is_file():
            return d
    return None


def migrate_link_savedvariables(root: str, flavor: str) -> dict:
    """Übernimmt die Daten von „Leeroyzan Link“ einmalig für Leeroylink: kopiert je WoW-Account
    SavedVariables/LeeroyzanLink.lua nach Leeroylink.lua (mit neuem Variablennamen). Die alte Datei
    bleibt unverändert liegen; eine schon vorhandene neue Datei wird nie überschrieben."""
    res = {"copied": [], "skipped": []}
    base = Path(root) / flavor / "WTF" / "Account"
    if not base.is_dir():
        return res
    for old in sorted(base.glob(f"*/SavedVariables/{OLD_LINK_ADDON}.lua")):
        account = old.parent.parent.name
        new = old.with_name(f"{LINK_ADDON}.lua")
        if new.exists():
            res["skipped"].append(account)
            continue
        data = old.read_bytes()
        out, n = re.subn(rb"^" + OLD_LINK_DB.encode() + rb"\s*=", LINK_DB.encode() + b" =", data, count=1, flags=re.M)
        if not n:
            res["skipped"].append(account)
            continue
        text = out.decode("utf-8", errors="replace")
        if not isinstance(parse_lua_savedvariables(text).get(LINK_DB), dict):     # wirft bei kaputter Datei
            raise RuntimeError(tr("Die Daten in {0} konnten nicht gelesen werden.", str(old)))
        tmp = new.with_name(f".{LINK_ADDON}.lua.tmp")
        tmp.write_bytes(out)
        os.replace(tmp, new)
        res["copied"].append(account)
    return res


def migrate_link_addon(root: str, flavor: str) -> dict:
    """„Leeroyzan Link“ → Leeroylink: Daten übernehmen, neues Addon installieren, altes Addon entfernen.
    Nur bei geschlossenem WoW – sonst schreibt WoW beim Beenden noch einmal die alte Datei."""
    if running_wow(root, flavor):
        raise RuntimeError(tr("WoW läuft noch. Beende zuerst das Spiel – sonst gehen die Daten der laufenden Sitzung verloren."))
    old = old_link_dir(root, flavor)
    res = migrate_link_savedvariables(root, flavor)
    base = old.parent if old else None
    res["version"] = install_link_addon(root, flavor, base)
    if old and old.exists():
        shutil.rmtree(old)
    return res


def install_link_addon(root: str, flavor: str, base: Path | None = None) -> str:
    """Kopiert das mitgelieferte Addon in den AddOns-Ordner. Data.lua (schreibt Leeroyzan) bleibt erhalten."""
    from .questdb import QUEST_DATA_FILE
    src = bundled_link_dir()
    if not src:
        raise RuntimeError(tr("Diese Leeroyzan-Version enthält das Addon nicht."))
    current = installed_link_dir(root, flavor)
    dest = current or ((base or addons_dir(root, flavor)) / LINK_ADDON)
    dest.parent.mkdir(parents=True, exist_ok=True)
    keep = {}
    for name in ("Data.lua", QUEST_DATA_FILE):               # schreibt Leeroyzan – bleiben erhalten
        if (dest / name).is_file():
            keep[name] = (dest / name).read_bytes()
    tmp = dest.parent / f".{LINK_ADDON}.new"
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.copytree(src, tmp)
    for name, data in keep.items():
        (tmp / name).write_bytes(data)
    old = dest.parent / f".{LINK_ADDON}.old"
    shutil.rmtree(old, ignore_errors=True)
    if dest.exists():
        dest.rename(old)
    tmp.rename(dest)
    shutil.rmtree(old, ignore_errors=True)
    return link_version(dest)
