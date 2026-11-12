"""Seite „Entdecken“: CurseForge-Suche und direkte Installation. (Mixin, wird in App eingemischt)."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from tkinter import ttk

from ..i18n import cur_lang, tr
from ..curseforge import (
    CFError,
    CF_PAGE_SIZE,
    CF_PREFIX,
    CF_SORTS,
    cf_files,
    cf_get_mod,
    cf_info,
    cf_mod_families,
    cf_search,
    fmt_count,
    parse_cf_ref,
    wow_family,
)
from .base import HAS_DND
from .theme import THEME
from .dialogs import Placeholder


class DiscoverPage:
    """Teil des Hauptfensters (wird in App eingemischt)."""

    # --- Entdecken ------------------------------------------------------------ #
    def _page_discover(self):
        t = THEME
        page, _ = self._page("discover", tr("Entdecken"), tr("Addons finden und installieren"))
        quick = self._card_box(page, pady=(14, 0), title=tr("Direkt installieren"))
        grid = ttk.Frame(quick, style="Panel.TFrame")
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)
        rows = ((tr("CurseForge-Link"), self.cf_link_var, self.install_cf_link,
                 tr("z. B. {0} oder nur leatrix-plus", "https://www.curseforge.com/wow/addons/leatrix-plus")),
                ("GitHub", self.gh_var, self.install_github_dialog, tr("owner/repo oder Link zum Repository")))
        for r, (label, var, cmd, hint) in enumerate(rows):
            ttk.Label(grid, text=label, style="Panel.TLabel", width=16).grid(row=r, column=0, sticky="w", pady=4)
            e = ttk.Entry(grid, textvariable=var)
            e.grid(row=r, column=1, sticky="ew", pady=4)
            e.bind("<Return>", lambda ev, c=cmd: c())
            Placeholder(e, var, hint)
            ttk.Button(grid, text=tr("Installieren"), width=14, command=cmd).grid(row=r, column=2, sticky="e",
                                                                               padx=(8, 0), pady=4)
        ttk.Label(grid, text=tr("ZIP-Datei"), style="Panel.TLabel", width=16).grid(row=2, column=0, sticky="w", pady=4)
        self.zip_cb = ttk.Combobox(grid, textvariable=self.zip_var, state="readonly")
        self.zip_cb.grid(row=2, column=1, sticky="ew", pady=4)
        zrow = ttk.Frame(grid, style="Panel.TFrame")
        zrow.grid(row=2, column=2, sticky="e", padx=(8, 0), pady=4)
        ttk.Button(zrow, text=tr("Installieren"), width=14, command=self.install_zip_choice).pack(side="left")
        ttk.Button(zrow, text=tr("Andere …"), command=self.install_zip_dialog).pack(side="left", padx=(8, 0))
        ttk.Label(quick, style="Panel.TLabel", foreground=t["text_faint2"],
                  text=tr("Tipp: ZIP-, .lzpack- und .lzlist-Dateien kannst du auch einfach ins Fenster ziehen.")
                  if HAS_DND else tr("Die Liste zeigt die neuesten ZIP-Dateien aus deinem Download-Ordner.")).pack(
            anchor="w", pady=(6, 0))

        box = self._card_box(page, pady=(12, 0), fill="both", expand=True, title=tr("CurseForge durchsuchen · nur WoW"))
        top = ttk.Frame(box, style="Panel.TFrame")
        top.pack(fill="x")
        e = self.cf_entry = ttk.Entry(top, textvariable=self.cf_q)
        e.pack(side="left", fill="x", expand=True, ipady=2)
        Placeholder(e, self.cf_q, tr("Addon-Name, z. B. Details, Questie, Bagnon …"))
        e.bind("<Return>", lambda ev: self.cf_search())
        ttk.Button(top, text=tr("Suchen"), style="Primary.TButton", command=self.cf_search).pack(side="left", padx=(8, 0))
        cb = ttk.Combobox(top, textvariable=self.cf_sort, values=[tr(k) for k in CF_SORTS], state="readonly",
                          width=19)
        cb.pack(side="right")
        cb.bind("<<ComboboxSelected>>", lambda ev: self.cf_search())
        ttk.Label(top, text=tr("Sortierung"), style="Panel.TLabel").pack(side="right", padx=(16, 8))
        self.cf_fit_chk = ttk.Checkbutton(box, variable=self.cf_fit, command=self.cf_show_rows,
                                          text=tr("Nur Addons für meine Spielversion"))
        self.cf_fit_chk.pack(anchor="w", pady=(8, 8))
        self.cf_q.trace_add("write", lambda *a: self._cf_typed())

        body = ttk.Frame(box, style="Panel.TFrame")
        body.pack(fill="both", expand=True)
        det = ttk.Frame(body, style="Detail.TFrame", width=300, padding=(16, 14))
        det.pack(side="right", fill="y", padx=(12, 0))
        det.pack_propagate(False)
        dbtn = ttk.Frame(det, style="Detail.TFrame")
        dbtn.pack(side="bottom", fill="x")
        self.cf_det_title = ttk.Label(det, style="DetailTitle.TLabel", wraplength=265, font=self.fonts["card"])
        self.cf_det_title.pack(anchor="w")
        self.cf_det_meta = ttk.Label(det, style="DetailNote.TLabel", wraplength=265, foreground=THEME["gold"])
        self.cf_det_meta.pack(anchor="w", pady=(4, 0))
        self.cf_det_text = ttk.Label(det, style="DetailNote.TLabel", wraplength=265, justify="left")
        self.cf_det_text.pack(anchor="w", pady=(8, 0))
        ttk.Label(dbtn, text=tr("Datei"), style="DetailKey.TLabel").pack(anchor="w")
        self.cf_file_cb = ttk.Combobox(dbtn, textvariable=self.cf_file_var, state="readonly")
        self.cf_file_cb.pack(fill="x", pady=(2, 10))
        self.cf_install_btn = ttk.Button(dbtn, text=tr("Installieren"), style="Primary.TButton", command=self.cf_install)
        self.cf_install_btn.pack(fill="x", pady=(0, 6))
        self.cf_page_btn = ttk.Button(dbtn, text=tr("Auf CurseForge ansehen ↗"), command=self.cf_open_page)
        self.cf_page_btn.pack(fill="x")

        lst = ttk.Frame(body, style="Panel.TFrame")
        lst.pack(side="left", fill="both", expand=True)
        foot = ttk.Frame(lst, style="Panel.TFrame")
        foot.pack(side="bottom", fill="x", pady=(8, 0))
        ttk.Label(foot, textvariable=self.cf_count, style="Panel.TLabel").pack(side="left")
        self.cf_more_btn = ttk.Button(foot, text=tr("Mehr laden"), command=lambda: self.cf_search(more=True))
        self.cf_more_btn.pack(side="right")
        frame, self.cf_tree = self._list(lst, (("name", tr("Addon")), ("author", tr("Autor")), ("downloads", tr("Downloads")),
                                               ("updated", tr("Aktualisiert")), ("versions", tr("Versionen"))),
                                         (160, 95, 122, 112, 110), stretch=("name", "versions"),
                                         select="extended")
        frame.pack(fill="both", expand=True)
        self.cf_tree.column("downloads", anchor="e")
        self.cf_tree.bind("<<TreeviewSelect>>", lambda ev: self.cf_show_details())
        self.cf_tree.bind("<Double-1>", lambda ev: self.cf_open_page())
        self.cf_results: list[dict] = []
        self.cf_total = 0
        self.cf_req = 0
        self.cf_searching = False
        self.cf_direct = False
        self._cf_debounce = None
        self.cf_files_cache: dict[int, dict] = {}
        self.cf_show_details()

    # ----- Entdecken: Direkt installieren -------------------------------------- #
    def recent_files(self, exts: tuple[str, ...], limit: int = 20) -> list[Path]:
        """Neueste Dateien mit diesen Endungen im Download-Ordner."""
        try:
            files = [p for p in self.download_dir().iterdir() if p.is_file() and p.suffix.lower() in exts]
        except OSError:
            return []
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return files[:limit]

    def refresh_zip_choices(self):
        self._zip_files = self.recent_files((".zip",), 25)
        labels = [f"{p.name}   ·   {datetime.fromtimestamp(p.stat().st_mtime):%d.%m. %H:%M}" for p in self._zip_files]
        self.zip_cb["values"] = labels
        self.zip_var.set(labels[0] if labels else "")          # die neueste Datei vorauswählen

    def install_zip_choice(self):
        labels = list(self.zip_cb["values"])
        if self.zip_var.get() not in labels:
            self.info(tr("Im Download-Ordner ({0}) liegt keine ZIP-Datei. Wähle eine mit „Andere …“ oder zieh sie ins Fenster.", self.download_dir()))
            return
        self.install_zip_dialog([str(self._zip_files[labels.index(self.zip_var.get())])])

    def install_cf_link(self):
        """CurseForge-Link (oder Name/ID) aus dem Feld auf „Entdecken“ installieren."""
        text = self.cf_link_var.get().strip()
        if not text:
            self.info(tr("Füge zuerst den Link zu einem CurseForge-Addon ein."))
            return
        try:
            ref = parse_cf_ref(text)
        except ValueError as e:
            self.error(str(e))
            return
        if not self.ready():
            return
        key = self.need_cf_key()
        if not key:
            return
        flavor, gv = self.flavor, (self.game[0] if self.game else "")

        def job():
            mod = cf_get_mod(ref, key)
            files = cf_files(mod["id"], key)
            if not files:
                raise CFError(tr("{0}: keine ZIP-Dateien bei CurseForge gefunden.", mod.get('name', ref)))
            return cf_info(mod, files, flavor, gv)

        def done(info):
            self.cf_link_var.set("")
            self._pick_and_install_cf(info)
        self.run_bg(tr("Suche auf CurseForge"), job, done, kind="read")

    # ----- Entdecken: CurseForge-Suche ---------------------------------------- #
    @property
    def cf_family(self) -> str:
        return wow_family(self.game[0]) if self.game else ""

    def _cf_typed(self):
        if self._cf_debounce:
            self.after_cancel(self._cf_debounce)
            self._cf_debounce = None
        text = self.cf_q.get().strip()
        if len(text) >= 3 or not text:
            self._cf_debounce = self.after(700, self.cf_search)

    def cf_search(self, more: bool = False):
        if self._cf_debounce:
            self.after_cancel(self._cf_debounce)
            self._cf_debounce = None
        key = self.cf_key()
        if not key:
            self.cf_count.set(tr("Für die Suche brauchst du deinen CurseForge-API-Key (Einstellungen)."))
            self.need_cf_key()
            return
        self.cf_req += 1
        req, text = self.cf_req, self.cf_q.get().strip()
        sort = next((k for k in CF_SORTS if tr(k) == self.cf_sort.get()), "Beliebtheit")
        index = len(self.cf_results) if more else 0
        self.cf_count.set(tr("Suche bei CurseForge …"))
        self.cf_more_btn.state(["disabled"])
        self.cf_searching = True

        def done(res, err):
            if req != self.cf_req:
                return                                   # veraltete Antwort
            self.cf_searching = False
            if err:
                self.cf_count.set(tr("Fehler: {0}", err))
                return
            mods, total = res
            self.cf_direct = "curseforge.com" in text.lower() or text.isdigit()
            known = {m["id"] for m in self.cf_results} if more else set()
            self.cf_results = (self.cf_results if more else []) + [m for m in mods if m.get("id") not in known]
            self.cf_total = total
            self.cf_show_rows()

        self.run_quiet(lambda: cf_search(text, key, sort, index), done)

    def _cf_installed(self) -> set[int]:
        return {int(sid[len(CF_PREFIX):]) for sid in self.sources()
                if sid.startswith(CF_PREFIX) and sid[len(CF_PREFIX):].isdigit()}

    def cf_show_rows(self):
        fam = self.cf_family
        self.cf_fit_chk.configure(text=tr("Nur Addons für meine Spielversion ({0})", fam) if fam
                                  else tr("Nur Addons für meine Spielversion (unbekannt)"))
        self.cf_fit_chk.state(["!disabled"] if fam else ["disabled"])
        tv = self.cf_tree
        sel = set(tv.selection())
        tv.delete(*tv.get_children())
        installed = self._cf_installed()
        shown = 0
        for m in self.cf_results:
            fams = cf_mod_families(m)
            fits = not fam or not fams or fam in fams
            if self.cf_fit.get() and fam and not fits and not self.cf_direct:
                continue
            inst = m.get("id") in installed
            authors = ", ".join(a.get("name", "") for a in m.get("authors") or [] if a.get("name"))
            tags = ["dim"] if inst else ([] if fits else ["other"])
            if shown % 2:
                tags.append("odd")
            tv.insert("", "end", iid=str(m["id"]), tags=tags,
                      values=(("✓ " if inst else "") + m.get("name", ""), authors, fmt_count(m.get("downloadCount")),
                              str(m.get("dateModified", ""))[:10], ", ".join(fams) or "–"))
            shown += 1
        keep = [i for i in sel if tv.exists(i)]
        if keep:
            tv.selection_set(keep)
        hidden = len(self.cf_results) - shown
        text = tr("{0} angezeigt", shown)
        if hidden:
            text += tr(" · {0} für andere Spielversionen ausgeblendet", hidden)
        text += tr(" · {0} Treffer", f"{self.cf_total:,}".replace(",", "." if cur_lang() == "de" else ","))
        if not self.cf_results:
            text = tr("Nichts gefunden.") if self.cf_req else ""
        self.cf_count.set(text)
        more = len(self.cf_results) < self.cf_total and len(self.cf_results) + CF_PAGE_SIZE <= 10_000
        self.cf_more_btn.state(["!disabled"] if more else ["disabled"])
        self.cf_show_details()

    def _cf_selected(self) -> list[dict]:
        ids = set(self.cf_tree.selection())
        return [m for m in self.cf_results if str(m.get("id")) in ids]

    def cf_show_details(self):
        sel = self._cf_selected()
        self.cf_file_cb["values"] = []
        self.cf_file_var.set("")
        if not sel:
            self.cf_det_title.configure(text=tr("Kein Addon gewählt"))
            self.cf_det_meta.configure(text="")
            self.cf_det_text.configure(text=tr("Suche oben nach einem Addon und wähle es in der Liste aus. "
                                            "Mit Strg oder Umschalt markierst du mehrere."))
            self.cf_install_btn.configure(text=tr("Installieren"))
            self.cf_install_btn.state(["disabled"])
            self.cf_page_btn.state(["disabled"])
            self.cf_file_cb.state(["disabled"])
            return
        installed = self._cf_installed()
        todo = [m for m in sel if m.get("id") not in installed]
        self.cf_page_btn.state(["!disabled"])
        if len(sel) > 1:
            self.cf_det_title.configure(text=tr("{0} Addons ausgewählt", len(sel)))
            self.cf_det_meta.configure(text="")
            self.cf_det_text.configure(text=tr("Für jedes Addon wird automatisch die passende Datei gewählt.")
                                       + (tr("\n{0} davon sind schon installiert.", len(sel) - len(todo))
                                          if len(todo) < len(sel) else ""))
            self.cf_install_btn.configure(text=tr("{0} installieren", len(todo)))
            self.cf_install_btn.state(["!disabled"] if todo else ["disabled"])
            self.cf_file_cb.state(["disabled"])
            return
        m = sel[0]
        authors = ", ".join(a.get("name", "") for a in m.get("authors") or [] if a.get("name"))
        self.cf_det_title.configure(text=m.get("name", ""))
        self.cf_det_meta.configure(text=(tr("von {0}  ·  ", authors) if authors else "")
                                   + tr("{0} Downloads  ·  ", fmt_count(m.get('downloadCount')))
                                   + str(m.get("dateModified", ""))[:10])
        summary = m.get("summary", "").strip()
        lines = [summary[:240] + ("…" if len(summary) > 240 else "")]
        if m.get("allowModDistribution") is False:
            lines.append(tr("Der Autor erlaubt keine Programm-Downloads – Leeroyzan öffnet dafür den Browser "
                         "und installiert die Datei danach automatisch."))
        self.cf_det_text.configure(text="\n\n".join(x for x in lines if x))
        inst = m.get("id") in installed
        self.cf_install_btn.configure(text=tr("✓ installiert") if inst else tr("Installieren"))
        self.cf_install_btn.state(["disabled"] if inst else ["!disabled"])
        self.cf_file_cb.state(["disabled"])
        if inst:
            return
        cached = self.cf_files_cache.get(m["id"])
        if cached:
            self._cf_fill_files(m["id"])
            return
        self.cf_file_var.set(tr("Dateien werden geladen …"))
        key, flavor, gv = self.cf_key(), self.flavor, (self.game[0] if self.game else "")

        def load():
            files = cf_files(m["id"], key)
            if not files:
                raise CFError(tr("keine ZIP-Dateien bei CurseForge gefunden"))
            return cf_info(m, files, flavor, gv)

        def done(info, err):
            if err:
                if self._cf_selected() == [m]:
                    self.cf_file_var.set(tr("Fehler: {0}", err))
                return
            self.cf_files_cache[m["id"]] = info
            if self._cf_selected() == [m]:
                self._cf_fill_files(m["id"])
        self.run_quiet(load, done)

    def _cf_fill_files(self, mod_id: int):
        info = self.cf_files_cache[mod_id]
        labels = [a.get("label", a["name"]) for a in info["assets"]]
        self.cf_file_cb["values"] = labels
        best = info["best"] or (info["assets"][0] if info["assets"] else None)
        if best:
            self.cf_file_var.set(labels[info["assets"].index(best)])
        self.cf_file_cb.state(["readonly", "!disabled"])

    def cf_open_page(self):
        sel = self._cf_selected()
        if sel:
            m = sel[0]
            self.open_link((m.get("links") or {}).get("websiteUrl")
                           or f"https://www.curseforge.com/wow/addons/{m.get('slug', '')}")

    def cf_install(self):
        installed = self._cf_installed()
        sel = [m for m in self._cf_selected() if m.get("id") not in installed]
        if not sel or not self.ready():
            return
        if len(sel) > 1:
            self._run_list_import([{"t": "c", "i": m["id"], "n": m.get("name", ""), "s": m.get("slug", "")}
                                   for m in sel], {}, what=tr("von CurseForge"))
            return
        info = self.cf_files_cache.get(sel[0]["id"])
        if not info:
            self.info(tr("Die Dateiliste wird noch geladen – einen Moment."))
            return
        labels = list(self.cf_file_cb["values"])
        asset = (info["assets"][labels.index(self.cf_file_var.get())] if self.cf_file_var.get() in labels
                 else info["best"])
        self._install_cf_asset(info, asset)
