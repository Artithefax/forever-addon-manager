"""Statische Prüfungen am Quelltext (ohne Fenster): Muster, die zur Laufzeit erst spät auffallen würden."""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = sorted((ROOT / "leeroy").rglob("*.py"))


class StaticTests(unittest.TestCase):
    def test_run_quiet_callbacks_take_result_and_error(self):
        """run_quiet ruft die Rückmeldung mit (Ergebnis, Fehler) auf – eine mit nur einem Parameter scheitert erst,
        wenn der Hintergrund-Vorgang fertig ist."""
        bad = []
        for path in SOURCES:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for fn in ast.walk(tree):
                if not isinstance(fn, ast.FunctionDef):
                    continue
                local = {n.name: n for n in ast.walk(fn) if isinstance(n, ast.FunctionDef) and n is not fn}
                for call in ast.walk(fn):
                    if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) \
                            and call.func.attr == "run_quiet" and len(call.args) == 2:
                        cb = call.args[1]
                        if isinstance(cb, ast.Name) and cb.id in local:
                            n = len(local[cb.id].args.args)
                        elif isinstance(cb, ast.Lambda):
                            n = len(cb.args.args)
                        else:
                            continue
                        if n != 2:
                            bad.append(f"{path.relative_to(ROOT)}:{call.lineno}")
        self.assertEqual(bad, [])

    def test_no_bare_lang_reads_outside_i18n(self):
        """Die Sprache wird über cur_lang() gelesen – ein importiertes LANG friert beim Import ein."""
        bad = []
        for path in SOURCES:
            if path.name == "i18n.py":
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Name) and node.id == "LANG":
                    bad.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
