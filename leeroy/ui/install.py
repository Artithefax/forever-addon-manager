"""Hauptfenster: InstallMixin."""
from __future__ import annotations

from pathlib import Path

from tkinter import filedialog

from ..i18n import tr
from ..config import log, save_config
from ..files import install_zip
from ..github import asset_key, choose_asset, github_install_asset, github_latest, parse_repo
from ..curseforge import (
    CFError,
    CF_KEY_URL,
    CF_PREFIX,
    ManualDownload,
    cf_files,
    cf_get_mod,
    cf_get_mods,
    cf_info,
    cf_install_asset,
    cf_match_installed,
    cf_queue_item,
    parse_cf_ref,
    toc_curse_id,
    zip_name_matches,
)
from ..sharing import LIST_EXT, PACK_EXT


class InstallMixin:
    """Teil des Hauptfensters (wird in App eingemischt)."""

    # ----- Installieren ---------------------------------------------------- #
    def install_zip_dialog(self, files=None):
        if not self.ready():
            return
        if not files:
            files = filedialog.askopenfilenames(title=tr("Addon-ZIP wählen"),
                                                filetypes=[(tr("ZIP-Archive"), "*.zip")])
        if not files:
            return
        root, flavor = self.root_path, self.flavor

        def job():
            return [install_zip(Path(f), root, flavor) for f in files]

        def done(res):
            names = [n for part in res for n in part]
            for zp, part in zip(files, res):
                self.remember_bundle(part)
                self._sync_cf_after_zip(Path(zp).name, part)
            save_config(self.cfg)
            log.info("ZIP installiert: %s", ", ".join(names))
            self.refresh()
            self.set_status(tr("Installiert: {0}", ', '.join(names)))

        self.run_bg(tr("Installiere ZIP"), job, done)

    def _sync_cf_after_zip(self, zip_name: str, folders: list[str]):
        """Wurde per ZIP genau die bekannte CurseForge-Datei installiert, gilt das Addon als aktuell."""
        for sid, src in list(self.sources().items()):
            if src.get("type") != "curseforge" or not (set(folders) & set(src.get("folders", []))):
                continue
            info = self.update_info.get(sid)
            asset = next((a for a in (info or {}).get("assets", [])
                          if zip_name_matches(zip_name, a.get("file_name", ""))), None)
            if asset:
                self._adopt_cf(sid, asset)
                self.updates.pop(sid, None)

    def install_github_dialog(self):
        if not self.ready():
            return
        text = self.gh_var.get().strip()
        if not text:
            self.show_page("discover")
            self.info(tr("Gib das GitHub-Repository ein (owner/repo oder Link) und klicke auf „Installieren“."))
            return
        try:
            repo = parse_repo(text)
        except ValueError as e:
            self.error(str(e))
            return
        token = self.cfg.get("github_token", "")

        def done(info):
            self.gh_var.set("")
            self._pick_and_install(info)
        self.run_bg(tr("Lese Release von {0}", repo), lambda: github_latest(repo, token), done)

    def need_cf_key(self) -> str:
        if not self.cf_key():
            self.notify(tr("Für CurseForge brauchst du deinen eigenen, kostenlosen API-Key. Er bleibt nur auf "
                        "diesem Rechner."), "warn", [(tr("Key eintragen"), self.settings_dialog),
                                                    (tr("Key holen ↗"), lambda: self.open_link(CF_KEY_URL))],
                        key="cfkey")
        return self.cf_key()

    def run_download_queue(self, items: list[dict], intro: str):
        """Gesperrte Addons: Download-Seite öffnen und im Hintergrund auf die Datei warten (kein Fenster)."""
        if not items:
            return
        for it in items:
            self.watch_download(CF_PREFIX + str(it["info"]["mod_id"]), info=it["info"], asset=it["asset"],
                                manual=True)
        self.open_link(items[0]["page"])
        names = ", ".join(i["name"] for i in items[:4]) + (" …" if len(items) > 4 else "")
        more = tr(" Die übrigen Download-Seiten findest du unter „Updates“.") if len(items) > 1 else ""
        self.notify(tr("{0} {1}. Klicke im Browser auf den Download – sobald die Datei in {2} liegt, installiert Leeroyzan sie automatisch.{3}", intro, names, self.download_dir(), more), "warn",
                    [(tr("Zur Liste"), lambda: self.show_page("updates"))], key="browser")
        self.refresh_browser_list()

    def _adopt_cf(self, sid: str, asset: dict):
        src = self.sources()[sid]
        src.update(tag=asset["name"], asset=asset["name"], asset_key=asset_key(asset["name"]),
                   file_id=asset["file_id"], game_versions=asset["game_versions"],
                   release_type=asset["release_type"])

    def link_curseforge_dialog(self):
        """Manuell installierte Addons mit CurseForge verknüpfen – danach laufen sie bei den Updates mit."""
        if not self.ready(modifies=False):
            return
        key = self.need_cf_key()
        if not key:
            return
        sel = [a for a in self.selected_addons() if not self.folder_source(a.name)[1]]
        targets: list[tuple] = []          # (Projekt-ID oder Slug, [Addons])
        if sel:
            ids = {a.name: toc_curse_id(a.meta) for a in sel}
            if all(ids.values()):
                by_id: dict = {}
                for a in sel:
                    by_id.setdefault(ids[a.name], []).append(a)
                targets = list(by_id.items())
            else:
                names = ", ".join(a.title for a in sel[:4]) + (" …" if len(sel) > 4 else "")
                text = self.ask_text(tr("CurseForge-Link zu „{0}“ einfügen:", names), ok=tr("Verknüpfen"))
                if not text:
                    return
                try:
                    targets = [(parse_cf_ref(text), sel)]
                except ValueError as e:
                    self.error(str(e))
                    return
        else:
            by_id = {}
            for a in self.addons:
                cid = toc_curse_id(a.meta)
                if cid and not self.folder_source(a.name)[1]:
                    by_id.setdefault(cid, []).append(a)
            if not by_id:
                self.info(tr("Bei den manuell installierten Addons ist keine CurseForge-Kennung "
                                              "hinterlegt.\n\nTipp: Wähle ein Addon in der Liste aus und klicke "
                                              "erneut – dann kannst du den CurseForge-Link einfügen."))
                return
            targets = list(by_id.items())
        flavor, gv = self.flavor, (self.game[0] if self.game else "")

        def job():
            results, errors, mods = [], [], {}
            ids = [r for r, _ in targets if isinstance(r, int)]
            if len(ids) > 1:
                try:
                    mods = {m["id"]: m for m in cf_get_mods(ids, key)}
                except CFError:
                    mods = {}
            for ref, addons in targets:
                try:
                    mod = mods.get(ref) or cf_get_mod(ref, key)
                    files = cf_files(mod["id"], key)
                    if not files:
                        raise CFError(tr("keine ZIP-Dateien bei CurseForge gefunden"))
                    info = cf_info(mod, files, flavor, gv)
                    versions = [a.version for a in addons]
                    results.append((info, cf_match_installed(versions, info["assets"]),
                                    [a.name for a in addons], versions))
                except Exception as e:  # noqa
                    errors.append(f"{addons[0].title}: {e}")
            return results, errors

        def done(out):
            results, errors = out
            unsure = [r for r in results if not r[1]]
            linked = 0
            for info, match, folders, _ in results:
                if match:
                    self.remember_cf_source(info, match, folders)
                    linked += 1
            if unsure:
                names = "\n".join(tr("• {0}  (installiert: {1}, neueste Datei: {2})", i['repo'],
                                     ', '.join(v for v in vers if v) or '?', i['tag']) for i, _, _, vers in unsure)
                current = self.ask(tr("Bei diesen Addons lässt sich die installierte Version keiner CurseForge-Datei zuordnen:\n\n{0}\n\nSollen sie als aktuell gelten?\n\nJa = die neueste Datei gilt als installiert.\nNein = sie werden als veraltet behandelt, das nächste Update installiert die neueste Datei.", names))
                for info, _, folders, _ in unsure:
                    self.remember_cf_source(info, info["best"] if current else dict(info["best"], name="", file_id=None),
                                            folders)
                    linked += 1
            save_config(self.cfg)
            self.refresh()
            self.set_status(tr("{0} Addon(s) mit CurseForge verknüpft.", linked))
            if errors:
                self.warn(tr("Nicht verknüpft:") + "\n\n" + "\n".join(errors))
            if linked:
                self.after(200, self.check_updates)

        self.run_bg(tr("Verknüpfe mit CurseForge"), job, done)

    def _pick_and_install_cf(self, info: dict):
        best = info["best"] or info["assets"][0]
        asset = best
        if len(info["assets"]) > 1:
            labels = [a.get("label", a["name"]) for a in info["assets"]]
            idx = self.ask_choice(tr("{0} von {1} bietet mehrere Dateien an. Welche passt zu deiner Spielversion? (Die Wahl gilt auch für spätere Updates.)", info['repo'], info['author'] or 'unbekannt'),
                                  labels, info["assets"].index(best), ok=tr("Installieren"))
            if idx is None:
                self.set_status(tr("Abgebrochen."))
                return
            asset = info["assets"][idx]
        self._install_cf_asset(info, asset)

    def _install_cf_asset(self, info: dict, asset: dict):
        root, flavor, key = self.root_path, self.flavor, self.cf_key()

        def done(folders):
            self.remember_cf_source(info, asset, folders)
            self.remember_bundle(folders)
            self.updates.pop(CF_PREFIX + str(info["mod_id"]), None)
            save_config(self.cfg)
            log.info("CurseForge installiert: %s (%s) -> %s", info["repo"], asset["name"], ", ".join(folders))
            self.refresh()
            self.set_status(tr("{0} von {1} installiert: {2}  ·  Quelle: {3}", info['repo'], info['author'] or '?', ', '.join(folders), info['page']))
            self.notify(tr("{0} von {1} ist installiert.", info['repo'], info['author'] or '?'), "ok",
                        [(tr("Projektseite ↗"), lambda: self.open_link(info["page"]))], timeout=12000)
            if getattr(self, "cf_tree", None):
                self.cf_show_rows()
            self.install_dependencies([asset])

        def failed(err):
            if isinstance(err, ManualDownload):
                self.set_status(tr("Download über den Browser nötig."))
                self.run_download_queue([cf_queue_item(info, asset)], f"{info['repo']}: {err.reason}")
            else:
                self.set_status(tr("Fehler: {0}", err))
                self.error(str(err))

        self.run_bg(tr("Lade {0}", asset['name']), lambda: cf_install_asset(asset, info, root, flavor, key),
                    done, failed)

    def _pick_and_install(self, info: dict):
        best = choose_asset(info, self.flavor)
        if not best:
            self.error(tr("{0}: Das Release enthält keine Dateien.", info['repo']))
            return
        asset = best
        if len(info["assets"]) > 1:
            labels = [a.get("label", a["name"]) for a in info["assets"]]
            idx = self.ask_choice(tr("{0} {1} bietet mehrere Dateien an. Welche passt zu deiner Spielversion? (Die Wahl gilt auch für spätere Updates.)", info['repo'], info['tag']),
                                  labels, info["assets"].index(best) if best in info["assets"] else 0,
                                  ok=tr("Installieren"))
            if idx is None:
                self.set_status(tr("Abgebrochen."))
                return
            asset = info["assets"][idx]
        root, flavor, token = self.root_path, self.flavor, self.cfg.get("github_token", "")

        def done(folders):
            self.remember_source(info["repo"], info, asset, folders)
            self.remember_bundle(folders)
            self.updates.pop(info["repo"], None)
            save_config(self.cfg)
            log.info("GitHub installiert: %s %s (%s) -> %s", info["repo"], info["tag"],
                     asset["name"], ", ".join(folders))
            self.refresh()
            self.set_status(tr("{0} {1} installiert: {2}", info['repo'], info['tag'], ', '.join(folders)))

        self.run_bg(tr("Lade {0}", asset['name']),
                    lambda: github_install_asset(asset, root, flavor, token), done)


    # ----- Drag & Drop ----------------------------------------------------- #
    def on_drop(self, event):
        paths = [Path(p) for p in self.tk.splitlist(event.data)]
        zips = [str(p) for p in paths if p.suffix.lower() == ".zip"]
        packs = [str(p) for p in paths if p.suffix.lower() == PACK_EXT]
        lists = [str(p) for p in paths if p.suffix.lower() == LIST_EXT]
        if not zips and not packs and not lists:
            self.set_status(tr("Nur .zip-, .lzpack- und .lzlist-Dateien können abgelegt werden."))
            return
        if lists:
            self.after(50, lambda: self.import_list_dialog(lists[0]))
        elif packs:
            self.after(50, lambda: self.import_pack_dialog(packs[0]))
        elif zips:
            self.after(50, lambda: self.install_zip_dialog(zips))
