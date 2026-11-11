"""WoW-Installation erkennen, .toc-Dateien lesen, Addons finden und gruppieren."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path


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


def running_wow(root: str, flavor: str) -> list[str]:
    """Liefert laufende WoW-Prozesse (nur Windows)."""
    if not sys.platform.startswith("win"):
        return []
    exes = {e.name.lower() for e in (Path(root) / flavor).glob("*.exe")
            if e.name.lower().startswith("wow")} if root and flavor else set()
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True,
                             creationflags=0x08000000, timeout=10).stdout   # ohne Konsolenfenster
    except (OSError, subprocess.SubprocessError):
        return []
    names = {l.split('","')[0].strip('"').lower() for l in out.splitlines() if l.strip()}
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


def parse_toc(path: Path) -> dict:
    meta = {}
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return meta
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("##"):
            continue
        body = line[2:].strip()
        if ":" not in body:
            continue
        key, val = body.split(":", 1)
        meta[key.strip().lower()] = val.strip()
    return meta


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
