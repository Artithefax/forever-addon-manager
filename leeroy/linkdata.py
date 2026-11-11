"""Leeroylink in beide Richtungen: Data.lua schreiben, Spieldaten, Diagnose, Charaktere."""
from __future__ import annotations

import re

from .wow import clean_wow_text
from .curseforge import html_to_text
from .lua import LINK_IMPORT, _LUA_NAME
from .sessions import _int, _list, _map, _num, _text
from .linkaddon import installed_link_dir


# --------------------------------------------------------------------------- #
# Leeroylink in beide Richtungen
#   Spiel -> Leeroyzan: SavedVariables (Fehler, Ladestatus, Speicher, Charaktere, Rezepte, Wunschliste)
#   Leeroyzan -> Spiel: Data.lua im Addon-Ordner (Updates, Changelogs, Sets als Profile, Wunschliste)
# WoW liest Data.lua beim Einloggen und bei /reload. Leeroyzan schreibt nur diese eine Datei.
# --------------------------------------------------------------------------- #

LINK_SCHEMA = 1
LUA_KEYWORDS = {"and", "break", "do", "else", "elseif", "end", "false", "for", "function", "if", "in", "local",
                "nil", "not", "or", "repeat", "return", "then", "true", "until", "while"}


def lua_string(text: str) -> str:
    out = ['"']
    for ch in text:
        o = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif o < 32 or o == 127:
            out.append(f"\\{o:03d}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def lua_literal(v, ind: str = "") -> str:
    """Python-Wert als Lua-Ausdruck (nur Daten: Zahlen, Texte, Listen, Tabellen)."""
    if v is None:
        return "nil"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else f"{v:.6g}"
    if isinstance(v, str):
        return lua_string(v)
    inner = ind + "\t"
    if isinstance(v, (list, tuple)):
        if not v:
            return "{}"
        return "{\n" + "".join(f"{inner}{lua_literal(x, inner)},\n" for x in v) + ind + "}"
    if isinstance(v, dict):
        if not v:
            return "{}"
        rows = []
        for k, x in v.items():
            if isinstance(k, str) and _LUA_NAME.fullmatch(k) and k not in LUA_KEYWORDS:
                key = k
            else:
                key = f"[{lua_literal(k)}]"
            rows.append(f"{inner}{key} = {lua_literal(x, inner)},\n")
        return "{\n" + "".join(rows) + ind + "}"
    return lua_string(str(v))


def link_data_text(payload: dict) -> str:
    return ("-- Data.lua: geschrieben von Leeroyzan (Updates, Changelogs, Profile, Wunschliste).\n"
            "-- Nicht von Hand ändern – Leeroyzan überschreibt die Datei. WoW liest sie beim Einloggen und bei /reload.\n"
            f"{LINK_IMPORT} = {lua_literal(payload)}\n")


def write_link_data(root: str, flavor: str, payload: dict) -> str:
    """Schreibt Data.lua ins installierte Addon. Ergebnis: written | unchanged | missing."""
    folder = installed_link_dir(root, flavor)
    if not folder:
        return "missing"
    target = folder / "Data.lua"
    new_cmp = link_data_text(dict(payload, generated=0))
    try:
        old = target.read_text(encoding="utf-8")
    except OSError:
        old = ""
    if re.sub(r"(?m)^\tgenerated = \d+,$", "\tgenerated = 0,", old) == new_cmp:
        return "unchanged"
    tmp = target.with_suffix(".tmp")
    tmp.write_text(link_data_text(payload), encoding="utf-8")
    tmp.replace(target)
    return "written"


def notes_lines(notes: str, limit: int = 12) -> list[str]:
    """Changelog -> kurze Zeilen fürs Spiel (ohne Markdown-Zeichen, ohne Leerzeilen)."""
    out = []
    for line in html_to_text(notes or "").splitlines():
        if line.lstrip().startswith("#"):          # Markdown-Überschriften („## Changes“) weglassen
            continue
        line = re.sub(r"^[\s>*\-+•]+", "", line).replace("**", "").replace("`", "").strip()
        if line:
            out.append(line[:200])
        if len(out) >= limit:
            break
    return out


def _norm_name(text: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", clean_wow_text(text or "").lower())


# ----- Spieldaten lesen ------------------------------------------------------ #
def _norm_char(key: str, raw, account: str) -> dict:
    c = _map(raw)
    info = _map(c.get("info"))
    name, _, realm = key.partition("-")
    recipes = {}
    for prof, book in _map(c.get("recipes")).items():
        book = _map(book)
        lst = {}
        for rname, e in _map(book.get("list")).items():
            e = _map(e)
            lst[rname] = {"item": _int(e.get("item")) or None, "made": max(1, _int(e.get("made"), 1)),
                          "r": [{"id": _int(_map(x).get("id")) or None, "n": max(1, _int(_map(x).get("n"), 1)),
                                 "name": _text(_map(x).get("name"))} for x in _list(e.get("r"))]}
        recipes[prof] = {"rank": _int(book.get("rank")), "max": _int(book.get("max")), "t": _int(book.get("t")),
                         "list": lst}
    return {
        "key": key, "name": name, "realm": realm, "account": account,
        "class": _text(info.get("class")), "level": _int(info.get("level")), "race": _text(info.get("race")),
        "faction": _text(info.get("faction")), "guild": _text(info.get("guild")),
        "money": _int(c.get("money")), "lastSeen": _int(c.get("lastSeen")), "scannedAt": _int(c.get("scannedAt")),
        "skills": [{"name": _text(_map(x).get("name")), "rank": _int(_map(x).get("rank")),
                    "max": _int(_map(x).get("max"))} for x in _list(c.get("skills"))],
        "bags": {k: _int(v) for k, v in _map(c.get("bags")).items() if _int(v) > 0},
        "bank": {k: _int(v) for k, v in _map(c.get("bank")).items() if _int(v) > 0},
        "bankSeen": isinstance(c.get("bank"), (dict, list)) and bool(c.get("bank")),
        "recipes": recipes,
        "autoProfile": _text(c.get("autoProfile")),
    }


def link_state_from_dbs(dbs: list[tuple[str, dict]]) -> dict:
    """Fasst die Addon-Daten aller WoW-Accounts dieser Spielversion zusammen."""
    st = {"accounts": [], "errors": [], "errorStats": {}, "blocked": [], "addons": {}, "addonsAt": 0,
          "memory": {"t": 0, "total": 0, "list": []}, "wishlist": [], "reports": {}, "chars": {}, "items": {},
          "interface": 0, "addonVersion": "", "writtenAt": 0, "importSeen": 0,
          "questRewards": {}, "zoneNames": {}, "factionNames": {}, "questGuide": {"on": True, "at": 0}}
    seen_wish = set()
    for account, db in dbs:
        g = _map(db.get("global"))
        ex = _map(db.get("export"))
        st["accounts"].append(account)
        written = _int(ex.get("writtenAt"))
        if written >= st["writtenAt"]:
            st.update(writtenAt=written, addonVersion=_text(ex.get("addonVersion")),
                      interface=_int(ex.get("interface")), importSeen=_int(ex.get("importSeen")))
        for e in _map(g.get("errors")).values():
            e = _map(e)
            st["errors"].append({"addon": _text(e.get("addon")) or "?", "msg": _text(e.get("msg")),
                                 "count": _int(e.get("count")), "first": _int(e.get("first")),
                                 "last": _int(e.get("last")), "version": _text(e.get("version")),
                                 "stack": _text(e.get("stack")), "account": account})
        for addon, es in _map(g.get("errorStats")).items():
            es = _map(es)
            cur = st["errorStats"].setdefault(addon, {"total": 0, "last": 0, "version": "", "byVersion": {}})
            cur["total"] += _int(es.get("total"))
            if _int(es.get("last")) >= cur["last"]:
                cur["last"], cur["version"] = _int(es.get("last")), _text(es.get("version"))
            for v, n in _map(es.get("byVersion")).items():
                cur["byVersion"][v] = cur["byVersion"].get(v, 0) + _int(n)
        for b in _map(g.get("blocked")).values():
            b = _map(b)
            st["blocked"].append({"addon": _text(b.get("addon")), "func": _text(b.get("func")), "count": _int(b.get("count")),
                                  "last": _int(b.get("last")), "forbidden": bool(b.get("forbidden"))})
        at = _int(g.get("addonsScannedAt"))
        if at >= st["addonsAt"]:
            st["addonsAt"] = at
            st["addons"] = {name: {"title": clean_wow_text(_text(_map(a).get("title"))) or name,
                                   "version": _text(_map(a).get("version")), "loaded": bool(_map(a).get("loaded")),
                                   "enabled": bool(_map(a).get("enabled")), "loadable": bool(_map(a).get("loadable")),
                                   "reason": _text(_map(a).get("reason")), "onDemand": bool(_map(a).get("onDemand")),
                                   "deps": [_text(d) for d in _list(_map(a).get("deps"))],
                                   "missingDeps": [_text(d) for d in _list(_map(a).get("missingDeps"))]}
                            for name, a in _map(g.get("addons")).items()}
        mem = _map(g.get("memory"))
        if _int(mem.get("t")) >= st["memory"]["t"]:
            st["memory"] = {"t": _int(mem.get("t")), "total": _num(mem.get("totalKB")),
                            "list": [{"name": _text(_map(m).get("name")), "kb": _num(_map(m).get("kb")),
                                      "cpu": _num(_map(m).get("cpu"))} for m in _list(mem.get("list"))]}
        for w in _map(g.get("wishlist")).values():
            w = _map(w)
            name = _text(w.get("name")).strip()
            if name and name.lower() not in seen_wish:
                seen_wish.add(name.lower())
                st["wishlist"].append({"name": name, "note": _text(w.get("note")), "added": _int(w.get("added"))})
        for addon, r in _map(g.get("reports")).items():
            r = _map(r)
            if _int(r.get("time")) >= st["reports"].get(addon, {}).get("time", 0):
                st["reports"][addon] = {"time": _int(r.get("time")), "version": _text(r.get("version"))}
        for key, c in _map(db.get("chars")).items():
            ch = _norm_char(key, c, account)
            if ch["lastSeen"] >= st["chars"].get(key, {}).get("lastSeen", -1):
                st["chars"][key] = ch
        for k, it in _map(g.get("items")).items():
            it = _map(it)
            if _text(it.get("name")):
                st["items"][k] = {"name": _text(it.get("name")), "q": _int(it.get("q"), 1)}
        # Questbegleiter: Belohnungen, die ein Charakter im Spiel gesehen hat, und Namen aus dem Spiel
        for qid, r in _map(g.get("questRewards")).items():
            r = _map(r)
            if _int(qid) and _int(r.get("t")) >= _int(st["questRewards"].get(_int(qid), {}).get("t"), -1):
                st["questRewards"][_int(qid)] = {
                    "t": _int(r.get("t")), "money": _int(r.get("m")), "xp": _int(r.get("xp")), "lvl": _int(r.get("lvl")),
                    "items": [{"id": _int(_map(x).get("id")), "n": _int(_map(x).get("n"), 1), "q": _int(_map(x).get("q"), 1),
                               "name": _text(_map(x).get("name"))} for x in _list(r.get("items"))],
                    "choice": [{"id": _int(_map(x).get("id")), "n": _int(_map(x).get("n"), 1), "q": _int(_map(x).get("q"), 1),
                                "name": _text(_map(x).get("name"))} for x in _list(r.get("choice"))],
                    "spell": _text(r.get("spell"))}
        sets = _map(db.get("settings"))
        on = _map(sets.get("modules")).get("QuestGuide")
        at = _int(_map(sets.get("toggledAt")).get("QuestGuide"))
        if at > st["questGuide"]["at"]:
            st["questGuide"] = {"on": on is not False, "at": at}
        names = _map(g.get("names"))
        for key, target in (("zones", "zoneNames"), ("factions", "factionNames")):
            for k, v in _map(names.get(key)).items():
                if _int(k) and _text(v):
                    st[target][_int(k)] = _text(v)
    st["wishlist"].sort(key=lambda w: w["added"])
    return st


def empty_link_state() -> dict:
    return link_state_from_dbs([])


def item_name(state: dict, item_id, fallback: str = "") -> str:
    it = state.get("items", {}).get(str(item_id))
    return it["name"] if it else (fallback or f"#{item_id}")


def item_quality(state: dict, item_id) -> int:
    it = state.get("items", {}).get(str(item_id))
    return it["q"] if it else 1


# ----- Diagnose ------------------------------------------------------------------ #
LOAD_REASONS = {"DEP_MISSING": "Abhängigkeit fehlt", "MISSING": "fehlt", "CORRUPT": "beschädigt",
                "INCOMPATIBLE": "nicht kompatibel", "INTERFACE_VERSION": "veraltet (Interface-Version)",
                "DEP_DISABLED": "Abhängigkeit deaktiviert", "DEP_INCOMPATIBLE": "Abhängigkeit nicht kompatibel",
                "DEP_CORRUPT": "Abhängigkeit beschädigt", "DEP_LOADABLE_FAILED": "Abhängigkeit lädt nicht",
                "BANNED": "gesperrt", "SECURE": "geschützt", "DISABLED": "deaktiviert",
                "NOT_DEMAND_LOADED": "lädt bei Bedarf", "DEMAND_LOADED": "lädt bei Bedarf"}


def error_rows(state: dict, versions: dict[str, str]) -> list[dict]:
    """Pro Addon: Fehler gesamt und in der jetzt installierten Version (versions: Ordner -> Version)."""
    rows = []
    for addon, es in state.get("errorStats", {}).items():
        cur = clean_wow_text(versions.get(addon, ""))
        in_cur = sum(n for v, n in es["byVersion"].items() if cur and clean_wow_text(v) == cur)
        errs = sorted((e for e in state["errors"] if e["addon"] == addon), key=lambda e: (e["count"], e["last"]), reverse=True)
        rows.append({"addon": addon, "total": es["total"], "current": in_cur, "version": cur, "last": es["last"],
                     "errors": errs, "byVersion": es["byVersion"], "installed": addon in versions})
    rows.sort(key=lambda r: (r["current"] > 0, r["last"]), reverse=True)
    return rows


def load_problems(state: dict) -> list[dict]:
    out = []
    for name, a in state.get("addons", {}).items():
        if name.startswith("Blizzard_") or not a["enabled"] or a["loaded"] or a["onDemand"]:
            continue
        if a["reason"] in ("DEMAND_LOADED", "NOT_DEMAND_LOADED", "DISABLED"):
            continue
        out.append({"name": name, "title": a["title"], "reason": a["reason"], "missing": a["missingDeps"]})
    out.sort(key=lambda p: p["name"].lower())
    return out


# ----- Charaktere, Taschen, Rezepte ------------------------------------------------ #
def char_items(ch: dict) -> dict[str, int]:
    out = dict(ch["bags"])
    for k, n in ch["bank"].items():
        out[k] = out.get(k, 0) + n
    return out


def find_items(state: dict, text: str, limit: int = 200) -> list[dict]:
    """Wer hat was? Suche nach Item-Name (Teiltext) oder ID in Taschen und Bank aller Charaktere."""
    text = text.strip().lower()
    if not text:
        return []
    out = []
    for ch in state.get("chars", {}).values():
        for where in ("bags", "bank"):
            for k, n in ch[where].items():
                name = item_name(state, k)
                if text == k or text in name.lower():
                    out.append({"char": ch["name"], "key": ch["key"], "where": where, "id": k, "name": name,
                                "q": item_quality(state, k), "n": n})
    out.sort(key=lambda r: (r["name"].lower(), r["char"].lower()))
    return out[:limit]


def shopping_list(state: dict, crafter: str, prof: str, recipe: str, qty: int) -> dict:
    """Zutaten für qty Stück: Bedarf, beim Hersteller vorhanden, bei anderen Charakteren, fehlt noch."""
    ch = state["chars"][crafter]
    e = ch["recipes"][prof]["list"][recipe]
    crafts = max(1, -(-max(1, qty) // max(1, e["made"])))
    own = char_items(ch)
    rows = []
    for r in e["r"]:
        need = r["n"] * crafts
        key = str(r["id"]) if r["id"] else ""
        have = own.get(key, 0) if key else None
        others = []
        if key:
            for other in state["chars"].values():
                if other["key"] == crafter:
                    continue
                n = char_items(other).get(key, 0)
                if n:
                    others.append({"char": other["name"], "n": n, "bank": other["bank"].get(key, 0)})
        others.sort(key=lambda o: o["n"], reverse=True)
        elsewhere = sum(o["n"] for o in others)
        rows.append({"id": r["id"], "name": item_name(state, r["id"], r["name"]) if r["id"] else (r["name"] or "?"),
                     "q": item_quality(state, r["id"]) if r["id"] else 1, "need": need, "have": have,
                     "others": others, "missing": max(0, need - (have or 0) - elsewhere) if have is not None else None,
                     "transfer": max(0, min(need - (have or 0), elsewhere)) if have is not None else 0})
    return {"crafts": crafts, "made": e["made"], "item": e["item"], "rows": rows,
            "product": item_name(state, e["item"], recipe) if e["item"] else recipe}
