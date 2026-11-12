"""Schreibt den Abschnitt einer Version aus CHANGELOG.md als Release-Text.

    python tools/release_notes.py 2.0.0 notes.md
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def section(text: str, version: str) -> str:
    """Text unter „## <Version>“ bis zur nächsten Überschrift dieser Ebene."""
    version = version.lstrip("v")
    m = re.search(rf"^## \[?v?{re.escape(version)}\]?[^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    if not m:
        raise SystemExit(f"CHANGELOG.md hat keinen Abschnitt für {version}")
    return m.group(1).strip() + "\n"


if __name__ == "__main__":
    notes = section((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), sys.argv[1])
    Path(sys.argv[2]).write_text(notes, encoding="utf-8")
    print(notes)
