"""WoW-Installation erkennen, .toc-Dateien lesen, Addons finden und gruppieren."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

from .config import config_dir, log, write_text_atomic


# --------------------------------------------------------------------------- #
# WoW-Installation erkennen
# --------------------------------------------------------------------------- #

DEFAULT_ROOTS = [
    r"C:\Program Files (x86)\World of Warcraft",
    r"C:\Program Files\World of Warcraft",
    r"D:\World of Warcraft",
    r"D:\Games\World of Warcraft",
    r"E:\World of Warcraft",
    "/Applications/World of Warcraft",
]

FLAVOR_RE = re.compile(r"^_[a-z0-9_]+_$", re.IGNORECASE)


def find_wow_root() -> str:
    for r in DEFAULT_ROOTS:
        if Path(r).is_dir() and list_flavors(r):
            return r
    return ""


def list_flavors(root: str) -> list[str]:
    """Liefert Unterordner wie _retail_, _classic_era_, _forever_."""
    p = Path(root)
    if not p.is_dir():
        return []
    out = []
    for child in sorted(p.iterdir()):
        if child.is_dir() and FLAVOR_RE.match(child.name):
            out.append(child.name)
    # Forever nach vorne sortieren (in der Beta-Phase liegt Forever in _classic_beta_)
    def prio(n: str) -> int:
        n = n.lower()
        if "forever" in n:
            return 0
        if n == "_classic_beta_":
            return 1
        return 2
    out.sort(key=lambda n: (prio(n), n))
    return out


def flavor_label(flavor: str) -> str:
    """Anzeigename für das Auswahlfeld."""
    n = flavor.lower()
    if "forever" in n or n == "_classic_beta_":
        return f"{flavor}  (Forever)"
    return flavor

def flavor_product(flavor: str) -> str:
    """_retail_ -> wow, _classic_beta_ -> wow_classic_beta, _forever_ -> wow_forever"""
    n = flavor.strip("_").lower()
    return "wow" if n == "retail" else f"wow_{n}"


def read_game_version(root: str, flavor: str) -> tuple[str, int] | None:
    """Liest Spielversion und Interface-Nummer aus der .build.info (z. B. 1.15.8 -> 11508)."""
    info = Path(root) / ".build.info"
    try:
        lines = [l for l in info.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip()]
    except OSError:
        return None
    if len(lines) < 2:
        return None
    cols = [c.split("!")[0].strip().lower() for c in lines[0].split("|")]
    if "version" not in cols or "product" not in cols:
        return None
    iv, ip = cols.index("version"), cols.index("product")
    want = flavor_product(flavor)
    for line in lines[1:]:
        vals = line.split("|")
        if len(vals) <= max(iv, ip) or vals[ip].strip().lower() != want:
            continue
        m = re.match(r"(\d+)\.(\d+)\.(\d+)", vals[iv].strip())
        if m:
            a, b, c = (int(x) for x in m.groups())
            return f"{a}.{b}.{c}", a * 10000 + b * 100 + c
    return None


def find_wow_exe(root: str, flavor: str) -> Path | None:
    """Sucht die Spiel-.exe im Versionsordner (z. B. WowClassicB.exe in _classic_beta_)."""
    folder = Path(root) / flavor
    if not root or not flavor or not folder.is_dir():
        return None
    skip = ("error", "crash", "launcher", "update", "repair", "uninstall")
    exes = [e for e in folder.glob("*.exe")
            if e.name.lower().startswith("wow") and not any(x in e.name.lower() for x in skip)]
    if not exes:
        return None
    return max(exes, key=lambda e: e.stat().st_size)   # die eigentliche Spieldatei ist die größte


def process_names() -> set[str]:
    """Namen aller laufenden Programme (klein geschrieben, nur Windows). Direkt über die Windows-Prozessliste –
    schneller als tasklist und unabhängig von der Systemsprache; tasklist bleibt der Rückweg."""
    if not sys.platform.startswith("win"):
        return set()
    try:
        return {n.lower() for n in _toolhelp_names()}
    except Exception:  # noqa – dann wie früher
        pass
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True,
                             creationflags=0x08000000, timeout=10).stdout   # ohne Konsolenfenster
    except (OSError, subprocess.SubprocessError):
        return set()
    return {l.split('","')[0].strip('"').lower() for l in out.splitlines() if l.strip()}


def _toolhelp_names() -> list[str]:
    import ctypes
    from ctypes import wintypes

    class Entry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.Process32FirstW.argtypes = k32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    k32.Process32FirstW.restype = k32.Process32NextW.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)               # TH32CS_SNAPPROCESS
    if not snap or snap == wintypes.HANDLE(-1).value:
        raise OSError(ctypes.get_last_error(), "CreateToolhelp32Snapshot")
    try:
        entry = Entry()
        entry.dwSize = ctypes.sizeof(Entry)
        names = []
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            names.append(entry.szExeFile)
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
        return names
    finally:
        k32.CloseHandle(snap)


def running_wow(root: str, flavor: str) -> list[str]:
    """Liefert laufende WoW-Prozesse (nur Windows)."""
    if not sys.platform.startswith("win"):
        return []
    exes = {e.name.lower() for e in (Path(root) / flavor).glob("*.exe")
            if e.name.lower().startswith("wow")} if root and flavor else set()
    names = process_names()
    if exes:
        return sorted(names & exes)
    return sorted(n for n in names if n.startswith("wow") and n.endswith(".exe"))


# --------------------------------------------------------------------------- #
# TOC-Parsing
# --------------------------------------------------------------------------- #

COLOR_RE = re.compile(r"\|c[0-9a-fA-F]{8}|\|r|\|T[^|]*\|t|\|A[^|]*\|a")


def clean_wow_text(s: str) -> str:
    return COLOR_RE.sub("", s).strip()


def pick_toc(folder: Path, flavor: str, game_if: int | None = None) -> Path | None:
    """Wählt die passende .toc-Datei (beachtet Suffixe wie _Vanilla, -Classic, _Forever)."""
    tocs = [t for t in folder.glob("*.toc")]
    if not tocs:
        return None
    name = folder.name.lower()
    fl = flavor.strip("_").lower()
    suffix_pref = []
    if "forever" in fl or fl == "classic_beta":
        suffix_pref = ["forever", "vanilla", "classic"]
    elif "classic" in fl:
        suffix_pref = ["vanilla", "classic", "cata", "mists", "wrath"]
    elif "retail" in fl:
        suffix_pref = ["mainline", "retail"]

    def score(t: Path) -> tuple:
        stem = t.stem.lower()
        if not stem.startswith(name):
            return (3, stem)
        rest = stem[len(name):].lstrip("_-")
        if rest == "":
            return (1, stem)
        if rest in suffix_pref:
            return (0, suffix_pref.index(rest))
        return (2, stem)

    ordered = sorted(tocs, key=score)
    if game_if:
        # Bevorzugt die .toc, deren Interface-Liste die Spielversion enthält – so wählt auch
        # WoW selbst. Erst danach greift die Reihenfolge nach Namens-Suffix.
        for t in ordered:
            if not t.stem.lower().startswith(name):
                continue
            nums = re.findall(r"\d{4,6}", parse_toc(t).get("interface", ""))
            if str(game_if) in nums:
                return t
    return ordered[0]


TOC_X_KEYS = {"x-curse-project-id", "x-wago-id", "x-wowi-id", "x-website", "x-category", "x-license"}
TOC_VALUE_MAX = 4096          # längere Werte (z. B. die Daten in QuestieDBs .toc) braucht der Manager nicht
_TOC_LOCK = threading.Lock()
_TOC_CACHE: dict[str, list] = {}          # Pfad -> [Größe, Änderungszeit (ns), Metadaten]
_TOC_STATE = {"loaded": False, "dirty": False}


def _toc_cache_file() -> Path:
    return config_dir() / "cache" / "toc.json"


def _read_toc(path: Path) -> dict:
    meta = {}
    try:
        raw = path.read_bytes()
    except OSError:
        return meta
    for line in raw.decode("utf-8-sig", errors="replace").splitlines():
        if not line.startswith("##", len(line) - len(line.lstrip())) or len(line) > TOC_VALUE_MAX + 200:
            continue
        body = line.strip()[2:].strip()
        if ":" not in body:
            continue
        key, val = body.split(":", 1)
        key = key.strip().lower()
        if key.startswith("x-") and key not in TOC_X_KEYS:
            continue                  # eigene Daten mancher Addons (QuestieDB: über 100.000 Einträge)
        meta[key] = val.strip()
    return meta


def parse_toc(path: Path) -> dict:
    """Metadaten einer .toc. Zwischengespeichert nach Größe und Änderungszeit – auch über Neustarts hinweg,
    damit große Dateien (QuestieDB: 16 MB) nur nach einer Änderung neu gelesen werden."""
    try:
        st = path.stat()
    except OSError:
        return {}
    key = str(path)
    with _TOC_LOCK:
        if not _TOC_STATE["loaded"]:
            _TOC_STATE["loaded"] = True
            try:
                data = json.loads(_toc_cache_file().read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    _TOC_CACHE.update({k: v for k, v in data.items() if isinstance(v, list) and len(v) == 3})
            except (OSError, ValueError):
                pass
        hit = _TOC_CACHE.get(key)
    if hit and hit[0] == st.st_size and hit[1] == st.st_mtime_ns:
        return dict(hit[2])
    meta = _read_toc(path)
    with _TOC_LOCK:
        _TOC_CACHE[key] = [st.st_size, st.st_mtime_ns, meta]
        _TOC_STATE["dirty"] = True
    return dict(meta)


def flush_toc_cache() -> None:
    """Speichert den .toc-Zwischenspeicher, wenn sich etwas geändert hat (verschwundene Dateien fliegen raus)."""
    with _TOC_LOCK:
        if not _TOC_STATE["dirty"]:
            return
        _TOC_STATE["dirty"] = False
        data = {k: v for k, v in _TOC_CACHE.items() if os.path.exists(k)}
    try:
        _toc_cache_file().parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(_toc_cache_file(), json.dumps(data, ensure_ascii=False))
    except OSError as e:
        log.warning(".toc-Zwischenspeicher nicht gespeichert: %s", e)


def toc_deps(meta: dict) -> list[str]:
    raw = ",".join(meta.get(k, "") for k in ("dependencies", "requireddeps", "dep"))
    return [d.strip() for d in raw.split(",") if d.strip()]


class Addon:
    def __init__(self, folder: Path, enabled: bool, flavor: str, game_if: int | None = None):
        self.folder = folder
        self.name = folder.name
        self.enabled = enabled
        toc = pick_toc(folder, flavor, game_if)
        self.toc_name = toc.name if toc else ""
        self.meta = parse_toc(toc) if toc else {}
        self.valid = toc is not None
        self.title = clean_wow_text(self.meta.get("title", self.name))
        self.version = self.meta.get("version", "")
        self.interface = self.meta.get("interface", "")
        self.interfaces = [int(x) for x in re.findall(r"\d{4,6}", self.interface)]
        self.author = clean_wow_text(self.meta.get("author", ""))
        self.deps = toc_deps(self.meta)

# --------------------------------------------------------------------------- #
# Gruppierung zusammengehöriger Addon-Ordner
# --------------------------------------------------------------------------- #

LIB_RE = re.compile(r"^(!|lib|ace\d|ace-|ace_|callbackhandler|taintless)", re.IGNORECASE)


def name_key(name: str) -> str:
    """Präfix vor dem ersten Trenner: Bagnon_Bank -> bagnon, DBM-Core -> dbm."""
    parts = re.split(r"[_\-]", name, maxsplit=1)
    return parts[0].lower() if len(parts) > 1 else ""


def compute_groups(addons: list, bundles: list[list[str]]) -> list[dict]:
    """Fasst zusammengehörige Ordner zusammen.

    Hinweise (in dieser Reihenfolge): gemeinsam installiert (ZIP/GitHub-Release),
    Namens-Präfix (Bagnon_Bank -> Bagnon, QuestieDB -> Questie), gemeinsames Präfix vor
    einem Trenner (Leatrix_Maps + Leatrix_Plus) und Abhängigkeiten, die nur eine einzige
    Gruppe benötigt (BagBrother -> Bagnon). Bibliotheken bleiben eigenständig.
    """
    names = {a.name.lower(): a for a in addons}
    parent = {n: n for n in names}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        if a in parent and b in parent:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

    is_lib = {n: bool(LIB_RE.match(n)) for n in names}
    # 1) gemeinsam installiert
    for bundle in bundles:
        members = [m.lower() for m in bundle if m.lower() in names]
        for m in members[1:]:
            union(members[0], m)
    # 2) Präfix: questiedb beginnt mit questie, bagnon_bank mit bagnon
    for a in names:
        if len(a) < 4 or is_lib[a]:
            continue
        for b in names:
            if b != a and not is_lib[b] and b.startswith(a):
                union(a, b)
    # 3) gemeinsames Präfix vor Trenner (mindestens zwei Ordner)
    keys: dict[str, list[str]] = {}
    for n in names:
        k = name_key(n)
        if len(k) >= 3 and not is_lib[n]:
            keys.setdefault(k, []).append(n)
    for members in keys.values():
        for m in members[1:]:
            union(members[0], m)
    # 4) Abhängigkeit, die nur von einer Gruppe gebraucht wird
    for _ in range(2):
        users: dict[str, set] = {}
        for n, a in names.items():
            for d in a.deps:
                d = d.lower()
                if d in names and d != n and not is_lib[d]:
                    users.setdefault(d, set()).add(find(n))
        for d, groups in users.items():
            if len(groups) == 1:
                union(next(iter(groups)), d)

    clusters: dict[str, list] = {}
    for n in names:
        clusters.setdefault(find(n), []).append(names[n])

    result = []
    for members in clusters.values():
        members.sort(key=lambda a: a.name.lower())
        low = [m.name.lower() for m in members]
        root = None
        if len(members) > 1:
            # Hauptordner: der, mit dem die meisten anderen beginnen; sonst der meistgebrauchte
            best = max(members, key=lambda m: (sum(1 for o in low if o != m.name.lower()
                                                   and o.startswith(m.name.lower())),
                                               sum(1 for o in members if m.name.lower()
                                                   in (d.lower() for d in o.deps)),
                                               -len(m.name)))
            if any(o != best.name.lower() and o.startswith(best.name.lower()) for o in low) or \
                    any(best.name.lower() in (d.lower() for d in o.deps) for o in members):
                root = best
        if root:
            title = root.name
        elif len(members) > 1:
            prefix = os.path.commonprefix([m.name for m in members]).rstrip("_- ")
            title = prefix if len(prefix) >= 3 else members[0].name
        else:
            title = members[0].name
        result.append({"title": title, "root": root, "members": members})
    return result
