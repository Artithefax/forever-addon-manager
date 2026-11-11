"""Addon-Pakete (.lzpack) und Download-Listen (.lzlist oder Textcode) zum Teilen."""
from __future__ import annotations

import base64
import json
import re
import zlib
from datetime import datetime

from .i18n import tr
from .github import parse_repo


# --------------------------------------------------------------------------- #
# Addon-Pakete zum Teilen (.lzpack = ZIP mit manifest.json + AddOns/<Ordner>/...)
# --------------------------------------------------------------------------- #

PACK_EXT = ".lzpack"
PACK_FORMAT = 1
PACK_MAX_BYTES = 1024 ** 3          # Schutz gegen übergroße/manipulierte Pakete (1 GB entpackt)
SAFE_NAME_RE = re.compile(r"^[\w .+\-!()&'\[\]]+$")

# --------------------------------------------------------------------------- #
# Download-Listen: nur Verweise (CurseForge-/GitHub-Quellen), als Datei oder als Textcode
# --------------------------------------------------------------------------- #

LIST_EXT = ".lzlist"
LIST_PREFIX = "LZ1"
LIST_FORMAT = "leeroyzan-list"
LIST_MAX_ENTRIES = 500
LIST_PART_SIZES = {"Discord / Chat (1800 Zeichen)": 1800, "Kurznachricht (500 Zeichen)": 500,
                   "SMS (150 Zeichen)": 150}


class ListError(ValueError):
    pass


def build_list(addons: list, sources: dict, flavor: str, sets: dict,
               exact: bool = True, with_sets: bool = True) -> tuple[dict, list[str]]:
    """Baut die Liste aus den gewählten Addons. Gibt (Nutzdaten, Namen ohne Quelle) zurück."""
    names = {a.name for a in addons}
    entries, linked = [], set()
    for sid, src in sources.items():
        folders = set(src.get("folders", []))
        if not folders & names:
            continue
        linked |= folders
        if src.get("type") == "curseforge":
            if type(src.get("mod_id")) is not int:
                continue
            e = {"t": "c", "i": src["mod_id"], "n": src.get("name", ""), "s": src.get("slug", "")}
            if exact and isinstance(src.get("file_id"), int):
                e["f"] = src["file_id"]
        else:
            e = {"t": "g", "r": sid}
            if src.get("asset_key"):
                e["k"] = src["asset_key"]
        entries.append(e)
    payload = {"v": 1, "fl": flavor, "a": entries}
    if with_sets:
        picked = {n: sorted(m for m in members if m in names) for n, members in sets.items()}
        picked = {n: m for n, m in picked.items() if m}
        if picked:
            payload["s"] = picked
    return payload, sorted(names - linked, key=str.lower)


def list_to_code(payload: dict, part_len: int = 1800) -> list[str]:
    """Komprimiert die Liste und zerlegt sie in Textteile 'LZ1:1/3:…' (Großbuchstaben + Ziffern)."""
    raw = zlib.compress(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"), 9)
    body = base64.b32encode(raw).decode("ascii").rstrip("=")
    n = 1
    while True:
        room = max(20, part_len - len(f"{LIST_PREFIX}:{n}/{n}:"))
        need = max(1, -(-len(body) // room))
        if need <= n:
            break
        n = need
    return [f"{LIST_PREFIX}:{i + 1}/{n}:{body[i * room:(i + 1) * room]}" for i in range(n)]


def list_to_file_text(payload: dict) -> str:
    doc = {"format": LIST_FORMAT, "created": datetime.now().isoformat(timespec="seconds"), **payload}
    return json.dumps(doc, ensure_ascii=False, indent=1)


def list_readable(payload: dict) -> str:
    lines = [tr("Addon-Liste für WoW {0} – {1} Addons (Leeroyzan)", (payload.get('fl') or '').strip('_'), len(payload['a']))]
    for e in payload["a"]:
        if e["t"] == "c":
            url = (f"https://www.curseforge.com/wow/addons/{e['s']}" if e.get("s")
                   else f"https://www.curseforge.com/projects/{e['i']}")
            lines.append(f"• {e.get('n') or e.get('s') or e['i']} – {url}")
        else:
            lines.append(f"• {e['r'].split('/')[-1]} – https://github.com/{e['r']}")
    return "\n".join(lines)


def _decode_code(text: str) -> dict:
    parts: dict[int, str] = {}
    total = 0
    for m in re.finditer(rf"{LIST_PREFIX}:(\d+)/(\d+):([A-Za-z2-7]+)", text, re.I):
        idx, tot = int(m.group(1)), int(m.group(2))
        if tot != total and total:
            raise ListError(tr("Die Teile gehören zu verschiedenen Listen."))
        total = tot
        parts[idx] = m.group(3).upper()
    if not parts:
        raise ListError(tr("Keine Download-Liste erkannt. Der Code beginnt mit „LZ1:“."))
    missing = [i for i in range(1, total + 1) if i not in parts]
    if missing:
        raise ListError(tr("Es fehlt Teil {0} von {1}.", ', '.join(map(str, missing)), total))
    body = "".join(parts[i] for i in range(1, total + 1))
    try:
        d = zlib.decompressobj()
        raw = d.decompress(base64.b32decode(body + "=" * (-len(body) % 8)), 2_000_000)
        if d.unconsumed_tail:
            raise ListError(tr("Die Liste ist ungewöhnlich groß und wird nicht geladen."))
        return json.loads(raw.decode("utf-8"))
    except ListError:
        raise
    except Exception:  # noqa – beschädigter Code
        raise ListError(tr("Der Code ist unvollständig oder beschädigt. Bitte alle Teile erneut kopieren.")) from None


def parse_list_text(text: str) -> dict:
    """Liest eine Liste aus Dateiinhalt (JSON) oder Chat-Text (LZ1-Code) und prüft sie streng."""
    text = text.replace("`", "").strip()
    try:
        data = json.loads(text) if text.startswith("{") else _decode_code(text)
    except json.JSONDecodeError:
        raise ListError(tr("Die Datei ist keine gültige Download-Liste.")) from None
    if not isinstance(data, dict) or data.get("v") != 1 or not isinstance(data.get("a"), list):
        raise ListError(tr("Unbekanntes oder zu neues Listenformat – bitte den Manager aktualisieren."))
    if len(data["a"]) > LIST_MAX_ENTRIES:
        raise ListError(tr("Die Liste ist zu lang."))
    clean = []
    for e in data["a"]:
        if not isinstance(e, dict):
            continue
        if e.get("t") == "c" and type(e.get("i")) is int and e["i"] > 0:
            item = {"t": "c", "i": e["i"], "n": str(e.get("n", ""))[:80],
                    "s": re.sub(r"[^a-z0-9-]", "", str(e.get("s", "")).lower())[:80]}
            if type(e.get("f")) is int and e["f"] > 0:
                item["f"] = e["f"]
            clean.append(item)
        elif e.get("t") == "g":
            try:
                item = {"t": "g", "r": parse_repo(str(e.get("r", "")))}
            except ValueError:
                continue
            if e.get("k"):
                item["k"] = str(e["k"])[:100]
            clean.append(item)
    if not clean:
        raise ListError(tr("Die Liste enthält keine installierbaren Addons."))
    sets: dict[str, list[str]] = {}
    raw_sets = data.get("s") if isinstance(data.get("s"), dict) else {}
    bad = r'[\x00-\x1f\\/:*?"<>|]'
    for name, members in list(raw_sets.items())[:50]:
        if isinstance(members, list) and 1 <= len(str(name)) <= 64 and not re.search(r"[\x00-\x1f]", str(name)):
            ok = [m for m in members[:300] if isinstance(m, str) and 1 <= len(m) <= 100
                  and not re.search(bad, m) and not m.startswith(".")]
            if ok:
                sets[str(name)] = ok
    return {"fl": str(data.get("fl", ""))[:60], "a": clean, "s": sets}
