"""Aufbau des Fensters: Seitenleiste, Seiten, Kacheln. (Mixin, wird in App eingemischt)."""
from __future__ import annotations

import re

from tkinter import ttk
import tkinter as tk

from ..constants import APP_VERSION, HEADER_LINKS
from ..i18n import tr
from ..sharing import LIST_PART_SIZES
from ..linkdata import empty_link_state
from .theme import THEME, UPD_MARK, apply_theme, draw_emblem, make_icon, spaced


class LayoutMixin:
    """Teil des Hauptfensters (wird in App eingemischt)."""

    # ----- Aufbau ---------------------------------------------------------- #
    PAGES = [("addons", "Meine Addons"), ("discover", "Entdecken"), ("updates", "Updates"),
             ("sessions", "Leeroylink"), ("characters", "Charaktere"), ("quests", "Quests"), ("auction", "Auktionshaus"), ("wowhead", "Wowhead"), ("diagnose", "Diagnose"),
             ("share", "Teilen"), ("sets", "Sets & Backups"), ("settings", "Einstellungen")]
    NAV_GROUPS = [("Addons", ("addons", "discover", "updates")),
                  ("Im Spiel", ("sessions", "characters", "quests", "auction", "wowhead")),
                  ("Werkzeuge", ("diagnose", "share", "sets", "settings"))]
    TREE_SHOWN = ("status", "name", "version", "source", "update")

    def _build_ui(self):
        self.fonts = apply_theme(self)
        try:
            self._icon = make_icon(self)
            self.iconphoto(True, self._icon)
        except tk.TclError:
            pass
        self._init_vars()
        self._build_layout()

    def _init_vars(self):
        self.root_var = tk.StringVar(value=self.cfg["wow_root"])
        self.flavor_var = tk.StringVar(value=self.cfg["flavor"])
        self.set_var = tk.StringVar()
        self.set_name_var = tk.StringVar()
        self.auto_backup_var = tk.BooleanVar(value=bool(self.cfg.get("auto_backup")))
        self.check_start_var = tk.BooleanVar(value=bool(self.cfg.get("check_on_start")))
        self.self_update_var = tk.StringVar(value=tr(self.UPDATE_MODES.get(self.cfg.get("self_update_mode"),
                                                                            self.UPDATE_MODES["auto"])))
        self.count_var = tk.StringVar()
        self.upd_count_var = tk.StringVar()
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *a: self.render())
        self.group_var = tk.BooleanVar(value=bool(self.cfg.get("group_addons", True)))
        self.status_var = tk.StringVar(value=tr("Bereit."))
        self.game_info_var = tk.StringVar()
        self.view = self.cfg.get("addon_view", "list") if self.cfg.get("addon_view") in ("list", "cards") else "list"
        # Entdecken
        self.cf_link_var = tk.StringVar()
        self.gh_var = tk.StringVar()
        self.zip_var = tk.StringVar()
        self.cf_q = tk.StringVar()
        self.cf_sort = tk.StringVar(value=tr("Beliebtheit"))
        self.cf_fit = tk.BooleanVar(value=True)
        self.cf_count = tk.StringVar()
        self.cf_file_var = tk.StringVar()
        # Teilen
        self.share_scope = tk.StringVar(value="all")
        self.share_exact = tk.BooleanVar(value=True)
        self.share_sets = tk.BooleanVar(value=True)
        self.share_size = tk.StringVar(value=tr(next(iter(LIST_PART_SIZES))))
        self.share_info = tk.StringVar()
        self.import_preview = tk.StringVar()
        self.recv_file_var = tk.StringVar()
        # Sitzungen
        self.sess_char_var = tk.StringVar()
        self.sess_period_var = tk.StringVar()
        self.sess_period = "all"
        self.link_state_var = tk.StringVar()
        self.sessions_all: list[dict] = []
        self.sessions_shown: list[dict] = []
        self._sess_parts: list = []
        self._sess_rows: list[dict] = []
        self._sess_sig = None
        self._sess_pending: dict[str, dict] = {}
        self._sess_importing = False
        self._link_hint_shown = False
        self.link_state = empty_link_state()
        self._link_write_job = None
        self._quest_write_running = False
        self.diag_state_var = tk.StringVar()
        self._diag_rows: list = []
        self.chars_total_var = tk.StringVar()
        self._chars_rows: list = []
        self.shop_char_var, self.shop_prof_var = tk.StringVar(), tk.StringVar()
        self.shop_recipe_var, self.shop_filter_var = tk.StringVar(), tk.StringVar()
        self.shop_qty_var = tk.StringVar(value="1")
        self.find_var = tk.StringVar()
        self.sess_view = self.cfg.get("sess_view", "report") if self.cfg.get("sess_view") in ("report", "charts", "analysis") else "report"
        self.discord_var = tk.StringVar(value=self.cfg.get("discord_webhook", ""))
        self.discord_auto_var = tk.BooleanVar(value=bool(self.cfg.get("discord_auto")))
        self.sort_col, self.sort_rev = "name", False
        self.open_groups: set[str] = set()
        self.group_members: dict[str, list[str]] = {}

    def _build_layout(self):
        t = THEME
        self._page_gen: dict[str, int] = {}     # neu aufgebaute Seiten müssen erst einmal gefüllt werden
        self.tree = None
        self.cards_inner = None
        self.detail = None
        self.nav_buttons: dict[str, ttk.Button] = {}
        self.pages: dict[str, ttk.Frame] = {}
        self.banners: dict[str, tk.Frame] = {}
        self.asking = 0
        self.ui = ttk.Frame(self)
        self.ui.pack(fill="both", expand=True)
        root = self.ui
        self._w_statusbar(root)

        side = ttk.Frame(root, style="Side.TFrame", width=238, padding=(14, 18, 14, 16))
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        tk.Frame(root, width=1, bg=t["line"]).pack(side="left", fill="y")
        brand = tk.Canvas(side, height=64, bg=t["header"], highlightthickness=0)
        brand.pack(fill="x")
        draw_emblem(brand, 0, 4, 54)
        import tkinter.font as tkfont
        lf = tkfont.Font(root=self, font=self.fonts["label"])
        tagline = next((x for x in (spaced(tr("Addon Manager")), tr("Addon Manager").upper(), spaced("Addon Manager"))
                        if lf.measure(x) <= 144), "ADDON MANAGER")
        brand.create_text(64, 20, anchor="w", fill=t["gold"], font=self.fonts["label"], text=tagline)
        brand.create_text(63, 44, anchor="w", fill=t["gold"], font=self.fonts["side"], text="LEEROYZAN")
        self._nav_after_brand = ttk.Label(side, text=spaced(tr("Spielversion")), style="SideKicker.TLabel")
        self._nav_after_brand.pack(anchor="w", pady=(18, 6))
        self._nav_flavor = self._w_flavor(side, width=22)
        self._nav_flavor.pack(fill="x")
        bottom = ttk.Frame(side, style="Side.TFrame")
        bottom.pack(side="bottom", fill="x")
        links = tk.Frame(bottom, bg=t["header"])
        links.pack(pady=(0, 10))
        self._nav_links = links
        for i, (text, url) in enumerate(HEADER_LINKS):
            if i:
                tk.Label(links, text="·", bg=t["header"], fg=t["text_faint"], font=self.fonts["small"]).pack(side="left", padx=1)
            lbl = tk.Label(links, text=tr(text), bg=t["header"], fg=t["text_dim"], font=self.fonts["small"], cursor="hand2")
            lbl.pack(side="left")
            lbl.bind("<Button-1>", lambda e, u=url: self.open_header_link(u))
            lbl.bind("<Enter>", lambda e, w=lbl: w.configure(fg=t["gold"]))
            lbl.bind("<Leave>", lambda e, w=lbl: w.configure(fg=t["text_dim"]))
        tk.Frame(bottom, height=1, bg=t["line"]).pack(fill="x", pady=(0, 12))
        self._nav_play = self._w_play(bottom, style="BigPlay.TButton", text=tr("▶   SPIELEN"))
        self._nav_play.pack(fill="x")
        self._nav_info = ttk.Label(bottom, textvariable=self.game_info_var, style="Side.TLabel", anchor="center",
                                   justify="center", wraplength=200)
        self._nav_info.pack(fill="x", pady=(8, 0))
        self._nav_version = ttk.Label(bottom, text=tr("Leeroyzan v{0}", APP_VERSION), style="Side.TLabel", anchor="center")
        self._nav_version.pack(fill="x")

        nav = ttk.Frame(side, style="Side.TFrame")
        nav.pack(fill="x", pady=(14, 0))
        labels = dict(self.PAGES)
        self._nav_heads = []
        for i, (title, keys) in enumerate(self.NAV_GROUPS):
            if i:                          # Gruppen durch eine feine Linie trennen (Titel kosten zu viel Höhe)
                sep = tk.Frame(nav, height=1, bg=t["line"])
                sep.pack(fill="x", padx=14, pady=6)
                self._nav_heads.append(sep)
            for key in keys:
                b = ttk.Button(nav, text=tr(labels[key]), style="Nav.TButton", cursor="hand2",
                               command=lambda k=key: self.show_page(k))
                b.pack(fill="x", pady=1)
                self.nav_buttons[key] = b
        self._nav_brand, self._nav_frame, self._nav_bottom = brand, nav, bottom
        self._nav_level, self._nav_need = 0, {}
        for name in ("Nav.TButton", "NavActive.TButton"):      # nach einem Neuaufbau wieder normal beginnen
            ttk.Style(self).configure(name, padding=self.NAV_PAD[False])
        side.bind("<Configure>", lambda e: self.after_idle(lambda h=e.height: self._fit_sidebar(h)), add="+")

        main = ttk.Frame(root)
        main.pack(side="left", fill="both", expand=True)
        self.notice_host = ttk.Frame(main, padding=(26, 14, 26, 0))
        self.notice_host.bind("<Configure>", lambda e: self._wrap_banners(e.width))
        self.page_host = ttk.Frame(main)
        self.page_host.pack(fill="both", expand=True)
        self._page_addons()
        self._page_discover()
        self._page_updates()
        self._page_sessions()
        self._page_characters()
        self._page_quests()
        self._page_auction()
        self._page_wowhead()
        self._page_diagnose()
        self._page_share()
        self._page_sets()
        self._page_settings()
        self._fit_wraps(self.page_host)
        self.show_page(self.cfg.get("last_page", "addons"))

    def _fit_sidebar(self, height: int):
        """Passt die Seitenleiste an die Fensterhöhe an, damit alle Seiten und „Spielen“ sichtbar bleiben:
        Stufe 1 ohne Links und Spielinfo, Stufe 2 zusätzlich ohne Logo und mit engeren Knöpfen. Gemessen wird
        echt (Platzbedarf gegen Platz) – das klappt auch mit 125 % Windows-Skalierung."""
        try:
            if not self._nav_frame.winfo_exists() or self._nav_frame.winfo_height() <= 1:
                return
            level = self._nav_level
            if level > 0 and height >= self._nav_need.get(level - 1, 1 << 30):
                self._set_sidebar_level(level - 1)
                self.after_idle(lambda: self._fit_sidebar(height))
                return
            overflow = self._nav_frame.winfo_reqheight() - self._nav_frame.winfo_height()
            if overflow > 0 and level < 2:
                self._nav_need[level] = height + overflow + 4
                self._set_sidebar_level(level + 1)
                self.after_idle(lambda: self._fit_sidebar(height))
        except tk.TclError:
            pass

    NAV_PAD = {False: (14, 4), True: (14, 1)}

    def _set_sidebar_level(self, level: int):
        self._nav_level = level
        compact = level >= 2
        style = ttk.Style(self)
        for name in ("Nav.TButton", "NavActive.TButton"):
            style.configure(name, padding=self.NAV_PAD[compact])
        for sep in self._nav_heads:
            sep.pack_configure(pady=2 if compact else 6)
        for w in (self._nav_brand, self._nav_after_brand, self._nav_links, self._nav_info, self._nav_version):
            w.pack_forget()
        if not compact:
            self._nav_brand.pack(fill="x", before=self._nav_flavor)
            self._nav_after_brand.pack(anchor="w", pady=(18, 6), before=self._nav_flavor)
            self._nav_version.pack(fill="x", after=self._nav_play)
        if level == 0:
            self._nav_links.pack(pady=(0, 10), before=self._nav_bottom.pack_slaves()[0])
            self._nav_info.pack(fill="x", pady=(8, 0), after=self._nav_play)
        self._nav_frame.pack_configure(pady=(8, 0) if compact else (14, 0))

    def _fit_wraps(self, widget):
        """Längere Fließtexte an die tatsächliche Kastenbreite anpassen – sonst werden lange
        Übersetzungen am rechten Rand abgeschnitten."""
        for child in widget.winfo_children():
            if isinstance(child, ttk.Label):
                try:
                    wrap = int(str(child.cget("wraplength")) or 0)
                except (ValueError, tk.TclError):
                    wrap = 0
                if wrap >= 300:
                    child.master.bind("<Configure>", lambda e, lbl=child: lbl.winfo_exists() and lbl.configure(
                        wraplength=max(200, e.width - 40)), add="+")
            self._fit_wraps(child)

    # ----- Kacheln ------------------------------------------------------------ #
    def _relayout_cards(self):
        if self.cards_inner is not None and getattr(self, "_card_widgets", None):
            self._place_cards()

    def render_cards(self, entries):
        """Zeichnet die Addon-Kacheln."""
        t = THEME
        for w in self.cards_inner.winfo_children():
            w.destroy()
        self._card_widgets = []
        if not entries:
            ttk.Label(self.cards_inner, text=tr("Keine Addons gefunden."), style="Body.TLabel").grid(
                row=0, column=0, sticky="w", pady=20)
            return
        sel = {a.name for a in self.selected_addons()}
        for title, members, vals, tags in entries:
            chosen = bool(sel) and {m.name for m in members} <= sel
            outer = tk.Frame(self.cards_inner, bg=t["gold"] if chosen else t["border_soft"])
            card = ttk.Frame(outer, style="Card.TFrame", padding=(16, 12))
            card.pack(fill="both", expand=True, padx=1, pady=1)
            top = ttk.Frame(card, style="Card.TFrame")
            top.pack(fill="x")
            on = all(m.enabled for m in members)
            ttk.Button(top, text=tr("AN") if on else tr("AUS"), style="ToggleOn.TButton" if on else "ToggleOff.TButton",
                       cursor="hand2", command=lambda ms=members: self.toggle_selected(ms)).pack(side="right")
            name = ttk.Label(top, text=title, style="CardTitle.TLabel", cursor="hand2")
            name.pack(side="left")
            folders = tr("{0} Ordner", len(members)) if len(members) > 1 else tr("1 Ordner")
            if len(members) > 1:
                rest = [m.name for m in members if m.name != title][:3]
                folders += " · " + ", ".join(rest) + (" …" if len(members) - 1 > 3 else "")
            ttk.Label(card, text=folders, style="CardSub.TLabel").pack(anchor="w", pady=(2, 8))
            chips = tk.Frame(card, bg=t["row_a"])
            chips.pack(fill="x")
            status_color = (t["text_faint"] if "disabled" in tags else t["warn"] if "warn" in tags
                            else t["old"] if "old" in tags else t["ok"])
            upd_chip = ""
            if "update" in tags:
                v = re.search(r"\d+(?:\.\d+)+", str(vals[6]).replace("  (Browser)", ""))
                upd_chip = f"{UPD_MARK} Update {v.group(0)}" if v else tr("{0} Update verfügbar", UPD_MARK)
            for text, fg, bg in ((f"v{vals[3]}" if vals[3] else tr("ohne Version"), t["text"], t["pill"]),
                                 (vals[5].split(":")[0] or tr("manuell"), t["text_dim"], t["pill"]),
                                 (upd_chip or vals[0].replace(f"{UPD_MARK} ", ""),
                                  t["update"] if upd_chip else status_color,
                                  t["update_bg"] if upd_chip else t["pill"])):
                tk.Label(chips, text=text, fg=fg, bg=bg, font=self.fonts["small"], padx=9, pady=2).pack(
                    side="left", padx=(0, 6))
            tk.Frame(card, height=1, bg=t["line"]).pack(fill="x", pady=(10, 4))
            links = ttk.Frame(card, style="Card.TFrame")
            links.pack(fill="x")
            acts = []
            if upd_chip:
                acts.append((tr("{0} Aktualisieren", UPD_MARK), lambda ms=members: self.update_selected(ms)))
            acts += [(tr("Details"), lambda ms=members: self.select_addons(ms)),
                     (tr("Ordner"), lambda ms=members: self.open_folder(ms)),
                     (tr("Entfernen"), lambda ms=members: self.remove_selected(list(ms)))]
            for text, cb in acts:
                ttk.Button(links, text=text, style="CardLink.TButton", cursor="hand2", width=len(text),
                           command=cb).pack(side="left", padx=(0, 10))
            for w in (card, name):
                w.bind("<Button-1>", lambda e, ms=members: self.select_addons(ms))
            self._card_widgets.append(outer)
        self._place_cards()

    def select_addons(self, members):
        """Markiert Addons (auch aus der Kachel-Ansicht) und zeigt sie im Detailbereich."""
        if self.tree is None:
            return
        names = [m.name for m in members]
        iids = [n for n in names if self.tree.exists(n)]
        gid = next((g for g, ms in self.group_members.items() if set(ms) == set(names)), None)
        if gid and self.tree.exists(gid):
            iids = [gid]
        if iids:
            self.tree.selection_set(iids)
            self.tree.see(iids[0])
        self.update_detail()
        if self.view == "cards":
            self.render()

    def _group_main(self, members):
        names = {m.name for m in members}
        return next((a for a in members if any(o.startswith(a.name) for o in names if o != a.name)), members[0])

    def _place_cards(self):
        width = max(self.cards_canvas.winfo_width(), 600)
        cols = max(1, min(4, width // 320))
        for c in range(4):
            self.cards_inner.columnconfigure(c, weight=1 if c < cols else 0, uniform="card" if c < cols else "")
        for i, w in enumerate(self._card_widgets):
            w.grid(row=i // cols, column=i % cols, sticky="nsew",
                   padx=(0 if i % cols == 0 else 12, 0), pady=(0, 12))

    def update_stats(self):
        """(Früher Kacheln des Launchers – die Zahlen stehen jetzt in der Seitenleiste.)"""
