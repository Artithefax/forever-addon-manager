"""Hauptfenster: DownloadsMixin."""
from __future__ import annotations

import time
from pathlib import Path

import tkinter as tk

from ..i18n import tr
from ..config import log, save_config
from ..wow import running_wow
from ..files import install_zip
from ..github import asset_key
from ..curseforge import CF_PREFIX, download_matches, zip_name_matches
from .theme import UPD_MARK


class DownloadsMixin:
    """Teil des Hauptfensters (wird in App eingemischt)."""

    # ----- Browser-Downloads automatisch einspielen ----------------------- #
    DL_POLL_MS = 2000

    def watch_download(self, sid: str, since: float | None = None, info: dict | None = None,
                       asset: dict | None = None, manual: bool = False):
        """Erwartet die ZIP eines CurseForge-Addons im Download-Ordner und installiert sie, sobald sie da ist."""
        info = info or self.update_info.get(sid) or {}
        asset = asset or info.get("best")
        if not asset or not asset.get("file_name"):
            return
        since = time.time() - 15 if since is None else since
        old = self.dl_watch.get(sid)
        if old and old["asset"].get("file_id") == asset.get("file_id"):
            old["since"] = min(old["since"], since)
            old["manual"] = old.get("manual") or manual
        else:
            self.dl_watch[sid] = {"info": info, "asset": asset, "since": since, "flavor": self.flavor,
                                  "manual": manual, "new": sid not in self.sources()}
            self._dl_checked.clear()          # schon gesehene Dateien für das neue Ziel erneut prüfen
        if not self._dl_job:
            self._dl_job = self.after(self.DL_POLL_MS, self._poll_downloads)

    def _poll_downloads(self):
        self._dl_job = None
        changed = False
        for sid, w in list(self.dl_watch.items()):          # nur noch Offenes beobachten
            src = self.sources().get(sid)
            if (w["flavor"] != self.flavor or (src and src.get("file_id") == w["asset"].get("file_id"))
                    or (not src and not w.get("new"))):
                del self.dl_watch[sid]
                changed = changed or w.get("manual")
        if changed:
            self.refresh_browser_list()
        if not self.dl_watch:
            return
        if not (self.busy or self.modal_download):
            hit = self._find_download()
            if hit:
                self._install_download(*hit)
        self._dl_job = self.after(self.DL_POLL_MS, self._poll_downloads)

    def _find_download(self):
        try:
            files = list(self.download_dir().glob("*.zip"))
        except OSError:
            return None
        for f in files:
            try:
                st = f.stat()
            except OSError:
                continue
            key, sig = str(f), (st.st_size, st.st_mtime)
            prev, self._dl_seen[key] = self._dl_seen.get(key), sig
            if prev != sig or st.st_size == 0 or self._dl_checked.get(key) == sig:
                continue                              # lädt noch, leer oder schon als unpassend erkannt
            for sid, w in self.dl_watch.items():
                if not zip_name_matches(f.name, w["asset"]["file_name"]):
                    continue
                if download_matches(f, w["asset"], w["since"]):
                    return sid, f
                self.set_status(tr("{0} passt nicht zur erwarteten Version {1} – bitte über „Update bei CurseForge herunterladen“ laden.", f.name, w['asset']['name']))
            self._dl_checked[key] = sig
        return None

    def _install_download(self, sid: str, path: Path):
        w = self.dl_watch.pop(sid)
        info, asset = w["info"], w["asset"]
        root, flavor = self.root_path, self.flavor
        before = dict(self.sources()[sid]) if sid in self.sources() else None

        def job():
            return install_zip(path, root, flavor, rollback=(sid, before) if before else None)

        def done(folders):
            self.remember_cf_source(info, asset, folders)
            self.remember_bundle(folders)
            self.updates.pop(sid, None)
            save_config(self.cfg)
            log.info("Aus dem Download-Ordner aktualisiert: %s (%s) <- %s", info.get("repo"), asset["name"], path)
            self.refresh()
            msg = tr("{0} aus deinem Download-Ordner installiert ({1}).", info.get('repo') or sid, asset['name'])
            if running_wow(root, flavor):
                msg += tr("  WoW läuft – wirkt nach /reload.")
            self.set_status(msg)
            self.notify(msg, "ok", [(tr("↶ Rückgängig"), lambda: self.rollback(sid))] if self.rollback_info(sid) else [],
                        timeout=12000)
            self.refresh_browser_list()
            self.install_dependencies([asset])

        def failed(err):
            self._dl_checked[str(path)] = self._dl_seen.get(str(path))
            self.dl_watch.setdefault(sid, w)          # weiter auf einen neuen Download warten
            if not self._dl_job:
                self._dl_job = self.after(self.DL_POLL_MS, self._poll_downloads)
            log.error("Download %s konnte nicht installiert werden: %s", path, err)
            self.set_status(tr("{0} konnte nicht installiert werden: {1}", path.name, err))

        self.run_bg(tr("Installiere {0}", path.name), job, done, failed)

    def _hint_update_link(self):
        try:
            sel = self.selected_addons()
        except tk.TclError:
            return
        if sel and len(self.tree.selection()) == 1:
            name, url = self.cf_update_link(sel)
            if url:
                self.set_status(tr("{0} Update für {1} verfügbar – „Jetzt aktualisieren“ rechts oder Rechtsklick.", UPD_MARK, name))

    def _on_right_click(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        if iid not in self.tree.selection():
            self.tree.selection_set(iid)
        self.tree.focus(iid)
        sel = self.selected_addons()
        if not sel:
            return
        main = self._group_main(sel)
        items = []
        if any(self.folder_source(a.name)[0] in self.pending_updates() for a in sel):
            items.append((tr("{0} Jetzt aktualisieren", UPD_MARK), lambda: self.update_selected(sel)))
        _, url = self.cf_update_link(sel)
        if url:
            items.append((tr("Bei CurseForge herunterladen ↗"), lambda: self.open_cf_update(sel)))
        sid, src = self.folder_source(main.name)
        if src:
            label = tr("CurseForge-Seite öffnen ↗") if src.get("type") == "curseforge" else tr("Release-Seite öffnen ↗")
            items.append((label, lambda: self.open_link(self.source_page(sid, src))))
            rb = self.rollback_info(sid)
            if rb:
                items.append((tr("↶ Zurück auf {0}", rb["source"].get("tag") or "?"), lambda: self.rollback(sid)))
            items.append((tr("Updates wieder erlauben") if self.is_held(sid) else tr("Festhalten (keine Updates)"),
                          lambda: self.toggle_hold(sid=sid)))
        if items:
            items.append(None)
        items += [(tr("Details"), lambda: self.show_details(main)),
                  (tr("Aktivieren / Deaktivieren"), lambda: self.toggle_selected(sel)),
                  (tr("Ordner öffnen"), lambda: self.open_folder(sel))]
        menu = self._menu(self.tree, items)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    def _on_double_click(self, event):
        iid = self.tree.identify_row(event.y)
        if iid and self._column_at(self.tree.identify_column(event.x)) == "update":
            self.tree.selection_set(iid)
            if self.cf_update_link(self.selected_addons())[1]:
                self.open_cf_update()
                return "break"
        if iid.startswith("grp:"):
            return            # Gruppen klappt Treeview selbst auf/zu
        if iid:
            self.tree.selection_set(iid)
            self.show_details()

    def bundles(self) -> list:
        return self.cfg["bundles"].setdefault(self.flavor, [])

    def remember_bundle(self, folders: list[str]):
        if len(folders) < 2:
            return
        new = set(f.lower() for f in folders)
        kept = [b for b in self.bundles() if not new & {x.lower() for x in b}]
        kept.append(sorted(folders))
        self.cfg["bundles"][self.flavor] = kept
        save_config(self.cfg)

    def _claim_folders(self, sid: str, folders: list[str]):
        """Ein Ordner gehört immer genau einer Quelle (z. B. nach Wechsel GitHub -> CurseForge)."""
        for other, s in self.sources().items():
            if other != sid:
                s["folders"] = [f for f in s.get("folders", []) if f not in folders]

    def remember_source(self, repo: str, info: dict, asset: dict, folders: list[str]):
        if repo.startswith(CF_PREFIX):
            self.remember_cf_source(info, asset, folders)
            return
        self._claim_folders(repo, folders)
        self.sources()[repo] = {"type": "github", "tag": info["tag"], "asset": asset["name"],
                                "asset_key": asset_key(asset["name"]) if asset["name"] != "zipball" else "",
                                "folders": folders}

    def remember_cf_source(self, info: dict, asset: dict, folders: list[str]):
        sid = CF_PREFIX + str(info["mod_id"])
        folders = sorted(set(folders) | set(self.sources().get(sid, {}).get("folders", [])))
        self._claim_folders(sid, folders)
        self.sources()[sid] = {
            "type": "curseforge", "tag": asset["name"], "asset": asset["name"],
            "asset_key": asset_key(asset["name"]), "mod_id": info["mod_id"], "file_id": asset["file_id"],
            "slug": info["slug"], "name": info["repo"], "author": info["author"], "page": info["page"],
            "allow_dl": info["allow_dl"], "game_versions": asset["game_versions"],
            "release_type": asset["release_type"], "folders": folders}
