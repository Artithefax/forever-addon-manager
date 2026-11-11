"""Sprachen: Deutsch ist die Ausgangssprache, tr() übersetzt ins Englische (i18n_en.py)."""
from __future__ import annotations

import os

from .i18n_en import EN


# --------------------------------------------------------------------------- #
# Sprachen – Deutsch ist die Ausgangssprache, die englischen Texte stehen in i18n_en.py
# --------------------------------------------------------------------------- #

LANGUAGES = {"de": "Deutsch", "en": "English"}
LANG = "de"
TRANSLATIONS: dict[str, dict[str, str]] = {"en": EN}


def tr(text: str, *args) -> str:
    """Übersetzt einen Oberflächentext in die gewählte Sprache; {0}, {1} … werden mit args gefüllt."""
    out = TRANSLATIONS.get(LANG, {}).get(text, text) if LANG != "de" else text
    if args:
        try:
            return out.format(*args)
        except (IndexError, KeyError, ValueError):
            return text.format(*args)
    return out


def cur_lang() -> str:
    """Aktuelle Sprache ("de" oder "en")."""
    return LANG


def set_language(code: str) -> None:
    global LANG
    LANG = code if code in LANGUAGES else "en"


def detect_language() -> str:
    """Sprache beim ersten Start: die Windows-Anzeigesprache (Deutsch oder Englisch), sonst Englisch."""
    code = ""
    if os.name == "nt":
        try:
            import ctypes
            primary = ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF
            code = {0x07: "de", 0x09: "en"}.get(primary, "")
        except Exception:  # noqa
            code = ""
    if not code:
        import locale
        loc = (locale.getlocale()[0] or os.environ.get("LANG", "") or "").lower()
        code = next((c for c in LANGUAGES if loc.startswith(c)), "")
    return code or "en"
