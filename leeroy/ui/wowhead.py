"""Seite „Wowhead“: Suche mit Vorschau und Favoriten. (Mixin, wird in App eingemischt)."""
from __future__ import annotations


from tkinter import ttk
import tkinter as tk

from ..i18n import tr
from ..config import log, save_config
from ..reports import QUALITY_COLORS, plural
from ..wowhead import (
    WOWHEAD_EDITIONS,
    WOWHEAD_TOOLTIP_TYPES,
    wowhead_base,
    wowhead_search,
    wowhead_search_url,
    wowhead_tooltip,
)
from .dialogs import Placeholder


class WowheadPage:
    """Teil des Hauptfensters (wird in App eingemischt)."""

    # --- Wowhead ---------------------------------------------------------------------- #
    def _page_wowhead(self):
        page, head = self._page("wowhead", tr("Nachschlagen"), "Wowhead")
        self.wh_results: list[dict] = []
        self.wh_seq = 0
        self.wh_after = None
        self.wh_tips: dict[tuple, dict] = {}
        self.wh_var = tk.StringVar()
        self.wh_info_var = tk.StringVar(value=tr("Tippe einen Namen ein – Gegenstand, NPC, Quest, Zauber, Zone …"))
        ed = self.cfg.get("wowhead_edition", "classic")
        if ed not in [e for e, _ in WOWHEAD_EDITIONS]:
            ed = "classic"
        self.wh_ed_var = tk.StringVar(value=tr(dict(WOWHEAD_EDITIONS)[ed]))

        row = ttk.Frame(page)
        row.pack(fill="x", pady=(12, 0))
        self.wh_entry = ttk.Entry(row, textvariable=self.wh_var)
        self.wh_entry.pack(side="left", fill="x", expand=True)
        Placeholder(self.wh_entry, self.wh_var, tr("Suchen, z. B. Kupfererz oder Hogger"))
        self.wh_entry.bind("<Return>", lambda e: self.wh_search(now=True))
        self.wh_var.trace_add("write", lambda *a: self.wh_search())
        cb = ttk.Combobox(row, textvariable=self.wh_ed_var, state="readonly", width=30,
                          values=[tr(label) for _, label in WOWHEAD_EDITIONS])
        cb.pack(side="left", padx=(8, 0))
        cb.bind("<<ComboboxSelected>>", lambda e: self._wh_edition())
        ttk.Button(row, text=tr("Suchen"), style="Primary.TButton", command=lambda: self.wh_search(now=True)).pack(
            side="left", padx=(8, 0))
        ttk.Button(row, text=tr("Auf Wowhead suchen ↗"), command=self.wh_open_search).pack(side="left", padx=(6, 0))
        ttk.Label(page, textvariable=self.wh_info_var, style="Body.TLabel").pack(anchor="w", pady=(8, 0))

        body = ttk.Frame(page)
        body.pack(fill="both", expand=True, pady=(8, 0))
        left = ttk.Frame(body, width=470)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        self.wh_mode = "results"
        self.wh_rows: list[dict] = []
        modes = ttk.Frame(left)
        modes.pack(fill="x", pady=(0, 6))
        self.wh_mode_btns = {}
        for key in ("results", "favs"):
            b = ttk.Button(modes, command=lambda k=key: self.wh_set_mode(k))
            b.pack(side="left", padx=(0 if key == "results" else 6, 0))
            self.wh_mode_btns[key] = b
        frame, self.wh_tree = self._list(left, (("name", tr("Name")), ("kind", tr("Art")), ("id", "ID")),
                                         (300, 100, 60), stretch=("name",))
        frame.pack(fill="both", expand=True)
        for i, color in enumerate(QUALITY_COLORS):
            self.wh_tree.tag_configure(f"q{i}", foreground=color)
        self.wh_tree.bind("<<TreeviewSelect>>", lambda e: self._wh_show())
        self.wh_tree.bind("<Double-1>", lambda e: self.wh_open())
        self.wh_tree.bind("<Return>", lambda e: self.wh_open())
        self.wh_tree.bind("<Delete>", lambda e: self.wh_mode == "favs" and self.wh_toggle_fav())
        box = self._card_box(body, pady=(0, 0), side="left", fill="both", expand=True, padx=(14, 0), title=tr("Vorschau"))
        tf, self.wh_text = self._text(box, height=8)
        tf.pack(fill="both", expand=True)
        self._tag_text(self.wh_text)
        btns = ttk.Frame(box, style="Panel.TFrame")
        btns.pack(fill="x", pady=(10, 0))
        self.wh_open_btn = ttk.Button(btns, text=tr("Auf Wowhead öffnen ↗"), style="Primary.TButton",
                                      command=self.wh_open, state="disabled")
        self.wh_open_btn.pack(side="left")
        self.wh_copy_btn = ttk.Button(btns, text=tr("Link kopieren"), command=self.wh_copy, state="disabled")
        self.wh_copy_btn.pack(side="left", padx=(6, 0))
        self.wh_fav_btn = ttk.Button(btns, text=tr("☆ Favorit"), command=self.wh_toggle_fav, state="disabled")
        self.wh_fav_btn.pack(side="right")
        self._wh_mode_buttons()
        self._set_text(self.wh_text, parts=[(tr("Wähle links einen Treffer, um die Kurzinfo zu sehen. "
                                                 "Doppelklick öffnet die Seite auf Wowhead."), "dim")])
        self.wh_text.configure(state="disabled")

    def _wh_edition_key(self) -> str:
        label = self.wh_ed_var.get()
        return next((e for e, l in WOWHEAD_EDITIONS if tr(l) == label), "classic")

    def _wh_edition(self):
        self.cfg["wowhead_edition"] = self._wh_edition_key()
        save_config(self.cfg)
        self.wh_tips.clear()
        if self.wh_var.get().strip():
            self.wh_search(now=True)

    def _wh_query(self) -> str:
        q = self.wh_var.get().strip()
        return "" if q == tr("Suchen, z. B. Kupfererz oder Hogger") else q

    def wh_search(self, now: bool = False):
        """Sucht beim Tippen (kurz verzögert) oder sofort mit Enter/Suchen."""
        if self.wh_after:
            self.after_cancel(self.wh_after)
            self.wh_after = None
        q = self._wh_query()
        if len(q) < (1 if now else 3):
            return
        if not now:
            self.wh_after = self.after(450, lambda: self.wh_search(now=True))
            return
        self.wh_seq += 1
        seq, edition = self.wh_seq, self._wh_edition_key()
        if self.wh_mode != "results":
            self.wh_mode = "results"
            self._wh_mode_buttons()
        self.wh_info_var.set(tr("Suche „{0}“ …", q))
        self.run_quiet(lambda: wowhead_search(q, edition), lambda res, err: self._wh_results(seq, q, res, err))

    def _wh_results(self, seq, q, res, err):
        if seq != self.wh_seq:
            return                              # eine neuere Suche läuft schon
        self.wh_results = res or []
        self.wh_mode = "results"
        self._wh_fill(self.wh_results)
        if err:
            log.warning("Wowhead-Suche: %s", err)
            self.wh_info_var.set(tr("Wowhead ist gerade nicht erreichbar ({0}). „Auf Wowhead suchen ↗“ öffnet die Suche im Browser.",
                                    str(err)[:120]))
            return
        if not self.wh_results:
            self.wh_info_var.set(tr("Nichts gefunden für „{0}“.", q))
            return
        self.wh_info_var.set(plural(len(self.wh_results), "{0} Treffer", "{0} Treffer") + "  ·  " +
                             tr("Doppelklick öffnet die Seite auf Wowhead."))
        self.wh_tree.selection_set("0")

    def _wh_selected(self):
        sel = self.wh_tree.selection()
        if not sel:
            return None
        try:
            return self.wh_rows[int(sel[0])]
        except (ValueError, IndexError):
            return None

    def _wh_show(self):
        r = self._wh_selected()
        state = "normal" if r else "disabled"
        self.wh_open_btn.configure(state=state)
        self.wh_copy_btn.configure(state=state)
        self.wh_fav_btn.configure(state=state, text=tr("★ Favorit entfernen") if r and self._wh_is_fav(r) else tr("☆ Favorit"))
        if not r:
            return
        key = (r.get("edition", self._wh_edition_key()), r["kind"], r["id"])
        self._wh_render(r, self.wh_tips.get(key), loading=key not in self.wh_tips)
        if key in self.wh_tips or r["kind"] not in WOWHEAD_TOOLTIP_TYPES:
            return

        def done(tip, err):
            if err:
                log.info("Wowhead-Tooltip %s: %s", key, err)
            self.wh_tips[key] = tip or {}
            if self._wh_selected() is r:
                self._wh_render(r, self.wh_tips[key])
        self.run_quiet(lambda: wowhead_tooltip(r["kind"], r["id"], key[0]), done)

    def _wh_render(self, r, tip, loading=False):
        q = (tip or {}).get("quality")
        q = r["quality"] if q is None else q
        parts = [((tip or {}).get("name") or r["name"], f"q{q}" if q is not None else "title"), ("\n", ""),
                 (f"{tr(r['kind_name'])} · ID {r['id']}\n\n", "dim")]
        if tip and tip.get("text"):
            lines = tip["text"].splitlines()
            if lines and lines[0].strip() == ((tip or {}).get("name") or r["name"]).strip():
                lines = lines[1:]               # der Name steht schon oben
            parts.append(("\n".join(lines).strip() + "\n", ""))
        elif loading and r["kind"] in WOWHEAD_TOOLTIP_TYPES:
            parts.append((tr("Lade Kurzinfo …") + "\n", "dim"))
        else:
            parts.append((tr("Keine Kurzinfo verfügbar – die ganze Seite gibt es auf Wowhead.") + "\n", "dim"))
        parts.append(("\n" + r["url"], "mono"))
        self._set_text(self.wh_text, parts=parts)
        self.wh_text.configure(state="disabled")

    def wh_open(self):
        r = self._wh_selected()
        if r:
            self.open_link(r["url"])

    def wh_copy(self):
        r = self._wh_selected()
        if r:
            self.clipboard_clear()
            self.clipboard_append(r["url"])
            self.set_status(tr("Link kopiert: {0}", r["url"]))

    # Favoriten (in der Leeroyzan-Konfiguration, je Eintrag mit seiner Wowhead-Ausgabe)
    def _wh_favs(self) -> list:
        favs = self.cfg.get("wowhead_favs")
        if not isinstance(favs, list):
            favs = self.cfg["wowhead_favs"] = []
        return favs

    @staticmethod
    def _wh_key(r) -> tuple:
        return (r.get("edition", ""), r.get("kind"), r.get("id"))

    def _wh_is_fav(self, r) -> bool:
        k = self._wh_key(r)
        return any(self._wh_key(f) == k for f in self._wh_favs())

    def _wh_fill(self, rows):
        self.wh_rows = list(rows)
        self.wh_tree.delete(*self.wh_tree.get_children())
        favs = {self._wh_key(f) for f in self._wh_favs()}
        editions = dict(WOWHEAD_EDITIONS)
        for i, r in enumerate(self.wh_rows):
            tags = [f"q{r['quality']}"] if r.get("quality") is not None else []
            if i % 2:
                tags.append("odd")
            name = r["name"]
            if self.wh_mode == "results" and self._wh_key(r) in favs:
                name = "★ " + name
            kind = tr(r["kind_name"])
            if self.wh_mode == "favs" and r.get("edition", "") != self._wh_edition_key():
                kind += " · " + tr(editions.get(r.get("edition", ""), "?")).split(" (")[0].replace(" Classic", "")
            self.wh_tree.insert("", "end", iid=str(i), values=(name, kind, r["id"]), tags=tags)
        self._wh_mode_buttons()
        self._wh_show()

    def _wh_mode_buttons(self):
        n = len(self._wh_favs())
        self.wh_mode_btns["results"].configure(text=tr("Treffer ({0})", len(self.wh_results)),
                                               style="ToggleOn.TButton" if self.wh_mode == "results" else "ToggleOff.TButton")
        self.wh_mode_btns["favs"].configure(text=tr("★ Favoriten ({0})", n),
                                            style="ToggleOn.TButton" if self.wh_mode == "favs" else "ToggleOff.TButton")

    def wh_set_mode(self, mode: str):
        self.wh_mode = mode
        if mode == "favs":
            self._wh_fill(self._wh_favs())
            self.wh_info_var.set(tr("Doppelklick öffnet die Seite auf Wowhead, Entf entfernt den Favoriten.") if self.wh_rows else
                                 tr("Noch keine Favoriten. Wähle einen Treffer und klicke auf „☆ Favorit“."))
        else:
            self._wh_fill(self.wh_results)
            self.wh_info_var.set(plural(len(self.wh_results), "{0} Treffer", "{0} Treffer") + "  ·  " +
                                 tr("Doppelklick öffnet die Seite auf Wowhead.") if self.wh_results else
                                 tr("Tippe einen Namen ein – Gegenstand, NPC, Quest, Zauber, Zone …"))
        if self.wh_rows:
            self.wh_tree.selection_set("0")

    def wh_toggle_fav(self):
        r = self._wh_selected()
        if not r:
            return
        favs = self._wh_favs()
        k = self._wh_key(r)
        sel = self.wh_tree.selection()
        if self._wh_is_fav(r):
            favs[:] = [f for f in favs if self._wh_key(f) != k]
            self.set_status(tr("Aus den Favoriten entfernt: {0}", r["name"]))
        else:
            favs.insert(0, {x: r.get(x) for x in ("kind", "kind_name", "id", "name", "quality", "edition", "url")})
            self.set_status(tr("Zu den Favoriten hinzugefügt: {0}", r["name"]))
        save_config(self.cfg)
        if self.wh_mode == "favs":
            self.wh_set_mode("favs")
            if sel and self.wh_rows:
                self.wh_tree.selection_set(str(min(int(sel[0]), len(self.wh_rows) - 1)))
        else:
            self._wh_fill(self.wh_rows)
            if sel:
                self.wh_tree.selection_set(sel[0])

    def wh_open_search(self):
        q = self._wh_query()
        ed = self._wh_edition_key()
        self.open_link(wowhead_search_url(ed, q) if q else wowhead_base(ed))
