"""Leeroylink: Spielsitzungen übernehmen, dauerhaft speichern und robust lesen."""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path

from .config import config_dir
from .lua import (
    LINK_ADDON,
    LINK_DB,
    LuaParseError,
    OLD_LINK_ADDON,
    OLD_LINK_DB,
    SESSION_FORMAT,
    parse_lua_savedvariables,
)


# ----- Werte robust lesen ------------------------------------------------- #
def _num(v, default=0):
    if isinstance(v, bool):
        return default
    if isinstance(v, (int, float)):
        return v
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _int(v, default=0) -> int:
    return int(round(_num(v, default)))


def _text(v) -> str:
    return v if isinstance(v, str) else ("" if v is None else str(v))


def _map(v) -> dict:
    if isinstance(v, dict):
        return {str(k): x for k, x in v.items()}
    if isinstance(v, list):
        return {str(i + 1): x for i, x in enumerate(v)}
    return {}


def _list(v) -> list:
    if isinstance(v, list):
        return v
    if isinstance(v, dict):
        try:
            return [v[k] for k in sorted(v, key=lambda k: _num(k))]
        except TypeError:
            return list(v.values())
    return []


def _counts(v) -> dict:
    return {k: _num(x) for k, x in _map(v).items() if _num(x)}


def _items(v) -> dict:
    out = {}
    for k, e in _map(v).items():
        e = _map(e)
        if _int(e.get("n")) <= 0:
            continue
        out[k] = {"name": _text(e.get("name")) or f"#{k}", "q": _int(e.get("q"), 1), "n": _int(e.get("n")),
                  "v": _int(e.get("v")), "p": _int(e.get("p"))}
    return out


def normalize_session(raw, flavor: str = "", account: str = "") -> dict | None:
    """Bringt eine Sitzung aus dem Addon in eine feste Form (JSON-tauglich, alle Felder vorhanden)."""
    r = _map(raw)
    char = _text(r.get("char"))
    start = _int(r.get("start"))
    if not char or start <= 0:
        return None
    g = lambda key: _map(r.get(key))   # noqa: E731
    tm, lv, xp, mo, qu, lo, cb, ki, mv, pr, ex = (g(k) for k in (
        "time", "level", "xp", "money", "quests", "loot", "combat", "kills", "move", "prof", "explore"))
    name, _, realm = char.partition("-")
    s = {
        "format": SESSION_FORMAT,
        "id": _text(r.get("id")) or f"{char}:{start}",
        "char": char, "name": _text(r.get("name")) or name, "realm": _text(r.get("realm")) or realm,
        "class": _text(r.get("class")), "race": _text(r.get("race")), "faction": _text(r.get("faction")),
        "addon": _text(r.get("addon")), "interface": _int(r.get("interface")),
        "flavor": flavor, "account": account,
        "start": start, "stop": max(start, _int(r.get("stop"), start)), "saved": _int(r.get("saved")),
        "played": _int(r.get("played")), "resumes": _int(r.get("resumes")),
        "playedTotal": _int(r.get("playedTotal")),
        "time": {k: _int(tm.get(k)) for k in ("combat", "afk", "rest", "dead", "taxi", "instance")},
        "level": {"from": _int(lv.get("from")), "to": _int(lv.get("to")),
                  "ups": [{"l": _int(_map(u).get("l")), "t": _int(_map(u).get("t"))} for u in _list(lv.get("ups"))]},
        "xp": {k: _int(xp.get(k)) for k in ("total", "kills", "quests", "explore", "other", "killsN", "combat")},
        "money": {"start": _int(mo.get("start")), "stop": _int(mo.get("stop")), "income": _int(mo.get("income")),
                  "spent": _int(mo.get("spent")), "gain": _counts(mo.get("gain")), "spend": _counts(mo.get("spend"))},
        "quests": {
            "accepted": [{"id": _int(_map(q).get("id")), "title": _text(_map(q).get("title")),
                          "lvl": _int(_map(q).get("lvl")), "t": _int(_map(q).get("t"))} for q in _list(qu.get("accepted"))],
            "done": [{"id": _int(_map(q).get("id")), "title": _text(_map(q).get("title")),
                      "xp": _int(_map(q).get("xp")), "money": _int(_map(q).get("money")),
                      "t": _int(_map(q).get("t")), "zone": _text(_map(q).get("zone"))} for q in _list(qu.get("done"))],
            "abandoned": [{"title": _text(_map(q).get("title")), "t": _int(_map(q).get("t"))}
                          for q in _list(qu.get("abandoned"))],
        },
        "loot": {"n": _int(lo.get("n")), "value": _int(lo.get("value")),
                 "q": {str(_int(k) if str(k).lstrip("-").isdigit() else k): _int(v) for k, v in _map(lo.get("q")).items()},
                 "items": _items(lo.get("items")),
                 "notable": [{"id": _int(_map(e).get("id")), "name": _text(_map(e).get("name")),
                              "q": _int(_map(e).get("q"), 2), "n": _int(_map(e).get("n"), 1),
                              "t": _int(_map(e).get("t")), "zone": _text(_map(e).get("zone")),
                              "src": _text(_map(e).get("src")), "boss": _text(_map(e).get("boss"))}
                             for e in _list(lo.get("notable"))]},
        "combat": {
            **{k: _int(cb.get(k)) for k in ("dmg", "dmgPet", "hits", "crits", "maxHit", "heal", "over", "maxHeal",
                                             "taken", "maxTaken", "healTaken", "castN", "buffN", "debuffN",
                                             "buffsGiven", "interrupts", "dispels")},
            **{k: _text(cb.get(k)) for k in ("maxHitSpell", "maxHealSpell", "maxTakenBy", "maxTakenSpell", "err")},
            "maxHitCrit": bool(cb.get("maxHitCrit")),
            "spells": {k: [_int(x) for x in (_list(v) + [0, 0, 0])[:3]] for k, v in _map(cb.get("spells")).items()},
            "heals": {k: [_int(x) for x in (_list(v) + [0, 0, 0])[:3]] for k, v in _map(cb.get("heals")).items()},
            "casts": _counts(cb.get("casts")), "buffs": _counts(cb.get("buffs")), "debuffs": _counts(cb.get("debuffs")),
            "takenBy": _counts(cb.get("takenBy")), "env": _counts(cb.get("env")),
            "swings": {k: _int(_map(cb.get("swings")).get(k))
                       for k in ("n", "hit", "crit", "miss", "dodge", "parry", "block", "glance")},
            "swingsTaken": {k: _int(_map(cb.get("swingsTaken")).get(k))
                            for k in ("n", "hit", "crit", "miss", "dodge", "parry", "block")},
        },
        "kills": {**{k: _int(ki.get(k)) for k in ("n", "normal", "elite", "rare", "rareelite", "boss", "players", "blows")},
                  "mobs": {k: {"name": _text(_map(e).get("name")) or "?", "n": _int(_map(e).get("n")),
                               "c": _text(_map(e).get("c")) or "normal"} for k, e in _map(ki.get("mobs")).items()}},
        "bosses": [{"name": _text(_map(b).get("name")), "t": _int(_map(b).get("t")), "inst": _text(_map(b).get("inst")),
                    "sec": _int(_map(b).get("sec"))} for b in _list(r.get("bosses"))],
        "wipes": _int(r.get("wipes")),
        "deaths": [{k: (_int(_map(d).get(k)) if k in ("t", "lvl") else _text(_map(d).get(k)))
                    for k in ("t", "zone", "sub", "by", "spell", "lvl", "inst")} for d in _list(r.get("deaths"))],
        "deathN": _int(r.get("deathN")),
        "move": {k: _num(mv.get(k)) for k in ("foot", "mount", "swim", "taxi", "ghost", "steps", "jumps")},
        "prof": {
            "skills": {k: {"h": _text(_map(e).get("h")), "a": _int(_map(e).get("a")), "b": _int(_map(e).get("b")),
                           "m": _int(_map(e).get("m"))} for k, e in _map(pr.get("skills")).items()},
            "ups": _int(pr.get("ups")),
            "crafted": _items(pr.get("crafted")),
            "gathered": {k: _items(v) for k, v in _map(pr.get("gathered")).items() if _items(v)},
            "learned": [_text(_map(e).get("name")) for e in _list(pr.get("learned")) if _map(e).get("name")],
        },
        "spellsLearned": [_text(_map(e).get("name")) for e in _list(r.get("spellsLearned")) if _map(e).get("name")],
        "explore": {
            "found": [{"name": _text(_map(e).get("name")), "xp": _int(_map(e).get("xp")), "zone": _text(_map(e).get("zone")),
                       "t": _int(_map(e).get("t"))} for e in _list(ex.get("found"))],
            "zones": {k: {"sec": _int(_map(z).get("sec")), "subs": sorted(str(x) for x in _map(_map(z).get("subs"))),
                          "xp": _int(_map(z).get("xp")), "money": _int(_map(z).get("money")),
                          "kills": _int(_map(z).get("kills"))}
                      for k, z in _map(ex.get("zones")).items()},
        },
        "runs": [{"name": _text(_map(x).get("name")), "kind": _text(_map(x).get("kind")), "diff": _text(_map(x).get("diff")),
                  "size": _int(_map(x).get("size")), "start": _int(_map(x).get("start")), "stop": _int(_map(x).get("stop")),
                  "kills": _int(_map(x).get("kills")), "bosses": [_text(b) for b in _list(_map(x).get("bosses"))],
                  "wipes": _int(_map(x).get("wipes")), "deaths": _int(_map(x).get("deaths")), "xp": _int(_map(x).get("xp")),
                  "money": _int(_map(x).get("money")), "loot": _int(_map(x).get("loot")), "group": _int(_map(x).get("group")),
                  "drops": [{"id": _int(_map(d).get("id")), "name": _text(_map(d).get("name")), "q": _int(_map(d).get("q"), 2),
                             "n": _int(_map(d).get("n"), 1), "boss": _text(_map(d).get("boss"))}
                            for d in _list(_map(x).get("drops"))]}
                 for x in _list(r.get("runs"))],
        "rep": _counts(r.get("rep")),
        "xpNow": _int(r.get("xpNow")), "xpMax": _int(r.get("xpMax")),
        "legacy": False,
    }
    # Kill-XP: Neuere Clients schützen die Chatzeile im Kampf – dann zählt die XP, die im Kampf kam (Link 0.3.1)
    x = s["xp"]
    if x["combat"] > x["kills"]:
        x["kills"] = x["combat"]
        x["other"] = max(0, x["total"] - x["kills"] - x["quests"] - x["explore"])
    # Ältere Sitzungen aus WoW Forever (kein Kampflog, Link ≤ 0.3.1): Kills aus den XP-Zeilen nehmen
    k = s["kills"]
    if k["n"] == 0 and x["killsN"] > 0:
        k["n"] = k["normal"] = x["killsN"]
    # Spielzeit fehlt (z. B. Absturz vor dem ersten Speichern): Zeitraum nehmen
    if s["played"] <= 0:
        s["played"] = max(0, s["stop"] - s["start"])
    return s


def legacy_session(raw, flavor: str = "", account: str = "") -> dict | None:
    """Sitzungen aus Leeroylink 0.1 (nur Dauer, Gold, XP, Kills)."""
    r = _map(raw)
    s = normalize_session({"char": r.get("char"), "start": r.get("start"),
                           "stop": _int(r.get("start")) + _int(r.get("duration")),
                           "saved": _int(r.get("start")) + _int(r.get("duration")),
                           "played": r.get("duration"), "level": {"from": r.get("level"), "to": r.get("level")},
                           "xp": {"total": r.get("xp")}, "kills": {"n": r.get("kills"), "normal": r.get("kills")}},
                          flavor, account)
    if s:
        s["legacy"] = True
        s["money"]["stop"] = s["money"]["start"] + _int(r.get("money"))
        s["money"]["net"] = _int(r.get("money"))
    return s


def session_net_money(s: dict) -> int:
    m = s.get("money", {})
    if "net" in m:
        return _int(m["net"])
    return _int(m.get("stop")) - _int(m.get("start"))


# ----- Dateien ------------------------------------------------------------ #
def link_savedvariables(root: str, flavor: str) -> list[Path]:
    """Alle SavedVariables-Dateien des Addons in dieser Spielversion (eine pro WoW-Account)."""
    base = Path(root) / flavor / "WTF" / "Account"
    if not root or not flavor or not base.is_dir():
        return []
    out = []
    for acc in sorted(p for p in base.iterdir() if p.is_dir()):
        for name in (LINK_ADDON, OLD_LINK_ADDON):      # noch nicht umgestellt: die alte Datei lesen
            f = acc / "SavedVariables" / f"{name}.lua"
            if f.is_file():
                out.append(f)
                break
    return out


def link_signature(root: str, flavor: str) -> tuple:
    out = []
    for p in link_savedvariables(root, flavor):
        try:
            st = p.stat()
            out.append((str(p), st.st_size, st.st_mtime_ns))
        except OSError:
            pass
    return tuple(out)


def sessions_dir(flavor: str) -> Path:
    name = re.sub(r"[^A-Za-z0-9]+", "", flavor) or "wow"
    return config_dir() / "sessions" / name


def _safe_name(text: str) -> str:
    return re.sub(r"[^\w.-]+", "_", text, flags=re.UNICODE).strip("_")[:60] or "char"


def session_path(flavor: str, s: dict) -> Path:
    stamp = datetime.fromtimestamp(s["start"]).strftime("%Y%m%d-%H%M%S")
    return sessions_dir(flavor) / f"{stamp}_{_safe_name(s['char'])}.json"


def read_link_db(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    values = parse_lua_savedvariables(text)
    db = values.get(LINK_DB)
    if not isinstance(db, dict):
        db = values.get(OLD_LINK_DB)
    return db if isinstance(db, dict) else {}


def sessions_in_db(db: dict, flavor: str = "", account: str = "") -> list[dict]:
    g = _map(db.get("global"))
    out = []
    for raw in _list(g.get("sessionLog")):
        if _int(_map(raw).get("v")) >= SESSION_FORMAT:
            s = normalize_session(raw, flavor, account)
            if s:
                out.append(s)
    for raw in _list(g.get("sessions")):          # alte Version 0.1
        s = legacy_session(raw, flavor, account)
        if s:
            out.append(s)
    return out


_SESSION_CACHE: dict[str, tuple[int, dict]] = {}     # Pfad -> (Änderungszeit, Sitzung)


def load_sessions(flavor: str) -> list[dict]:
    """Alle gespeicherten Sitzungen dieser Spielversion, neueste zuerst (unveränderte Dateien aus dem Cache)."""
    out = []
    d = sessions_dir(flavor)
    if not d.is_dir():
        return out
    for p in d.glob("*.json"):
        key = str(p)
        try:
            mtime = p.stat().st_mtime_ns
            cached = _SESSION_CACHE.get(key)
            if cached and cached[0] == mtime:
                s = cached[1]
            else:
                s = json.loads(p.read_text(encoding="utf-8"))
                _SESSION_CACHE[key] = (mtime, s)
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(s, dict) and s.get("char") and s.get("start"):
            s["_file"] = key
            k, x = s.get("kills"), s.get("xp")       # schon archivierte Forever-Sitzungen ohne Kills
            if isinstance(k, dict) and isinstance(x, dict) and not k.get("n") and x.get("killsN"):
                k["n"] = k["normal"] = x["killsN"]
            out.append(s)
    out.sort(key=lambda s: s.get("start", 0), reverse=True)
    return out


def save_session(flavor: str, s: dict) -> Path:
    p = session_path(flavor, s)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    data = {k: v for k, v in s.items() if not k.startswith("_")}
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)
    return p


def _deleted_file(flavor: str) -> Path:
    return sessions_dir(flavor) / "deleted.txt"


def deleted_session_ids(flavor: str) -> set[str]:
    try:
        return {l.strip() for l in _deleted_file(flavor).read_text(encoding="utf-8").splitlines() if l.strip()}
    except OSError:
        return set()


def delete_session(flavor: str, s: dict) -> None:
    """Entfernt eine Sitzung aus dem Archiv und merkt sich die ID, damit sie nicht wieder importiert wird."""
    p = Path(s.get("_file") or session_path(flavor, s))
    p.unlink(missing_ok=True)
    f = _deleted_file(flavor)
    f.parent.mkdir(parents=True, exist_ok=True)
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(s["id"] + "\n")


def import_link_sessions(root: str, flavor: str) -> dict:
    """Übernimmt neue und fortgesetzte Sitzungen aus allen Accounts. Schreibt nie in WoW-Dateien."""
    from .linkdata import link_state_from_dbs
    result = {"new": [], "updated": [], "errors": [], "files": 0}
    deleted = deleted_session_ids(flavor)
    dbs = []
    for path in link_savedvariables(root, flavor):
        account = path.parent.parent.name
        try:
            db = read_link_db(path)
        except (OSError, LuaParseError) as e:
            result["errors"].append(f"{path.name} ({account}): {e}")
            continue
        result["files"] += 1
        dbs.append((account, db))
        for s in sessions_in_db(db, flavor, account):
            if s["id"] in deleted:
                continue
            target = session_path(flavor, s)
            old = None
            if target.is_file():
                try:
                    old = json.loads(target.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    old = None
            if old is not None:
                if s["legacy"] and not old.get("legacy"):
                    continue                      # nie eine volle Sitzung durch die alte Kurzform ersetzen
                if (s["saved"], s["played"]) <= (_int(old.get("saved")), _int(old.get("played"))):
                    continue
            s["imported"] = int(time.time())
            if old is not None and old.get("imported_first"):
                s["imported_first"] = old["imported_first"]
            else:
                s["imported_first"] = s["imported"]
            save_session(flavor, s)
            (result["updated"] if old is not None else result["new"]).append(s)
    result["state"] = link_state_from_dbs(dbs)
    return result
