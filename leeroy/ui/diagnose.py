"""Hauptfenster: DiagnosePage."""
from __future__ import annotations

import urllib.parse
from datetime import datetime

from tkinter import ttk

from ..i18n import cur_lang, tr
from ..config import save_config
from ..wow import clean_wow_text
from ..reports import QUALITY_COLORS, fmt_int, fmt_pct, plural
from ..linkdata import LOAD_REASONS, _norm_name, error_rows, load_problems
from .theme import THEME


class DiagnosePage:
    """Teil des Hauptfensters (wird in App eingemischt)."""

    # --- Diagnose -------------------------------------------------------------------------- #
    DIAG_VIEWS = [("errors", "Fehler"), ("load", "Ladeprobleme"), ("memory", "Speicher"), ("wish", "Wunschliste")]

    def _page_diagnose(self):
        page, head = self._page("diagnose", tr("Leeroylink"), tr("Diagnose aus dem Spiel"))
        right = ttk.Frame(head)
        right.pack(side="right", anchor="s")
        ttk.Button(right, text=tr("Jetzt einlesen"), command=lambda: self.import_sessions(manual=True)).pack(side="right")
        ttk.Label(right, textvariable=self.diag_state_var).pack(side="right", padx=(0, 14))
        seg = ttk.Frame(page)
        seg.pack(fill="x", pady=(12, 0))
        self.diag_btns = {}
        for key, label in self.DIAG_VIEWS:
            b = ttk.Button(seg, text=tr(label), width=16, command=lambda k=key: self._diag_select_view(k))
            b.pack(side="left", padx=(0, 4))
            self.diag_btns[key] = b
        body = ttk.Frame(page)
        body.pack(fill="both", expand=True, pady=(12, 0))
        left = ttk.Frame(body, width=520)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        self.diag_list_host = left
        self.diag_lists = {}
        specs = {
            "errors": ((("addon", tr("Addon")), ("cur", tr("Diese Version")), ("total", tr("Gesamt")), ("last", tr("Zuletzt"))),
                       (190, 104, 80, 130), ("addon",)),
            "load": ((("addon", tr("Addon")), ("reason", tr("Grund"))), (200, 300), ("reason",)),
            "memory": ((("addon", tr("Addon")), ("mb", tr("Speicher")), ("share", tr("Anteil"))), (250, 120, 120), ("addon",)),
            "wish": ((("name", tr("Vorgemerkt")), ("when", tr("Seit")), ("state", tr("Status"))), (220, 120, 160), ("name",)),
        }
        for key, (cols, widths, stretch) in specs.items():
            frame, tv = self._list(left, cols, widths, stretch=stretch)
            tv.bind("<<TreeviewSelect>>", lambda e: self._diag_show())
            self.diag_lists[key] = (frame, tv)
        box = self._card_box(body, pady=(0, 0), side="left", fill="both", expand=True, padx=(14, 0))
        btns = ttk.Frame(box, style="Panel.TFrame")
        btns.pack(fill="x", side="bottom", pady=(10, 0))
        self.diag_btn_frame = btns
        tf, self.diag_text = self._text(box)
        tf.pack(fill="both", expand=True)
        self._tag_text(self.diag_text)
        self.diag_view = self.cfg.get("diag_view", "errors")

    def _tag_text(self, txt):
        t = THEME
        txt.configure(spacing1=1, spacing3=1, padx=14, pady=10)
        txt.tag_configure("title", foreground=t["gold"], font=self.fonts["card"], spacing3=2)
        txt.tag_configure("hl", foreground=t["gold"])
        txt.tag_configure("ok", foreground=t["ok"])
        txt.tag_configure("warn", foreground=t["warn"])
        txt.tag_configure("mono", font=("Consolas", 9), foreground=t["text_dim"])
        for i, color in enumerate(QUALITY_COLORS):
            txt.tag_configure(f"q{i}", foreground=color)

    def _diag_select_view(self, key: str):
        self.diag_view = key
        self.cfg["diag_view"] = key
        self.refresh_diagnose()

    def _diag_buttons(self, items):
        for w in self.diag_btn_frame.winfo_children():
            w.destroy()
        for i, (label, cmd, style) in enumerate(items):
            ttk.Button(self.diag_btn_frame, text=label, command=cmd, style=style).pack(side="left", padx=(0 if i == 0 else 6, 0))

    def refresh_diagnose(self):
        if getattr(self, "diag_text", None) is None or not self.diag_text.winfo_exists():
            return
        st = self.link_state
        when = datetime.fromtimestamp(st["writtenAt"]).strftime(tr("%d.%m.%Y  %H:%M")) if st["writtenAt"] else ""
        self.diag_state_var.set(tr("Daten aus dem Spiel vom {0}", when) if when else tr("Noch keine Daten aus dem Spiel"))
        for key, b in self.diag_btns.items():
            b.configure(style="Primary.TButton" if key == self.diag_view else "TButton")
        for key, (frame, tv) in self.diag_lists.items():
            frame.pack_forget()
        frame, tv = self.diag_lists[self.diag_view]
        frame.pack(fill="both", expand=True)
        tv.delete(*tv.get_children())
        self._diag_rows = []
        versions = {a.name: a.version for a in self.addons}
        hidden = self.cfg.setdefault("diag_hidden", {}).setdefault(self.flavor, {})
        if self.diag_view == "errors":
            rows = [r for r in error_rows(st, versions) if hidden.get(r["addon"], -1) < r["total"]]
            for i, r in enumerate(rows):
                tags = ["odd"] if i % 2 else []
                if r["current"]:
                    tags.append("update")
                elif not r["installed"]:
                    tags.append("dim")
                tv.insert("", "end", iid=f"r{i}", tags=tuple(tags), values=(
                    r["addon"], fmt_int(r["current"]) if r["installed"] else "–", fmt_int(r["total"]),
                    datetime.fromtimestamp(r["last"]).strftime(tr("%d.%m.%Y  %H:%M")) if r["last"] else ""))
            self._diag_rows = rows
        elif self.diag_view == "load":
            rows = load_problems(st)
            for i, r in enumerate(rows):
                reason = tr(LOAD_REASONS.get(r["reason"], r["reason"] or "unbekannt"))
                if r["missing"]:
                    reason += ": " + ", ".join(r["missing"])
                tv.insert("", "end", iid=f"r{i}", tags=("odd",) if i % 2 else (), values=(r["name"], reason))
            self._diag_rows = rows
        elif self.diag_view == "memory":
            mem = st["memory"]
            total = mem["total"] or sum(m["kb"] for m in mem["list"]) or 1
            rows = sorted(mem["list"], key=lambda m: m["kb"], reverse=True)
            for i, m in enumerate(rows):
                tv.insert("", "end", iid=f"r{i}", tags=("odd",) if i % 2 else (), values=(
                    m["name"], f"{m['kb'] / 1024:.1f} MB".replace(".", "," if cur_lang() == "de" else "."), fmt_pct(m["kb"], total)))
            self._diag_rows = rows
        else:
            names = {_norm_name(a.name) for a in self.addons} | {_norm_name(a.title) for a in self.addons}
            rows = []
            for w in st["wishlist"]:
                done = _norm_name(w["name"]) in names
                rows.append(dict(w, done=done))
            for i, w in enumerate(rows):
                tv.insert("", "end", iid=f"r{i}", tags=(("odd",) if i % 2 else ()) + (("dim",) if w["done"] else ()), values=(
                    w["name"], datetime.fromtimestamp(w["added"]).strftime(tr("%d.%m.%Y")) if w["added"] else "",
                    tr("installiert") if w["done"] else tr("offen")))
            self._diag_rows = rows
        self._diag_show()

    def _diag_selected(self):
        tv = self.diag_lists[self.diag_view][1]
        sel = tv.selection()
        if not sel:
            return None
        try:
            return self._diag_rows[int(sel[0][1:])]
        except (ValueError, IndexError):
            return None

    def _diag_show(self):
        st = self.link_state
        row = self._diag_selected()
        P: list[tuple[str, str]] = []
        add = lambda text, tag="": P.append((text, tag))   # noqa: E731
        btns = []
        view = self.diag_view
        if not st["accounts"]:
            add(tr("Noch keine Daten aus dem Spiel") + "\n", "title")
            add(tr("Leeroylink schreibt Fehler, Ladestatus, Speicherverbrauch, Charaktere und deine Wunschliste "
                   "beim Ausloggen und bei /reload. Installiere das Addon auf der Seite „Leeroylink“ und logge dich "
                   "einmal ein und wieder aus.") + "\n", "")
        elif view == "errors":
            if row is None:
                add(tr("Lua-Fehler aus dem Spiel") + "\n", "title")
                n_cur = sum(1 for r in self._diag_rows if r["current"])
                add(tr("Leeroylink ordnet jeden Lua-Fehler dem verursachenden Addon zu und zählt ihn pro Version. "
                       "Gelb markiert: Fehler in der Version, die jetzt installiert ist.") + "\n\n", "dim")
                if not self._diag_rows:
                    add(tr("Keine Fehler – alles sauber.") + "\n", "ok")
                elif n_cur:
                    add(plural(n_cur, "{0} Addon hat Fehler in der aktuellen Version.",
                               "{0} Addons haben Fehler in der aktuellen Version.") + "\n", "warn")
                blocked = sorted(st["blocked"], key=lambda b: b["count"], reverse=True)[:8]
                if blocked:
                    add("\n" + tr("Blockierte Aktionen") + "\n", "head")
                    add(tr("„Aktion wurde durch ein Addon blockiert“ – diese Addons haben geschützte Funktionen aufgerufen:") + "\n", "dim")
                    for b in blocked:
                        add(f"{b['addon']}: {b['func']} ×{b['count']}" + (tr(" (verboten)") if b["forbidden"] else "") + "\n")
            else:
                add(row["addon"] + "\n", "title")
                if row["installed"]:
                    add(tr("Installiert: {0}", row["version"] or "?") + "\n", "dim")
                else:
                    add(tr("Nicht (mehr) installiert.") + "\n", "dim")
                if row["addon"] in ("?", "Blizzard"):
                    add(tr("Fehler, die keinem Addon zugeordnet werden konnten (oder in Blizzards eigener Oberfläche).") + "\n", "dim")
                add("\n" + tr("Nach Version") + "\n", "head")
                for v, n in sorted(row["byVersion"].items(), key=lambda kv: kv[1], reverse=True):
                    cur = row["installed"] and clean_wow_text(v) == row["version"]
                    add(f"{v}: {fmt_int(n)}" + (tr("  ← installiert") if cur else "") + "\n", "warn" if cur else "")
                add("\n" + tr("Meldungen") + "\n", "head")
                for e in row["errors"][:8]:
                    add(f"×{e['count']}  ", "hl")
                    add(e["msg"] + "\n")
                    add("    " + tr("zuerst {0}, zuletzt {1}", datetime.fromtimestamp(e["first"]).strftime(tr("%d.%m.%Y  %H:%M")),
                                    datetime.fromtimestamp(e["last"]).strftime(tr("%d.%m.%Y  %H:%M"))) + "\n", "dim")
                if row["errors"] and row["errors"][0]["stack"]:
                    add("\n" + tr("Aufrufkette (häufigster Fehler)") + "\n", "head")
                    add(row["errors"][0]["stack"][:600] + "\n", "mono")
                sid = self._source_of(row["addon"])
                rb = self.rollback_info(sid) if sid else None
                addon = self._addon_by_name(row["addon"])
                if rb:
                    btns.append((tr("↶ Zurück auf {0}", rb["source"].get("tag") or "?"), lambda sid=sid: self.rollback(sid), "Primary.TButton"))
                if sid:
                    btns.append((tr("Freigeben") if self.is_held(sid) else tr("Festhalten"), lambda sid=sid: (self.toggle_hold(sid=sid), self.refresh_diagnose()), "TButton"))
                if addon is not None:
                    btns.append((tr("Deaktivieren") if addon.enabled else tr("Aktivieren"), lambda a=addon: (self.toggle_selected([a]), self.refresh_diagnose()), "TButton"))
                btns.append((tr("Ausblenden"), lambda r=row: self._diag_hide(r), "TButton"))
        elif view == "load":
            if row is None:
                add(tr("Ladeprobleme") + "\n", "title")
                add(tr("Addons, die aktiviert sind, die WoW aber nicht geladen hat – mit dem Grund aus dem Spiel.") + "\n\n", "dim")
                if not self._diag_rows:
                    add(tr("Alle aktivierten Addons wurden geladen.") + "\n", "ok")
            else:
                add(row["name"] + "\n", "title")
                add(tr(LOAD_REASONS.get(row["reason"], row["reason"] or "unbekannt")) + "\n\n", "warn")
                if row["reason"] == "INTERFACE_VERSION":
                    add(tr("Das Addon ist für eine andere Spielversion gebaut. Suche ein Update oder setze im Addon-Menü "
                           "des Spiels den Haken bei „Veraltete AddOns laden“.") + "\n", "")
                for dep in row["missing"]:
                    other = self._addon_by_name(dep)
                    if other is not None and not other.enabled:
                        add(tr("{0} ist installiert, aber deaktiviert.", dep) + "\n")
                        btns.append((tr("{0} aktivieren", dep), lambda a=other: (self.toggle_selected([a]), self.refresh_diagnose()), "Primary.TButton"))
                    elif other is None:
                        add(tr("{0} fehlt.", dep) + "\n")
                        btns.append((tr("{0} suchen", dep), lambda d=dep: self.search_addon(d), "Primary.TButton"))
                addon = self._addon_by_name(row["name"])
                if addon is not None:
                    btns.append((tr("Ordner öffnen"), lambda a=addon: self.open_folder([a]), "TButton"))
        elif view == "memory":
            mem = st["memory"]
            add(tr("Speicherverbrauch") + "\n", "title")
            if mem["t"]:
                add(tr("Gemessen am {0} · gesamt {1}", datetime.fromtimestamp(mem["t"]).strftime(tr("%d.%m.%Y  %H:%M")),
                       f"{mem['total'] / 1024:.0f} MB") + "\n\n", "dim")
            add(tr("Viel Speicher ist kein Fehler – große Datenbanken (Questie, AtlasLoot) brauchen ihn. Spürbar wird es "
                   "erst bei Rucklern beim Laden oder auf schwachen Rechnern.") + "\n", "dim")
            if row is not None:
                add("\n" + row["name"] + "\n", "head")
                add(f"{row['kb'] / 1024:.1f} MB · {fmt_pct(row['kb'], mem['total'] or 1)}\n")
                if row["cpu"]:
                    add(tr("Rechenzeit zuletzt: {0} ms pro Bild", f"{row['cpu']:.2f}") + "\n", "dim")
                addon = self._addon_by_name(row["name"])
                if addon is not None:
                    btns.append((tr("Deaktivieren") if addon.enabled else tr("Aktivieren"), lambda a=addon: (self.toggle_selected([a]), self.refresh_diagnose()), "TButton"))
        else:
            add(tr("Wunschliste aus dem Spiel") + "\n", "title")
            add(tr("Im Spiel mit /lz merken <Name> vorgemerkt. Installierte Einträge meldet Leeroyzan ans Addon zurück, "
                   "das sie dann von der Liste nimmt.") + "\n\n", "dim")
            if not self._diag_rows:
                add(tr("Nichts vorgemerkt.") + "\n", "dim")
            if row is not None:
                add(row["name"] + "\n", "head")
                if row["note"]:
                    add(row["note"] + "\n")
                add((tr("Ist installiert.") if row["done"] else tr("Noch nicht installiert.")) + "\n", "ok" if row["done"] else "")
                if not row["done"]:
                    btns.append((tr("Auf CurseForge suchen"), lambda n=row["name"]: self.search_addon(n), "Primary.TButton"))
        self._set_text(self.diag_text, parts=P)
        self._diag_buttons(btns)

    def _diag_hide(self, row):
        self.cfg.setdefault("diag_hidden", {}).setdefault(self.flavor, {})[row["addon"]] = row["total"]
        save_config(self.cfg)
        self.refresh_diagnose()
        self.set_status(tr("{0} ausgeblendet – erscheint wieder, sobald neue Fehler dazukommen.", row["addon"]))

    def search_addon(self, name: str):
        """Addon-Namen auf der Seite „Entdecken“ suchen (CurseForge) – ohne API-Key im Browser."""
        if self.cf_key():
            self.cf_q.set(name)
            self.show_page("discover")
            self.cf_search()
        else:
            self.open_link("https://www.curseforge.com/wow/search?search=" + urllib.parse.quote(name))
