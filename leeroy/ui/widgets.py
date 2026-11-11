"""Hauptfenster: WidgetsMixin."""
from __future__ import annotations


from tkinter import ttk
import tkinter as tk

from ..i18n import tr
from ..config import LOG_FILE
from ..files import open_in_explorer
from ..sessions import link_signature
from .theme import THEME, spaced


class WidgetsMixin:
    """Teil des Hauptfensters (wird in App eingemischt)."""

    # ----- Gemeinsame Bausteine ------------------------------------------- #
    def _w_path(self, parent, frame_style="Panel.TFrame", label_style="Panel.TLabel", with_version=False):
        row = ttk.Frame(parent, style=frame_style)
        ttk.Label(row, text=tr("WoW-Ordner"), style=label_style).pack(side="left", padx=(0, 8))
        entry = ttk.Entry(row, textvariable=self.root_var, width=50)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda e: self._load_flavors())
        entry.bind("<FocusOut>", lambda e: self._load_flavors()
                   if self.root_path != self.cfg.get("wow_root") else None)
        ttk.Button(row, text=tr("Durchsuchen…"), command=self.browse_root).pack(side="left", padx=(8, 0))
        return row

    def _w_flavor(self, parent, width=24):
        cb = ttk.Combobox(parent, textvariable=self.flavor_var, state="readonly", width=width)
        cb.bind("<<ComboboxSelected>>", lambda e: self.on_flavor_change())
        self.flavor_cb = cb
        return cb

    def _w_check(self, parent, text, var, panel=True):
        return ttk.Checkbutton(parent, text=text, variable=var, command=self.save_options,
                               style="Wrap.TCheckbutton" if panel else "Plain.TCheckbutton")

    def _w_update_mode(self, parent, label_style="Panel.TLabel"):
        row = ttk.Frame(parent, style="Panel.TFrame" if label_style == "Panel.TLabel" else "TFrame")
        ttk.Label(row, text=tr("Leeroyzan-Updates"), style=label_style).pack(side="left", padx=(0, 8))
        cb = ttk.Combobox(row, textvariable=self.self_update_var, state="readonly", width=21,
                          values=[tr(v) for v in self.UPDATE_MODES.values()])
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda e: self.save_options())
        return row

    def _w_filter(self, parent, width=24):
        return ttk.Entry(parent, textvariable=self.filter_var, width=width)

    def _w_play(self, parent, style="Play.TButton", text=None):
        text = text or tr("▶  Spielen")
        self.play_btn = ttk.Button(parent, text=text, style=style, cursor="hand2", command=self.start_wow)
        return self.play_btn

    def _w_statusbar(self, parent):
        t = THEME
        ttk.Label(parent, textvariable=self.status_var, style="Status.TLabel",
                  anchor="w").pack(fill="x", side="bottom")
        tk.Frame(parent, height=1, bg=t["line"]).pack(fill="x", side="bottom")

    def _menu(self, parent, items) -> tk.Menu:
        t = THEME
        m = tk.Menu(parent, tearoff=False, bg=t["panel"], fg=t["text"], activebackground=t["active"],
                    activeforeground=t["gold"], bd=0, relief="flat", font=self.fonts["base"])
        for item in items:
            if item is None:
                m.add_separator()
            else:
                m.add_command(label="  " + item[0] + "  ", command=item[1])
        return m

    def _framed(self, parent):
        """Feiner Rahmen um Listen."""
        return tk.Frame(parent, bg=THEME["border_soft"], highlightthickness=0)

    def _list(self, parent, columns, widths, stretch=(), height=4, select="browse"):
        """Treeview mit Theme und Scrollbar (für Suchergebnisse, Updates, Sets, Backups)."""
        t = THEME
        frame = self._framed(parent)
        tv = ttk.Treeview(frame, columns=[c for c, _ in columns], show="headings", selectmode=select, height=height)
        import tkinter.font as tkfont
        hf = tkfont.Font(root=self, font=ttk.Style(self).lookup("Treeview.Heading", "font") or "TkHeadingFont")
        for (cid, label), w in zip(columns, widths):
            head = spaced(label) if label else ""
            if head and hf.measure(head) > w - 14:      # zu breit: ohne Sperrung
                head = label.upper()
            tv.heading(cid, text=head, anchor="w")
            tv.column(cid, width=w, anchor="w", stretch=cid in stretch)
        tv.tag_configure("odd", background=t["row_b"])
        tv.tag_configure("dim", foreground=t["text_faint"])
        tv.tag_configure("other", foreground=t["text_dim"])
        tv.tag_configure("update", foreground=t["update"])
        sb = ttk.Scrollbar(frame, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=sb.set)
        tv.pack(side="left", fill="both", expand=True, padx=(1, 0), pady=1)
        sb.pack(side="right", fill="y", padx=(0, 1), pady=1)
        return frame, tv

    def _text(self, parent, height=4, readonly=True):
        t = THEME
        frame = self._framed(parent)
        txt = tk.Text(frame, wrap="word", height=height, bg=t["row_a"], fg=t["text"], insertbackground=t["gold"],
                      highlightthickness=0, borderwidth=0, padx=10, pady=8, font=self.fonts["base"])
        txt.tag_configure("head", foreground=t["gold"], font=self.fonts["bold"], spacing1=4, spacing3=4)
        txt.tag_configure("dim", foreground=t["text_dim"])
        sb = ttk.Scrollbar(frame, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        txt.pack(side="left", fill="both", expand=True, padx=(1, 0), pady=1)
        sb.pack(side="right", fill="y", padx=(0, 1), pady=1)
        if readonly:
            txt.configure(state="disabled")
        return frame, txt

    @staticmethod
    def _set_text(txt: tk.Text, content: str = "", parts=None):
        txt.configure(state="normal")
        txt.delete("1.0", "end")
        for text, tag in (parts or [(content, "")]):
            txt.insert("end", text, tag)
        txt.configure(state="disabled")

    def _w_tree(self, parent):
        t = THEME
        body = self._framed(parent)
        self.tree = ttk.Treeview(body, columns=self.COLUMNS, displaycolumns=self.TREE_SHOWN,
                                 show="tree headings", selectmode="extended")
        self.tree.column("#0", width=30, minwidth=30, stretch=False)
        self.tree.heading("#0", text="")
        self.tree.bind("<<TreeviewOpen>>", lambda e: self._remember_open(True))
        self.tree.bind("<<TreeviewClose>>", lambda e: self._remember_open(False))
        widths = {"status": 95, "name": 170, "title": 200, "version": 95,
                  "interface": 90, "source": 140, "update": 160}
        for c in self.COLUMNS:
            self.tree.heading(c, text=spaced(tr(self.HEADINGS[c])), anchor="w",
                              command=lambda c=c: self.sort_by(c))
            self.tree.column(c, width=widths[c], anchor="w", stretch=c in ("name", "update"))
        vsb = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True, padx=(1, 0), pady=1)
        vsb.pack(side="right", fill="y", padx=(0, 1), pady=1)
        self.tree.tag_configure("odd", background=t["row_b"])
        self.tree.tag_configure("group", font=self.fonts["bold"])
        self.tree.tag_configure("child", foreground=t["text_dim"])
        self.tree.tag_configure("disabled", foreground=t["text_faint"])
        self.tree.tag_configure("warn", foreground=t["warn"])
        self.tree.tag_configure("old", foreground=t["old"])
        self.tree.tag_configure("update", foreground=t["update"])
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<Button-3>", self._on_right_click)
        self.tree.bind("<Control-a>", lambda e: (self.select_all(force=True), "break")[1])
        self.tree.bind("<Control-A>", lambda e: (self.select_all(force=True), "break")[1])
        self.tree.bind("<Delete>", lambda e: self.remove_selected())
        self.tree.bind("<<TreeviewSelect>>", lambda e: (self.update_detail(), self._hint_update_link()))
        return body

    def _scroll_area(self, parent, bg):
        """Scrollbarer Bereich (Canvas + Frame) mit Mausrad-Unterstützung."""
        wrap = tk.Frame(parent, bg=bg)
        canvas = tk.Canvas(wrap, bg=bg, highlightthickness=0)
        vsb = ttk.Scrollbar(wrap, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg=bg)
        win = canvas.create_window(0, 0, window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: (canvas.itemconfigure(win, width=e.width), self._relayout_cards()))
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        def wheel(e):
            delta = -1 if (getattr(e, "delta", 0) > 0 or getattr(e, "num", 0) == 4) else 1
            canvas.yview_scroll(delta * 2, "units")
        canvas.bind("<Enter>", lambda e: (canvas.bind_all("<MouseWheel>", wheel),
                                          canvas.bind_all("<Button-4>", wheel),
                                          canvas.bind_all("<Button-5>", wheel)))
        canvas.bind("<Leave>", lambda e: (canvas.unbind_all("<MouseWheel>"),
                                          canvas.unbind_all("<Button-4>"),
                                          canvas.unbind_all("<Button-5>")))
        self.cards_canvas = canvas
        return wrap, inner

    def update_game_info(self):
        text = tr("Spielversion {0}  ·  Interface {1}", self.game[0], self.game[1]) if self.game else tr("Spielversion unbekannt")
        self.game_info_var.set(text)

    def open_log(self):
        if LOG_FILE.exists():
            open_in_explorer(LOG_FILE)

    # ----- Seiten ------------------------------------------------------------ #
    def _page(self, key, kicker, title, text=""):
        page = ttk.Frame(self.page_host, padding=(26, 18, 26, 14))
        self.pages[key] = page
        head = ttk.Frame(page)
        head.pack(fill="x")
        left = ttk.Frame(head)
        left.pack(side="left", fill="x", expand=True)
        ttk.Label(left, text=spaced(kicker), style="Kicker.TLabel").pack(anchor="w")
        ttk.Label(left, text=title, style="PageTitle.TLabel").pack(anchor="w", pady=(2, 0))
        if text:
            ttk.Label(page, text=text, style="Body.TLabel", wraplength=900, justify="left").pack(
                anchor="w", pady=(6, 0))
        return page, head

    def _card_box(self, parent, pady=(16, 0), fill="x", expand=False, title="", side=None, padx=(0, 0)):
        t = THEME
        box = tk.Frame(parent, bg=t["panel"], highlightthickness=1,
                       highlightbackground=t["border_soft"], highlightcolor=t["border_soft"])
        if side:
            box.pack(side=side, fill=fill, expand=expand, pady=pady, padx=padx)
        else:
            box.pack(fill=fill, expand=expand, pady=pady, padx=padx)
        inner = ttk.Frame(box, style="Panel.TFrame", padding=(16, 14))
        inner.pack(fill="both", expand=True)
        if title:
            ttk.Label(inner, text=spaced(title), style="CardKicker.TLabel").pack(anchor="w", pady=(0, 10))
        return inner

    def show_page(self, key):
        if key not in self.pages:
            key = "addons"
        for k, p in self.pages.items():
            p.pack_forget()
            self.nav_buttons[k].configure(style="NavActive.TButton" if k == key else "Nav.TButton")
        self.pages[key].pack(fill="both", expand=True)
        self.cfg["last_page"] = key
        self.current_page = key
        if key == "sets":
            self.refresh_sets_overview()
            self.refresh_backup_list()
        elif key == "discover":
            self.refresh_zip_choices()
            if not self.cf_results and self.cf_key() and not self.cf_searching:
                self.cf_search()
        elif key == "share":
            self.rebuild_share()
            self.refresh_recv_files()
        elif key == "updates":
            self.refresh_updates_page()
        elif key == "diagnose":
            self.refresh_diagnose()
        elif key == "characters":
            self.refresh_characters()
        elif key == "settings":
            self.refresh_rollback_usage()
        elif key == "wowhead":
            self.after(50, self.wh_entry.focus_set)
        elif key == "quests":
            self.qdb_open()
        elif key == "auction":
            self.ah_open()
        elif key == "sessions":
            self.refresh_sessions_page()
            if not self._sess_importing and self.root_path and self.flavor \
                    and link_signature(self.root_path, self.flavor) != self._sess_sig:
                self.import_sessions()
