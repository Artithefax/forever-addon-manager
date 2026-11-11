"""Selbst-Update des Managers über GitHub-Releases."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from .constants import APP_VERSION, EXE_ASSET, SCRIPT_ASSET, UPDATE_REPO
from .i18n import tr
from .config import app_dir, config_dir, log
from .github import github_request, http_get, open_url, release_info


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
    p = app_dir() / SCRIPT_ASSET
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


class UpdateCancelled(Exception):
    pass


def download_with_progress(url: str, token: str = "", accept: str = "application/octet-stream",
                           progress: dict | None = None) -> bytes:
    """Wie http_get, meldet aber Fortschritt (progress["done"], ["total"]) und lässt sich abbrechen (["cancel"])."""
    req = github_request(url, token, accept)
    prog = progress if progress is not None else {}
    with open_url(req, timeout=30) as r:
        prog["total"] = int(r.headers.get("Content-Length") or 0)
        prog["done"] = 0
        parts = []
        while True:
            if prog.get("cancel"):
                raise UpdateCancelled()
            chunk = r.read(64 * 1024)
            if not chunk:
                break
            parts.append(chunk)
            prog["done"] += len(chunk)
    return b"".join(parts)


def download_self_update(info: dict, token: str = "", progress: dict | None = None) -> Path:
    """Lädt die neue .exe neben die laufende und prüft sie (MZ-Header, ggf. SHA-256)."""
    asset = self_update_asset(info)
    if not asset:
        raise ValueError(tr("Das Release enthält noch keine {0}.", EXE_ASSET))
    prog = progress if progress is not None else {}
    prog["phase"] = "download"
    blob = download_with_progress(asset["url"], token, progress=prog)
    prog["phase"] = "verify"
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
        raise PermissionError(tr("Keine Schreibrechte in {0}. Verschiebe den Manager z. B. in deinen Benutzerordner oder lade das Update von der Release-Seite.", target.parent)) from None
    return target


def download_script_update(info: dict, token: str = "", progress: dict | None = None) -> Path:
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
    target = (app_dir() / SCRIPT_ASSET).with_suffix(".new.py")
    target.write_text(text, encoding="utf-8")
    return target


def apply_script_update(new_script: Path) -> None:
    """Ersetzt das laufende Skript (alte Fassung bleibt als .py.bak) und startet es neu."""
    cur = app_dir() / SCRIPT_ASSET
    shutil.copy2(cur, cur.with_suffix(".py.bak"))
    os.replace(new_script, cur)
    flags = 0x00000008 if os.name == "nt" else 0          # losgelöst starten
    subprocess.Popen([sys.executable, str(cur), *sys.argv[1:]], close_fds=True, creationflags=flags,
                     env=clean_child_env())


def clean_child_env() -> dict:
    """Umgebung für Prozesse, die (über Umwege) wieder Leeroyzan.exe starten.

    Die .exe entpackt sich beim Start in einen Temp-Ordner (_MEI…) und legt dessen Pfad in Umgebungsvariablen
    ab. Erbt die neue Version diese Variablen, hält sie sich für einen Teil der alten und sucht python312.dll im
    längst gelöschten Ordner der alten – „Failed to load Python DLL“. Deshalb ohne diese Variablen starten."""
    env = dict(os.environ)
    mei = str(getattr(sys, "_MEIPASS", "") or "")
    for key in list(env):
        if key.startswith("_PYI_") or key in ("_MEIPASS2", "_PYIBoot_SPLASH") or \
                (mei and env[key].startswith(mei)):
            env.pop(key, None)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def launch_update_finisher(new_exe: Path, tag: str) -> None:
    """Startet die neue Version selbst (Leeroyzan.new.exe --finish-update …). Sie zeigt ein eigenes Fenster,
    wartet, bis diese Version beendet ist, setzt sich an ihre Stelle und startet von dort neu.
    So bleibt der ganze Ablauf sichtbar – ohne unsichtbares Skript dazwischen."""
    old_exe = Path(sys.executable)
    pids = [str(os.getpid())]
    try:
        pids.append(str(os.getppid()))            # der Starter der .exe (PyInstaller) hält die Datei bis zuletzt
    except OSError:
        pass
    with (config_dir() / "update.log").open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%d.%m.%Y %H:%M:%S} Update {tag}: starte {new_exe.name} zum Einsetzen\n")
    subprocess.Popen([str(new_exe), FINISH_FLAG, str(old_exe), ",".join(pids), tag], close_fds=True,
                     creationflags=0x00000008 if os.name == "nt" else 0, env=clean_child_env())


FINISH_FLAG = "--finish-update"


def wait_for_exit(pid: int, timeout: float) -> bool:
    """True, sobald der Prozess beendet ist (oder nicht mehr existiert)."""
    if os.name != "nt":
        end = time.time() + timeout
        while time.time() < end:
            try:
                os.kill(pid, 0)
            except OSError:
                return True
            time.sleep(0.2)
        return False
    import ctypes
    k32 = ctypes.windll.kernel32
    handle = k32.OpenProcess(0x00100000, False, pid)          # SYNCHRONIZE
    if not handle:
        return True
    try:
        return k32.WaitForSingleObject(handle, int(timeout * 1000)) == 0
    finally:
        k32.CloseHandle(handle)


def replace_exe(src: Path, dst: Path) -> None:
    """Kopiert die laufende neue .exe an die Stelle der alten (über eine Zwischendatei, dann umbenennen)."""
    tmp = dst.with_name(dst.name + ".tmp")
    shutil.copyfile(src, tmp)
    try:
        os.replace(tmp, dst)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def cleanup_self_update(tries: int = 0) -> bool:
    """Räumt ein liegengebliebenes .new.exe weg. False, wenn die Datei noch gesperrt ist (z. B. weil das
    Einsetz-Fenster der neuen Version sich gerade erst schließt) – dann später noch einmal versuchen."""
    try:
        if is_frozen():
            if Path(sys.executable).name.lower().endswith(".new.exe"):
                return True                              # diese Instanz IST die neue Datei
            new_exe_path().unlink(missing_ok=True)
        else:
            (app_dir() / SCRIPT_ASSET).with_suffix(".new.py").unlink(missing_ok=True)
        return True
    except OSError:
        return False
