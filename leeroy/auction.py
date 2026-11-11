"""Auktionshaus: Preise aus Auctionator."""
from __future__ import annotations

import re
import struct
from datetime import datetime, timedelta
from pathlib import Path

from .config import log
from .lua import LuaParseError, parse_lua_savedvariables
from .sessions import _int
from .questdb import QuestDB, QuestDBError, cbor_decode


# --------------------------------------------------------------------------- #
# Auktionshaus: Preise aus Auctionator (dessen SavedVariables). Auctionator speichert seit
# Datenbankversion 8 je Realm einen CBOR-Block in einem Lua-String: Gegenstand → {m = aktueller
# Mindestpreis, h/l = höchster/niedrigster Mindestpreis je Tag, a = Anzahl je Tag}.
# Tage zählt Auctionator ab dem 1.1.2020. Leeroyzan liest nur.
# --------------------------------------------------------------------------- #
AUC_ADDON = "Auctionator"
AUC_DAY0 = datetime(2020, 1, 1)
ITEM_CLASSES = {0: "Verbrauchbar", 1: "Behälter", 2: "Waffe", 3: "Edelstein", 4: "Rüstung", 5: "Reagenz", 6: "Projektil",
                7: "Handwerkswaren", 8: "Gegenstandsverbesserung", 9: "Rezept", 10: "Währung", 11: "Köcher", 12: "Quest",
                13: "Schlüssel", 15: "Verschiedenes", 16: "Glyphe", 17: "Kampfhaustier"}


def auc_day_date(day: int) -> datetime:
    return AUC_DAY0 + timedelta(days=int(day))


def auctionator_savedvariables(root: str, flavor: str) -> list[Path]:
    base = Path(root) / flavor / "WTF" / "Account"
    if not root or not flavor or not base.is_dir():
        return []
    return sorted(p for p in base.glob(f"*/SavedVariables/{AUC_ADDON}.lua") if p.is_file())


_LUA_BYTE_ESC = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("a"): 7, ord("b"): 8, ord("f"): 12, ord("v"): 11, 10: 10, 13: 10}


def _lua_bytes(buf: bytes, i: int) -> tuple[bytes, int]:
    """Lua-Stringliteral ab buf[i] == '"' als rohe Bytes (Auctionator legt Binärdaten darin ab)."""
    if buf[i:i + 1] != b'"':
        raise LuaParseError("string expected")
    i += 1
    out = bytearray()
    n = len(buf)
    while i < n:
        c = buf[i]
        if c == 0x22:
            return bytes(out), i + 1
        if c == 0x5C:
            e = buf[i + 1]
            if 0x30 <= e <= 0x39:
                j = i + 1
                while j < i + 4 and 0x30 <= buf[j] <= 0x39:
                    j += 1
                out.append(int(buf[i + 1:j]) & 0xFF)
                i = j
                continue
            if e == 13 and buf[i + 2:i + 3] == b"\n":             # Zeilenende \r\n nach dem Backslash
                i += 1
            out.append(_LUA_BYTE_ESC.get(e, e))
            i += 2
            continue
        out.append(c)
        i += 1
    raise LuaParseError("unterminated string")


def _lua_table_entries(buf: bytes, start: int):
    """Die Einträge einer Lua-Tabelle (oberste Ebene) ab buf[start] == '{': (Schlüssel, Rohwert)."""
    i = start + 1
    n = len(buf)
    while i < n:
        while i < n and buf[i] in b" \t\r\n,":
            i += 1
        if i >= n or buf[i] == 0x7D:                        # }
            return
        if buf[i:i + 2] != b"[\"" and buf[i:i + 1] != b"[":
            raise LuaParseError("key expected")
        if buf[i + 1:i + 2] == b'"':
            key, i = _lua_bytes(buf, i + 1)
            key = key.decode("utf-8", "replace")
        else:
            j = buf.index(b"]", i)
            key = buf[i + 1:j].decode("ascii", "replace")
            i = j
        i = buf.index(b"=", i) + 1
        while buf[i] in b" \t":
            i += 1
        if buf[i] == 0x22:
            val, i = _lua_bytes(buf, i)
        elif buf[i] == 0x7B:                                 # Tabelle: bis zur passenden Klammer
            depth, j = 0, i
            while j < n:
                c = buf[j]
                if c == 0x22:
                    _, j = _lua_bytes(buf, j)
                    continue
                if c == 0x7B:
                    depth += 1
                elif c == 0x7D:
                    depth -= 1
                    if depth == 0:
                        j += 1
                        break
                j += 1
            val, i = ("table", buf[i:j]), j
        else:
            j = i
            while j < n and buf[j] not in b",\r\n}":
                j += 1
            val, i = buf[i:j].strip().decode("ascii", "replace"), j
        yield key, val


def _auc_days(v) -> dict[int, int]:
    if isinstance(v, dict):
        return {_int(k): _int(x) for k, x in v.items() if _int(k) and isinstance(x, (int, float))}
    return {}


def read_auctionator(path: Path) -> dict:
    """{"realms": {Realm: {Schlüssel: Eintrag}}, "vendor": {ID: Händlerpreis}, "version": n}"""
    raw = path.read_bytes()
    out = {"realms": {}, "vendor": {}, "version": 0}
    m = re.search(rb"(?m)^AUCTIONATOR_PRICE_DATABASE = \{", raw)
    if m:
        for key, val in _lua_table_entries(raw, m.end() - 1):
            if key == "__dbversion":
                out["version"] = _int(val)
                continue
            data = None
            if isinstance(val, bytes):                         # Version 8: CBOR
                try:
                    data = cbor_decode(val)
                except (QuestDBError, IndexError, struct.error, ValueError) as e:
                    log.warning("Auctionator %s: %s", key, e)
            elif isinstance(val, tuple):                       # ältere Versionen: Lua-Tabelle
                try:
                    data = parse_lua_savedvariables("X = " + val[1].decode("utf-8", "replace")).get("X")
                except LuaParseError as e:
                    log.warning("Auctionator %s: %s", key, e)
            if isinstance(data, dict):
                entries = {str(k): v for k, v in data.items() if isinstance(v, dict) and "m" in v}
                if entries:
                    out["realms"][key] = entries
    m = re.search(rb"(?m)^AUCTIONATOR_VENDOR_PRICE_CACHE = \{", raw)
    if m:
        for key, val in _lua_table_entries(raw, m.end() - 1):
            if isinstance(val, str):
                try:
                    out["vendor"][str(key)] = float(val)
                except ValueError:
                    pass
    return out


def load_auctionator(root: str, flavor: str) -> dict:
    """Alle Accounts zusammen; je Realm gilt der neueste Datenstand."""
    out = {"realms": {}, "vendor": {}, "files": 0, "mtime": 0, "errors": []}
    for path in auctionator_savedvariables(root, flavor):
        try:
            d = read_auctionator(path)
        except (OSError, LuaParseError, ValueError) as e:
            out["errors"].append(f"{path.parent.parent.name}: {e}")
            continue
        out["files"] += 1
        out["mtime"] = max(out["mtime"], path.stat().st_mtime)
        out["vendor"].update(d["vendor"])
        for realm, entries in d["realms"].items():
            cur = out["realms"].get(realm)
            if cur is None or _auc_latest(entries) >= _auc_latest(cur):
                out["realms"][realm] = entries
    return out


def _auc_latest(entries: dict) -> int:
    return max((max(_auc_days(e.get("h")) or [0]) for e in entries.values()), default=0)


def auction_row(key: str, e: dict, vendor: dict) -> dict:
    """Ein Gegenstand mit Preisverlauf: aktueller Preis, Tiefst-/Höchstwerte, Ø der letzten 14 Tage."""
    m = re.match(r"(?:[a-z]+:)?(\d+)", key)
    iid = int(m.group(1)) if m else 0
    high, low, avail = _auc_days(e.get("h")), _auc_days(e.get("l")), _auc_days(e.get("a"))
    days = sorted(set(high) | set(low))
    hist = [(d, low.get(d, high.get(d, 0)), high.get(d, low.get(d, 0)), avail.get(d, 0)) for d in days]
    last = days[-1] if days else 0
    recent = [lo for d, lo, _, _ in hist if d > last - 14 and lo > 0]
    avg = sum(recent) / len(recent) if recent else 0
    price = _int(e.get("m"))
    older = [lo for d, lo, _, _ in hist if d > last - 15 and d < last and lo > 0]
    ref = sum(older) / len(older) if older else 0
    return {"key": key, "id": iid, "price": price, "last": last, "avail": avail.get(last, 0), "hist": hist,
            "avg": avg, "change": (price / ref - 1) if ref and price else None, "days": len(days),
            "vendor": int(round(vendor.get(str(iid), 0)))}


def item_details(iid: int, qdb: "QuestDB | None", cm: dict | None, state: dict, locale: str) -> dict:
    """Name, Qualität, Klasse und Stufe eines Gegenstands aus allem, was Leeroyzan kennt."""
    learned = state.get("items", {}).get(str(iid))
    full = ((cm or {}).get("all_items") or {}).get(str(iid))
    row = qdb.row("Item", iid, 16) if qdb else None
    name = (qdb.localized("Item", iid, 1, locale) if qdb else None) or (learned or {}).get("name") or \
        (full[0] if full else "") or (row or {}).get(1) or f"#{iid}"
    q = full[1] if full else (learned or {}).get("q", 1)
    cls = full[2] if full else _int((row or {}).get(12), -1)
    return {"name": str(name), "q": q if isinstance(q, int) else 1, "cls": cls,
            "ilvl": full[4] if full else _int((row or {}).get(9)), "req": full[5] if full else _int((row or {}).get(10)),
            "sell": full[6] if full else 0}


def owned_counts(state: dict) -> dict[int, int]:
    """Wie viel davon deine Charaktere in Taschen und Bank haben (Leeroylink)."""
    out: dict[int, int] = {}
    for ch in state.get("chars", {}).values():
        for where in ("bags", "bank"):
            for k, n in ch[where].items():
                if str(k).isdigit():
                    out[int(k)] = out.get(int(k), 0) + n
    return out


def auction_rows(data: dict, qdb: "QuestDB | None", cm: dict | None, state: dict, locale: str) -> dict[str, list[dict]]:
    """Alle Zeilen je Realm mit Gegenstandsdetails – läuft im Hintergrund, nicht im Fenster-Thread."""
    owned = owned_counts(state)
    out: dict[str, list[dict]] = {}
    for realm, entries in data.get("realms", {}).items():
        rows = []
        for key, e in entries.items():
            r = auction_row(key, e, data["vendor"])
            r.update(item_details(r["id"], qdb, cm, state, locale))
            r["owned"] = owned.get(r["id"], 0)
            rows.append(r)
        out[realm] = rows
    return out
