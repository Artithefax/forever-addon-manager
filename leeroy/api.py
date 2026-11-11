"""Flache Sicht auf alle Module (für Tests und Skripte): import leeroy.api as lz; lz.parse_toc(...).

Zum Ersetzen einer Funktion in Tests das Modul nehmen, in dem sie benutzt wird
(module_of("fetch_reward_data").http_get = ...), nicht diese Sicht.
"""
import importlib
import sys

MODULES = ["constants", "i18n", "config", "wow", "files", "github", "curseforge", "sharing", "selfupdate", "lua",
           "sessions", "linkaddon", "reports", "linkdata", "wowhead", "questdb", "auction"]
UI_MODULES = ["ui.base", "ui.theme", "ui.dialogs", "ui.app", "main"]


def _load(names):
    for name in names:
        mod = importlib.import_module(f"leeroy.{name}")
        globals().update({k: v for k, v in vars(mod).items() if not k.startswith("__")})


def module_of(name: str):
    """Modul, in dem eine Funktion oder Klasse definiert ist."""
    return sys.modules[globals()[name].__module__]


_load(MODULES)
try:
    _load(UI_MODULES)
except ImportError:          # ohne tkinter: nur die Logik
    pass
