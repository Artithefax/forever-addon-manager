"""Hauptfenster: UpdatesPage."""
from __future__ import annotations


from tkinter import ttk
import tkinter as tk

from ..constants import APP_VERSION
from ..i18n import tr
from ..config import log, save_config
from ..wow import running_wow
from ..files import backup_wtf, read_rollback, restore_rollback
from ..github import choose_asset, github_install_asset, github_latest
from ..curseforge import (
    CF_PREFIX,
    ManualDownload,
    cf_changelog,
    cf_check,
    cf_install_asset,
    cf_match_installed,
    cf_queue_item,
)
from .theme import UPD_MARK


class UpdatesPage:
    """Teil des Hauptfensters (wird in App eingemischt)."""

    # --- Updates ---------------------------------------------------------------- #
    def _page_updates(self):
        page, head = self._page("updates", tr("Updates"), tr("Addons aktuell halten"))
        right = ttk.Frame(head)
        right.pack(side="right", anchor="s")
        ttk.Button(right, text=tr("{0}  Alle aktualisieren", UPD_MARK), style="Primary.TButton",
                   command=self.update_all).pack(side="right")
        ttk.Button(right, text=tr("Jetzt prüfen"), command=self.check_updates).pack(side="right", padx=(0, 8))
        ttk.Label(right, textvariable=self.upd_count_var).pack(side="right", padx=(0, 16))

        opts = ttk.Frame(page)
        opts.pack(fill="x", pady=(8, 0))
        self._w_check(opts, tr("Beim Start auf Updates prüfen"), self.check_start_var, panel=False).pack(side="left")
        self._w_check(opts, tr("Vor Updates automatisch WTF sichern"), self.auto_backup_var, panel=False).pack(
            side="left", padx=(20, 0))

        self.browser_box_outer = ttk.Frame(page)
        self.browser_box_outer.pack(fill="x")
        self.browser_box = None

        body = ttk.Frame(page)
        body.pack(fill="both", expand=True, pady=(12, 0))
        side = ttk.Frame(body, width=360)
        side.pack(side="right", fill="y", padx=(14, 0))
        side.pack_propagate(False)
        nbox = self._card_box(side, pady=(0, 0), fill="both", expand=True, title=tr("Was ist neu?"))
        nf, self.upd_notes = self._text(nbox)
        nf.pack(fill="both", expand=True)
        self.upd_one_btn = ttk.Button(nbox, text=tr("{0}  Dieses aktualisieren", UPD_MARK), style="Primary.TButton",
                                      command=self.update_selected_in_list)
        self.upd_one_btn.pack(fill="x", pady=(10, 0))
        nb = ttk.Frame(nbox, style="Panel.TFrame")
        nb.pack(fill="x", pady=(6, 0))
        nb.columnconfigure(0, weight=1, uniform="b")
        nb.columnconfigure(1, weight=1, uniform="b")
        self.upd_dl_btn = ttk.Button(nb, text=tr("Im Browser laden ↗"), command=self._upd_open_download)
        self.upd_dl_btn.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        self.upd_page_btn = ttk.Button(nb, text=tr("Projektseite ↗"), command=self._upd_open_page)
        self.upd_page_btn.grid(row=0, column=1, sticky="ew", padx=(3, 0))

        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True)
        frame, self.upd_tree = self._list(left, (("name", tr("Addon")), ("old", tr("Installiert")), ("new", tr("Neu")),
                                                 ("hint", tr("Hinweis"))),
                                          (170, 140, 160, 120), stretch=("name", "new"))
        frame.pack(fill="both", expand=True)
        self.upd_tree.bind("<<TreeviewSelect>>", lambda e: self._upd_show())

        low = ttk.Frame(page)
        low.pack(fill="x", pady=(12, 0))
        b1 = self._card_box(low, pady=(0, 0), side="left", fill="both", expand=True, title=tr("Manuell installierte Addons"))
        ttk.Label(b1, style="Panel.TLabel", wraplength=420, justify="left",
                  text=tr("Verknüpfe sie mit CurseForge, dann prüft Leeroyzan auch diese auf Updates.")).pack(anchor="w")
        ttk.Button(b1, text=tr("Mit CurseForge verknüpfen"), command=self.link_curseforge_dialog).pack(anchor="w", pady=(8, 0))
        b2 = self._card_box(low, pady=(0, 0), side="left", fill="both", expand=True, title=tr("Leeroyzan selbst"),
                            padx=(12, 0))
        self.manager_state = tk.StringVar(value=tr("Installiert: v{0}", APP_VERSION))
        ttk.Label(b2, textvariable=self.manager_state, style="Panel.TLabel", wraplength=420,
                  justify="left").pack(anchor="w")
        mrow = ttk.Frame(b2, style="Panel.TFrame")
        mrow.pack(fill="x", pady=(8, 0))
        ttk.Button(mrow, text=tr("Nach Update suchen"), command=self.check_manager_update).pack(side="right")
        self._w_update_mode(mrow).pack(side="left")

    # ----- Updates-Seite --------------------------------------------------------- #
    def pending_updates(self) -> list[str]:
        return [r for r in self.updates if self.has_update(r)]

    # ----- Festhalten / Rückgängig ------------------------------------------------ #
    def held(self) -> list:
        return self.cfg["held"].setdefault(self.flavor, [])

    def is_held(self, sid: str) -> bool:
        return bool(sid) and sid in self.cfg.get("held", {}).get(self.flavor, [])

    def held_update(self, sid: str) -> str:
        """Neuere Version eines festgehaltenen Addons ("" wenn keine)."""
        src, new = self.sources().get(sid), self.updates.get(sid)
        return new if src and new and new != src.get("tag") and self.is_held(sid) else ""

    def has_update(self, sid: str) -> bool:
        src, new = self.sources().get(sid), self.updates.get(sid)
        return bool(src and new and new != src.get("tag") and not self.is_held(sid))

    def toggle_hold(self, addons=None, sid: str = ""):
        if not sid:
            sel = list(addons) if addons is not None else self.selected_addons()
            if not sel:
                return
            sid = self.folder_source(self._group_main(sel).name)[0]
        if not sid or sid not in self.sources():
            return
        name, held = self.source_name(sid), self.held()
        if sid in held:
            held.remove(sid)
            msg = tr("{0}: Updates sind wieder erlaubt.", name)
        else:
            held.append(sid)
            self.dl_watch.pop(sid, None)
            msg = tr("{0} ist festgehalten und bekommt keine Updates, bis du es wieder freigibst.", name)
        save_config(self.cfg)
        log.info("Festhalten %s: %s", sid, sid in held)
        self.render()
        self.info(msg)

    def rollback_info(self, sid: str) -> dict | None:
        """Gespeicherte Vorversion – nur wenn sie sich von der installierten unterscheidet."""
        src = self.sources().get(sid) if sid else None
        rb = read_rollback(self.flavor, sid) if src else None
        return rb if rb and rb["source"].get("tag") != src.get("tag") else None

    def rollback_selected(self, addons=None):
        sel = list(addons) if addons is not None else self.selected_addons()
        if sel:
            self.rollback(self.folder_source(self._group_main(sel).name)[0])

    def rollback(self, sid: str):
        rb = self.rollback_info(sid)
        if not rb or not self.ready():
            return
        name, old_tag = self.source_name(sid), rb["source"].get("tag") or "?"
        if not self.ask(tr("{0} auf die vorherige Version {1} zurücksetzen? Danach ist das Addon festgehalten, "
                           "damit es nicht gleich wieder aktualisiert wird.", name, old_tag),
                        yes=tr("Zurücksetzen"), no=tr("Abbrechen")):
            return
        root, flavor = self.root_path, self.flavor
        current = list(self.sources()[sid].get("folders", []))

        def done(meta):
            old = dict(meta["source"])
            self._claim_folders(sid, old.get("folders", []))
            self.sources()[sid] = old
            if sid not in self.held():
                self.held().append(sid)
            self.dl_watch.pop(sid, None)
            save_config(self.cfg)
            log.info("Rückgängig: %s -> %s", sid, old.get("tag"))
            self.refresh()
            msg = tr("{0} ist zurück auf {1} und festgehalten.", name, old_tag)
            if running_wow(root, flavor):
                msg += tr("  WoW läuft – wirkt nach /reload.")
            self.set_status(msg)
            self.notify(msg, "ok", [(tr("Freigeben"), lambda: self.toggle_hold(sid=sid))], timeout=12000)

        def failed(err):
            self.set_status(tr("Fehler: {0}", err))
            self.error(str(err))

        self.run_bg(tr("Setze {0} zurück", name), lambda: restore_rollback(root, flavor, sid, current), done, failed)

    # ----- Updates --------------------------------------------------------- #
    def check_updates(self, silent: bool = False):
        if not self.ready(modifies=False, quiet=silent):
            return
        repos = list(self.sources().keys())
        if not repos:
            if not silent:
                self.info(tr("Keine Addons mit Update-Quelle (GitHub/CurseForge) "
                                              "in dieser Version installiert."))
            return
        token, cf_key = self.cfg.get("github_token", ""), self.cf_key()
        srcs = {r: dict(self.sources()[r]) for r in repos}
        flavor, gv = self.flavor, (self.game[0] if self.game else "")

        def job():
            res, errors = {}, []
            for r in repos:
                s = srcs[r]
                try:
                    if s.get("type") == "curseforge":
                        if not cf_key:
                            if not silent:
                                errors.append(tr("{0}: CurseForge-API-Key fehlt (Einstellungen).", s.get('name', r)))
                            continue
                        res[r] = cf_check(s, cf_key, flavor, gv)
                    else:
                        res[r] = github_latest(r, token)
                except Exception as e:  # noqa
                    errors.append(f"{s.get('name', r)}: {e}")
            return res, errors

        def done(out):
            res, errors = out
            for r, info in res.items():
                self.updates[r] = info["tag"]
                self.update_info[r] = info
                src = self.sources().get(r)
                if r.startswith(CF_PREFIX) and src:
                    src["allow_dl"] = info.get("allow_dl", True)
                    # Habe ich die neueste Datei schon (z. B. per Browser/ZIP installiert)? Dann kein Update.
                    if info["tag"] != src.get("tag") and info.get("best"):
                        versions = [a.version for a in self.addons if a.name in src.get("folders", [])]
                        if cf_match_installed(versions, [info["best"]]):
                            self._adopt_cf(r, info["best"])
            save_config(self.cfg)
            self.render()
            for r, info in res.items():
                if r.startswith(CF_PREFIX) and info.get("best") and self.has_update(r):
                    self.watch_download(r)
            n = sum(1 for r in res if self.has_update(r))
            self.schedule_link_write()
            msg = tr("{0} Update(s) verfügbar.", n) if n else tr("Alle Addons sind aktuell.")
            if errors:
                msg += tr("  ({0} Fehler)", len(errors))
                log.warning("Update-Prüfung: %s", "; ".join(errors))
            self.set_status(msg)
            if errors and not silent:
                self.warn("\n".join(errors))

        def failed(err):
            self.set_status(tr("Update-Prüfung fehlgeschlagen: {0}", err))
            if not silent:
                self.error(str(err))

        self.run_bg(tr("Prüfe Updates"), job, done, failed)

    def update_all(self, only: list[str] | None = None):
        pending = [r for r in self.pending_updates() if only is None or r in only]
        if not pending:
            self.info(tr("Keine Updates bekannt – erst „Jetzt prüfen“.") if not self.updates else
                      tr("Alle Addons sind aktuell."))
            return
        if not self.ready():
            return
        root, flavor, token = self.root_path, self.flavor, self.cfg.get("github_token", "")
        cf_key = self.cf_key()
        plan = []
        for r in pending:
            info = self.update_info[r]
            if r.startswith(CF_PREFIX):
                asset = info.get("best")
            else:
                asset = choose_asset(info, flavor, self.sources()[r].get("asset_key", ""))
            if asset:
                plan.append((r, info, asset))

        want_backup = bool(self.cfg.get("auto_backup"))
        before = {r: dict(self.sources()[r]) for r, _, _ in plan}

        def job():
            if want_backup:      # im Hintergrund-Thread nur mit lokalen Werten arbeiten
                try:
                    log.info("Auto-Backup: %s", backup_wtf(root, flavor, "auto"))
                except FileNotFoundError:
                    pass
            ok, manual, errors = [], [], []
            for r, info, asset in plan:
                try:
                    keep = (r, before[r])         # alte Fassung für „Rückgängig“ (verschoben, nicht kopiert)
                    if r.startswith(CF_PREFIX):
                        folders = cf_install_asset(asset, info, root, flavor, cf_key, rollback=keep)
                        if not info.get("notes") and asset.get("file_id"):
                            try:          # Changelog für Leeroylink (Anzeige im Spiel)
                                info["notes"] = cf_changelog(asset.get("mod_id") or info.get("mod_id"), asset["file_id"], cf_key)
                            except Exception:  # noqa – nur ein Extra
                                pass
                    else:
                        folders = github_install_asset(asset, root, flavor, token, rollback=keep)
                    ok.append((r, info, asset, folders))
                except ManualDownload as m:
                    manual.append((info, asset, m))
                except Exception as e:  # noqa
                    log.error("Update %s: %s", r, e)
                    errors.append(f"{info.get('repo', r)}: {e}")
            return ok, manual, errors

        def done(out):
            results, manual, errors = out
            for repo, info, asset, folders in results:
                self.remember_source(repo, info, asset, folders)
                log.info("Aktualisiert: %s -> %s (%s)", repo, info["tag"], asset["name"])
            self.record_link_news(results)
            save_config(self.cfg)
            self.refresh()
            msg = tr("{0} Addon(s) aktualisiert.", len(results))
            if manual or errors:
                msg += tr("  ({0} nicht automatisch)", len(manual) + len(errors))
            self.set_status(msg)
            if results:
                acts = [(tr("↶ Rückgängig"), lambda r=results[0][0]: self.rollback(r))] \
                    if len(results) == 1 and self.rollback_info(results[0][0]) else []
                self.notify(msg, "ok", acts, timeout=12000)
            if errors:
                self.warn(tr("Nicht aktualisiert:") + "\n" + "\n".join(errors))
            if manual:
                self.run_download_queue([cf_queue_item(i, a) for i, a, _ in manual],
                                        tr("Im Browser laden (der Autor sperrt Programm-Downloads):"))
            self.install_dependencies([a for r, _, a, _ in results if r.startswith(CF_PREFIX)])

        self.run_bg(tr("Aktualisiere"), job, done)
