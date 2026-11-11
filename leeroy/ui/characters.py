"""Hauptfenster: CharactersPage."""
from __future__ import annotations

from datetime import datetime

from tkinter import ttk
import tkinter as tk

from ..i18n import tr
from ..sessions import load_sessions
from ..reports import CLASS_NAMES, fmt_day, fmt_dur, fmt_int, fmt_money, plural, skill_group_by_name
from ..linkdata import char_items, find_items, item_name, item_quality, shopping_list
from .dialogs import Placeholder


class CharactersPage:
    """Teil des Hauptfensters (wird in App eingemischt)."""

    # --- Charaktere -------------------------------------------------------------------------- #
    def _page_characters(self):
        page, head = self._page("characters", tr("Leeroylink"), tr("Charaktere"))
        right = ttk.Frame(head)
        right.pack(side="right", anchor="s")
        ttk.Label(right, textvariable=self.chars_total_var, style="Body.TLabel").pack(side="right")
        top = ttk.Frame(page)
        top.pack(fill="both", expand=True, pady=(12, 0))
        left = ttk.Frame(top, width=520)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        frame, self.chars_tree = self._list(left, (("name", tr("Charakter")), ("level", tr("Stufe")), ("cls", tr("Klasse")),
                                                    ("gold", tr("Gold")), ("seen", tr("Zuletzt"))),
                                            (128, 66, 100, 108, 104), stretch=("name",))
        frame.pack(fill="both", expand=True)
        self.chars_tree.bind("<<TreeviewSelect>>", lambda e: self._chars_show())
        box = self._card_box(top, pady=(0, 0), side="left", fill="both", expand=True, padx=(14, 0))
        tf, self.chars_text = self._text(box, height=8)
        tf.pack(fill="both", expand=True)
        self._tag_text(self.chars_text)

        low = ttk.Frame(page)
        low.pack(fill="both", expand=True, pady=(12, 0))
        low.columnconfigure(0, weight=3, uniform="low")
        low.columnconfigure(1, weight=2, uniform="low")
        low.rowconfigure(0, weight=1)
        lcol, rcol = ttk.Frame(low), ttk.Frame(low)
        lcol.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        rcol.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        sbox = self._card_box(lcol, pady=(0, 0), fill="both", expand=True, title=tr("Einkaufsliste"))
        r1 = ttk.Frame(sbox, style="Panel.TFrame")
        r1.pack(fill="x")
        self.shop_char_cb = ttk.Combobox(r1, textvariable=self.shop_char_var, state="readonly", width=14)
        self.shop_char_cb.pack(side="left")
        self.shop_char_cb.bind("<<ComboboxSelected>>", lambda e: self._shop_fill(level=1))
        self.shop_prof_cb = ttk.Combobox(r1, textvariable=self.shop_prof_var, state="readonly", width=14)
        self.shop_prof_cb.pack(side="left", padx=(6, 0))
        self.shop_prof_cb.bind("<<ComboboxSelected>>", lambda e: self._shop_fill(level=2))
        sp = ttk.Spinbox(r1, from_=1, to=999, width=5, textvariable=self.shop_qty_var, command=self.shop_calc)
        sp.pack(side="right")
        sp.bind("<Return>", lambda e: self.shop_calc())
        ttk.Label(r1, text=tr("Menge"), style="Panel.TLabel").pack(side="right", padx=(8, 6))
        r2 = ttk.Frame(sbox, style="Panel.TFrame")
        r2.pack(fill="x", pady=(6, 0))
        fe = ttk.Entry(r2, textvariable=self.shop_filter_var, width=14)
        fe.pack(side="left")
        Placeholder(fe, self.shop_filter_var, tr("Filter"))
        self.shop_filter_var.trace_add("write", lambda *a: self._shop_fill(level=2, keep=True))
        self.shop_recipe_cb = ttk.Combobox(r2, textvariable=self.shop_recipe_var, state="readonly", width=20)
        self.shop_recipe_cb.pack(side="left", fill="x", expand=True, padx=(6, 0))
        self.shop_recipe_cb.bind("<<ComboboxSelected>>", lambda e: self.shop_calc())
        tf2, self.shop_text = self._text(sbox, height=7)
        tf2.pack(fill="both", expand=True, pady=(8, 0))
        self._tag_text(self.shop_text)

        fbox = self._card_box(rcol, pady=(0, 0), fill="both", expand=True, title=tr("Wer hat was?"))
        fr = ttk.Frame(fbox, style="Panel.TFrame")
        fr.pack(fill="x")
        fe2 = ttk.Entry(fr, textvariable=self.find_var)
        fe2.pack(side="left", fill="x", expand=True)
        Placeholder(fe2, self.find_var, tr("Item-Name oder ID, z. B. Kupfererz"))
        fe2.bind("<Return>", lambda e: self.find_item())
        ttk.Button(fr, text=tr("Suchen"), style="Primary.TButton", command=self.find_item).pack(side="left", padx=(8, 0))
        tf3, self.find_text = self._text(fbox, height=7)
        tf3.pack(fill="both", expand=True, pady=(8, 0))
        self._tag_text(self.find_text)

    def _chars_sorted(self) -> list[dict]:
        return sorted(self.link_state["chars"].values(), key=lambda c: (-c["lastSeen"], c["name"].lower()))

    def refresh_characters(self):
        if getattr(self, "chars_tree", None) is None or not self.chars_tree.winfo_exists():
            return
        chars = self._chars_sorted()
        total = sum(c["money"] for c in chars)
        self.chars_total_var.set(tr("{0} · zusammen {1}", plural(len(chars), "{0} Charakter", "{0} Charaktere"),
                                    fmt_money(total)) if chars else "")
        tv = self.chars_tree
        keep = tv.selection()[0] if tv.selection() else ""
        tv.delete(*tv.get_children())
        self._chars_rows = chars
        for i, c in enumerate(chars):
            cls = tr(CLASS_NAMES.get(c["class"], c["class"].title())) if c["class"] else ""
            tv.insert("", "end", iid=f"c{i}", tags=("odd",) if i % 2 else (), values=(
                c["name"], c["level"] or "", cls, fmt_money(c["money"]),
                datetime.fromtimestamp(c["lastSeen"]).strftime(tr("%d.%m.%Y")) if c["lastSeen"] else ""))
        if chars:
            tv.selection_set(keep if keep and tv.exists(keep) else "c0")
        self._chars_show()
        self._shop_fill(level=0, keep=True)

    def _chars_show(self):
        P: list[tuple[str, str]] = []
        add = lambda text, tag="": P.append((text, tag))   # noqa: E731
        sel = self.chars_tree.selection()
        c = None
        if sel:
            try:
                c = self._chars_rows[int(sel[0][1:])]
            except (ValueError, IndexError):
                c = None
        if c is None:
            add(tr("Noch keine Charaktere") + "\n", "title")
            add(tr("Leeroylink speichert Klasse, Stufe, Gold, Berufe sowie Taschen- und Bankinhalt jedes Charakters, "
                   "mit dem du dich einloggst (Bank: sobald du sie einmal öffnest).") + "\n", "")
        else:
            cls = tr(CLASS_NAMES.get(c["class"], c["class"].title())) if c["class"] else ""
            add(c["name"] + "\n", "title")
            add(" · ".join(x for x in (cls, tr("Stufe {0}", c["level"]) if c["level"] else "", c["realm"],
                                       c["guild"] and f"<{c['guild']}>") if x) + "\n", "dim")
            add(tr("Gold: {0}", fmt_money(c["money"])) + "\n")
            mine = [s for s in self.sessions_all if s["char"] == c["key"]] or [
                s for s in load_sessions(self.flavor) if s["char"] == c["key"]]
            if mine:
                add(tr("{0} in {1} · zuletzt {2}", fmt_dur(sum(s["played"] for s in mine)),
                       plural(len(mine), "{0} Sitzung", "{0} Sitzungen"), fmt_day(max(s["start"] for s in mine))) + "\n", "dim")
            profs = [sk for sk in c["skills"] if skill_group_by_name(sk["name"]) == "prof"] or c["skills"][:6]
            if c["skills"]:
                add("\n" + tr("Berufe und Fertigkeiten") + "\n", "head")
                for sk in c["skills"]:
                    book = c["recipes"].get(sk["name"])
                    extra = tr("  · {0} Rezepte gespeichert", len(book["list"])) if book else ""
                    add(f"{sk['name']} {sk['rank']}/{sk['max']}" + extra + "\n", "" if sk in profs else "dim")
            for prof, book in c["recipes"].items():
                if not any(sk["name"] == prof for sk in c["skills"]):
                    add(f"{prof} {book['rank']}/{book['max']}" + tr("  · {0} Rezepte gespeichert", len(book["list"])) + "\n")
            items = char_items(c)
            if items:
                add("\n" + tr("Taschen und Bank") + "\n", "head")
                if not c["bankSeen"]:
                    add(tr("Bank noch nicht gelesen – öffne sie einmal im Spiel.") + "\n", "dim")
                top = sorted(items.items(), key=lambda kv: kv[1], reverse=True)[:30]
                for i, (k, n) in enumerate(top):
                    if i:
                        add(", ", "dim")
                    add(item_name(self.link_state, k), f"q{min(max(item_quality(self.link_state, k), 0), 6)}")
                    add(f" ×{fmt_int(n)}", "dim")
                add("\n")
        self._set_text(self.chars_text, parts=P)

    def _shop_fill(self, level: int = 0, keep: bool = False):
        """Auswahl der Einkaufsliste: Charakter -> Beruf -> Rezept (mit Filter)."""
        if getattr(self, "shop_char_cb", None) is None or not self.shop_char_cb.winfo_exists():
            return
        chars = {c["name"] if sum(1 for o in self.link_state["chars"].values() if o["name"] == c["name"]) == 1
                 else f"{c['name']} ({c['realm']})": c for c in self.link_state["chars"].values() if c["recipes"]}
        self._shop_chars = chars
        if level <= 0:
            self.shop_char_cb["values"] = sorted(chars, key=str.lower)
            if self.shop_char_var.get() not in chars:
                self.shop_char_var.set(next(iter(sorted(chars, key=str.lower)), ""))
        c = chars.get(self.shop_char_var.get())
        profs = sorted(c["recipes"], key=str.lower) if c else []
        if level <= 1:
            self.shop_prof_cb["values"] = profs
            if self.shop_prof_var.get() not in profs:
                self.shop_prof_var.set(profs[0] if profs else "")
        book = c["recipes"].get(self.shop_prof_var.get()) if c else None
        flt = self.shop_filter_var.get().strip().lower()
        if flt == tr("Filter").lower():
            flt = ""
        names = sorted((n for n in (book["list"] if book else {}) if flt in n.lower()), key=str.lower)
        self.shop_recipe_cb["values"] = names
        if self.shop_recipe_var.get() not in names:
            self.shop_recipe_var.set(names[0] if names else "")
        self.shop_calc()

    def shop_calc(self):
        P: list[tuple[str, str]] = []
        add = lambda text, tag="": P.append((text, tag))   # noqa: E731
        c = getattr(self, "_shop_chars", {}).get(self.shop_char_var.get())
        prof, recipe = self.shop_prof_var.get(), self.shop_recipe_var.get()
        try:
            qty = max(1, min(999, int(self.shop_qty_var.get())))
        except (ValueError, tk.TclError):
            qty = 1
        if not c or not recipe or recipe not in c["recipes"].get(prof, {}).get("list", {}):
            add(tr("Öffne im Spiel einmal dein Berufsfenster – dann merkt sich Leeroylink die Rezepte, und hier "
                   "entsteht die Einkaufsliste über alle Charaktere.") + "\n", "dim")
        else:
            res = shopping_list(self.link_state, c["key"], prof, recipe, qty)
            add(tr("{0} × {1}", qty, res["product"]), "hl")
            add(("  " + tr("({0} Herstellungen)", res["crafts"]) if res["made"] > 1 or res["crafts"] != qty else "") + "\n", "dim")
            for r in res["rows"]:
                add(f"{r['need']}× ", "")
                add(r["name"], f"q{min(max(r['q'], 0), 6)}")
                if r["have"] is None:
                    add("  " + tr("(Bestand unbekannt)") + "\n", "dim")
                    continue
                if r["missing"] == 0 and r["have"] >= r["need"]:
                    add("  ✓ " + tr("hast du ({0})", r["have"]) + "\n", "ok")
                    continue
                parts = [tr("hast {0}", r["have"])]
                if r["others"]:
                    parts.append(tr("bei {0}", ", ".join(f"{o['char']} {o['n']}" for o in r["others"][:4])))
                add("  " + " · ".join(parts), "dim")
                if r["missing"]:
                    add("  " + tr("fehlt: {0}", r["missing"]) + "\n", "warn")
                else:
                    add("  " + tr("→ {0} herüberschicken", r["transfer"]) + "\n", "ok")
        self._set_text(self.shop_text, parts=P)

    def find_item(self):
        text = self.find_var.get().strip()
        if text == tr("Item-Name oder ID, z. B. Kupfererz"):
            text = ""
        P: list[tuple[str, str]] = []
        add = lambda t, tag="": P.append((t, tag))   # noqa: E731
        res = find_items(self.link_state, text) if text else []
        if not text:
            add(tr("Durchsucht Taschen und Bank aller Charaktere.") + "\n", "dim")
        elif not res:
            add(tr("Nichts gefunden.") + "\n", "dim")
        groups: dict[str, list] = {}
        for r in res:
            groups.setdefault(r["id"], []).append(r)
        for k, rows in groups.items():
            add(rows[0]["name"], f"q{min(max(rows[0]['q'], 0), 6)}")
            add(f"  ×{fmt_int(sum(r['n'] for r in rows))}\n", "dim")
            add("    " + ", ".join(f"{r['char']} {r['n']} ({tr('Bank') if r['where'] == 'bank' else tr('Taschen')})"
                                   for r in rows) + "\n")
        self._set_text(self.find_text, parts=P)
