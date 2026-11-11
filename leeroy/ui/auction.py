"""Hauptfenster: AuctionPage."""
from __future__ import annotations


from tkinter import ttk
import tkinter as tk

from ..i18n import cur_lang, tr
from ..config import log, save_config
from ..reports import QUALITY_COLORS, fmt_int, fmt_money, plural
from ..wowhead import wowhead_url
from ..questdb import QuestDBError, _REWARD_TRIED, ensure_reward_data, load_questdb, load_reward_data, rewards_due
from ..auction import (
    ITEM_CLASSES,
    auc_day_date,
    auction_row,
    auctionator_savedvariables,
    item_details,
    load_auctionator,
    owned_counts,
)
from .theme import THEME
from .dialogs import Placeholder


class AuctionPage:
    """Teil des Hauptfensters (wird in App eingemischt)."""

    # --- Auktionshaus (Preise aus Auctionator) --------------------------------------- #
    AH_QUALITIES = [(-1, "Alle Qualitäten"), (0, "Schlecht"), (1, "Gewöhnlich"), (2, "Selten"), (3, "Rar"), (4, "Episch"), (5, "Legendär")]
    AH_COLUMNS = (("name", "Gegenstand"), ("price", "Preis"), ("avg", "Ø 14 Tage"), ("change", "Änderung"),
                  ("avail", "Anzahl"), ("last", "Stand"))

    def _page_auction(self):
        page, head = self._page("auction", "Auctionator", tr("Auktionshaus"))
        self.ah_data = None
        self.ah_sig = None
        self.ah_rows: list[dict] = []
        self.ah_shown: list[dict] = []
        self.ah_sort = ("price", True)
        self.ah_current = None
        self.ah_status_var = tk.StringVar(value="")
        self.ah_info_var = tk.StringVar(value="")
        self.ah_var = tk.StringVar()
        self.ah_cls_var = tk.StringVar(value=tr("Alle Kategorien"))
        self.ah_q_var = tk.StringVar(value=tr(self.AH_QUALITIES[0][1]))
        self.ah_realm_var = tk.StringVar()
        self.ah_lmin, self.ah_lmax = tk.StringVar(), tk.StringVar()
        self.ah_deal = tk.BooleanVar(value=False)
        self.ah_vendor = tk.BooleanVar(value=False)
        self.ah_owned = tk.BooleanVar(value=False)
        ttk.Button(head, text=tr("Neu einlesen"), command=lambda: self.ah_open(force=True)).pack(side="right", anchor="s", padx=(12, 0))
        self.ah_realm_cb = ttk.Combobox(head, textvariable=self.ah_realm_var, state="readonly", width=16)
        self.ah_realm_cb.bind("<<ComboboxSelected>>", lambda e: self._ah_build_rows())
        ttk.Label(head, textvariable=self.ah_status_var, style="Body.TLabel").pack(side="right", anchor="s")

        row = ttk.Frame(page)
        row.pack(fill="x", pady=(12, 0))
        e = ttk.Entry(row, textvariable=self.ah_var)
        e.pack(side="left", fill="x", expand=True)
        Placeholder(e, self.ah_var, tr("Gegenstand oder ID, z. B. Leinenstoff"))
        self.ah_var.trace_add("write", lambda *a: self._ah_filter_later())
        self.ah_cls_cb = ttk.Combobox(row, textvariable=self.ah_cls_var, state="readonly", width=20,
                                      values=[tr("Alle Kategorien")] + [tr(v) for v in ITEM_CLASSES.values()])
        self.ah_cls_cb.pack(side="left", padx=(8, 0))
        self.ah_cls_cb.bind("<<ComboboxSelected>>", lambda e: self.ah_filter())
        cb = ttk.Combobox(row, textvariable=self.ah_q_var, state="readonly", width=15,
                          values=[tr(l) for _, l in self.AH_QUALITIES])
        cb.pack(side="left", padx=(8, 0))
        cb.bind("<<ComboboxSelected>>", lambda e: self.ah_filter())
        ttk.Label(row, text=tr("Stufe"), style="Body.TLabel").pack(side="left", padx=(10, 4))
        for var in (self.ah_lmin, self.ah_lmax):
            sp = ttk.Spinbox(row, from_=0, to=80, width=4, textvariable=var, command=self.ah_filter)
            sp.pack(side="left", padx=(0, 2))
            sp.bind("<Return>", lambda e: self.ah_filter())
        row2 = ttk.Frame(page)
        row2.pack(fill="x", pady=(8, 0))
        for var, label in ((self.ah_deal, "Schnäppchen (mind. 20 % unter Ø)"), (self.ah_vendor, "Unter Händlerpreis"),
                           (self.ah_owned, "Im Besitz deiner Charaktere")):
            ttk.Checkbutton(row2, text=tr(label), variable=var, command=self.ah_filter).pack(side="left", padx=(0, 16))
        ttk.Label(row2, textvariable=self.ah_info_var, style="Body.TLabel").pack(side="right")

        body = ttk.Frame(page)
        body.pack(fill="both", expand=True, pady=(8, 0))
        left = ttk.Frame(body, width=610)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        frame, self.ah_tree = self._list(left, [(c, tr(l)) for c, l in self.AH_COLUMNS], (196, 84, 90, 84, 66, 62), stretch=("name",))
        frame.pack(fill="both", expand=True)
        for c, l in self.AH_COLUMNS:
            self.ah_tree.heading(c, command=lambda c=c: self._ah_sort_by(c))
        for i, color in enumerate(QUALITY_COLORS):
            self.ah_tree.tag_configure(f"q{i}", foreground=color)
        self.ah_tree.bind("<<TreeviewSelect>>", lambda e: self._ah_selected())
        self.ah_tree.bind("<Double-1>", lambda e: self.ah_wowhead())
        box = self._card_box(body, pady=(0, 0), side="left", fill="both", expand=True, padx=(14, 0), title=tr("Details"))
        tf, self.ah_text = self._text(box, height=7)
        tf.pack(fill="both", expand=True)
        self._tag_text(self.ah_text)
        self.ah_canvas = tk.Canvas(box, height=170, bg=THEME["panel"], highlightthickness=0)
        self.ah_canvas.pack(fill="x", pady=(8, 0))
        self.ah_canvas.bind("<Configure>", lambda e: self._ah_draw_chart())
        btns = ttk.Frame(box, style="Panel.TFrame")
        btns.pack(fill="x", pady=(10, 0))
        self.ah_wh_btn = ttk.Button(btns, text=tr("Auf Wowhead öffnen ↗"), style="Primary.TButton",
                                    command=self.ah_wowhead, state="disabled")
        self.ah_wh_btn.pack(side="left")
        self.ah_fav_btn = ttk.Button(btns, text=tr("☆ Favorit"), command=self.ah_fav, state="disabled")
        self.ah_fav_btn.pack(side="right")
        self._set_text(self.ah_text, parts=[(tr("Die Preise kommen aus Auctionator: Scanne im Spiel das Auktionshaus "
                                                 "(in Auctionator „Scan“ bzw. Vollscan). WoW speichert die Daten beim "
                                                 "Ausloggen und bei /reload, dann zeigt Leeroyzan sie hier."), "dim")])
        self.ah_text.configure(state="disabled")
        self._ah_filter_job = None

    def ah_open(self, force: bool = False):
        if not self.root_path or not self.flavor:
            self.ah_status_var.set(tr("Wähle zuerst den WoW-Ordner."))
            return
        files = auctionator_savedvariables(self.root_path, self.flavor)
        sig = (self.root_path, self.flavor, tuple((str(f), f.stat().st_mtime) for f in files))
        if not force and sig == self.ah_sig and self.ah_data is not None:
            return
        self.ah_sig = sig
        root, flavor = self.root_path, self.flavor
        self.ah_status_var.set(tr("Lese Auctionator …"))

        def work():
            data = load_auctionator(root, flavor)
            try:
                qdb = load_questdb(root, flavor)
            except (QuestDBError, OSError):
                qdb = None
            return data, qdb, load_reward_data(flavor)

        def done(res, err):
            if err:
                log.warning("Auctionator: %s", err)
                self.ah_status_var.set(tr("Auctionator konnte nicht gelesen werden: {0}", str(err)[:160]))
                return
            self.ah_data, self.ah_qdb, self.ah_cm = res
            realms = sorted(self.ah_data["realms"])
            self.ah_realm_cb.configure(values=realms)
            if self.ah_realm_var.get() not in realms:
                self.ah_realm_var.set(realms[0] if realms else "")
            if len(realms) > 1:
                self.ah_realm_cb.pack(side="right", anchor="s", padx=(12, 0))
            else:
                self.ah_realm_cb.pack_forget()
            self._ah_build_rows()
            if rewards_due(flavor) and flavor not in _REWARD_TRIED:     # Qualität und Kategorie aller Gegenstände
                token = self.cfg.get("github_token", "")
                self.run_quiet(lambda: ensure_reward_data(flavor, token),
                               lambda cm, e: (setattr(self, "ah_cm", cm or self.ah_cm), self._ah_build_rows()) if cm else None)
        self.run_quiet(work, done)

    def _ah_build_rows(self):
        data = self.ah_data
        if data is None:
            return
        if not data["realms"]:
            self.ah_rows = []
            self.ah_status_var.set(tr("Noch keine Auctionator-Daten. Scanne im Spiel das Auktionshaus und logge dich aus (oder /reload)."
                                      if not data["files"] else "Auctionator hat noch keine Preise gespeichert – scanne im Spiel das Auktionshaus."))
            self.ah_filter()
            return
        entries = data["realms"].get(self.ah_realm_var.get(), {})
        loc = "deDE" if cur_lang() == "de" else "enUS"
        owned = owned_counts(self.link_state)
        rows = []
        for key, e in entries.items():
            r = auction_row(key, e, data["vendor"])
            r.update(item_details(r["id"], self.ah_qdb, self.ah_cm, self.link_state, loc))
            r["owned"] = owned.get(r["id"], 0)
            rows.append(r)
        self.ah_rows = rows
        last = max((r["last"] for r in rows), default=0)
        worth = sum(r["owned"] * r["price"] for r in rows)
        status = tr("{0} Gegenstände · Stand {1}", fmt_int(len(rows)), auc_day_date(last).strftime(tr("%d.%m.%Y")) if last else "–")
        if worth:
            status += "  ·  " + tr("deine Bestände: {0}", fmt_money(worth))
        self.ah_status_var.set(status)
        self.ah_filter()

    def _ah_filter_later(self):
        if self._ah_filter_job:
            self.after_cancel(self._ah_filter_job)
        self._ah_filter_job = self.after(250, self.ah_filter)

    def ah_filter(self):
        self._ah_filter_job = None
        text = self.ah_var.get().strip().lower()
        cls_label = self.ah_cls_var.get()
        cls = next((k for k, v in ITEM_CLASSES.items() if tr(v) == cls_label), None)
        q = next((k for k, l in self.AH_QUALITIES if tr(l) == self.ah_q_var.get()), -1)
        try:
            lmin, lmax = int(self.ah_lmin.get() or 0), int(self.ah_lmax.get() or 0)
        except ValueError:
            lmin = lmax = 0
        out = []
        for r in self.ah_rows:
            if text and text not in r["name"].lower() and text != str(r["id"]):
                continue
            if cls is not None and r["cls"] != cls or q >= 0 and r["q"] != q:
                continue
            if lmin and r["req"] < lmin or lmax and r["req"] > lmax:
                continue
            if self.ah_deal.get() and not (r["change"] is not None and r["change"] <= -0.2):
                continue
            if self.ah_vendor.get() and not (r["vendor"] and r["price"] and r["price"] < r["vendor"]):
                continue
            if self.ah_owned.get() and not r["owned"]:
                continue
            out.append(r)
        col, desc = self.ah_sort
        known = [r for r in out if col == "name" or r[col] is not None]
        known.sort(key=(lambda r: r["name"].lower()) if col == "name" else (lambda r: r[col]), reverse=desc)
        out = known + [r for r in out if col != "name" and r[col] is None]      # ohne Wert ans Ende
        self.ah_shown = out
        self.ah_tree.delete(*self.ah_tree.get_children())
        for i, r in enumerate(out):
            tags = [f"q{min(max(r['q'], 0), 6)}"] + (["odd"] if i % 2 else [])
            ch = "" if r["change"] is None else f"{r['change'] * 100:+.0f} %"
            self.ah_tree.insert("", "end", iid=r["key"], tags=tags, values=(
                r["name"], fmt_money(r["price"]), fmt_money(int(r["avg"])) if r["avg"] and r["days"] > 1 else "–", ch,
                fmt_int(r["avail"]), auc_day_date(r["last"]).strftime("%d.%m") if r["last"] else "–"))
        self.ah_info_var.set(plural(len(out), "{0} Gegenstand", "{0} Gegenstände") if self.ah_rows else "")
        for c, l in self.AH_COLUMNS:
            arrow = (" ▼" if desc else " ▲") if c == col else ""
            self.ah_tree.heading(c, text=tr(l).upper() + arrow)
        if out and (self.ah_current is None or not self.ah_tree.exists(self.ah_current)):
            self.ah_tree.selection_set(out[0]["key"])
        elif self.ah_current and self.ah_tree.exists(self.ah_current):
            self.ah_tree.selection_set(self.ah_current)

    def _ah_sort_by(self, col):
        cur, desc = self.ah_sort
        self.ah_sort = (col, not desc if col == cur else col != "name")
        self.ah_filter()

    def _ah_selected(self):
        sel = self.ah_tree.selection()
        r = next((x for x in self.ah_shown if sel and x["key"] == sel[0]), None)
        self.ah_current = r["key"] if r else None
        for b in (self.ah_wh_btn, self.ah_fav_btn):
            b.configure(state="normal" if r else "disabled")
        if not r:
            return
        self.ah_fav_btn.configure(text=tr("★ Favorit entfernen") if self._wh_is_fav(self._ah_fav_entry(r)) else tr("☆ Favorit"))
        parts = [(r["name"] + "\n", f"q{min(max(r['q'], 0), 6)}")]
        meta = [tr(ITEM_CLASSES.get(r["cls"], "")) if r["cls"] in ITEM_CLASSES else ""]
        if r["req"] > 1:
            meta.append(tr("ab Stufe {0}", r["req"]))
        if r["ilvl"]:
            meta.append(tr("Gegenstandsstufe {0}", r["ilvl"]))
        meta.append(f"ID {r['id']}")
        parts.append(("  ·  ".join(m for m in meta if m) + "\n\n", "dim"))
        lows = [lo for _, lo, _, _ in r["hist"] if lo]
        rows = [(tr("Aktueller Mindestpreis"), fmt_money(r["price"])),
                (tr("Ø der letzten 14 Tage"), fmt_money(int(r["avg"])) if r["avg"] and r["days"] > 1 else "–"),
                (tr("Tiefster / höchster Tagespreis"), f"{fmt_money(min(lows))} / {fmt_money(max(hi for _, _, hi, _ in r['hist']))}" if lows else "–"),
                (tr("Zuletzt im Angebot"), tr("{0} Stück am {1}", fmt_int(r["avail"]), auc_day_date(r["last"]).strftime(tr("%d.%m.%Y"))) if r["last"] else "–"),
                (tr("Händler zahlt"), fmt_money(r["vendor"] or r["sell"]) if (r["vendor"] or r["sell"]) else "–")]
        if r["change"] is not None:
            rows.insert(2, (tr("Änderung zum Schnitt davor"), f"{r['change'] * 100:+.0f} %"))
        for k, v in rows:
            parts += [(k + ": ", "dim"), (v + "\n", "")]
        if r["owned"]:
            parts += [("\n" + tr("Deine Charaktere haben {0} Stück – Wert laut AH: {1}", fmt_int(r["owned"]),
                                 fmt_money(r["owned"] * r["price"])) + "\n", "hl")]
        if (r["vendor"] or r["sell"]) and r["price"] and r["price"] < (r["vendor"] or r["sell"]):
            parts += [("\n" + tr("Im Auktionshaus billiger als beim Händler – kaufen und beim Händler verkaufen lohnt sich.") + "\n", "ok")]
        if r["days"] < 2:
            parts += [("\n" + tr("Erst ein Scantag – den Verlauf gibt es nach weiteren Scans.") + "\n", "dim")]
        self._set_text(self.ah_text, parts=parts)
        self.ah_text.configure(state="disabled")
        self._ah_draw_chart()

    def _ah_draw_chart(self):
        cv = self.ah_canvas
        cv.delete("all")
        r = next((x for x in self.ah_shown if x["key"] == self.ah_current), None)
        w = max(cv.winfo_width(), 200)
        h = int(cv.cget("height"))
        if not r or not r["hist"]:
            return
        hist = r["hist"][-30:]
        vals = [v for _, lo, hi, _ in hist for v in (lo, hi) if v]
        if not vals:
            return
        lo_v, hi_v = min(vals), max(vals)
        if hi_v == lo_v:
            lo_v = 0
        x0, y0, x1, y1 = self._chart_frame(cv, 0, 0, w - 1, h - 1, tr("Preisverlauf (Tagestief und -hoch)"),
                                            (fmt_money(int(lo_v)), fmt_money(int(hi_v))))
        days = [d for d, _, _, _ in hist]
        dmin, dmax = days[0], days[-1]
        span = (dmax - dmin) or 1

        def xy(d, v):
            x = (x0 + x1) / 2 if dmin == dmax else x0 + (d - dmin) / span * (x1 - x0)
            return x, y1 - (v - lo_v) / (hi_v - lo_v) * (y1 - y0)
        t = THEME
        for v in (lo_v, hi_v):
            yy = xy(dmin, v)[1]
            cv.create_line(x0, yy, x1, yy, fill=t["line"], dash=(2, 3))
            cv.create_text(x0 - 6, yy, text=fmt_money(int(v)), anchor="e", fill=t["text_faint"], font=self.fonts["small"])
        for idx, color in ((2, "#6fb7ff"), (1, t["gold"])):
            pts = [c for d, *vals_ in hist for c in xy(d, vals_[idx - 1])]
            if len(pts) >= 4:
                cv.create_line(*pts, fill=color, width=2)
            for j in range(0, len(pts), 2):
                cv.create_oval(pts[j] - 2.5, pts[j + 1] - 2.5, pts[j] + 2.5, pts[j + 1] + 2.5, fill=color, outline="")
        cv.create_text(x0, y1 + 12, text=auc_day_date(dmin).strftime("%d.%m"), anchor="w", fill=t["text_faint"], font=self.fonts["small"])
        cv.create_text(x1, y1 + 12, text=auc_day_date(dmax).strftime("%d.%m"), anchor="e", fill=t["text_faint"], font=self.fonts["small"])
        cv.create_text((x0 + x1) / 2, y1 + 12, text=tr("gold = Tagestief, blau = Tageshoch"), fill=t["text_faint"], font=self.fonts["small"])

    def _ah_row(self):
        return next((x for x in self.ah_shown if x["key"] == self.ah_current), None)

    def _ah_fav_entry(self, r) -> dict:
        ed = self.cfg.get("wowhead_edition", "classic")
        return {"kind": "item", "kind_name": "Gegenstand", "id": r["id"], "name": r["name"], "quality": r["q"],
                "edition": ed, "url": wowhead_url(ed, "item", r["id"])}

    def ah_wowhead(self):
        r = self._ah_row()
        if r:
            self.open_link(wowhead_url(self.cfg.get("wowhead_edition", "classic"), "item", r["id"]))

    def ah_fav(self):
        r = self._ah_row()
        if not r:
            return
        e = self._ah_fav_entry(r)
        favs = self._wh_favs()
        if self._wh_is_fav(e):
            favs[:] = [f for f in favs if self._wh_key(f) != self._wh_key(e)]
        else:
            favs.insert(0, e)
        save_config(self.cfg)
        self._wh_mode_buttons()
        self.ah_fav_btn.configure(text=tr("★ Favorit entfernen") if self._wh_is_fav(e) else tr("☆ Favorit"))
