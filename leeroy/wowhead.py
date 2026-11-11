"""Wowhead: Suche mit Vorschau."""
from __future__ import annotations

import html
import json
import re
import urllib.parse
import urllib.request

from .constants import APP_VERSION
from .i18n import cur_lang
from .reports import QUALITY_COLORS


# --------------------------------------------------------------------------- #
# Wowhead: Suche mit Vorschau
# --------------------------------------------------------------------------- #
# Wowhead hat getrennte Datenbanken je Spielausgabe – WoW Forever passt am besten zu Classic.
WOWHEAD_EDITIONS = [("classic", "Classic (Era / Anniversary)"), ("tbc", "Burning Crusade Classic"),
                    ("wotlk", "Wrath of the Lich King Classic"), ("cata", "Cataclysm Classic"),
                    ("mop-classic", "Mists of Pandaria Classic"), ("", "Retail (aktuelles WoW)")]
# Typnummern aus der Wowhead-Suche → Adressteil und Anzeigename
WOWHEAD_TYPES = {1: ("npc", "NPC"), 2: ("object", "Objekt"), 3: ("item", "Gegenstand"), 4: ("item-set", "Set"),
                 5: ("quest", "Quest"), 6: ("spell", "Zauber"), 7: ("zone", "Zone"), 8: ("faction", "Fraktion"),
                 10: ("achievement", "Erfolg"), 11: ("title", "Titel"), 12: ("event", "Ereignis"),
                 13: ("class", "Klasse"), 14: ("race", "Volk"), 15: ("skill", "Fertigkeit"), 17: ("currency", "Währung")}
WOWHEAD_TOOLTIP_TYPES = ("item", "npc", "quest", "spell", "object", "achievement", "currency", "item-set")
WOWHEAD_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Leeroyzan/" + APP_VERSION


def wowhead_base(edition: str, lang: str = "") -> str:
    """https://www.wowhead.com[/classic][/de] – deutsche Seiten, wenn Leeroyzan auf Deutsch läuft."""
    lang = lang or cur_lang()
    return "https://www.wowhead.com" + (f"/{edition}" if edition else "") + ("/de" if lang == "de" else "")


def wowhead_url(edition: str, kind: str, wid) -> str:
    return f"{wowhead_base(edition)}/{kind}={wid}"


def wowhead_search_url(edition: str, query: str) -> str:
    return f"{wowhead_base(edition)}/search?q={urllib.parse.quote(query)}"


def _wowhead_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": WOWHEAD_UA, "Accept": "application/json, text/javascript, */*"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def wowhead_search(query: str, edition: str) -> list[dict]:
    """Die Vorschläge der Wowhead-Suche (wie im Suchfeld auf der Seite): Name, Art, ID, Qualität."""
    query = query.strip()
    if not query:
        return []
    data = _wowhead_json(f"{wowhead_base(edition)}/search/suggestions-template?q={urllib.parse.quote(query)}")
    out = []
    for r in (data.get("results") or []) if isinstance(data, dict) else []:
        if not isinstance(r, dict):
            continue
        t = WOWHEAD_TYPES.get(r.get("type"))
        name = r.get("name")
        if not t or not name or r.get("id") is None:
            continue
        q = r.get("quality")
        out.append({"kind": t[0], "kind_name": t[1], "id": r["id"], "name": html.unescape(str(name)), "edition": edition,
                    "quality": q if isinstance(q, int) and 0 <= q < len(QUALITY_COLORS) else None,
                    "url": wowhead_url(edition, t[0], r["id"])})
    return out


def tooltip_text(markup: str) -> str:
    """Wowhead-Tooltip (HTML) → lesbarer Text mit Zeilenumbrüchen."""
    t = re.sub(r"<!--.*?-->", "", markup or "", flags=re.S)
    t = re.sub(r"<(script|style)\b.*?</\1>", "", t, flags=re.S | re.I)
    t = re.sub(r"<br\s*/?>|<table\b[^>]*>|</(div|tr|table|p|li|h\d)>", "\n", t, flags=re.I)
    t = re.sub(r"</t[dh]>", "   ", t, flags=re.I)
    t = html.unescape(re.sub(r"<[^>]+>", "", t))
    lines = [re.sub(r"[ \t\u00a0]+", " ", ln).strip() for ln in t.splitlines()]
    out = []
    for ln in lines:
        if ln or (out and out[-1]):
            out.append(ln)
    return "\n".join(out).strip()


def wowhead_tooltip(kind: str, wid, edition: str) -> dict:
    """Kurzinfo zu einem Eintrag (der Tooltip, den Wowhead beim Überfahren eines Links zeigt)."""
    if kind not in WOWHEAD_TOOLTIP_TYPES:
        return {}
    env = (f"/{edition}" if edition else "") + ("/de" if cur_lang() == "de" else "")
    data = _wowhead_json(f"https://nether.wowhead.com{env}/tooltip/{kind}/{wid}")
    if not isinstance(data, dict):
        return {}
    q = data.get("quality")
    return {"name": html.unescape(str(data.get("name") or "")), "text": tooltip_text(str(data.get("tooltip") or "")),
            "quality": q if isinstance(q, int) and 0 <= q < len(QUALITY_COLORS) else None}
