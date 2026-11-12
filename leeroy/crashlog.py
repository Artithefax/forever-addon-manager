"""Absturz-Protokoll: hält fest, warum Leeroyzan unerwartet zu ging.

- Python-Fehler im Fenster, in Hintergrund-Threads und beim Start landen mit Traceback in manager.log.
- Harte Abstürze (Zugriffsverletzung in einer DLL, Tcl/Tk) schreibt faulthandler mit den Python-Stacks aller
  Threads in crash.log – die .exe hat kein Konsolenfenster, sonst wäre das spurlos weg.
- Eine Markierungsdatei zeigt beim nächsten Start, dass die letzte Sitzung nicht sauber beendet wurde.
"""
from __future__ import annotations

import faulthandler
import os
import sys
import threading
import traceback
from datetime import datetime

from .config import config_dir, log
from .constants import APP_VERSION

CRASH_FILE = "crash.log"
MARKER_FILE = "running.flag"
MAX_CRASH_BYTES = 256 * 1024

_crash_fh = None


def _pid_alive(pid: int) -> bool:
    if pid <= 0 or pid == os.getpid():
        return False
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    try:
        from .selfupdate import wait_for_exit
        return not wait_for_exit(pid, 0)
    except Exception:  # noqa
        return False


def _previous_end() -> str:
    """Prüft die Markierung der letzten Sitzung. Rückgabe: '' (sauber) oder eine kurze Beschreibung."""
    marker = config_dir() / MARKER_FILE
    try:
        text = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    parts = text.split("|")
    try:
        pid = int(parts[0])
    except (ValueError, IndexError):
        pid = 0
    if _pid_alive(pid):
        return ""                                # läuft noch (zweites Fenster) – kein Absturz
    return " | ".join(p for p in parts[1:] if p) or "?"


def _crash_tail(limit: int = 4000) -> str:
    try:
        data = (config_dir() / CRASH_FILE).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    last = data.rfind("=== ")
    block = data[last:] if last >= 0 else data
    # Nur interessant, wenn nach der Startzeile wirklich etwas steht
    lines = [ln for ln in block.splitlines()[1:] if ln.strip()]
    return "\n".join(lines)[-limit:]


def start() -> dict:
    """Beim Programmstart aufrufen (nach setup_logging). Liefert Infos zur letzten Sitzung:
    {"unclean": bool, "since": str, "crash": str} – crash = letzter Eintrag aus crash.log, falls vorhanden."""
    global _crash_fh
    info = {"unclean": False, "since": "", "crash": ""}
    since = _previous_end()
    if since:
        info.update(unclean=True, since=since, crash=_crash_tail())
        log.warning("Die letzte Sitzung (gestartet %s) wurde nicht sauber beendet.%s", since,
                    (" Letzter Eintrag in crash.log:\n" + info["crash"]) if info["crash"] else "")

    path = config_dir() / CRASH_FILE
    try:
        if path.exists() and path.stat().st_size > MAX_CRASH_BYTES:
            keep = path.read_text(encoding="utf-8", errors="replace")[-MAX_CRASH_BYTES // 2:]
            path.write_text(keep, encoding="utf-8")
        _crash_fh = open(path, "a", encoding="utf-8")
        _crash_fh.write(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} Leeroyzan {APP_VERSION} gestartet "
                        f"(PID {os.getpid()}) ===\n")
        _crash_fh.flush()
        faulthandler.enable(file=_crash_fh, all_threads=True)
    except OSError as e:
        log.warning("crash.log nicht nutzbar: %s", e)

    try:
        (config_dir() / MARKER_FILE).write_text(
            f"{os.getpid()}|{datetime.now():%d.%m.%Y %H:%M}|{APP_VERSION}", encoding="utf-8")
    except OSError:
        pass

    def excepthook(exc_type, exc, tb):
        log.critical("Unbehandelter Fehler:\n%s", "".join(traceback.format_exception(exc_type, exc, tb)))
        sys.__excepthook__(exc_type, exc, tb)

    def thread_hook(args):
        if args.exc_type is SystemExit:
            return
        log.error("Fehler im Hintergrund-Thread %s:\n%s", getattr(args.thread, "name", "?"),
                  "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)))

    sys.excepthook = excepthook
    threading.excepthook = thread_hook
    return info


def tk_callback_error(exc_type, exc, tb) -> None:
    """Für Tk.report_callback_exception: Fehler in Klick-/Timer-Rückmeldungen ins Protokoll statt ins Nichts."""
    log.error("Fehler im Fenster:\n%s", "".join(traceback.format_exception(exc_type, exc, tb)))


def clean_exit() -> None:
    """Bei normalem Beenden: Markierung entfernen, faulthandler abschalten."""
    try:
        (config_dir() / MARKER_FILE).unlink(missing_ok=True)
    except OSError:
        pass
    try:
        faulthandler.disable()
        if _crash_fh:
            _crash_fh.close()
    except Exception:  # noqa
        pass
    log.info("Beendet.")
