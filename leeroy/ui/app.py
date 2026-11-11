"""Hauptfenster: setzt die Seiten und Bausteine zu einer Anwendung zusammen."""
from __future__ import annotations

import queue
import re

from tkinter import ttk
import tkinter as tk

from ..constants import APP_NAME, APP_VERSION
from ..i18n import LANGUAGES, detect_language, set_language, tr
from ..config import SECRETS_LOST, config_dir, load_config, log, save_config
from ..files import prune_rollbacks, recover_staging
from ..wow import Addon, find_wow_root
from .base import BaseTk, DND_FILES, HAS_DND
from .layout import LayoutMixin
from .notify import NotifyMixin
from .widgets import WidgetsMixin
from .addons import AddonsPage
from .discover import DiscoverPage
from .updates import UpdatesPage
from .share import SharePage
from .sets import SetsPage
from .sessions import SessionsPage
from .linkdata import LinkDataMixin
from .diagnose import DiagnosePage
from .characters import CharactersPage
from .quests import QuestsPage
from .auction import AuctionPage
from .wowhead import WowheadPage
from .settings import SettingsPage
from .common import CommonMixin
from .downloads import DownloadsMixin
from .install import InstallMixin
from .selfupdate import SelfUpdateMixin


class App(LayoutMixin, NotifyMixin, WidgetsMixin, AddonsPage, DiscoverPage, UpdatesPage, SharePage, SetsPage, SessionsPage, LinkDataMixin, DiagnosePage, CharactersPage, QuestsPage, AuctionPage, WowheadPage, SettingsPage, CommonMixin, DownloadsMixin, InstallMixin, SelfUpdateMixin, BaseTk):
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
        self._cleanup_update_file()
        self.after(2500, self._housekeeping)
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

    def _housekeeping(self):
        """Einmal nach dem Start im Hintergrund: Reste abgebrochener Vorgänge zurückholen, alte
        Rückgängig-Versionen aufräumen, Hinweis auf nicht lesbare Schlüssel."""
        if SECRETS_LOST:
            names = {"github_token": "GitHub-Token", "curseforge_key": "CurseForge-API-Key", "discord_webhook": "Discord-Webhook"}
            self.notify(tr("Deine gespeicherten Zugangsdaten ({0}) stammen von einem anderen PC oder Windows-Konto und "
                           "ließen sich hier nicht entschlüsseln. Bitte in den Einstellungen neu eingeben.",
                           ", ".join(names.get(k, k) for k in SECRETS_LOST)), "warn",
                        [(tr("Einstellungen"), lambda: self.show_page("settings"))], key="secrets")
        root, flavor = self.root_path, self.flavor
        if not root or not flavor:
            return
        keep = set(self.sources()) | set(self.cfg.get("parked_sources", {}).get(flavor, {}))

        def work():
            return recover_staging(root, flavor), prune_rollbacks(flavor, keep)

        def done(res):
            back, removed = res
            if removed:
                log.info("%d alte Rückgängig-Versionen gelöscht", removed)
            if back:
                self.notify(tr("Nach einem abgebrochenen Vorgang wiederhergestellt: {0}", ", ".join(back)), "warn")
                self.refresh()
        self.run_quiet(work, done)

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
