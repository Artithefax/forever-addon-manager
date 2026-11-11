"""Hauptfenster: SetsPage."""
from __future__ import annotations


from tkinter import ttk

from ..i18n import tr
from ..config import BACKUP_DIR, log, save_config
from ..files import backup_wtf, list_backups, open_in_explorer, restore_backup, set_enabled
from .dialogs import BACKUP_KINDS, Placeholder, SET_ALL_OFF, SET_ALL_ON


class SetsPage:
    """Teil des Hauptfensters (wird in App eingemischt)."""

    # --- Sets & Backups ------------------------------------------------------------ #
    def _page_sets(self):
        page, _ = self._page("sets", tr("Sets & Backups"), tr("Addon-Gruppen und Sicherungen"))
        cols = ttk.Frame(page)
        cols.pack(fill="both", expand=True, pady=(12, 0))
        cols.columnconfigure(0, weight=1, uniform="c")
        cols.columnconfigure(1, weight=1, uniform="c")
        cols.rowconfigure(0, weight=1)
        lf = ttk.Frame(cols)
        lf.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        rf = ttk.Frame(cols)
        rf.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        box = self._card_box(lf, pady=(0, 0), fill="both", expand=True, title=tr("Addon-Sets"))
        ttk.Label(box, style="Panel.TLabel", wraplength=460, justify="left",
                  text=tr("Ein Set merkt sich, welche Addons aktiv sind. „Anwenden“ schaltet genau diese ein "
                       "und alle anderen aus – z. B. für Leveln, Raid oder einen Login ohne Addons.")).pack(anchor="w")
        frame, self.sets_tree = self._list(box, (("name", tr("Set")), ("count", tr("Addons"))), (260, 80),
                                           stretch=("name",), height=4)
        frame.pack(fill="both", expand=True, pady=(10, 0))
        self.sets_tree.bind("<<TreeviewSelect>>", lambda e: self._set_selected())
        self.sets_tree.bind("<Double-1>", lambda e: self.apply_set())
        self.set_members = ttk.Label(box, style="Panel.TLabel", wraplength=460, justify="left")
        self.set_members.pack(anchor="w", pady=(8, 0))
        row = ttk.Frame(box, style="Panel.TFrame")
        row.pack(fill="x", pady=(8, 0))
        ttk.Button(row, text=tr("Anwenden"), style="Primary.TButton", command=self.apply_set).pack(side="left")
        ttk.Button(row, text=tr("Löschen"), style="Danger.TButton", command=self.delete_set).pack(side="left", padx=(6, 0))
        srow = ttk.Frame(box, style="Panel.TFrame")
        srow.pack(fill="x", pady=(12, 0))
        e = ttk.Entry(srow, textvariable=self.set_name_var)
        e.pack(side="left", fill="x", expand=True)
        Placeholder(e, self.set_name_var, tr("Name für ein neues Set"))
        e.bind("<Return>", lambda ev: self.save_set())
        ttk.Button(srow, text=tr("Aktive als Set speichern"), command=self.save_set).pack(side="left", padx=(8, 0))

        bbox = self._card_box(rf, pady=(0, 0), fill="both", expand=True, title=tr("Backups · WTF-Ordner"))
        ttk.Label(bbox, style="Panel.TLabel", wraplength=460, justify="left",
                  text=tr("Gesichert werden alle Addon-Einstellungen (SavedVariables) dieser Spielversion.")).pack(anchor="w")
        frame, self.backup_tree = self._list(bbox, (("when", tr("Zeitpunkt")), ("kind", tr("Art"))), (170, 200),
                                             stretch=("kind",), height=4)
        frame.pack(fill="both", expand=True, pady=(10, 0))
        brow = ttk.Frame(bbox, style="Panel.TFrame")
        brow.pack(fill="x", pady=(8, 0))
        ttk.Button(brow, text=tr("Jetzt sichern"), style="Primary.TButton", command=self.backup).pack(side="left")
        ttk.Button(brow, text=tr("Wiederherstellen"), command=self.restore_dialog).pack(side="left", padx=(6, 0))
        ttk.Button(brow, text=tr("Ordner öffnen"),
                   command=lambda: (BACKUP_DIR.mkdir(parents=True, exist_ok=True),
                                    open_in_explorer(BACKUP_DIR))).pack(side="right")
        self._w_check(bbox, tr("Vor Updates automatisch sichern"), self.auto_backup_var).pack(anchor="w", pady=(10, 0))

    # ----- Sets & Backups -------------------------------------------------------- #
    def reload_sets(self, select: str = ""):
        self.refresh_sets_overview(select)
        self.schedule_link_write()

    def refresh_sets_overview(self, select: str = ""):
        tv = getattr(self, "sets_tree", None)
        if tv is None:
            return
        cur = select or self.set_var.get()
        tv.delete(*tv.get_children())
        tv.insert("", "end", iid=SET_ALL_OFF, values=(tr(SET_ALL_OFF), "0"), tags=["dim"])
        tv.insert("", "end", iid=SET_ALL_ON, values=(tr(SET_ALL_ON), str(len(self.addons))), tags=["dim"])
        for i, (name, members) in enumerate(sorted(self.sets().items(), key=lambda kv: kv[0].lower())):
            tv.insert("", "end", iid=f"set:{name}", values=(name, str(len(members))), tags=["odd"] if i % 2 else [])
        iid = f"set:{cur}" if cur in self.sets() else (cur if cur in (SET_ALL_OFF, SET_ALL_ON) else "")
        if iid and tv.exists(iid):
            tv.selection_set(iid)
        self._set_selected()

    def _set_selected(self):
        sel = self.sets_tree.selection()
        name = ""
        if sel:
            name = sel[0][4:] if sel[0].startswith("set:") else sel[0]
        self.set_var.set(name)
        if name in self.sets():
            self.set_members.configure(text=", ".join(sorted(self.sets()[name], key=str.lower)))
        elif name == SET_ALL_OFF:
            self.set_members.configure(text=tr("Schaltet alle Addons aus."))
        elif name == SET_ALL_ON:
            self.set_members.configure(text=tr("Schaltet alle Addons ein."))
        else:
            self.set_members.configure(text=tr("Wähle ein Set aus – oder speichere die gerade aktiven Addons "
                                            "als neues Set."))

    def refresh_backup_list(self):
        tv = getattr(self, "backup_tree", None)
        if tv is None:
            return
        tv.delete(*tv.get_children())
        self._backups = list_backups(self.flavor) if self.flavor else []
        for i, b in enumerate(self._backups):
            tv.insert("", "end", iid=str(i), values=(b["when"].strftime(tr("%d.%m.%Y  %H:%M")),
                                                     tr(BACKUP_KINDS.get(b["kind"], b["kind"]))),
                      tags=["odd"] if i % 2 else [])
        if self._backups:
            tv.selection_set("0")

    # ----- Sets ------------------------------------------------------------ #
    def save_set(self):
        if not self.root_path or not self.flavor:
            return
        active = sorted(a.name for a in self.addons if a.enabled)
        if not active:
            self.info(tr("Es sind keine Addons aktiv."))
            return
        name = self.set_name_var.get()
        if not name or not name.strip():
            self.show_page("sets")
            self.info(tr("Gib unten einen Namen für das neue Set ein ({0} aktive Addons).", len(active)))
            return
        name = name.strip()
        if name in (SET_ALL_OFF, SET_ALL_ON):
            return
        if name in self.sets() and not self.ask(tr("Set „{0}“ überschreiben?", name)):
            return
        self.sets()[name] = active
        save_config(self.cfg)
        self.set_name_var.set("")
        log.info("Set gespeichert: %s (%d Addons)", name, len(active))
        self.reload_sets(name)
        self.refresh_sets_overview()
        self.set_status(tr("Set „{0}“ mit {1} Addons gespeichert.", name, len(active)))

    def delete_set(self):
        name = self.set_var.get()
        if name not in self.sets():
            return
        if self.ask(tr("Set „{0}“ löschen? Die Addons selbst bleiben erhalten.", name), yes=tr("Löschen"), no=tr("Abbrechen")):
            del self.sets()[name]
            save_config(self.cfg)
            log.info("Set gelöscht: %s", name)
            self.reload_sets()
            self.refresh_sets_overview()

    def apply_set(self):
        name = self.set_var.get()
        if not name:
            self.info(tr("Wähle zuerst ein Set aus."))
            return
        if not self.ready():
            return
        if name == SET_ALL_OFF:
            wanted = set()
        elif name == SET_ALL_ON:
            wanted = {a.name.lower() for a in self.addons}
        else:
            wanted = {n.lower() for n in self.sets().get(name, [])}
        changes, errors = 0, []
        for a in self.addons:
            should = a.name.lower() in wanted
            if a.enabled != should:
                try:
                    set_enabled(a, self.root_path, self.flavor, should)
                    changes += 1
                except Exception as e:  # noqa
                    errors.append(f"{a.name}: {e}")
        installed = {a.name.lower() for a in self.addons}
        missing = sorted(n for n in self.sets().get(name, []) if n.lower() not in installed)
        log.info("Set angewendet: %s (%d Änderungen)", name, changes)
        self.refresh()
        msg = tr("Set „{0}“ angewendet: {1} Änderung(en).", name, changes)
        if missing:
            msg += tr("  Nicht installiert: {0}", ', '.join(missing))
        self.set_status(msg)
        if errors:
            self.error("\n".join(errors))

    # ----- Backups --------------------------------------------------------- #
    def backup(self):
        if not self.ready(modifies=False):
            return
        root, flavor = self.root_path, self.flavor

        def done(path):
            log.info("Backup erstellt: %s", path)
            self.refresh_backup_list()
            self.update_stats()
            self.set_status(tr("Backup erstellt: {0}", path))
            self.notify(tr("Backup erstellt: {0}", path.name), "ok", [(tr("Ordner öffnen"), lambda: open_in_explorer(path.parent))],
                        key="backup", timeout=15000)

        self.run_bg(tr("Sichere WTF"), lambda: backup_wtf(root, flavor), done)

    def restore_dialog(self):
        if not self.root_path or not self.flavor:
            return
        backups = list_backups(self.flavor)
        if not backups:
            self.info(tr("Für {0} gibt es noch keine Backups – lege eins mit „Jetzt sichern“ an.", self.flavor))
            return
        self.show_page("sets")
        sel = self.backup_tree.selection()
        if not sel:
            self.info(tr("Wähle in der Liste das Backup aus, das du zurückholen willst."))
            return
        chosen = self._backups[int(sel[0])]
        if not self.ask(tr("Stand vom {0:%d.%m.%Y %H:%M} wiederherstellen? Dein aktueller Stand wird vorher automatisch gesichert.", chosen['when']), yes=tr("Wiederherstellen"), no=tr("Abbrechen")):
            return
        if not self.ready():
            return
        root, flavor = self.root_path, self.flavor

        def done(safety):
            log.info("Backup wiederhergestellt: %s (vorher gesichert: %s)", chosen["path"], safety)
            self.refresh_backup_list()
            self.update_stats()
            self.set_status(tr("Stand vom {0:%d.%m.%Y %H:%M} wiederhergestellt.", chosen['when']))
            self.notify(tr("Der Stand vom {0:%d.%m.%Y %H:%M} ist wiederhergestellt. Dein vorheriger Stand wurde als „vor Wiederherstellung“ gesichert.", chosen['when']), "ok", timeout=15000)

        self.run_bg(tr("Stelle Backup wieder her"),
                    lambda: restore_backup(chosen["path"], root, flavor), done)
