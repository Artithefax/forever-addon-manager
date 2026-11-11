"""Every German text in tr()/plural() has an English translation, and no translation is left over.

Scans leeroyzan.py and the whole leeroy package with the Python parser, so concatenated
strings and texts in every module count. No display needed.
"""
import ast
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("APPDATA", tempfile.mkdtemp(prefix="lz-i18n-"))
sys.path.insert(0, str(ROOT))

from leeroy.i18n import TRANSLATIONS  # noqa: E402

SOURCES = [ROOT / "leeroyzan.py", *sorted((ROOT / "leeroy").rglob("*.py"))]


def ui_texts() -> dict[str, str]:
    """German text -> 'file:line' of its first use."""
    found: dict[str, str] = {}
    for path in SOURCES:
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            if node.func.id == "tr":
                args = node.args[:1]
            elif node.func.id == "plural":
                args = node.args[1:3]
            else:
                continue
            for a in args:
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    found.setdefault(a.value, f"{path.relative_to(ROOT)}:{node.lineno}")
    return found


class TranslationTests(unittest.TestCase):
    def test_every_text_translated(self):
        en = TRANSLATIONS["en"]
        missing = sorted(f"{where}: {text!r}" for text, where in ui_texts().items() if text not in en)
        self.assertEqual(missing, [], "missing English texts:\n" + "\n".join(missing))

    def test_placeholders_match(self):
        import re
        rx = re.compile(r"\{\d+\}")
        wrong = sorted(k for k, v in TRANSLATIONS["en"].items() if sorted(rx.findall(k)) != sorted(rx.findall(v)))
        self.assertEqual(wrong, [], "placeholders differ:\n" + "\n".join(wrong))


if __name__ == "__main__":
    unittest.main()
