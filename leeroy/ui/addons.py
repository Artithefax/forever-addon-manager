"""Hauptfenster: AddonsPage."""
from __future__ import annotations

import shutil

from tkinter import ttk
import tkinter as tk

from ..i18n import tr
from ..config import log, save_config
from ..wow import Addon, clean_wow_text, compute_groups
from ..files import addons_dir, open_in_explorer, plain_key, scan_addons, set_enabled
from ..curseforge import (
    CFError,
    CF_PREFIX,
    ManualDownload,
    cf_download_url,
    cf_files,
    cf_get_mod,
    cf_info,
    cf_install_asset,
    cf_queue_item,
)
from .theme import THEME, UPD_MARK, spaced


class AddonsPage:
    """Teil des Hauptfensters (wird in App eingemischt)."""

    # --- Meine Addons --------------------------------------------------------- #
    def _page_addons(self):
        t = THEME
        page, head = self._page("addons", tr("Addons & UI"), tr("Meine Addons"))
        right = ttk.Frame(head)
        right.pack(side="right", anchor="s")
        ttk.Button(right, text=tr("+  Addons hinzufügen"), style="Primary.TButton",
                   command=lambda: self.show_page("discover")).pack(side="right")
        self.view_btns = {}
        seg = ttk.Frame(right)
        seg.pack(side="right", padx=(0, 14))
        for key, label in (("list", tr("Liste")), ("cards", tr("Kacheln"))):
            b = ttk.Button(seg, text=label, width=8, command=lambda k=key: self.set_view(k))
            b.pack(side="left")
            self.view_btns[key] = b
        ttk.Checkbutton(right, text=tr("Gruppieren"), variable=self.group_var, style="Plain.TCheckbutton",
                        command=self._toggle_grouping).pack(side="right", padx=(0, 16))
        self.filter_entry = self._w_filter(right, width=22)
        self.filter_entry.pack(side="right", padx=(0, 16), ipady=3)
        ttk.Label(right, text=tr("Suche")).pack(side="right", padx=(0, 8))

        ttk.Label(page, textvariable=self.count_var, style="Body.TLabel").pack(anchor="w", pady=(6, 0))
        body = ttk.Frame(page)
        body.pack(fill="both", expand=True, pady=(8, 0))
        detail = ttk.Frame(body, style="Detail.TFrame", width=300, padding=(18, 16))
        detail.pack(side="right", fill="y")
        detail.pack_propagate(False)
        tk.Frame(body, width=1, bg=t["line"]).pack(side="right", fill="y")
        self._build_detail(detail)

        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True, padx=(0, 14))
        bar = tk.Frame(left, bg=t["panel"], highlightthickness=1, highlightbackground=t["border"])
        bar.pack(side="bottom", fill="x", pady=(10, 0))
        barin = ttk.Frame(bar, style="Panel.TFrame", padding=(10, 8))
        barin.pack(fill="x")
        for text, cmd in ((tr("Alle auswählen"), self.select_all), (tr("An / Aus"), self.toggle_selected),
                          (tr("Teilen …"), lambda: self.share_selection())):
            ttk.Button(barin, text=text, command=cmd).pack(side="left", padx=(0, 6))
        ttk.Button(barin, text=tr("Entfernen"), command=self.remove_selected, style="Danger.TButton").pack(side="left")
        self.list_host = ttk.Frame(left)
        self.list_host.pack(fill="both", expand=True)
        self.tree_frame = self._w_tree(self.list_host)
        self.cards_wrap, self.cards_inner = self._scroll_area(self.list_host, t["bg"])
        self.set_view(self.view, save=False)

    def set_view(self, view: str, save: bool = True):
        self.view = view
        if save:
            self.cfg["addon_view"] = view
            save_config(self.cfg)
        for k, b in self.view_btns.items():
            b.configure(style="Primary.TButton" if k == view else "TButton")
        if view == "cards":
            self.tree_frame.pack_forget()
            self.cards_wrap.pack(fill="both", expand=True)
        else:
            self.cards_wrap.pack_forget()
            self.tree_frame.pack(fill="both", expand=True)
        if save:
            self.render()

    def _build_detail(self, parent):
        self.detail = {
            "kicker": ttk.Label(parent, style="DetailKicker.TLabel"),
            "title": ttk.Label(parent, style="DetailTitle.TLabel", wraplength=260),
            "note": ttk.Label(parent, style="DetailNote.TLabel", wraplength=260, justify="left"),
        }
        self.detail["kicker"].pack(anchor="w")
        self.detail["title"].pack(anchor="w", pady=(4, 0))
        self.detail["note"].pack(anchor="w", pady=(6, 0))
        grid = ttk.Frame(parent, style="Detail.TFrame")
        grid.pack(fill="x", pady=(14, 0))
        for i, (key, label) in enumerate((("version", tr("Version")), ("release", tr("Quelle")),
                                           ("iface", "Interface"), ("folders", tr("Ordner")),
                                           ("deps", tr("Benötigt")))):
            ttk.Label(grid, text=label, style="DetailKey.TLabel").grid(row=i, column=0, sticky="nw", pady=4)
            v = ttk.Label(grid, style="DetailVal.TLabel", wraplength=170, justify="left")
            v.grid(row=i, column=1, sticky="nw", padx=(12, 0), pady=4)
            self.detail[key] = v
        btns = ttk.Frame(parent, style="Detail.TFrame")
        btns.pack(fill="x", side="bottom")
        btns.columnconfigure((0, 1), weight=1, uniform="detail")
        d = self.detail
        d["upd_btn"] = ttk.Button(btns, text=tr("{0}  Jetzt aktualisieren", UPD_MARK), style="Primary.TButton",
                                  command=self.update_selected)
        d["dl_btn"] = ttk.Button(btns, text=tr("Bei CurseForge herunterladen ↗"), command=self.open_cf_update)
        d["rb_btn"] = ttk.Button(btns, text="", command=self.rollback_selected)
        d["link_btn"] = ttk.Button(btns, text=tr("Mit CurseForge verknüpfen"), command=self.link_curseforge_dialog)
        d["page_btn"] = ttk.Button(btns, text=tr("Quelle ↗"), command=self._detail_release)
        d["folder_btn"] = ttk.Button(btns, text=tr("Ordner"), command=self.open_folder)
        d["hold_btn"] = ttk.Button(btns, text=tr("Festhalten"), command=self.toggle_hold)
        d["rm_btn"] = ttk.Button(btns, text=tr("Entfernen"), command=self.remove_selected, style="Danger.TButton")
        self.update_detail()

    DETAIL_BTNS = ("upd_btn", "dl_btn", "rb_btn", "link_btn", "page_btn", "folder_btn", "hold_btn", "rm_btn")

    def update_detail(self):
        if not self.detail:
            return
        d = self.detail
        try:
            sel = self.selected_addons()
        except tk.TclError:
            return
        for k in self.DETAIL_BTNS:
            d[k].grid_forget()
        if not sel:
            d["kicker"].configure(text=spaced(tr("Details")))
            d["title"].configure(text=tr("Kein Addon gewählt"))
            d["note"].configure(text=tr("Wähle links ein Addon oder eine Gruppe aus.") if self.addons else
                                tr("Noch keine Addons installiert – über „Addons hinzufügen“ geht es los."))
            for k in ("version", "release", "iface", "folders", "deps"):
                d[k].configure(text="–", foreground=THEME["text"])
            return
        names = {a.name for a in sel}
        main = self._group_main(sel)
        sid, src = self.folder_source(main.name)
        d["kicker"].configure(text=spaced(self.source_label(sid, src).replace(": ", " · "))
                              if src else spaced(tr("Manuell installiert")))
        d["title"].configure(text=main.title if len(sel) == 1 else tr("{0}  ({1} Ordner)", main.title, len(sel)))
        d["note"].configure(text=clean_wow_text(main.meta.get("notes", "")) or tr("Keine Beschreibung in der .toc."))
        d["version"].configure(text=main.version or "–")
        pending = self.has_update(sid)
        if src:
            if pending:
                d["release"].configure(text=tr("{0} Update: {1}", UPD_MARK, self.updates[sid]), foreground=THEME["update"])
            elif self.is_held(sid):
                newer = self.held_update(sid)
                d["release"].configure(text=src.get("tag", "–") + tr(" · festgehalten")
                                       + (tr(" (neu: {0})", newer) if newer else ""), foreground=THEME["text_dim"])
            else:
                d["release"].configure(text=src.get("tag", "–") + (tr("  · aktuell") if sid in self.updates else ""),
                                       foreground=THEME["text"])
        else:
            d["release"].configure(text=tr("manuell – keine Update-Prüfung"), foreground=THEME["text_dim"])
        if self.is_outdated(main):
            d["iface"].configure(text=tr("{0} · veraltet", main.interface), foreground=THEME["old"])
        else:
            d["iface"].configure(text=(main.interface or "–") + (tr(" · passt") if self.game and main.interfaces else ""),
                                 foreground=THEME["text"])
        d["folders"].configure(text=", ".join(sorted(names)), foreground=THEME["text"])
        deps = sorted({x for a in sel for x in a.deps if x not in names})
        d["deps"].configure(text=", ".join(deps) or "–", foreground=THEME["text"])
        full, half = [], []
        if pending:
            full.append("upd_btn")
            if self.cf_update_link(sel)[1]:
                full.append("dl_btn")
        rb = self.rollback_info(sid)
        if rb:
            d["rb_btn"].configure(text=tr("↶ Zurück auf {0}", rb["source"].get("tag") or "?"))
            full.append("rb_btn")
        if not src:
            full.append("link_btn")
        else:
            half.append("page_btn")
        half.append("folder_btn")
        if src:
            d["hold_btn"].configure(text=tr("Freigeben") if self.is_held(sid) else tr("Festhalten"))
            half.append("hold_btn")
        half.append("rm_btn")
        for row, k in enumerate(full):
            d[k].grid(row=row, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        for i, k in enumerate(half):
            d[k].grid(row=len(full) + i // 2, column=i % 2, sticky="ew", pady=(0, 6),
                      padx=(0, 3) if i % 2 == 0 else (3, 0))

    def _detail_release(self):
        sel = self.selected_addons()
        if not sel:
            return
        sid, src = self.folder_source(self._group_main(sel).name)
        if src:
            self.open_link(self.source_page(sid, src))

    # ----- Anzeige --------------------------------------------------------- #
    def refresh(self):
        if self.root_path and self.flavor:
            self.addons = scan_addons(self.root_path, self.flavor, self.game[1] if self.game else None)
        else:
            self.addons = []
        # Quellen aufräumen, deren Ordner nicht mehr existieren
        present = {a.name for a in self.addons}
        for sid in list(self.sources()):
            s = self.sources()[sid]
            s["folders"] = [f for f in s.get("folders", []) if f in present]
            if not s["folders"]:
                del self.sources()[sid]
        save_config(self.cfg)
        self.render()
        self.schedule_link_write()
        if not self.busy:
            self.set_status(tr("{0} Addons in {1} gefunden.", len(self.addons), self.flavor or '–'))

    def is_outdated(self, a: Addon) -> bool:
        return bool(self.game and a.interfaces and self.game[1] not in a.interfaces)

    def row_values(self, a: Addon):
        enabled_names = {x.name.lower() for x in self.addons if x.enabled}
        missing = [d for d in a.deps if d.lower() not in enabled_names]
        outdated = self.is_outdated(a)
        if not a.valid:
            status = tr("keine .toc")
        elif not a.enabled:
            status = tr("deaktiviert")
        elif missing:
            status = tr("fehlt: {0}", ", ".join(missing))
        elif outdated:
            status = tr("veraltet")
        else:
            status = tr("aktiv")
        sid, src = self.folder_source(a.name)
        source = self.source_label(sid, src) if src else tr("manuell")
        upd = ""
        has_update = self.has_update(sid)
        if src:
            upd = (f"{UPD_MARK} {self.updates[sid]}" + ("  (Browser)" if src.get("allow_dl") is False else "")
                   if has_update else src.get("tag", "") + (tr(" · festgehalten") if self.is_held(sid) else ""))
        if has_update:
            status = f"{UPD_MARK} {status}"
        tags = []
        if not a.enabled:
            tags.append("disabled")
        elif missing or not a.valid:
            tags.append("warn")
        elif outdated:
            tags.append("old")
        if has_update:
            tags.append("update")
        return (status, a.name, a.title, a.version, a.interface, source, upd), tags

    def group_row(self, g: dict, child_rows: list):
        """Werte und Tags für die Zeile einer Gruppe."""
        members = g["members"]
        on = sum(1 for m in members if m.enabled)
        n = len(members)
        status = tr("aktiv") if on == n else (tr("deaktiviert") if on == 0 else tr("{0}/{1} aktiv", on, n))
        child_tags = [t for _, tags, _ in child_rows for t in tags]
        if "warn" in child_tags:
            status += tr("  ·  Hinweis")
        elif "old" in child_tags:
            status += "  ·  veraltet"
        if "update" in child_tags:
            status = f"{UPD_MARK} {status}"
        root = g["root"]
        ref = root or members[0]
        rvals, _ = self.row_values(ref)
        same = lambda i: rvals[i] if all(r[0][i] == rvals[i] for r in child_rows) else ""  # noqa
        title = root.title if root else g["title"]
        values = (status, f"{g['title']}  ({n})", title,
                  rvals[3] if root else same(3), rvals[4] if root else same(4),
                  same(5) or (rvals[5] if root else "gemischt"), same(6) or rvals[6])
        tags = ["group"]
        if on == 0:
            tags.append("disabled")
        elif "warn" in child_tags:
            tags.append("warn")
        elif "old" in child_tags:
            tags.append("old")
        if "update" in child_tags:
            tags.append("update")
        return values, tags

    def render(self):
        if not hasattr(self, "ui"):
            return
        has_tree = self.tree is not None and self.tree.winfo_exists()
        sel_before = set(self.tree.selection()) if has_tree else set()
        if has_tree:
            self.tree.delete(*self.tree.get_children())
        self.group_members: dict[str, list[str]] = {}
        q = self.filter_var.get().lower().strip()
        idx = self.COLUMNS.index(self.sort_col)
        grouping = bool(self.group_var.get())

        def matches(vals):
            return not q or any(q in str(v).lower() for v in vals)

        if grouping:
            src_bundles = [s.get("folders", []) for s in self.sources().values()]
            groups = compute_groups(self.addons, self.bundles() + src_bundles)
        else:
            groups = [{"title": a.name, "root": None, "members": [a]} for a in self.addons]

        entries = []   # (sortkey, kind, payload)
        for g in groups:
            child_rows = [(*self.row_values(m), m) for m in g["members"]]
            if len(g["members"]) == 1:
                vals, tags, a = child_rows[0]
                if matches(vals):
                    entries.append((str(vals[idx]).lower(), "single", (vals, tags, a)))
                continue
            gvals, gtags = self.group_row(g, child_rows)
            if not (matches(gvals) or any(matches(r[0]) for r in child_rows)):
                continue
            entries.append((str(gvals[idx]).lower(), "group", (g, gvals, gtags, child_rows)))
        entries.sort(key=lambda e: e[0], reverse=self.sort_rev)

        for i, (_, kind, payload) in enumerate(entries if has_tree else []):
            stripe = ["odd"] if i % 2 else []
            if kind == "single":
                vals, tags, a = payload
                self.tree.insert("", "end", iid=a.name, values=vals, tags=tags + stripe)
                continue
            g, gvals, gtags, child_rows = payload
            gid = f"grp:{g['title'].lower()}"
            self.group_members[gid] = [m.name for m in g["members"]]
            self.tree.insert("", "end", iid=gid, values=gvals, tags=gtags + stripe,
                             open=gid in self.open_groups)
            for vals, tags, a in child_rows:
                vals = (vals[0], "   " + vals[1], *vals[2:])
                self.tree.insert(gid, "end", iid=a.name, values=vals,
                                 tags=tags + ["child"] + stripe)

        if has_tree:
            keep = [iid for iid in sel_before if self.tree.exists(iid)]
            if keep:
                self.tree.selection_set(keep)
        if self.cards_inner is not None and self.cards_inner.winfo_exists() and self.view == "cards":
            cards = []
            for _, kind, payload in entries:
                if kind == "single":
                    vals, tags, a = payload
                    cards.append((a.title if a.title and len(a.title) < 32 else a.name, [a], vals, tags))
                else:
                    g, gvals, gtags, _ = payload
                    cards.append((g["title"], g["members"], gvals, gtags))
            self.render_cards(cards)
        active = sum(1 for a in self.addons if a.enabled)
        old = sum(1 for a in self.addons if a.enabled and self.is_outdated(a))
        upd = len(self.pending_updates())
        held_n = sum(1 for r in self.held() if self.held_update(r))
        parts = [tr("{0} aktiv / {1} gesamt", active, len(self.addons))]
        if old:
            parts.append(tr("{0} veraltet", old))
        if upd:
            parts.append(tr("{0} Update(s)", upd))
        self.count_var.set("  ·  ".join(parts))
        if self.nav_buttons:
            self.nav_buttons["addons"].configure(text=tr("Meine Addons   {0}", len(self.addons)))
            self.nav_buttons["updates"].configure(text=tr("Updates   {0} {1}", UPD_MARK, upd) if upd else tr("Updates"))
        self.upd_count_var.set((tr("{0} Update(s) verfügbar", upd) if upd else
                                (tr("Alles aktuell") if self.updates else tr("Noch nicht geprüft")))
                               + (tr("  ·  {0} festgehalten", held_n) if held_n else ""))
        self.update_detail()
        if self.current_page == "updates":
            self.refresh_updates_page()

    def sort_by(self, col):
        self.sort_rev = not self.sort_rev if self.sort_col == col else False
        self.sort_col = col
        self.render()

    def show_details(self, addon=None):
        """Zeigt ein Addon im Detailbereich der Seite „Meine Addons“ (kein eigenes Fenster)."""
        self.show_page("addons")
        if addon is not None:
            self.select_addons([addon])

    # ----- Abhängigkeiten ------------------------------------------------------------ #
    def install_dependencies(self, assets: list[dict]):
        """Installiert Addons nach, die laut CurseForge zwingend gebraucht werden und noch fehlen."""
        want = []
        for a in assets:
            for mid in (a or {}).get("deps") or []:
                if mid not in want and CF_PREFIX + str(mid) not in self.sources():
                    want.append(mid)
        key = self.cf_key()
        if not want or not key or not self.root_path or not self.flavor:
            return
        root, flavor = self.root_path, self.flavor
        gv = self.game[0] if self.game else ""
        have = {plain_key(a.name) for a in self.addons} | {plain_key(a.title) for a in self.addons}
        known = set(self.sources())

        def job():
            ok, manual, errors, seen = [], [], [], set()
            todo = list(want)
            while todo and len(seen) < 25:
                mid = todo.pop(0)
                if mid in seen or CF_PREFIX + str(mid) in known:
                    continue
                seen.add(mid)
                info, label = None, str(mid)
                try:
                    mod = cf_get_mod(mid, key)
                    label = mod.get("name") or label
                    if {plain_key(mod.get("name", "")), plain_key(mod.get("slug", ""))} & have - {""}:
                        continue                         # schon da (z. B. von Hand installiert)
                    info = cf_info(mod, cf_files(mid, key), flavor, gv)
                    if not info["best"]:
                        raise CFError(tr("keine passende Datei gefunden"))
                    got = cf_install_asset(info["best"], info, root, flavor, key)
                    ok.append((info, info["best"], got))
                    have.update(plain_key(g) for g in got)
                    todo += [d for d in info["best"].get("deps", []) if d not in seen]
                except ManualDownload:
                    manual.append((info, info["best"]))
                except Exception as e:  # noqa
                    log.error("Abhängigkeit %s: %s", label, e)
                    errors.append(f"{label}: {e}")
            return ok, manual, errors

        def done(out):
            ok, manual, errors = out
            for info, asset, got in ok:
                self.remember_cf_source(info, asset, got)
                self.remember_bundle(got)
            if ok:
                save_config(self.cfg)
                self.refresh()
                names = ", ".join(info["repo"] for info, _, _ in ok)
                log.info("Abhängigkeiten installiert: %s", names)
                self.set_status(tr("Benötigte Addons mitinstalliert: {0}", names))
                self.notify(tr("Benötigte Addons mitinstalliert: {0}", names), "ok", timeout=10000)
            if errors:
                self.warn(tr("Benötigte Addons nicht installiert:") + "\n" + "\n".join(errors))
            if manual:
                self.run_download_queue([cf_queue_item(i, a) for i, a in manual],
                                        tr("Benötigte Addons – im Browser laden:"))

        self.run_bg(tr("Installiere benötigte Addons"), job, done)

    def refresh_updates_page(self):
        if not getattr(self, "upd_tree", None):
            return
        tv = self.upd_tree
        sel = tv.selection()
        tv.delete(*tv.get_children())
        pend = self.pending_updates()
        for i, r in enumerate(sorted(pend, key=lambda r: self.source_name(r).lower())):
            src, info = self.sources()[r], self.update_info.get(r, {})
            hint = tr("Browser-Download") if r.startswith(CF_PREFIX) and info.get("allow_dl") is False else \
                ("CurseForge" if r.startswith(CF_PREFIX) else "GitHub")
            tv.insert("", "end", iid=r, tags=["update"] + (["odd"] if i % 2 else []),
                      values=(self.source_name(r), src.get("tag", "?"), f"{UPD_MARK} {self.updates[r]}", hint))
        keep = [i for i in sel if tv.exists(i)]
        if keep:
            tv.selection_set(keep)
        elif pend:
            tv.selection_set(tv.get_children()[0])
        if not pend:
            self._set_text(self.upd_notes, parts=[(tr("Alles aktuell.") + "\n", "head"),
                                                  (tr("Keine Updates bekannt.") if self.updates else
                                                   tr("Noch nicht geprüft – „Jetzt prüfen“."), "dim")])
        self._upd_show()
        self.refresh_browser_list()

    def _upd_selected(self) -> str:
        sel = self.upd_tree.selection() if getattr(self, "upd_tree", None) else ()
        return sel[0] if sel else ""

    def _upd_show(self):
        r = self._upd_selected()
        for b in (self.upd_one_btn, self.upd_page_btn, self.upd_dl_btn):
            b.state(["!disabled"] if r else ["disabled"])
        if not r:
            return
        info = self.update_info.get(r, {})
        src = self.sources().get(r, {})
        notes = (info.get("notes") or "").strip() or tr("Keine Änderungshinweise vorhanden.")
        parts = [(f"{self.source_name(r)}\n", "head"),
                 (f"{src.get('tag', '?')}  →  {self.updates.get(r, '?')}"
                  + (f"   ·   {info['published']}" if info.get("published") else "") + "\n\n", "dim")]
        if r.startswith(CF_PREFIX) and info.get("allow_dl") is False:
            parts.append((tr("Der Autor sperrt Downloads über Programme: Leeroyzan öffnet die Download-Seite und "
                          "installiert die Datei, sobald sie in deinem Download-Ordner liegt.\n\n"), "dim"))
        parts.append((notes, ""))
        self._set_text(self.upd_notes, parts=parts)
        self.upd_dl_btn.state(["!disabled"] if r.startswith(CF_PREFIX) and info.get("best") else ["disabled"])

    def update_selected_in_list(self):
        r = self._upd_selected()
        if r:
            self.update_all(only=[r])

    def _upd_open_page(self):
        r = self._upd_selected()
        if r and r in self.sources():
            self.open_link(self.source_page(r, self.sources()[r]))

    def _upd_open_download(self):
        r = self._upd_selected()
        if r:
            self.open_cf_update(sid=r)

    def update_selected(self, addons=None):
        sel = list(addons) if addons is not None else self.selected_addons()
        sids = []
        for a in sel:
            sid, src = self.folder_source(a.name)
            if sid and sid not in sids and sid in self.pending_updates():
                sids.append(sid)
        if sids:
            self.update_all(only=sids)

    def refresh_browser_list(self):
        """Karte „Im Browser laden“ auf der Updates-Seite: gesperrte Downloads, auf die Leeroyzan wartet."""
        if not getattr(self, "browser_box_outer", None):
            return
        if self.browser_box is not None:
            self.browser_box.master.destroy()
            self.browser_box = None
        waiting = [(sid, w) for sid, w in self.dl_watch.items() if w.get("manual")]
        if not waiting:
            if "browser" in self.banners:
                self.dismiss("browser")
            return
        box = self._card_box(self.browser_box_outer, pady=(12, 0),
                             title=tr("Im Browser laden · Leeroyzan wartet auf {0} Datei(en)", len(waiting)))
        self.browser_box = box
        ttk.Label(box, style="Panel.TLabel", wraplength=900, justify="left",
                  text=tr("Sobald die Datei in {0} liegt, wird sie automatisch installiert.", self.download_dir())).pack(
            anchor="w", pady=(0, 6))
        for sid, w in waiting:
            row = ttk.Frame(box, style="Panel.TFrame")
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=f"{w['info'].get('repo') or sid}  ·  {w['asset'].get('file_name', '')}",
                      style="Panel.TLabel").pack(side="left")
            ttk.Button(row, text=tr("Nicht mehr warten"),
                       command=lambda s=sid: (self.dl_watch.pop(s, None), self.refresh_browser_list())).pack(side="right")
            ttk.Button(row, text=tr("Download-Seite öffnen ↗"),
                       command=lambda w=w: self.open_link(cf_download_url(w["info"], w["asset"]))).pack(
                side="right", padx=(0, 6))

    # ----- Auswahl / Verwalten -------------------------------------------- #
    def select_all(self, force: bool = False):
        """Wählt alle sichtbaren Addons aus; ein zweiter Klick hebt die Auswahl auf."""
        if self.tree is None:
            return
        items = self.tree.get_children()
        if not force and items and set(self.tree.selection()) == set(items):
            self.tree.selection_remove(*items)
            self.set_status(tr("Auswahl aufgehoben."))
        else:
            self.tree.selection_set(items)
            self.set_status(tr("{0} Addons ausgewählt.", len(self.selected_addons())))
        self.tree.focus_set()

    def toggle_selected(self, addons=None):
        sel = list(addons) if addons is not None else self.selected_addons()
        if not sel or not self.ready():
            return
        # Einheitlich schalten: ist etwas aus, wird alles eingeschaltet – sonst alles aus.
        # So bleiben Gruppen wie Bagnon immer vollständig an oder aus.
        target = not all(a.enabled for a in sel)
        errors = []
        for a in sel:
            if a.enabled == target:
                continue
            try:
                set_enabled(a, self.root_path, self.flavor, target)
                log.info("%s: %s", "Aktiviert" if target else "Deaktiviert", a.name)
            except Exception as e:  # noqa
                errors.append(f"{a.name}: {e}")
        self.refresh()
        if errors:
            self.error("\n".join(errors))

    def remove_selected(self, addons=None):
        sel = list(addons) if addons is not None else self.selected_addons()
        if not sel or not self.ready():
            return
        names = ", ".join(a.name for a in sel)
        if not self.ask(tr("{0} wirklich entfernen? Die Ordner werden gelöscht.", names), yes=tr("Entfernen"), no=tr("Abbrechen")):
            return
        for a in sel:
            shutil.rmtree(a.folder, ignore_errors=True)
        log.info("Entfernt: %s", names)
        self.refresh()
        self.set_status(tr("Entfernt: {0}", names))

    def open_folder(self, addons=None):
        """Öffnet den Ordner des gewählten Addons; mit addons=[] den AddOns-Ordner selbst."""
        if not self.root_path or not self.flavor:
            return
        sel = list(addons) if addons is not None else self.selected_addons()
        if len(sel) > 1:
            sel = [self._group_main(sel)]
        path = sel[0].folder if sel else addons_dir(self.root_path, self.flavor)
        path.mkdir(parents=True, exist_ok=True)
        open_in_explorer(path)
