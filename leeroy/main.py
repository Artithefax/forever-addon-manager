"""Startpunkt: normales Fenster oder Abschluss eines Manager-Updates."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from tkinter import ttk
import tkinter as tk

from .i18n import set_language, tr
from .config import config_dir, load_config, log, setup_logging
from .selfupdate import FINISH_FLAG, clean_child_env, replace_exe, wait_for_exit
from .ui.base import BaseTk
from .ui.theme import THEME, apply_theme
from .ui.dialogs import UpdateSteps
from .ui.app import App


def finish_update_main(old_exe: Path, pids: list[int], tag: str) -> None:
    """Läuft in der NEUEN Version (als Leeroyzan.new.exe): sichtbares Fenster, das die alte Datei ersetzt."""
    cfg = load_config()
    set_language(cfg.get("language", "de"))
    root = BaseTk()
    root.withdraw()
    root.fonts = apply_theme(root)
    t = THEME
    root.title(tr("Leeroyzan-Update"))
    root.configure(bg=t["panel"])
    root.resizable(False, False)
    frame = ttk.Frame(root, style="Panel.TFrame", padding=(24, 20))
    frame.pack(fill="both", expand=True)
    tk.Label(frame, text=tr("Leeroyzan {0} wird eingesetzt", tag), bg=t["panel"], fg=t["gold"],
             font=root.fonts["page"]).pack(anchor="w")
    steps = UpdateSteps(frame, [("wait", tr("Alte Version beenden")), ("replace", tr("Leeroyzan.exe ersetzen")),
                                ("start", tr("Neue Version starten"))])
    btns = ttk.Frame(frame, style="Panel.TFrame")
    btns.pack(fill="x", pady=(16, 0))
    ulog = config_dir() / "update.log"
    me = Path(sys.executable)

    def note(text):
        try:
            with ulog.open("a", encoding="utf-8") as f:
                f.write(f"{datetime.now():%d.%m.%Y %H:%M:%S} {text}\n")
        except OSError:
            pass

    state = {"result": None}

    def work():
        steps.set("wait", tr("Warte, bis die alte Version beendet ist …"))
        for pid in pids:
            if not wait_for_exit(pid, 30):
                state["result"] = ("error", tr("Die alte Version (Prozess {0}) hat sich nach 30 Sekunden noch nicht beendet. "
                                               "Schließe Leeroyzan von Hand (Task-Manager) und versuche es nochmal.", pid))
                return
        steps.set("replace", tr("Ersetze {0} …", old_exe.name))
        last = None
        for attempt in range(1, 61):
            try:
                replace_exe(me, old_exe)
                note(f"ersetzt durch {tag}")
                state["result"] = ("ok", None)
                return
            except OSError as e:
                last = e
                steps.set("replace", tr("Die Datei ist noch gesperrt – Versuch {0} von 60 (oft prüft gerade ein Virenscanner die neue Datei) …", attempt))
                time.sleep(1)
        note(f"FEHLER: {old_exe} nicht ersetzt: {last}")
        state["result"] = ("error", tr("{0} ließ sich nicht ersetzen: {1}\n\nHäufige Ursachen: ein Virenscanner, OneDrive oder ein "
                                       "geschützter Ordner. Lege Leeroyzan am besten in einen eigenen Ordner.", old_exe.name, last))

    def start_new():
        steps.set("start", tr("Starte Leeroyzan {0} …", tag))
        try:
            subprocess.Popen([str(old_exe)], close_fds=True, env=clean_child_env(),
                             creationflags=0x00000008 if os.name == "nt" else 0)
        except OSError as e:
            show_error(tr("Leeroyzan konnte nicht gestartet werden: {0}", e))
            return
        steps.done("start")
        root.after(1200, root.destroy)

    def show_error(text):
        steps.fail(text)
        for w in btns.winfo_children():
            w.destroy()
        ttk.Button(btns, text=tr("Nochmal versuchen"), style="Primary.TButton", command=begin).pack(side="left")
        ttk.Button(btns, text=tr("Neue Version trotzdem starten"),
                   command=lambda: (subprocess.Popen([str(me)], close_fds=True, env=clean_child_env()), root.destroy())).pack(side="left", padx=(8, 0))
        ttk.Button(btns, text=tr("Schließen"), command=root.destroy).pack(side="right")

    def poll():
        res = state["result"]
        if res is None:
            root.after(150, poll)
        elif res[0] == "ok":
            steps.done("replace")
            start_new()
        else:
            show_error(res[1])

    def begin():
        for w in btns.winfo_children():
            w.destroy()
        state["result"] = None
        threading.Thread(target=work, daemon=True).start()
        poll()

    root.update_idletasks()
    root.geometry(f"+{(root.winfo_screenwidth() - 520) // 2}+{(root.winfo_screenheight() - 300) // 3}")
    root.deiconify()
    root.after(200, begin)
    root.mainloop()


def main():
    setup_logging()
    if len(sys.argv) >= 5 and sys.argv[1] == FINISH_FLAG:
        try:
            pids = [int(x) for x in sys.argv[3].split(",") if x.strip().isdigit()]
        except ValueError:
            pids = []
        log.info("Update %s: setze die neue Version ein (%s)", sys.argv[4], sys.argv[2])
        finish_update_main(Path(sys.argv[2]), pids, sys.argv[4])
        return
    app = App()
    app.mainloop()
