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

    def test_batch_jump_targets_exist(self):
        """Jedes goto/call :Ziel in den .bat-Dateien hat seine Sprungmarke – cmd.exe merkt es erst beim Sprung
        (\"Das Sprungziel - ... wurde nicht gefunden\"). Dazu: reines ASCII mit CRLF, sonst findet cmd Marken nicht."""
        import re
        for bat in sorted(ROOT.glob("*.bat")):
            raw = bat.read_bytes()
            raw.decode("ascii")
            text = raw.decode("ascii").replace("\r\n", "\n")
            labels = {m.lower() for m in re.findall(r"(?m)^:(\w+)", text)}
            targets = re.findall(r"(?i)\b(?:goto|call)\s+:([\w%]+)", text)
            missing = sorted({t for t in targets if "%" not in t and t.lower() not in labels})
            self.assertEqual(missing, [], f"{bat.name}: Sprungmarken fehlen")
            for t in targets:                         # call :lang_%LZ_LANG% -> beide Sprachen da
                if "%" in t:
                    prefix = t.split("%")[0].lower()
                    self.assertTrue([lbl for lbl in labels if lbl.startswith(prefix)], f"{bat.name}: {t}")

    def test_changelog_has_current_version(self):
        """Der Release-Workflow nimmt den Text aus CHANGELOG.md – ohne Abschnitt bricht er ab."""
        import re
        import sys
        sys.path.insert(0, str(ROOT / "tools"))
        from release_notes import section
        version = re.search(r'^APP_VERSION = "([^"]+)"', (ROOT / "leeroy" / "constants.py").read_text(encoding="utf-8"), re.M)[1]
        self.assertTrue(section((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), version).strip())

    def test_leeroylink_versions_agree(self):
        toc = (ROOT / "Leeroylink" / "Leeroylink.toc").read_text(encoding="utf-8")
        core = (ROOT / "Leeroylink" / "Core" / "Core.lua").read_text(encoding="utf-8")
        import re
        version = re.search(r"^## Version: (\S+)", toc, re.M)[1]
        self.assertIn(f'or "{version}"', core)


if __name__ == "__main__":
    unittest.main()
