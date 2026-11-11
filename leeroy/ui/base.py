"""Tk-Grundlagen: Fensterklasse mit optionalem Drag & Drop."""
from __future__ import annotations


import tkinter as tk


try:                                   # optional: Drag & Drop
    from tkinterdnd2 import TkinterDnD, DND_FILES
    BaseTk = TkinterDnD.Tk
    HAS_DND = True
except Exception:                      # noqa – Paket fehlt oder Tk-Erweiterung nicht ladbar
    BaseTk = tk.Tk
    DND_FILES = None
    HAS_DND = False
