"""Seite „Einstellungen“: WoW-Ordner, CurseForge, Sprache, Speicher, Hilfe. (Mixin, wird in App eingemischt)."""
from __future__ import annotations


from tkinter import ttk, filedialog
import tkinter as tk

from ..constants import HEADER_LINKS
from ..i18n import LANGUAGES, cur_lang, set_language, tr
from ..config import PORTABLE_DIR_NAME, config_dir, portable_dir, save_config
from ..wow import flavor_label, list_flavors
from ..files import ROLLBACK_MAX_DAYS, open_in_explorer, prune_rollbacks, rollback_usage
from ..reports import fmt_bytes
from ..curseforge import CF_GAME_ID, CF_KEY_URL, cf_request
from ..sharing import LIST_PART_SIZES
from .theme import THEME


class SettingsPage:
    """Teil des Hauptfensters (wird in App eingemischt)."""

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
        sbox = self._card_box(lf, pady=(12, 0), title=tr("Speicher"))
        self.rb_label = ttk.Label(sbox, style="Panel.TLabel", text=tr("Rückgängig-Versionen: wird berechnet …"))
        self.rb_label.pack(anchor="w")
        ttk.Label(sbox, style="Panel.TLabel", foreground=THEME["text_faint2"], wraplength=520, justify="left",
                  text=tr("Vor jedem Update bewahrt Leeroyzan die alte Fassung auf, damit „Rückgängig“ geht. "
                          "Vorversionen entfernter Addons und alles, was älter als {0} Tage ist, löscht es von selbst.",
                          ROLLBACK_MAX_DAYS)).pack(anchor="w", pady=(4, 0))
        ttk.Button(sbox, text=tr("Alle Rückgängig-Versionen löschen"), command=self.clear_rollbacks).pack(
            anchor="w", pady=(8, 0))
        gbox = self._card_box(rf, pady=(0, 0), title=tr("Allgemein"))
        lrow = ttk.Frame(gbox, style="Panel.TFrame")
        lrow.pack(fill="x", pady=(0, 12))
        ttk.Label(lrow, text="Language" if cur_lang() == "en" else f"{tr('Sprache')} · Language",
                  style="Panel.TLabel").pack(side="left", padx=(0, 8))
        self.lang_var = tk.StringVar(value=LANGUAGES.get(cur_lang(), "English"))
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
        dbox = self._card_box(rf, pady=(12, 0), title="Discord")
        self._w_discord(dbox).pack(fill="x")
        hbox = self._card_box(rf, pady=(12, 0), title=tr("Hilfe"))
        for text, cmd in ((tr("Log öffnen"), self.open_log), (tr("Datenordner öffnen"), lambda: open_in_explorer(config_dir())),
                          (tr("Addon-Ordner öffnen"), lambda: self.open_folder([])), (tr("Neu einlesen"), self.refresh)):
            ttk.Button(hbox, text=text, command=cmd).pack(anchor="w", pady=(0, 6), fill="x")
        ttk.Label(hbox, style="Panel.TLabel", foreground=THEME["text_faint2"], wraplength=360, justify="left",
                  text=tr("Tastenkürzel: Strg+1 bis Strg+9 wechseln die Seite (Reihenfolge wie links), Strg+, öffnet "
                          "die Einstellungen, Strg+F sucht, F5 liest neu ein, Entf entfernt.")).pack(anchor="w", pady=(2, 8))
        links = ttk.Frame(hbox, style="Panel.TFrame")
        links.pack(fill="x", pady=(6, 0))
        for i, (text, url) in enumerate(HEADER_LINKS):
            links.columnconfigure(i, weight=1, uniform="l")
            ttk.Button(links, text=f"{tr(text)} ↗", command=lambda u=url: self.open_header_link(u)).grid(
                row=0, column=i, sticky="ew", padx=(0 if i == 0 else 6, 0))

    def refresh_rollback_usage(self):
        def done(res, err):
            if err or not res:
                return
            size, count = res
            try:
                self.rb_label.configure(text=tr("Rückgängig-Versionen: {0} für {1} Addons.", fmt_bytes(size), count))
            except (AttributeError, tk.TclError):
                pass
        self.run_quiet(rollback_usage, done)

    def clear_rollbacks(self):
        if not self.flavor or not self.ask(tr("Alle aufbewahrten Vorversionen dieser Spielversion löschen? "
                                              "„Rückgängig“ geht danach erst wieder nach dem nächsten Update.")):
            return
        n = prune_rollbacks(self.flavor, everything=True)
        self.notify(tr("{0} Rückgängig-Versionen gelöscht.", n), "ok", timeout=6000)
        self.refresh_rollback_usage()

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
        if code == cur_lang() or code not in LANGUAGES:
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
