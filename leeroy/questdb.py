"""Questdatenbank aus QuestieDB und Questbelohnungen aus cMaNGOS."""
from __future__ import annotations

import base64
import bisect
import gzip
import hashlib
import json
import os
import re
import shutil
import struct
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path

from .constants import USER_AGENT
from .i18n import cur_lang, tr
from .config import config_dir, log
from .files import addons_dir, disabled_dir
from .github import http_get
from .linkaddon import installed_link_dir
from .linkdata import lua_string


# --------------------------------------------------------------------------- #
# Questdatenbank: liest QuestieDB (das Daten-Addon von Questie) direkt aus dem Addon-Ordner.
# QuestieDB legt seine Daten "gebacken" in den Metadaten seiner .toc ab: Base64 → (zlib) → CBOR.
# Leeroyzan liest nur; im Spiel braucht Leeroylink QuestieDB dadurch nicht geladen zu haben.
# --------------------------------------------------------------------------- #
QDB_ADDON = "QuestieDB"
QDB_FLAVORS = {"_forever_": "Forever", "_classic_era_": "Era", "_classic_": "Classic", "_anniversary_": "Era",
               "_classic_era_ptr_": "Era", "_retail_": "Retail"}
QDB_LOCALES = ("deDE", "enUS")
# Rassen-Bits (requiredRaces): Allianz / Horde
RACES_ALLIANCE = 1 | 4 | 8 | 64 | 1024 | 2097152 | 16777216
RACES_HORDE = 2 | 16 | 32 | 128 | 256 | 512 | 33554432
QUEST_SORTS_DE = {"WARRIOR": "Krieger", "PALADIN": "Paladin", "HUNTER": "Jäger", "ROGUE": "Schurke", "PRIEST": "Priester",
                  "SHAMAN": "Schamane", "MAGE": "Magier", "WARLOCK": "Hexenmeister", "DRUID": "Druide",
                  "FIRST_AID": "Erste Hilfe", "COOKING": "Kochkunst", "FISHING": "Angeln", "ALCHEMY": "Alchemie",
                  "BLACKSMITHING": "Schmiedekunst", "LEATHERWORKING": "Lederverarbeitung", "TAILORING": "Schneiderei",
                  "ENGINEERING": "Ingenieurskunst", "HERBALISM": "Kräuterkunde", "REPUTATION": "Ruf", "SPECIAL": "Besonderes",
                  "SEASONAL": "Saisonal", "LEGENDARY": "Legendär", "EPIC": "Episch", "TREASURE_MAP": "Schatzkarte"}


class QuestDBError(RuntimeError):
    pass


def cbor_decode(data: bytes):
    """Kleiner CBOR-Decoder (RFC 8949) – genug für QuestieDB: Zahlen, Texte, Listen, Tabellen."""
    pos = 0
    brk = object()

    def head():
        nonlocal pos
        b = data[pos]
        pos += 1
        mt, ai = b >> 5, b & 31
        if ai < 24:
            return mt, ai, b
        if ai in (24, 25, 26, 27):
            n = 1 << (ai - 24)
            v = int.from_bytes(data[pos:pos + n], "big")
            pos += n
            return mt, v, b
        if ai == 31:
            return mt, None, b
        raise QuestDBError(f"CBOR: unbekanntes Format {b:#x}")

    def item():
        nonlocal pos
        b = data[pos]
        if b == 0xFF:
            pos += 1
            return brk
        if b in (0xF9, 0xFA, 0xFB):
            pos += 1
            n, fmt = {0xF9: (2, ">e"), 0xFA: (4, ">f"), 0xFB: (8, ">d")}[b]
            v = struct.unpack(fmt, data[pos:pos + n])[0]
            pos += n
            return v
        mt, v, _ = head()
        if mt == 0:
            return v
        if mt == 1:
            return -1 - v
        if mt in (2, 3):
            if v is None:
                parts = []
                while (x := item()) is not brk:
                    parts.append(x.encode("utf-8") if isinstance(x, str) else x)
                raw = b"".join(parts)
            else:
                raw = data[pos:pos + v]
                pos += v
            return raw.decode("utf-8", "replace")          # WoW behandelt beides als Text
        if mt == 4:
            out = []
            if v is None:
                while (x := item()) is not brk:
                    out.append(x)
            else:
                for _ in range(v):
                    out.append(item())
            return out
        if mt == 5:
            out = {}
            if v is None:
                while (k := item()) is not brk:
                    out[k] = item()
            else:
                for _ in range(v):
                    k = item()
                    out[k] = item()
            return out
        if mt == 6:
            return item()
        if mt == 7:
            return {20: False, 21: True}.get(v)
        raise QuestDBError("CBOR: unbekannter Typ")

    return item()


def _slot(t, i):
    """Feld i (1-basiert, wie in Lua) aus einer CBOR-Liste oder -Tabelle."""
    if isinstance(t, list):
        return t[i - 1] if 0 < i <= len(t) else None
    if isinstance(t, dict):
        return t.get(i)
    return None


def _ids(v) -> list[int]:
    if isinstance(v, dict):
        v = [v[k] for k in sorted(v) if isinstance(k, int)]
    return [int(x) for x in (v or []) if isinstance(x, (int, float)) and x]


def _pretty_enum(name: str) -> str:
    return " ".join(w.capitalize() if w not in ("OF", "THE") else w.lower() for w in name.split("_"))


def find_questie_toc(root: str, flavor: str) -> Path | None:
    """Die passende QuestieDB-.toc (gebackene Daten) für diese Spielversion."""
    if not root or not flavor:
        return None
    want = QDB_FLAVORS.get(flavor, "")
    for base in (addons_dir(root, flavor), disabled_dir(root, flavor)):
        d = base / QDB_ADDON
        if not d.is_dir():
            continue
        tocs = sorted(d.glob(f"{QDB_ADDON}*.toc"))
        if want and (d / f"{QDB_ADDON}_{want}.toc") in tocs:
            return d / f"{QDB_ADDON}_{want}.toc"
        for t in tocs:
            try:
                with t.open(encoding="utf-8", errors="replace") as f:
                    head = f.read(4000)
            except OSError:
                continue
            if "X-Mode: baked" in head and (not want or f"X-Flavor: {want}" in head):
                return t
    return None


def game_locale(root: str, flavor: str) -> str:
    """Sprache des Spiels (WTF/Config.wtf), sonst die von Leeroyzan."""
    try:
        text = (Path(root) / flavor / "WTF" / "Config.wtf").read_text(encoding="utf-8", errors="replace")
        m = re.search(r'^SET textLocale "(\w{4})"', text, re.M)
        if m:
            return m.group(1)
    except OSError:
        pass
    return "deDE" if cur_lang() == "de" else "enUS"


class QuestDB:
    """Die Questdatenbank aus QuestieDB – Quests, NPCs, Objekte, Gegenstände, Quest-XP."""

    def __init__(self, toc: Path):
        self.toc = toc
        self.folder = toc.parent
        st = toc.stat()
        self.signature = f"{toc.name}:{st.st_size}:{int(st.st_mtime)}"
        self.meta: dict[str, str] = {}
        with toc.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("## "):
                    k, _, v = line[3:].rstrip("\r\n").partition(": ")
                    self.meta[k] = v
        if self.meta.get("X-Mode") != "baked":
            raise QuestDBError(tr("Diese QuestieDB-Version speichert ihre Daten in einem unbekannten Format."))
        self.version = self.meta.get("Version", "?")
        self.flavor_name = self.meta.get("X-Flavor", "")
        self._ids = {}
        self._rows = {"Quest": {}, "Npc": {}, "Object": {}, "Item": {}}
        self._l10n = {}
        self.quests: dict[int, dict] = {}
        for qid in self.all_ids("Quest"):
            q = self._quest(qid)
            if q:
                self.quests[qid] = q
        self.xp = self._read_xp()
        self.zone_names, self.sort_names, self.faction_names = self._read_enums()
        self.next_of: dict[int, list[int]] = {}
        self.prev_of: dict[int, list[int]] = {}
        for qid, q in self.quests.items():
            for p in q["pre"]:
                if p in self.quests:
                    self.next_of.setdefault(p, []).append(qid)
                    self.prev_of.setdefault(qid, []).append(p)
            if q["next"] and q["next"] in self.quests:
                lst = self.next_of.setdefault(qid, [])
                if q["next"] not in lst:
                    lst.insert(0, q["next"])
                back = self.prev_of.setdefault(q["next"], [])
                if qid not in back:
                    back.append(qid)

    # ----- Rohdaten ----------------------------------------------------- #
    def _stored(self, key: str) -> str | None:
        v = self.meta.get(key)
        if v and v.startswith("~") and v.endswith("~") and v[1:-1].isdigit():
            parts = []
            for i in range(1, int(v[1:-1]) + 1):
                part = self.meta.get(f"{key}-{i}")
                if part is None:
                    raise QuestDBError(f"QuestieDB: {key}-{i} fehlt")
                parts.append(part)
            return "".join(parts)
        return v

    @staticmethod
    def _decode(text: str, packed: bool = False):
        raw = base64.b64decode(text)
        if packed:
            raw = zlib.decompress(raw)
        return cbor_decode(raw)

    def all_ids(self, kind: str) -> list[int]:
        if kind not in self._ids:
            enc = self._stored(f"X-{kind}-IDS")
            self._ids[kind] = sorted(int(x) for x in (self._decode(enc, True) if enc else []))
        return self._ids[kind]

    def row(self, kind: str, eid: int, fields: int) -> dict | None:
        cache = self._rows[kind]
        if eid in cache:
            return cache[eid]
        enc = self._stored(f"X-{kind}-{eid}-S")
        if enc is None:
            cache[eid] = None
            return None
        r = self._decode(enc)
        if isinstance(r, list):
            r = {i + 1: v for i, v in enumerate(r) if v is not None}
        presence = r.pop("p", 0) or 0
        for fi in range(1, fields + 1):
            if int(presence) >> (fi - 1) & 1:
                t = self._stored(f"X-{kind}-{eid}-{fi}")
                if t:
                    r[fi] = self._decode(t)
        cache[eid] = r
        return r

    def localized(self, kind: str, eid: int, column: int, locale: str):
        """Übersetzter Text (Spalte 1 = Name, 2 = Ziele/Untertitel) – None, wenn keiner da ist."""
        if locale == "enUS":
            return None
        key = (kind, locale)
        if key not in self._l10n:
            enc = self._stored(f"X-l10n-{locale}-{kind}")
            try:
                self._l10n[key] = self._decode(enc, True) if enc else None
            except Exception:  # noqa – fehlende Übersetzung ist kein Fehler
                self._l10n[key] = None
        block = self._l10n[key]
        col = _slot(block, column) if block is not None else None
        if col is None:
            return None
        ids = self.all_ids(kind)
        i = bisect.bisect_left(ids, eid)
        if i >= len(ids) or ids[i] != eid:
            return None
        return _slot(col, i + 1)

    # ----- Quests ------------------------------------------------------- #
    def _quest(self, qid: int) -> dict | None:
        r = self.row("Quest", qid, 36)
        if not r or not r.get(1):
            return None
        races = int(r.get(6) or 0) & 0xFFFFFFFF
        a, h = races & RACES_ALLIANCE, races & RACES_HORDE
        side = "A" if a and not h else "H" if h and not a else ""
        starts, ends = r.get(2), r.get(3)
        objectives = r.get(10)
        pre = _ids(r.get(12)) + [x for x in _ids(r.get(13)) if x not in _ids(r.get(12))]
        return {
            "id": qid, "name": str(r[1]), "lvl": int(r.get(5) or 0), "req": int(r.get(4) or 0), "side": side,
            "classes": int(r.get(7) or 0), "zone": int(r.get(17) or 0),
            "start": {"npc": _ids(_slot(starts, 1)), "obj": _ids(_slot(starts, 2)), "item": _ids(_slot(starts, 3))},
            "end": {"npc": _ids(_slot(ends, 1)), "obj": _ids(_slot(ends, 2))},
            "text": [str(x) for x in (r.get(8) or []) if isinstance(x, str)],
            "kill": [int(_slot(e, 1)) for e in (_slot(objectives, 1) or []) if isinstance(_slot(e, 1), (int, float))],
            "objs": [int(_slot(e, 1)) for e in (_slot(objectives, 2) or []) if isinstance(_slot(e, 1), (int, float))],
            "items": [int(_slot(e, 1)) for e in (_slot(objectives, 3) or []) if isinstance(_slot(e, 1), (int, float))],
            "pre": pre, "pre_all": bool(_ids(r.get(12))), "next": int(r.get(22) or 0),
            "exclusive": _ids(r.get(16)), "rep": [(int(_slot(x, 1)), int(_slot(x, 2))) for x in (r.get(26) or [])
                                                   if isinstance(x, (list, dict)) and _slot(x, 1) and _slot(x, 2)],
            "skill": (int(_slot(r[18], 1) or 0), int(_slot(r[18], 2) or 0)) if r.get(18) else None,
            "repeatable": bool(int(r.get(24) or 0) & 1), "breadcrumb_for": int(r.get(27) or 0),
            "src_item": int(r.get(11) or 0),
        }

    def _read_xp(self) -> dict[int, tuple[int, int]]:
        out = {}
        for f in sorted((self.folder / "support").glob(f"{self.flavor_name or '*'}/QuestXP/xpDB-*.lua")):
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for m in re.finditer(r"\[(\d+)\]\s*=\s*\{\s*(\d+)\s*,\s*(\d+)\s*\}", text):
                out.setdefault(int(m.group(1)), (int(m.group(2)), int(m.group(3))))
        return out

    def _read_enums(self):
        zones, sorts, factions = {}, {}, {}
        for name, target, sign in (("zones.lua", zones, 1), ("quests.lua", sorts, -1), ("factions.lua", factions, 1)):
            try:
                text = (self.folder / "src" / "corrections" / "enum" / name).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if name == "quests.lua":
                m = re.search(r"sortKeys\s*=\s*\{(.*?)\n\}", text, re.S)
                text = m.group(1) if m else ""
            for m in re.finditer(r"^\s*([A-Z][A-Z0-9_]*)\s*=\s*(-?\d+)\s*,", text, re.M):
                v = int(m.group(2))
                if (v < 0) == (sign < 0) and v not in target:
                    target[v] = m.group(1)
        # Unterzonen (z. B. Nordhaintal) mit ihren englischen Namen aus den Kommentaren
        self.zone_parent, self.zone_plain = {}, {}
        for f in (self.folder / "support").glob(f"{self.flavor_name or '*'}/Zones/subZoneToParentZone.lua"):
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for m in re.finditer(r"\[(\d+)\]\s*=\s*(\d+)\s*,\s*--\s*(.+?)\s*->\s*(.+?)\s*$", text, re.M):
                sub, parent = int(m.group(1)), int(m.group(2))
                self.zone_parent.setdefault(sub, parent)
                self.zone_plain.setdefault(sub, m.group(3))
                self.zone_plain.setdefault(parent, m.group(4))
        return zones, sorts, factions

    # ----- Namen -------------------------------------------------------- #
    def quest_name(self, qid: int, locale: str) -> str:
        q = self.quests.get(qid)
        return (self.localized("Quest", qid, 1, locale) or (q["name"] if q else f"#{qid}")) if q else f"#{qid}"

    def quest_text(self, qid: int, locale: str) -> list[str]:
        loc = self.localized("Quest", qid, 2, locale)
        if isinstance(loc, dict):
            loc = [loc[k] for k in sorted(loc)]
        if isinstance(loc, list) and any(isinstance(x, str) for x in loc):
            return [x for x in loc if isinstance(x, str)]
        return self.quests.get(qid, {}).get("text", [])

    def npc(self, nid: int, locale: str) -> dict | None:
        r = self.row("Npc", nid, 20)
        if not r:
            return None
        name = self.localized("Npc", nid, 1, locale) or r.get(1) or f"NPC {nid}"
        sub = self.localized("Npc", nid, 2, locale) or r.get(14) or ""
        zone, pos = int(r.get(9) or 0), None
        spawns = r.get(7)
        if isinstance(spawns, dict) and spawns:
            z = zone if zone in spawns else next(iter(spawns))
            pts = spawns.get(z) or []
            if pts and isinstance(pts[0], list) and len(pts[0]) >= 2 and pts[0][0] is not None:
                pos = (float(pts[0][0]), float(pts[0][1]))
            zone = int(z)
        return {"id": nid, "name": str(name), "sub": str(sub or ""), "zone": zone, "pos": pos,
                "level": (int(r.get(4) or 0), int(r.get(5) or 0)), "rank": int(r.get(6) or 0)}

    def obj_name(self, oid: int, locale: str) -> str:
        r = self.row("Object", oid, 10) or {}
        return str(self.localized("Object", oid, 1, locale) or r.get(1) or f"#{oid}")

    def item_name(self, iid: int, locale: str) -> str:
        r = self.row("Item", iid, 20) or {}
        return str(self.localized("Item", iid, 1, locale) or r.get(1) or f"#{iid}")

    def zone_name(self, zid: int, learned: dict | None = None) -> str:
        if learned and learned.get(zid):
            return learned[zid]
        if zid < 0:
            key = self.sort_names.get(zid, "")
            return tr(QUEST_SORTS_DE.get(key, _pretty_enum(key))) if key else ""
        if zid in self.zone_plain:
            return self.zone_plain[zid]
        key = self.zone_names.get(zid)
        return _pretty_enum(key) if key else (f"#{zid}" if zid else "")

    def faction_name(self, fid: int, learned: dict | None = None) -> str:
        if learned and learned.get(fid):
            return learned[fid]
        key = self.faction_names.get(fid)
        return _pretty_enum(key) if key else f"#{fid}"

    def reward_items(self, qid: int) -> list[int]:
        """Gegenstände, die QuestieDB als Belohnung dieser Quest kennt (ohne Anzahl, ohne Wahl/fest)."""
        if not hasattr(self, "_reward_items"):
            out: dict[int, list[int]] = {}
            for iid in self.all_ids("Item"):
                for q in _ids((self.row("Item", iid, 16) or {}).get(6)):
                    out.setdefault(q, []).append(iid)
            self._reward_items = out
        return self._reward_items.get(qid, [])

    # ----- Questreihen -------------------------------------------------- #
    def chain(self, qid: int) -> list[int]:
        """Die Questreihe als Liste: Anfang … qid … Ende (Verzweigungen: der Hauptpfad)."""
        seen = {qid}
        back, cur = [], qid
        while len(back) < 40:
            prev = [p for p in self.prev_of.get(cur, []) if p not in seen]
            nxt = [p for p in prev if self.quests[p]["next"] == cur] or prev
            if not nxt:
                break
            cur = nxt[0]
            seen.add(cur)
            back.append(cur)
        fwd, cur = [], qid
        while len(fwd) < 40:
            nxt = [n for n in self.next_of.get(cur, []) if n not in seen]
            if not nxt:
                break
            cur = nxt[0]
            seen.add(cur)
            fwd.append(cur)
        return list(reversed(back)) + [qid] + fwd

    def search(self, text: str, locale: str, side: str = "", lmin: int = 0, lmax: int = 0, limit: int = 300) -> list[int]:
        text = text.strip().lower()
        npc_hits: set[int] = set()
        if text and not text.isdigit() and len(text) >= 3:
            for qid, q in self.quests.items():           # Questgeber/-abgabe nach Name
                for nid in q["start"]["npc"][:3] + q["end"]["npc"][:3]:
                    n = self.npc(nid, locale)
                    if n and text in n["name"].lower():
                        npc_hits.add(qid)
                        break
        out = []
        for qid, q in self.quests.items():
            if side and q["side"] and q["side"] != side:
                continue
            if lmin and q["lvl"] < lmin or lmax and q["lvl"] > lmax:
                continue
            if text:
                if text.isdigit():
                    if str(qid) != text:
                        continue
                elif (text not in self.quest_name(qid, locale).lower() and text not in q["name"].lower()
                      and qid not in npc_hits and text not in self.zone_name(q["zone"]).lower()):
                    continue
            out.append(qid)
        out.sort(key=lambda i: (self.quests[i]["lvl"], self.quest_name(i, locale)))
        return out[:limit]


# Belohnungen (Gegenstände, Wahl, Gold) stehen nicht vollständig in QuestieDB. Für Classic gibt es sie
# offen in der Spieldatenbank des cMaNGOS-Projekts (GitHub, GPL). Leeroyzan lädt sie einmal herunter,
# zieht nur die Questbelohnungen heraus und legt sie in Leeroyzan-Data/questdata ab.
REWARD_REPOS = {"_forever_": "cmangos/classic-db", "_classic_era_": "cmangos/classic-db",
                "_anniversary_": "cmangos/classic-db", "_classic_era_ptr_": "cmangos/classic-db"}
REWARD_CHECK_DAYS = 30
_REWARD_TRIED: set[str] = set()
_SQL_TOKEN = re.compile(r"'((?:[^'\\]|\\.)*)'|(NULL)|(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)|([(),])")
_SQL_ESC = {"n": "\n", "r": "\r", "t": "\t", "0": "\0", "Z": "\x1a"}


def _sql_rows(values: str) -> list[list]:
    """Werte eines MySQL-INSERT: (1,'a',NULL),(2,'b',3) → [[1, 'a', None], [2, 'b', 3]]."""
    out, row = [], None
    for m in _SQL_TOKEN.finditer(values):
        text, null, num, punct = m.groups()
        if punct == "(":
            row = []
        elif punct == ")":
            if row is not None:
                out.append(row)
            row = None
        elif punct == ",":
            continue
        elif row is not None:
            if text is not None:
                row.append(re.sub(r"\\(.)", lambda e: _SQL_ESC.get(e.group(1), e.group(1)), text) if "\\" in text else text)
            elif null:
                row.append(None)
            else:
                row.append(float(num) if "." in num or "e" in num.lower() else int(num))
    return out


def parse_quest_rewards_dump(path: Path) -> dict:
    """Questbelohnungen aus einem cMaNGOS-Datenbankabzug (.sql.gz): Gegenstände, Wahl, Gold, Zauber."""
    want = {"quest_template", "item_template"}
    cols: dict[str, list[str]] = {}
    rows: dict[str, list] = {k: [] for k in want}
    cur = None
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            if line.startswith("CREATE TABLE `"):
                name = line.split("`")[1]
                cur = name if name in want else None
                if cur:
                    cols[cur] = []
            elif cur and line.startswith("  `"):
                cols[cur].append(line.split("`")[1])
            elif line.startswith(")"):
                cur = None
            elif line.startswith("INSERT INTO `") and line.split("`")[1] in want:
                rows[line.split("`")[1]] += _sql_rows(line[line.index(" VALUES ") + 8:])
    if not rows["quest_template"] or "entry" not in cols.get("quest_template", []):
        raise QuestDBError(tr("Die Belohnungsdaten haben ein unbekanntes Format."))
    qc = {c: i for i, c in enumerate(cols["quest_template"])}
    ic = {c: i for i, c in enumerate(cols.get("item_template", []))}

    def col(r, name, default=0):
        i = qc.get(name)
        return r[i] if i is not None and i < len(r) and r[i] is not None else default

    quests, used = {}, set()
    for r in rows["quest_template"]:
        e = {}
        items = [[col(r, f"RewItemId{i}"), col(r, f"RewItemCount{i}") or 1] for i in range(1, 5) if col(r, f"RewItemId{i}")]
        choice = [[col(r, f"RewChoiceItemId{i}"), col(r, f"RewChoiceItemCount{i}") or 1] for i in range(1, 7)
                  if col(r, f"RewChoiceItemId{i}")]
        if items:
            e["i"] = items
        if choice:
            e["c"] = choice
        if col(r, "RewOrReqMoney") > 0:
            e["m"] = col(r, "RewOrReqMoney")
        if col(r, "RewMoneyMaxLevel") > 0:
            e["mx"] = col(r, "RewMoneyMaxLevel")
        if col(r, "RewSpell") or col(r, "RewSpellCast"):
            e["s"] = col(r, "RewSpell") or col(r, "RewSpellCast")
        if e:
            quests[str(col(r, "entry"))] = e
            used.update(x[0] for x in items + choice)
    items, all_items = {}, {}
    if "entry" in ic:
        def icol(r, name, default=0):
            i = ic.get(name)
            return r[i] if i is not None and i < len(r) and r[i] is not None else default
        for r in rows["item_template"]:
            iid = icol(r, "entry")
            if iid in used:
                items[str(iid)] = [icol(r, "name", ""), icol(r, "Quality", 1)]
            # für das Auktionshaus: Name, Qualität, Klasse, Unterklasse, Gegenstandsstufe, Mindeststufe, Händlerpreis
            all_items[str(iid)] = [icol(r, "name", ""), icol(r, "Quality", 1), icol(r, "class"), icol(r, "subclass"),
                                   icol(r, "ItemLevel"), icol(r, "RequiredLevel"), icol(r, "SellPrice")]
    return {"q": quests, "items": items, "all_items": all_items}


def rewards_file(flavor: str) -> Path | None:
    repo = REWARD_REPOS.get(flavor)
    return config_dir() / "questdata" / (repo.replace("/", "_") + "-rewards.json") if repo else None


def load_reward_data(flavor: str) -> dict | None:
    path = rewards_file(flavor)
    if not path or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and isinstance(data.get("q"), dict) else None
    except (OSError, ValueError):
        return None


def rewards_due(flavor: str) -> bool:
    if flavor not in REWARD_REPOS:
        return False
    data = load_reward_data(flavor)
    return not data or "all_items" not in data or time.time() - (data.get("checked") or 0) > REWARD_CHECK_DAYS * 86400


def fetch_reward_data(flavor: str, token: str = "") -> dict | None:
    """Lädt den aktuellen Datenbankabzug von GitHub (nur wenn er sich geändert hat) und speichert die Belohnungen."""
    repo = REWARD_REPOS.get(flavor)
    if not repo:
        return None
    path = rewards_file(flavor)
    old = load_reward_data(flavor)
    listing = json.loads(http_get(f"https://api.github.com/repos/{repo}/contents/Full_DB", token))
    dumps = sorted((x for x in listing if isinstance(x, dict) and str(x.get("name", "")).endswith(".sql.gz")),
                   key=lambda x: x["name"])
    if not dumps:
        raise QuestDBError(tr("Im Projekt {0} gibt es keinen Datenbankabzug.", repo))
    pick = dumps[-1]
    path.parent.mkdir(parents=True, exist_ok=True)
    if old and old.get("sha") == pick.get("sha") and "all_items" in old:
        old["checked"] = int(time.time())
        path.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
        return old
    url = pick.get("download_url") or ""
    if urllib.parse.urlparse(url).hostname not in ("raw.githubusercontent.com", "github.com"):
        raise QuestDBError(tr("Unerwartete Download-Adresse: {0}", url))
    tmp = path.with_name(path.name + ".sql.gz.part")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=120) as r, tmp.open("wb") as f:
            shutil.copyfileobj(r, f, 1 << 20)
        data = parse_quest_rewards_dump(tmp)
    finally:
        tmp.unlink(missing_ok=True)
    data.update(repo=repo, source=pick["name"], sha=pick.get("sha", ""), fetched=int(time.time()), checked=int(time.time()))
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    log.info("Questbelohnungen aus %s/%s: %d Quests", repo, pick["name"], len(data["q"]))
    return data


def ensure_reward_data(flavor: str, token: str = "") -> dict | None:
    """Vorhandene Belohnungsdaten; fehlen sie oder sind sie alt, einmal pro Programmstart nachladen."""
    data = load_reward_data(flavor)
    if rewards_due(flavor) and flavor not in _REWARD_TRIED:
        _REWARD_TRIED.add(flavor)
        try:
            data = fetch_reward_data(flavor, token) or data
        except (OSError, ValueError, QuestDBError, urllib.error.URLError) as e:
            log.warning("Questbelohnungen nicht geladen: %s", e)
    return data


def quest_rewards(db: QuestDB, qid: int, cm: dict | None, learned: dict | None, locale: str,
                  item_names: dict | None = None) -> dict:
    """Alles, was über die Belohnung einer Quest bekannt ist – aus der besten verfügbaren Quelle:
    im Spiel gesehen (genau) > cMaNGOS (Classic) > QuestieDB (nur welche Gegenstände)."""
    cm_items = (cm or {}).get("items", {})

    def name(iid, fallback=""):
        return (db.localized("Item", iid, 1, locale) or (item_names or {}).get(str(iid), {}).get("name")
                or (cm_items.get(str(iid)) or [None])[0] or fallback or (db.row("Item", iid, 16) or {}).get(1) or f"#{iid}")

    def quality(iid, fallback=None):
        q = (cm_items.get(str(iid)) or [None, None])[1]
        if q is None:
            q = (item_names or {}).get(str(iid), {}).get("q", fallback)
        return q if isinstance(q, int) else 1

    out = {"src": "", "items": [], "choice": [], "unsure": [], "money": None, "maxmoney": 0, "spell": None}
    if learned:
        out.update(src="game", money=learned.get("money"), spell=learned.get("spell") or None,
                   items=[{"id": x["id"], "n": x["n"], "q": x["q"], "name": name(x["id"], x["name"])} for x in learned["items"]],
                   choice=[{"id": x["id"], "n": x["n"], "q": x["q"], "name": name(x["id"], x["name"])} for x in learned["choice"]])
        return out
    e = (cm or {}).get("q", {}).get(str(qid))
    if e:
        out.update(src="cmangos", money=e.get("m", 0), maxmoney=e.get("mx", 0), spell=e.get("s"),
                   items=[{"id": i, "n": n, "q": quality(i), "name": name(i)} for i, n in e.get("i", [])],
                   choice=[{"id": i, "n": n, "q": quality(i), "name": name(i)} for i, n in e.get("c", [])])
        return out
    items = db.reward_items(qid)
    if items:
        out.update(src="questie", unsure=[{"id": i, "n": 1, "q": quality(i), "name": name(i)} for i in items])
    return out


QUEST_DATA_FORMAT = 2
QUEST_DATA_FILE = "Quests.lua"


def _lua_num(v) -> str:
    if isinstance(v, float):
        return f"{v:.1f}".rstrip("0").rstrip(".")
    return str(int(v))


def quest_data_text(db: QuestDB, locale: str, zone_names: dict | None = None, faction_names: dict | None = None,
                    cm: dict | None = None, item_names: dict | None = None) -> str:
    """Quests.lua für Leeroylink: alle Quests kompakt in der Sprache des Spiels.
    q[id] = {Name, Stufe, Mindeststufe, Zone, Seite, Folgequest, {Vorquests}, Questgeber, Abgabe,
             {Ziele}, {Fraktion, Ruf, …}, XP, Klassen, wiederholbar,
             {Gegenstand, Anzahl, …}, {Wahl: Gegenstand, Anzahl, …}, Gold, Zauber, {Gegenstände laut QuestieDB}}
    Questgeber = {Name, Zone, x, y, Art}; items[id] = {Name, Qualität}"""
    zone_names, faction_names = zone_names or {}, faction_names or {}
    zones_used, factions_used = set(), set()
    items_used: dict[int, tuple[str, int]] = {}

    def flat(lst, with_count=True):
        out = []
        for x in lst:
            items_used[x["id"]] = (x["name"], x["q"])
            out += [str(x["id"]), str(x["n"])] if with_count else [str(x["id"])]
        return "{%s}" % ",".join(out) if out else "false"
    L = lua_string

    def giver(g) -> str:
        for nid in g.get("npc", [])[:2]:
            n = db.npc(nid, locale)
            if n:
                zones_used.add(n["zone"])
                x, y = n["pos"] or (None, None)
                return "{%s,%s,%s,%s}" % (L(n["name"]), n["zone"] or "false",
                                          _lua_num(x) if x is not None else "false", _lua_num(y) if y is not None else "false")
        for oid in g.get("obj", [])[:1]:
            return "{%s,false,false,false,\"o\"}" % L(db.obj_name(oid, locale))
        for iid in g.get("item", [])[:1]:
            return "{%s,false,false,false,\"i\"}" % L(db.item_name(iid, locale))
        return "false"

    rows = []
    for qid in sorted(db.quests):
        q = db.quests[qid]
        zones_used.add(q["zone"])
        texts = db.quest_text(qid, locale)
        rep = []
        for fid, val in q["rep"]:
            factions_used.add(fid)
            rep += [str(fid), str(val)]
        xp = db.xp.get(qid)
        fields = [L(db.quest_name(qid, locale)), str(q["lvl"]), str(q["req"]), str(q["zone"] or "false"),
                  L(q["side"]) if q["side"] else "false", str(q["next"]) if q["next"] else "false",
                  "{%s}" % ",".join(str(p) for p in q["pre"]) if q["pre"] else "false",
                  giver(q["start"]), giver(q["end"]),
                  "{%s}" % ",".join(L(t) for t in texts) if texts else "false",
                  "{%s}" % ",".join(rep) if rep else "false",
                  str(xp[1]) if xp else "false", str(q["classes"]) if q["classes"] else "false",
                  "true" if q["repeatable"] else "false"]
        rw = quest_rewards(db, qid, cm, None, locale, item_names)
        fields += [flat(rw["items"]), flat(rw["choice"]), str(rw["money"]) if rw["money"] else "false",
                   str(rw["spell"]) if isinstance(rw["spell"], int) and rw["spell"] else "false",
                   flat(rw["unsure"], with_count=False)]
        while fields and fields[-1] == "false":
            fields.pop()
        rows.append(f"[{qid}]={{{','.join(fields)}}},")
    zl = ",".join(f"[{z}]={L(db.zone_name(z, zone_names))}" for z in sorted(zones_used) if z and db.zone_name(z, zone_names))
    fl = ",".join(f"[{f}]={L(db.faction_name(f, faction_names))}" for f in sorted(factions_used))
    il = ",".join(f"[{i}]={{{L(n)},{q}}}" for i, (n, q) in sorted(items_used.items()))
    return ("-- Quests.lua: geschrieben von Leeroyzan aus QuestieDB " + db.version + " (" + locale + ").\n"
            "-- Nicht von Hand ändern – Leeroyzan schreibt die Datei neu, wenn sich QuestieDB ändert.\n"
            f"LeeroylinkQuests = {{\nv={QUEST_DATA_FORMAT},src={L('QuestieDB ' + db.version)},locale={L(locale)},\n"
            f"zones={{{zl}}},\nfactions={{{fl}}},\nitems={{{il}}},\nrewards={L((cm or {}).get('source', ''))},\nq={{\n"
            + "\n".join(rows) + "\n},\n}\n")


QUEST_DATA_OFF = ("-- sig: off\n-- Quests.lua: Der Questbegleiter ist in Leeroyzan ausgeschaltet – keine Questdaten im Spiel.\n"
                  "LeeroylinkQuests = nil\n")


def write_quest_data(root: str, flavor: str, zone_names: dict | None = None, faction_names: dict | None = None,
                     cm: dict | None = None, item_names: dict | None = None, enabled: bool = True) -> str:
    """Schreibt Quests.lua ins installierte Leeroylink. Ergebnis: written | unchanged | missing | noqdb | off.
    Ausgeschaltet bleibt die Datei leer – dann belegen die Questdaten im Spiel keinen Speicher."""
    folder = installed_link_dir(root, flavor)
    if not folder:
        return "missing"
    target = folder / QUEST_DATA_FILE
    if not enabled:
        try:
            if target.read_text(encoding="utf-8", errors="replace") == QUEST_DATA_OFF:
                return "off"
        except OSError:
            pass
        target.write_text(QUEST_DATA_OFF, encoding="utf-8", newline="\n")
        return "off"
    toc = find_questie_toc(root, flavor)
    if not toc:
        return "noqdb"
    st = toc.stat()
    locale = game_locale(root, flavor)
    names_key = hashlib.sha1(json.dumps([sorted((zone_names or {}).items()), sorted((faction_names or {}).items()),
                                         (cm or {}).get("sha", "")],
                                        ensure_ascii=False).encode("utf-8")).hexdigest()[:10]
    sig = f"-- sig: {toc.name}:{st.st_size}:{int(st.st_mtime)}|{locale}|{QUEST_DATA_FORMAT}|{names_key}"
    try:
        with target.open(encoding="utf-8", errors="replace") as f:
            if f.readline().strip() == sig:
                return "unchanged"
    except OSError:
        pass
    try:
        db = load_questdb(root, flavor)
    except QuestDBError:
        return "noqdb"
    text = sig + "\n" + quest_data_text(db, locale, zone_names, faction_names, cm, item_names)
    tmp = target.with_name(QUEST_DATA_FILE + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, target)
    return "written"


_QDB_CACHE: dict[str, QuestDB] = {}


def load_questdb(root: str, flavor: str) -> QuestDB:
    toc = find_questie_toc(root, flavor)
    if not toc:
        raise QuestDBError(tr("QuestieDB ist in dieser Spielversion nicht installiert."))
    st = toc.stat()
    key = f"{toc}:{st.st_size}:{int(st.st_mtime)}"
    if key not in _QDB_CACHE:
        _QDB_CACHE.clear()
        _QDB_CACHE[key] = QuestDB(toc)
    return _QDB_CACHE[key]
