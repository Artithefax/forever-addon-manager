"""Theme (Braun/Gold im Stil eines Fantasy-MMO-Interfaces)."""
from __future__ import annotations


from tkinter import ttk
import tkinter as tk


# --------------------------------------------------------------------------- #
# Theme (Braun/Gold im Stil eines Fantasy-MMO-Interfaces)
# --------------------------------------------------------------------------- #

THEME = {
    # Farbwerte nach dem Stil der Vorlage (dunkles Petrol-Schwarz, Bernstein-Gold, Pergament)
    "bg":          "#0d1212",   # Seitenhintergrund
    "header":      "#090e0e",   # Kopf- und Fußzeile
    "panel":       "#131c1c",   # Kästen
    "row_a":       "#0f1515",
    "row_b":       "#121a1a",
    "active":      "#1f241e",   # aktive Zeile / Hover
    "pill":        "#222722",
    "line":        "#1e2626",   # feine Trennlinien
    "border":      "#67593d",   # gedämpfter Goldrahmen
    "border_soft": "#3b3a2c",
    "field_line":  "#6e715f",   # Rahmen von Eingabefeldern
    "gold":        "#e6bb70",   # Akzent
    "gold_deep":   "#c9963f",
    "text":        "#f3f0e8",   # Pergament-Weiß
    "text_dim":    "#b4bbb9",
    "text_faint":  "#6b7372",
    "text_faint2": "#8e9694",   # Nebentexte mit ausreichend Kontrast
    "btn_primary": "#644e2b",   # Bronze-Button
    "btn_primary_hi": "#7a5f34",
    "btn_text":    "#fff7e6",
    "crest":       "#16242c",   # Wappen-Nachtblau
    "warn":        "#e69633",
    "ok":          "#7ddc6a",   # aktiv / alles in Ordnung
    "update":      "#ffd54a",   # Update verfügbar
    "update_bg":   "#2d2610",
    "old":         "#d6c37a",   # Interface passt nicht zur Spielversion
}


UPD_MARK = "⬇"          # Kennzeichen für Addons mit verfügbarem Update


def spaced(text: str) -> str:
    """Großbuchstaben mit Sperrung wie bei den Kategorie-Labels (Tk kennt kein letter-spacing)."""
    return "\u2009".join(text.upper())


def pick_font(root, candidates, size, weight="normal"):
    import tkinter.font as tkfont
    families = set(tkfont.families(root))
    for f in candidates:
        if f in families:
            return (f, size, weight)
    return ("TkDefaultFont", size, weight)


def apply_theme(root) -> dict:
    t = THEME
    style = ttk.Style(root)
    style.theme_use("clam")      # einziges Theme, das Farben überall zuverlässig übernimmt
    serif = ["Georgia", "Cambria", "Book Antiqua", "Times New Roman"]
    sans = ["Segoe UI", "Source Sans 3", "Helvetica", "Arial"]
    fonts = {
        "base":  pick_font(root, sans, 10),
        "small": pick_font(root, sans, 9),
        "bold":  pick_font(root, ["Segoe UI Semibold"] + sans, 10, "bold"),
        "label": pick_font(root, ["Segoe UI Semibold"] + sans, 8, "bold"),
        "brand": pick_font(root, serif, 9, "bold"),
        "title": pick_font(root, serif, 22, "bold"),
        "sub":   pick_font(root, serif, 10, "italic"),
        "page":  pick_font(root, serif, 18, "bold"),
        "side":  pick_font(root, serif, 14, "bold"),
        "hero":  pick_font(root, serif, 26, "bold"),
        "play":  pick_font(root, serif, 15, "bold"),
        "tile":  pick_font(root, serif, 20, "bold"),
        "card":  pick_font(root, serif, 13, "bold"),
    }
    root.configure(bg=t["bg"])

    style.configure(".", background=t["bg"], foreground=t["text"], font=fonts["base"],
                    bordercolor=t["border_soft"], darkcolor=t["bg"], lightcolor=t["bg"],
                    troughcolor=t["bg"], focuscolor=t["gold"],
                    selectbackground=t["active"], selectforeground=t["gold"],
                    fieldbackground=t["panel"], insertcolor=t["gold"])
    style.configure("TFrame", background=t["bg"])
    style.configure("Panel.TFrame", background=t["panel"])
    style.configure("Header.TFrame", background=t["header"])
    style.configure("TLabel", background=t["bg"], foreground=t["text_dim"])
    style.configure("Panel.TLabel", background=t["panel"], foreground=t["text_dim"])
    style.configure("Section.TLabel", background=t["bg"], foreground=t["gold"], font=fonts["label"])
    style.configure("Count.TLabel", background=t["bg"], foreground=t["text_dim"])
    style.configure("Hint.TLabel", background=t["bg"], foreground=t["text_faint"], font=fonts["small"])
    style.configure("TCheckbutton", background=t["panel"], foreground=t["text_dim"],
                    indicatorbackground=t["bg"], indicatorforeground=t["gold"],
                    indicatorcolor=t["bg"], bordercolor=t["field_line"], focuscolor=t["panel"],
                    upperbordercolor=t["field_line"], lowerbordercolor=t["field_line"])
    style.configure("Wrap.TCheckbutton", wraplength=330, justify="left")   # lange Texte umbrechen
    style.configure("Plain.TCheckbutton", background=t["bg"], foreground=t["text_dim"],
                    focuscolor=t["bg"])
    style.map("Plain.TCheckbutton", background=[("active", t["bg"])],
              foreground=[("active", t["gold"])],
              indicatorcolor=[("selected", t["gold"]), ("pressed", t["active"])],
              indicatorbackground=[("selected", t["gold"])])
    style.map("TCheckbutton", background=[("active", t["panel"])],
              foreground=[("active", t["gold"])],
              indicatorcolor=[("selected", t["gold"]), ("pressed", t["active"])],
              indicatorbackground=[("selected", t["gold"])])
    style.configure("TRadiobutton", background=t["panel"], foreground=t["text_dim"],
                    indicatorbackground=t["bg"], indicatorforeground=t["gold"],
                    indicatorcolor=t["bg"], bordercolor=t["field_line"], focuscolor=t["panel"],
                    upperbordercolor=t["field_line"], lowerbordercolor=t["field_line"])
    style.map("TRadiobutton", background=[("active", t["panel"])], foreground=[("active", t["gold"])],
              indicatorcolor=[("selected", t["gold"]), ("pressed", t["active"])],
              indicatorbackground=[("selected", t["gold"])])
    style.configure("CardKicker.TLabel", background=t["panel"], foreground=t["gold"], font=fonts["label"])
    style.configure("Status.TLabel", background=t["header"], foreground=t["text_dim"],
                    padding=(12, 5))

    # Sekundär-Buttons: ruhig, feiner Goldrahmen, Text wird beim Hover golden
    style.configure("TButton", background=t["panel"], foreground=t["text"], font=fonts["base"],
                    bordercolor=t["border"], lightcolor=t["panel"], darkcolor=t["panel"],
                    padding=(12, 5), relief="solid", borderwidth=1)
    style.map("TButton",
              background=[("pressed", t["pill"]), ("active", t["active"])],
              foreground=[("disabled", t["text_faint"]), ("active", t["gold"])],
              bordercolor=[("active", t["gold"])],
              lightcolor=[("active", t["active"])], darkcolor=[("active", t["active"])])
    # Haupt-Buttons: Bronze mit hellem Goldrand (wie "Suchen")
    style.configure("Primary.TButton", background=t["btn_primary"], foreground=t["btn_text"],
                    bordercolor=t["gold"], lightcolor=t["btn_primary"],
                    darkcolor=t["btn_primary"], font=fonts["bold"])
    style.map("Primary.TButton",
              background=[("pressed", t["btn_primary"]), ("active", t["btn_primary_hi"])],
              foreground=[("active", t["btn_text"])],
              lightcolor=[("active", t["btn_primary_hi"])],
              darkcolor=[("active", t["btn_primary_hi"])])

    # Spielen-Button in der Kopfzeile: Bronze mit Goldrand
    style.configure("Play.TButton", background=t["btn_primary"], foreground=t["btn_text"],
                    bordercolor=t["gold"], lightcolor=t["btn_primary"], darkcolor=t["btn_primary"],
                    font=fonts["bold"], padding=(16, 3))
    style.map("Play.TButton",
              background=[("pressed", t["btn_primary"]), ("active", t["btn_primary_hi"]),
                          ("disabled", t["panel"])],
              foreground=[("disabled", t["text_faint"])],
              bordercolor=[("disabled", t["border_soft"])],
              lightcolor=[("active", t["btn_primary_hi"])], darkcolor=[("active", t["btn_primary_hi"])])

    # Kleine Link-Buttons in der Kopfzeile
    style.configure("Link.TButton", background=t["header"], foreground=t["text_dim"],
                    font=fonts["small"], bordercolor=t["border_soft"], lightcolor=t["header"],
                    darkcolor=t["header"], padding=(10, 3))
    style.map("Link.TButton",
              background=[("pressed", t["panel"]), ("active", t["panel"])],
              foreground=[("active", t["gold"])],
              bordercolor=[("active", t["gold"])],
              lightcolor=[("active", t["panel"])], darkcolor=[("active", t["panel"])])

    style.configure("TEntry", fieldbackground=t["bg"], foreground=t["text"],
                    bordercolor=t["field_line"], lightcolor=t["bg"], darkcolor=t["bg"],
                    padding=5)
    style.map("TEntry", bordercolor=[("focus", t["gold"])])
    style.configure("TCombobox", fieldbackground=t["bg"], background=t["bg"],
                    foreground=t["text"], arrowcolor=t["gold"], bordercolor=t["field_line"],
                    lightcolor=t["bg"], darkcolor=t["bg"], padding=4)
    style.map("TCombobox", fieldbackground=[("readonly", t["bg"])],
              foreground=[("readonly", t["text"])],
              selectbackground=[("readonly", t["bg"])],
              selectforeground=[("readonly", t["text"])],
              background=[("active", t["active"])],
              bordercolor=[("focus", t["gold"]), ("active", t["gold"])])
    root.option_add("*TCombobox*Listbox.background", t["panel"])
    root.option_add("*TCombobox*Listbox.foreground", t["text"])
    root.option_add("*TCombobox*Listbox.selectBackground", t["active"])
    root.option_add("*TCombobox*Listbox.selectForeground", t["gold"])
    root.option_add("*TCombobox*Listbox.font", fonts["base"])

    style.configure("Treeview", background=t["row_a"], fieldbackground=t["row_a"],
                    foreground=t["text"], rowheight=30, bordercolor=t["border_soft"],
                    lightcolor=t["row_a"], darkcolor=t["row_a"], borderwidth=1)
    style.map("Treeview", background=[("selected", t["active"])],
              foreground=[("selected", t["gold"])])
    style.configure("Treeview.Heading", background=t["header"], foreground=t["gold"],
                    font=fonts["label"], bordercolor=t["line"], lightcolor=t["header"],
                    darkcolor=t["header"], relief="flat", padding=(8, 7))
    style.map("Treeview.Heading", background=[("active", t["panel"])])

    # --- Ansicht „Seitenleiste“ ---
    style.configure("Side.TFrame", background=t["header"])
    style.configure("Side.TLabel", background=t["header"], foreground=t["text_dim"], font=fonts["small"])
    style.configure("SideKicker.TLabel", background=t["header"], foreground=t["gold"], font=fonts["label"])
    style.configure("Nav.TButton", background=t["header"], foreground=t["text"], font=fonts["base"],
                    bordercolor=t["header"], lightcolor=t["header"], darkcolor=t["header"],
                    anchor="w", padding=(14, 4), relief="flat")
    style.map("Nav.TButton", background=[("active", t["panel"])], foreground=[("active", t["gold"])],
              bordercolor=[("active", t["header"])], lightcolor=[("active", t["panel"])],
              darkcolor=[("active", t["panel"])])
    style.configure("NavActive.TButton", background=t["active"], foreground=t["gold"], font=fonts["bold"],
                    bordercolor=t["gold"], lightcolor=t["active"], darkcolor=t["active"],
                    anchor="w", padding=(14, 4), relief="flat")
    style.map("NavActive.TButton", background=[("active", t["active"])])
    style.configure("Kicker.TLabel", background=t["bg"], foreground=t["gold"], font=fonts["label"])
    style.configure("PageTitle.TLabel", background=t["bg"], foreground=t["text"], font=fonts["page"])
    style.configure("Body.TLabel", background=t["bg"], foreground=t["text_dim"])
    style.configure("Detail.TFrame", background=t["row_a"])
    style.configure("DetailKey.TLabel", background=t["row_a"], foreground=t["text_faint2"])
    style.configure("DetailVal.TLabel", background=t["row_a"], foreground=t["text"])
    style.configure("DetailKicker.TLabel", background=t["row_a"], foreground=t["gold"], font=fonts["label"])
    style.configure("DetailTitle.TLabel", background=t["row_a"], foreground=t["text"], font=fonts["page"])
    style.configure("DetailNote.TLabel", background=t["row_a"], foreground=t["text_dim"])
    style.configure("Danger.TButton", background=t["bg"], foreground=t["warn"], bordercolor="#5a3d1c",
                    lightcolor=t["bg"], darkcolor=t["bg"])
    style.map("Danger.TButton", background=[("active", "#21160c")], bordercolor=[("active", t["warn"])])

    # --- Ansicht „Launcher“ ---
    style.configure("Head.TFrame", background=t["header"])
    style.configure("HeadKicker.TLabel", background=t["header"], foreground=t["gold"], font=fonts["brand"])
    style.configure("HeadTitle.TLabel", background=t["header"], foreground=t["gold"], font=fonts["hero"])
    style.configure("HeadInfo.TLabel", background=t["header"], foreground=t["text_dim"], font=fonts["small"])
    style.configure("BigPlay.TButton", background=t["btn_primary"], foreground=t["btn_text"],
                    bordercolor=t["gold"], lightcolor=t["btn_primary"], darkcolor=t["btn_primary"],
                    font=fonts["play"], padding=(34, 14))
    style.map("BigPlay.TButton",
              background=[("pressed", t["btn_primary"]), ("active", t["btn_primary_hi"]),
                          ("disabled", t["panel"])],
              foreground=[("disabled", t["text_faint"])],
              lightcolor=[("active", t["btn_primary_hi"])], darkcolor=[("active", t["btn_primary_hi"])])
    style.configure("Tile.TFrame", background=t["panel"])
    style.configure("TileKicker.TLabel", background=t["panel"], foreground=t["gold"], font=fonts["label"])
    style.configure("TileValue.TLabel", background=t["panel"], foreground=t["text"], font=fonts["tile"])
    style.configure("TileHint.TLabel", background=t["panel"], foreground=t["text_faint2"], font=fonts["small"])
    style.configure("Card.TFrame", background=t["row_a"])
    style.configure("CardTitle.TLabel", background=t["row_a"], foreground=t["text"], font=fonts["card"])
    style.configure("CardSub.TLabel", background=t["row_a"], foreground=t["text_faint2"], font=fonts["small"])
    style.configure("CardLink.TButton", background=t["row_a"], foreground=t["gold"], font=fonts["small"],
                    bordercolor=t["row_a"], lightcolor=t["row_a"], darkcolor=t["row_a"], padding=(0, 2))
    style.map("CardLink.TButton", foreground=[("active", t["text"])], background=[("active", t["row_a"])],
              bordercolor=[("active", t["row_a"])])
    style.configure("ToggleOn.TButton", background=t["btn_primary"], foreground=t["btn_text"],
                    bordercolor=t["gold"], lightcolor=t["btn_primary"], darkcolor=t["btn_primary"],
                    font=fonts["label"], padding=(10, 3))
    style.map("ToggleOn.TButton", background=[("active", t["btn_primary_hi"])])
    style.configure("ToggleOff.TButton", background=t["pill"], foreground=t["text_dim"],
                    bordercolor=t["border_soft"], lightcolor=t["pill"], darkcolor=t["pill"],
                    font=fonts["label"], padding=(10, 3))
    style.map("ToggleOff.TButton", background=[("active", t["active"])], foreground=[("active", t["gold"])])
    for name, bg, fg, bc, fnt in (("TMenubutton", t["panel"], t["text"], t["border"], fonts["base"]),
                                  ("Primary.TMenubutton", t["btn_primary"], t["btn_text"], t["gold"], fonts["bold"])):
        style.configure(name, background=bg, foreground=fg, bordercolor=bc, lightcolor=bg, darkcolor=bg,
                        arrowcolor=fg, font=fnt, padding=(12, 5), relief="solid")
        style.map(name, background=[("active", t["active"] if name == "TMenubutton" else t["btn_primary_hi"])],
                  foreground=[("active", t["gold"] if name == "TMenubutton" else t["btn_text"])])

    style.configure("Vertical.TScrollbar", background=t["panel"], troughcolor=t["bg"],
                    bordercolor=t["bg"], arrowcolor=t["gold"],
                    lightcolor=t["panel"], darkcolor=t["panel"], gripcount=0)
    style.map("Vertical.TScrollbar", background=[("active", t["border"])])
    return fonts


def draw_emblem(cv, x0: int, y0: int, s: int) -> None:
    """Eigenes Wappen: Schild mit schräg nach vorn stürmendem L und Fahrtlinien."""
    t = THEME

    def p(fx, fy):
        return x0 + fx * s, y0 + fy * s

    def lean(fx, fy):                      # Neigung nach vorn
        return p(fx + 0.18 * (.5 - fy), fy)

    shield = [p(.12, .10), p(.50, .02), p(.88, .10), p(.88, .48),
              p(.72, .78), p(.50, .96), p(.28, .78), p(.12, .48)]
    cv.create_polygon([c for pt in shield for c in pt], fill=t["crest"], outline=t["gold"],
                      width=max(2, s // 24))
    inner = [p(.20, .16), p(.50, .10), p(.80, .16), p(.80, .47),
             p(.67, .72), p(.50, .87), p(.33, .72), p(.20, .47)]
    cv.create_polygon([c for pt in inner for c in pt], fill="", outline=t["border"],
                      width=max(1, s // 40))
    letter = [(.36, .24), (.50, .24), (.50, .62), (.70, .62), (.70, .74), (.36, .74)]
    cv.create_polygon([c for fx, fy in letter for c in lean(fx, fy)], fill=t["gold"],
                      outline=t["gold_deep"], width=max(1, s // 60))
    for fy, ln in ((.36, .12), (.48, .16), (.60, .12)):
        cv.create_line(*lean(.33 - ln, fy), *lean(.33, fy), fill=t["text"],
                       width=max(2, s // 28), capstyle="round")


ICON_PNG = (
    "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"
)


def make_icon(root, n: int = 32):
    """Fenstersymbol aus dem eingebetteten PNG (L im Schild)."""
    return tk.PhotoImage(master=root, data=ICON_PNG)
