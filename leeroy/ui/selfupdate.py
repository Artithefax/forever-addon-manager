"""Hauptfenster: SelfUpdateMixin."""
from __future__ import annotations

import sys
import time
import urllib.error
from pathlib import Path

import tkinter as tk

from ..constants import APP_VERSION, UPDATE_REPO
from ..i18n import tr
from ..config import log, save_config
from ..selfupdate import (
    NoReleaseYet,
    UpdateCancelled,
    apply_script_update,
    check_self_update,
    cleanup_self_update,
    download_script_update,
    download_self_update,
    is_frozen,
    launch_update_finisher,
    parse_version,
    update_ready,
)
from .theme import UPD_MARK
from .dialogs import UpdateWindow


class SelfUpdateMixin:
    """Teil des Hauptfensters (wird in App eingemischt)."""

    # ----- Selbst-Update --------------------------------------------------- #
    def _cleanup_update_file(self, tries: int = 0):
        """Leeroyzan.new.exe wegräumen – direkt nach einem Update ist sie noch kurz in Benutzung."""
        if not cleanup_self_update() and tries < 10:
            self.after(3000, lambda: self._cleanup_update_file(tries + 1))

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
        w = getattr(self, "update_win", None)
        if w is not None and w.alive() and (self.watching_build or not getattr(self, "update_progress", {}).get("finished", True)):
            if not silent:
                w.show()                     # ein Update läuft schon: dessen Fenster zeigen
            return
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

    BUILD_POLL_MS = 30_000          # alle 30 Sekunden nachsehen (GitHub erlaubt ohne Token 60 Abfragen/Stunde) …
    BUILD_POLL_TRIES = 50           # … höchstens etwa 25 Minuten lang

    def _update_window(self, tag: str, show: bool = True) -> "UpdateWindow":
        w = getattr(self, "update_win", None)
        if w is None or not w.alive() or w.tag != tag:
            if w is not None:
                w.close()
            w = self.update_win = UpdateWindow(self, tag)
        if show:
            w.show()
        return w

    def cancel_manager_update(self):
        self.watching_build = ""
        prog = getattr(self, "update_progress", None)
        if prog is not None:
            prog["cancel"] = True
        self._update_cancelled = True
        w = getattr(self, "update_win", None)
        if w is not None:
            w.close()
        self.set_status(tr("Manager-Update abgebrochen."))
        log.info("Manager-Update abgebrochen")

    def _build_countdown(self, tag: str, started: float, next_at: float):
        """Zählt im Update-Fenster die Zeit bis zur nächsten Nachfrage bei GitHub herunter."""
        w = getattr(self, "update_win", None)
        if self.watching_build != tag or w is None or not w.alive():
            return
        waited = int(time.time() - started)
        left = max(0, int(next_at - time.time()))
        w.steps.set("build", tr("GitHub baut die neue Leeroyzan.exe – das dauert meist 1–5 Minuten.\n"
                                "Wartet seit {0}:{1:02d} min · nächste Nachfrage in {2} s.", waited // 60, waited % 60, left))
        self.after(1000, lambda: self._build_countdown(tag, started, next_at))

    def watch_manager_build(self, tag: str, tries: int = 0, manual: bool = False):
        """Wartet im Hintergrund, bis die .exe des Releases gebaut ist, und installiert sie dann automatisch."""
        if tries == 0:
            if self.watching_build == tag:
                self._update_window(tag)             # läuft schon – Fenster wieder zeigen
                return
            self.watching_build = tag
            self._watch_manual = manual
            self._build_started = time.time()
            log.info("Manager-Update %s: warte auf den Build der .exe", tag)
            w = self._update_window(tag)
            w.buttons([(tr("Actions-Seite ↗"), lambda: self.open_link(f"https://github.com/{UPDATE_REPO}/actions"))])
        if self.watching_build != tag:
            return
        if tries >= self.BUILD_POLL_TRIES:
            self.watching_build = ""
            log.warning("Manager-Update %s: .exe nach %d Versuchen nicht fertig", tag, tries)
            self.set_status(tr("Für {0} ist auf GitHub keine Leeroyzan.exe erschienen.", tag))
            w = getattr(self, "update_win", None)
            if w is not None and w.alive():
                w.steps.fail(tr("Nach etwa 25 Minuten ist noch keine Leeroyzan.exe am Release – der Build auf GitHub ist "
                                "wahrscheinlich fehlgeschlagen (rotes X unter „Actions“, dort „Re-run jobs“)."))
                w.buttons([(tr("Actions-Seite ↗"), lambda: self.open_link(f"https://github.com/{UPDATE_REPO}/actions"))])
            if self._watch_manual:
                self.notify(tr("Für {0} ist auch nach 30 Minuten keine Leeroyzan.exe am Release – wahrscheinlich ist der automatische Build auf GitHub fehlgeschlagen (rotes X unter „Actions“, dort „Re-run jobs“).", tag), "warn",
                            [(tr("Actions-Seite ↗"), lambda: self.open_link(f"https://github.com/{UPDATE_REPO}/actions"))],
                            key="manager")
            return
        self.after(10, lambda: self._build_countdown(tag, self._build_started, time.time()))
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
            next_at = time.time() + self.BUILD_POLL_MS / 1000
            self.after(10, lambda: self._build_countdown(tag, self._build_started, next_at))
            self.after(self.BUILD_POLL_MS, lambda: self.watch_manager_build(tag, tries + 1))

        self.run_quiet(lambda: check_self_update(token), done)

    def install_manager_update(self, info: dict, automatic: bool = False, _first: bool = True):
        """Lädt die neue .exe (mit Fortschritt im Update-Fenster) und lässt sie sich selbst einsetzen."""
        tag = info["tag"]
        w = self._update_window(tag)
        if _first:
            self._update_cancelled = False
        if self._update_cancelled:
            return
        w.steps.done("build")
        if self.busy:                         # laufende Vorgänge nie unterbrechen – aber sichtbar warten
            w.steps.set("download", tr("Wartet, bis „{0}“ fertig ist …", getattr(self, "busy_label", tr("ein Vorgang"))))
            self.after(1000, lambda: self.install_manager_update(info, automatic, _first=False))
            return
        token = self.cfg.get("github_token", "")
        prog = self.update_progress = {"done": 0, "total": 0, "phase": "download"}
        w.buttons([])
        started = time.time()

        def tick():
            if not w.alive() or self.update_progress is not prog or prog.get("finished"):
                return
            if prog.get("phase") == "verify":
                w.steps.set("verify", tr("Vergleiche die Prüfsumme mit der von GitHub …"))
                w.steps.fraction(None)
            else:
                done_b, total = prog.get("done", 0), prog.get("total", 0)
                speed = done_b / max(0.5, time.time() - started)
                if total:
                    w.steps.fraction(done_b / total)
                    w.steps.set("download", tr("{0} von {1} MB · {2} MB/s", f"{done_b / 1e6:.1f}", f"{total / 1e6:.1f}",
                                               f"{speed / 1e6:.1f}"))
                else:
                    w.steps.fraction(None)
                    w.steps.set("download", tr("{0} MB geladen …", f"{done_b / 1e6:.1f}"))
            self.after(150, tick)
        tick()

        def done(new_exe):
            prog["finished"] = True
            if self._update_cancelled:
                cleanup_self_update()
                return
            log.info("Manager-Update %s geladen (%s), starte neu.", tag, "automatisch" if automatic else "manuell")
            w.steps.done("download")
            w.steps.done("verify")
            w.steps.fraction(1)
            if self.busy:
                w.steps.set("restart", tr("Fertig geladen – wartet, bis „{0}“ fertig ist, dann startet Leeroyzan neu …",
                                          getattr(self, "busy_label", tr("ein Vorgang"))))
                self.after(1000, lambda: done(new_exe))
                return
            self._restart_countdown(tag, new_exe, 3)

        def failed(err):
            prog["finished"] = True
            if isinstance(err, UpdateCancelled):
                cleanup_self_update()
                return
            log.error("Manager-Update fehlgeschlagen: %s", err)
            if isinstance(err, PermissionError):
                err = (tr("Keine Schreibrechte im Ordner {0}.\nLege Leeroyzan.exe in einen eigenen Ordner, z. B. Dokumente\\Leeroyzan.", Path(sys.executable).parent))
            elif isinstance(err, urllib.error.URLError):
                err = tr("Keine Verbindung zu GitHub ({0}).", getattr(err, "reason", err))
            if w.alive():
                w.steps.fail(tr("Das hat nicht geklappt: {0}", err))
                w.buttons([(tr("Nochmal versuchen"), lambda: self.install_manager_update(info, automatic)),
                           (tr("Release-Seite ↗"), lambda: self.open_link(info.get("page") or f"https://github.com/{UPDATE_REPO}/releases"))])
            if automatic:
                self.set_status(tr("Automatisches Manager-Update fehlgeschlagen – siehe Log."))
                if self.cfg.get("self_update_failed_tag") == tag:
                    return                    # pro Version nur einmal melden
                self.cfg["self_update_failed_tag"] = tag
                save_config(self.cfg)
            self.notify(tr("Das Leeroyzan-Update ist fehlgeschlagen: {0}", err), "error",
                        [(tr("Details"), lambda: self._update_window(tag))], key="manager")

        fetch = download_self_update if is_frozen() else download_script_update
        self.set_status(tr("{0} Lade Leeroyzan {1} …", UPD_MARK, tag))
        self.manager_state.set(tr("Installiert: v{0}  ·  {1} wird geladen und eingesetzt.", APP_VERSION, tag))
        self.run_quiet(lambda: fetch(info, token, prog), lambda res, err: failed(err) if err else done(res))

    def _restart_countdown(self, tag: str, new_exe: Path, seconds: int):
        """Kurz ankündigen, dann beenden; die neue Version setzt sich in ihrem eigenen Fenster ein."""
        w = getattr(self, "update_win", None)
        if self._update_cancelled:
            cleanup_self_update()
            return
        if seconds > 0 and w is not None and w.alive():
            w.show()
            w.steps.set("restart", tr("Leeroyzan schließt sich in {0} s – die neue Version zeigt dann ein kleines Fenster, "
                                      "setzt sich ein und startet von selbst neu.", seconds))
            w.buttons([(tr("Jetzt neu starten"), lambda: self._restart_countdown(tag, new_exe, 0))])
            self._restart_job = self.after(1000, lambda: self._restart_countdown(tag, new_exe, seconds - 1))
            return
        if getattr(self, "_restart_job", None):
            try:
                self.after_cancel(self._restart_job)
            except tk.TclError:
                pass
        if getattr(self, "_restarting", False):
            return
        self._restarting = True
        try:
            self.cfg["pending_self_update"] = {"from": APP_VERSION, "to": tag}
            save_config(self.cfg)
            if is_frozen():
                launch_update_finisher(new_exe, tag)
            else:
                apply_script_update(new_exe)
        except Exception as e:  # noqa
            self._restarting = False
            self.cfg.pop("pending_self_update", None)
            save_config(self.cfg)
            if w is not None and w.alive():
                w.steps.fail(tr("Update konnte nicht gestartet werden: {0}", e))
            else:
                self.error(tr("Update konnte nicht gestartet werden:\n{0}", e))
            return
        self.destroy()
