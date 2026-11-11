"""Installieren, Rückgängig, Quellen und Einstellungen – ohne Fenster (python3 -m unittest tests.test_files)."""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="lz-files-"))
os.environ["APPDATA"] = str(TMP / "appdata")
sys.path.insert(0, str(ROOT))

from leeroy import config, files, vault  # noqa: E402


def make_zip(path: Path, folders: dict[str, str]) -> Path:
    """folders: Name -> Inhalt einer Datei version.txt."""
    with zipfile.ZipFile(path, "w") as zf:
        for name, text in folders.items():
            zf.writestr(f"{name}/{name}.toc", "## Interface: 16001\n")
            zf.writestr(f"{name}/version.txt", text)
    return path


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.root = TMP / f"wow{time.time_ns()}"
        self.fl = "_forever_"
        self.addons = self.root / self.fl / "Interface" / "AddOns"
        self.addons.mkdir(parents=True)

    def version(self, name: str) -> str:
        return (self.addons / name / "version.txt").read_text()

    def test_update_keeps_rollback_and_restores(self):
        files.install_zip(make_zip(TMP / "v1.zip", {"Foo": "1", "FooOptions": "1"}), str(self.root), self.fl)
        src = {"type": "github", "folders": ["Foo", "FooOptions"]}
        got = files.install_zip(make_zip(TMP / "v2.zip", {"Foo": "2", "FooOptions": "2"}), str(self.root), self.fl,
                                rollback=("me/foo", src))
        self.assertEqual(sorted(got), ["Foo", "FooOptions"])
        self.assertEqual(self.version("Foo"), "2")
        meta = files.read_rollback(self.fl, "me/foo")
        self.assertEqual(meta["enabled"], {"Foo": True, "FooOptions": True})
        self.assertEqual((files.rollback_dir(self.fl, "me/foo") / "AddOns" / "Foo" / "version.txt").read_text(), "1")
        files.restore_rollback(str(self.root), self.fl, "me/foo", ["Foo", "FooOptions"])
        self.assertEqual((self.version("Foo"), self.version("FooOptions")), ("1", "1"))
        self.assertFalse(files.rollback_dir(self.fl, "me/foo").exists())
        self.assertEqual([d.name for d in (self.root / self.fl / "Interface").iterdir()], ["AddOns"])  # kein Rest

    def test_failed_swap_leaves_old_version(self):
        files.install_zip(make_zip(TMP / "a1.zip", {"Bar": "1", "BarData": "1"}), str(self.root), self.fl)
        real = shutil.move
        calls = {"n": 0}

        def flaky(src, dst):
            if "new" in str(src) and "BarData" in str(src):
                calls["n"] += 1
                raise PermissionError("Datei gesperrt")
            return real(src, dst)
        files.shutil.move = flaky
        try:
            with self.assertRaises(PermissionError):
                files.install_zip(make_zip(TMP / "a2.zip", {"Bar": "2", "BarData": "2"}), str(self.root), self.fl)
        finally:
            files.shutil.move = real
        self.assertEqual(calls["n"], 1)
        self.assertEqual((self.version("Bar"), self.version("BarData")), ("1", "1"))

    def test_recover_after_crash(self):
        work = self.root / self.fl / "Interface" / ".leeroyzan-crash"
        (work / "old" / "0_Baz").mkdir(parents=True)
        (work / "old" / "0_Baz" / "version.txt").write_text("1")
        self.assertEqual(files.recover_staging(str(self.root), self.fl, min_age=0), ["Baz"])
        self.assertEqual(self.version("Baz"), "1")
        self.assertFalse(work.exists())

    def test_prune_rollbacks(self):
        files.install_zip(make_zip(TMP / "p1.zip", {"Qux": "1"}), str(self.root), self.fl)
        files.install_zip(make_zip(TMP / "p2.zip", {"Qux": "2"}), str(self.root), self.fl, rollback=("me/qux", {"folders": ["Qux"]}))
        files.install_zip(make_zip(TMP / "p3.zip", {"Qux": "3"}), str(self.root), self.fl, rollback=("me/other", {"folders": ["Qux"]}))
        size, count = files.rollback_usage()
        self.assertGreater(size, 0)
        self.assertGreaterEqual(count, 2)
        self.assertEqual(files.prune_rollbacks(self.fl, keep={"me/qux"}), 1)
        self.assertIsNotNone(files.read_rollback(self.fl, "me/qux"))
        self.assertIsNone(files.read_rollback(self.fl, "me/other"))


class SourceTests(unittest.TestCase):
    def test_unhealthy_scan_changes_nothing(self):
        cfg = {"sources": {"_f_": {"a/b": {"folders": ["A"]}}}}
        self.assertFalse(config.reconcile_sources(cfg, "_f_", set(), healthy=False))
        self.assertIn("a/b", cfg["sources"]["_f_"])

    def test_park_and_return(self):
        cfg = {"sources": {"_f_": {"a/b": {"folders": ["A"], "tag": "1"}, "c/d": {"folders": ["C", "C2"]}}}}
        self.assertTrue(config.reconcile_sources(cfg, "_f_", {"C", "X"}, healthy=True, now=1000))
        self.assertNotIn("a/b", cfg["sources"]["_f_"])
        self.assertEqual(cfg["parked_sources"]["_f_"]["a/b"]["parked"], 1000)
        self.assertEqual(cfg["sources"]["_f_"]["c/d"]["folders"], ["C"])
        self.assertTrue(config.reconcile_sources(cfg, "_f_", {"A", "C"}, healthy=True, now=2000))
        self.assertEqual(cfg["sources"]["_f_"]["a/b"], {"folders": ["A"], "tag": "1"})
        self.assertEqual(cfg["parked_sources"]["_f_"], {})

    def test_parked_expire(self):
        cfg = {"sources": {"_f_": {"a/b": {"folders": ["A"]}}}}
        config.reconcile_sources(cfg, "_f_", {"X"}, healthy=True, now=0)
        config.reconcile_sources(cfg, "_f_", {"X"}, healthy=True, now=config.SOURCE_PARK_DAYS * 86400 + 10)
        self.assertEqual(cfg["parked_sources"]["_f_"], {})


class ConfigTests(unittest.TestCase):
    def test_save_only_on_change(self):
        writes = []
        real = config.write_text_atomic
        config.write_text_atomic = lambda p, t, newline=None: (writes.append(p), real(p, t, newline))
        try:
            cfg = config.load_config()
            cfg["flavor"] = f"x{time.time_ns()}"
            config.save_config(cfg)
            config.save_config(cfg)
            cfg["flavor"] += "y"
            config.save_config(cfg)
        finally:
            config.write_text_atomic = real
        self.assertEqual(len(writes), 2)

    def test_secrets_sealed_and_lost(self):
        fake = (lambda d: bytes(reversed(d)), lambda d: bytes(reversed(d)))
        vault._FUNCS[:] = [fake]
        try:
            cfg = config.load_config()
            cfg["curseforge_key"] = "geheim"
            config.save_config(cfg)
            raw = json.loads(config.CONFIG_FILE.read_text(encoding="utf-8"))
            self.assertIsInstance(raw["curseforge_key"], dict)
            self.assertNotIn("geheim", config.CONFIG_FILE.read_text(encoding="utf-8"))
            self.assertEqual(config.load_config()["curseforge_key"], "geheim")
            vault._FUNCS[:] = [None]                      # anderer PC: nicht lesbar
            self.assertEqual(config.load_config()["curseforge_key"], "")
            self.assertEqual(config.SECRETS_LOST, ["curseforge_key"])
        finally:
            vault._FUNCS[:] = []
            config.SECRETS_LOST.clear()


if __name__ == "__main__":
    unittest.main()
