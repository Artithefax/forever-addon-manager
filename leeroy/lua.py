"""Leser für WoW-SavedVariables (Lua-Tabellen)."""
from __future__ import annotations

import re


# --------------------------------------------------------------------------- #
# Leeroylink: Spielsitzungen aus dem Begleit-Addon übernehmen und zusammenfassen
#
# Das Addon schreibt jede Sitzung in seine SavedVariables (LeeroylinkDB.global.sessionLog,
# die neuesten 30). WoW speichert die Datei beim Ausloggen und bei /reload. Leeroyzan liest sie
# nur (schreibt nie hinein) und legt jede Sitzung dauerhaft als JSON in Leeroyzan-Data/sessions ab.
# --------------------------------------------------------------------------- #

LINK_ADDON = "Leeroylink"
LINK_DB = "LeeroylinkDB"
LINK_IMPORT = "LeeroylinkImport"
# Bis Version 0.3.2 hieß das Addon "Leeroyzan Link" (Ordner und Daten unter diesem Namen)
OLD_LINK_ADDON = "LeeroyzanLink"
OLD_LINK_DB = "LeeroyzanLinkDB"
SESSION_FORMAT = 2
YARD_M = 0.9144


class LuaParseError(ValueError):
    pass


_LUA_NUM = re.compile(r"-?(?:0[xX][0-9a-fA-F]+|(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)")
_LUA_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_LUA_ESC = {"n": 10, "t": 9, "r": 13, "a": 7, "b": 8, "f": 12, "v": 11, "\\": 92, '"': 34, "'": 39, "\n": 10}


class _LuaReader:
    """Liest Lua-Tabellen, wie WoW sie in SavedVariables schreibt (nur Daten, kein Code)."""

    def __init__(self, text: str):
        self.s, self.i, self.n = text, 0, len(text)

    def fail(self, what: str):
        line = self.s.count("\n", 0, self.i) + 1
        raise LuaParseError(f"{what} (Zeile {line})")

    def ws(self):
        s, i, n = self.s, self.i, self.n
        while i < n:
            c = s[i]
            if c in " \t\r\n﻿":
                i += 1
            elif s.startswith("--", i):
                if s.startswith("--[[", i):
                    end = s.find("]]", i + 4)
                    i = n if end < 0 else end + 2
                else:
                    end = s.find("\n", i)
                    i = n if end < 0 else end + 1
            else:
                break
        self.i = i

    def peek(self) -> str:
        return self.s[self.i] if self.i < self.n else ""

    def expect(self, c: str):
        self.ws()
        if self.peek() != c:
            self.fail(f"'{c}' erwartet")
        self.i += 1

    def string(self) -> str:
        s, q = self.s, self.s[self.i]
        i = self.i + 1
        out = bytearray()
        start = i
        while True:
            if i >= self.n:
                self.fail("Text ohne Ende")
            c = s[i]
            if c == q:
                out += s[start:i].encode("utf-8")
                self.i = i + 1
                return out.decode("utf-8", "replace")
            if c == "\\":
                out += s[start:i].encode("utf-8")
                e = s[i + 1] if i + 1 < self.n else ""
                if e.isdigit():
                    j = i + 1
                    while j < self.n and j < i + 4 and s[j].isdigit():
                        j += 1
                    out.append(int(s[i + 1:j]) & 0xFF)
                    i = j
                else:
                    out.append(_LUA_ESC.get(e, ord(e) if e and ord(e) < 128 else 63))
                    i += 2
                start = i
            else:
                i += 1

    def value(self):
        self.ws()
        c = self.peek()
        if c == "{":
            return self.table()
        if c in "\"'":
            return self.string()
        m = _LUA_NUM.match(self.s, self.i)
        if m and m.group():
            self.i = m.end()
            t = m.group()
            if t.lower().startswith(("0x", "-0x")):
                return int(t, 16)
            if re.fullmatch(r"-?\d+", t):
                return int(t)
            return float(t)
        m = _LUA_NAME.match(self.s, self.i)
        if m:
            self.i = m.end()
            word = m.group()
            if word in ("true", "false"):
                return word == "true"
            if word == "nil":
                return None
            if word in ("inf", "nan"):
                return 0
        self.fail("Wert erwartet")

    def table(self):
        self.i += 1
        d: dict = {}
        pos = 1
        while True:
            self.ws()
            c = self.peek()
            if c == "}":
                self.i += 1
                break
            if c == "":
                self.fail("Tabelle ohne Ende")
            if c == "[":
                self.i += 1
                k = self.value()
                self.expect("]")
                self.expect("=")
                d[k] = self.value()
            else:
                m = _LUA_NAME.match(self.s, self.i)
                j = m.end() if m else self.i
                while j < self.n and self.s[j] in " \t\r\n":
                    j += 1
                if m and m.group() not in ("true", "false", "nil") and self.s.startswith("=", j) \
                        and not self.s.startswith("==", j):
                    self.i = j + 1
                    d[m.group()] = self.value()
                else:
                    d[pos] = self.value()
                    pos += 1
            self.ws()
            if self.peek() in (",", ";"):
                self.i += 1
        if d and all(isinstance(k, int) for k in d) and sorted(d) == list(range(1, len(d) + 1)):
            return [d[k] for k in range(1, len(d) + 1)]
        return d


def parse_lua_savedvariables(text: str) -> dict:
    """Liefert {Variablenname: Wert} aus einer SavedVariables-Datei."""
    r = _LuaReader(text)
    out = {}
    while True:
        r.ws()
        if r.i >= r.n:
            return out
        m = _LUA_NAME.match(r.s, r.i)
        if not m:
            r.fail("Variablenname erwartet")
        r.i = m.end()
        r.expect("=")
        out[m.group()] = r.value()
