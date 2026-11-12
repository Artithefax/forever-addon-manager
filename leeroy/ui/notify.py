"""Hinweisleiste oben im Fenster statt Pop-up-Fenstern, Fragen und Eingaben. (Mixin, wird in App eingemischt)."""
from __future__ import annotations

import time

from tkinter import ttk
import tkinter as tk

from ..i18n import tr
from .theme import THEME, UPD_MARK


class NotifyMixin:
    """Teil des Hauptfensters (wird in App eingemischt)."""

    # ----- Hinweisleiste statt Pop-up-Fenstern ------------------------------ #
    BANNER_STYLE = {   # Akzent, Hintergrund, Symbol
        "info": ("gold", "panel", "ℹ"), "ok": ("ok", "panel", "✓"), "warn": ("warn", "panel", "⚠"),
        "error": ("warn", "panel", "✕"), "ask": ("gold", "active", "?"), "update": ("update", "update_bg", UPD_MARK),
    }

    def notify(self, text: str, kind: str = "info", actions=(), key: str = "", timeout: int = 0,
               closable: bool = True, extra=None) -> str:
        """Zeigt eine Meldung oben im Fenster – mit optionalen Knöpfen. Ersetzt Pop-up-Fenster."""
        t = THEME
        # Erfolgs- und Info-Meldungen teilen sich einen Platz, damit sich nichts stapelt
        if kind in ("ok", "info"):
            key = "toast"          # Erfolgs-/Info-Meldungen teilen sich einen Platz, damit sich nichts stapelt
        key = key or f"{kind}:{text[:80]}"          # gleiche Meldung ersetzt sich selbst
        self.dismiss(key)
        others = [k for k in self.banners if not k.startswith("ask")]
        while len(others) >= 2:
            self.dismiss(others.pop(0))
        accent, bg, icon = self.BANNER_STYLE.get(kind, self.BANNER_STYLE["info"])
        accent, bg = t[accent], t[bg]
        frame = tk.Frame(self.notice_host, bg=bg, highlightthickness=1, highlightbackground=t["border_soft"])
        tk.Frame(frame, width=4, bg=accent).pack(side="left", fill="y")
        body = tk.Frame(frame, bg=bg)
        body.pack(side="left", fill="both", expand=True, padx=(12, 8), pady=10)
        top = tk.Frame(body, bg=bg)
        top.pack(fill="x")
        tk.Label(top, text=icon, bg=bg, fg=accent, font=self.fonts["bold"]).pack(side="left", anchor="n", padx=(0, 10))
        lbl = tk.Label(top, text=text, bg=bg, fg=t["text"], font=self.fonts["base"], justify="left", anchor="w",
                       wraplength=max(300, self.notice_host.winfo_width() - 380))
        lbl.pack(side="left", fill="x", expand=True)
        frame._wrap_label = lbl
        if closable:
            x = tk.Label(top, text="✕", bg=bg, fg=t["text_faint"], cursor="hand2", font=self.fonts["base"])
            x.pack(side="right", anchor="n", padx=(8, 0))
            x.bind("<Button-1>", lambda e: self.dismiss(key))
        btns = tk.Frame(top, bg=bg)
        btns.pack(side="right", anchor="n")
        frame._buttons = []
        for i, (label, cmd) in enumerate(actions):
            def run(c=cmd):
                self.dismiss(key)
                c()
            b = ttk.Button(btns, text=label, style="Primary.TButton" if i == 0 else "TButton", command=run)
            b.pack(side="left", padx=(8, 0))
            frame._buttons.append(b)
        if extra:
            extra(body, bg)
        frame.pack(fill="x", pady=(0, 8))
        self.banners[key] = frame
        if not self.notice_host.winfo_ismapped():
            self.notice_host.pack(fill="x", before=self.page_host)
        if timeout:
            self.after(timeout, lambda: self.dismiss(key) if self.banners.get(key) is frame else None)
        return key

    def dismiss(self, key: str):
        frame = self.banners.pop(key, None)
        if frame is not None:
            try:
                frame.destroy()
            except tk.TclError:
                pass
        if not self.banners and self.notice_host.winfo_ismapped():
            self.notice_host.pack_forget()

    def _wrap_banners(self, width: int):
        for f in self.banners.values():
            try:
                f._wrap_label.configure(wraplength=max(300, width - 380))
            except (tk.TclError, AttributeError):
                pass

    def info(self, text: str, **kw):
        self.set_status(text.split("\n")[0])
        self.notify(text, "info", timeout=12000)

    def warn(self, text: str, **kw):
        self.notify(text, "warn")

    def error(self, text: str, **kw):
        self.set_status(tr("Fehler: {0}", text.split("\n")[0]))
        self.notify(text, "error")

    def _wait_inline(self, text: str, kind: str, buttons, extra=None, default=None):
        """Frage oben im Fenster; wartet (ohne neues Fenster), bis ein Knopf gedrückt wird."""
        var = tk.StringVar(value="")
        key = f"ask{time.time()}"
        answer = {"v": default}

        def choose(v):
            answer["v"] = v() if callable(v) else v
            var.set("x")
        self.notify(text, kind, [(label, (lambda v=v: choose(v))) for label, v in buttons], key=key,
                    closable=False, extra=extra)
        banner = self.banners[key]
        self.show_notice(banner)
        old = (self.bind("<Escape>"), self.bind("<Return>"))
        self.bind("<Escape>", lambda e: choose(buttons[-1][1]))
        if banner._buttons:
            self.bind("<Return>", lambda e: choose(buttons[0][1]))
        self.asking += 1
        try:
            self.wait_variable(var)
        except tk.TclError:
            return default
        finally:
            self.asking -= 1
            try:
                self.bind("<Escape>", old[0] or "")
                self.bind("<Return>", old[1] or "")
            except tk.TclError:
                pass
        self.dismiss(key)
        return answer["v"]

    def show_notice(self, banner):
        """Sorgt dafür, dass eine Frage sichtbar ist (die Leiste liegt über jeder Seite)."""
        try:
            banner.focus_set()
        except tk.TclError:
            pass

    def ask(self, text: str, yes: str | None = None, no: str | None = None, **kw) -> bool:
        return bool(self._wait_inline(text, "ask", [(yes or tr("Ja"), True), (no or tr("Nein"), False)],
                                      default=False))

    def ask_text(self, text: str, initial: str = "", ok: str = "OK") -> str | None:
        var = tk.StringVar(value=initial)
        holder = {}

        def extra(body, bg):
            e = ttk.Entry(body, textvariable=var)
            e.pack(fill="x", pady=(8, 0))
            holder["e"] = e
            self.after(50, lambda: (e.focus_set(), e.icursor("end")))
        res = self._wait_inline(text, "ask", [(ok, lambda: var.get().strip() or None), (tr("Abbrechen"), None)],
                                extra=extra)
        return res or None

    def ask_choice(self, text: str, options: list[str], default: int = 0, ok: str | None = None) -> int | None:
        ok = ok or tr("Auswählen")
        var = tk.StringVar(value=options[default] if options else "")

        def extra(body, bg):
            cb = ttk.Combobox(body, textvariable=var, values=options, state="readonly")
            cb.pack(fill="x", pady=(8, 0))
        res = self._wait_inline(text, "ask", [(ok, lambda: options.index(var.get()) if var.get() in options else None),
                                              (tr("Abbrechen"), None)], extra=extra)
        return res
