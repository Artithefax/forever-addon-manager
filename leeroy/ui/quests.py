"""Seite „Quests“: Questdatenbank aus QuestieDB mit Questreihen und Belohnungen. (Mixin, wird in App eingemischt)."""
from __future__ import annotations

import time
from datetime import datetime

from tkinter import ttk
import tkinter as tk

from ..i18n import cur_lang, tr
from ..config import log, save_config
from ..reports import CLASS_NAMES, ReportParts, fmt_int, fmt_money, plural
from ..wowhead import wowhead_url
from ..questdb import (
    QuestDBError,
    REWARD_REPOS,
    _REWARD_TRIED,
    ensure_reward_data,
    load_questdb,
    load_reward_data,
    quest_rewards,
    rewards_due,
)
from .dialogs import Placeholder


class QuestsPage:
    """Teil des Hauptfensters (wird in App eingemischt)."""

    # --- Quests (Questdatenbank aus QuestieDB) --------------------------------------- #
    QDB_SIDES = [("", "Beide Fraktionen"), ("A", "Allianz"), ("H", "Horde")]
    CLASS_BITS = [(1, "WARRIOR"), (2, "PALADIN"), (4, "HUNTER"), (8, "ROGUE"), (16, "PRIEST"), (32, "DEATHKNIGHT"),
                  (64, "SHAMAN"), (128, "MAGE"), (256, "WARLOCK"), (1024, "DRUID")]

    def _page_quests(self):
        page, head = self._page("quests", "QuestieDB", tr("Quests"))
        self.qdb = None
        self.qdb_key = None
        self.qdb_seq = 0
        self.qdb_rows: list[int] = []
        self.qdb_current = None
        self.qdb_history: list[int] = []
        self.qdb_status_var = tk.StringVar(value="")
        self.qdb_info_var = tk.StringVar(value=tr("Suche nach Quest, NPC, Zone oder Quest-ID – oder grenze nur die Stufe ein."))
        self.qdb_var = tk.StringVar()
        self.qdb_side_var = tk.StringVar(value=tr(self.QDB_SIDES[0][1]))
        self.qdb_lmin = tk.StringVar(value="")
        self.qdb_lmax = tk.StringVar(value="")
        self.qdb_rewards = None
        self.qdb_guide_btn = ttk.Button(head, command=self.toggle_quest_guide)
        self.qdb_guide_btn.pack(side="right", anchor="s", padx=(12, 0))
        ttk.Label(head, textvariable=self.qdb_status_var, style="Body.TLabel").pack(side="right", anchor="s")
        self._quest_guide_button()

        row = ttk.Frame(page)
        row.pack(fill="x", pady=(12, 0))
        self.qdb_entry = ttk.Entry(row, textvariable=self.qdb_var)
        self.qdb_entry.pack(side="left", fill="x", expand=True)
        Placeholder(self.qdb_entry, self.qdb_var, tr("Quest, NPC, Zone oder ID, z. B. Hogger"))
        self.qdb_entry.bind("<Return>", lambda e: self.qdb_search())
        cb = ttk.Combobox(row, textvariable=self.qdb_side_var, state="readonly", width=16,
                          values=[tr(label) for _, label in self.QDB_SIDES])
        cb.pack(side="left", padx=(8, 0))
        cb.bind("<<ComboboxSelected>>", lambda e: self.qdb_search())
        ttk.Label(row, text=tr("Stufe"), style="Body.TLabel").pack(side="left", padx=(10, 4))
        for var in (self.qdb_lmin, self.qdb_lmax):
            sp = ttk.Spinbox(row, from_=0, to=80, width=4, textvariable=var, command=self.qdb_search)
            sp.pack(side="left", padx=(0, 2))
            sp.bind("<Return>", lambda e: self.qdb_search())
        ttk.Button(row, text=tr("Suchen"), style="Primary.TButton", command=self.qdb_search).pack(side="left", padx=(8, 0))
        ttk.Label(page, textvariable=self.qdb_info_var, style="Body.TLabel").pack(anchor="w", pady=(8, 0))

        body = ttk.Frame(page)
        body.pack(fill="both", expand=True, pady=(8, 0))
        left = ttk.Frame(body, width=500)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        frame, self.qdb_tree = self._list(left, (("name", tr("Quest")), ("lvl", tr("Stufe")), ("zone", tr("Zone")), ("id", "ID")),
                                          (226, 66, 142, 52), stretch=("name",))
        frame.pack(fill="both", expand=True)
        self.qdb_tree.tag_configure("A", foreground="#7fb2ff")
        self.qdb_tree.tag_configure("H", foreground="#ff7f7f")
        self.qdb_tree.bind("<<TreeviewSelect>>", lambda e: self._qdb_selected())
        self.qdb_tree.bind("<Double-1>", lambda e: self.qdb_wowhead())
        box = self._card_box(body, pady=(0, 0), side="left", fill="both", expand=True, padx=(14, 0), title=tr("Details"))
        tf, self.qdb_text = self._text(box, height=8)
        tf.pack(fill="both", expand=True)
        self._tag_text(self.qdb_text)
        self.qdb_text.tag_configure("A", foreground="#7fb2ff")
        self.qdb_text.tag_configure("H", foreground="#ff7f7f")
        btns = ttk.Frame(box, style="Panel.TFrame")
        btns.pack(fill="x", pady=(10, 0))
        self.qdb_back_btn = ttk.Button(btns, text=tr("← Zurück"), command=self.qdb_back, state="disabled")
        self.qdb_back_btn.pack(side="left")
        self.qdb_wh_btn = ttk.Button(btns, text=tr("Auf Wowhead öffnen ↗"), style="Primary.TButton",
                                     command=self.qdb_wowhead, state="disabled")
        self.qdb_wh_btn.pack(side="left", padx=(6, 0))
        self.qdb_fav_btn = ttk.Button(btns, text=tr("☆ Favorit"), command=self.qdb_fav, state="disabled")
        self.qdb_fav_btn.pack(side="right")
        self._set_text(self.qdb_text, parts=[(tr("Die Quests kommen aus QuestieDB, der Datenbank von Questie. Leeroyzan "
                                                   "liest sie direkt aus dem Addon-Ordner – QuestieDB muss dafür nur "
                                                   "installiert sein. Belohnungen mit Gegenständen kennt Leeroyzan, sobald "
                                                   "einer deiner Charaktere die Quest gesehen hat (Leeroylink)."), "dim")])
        self.qdb_text.configure(state="disabled")

    def _qdb_locale(self) -> str:
        return "deDE" if cur_lang() == "de" else "enUS"

    def qdb_open(self):
        """Lädt die Datenbank beim ersten Öffnen der Seite (im Hintergrund)."""
        self.after(50, self.qdb_entry.focus_set)
        if not self.root_path or not self.flavor:
            self.qdb_status_var.set("")
            self.qdb_info_var.set(tr("Wähle zuerst den WoW-Ordner."))
            return
        key = (self.root_path, self.flavor)
        if self.qdb is not None and self.qdb_key == key:
            return
        self.qdb, self.qdb_key = None, key
        self.qdb_status_var.set(tr("Lade QuestieDB …"))
        root, flavor = key

        def load():
            db = load_questdb(root, flavor)
            db.reward_items(0)               # Belohnungsindex gleich hier bauen, nicht beim ersten Klick
            return db, load_reward_data(flavor)

        def done(res, err):
            if (self.root_path, self.flavor) != key:
                return
            if err:
                self.qdb_status_var.set("")
                if isinstance(err, QuestDBError):
                    self.qdb_info_var.set(str(err) + " " + tr("Installiere Questie (bringt QuestieDB mit) über „Entdecken“ – danach hier neu öffnen."))
                else:
                    log.warning("QuestieDB: %s", err)
                    self.qdb_info_var.set(tr("QuestieDB konnte nicht gelesen werden: {0}", str(err)[:160]))
                self.qdb_key = None
                return
            self.qdb, self.qdb_rewards = res
            self._qdb_status()
            if self.qdb_var.get().strip() or self.qdb_lmin.get().strip() or self.qdb_lmax.get().strip():
                self.qdb_search()
            self._qdb_load_rewards()
        self.run_quiet(load, done)

    def _qdb_status(self, extra: str = ""):
        db = self.qdb
        if db is None:
            return
        text = tr("QuestieDB {0} · {1} Quests", db.version, fmt_int(len(db.quests)))
        if self.qdb_rewards:
            text += "  ·  " + tr("Belohnungen: cMaNGOS ({0} Quests)", fmt_int(len(self.qdb_rewards.get("q", {}))))
        self.qdb_status_var.set(text + extra)

    def _qdb_load_rewards(self):
        """Belohnungsdaten (Gegenstände, Wahl, Gold) einmal von GitHub holen, wenn sie fehlen oder alt sind."""
        flavor = self.flavor
        if not rewards_due(flavor) or flavor in _REWARD_TRIED:
            return
        self._qdb_status("  ·  " + tr("lade Belohnungen …"))
        token = self.cfg.get("github_token", "")

        def done(data, err):
            if err:
                log.warning("Questbelohnungen: %s", err)
            if data and flavor == self.flavor:
                self.qdb_rewards = data
                if self.qdb_current is not None:
                    self.qdb_show(self.qdb_current, remember=False)
                self.schedule_link_write()                 # Quests.lua mit den Belohnungen neu schreiben
            self._qdb_status()
        self.run_quiet(lambda: ensure_reward_data(flavor, token), done)

    # Questbegleiter im Spiel an/aus (gilt die jüngste Änderung – hier oder im Spiel)
    def quest_guide_on(self) -> bool:
        mine = self.cfg.get("quest_guide") or {}
        game = self.link_state.get("questGuide") or {"on": True, "at": 0}
        if game.get("at", 0) > mine.get("at", 0):
            return bool(game.get("on", True))
        return bool(mine.get("on", True))

    def toggle_quest_guide(self):
        on = not self.quest_guide_on()
        self.cfg["quest_guide"] = {"on": on, "at": int(time.time())}
        save_config(self.cfg)
        self._quest_guide_button()
        self.schedule_link_write()
        self.set_status(tr("Questbegleiter im Spiel eingeschaltet – nach dem nächsten Einloggen oder /reload aktiv.") if on else
                        tr("Questbegleiter im Spiel ausgeschaltet – ab dem nächsten Einloggen oder /reload, ohne Questdaten im Speicher."))

    def _quest_guide_button(self):
        if getattr(self, "qdb_guide_btn", None) is None:
            return
        on = self.quest_guide_on()
        self.qdb_guide_btn.configure(text=tr("Questbegleiter im Spiel: AN") if on else tr("Questbegleiter im Spiel: AUS"),
                                     style="ToggleOn.TButton" if on else "ToggleOff.TButton")

    def _qdb_side(self) -> str:
        label = self.qdb_side_var.get()
        return next((k for k, l in self.QDB_SIDES if tr(l) == label), "")

    def qdb_search(self):
        if self.qdb is None:
            self.qdb_open()
            return
        q = self.qdb_var.get().strip()
        try:
            lmin, lmax = int(self.qdb_lmin.get() or 0), int(self.qdb_lmax.get() or 0)
        except ValueError:
            lmin = lmax = 0
        if not q and not lmin and not lmax and not self._qdb_side():
            self.qdb_info_var.set(tr("Suche nach Quest, NPC, Zone oder Quest-ID – oder grenze nur die Stufe ein."))
            return
        self.qdb_seq += 1
        seq, db, side, loc = self.qdb_seq, self.qdb, self._qdb_side(), self._qdb_locale()
        self.qdb_info_var.set(tr("Suche …"))
        self.run_quiet(lambda: db.search(q, loc, side, lmin, lmax), lambda res, err: self._qdb_results(seq, res, err))

    def _qdb_results(self, seq, res, err):
        if seq != self.qdb_seq:
            return
        self.qdb_tree.delete(*self.qdb_tree.get_children())
        self.qdb_rows = res or []
        if err:
            log.warning("Quest-Suche: %s", err)
            self.qdb_info_var.set(tr("Die Suche ist fehlgeschlagen: {0}", str(err)[:160]))
            return
        db, loc = self.qdb, self._qdb_locale()
        zn = self.link_state.get("zoneNames", {})
        for i, qid in enumerate(self.qdb_rows):
            q = db.quests[qid]
            tags = [q["side"]] if q["side"] else []
            if i % 2:
                tags.append("odd")
            self.qdb_tree.insert("", "end", iid=str(qid), values=(db.quest_name(qid, loc), q["lvl"],
                                                               db.zone_name(q["zone"], zn), qid), tags=tags)
        n = len(self.qdb_rows)
        self.qdb_info_var.set((plural(n, "{0} Quest", "{0} Quests") + (tr(" (die ersten 300)") if n >= 300 else "")
                               + "  ·  " + tr("Doppelklick öffnet die Quest auf Wowhead.")) if n else tr("Nichts gefunden."))
        if n:
            self.qdb_tree.selection_set(str(self.qdb_rows[0]))

    def _qdb_selected(self):
        sel = self.qdb_tree.selection()
        if sel:
            self.qdb_show(int(sel[0]), remember=False)

    def qdb_show(self, qid: int, remember: bool = True):
        db = self.qdb
        if db is None or qid not in db.quests:
            return
        if remember and self.qdb_current and self.qdb_current != qid:
            self.qdb_history.append(self.qdb_current)
        self.qdb_current = qid
        for b in (self.qdb_wh_btn, self.qdb_fav_btn):
            b.configure(state="normal")
        self.qdb_back_btn.configure(state="normal" if self.qdb_history else "disabled")
        self.qdb_fav_btn.configure(text=tr("★ Favorit entfernen") if self._qdb_is_fav(qid) else tr("☆ Favorit"))
        self._set_text(self.qdb_text, parts=self._qdb_parts(qid))
        self.qdb_text.configure(state="disabled")
        self.qdb_text.yview_moveto(0)

    def _qdb_link(self, txt_parts, qid: int, text: str, tag: str = ""):
        """Klickbarer Questname im Detailtext."""
        name = f"qlink{qid}"
        self.qdb_text.tag_configure(name, underline=False)
        self.qdb_text.tag_bind(name, "<Button-1>", lambda e, i=qid: self.qdb_show(i))
        self.qdb_text.tag_bind(name, "<Enter>", lambda e: self.qdb_text.configure(cursor="hand2"))
        self.qdb_text.tag_bind(name, "<Leave>", lambda e: self.qdb_text.configure(cursor=""))
        txt_parts.append((text, (name, "hl") if not tag else (name, tag)))

    def _qdb_where(self, g: dict) -> str:
        db, loc = self.qdb, self._qdb_locale()
        zn = self.link_state.get("zoneNames", {})
        out = []
        for nid in g.get("npc", [])[:3]:
            n = db.npc(nid, loc)
            if n:
                s = n["name"] + (f" <{n['sub']}>" if n["sub"] else "")
                if n["zone"]:
                    s += " – " + db.zone_name(n["zone"], zn)
                    if n["pos"]:
                        s += f" {n['pos'][0]:.0f}, {n['pos'][1]:.0f}"
                out.append(s)
        for oid in g.get("obj", [])[:2]:
            out.append(db.obj_name(oid, loc) + " " + tr("(Objekt)"))
        for iid in g.get("item", [])[:2]:
            out.append(db.item_name(iid, loc) + " " + tr("(Gegenstand)"))
        return "; ".join(out)

    def _qdb_parts(self, qid: int) -> list:
        """Alles zu einer Quest als (Text, Tag)-Teile für das Detailfeld."""
        db, loc = self.qdb, self._qdb_locale()
        q = db.quests[qid]
        chain = db.chain(qid)
        out = ReportParts()
        self._qdb_part_head(out, db, loc, q, qid)
        self._qdb_part_chain(out, db, loc, qid, chain)
        self._qdb_part_task(out, db, loc, q, qid)
        self._qdb_part_reward(out, db, loc, q, qid)
        self._qdb_part_links(out, db, loc, q, qid, chain)
        return out.parts

    def _qdb_part_head(self, out, db, loc, q, qid):
        """Name, Stufe, Zone, Fraktion, Klassen."""
        zn = self.link_state.get("zoneNames", {})
        out.line(db.quest_name(qid, loc), "title")
        meta = [tr("Stufe {0}", q["lvl"])]
        if q["req"] > 1:
            meta.append(tr("ab Stufe {0}", q["req"]))
        if q["zone"]:
            meta.append(db.zone_name(q["zone"], zn))
        meta.append({"A": tr("Allianz"), "H": tr("Horde")}.get(q["side"], tr("beide Fraktionen")))
        classes = [tr(CLASS_NAMES[c]) for bit, c in self.CLASS_BITS if q["classes"] & bit]
        if classes and len(classes) < 9:
            meta.append(", ".join(classes))
        if q["repeatable"]:
            meta.append(tr("wiederholbar"))
        out.line("  ·  ".join(meta) + f"  ·  ID {qid}", "dim")

    def _qdb_part_chain(self, out, db, loc, qid, chain):
        """Die Questreihe mit der aktuellen Quest."""
        if len(chain) > 1:
            pos = chain.index(qid) + 1
            out.head(tr("Questreihe – Teil {0} von {1}", pos, len(chain)))
            for i, cid in enumerate(chain, 1):
                c = db.quests[cid]
                out.parts.append((f"{i:>2}. ", "dim"))
                if cid == qid:
                    out.parts.append((db.quest_name(cid, loc), "title"))
                else:
                    self._qdb_link(out.parts, cid, db.quest_name(cid, loc), c["side"] or "hl")
                out.parts.append((f"  [{c['lvl']}]\n", "dim"))

    def _qdb_part_task(self, out, db, loc, q, qid):
        """Questgeber, Abgabe, Aufgabentext und Ziele."""
        start, end = self._qdb_where(q["start"]), self._qdb_where(q["end"])
        if start or end:
            out.head(tr("Questgeber und Abgabe"))
            if start:
                out.line(tr("Beginnt bei: {0}", start))
            if end:
                out.line(tr("Abgabe bei: {0}", end))
        texts = db.quest_text(qid, loc)
        if texts:
            out.head(tr("Aufgabe"))
            for t in texts:
                out.line(t)
        goals = ([tr("Besiegen: {0}", ", ".join((db.npc(n, loc) or {}).get("name", f"#{n}") for n in q["kill"][:6]))] if q["kill"] else []) + \
                ([tr("Sammeln: {0}", ", ".join(db.item_name(i, loc) for i in q["items"][:6]))] if q["items"] else []) + \
                ([tr("Benutzen: {0}", ", ".join(db.obj_name(o, loc) for o in q["objs"][:6]))] if q["objs"] else [])
        for g in goals:
            out.line("• " + g, "dim")

    def _qdb_part_reward(self, out, db, loc, q, qid):
        """Belohnung mit Quelle."""
        fn = self.link_state.get("factionNames", {})
        out.head(tr("Belohnung"))
        seen = self.link_state.get("questRewards", {}).get(qid)
        rw = quest_rewards(db, qid, self.qdb_rewards, seen, loc, self.link_state.get("items", {}))
        xp = (seen or {}).get("xp") or (db.xp.get(qid) or (0, 0))[1]
        bits = []
        if xp:
            bits.append(f"{fmt_int(xp)} XP")
        if rw["money"]:
            bits.append(fmt_money(rw["money"]))
        for fid, val in q["rep"]:
            bits.append(f"{db.faction_name(fid, fn)} {'+' if val > 0 else ''}{val}")
        if rw["spell"]:
            bits.append(tr("Zauber {0}", rw["spell"]) if isinstance(rw["spell"], int) else str(rw["spell"]))
        out.line("  ·  ".join(bits) if bits else "–")

        def items(lst):
            for it in lst:
                out.parts.append(("• ", ""))
                out.parts.append((it["name"], f"q{min(max(it['q'], 0), 6)}"))
                out.parts.append(((f" ×{it['n']}" if it["n"] > 1 else "") + "\n", ""))
        items(rw["items"])
        if rw["choice"]:
            out.line(tr("Eine davon zur Wahl:"), "dim")
            items(rw["choice"])
        if rw["unsure"]:
            out.line(tr("Gegenstände (laut QuestieDB – ob fest oder zur Wahl, ist nicht bekannt):"), "dim")
            items(rw["unsure"])
        if rw["maxmoney"] and not rw["money"]:
            out.line(tr("Auf Höchststufe statt Erfahrung: {0}", fmt_money(rw["maxmoney"])), "dim")
        src = {"game": tr("Quelle: im Spiel gesehen am {0}", datetime.fromtimestamp((seen or {}).get("t") or 0).strftime(tr("%d.%m.%Y"))),
               "cmangos": tr("Quelle: Spieldatenbank von cMaNGOS (Classic) – in WoW Forever kann es Abweichungen geben"),
               "questie": tr("Quelle: QuestieDB (Gold unbekannt)")}.get(rw["src"])
        if src:
            out.line(src, "dim")
        elif self.qdb_rewards is None and REWARD_REPOS.get(self.flavor):
            out.line(tr("Gegenstände und Gold: Die Belohnungsdaten werden gerade geladen …"), "dim")
        else:
            out.line(tr("Gegenstände und Gold: unbekannt. Leeroylink merkt sie sich, sobald einer deiner Charaktere "
                    "die Quest sieht – bis dahin hilft Wowhead."), "dim")

    def _qdb_part_links(self, out, db, loc, q, qid, chain):
        """Voraussetzungen, freigeschaltete und ausschließende Quests, Summe bis zum Ende der Reihe."""
        fn = self.link_state.get("factionNames", {})
        pre = [p for p in q["pre"] if p in db.quests and p not in chain]
        if pre or q["skill"]:
            out.head(tr("Voraussetzungen"))
            if q["skill"]:
                out.line(tr("Beruf/Fertigkeit {0}: mindestens {1}", q["skill"][0], q["skill"][1]))
            if pre:
                out.parts.append((tr("Erst abschließen ({0}): ", tr("alle") if q["pre_all"] else tr("eine davon")), ""))
                for i, p in enumerate(pre[:8]):
                    if i:
                        out.parts.append((", ", ""))
                    self._qdb_link(out.parts, p, db.quest_name(p, loc), db.quests[p]["side"] or "hl")
                out.parts.append(("\n", ""))
        more = [n for n in db.next_of.get(qid, []) if n not in chain]
        if more:
            out.head(tr("Schaltet außerdem frei"))
            for n in more[:10]:
                out.parts.append(("• ", ""))
                self._qdb_link(out.parts, n, db.quest_name(n, loc), db.quests[n]["side"] or "hl")
                out.parts.append((f"  [{db.quests[n]['lvl']}]\n", "dim"))
        excl = [x for x in q["exclusive"] if x in db.quests]
        if excl:
            out.head(tr("Schließt sich aus mit"))
            for x in excl[:6]:
                out.parts.append(("• ", ""))
                self._qdb_link(out.parts, x, db.quest_name(x, loc))
                out.parts.append(("\n", ""))
        if len(chain) > 1 and chain.index(qid) < len(chain) - 1:
            rest = chain[chain.index(qid):]
            total_xp = sum(((self.link_state.get("questRewards", {}).get(c) or {}).get("xp") or
                            (db.xp.get(c) or (0, 0))[1]) for c in rest)
            reps: dict[int, int] = {}
            for c in rest:
                for fid, val in db.quests[c]["rep"]:
                    reps[fid] = reps.get(fid, 0) + val
            out.head(tr("Bis zum Ende der Reihe ({0} Quests)", len(rest)))
            out.line("  ·  ".join([f"{fmt_int(total_xp)} XP"] + [f"{db.faction_name(f, fn)} +{v}" for f, v in reps.items()]))

    def qdb_back(self):
        if self.qdb_history:
            self.qdb_show(self.qdb_history.pop(), remember=False)
            self.qdb_back_btn.configure(state="normal" if self.qdb_history else "disabled")

    def _qdb_fav_entry(self, qid: int) -> dict:
        ed = self.cfg.get("wowhead_edition", "classic")
        return {"kind": "quest", "kind_name": "Quest", "id": qid, "name": self.qdb.quest_name(qid, self._qdb_locale()),
                "quality": None, "edition": ed, "url": wowhead_url(ed, "quest", qid)}

    def _qdb_is_fav(self, qid: int) -> bool:
        return self._wh_is_fav(self._qdb_fav_entry(qid))

    def qdb_fav(self):
        qid = self.qdb_current
        if qid is None:
            return
        e = self._qdb_fav_entry(qid)
        favs = self._wh_favs()
        if self._wh_is_fav(e):
            favs[:] = [f for f in favs if self._wh_key(f) != self._wh_key(e)]
            self.set_status(tr("Aus den Favoriten entfernt: {0}", e["name"]))
        else:
            favs.insert(0, e)
            self.set_status(tr("Zu den Favoriten hinzugefügt: {0}", e["name"]))
        save_config(self.cfg)
        self._wh_mode_buttons()
        self.qdb_fav_btn.configure(text=tr("★ Favorit entfernen") if self._qdb_is_fav(qid) else tr("☆ Favorit"))

    def qdb_wowhead(self):
        if self.qdb_current is not None:
            self.open_link(wowhead_url(self.cfg.get("wowhead_edition", "classic"), "quest", self.qdb_current))
