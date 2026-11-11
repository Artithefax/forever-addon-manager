"""Dialoge im App-Design und das Update-Fenster."""
from __future__ import annotations


from tkinter import ttk
import tkinter as tk

from ..constants import APP_VERSION
from ..i18n import tr
from .theme import THEME


# --------------------------------------------------------------------------- #
# Dialoge im App-Design
# --------------------------------------------------------------------------- #

BACKUP_KINDS = {"": "manuell", "auto": "automatisch vor Update", "vorher": "vor Wiederherstellung"}


class Placeholder:
    """Grauer Hinweistext in einem leeren Eingabefeld – verschwindet, sobald man tippt."""

    def __init__(self, entry, var: tk.StringVar, text: str):
        t = THEME
        self.entry, self.var = entry, var
        self.lbl = tk.Label(entry, text=text, fg=t["text_faint"], bg=t["bg"], cursor="xterm",
                            font=entry.winfo_toplevel().fonts["base"] if hasattr(entry.winfo_toplevel(), "fonts")
                            else None)
        self.lbl.bind("<Button-1>", lambda e: entry.focus_set())
        var.trace_add("write", lambda *a: self.update())
        entry.bind("<FocusIn>", lambda e: self.update(), add="+")
        entry.bind("<FocusOut>", lambda e: self.update(), add="+")
        self.update()

    def update(self):
        try:
            if self.var.get():
                self.lbl.place_forget()
            else:
                self.lbl.place(x=6, rely=0.5, anchor="w")
        except tk.TclError:
            pass


class UpdateSteps:
    """Schrittliste für das Update-Fenster: ✓ erledigt, ▶ läuft, ○ offen, ✕ Fehler – mit Detailzeile und Balken."""

    def __init__(self, parent, steps: list[tuple[str, str]]):
        t = THEME
        fonts = parent.winfo_toplevel().fonts
        self.parent = parent
        self.keys = [k for k, _ in steps]
        self.rows = {}
        box = ttk.Frame(parent, style="Panel.TFrame")
        box.pack(fill="x", pady=(14, 0))
        for i, (key, label) in enumerate(steps):
            icon = tk.Label(box, text="○", width=2, bg=t["panel"], fg=t["text_faint"], font=fonts["bold"])
            icon.grid(row=i, column=0, sticky="w", pady=3)
            text = tk.Label(box, text=label, bg=t["panel"], fg=t["text_dim"], font=fonts["base"], anchor="w")
            text.grid(row=i, column=1, sticky="w", pady=3)
            self.rows[key] = (icon, text)
        self.detail = tk.StringVar(value="")
        self.detail_lbl = tk.Label(parent, textvariable=self.detail, bg=t["panel"], fg=t["text_dim"], font=fonts["small"],
                                   anchor="w", justify="left", wraplength=470)
        self.detail_lbl.pack(fill="x", pady=(12, 0))
        style = ttk.Style(parent)
        style.configure("Update.Horizontal.TProgressbar", troughcolor=t["row_a"], background=t["gold"],
                        bordercolor=t["border_soft"], lightcolor=t["gold"], darkcolor=t["gold_deep"])
        self.bar = ttk.Progressbar(parent, style="Update.Horizontal.TProgressbar", mode="indeterminate", length=470)
        self.bar.pack(fill="x", pady=(8, 0))
        self.bar.start(12)
        self.current = None
        self._pending: list = []          # Aufrufe aus Hintergrund-Threads

    def _ui(self, fn):
        try:
            self.parent.after(0, fn)
        except (RuntimeError, tk.TclError):
            pass

    def set(self, key: str, detail: str = ""):
        self._ui(lambda: self._set(key, detail))

    def _set(self, key, detail):
        t = THEME
        if key != self.current:
            for k in self.keys:
                icon, text = self.rows[k]
                if self.keys.index(k) < self.keys.index(key):
                    icon.configure(text="✓", fg=t["ok"]); text.configure(fg=t["text_dim"])
                elif k == key:
                    icon.configure(text="▶", fg=t["gold"]); text.configure(fg=t["text"])
                else:
                    icon.configure(text="○", fg=t["text_faint"]); text.configure(fg=t["text_dim"])
            self.current = key
        self.detail_lbl.configure(fg=t["text_dim"])
        self.detail.set(detail)

    def done(self, key: str):
        icon, text = self.rows[key]
        icon.configure(text="✓", fg=THEME["ok"])
        text.configure(fg=THEME["text_dim"])

    def fraction(self, value: float | None):
        """Fortschritt 0–1, oder None für „läuft“ ohne bekannte Dauer."""
        if value is None:
            if str(self.bar.cget("mode")) != "indeterminate":
                self.bar.configure(mode="indeterminate")
                self.bar.start(12)
        else:
            if str(self.bar.cget("mode")) != "determinate":
                self.bar.stop()
                self.bar.configure(mode="determinate", maximum=1000)
            self.bar.configure(value=max(0, min(1, value)) * 1000)

    def fail(self, text: str):
        t = THEME
        if self.current:
            icon, label = self.rows[self.current]
            icon.configure(text="✕", fg=t["warn"])
        self.bar.stop()
        self.bar.configure(mode="determinate", value=0)
        self.detail_lbl.configure(fg=t["warn"])
        self.detail.set(text)


class UpdateWindow:
    """Das sichtbare Manager-Update: Build auf GitHub → Herunterladen → Prüfen → Neustart."""

    def __init__(self, app, tag: str):
        t = THEME
        self.app, self.tag = app, tag
        self.win = tk.Toplevel(app)
        self.win.fonts = app.fonts
        self.win.title(tr("Leeroyzan-Update"))
        self.win.configure(bg=t["panel"])
        self.win.resizable(False, False)
        self.win.transient(app)
        self.win.protocol("WM_DELETE_WINDOW", self.hide)
        frame = ttk.Frame(self.win, style="Panel.TFrame", padding=(24, 20))
        frame.pack(fill="both", expand=True)
        tk.Label(frame, text=tr("Update auf Leeroyzan {0}", tag), bg=t["panel"], fg=t["gold"], font=app.fonts["page"]).pack(anchor="w")
        ttk.Label(frame, text=tr("Installiert ist v{0}. Du kannst nebenbei weiterarbeiten – vor dem Neustart wartet "
                                 "Leeroyzan, bis laufende Vorgänge fertig sind.", APP_VERSION),
                  style="Panel.TLabel", wraplength=470, justify="left").pack(anchor="w", pady=(4, 0))
        self.steps = UpdateSteps(frame, [("build", tr("Neue Version auf GitHub bereitstellen")), ("download", tr("Herunterladen")),
                                         ("verify", tr("Prüfen (Prüfsumme)")), ("restart", tr("Neu starten"))])
        self.btns = ttk.Frame(frame, style="Panel.TFrame")
        self.btns.pack(fill="x", pady=(16, 0))
        self.buttons([])
        self.win.update_idletasks()
        x = app.winfo_rootx() + (app.winfo_width() - self.win.winfo_reqwidth()) // 2
        y = app.winfo_rooty() + (app.winfo_height() - self.win.winfo_reqheight()) // 3
        self.win.geometry(f"+{max(0, x)}+{max(0, y)}")

    def buttons(self, extra: list[tuple[str, callable]], cancel=True):
        for w in self.btns.winfo_children():
            w.destroy()
        for i, (label, cmd) in enumerate(extra):
            ttk.Button(self.btns, text=label, command=cmd, style="Primary.TButton" if i == 0 else "TButton").pack(
                side="left", padx=(0 if i == 0 else 8, 0))
        if cancel:
            ttk.Button(self.btns, text=tr("Abbrechen"), command=self.app.cancel_manager_update).pack(side="right")
        ttk.Button(self.btns, text=tr("Im Hintergrund"), command=self.hide).pack(side="right", padx=(0, 8))

    def show(self):
        if self.alive():
            self.win.deiconify()
            self.win.lift()

    def hide(self):
        if self.alive():
            self.win.withdraw()
            self.app.set_status(tr("Leeroyzan-Update auf {0} läuft im Hintergrund – „Nach Manager-Update suchen“ zeigt den Fortschritt wieder.", self.tag))

    def alive(self) -> bool:
        try:
            return bool(self.win.winfo_exists())
        except tk.TclError:
            return False

    def close(self):
        if self.alive():
            self.win.destroy()


SET_ALL_OFF = "— Alle deaktivieren —"
SET_ALL_ON = "— Alle aktivieren —"
