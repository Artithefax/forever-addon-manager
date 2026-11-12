"""Windows-only paths that the Linux tests cannot reach: DPAPI, the process list, waiting for a process,
the Authenticode check of the updater. Runs in the Windows job of the test workflow."""
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="lz-win-"))
os.environ["APPDATA"] = str(TMP / "appdata")
sys.path.insert(0, str(ROOT))

WINDOWS = os.name == "nt"


@unittest.skipUnless(WINDOWS, "Windows only")
class WindowsTests(unittest.TestCase):
    def test_dpapi_roundtrip(self):
        from leeroy import vault
        self.assertTrue(vault.available())
        sealed = vault.seal("geheim – äöü")
        self.assertIsInstance(sealed, dict)
        self.assertNotIn("geheim", str(sealed))
        self.assertEqual(vault.unseal(sealed), "geheim – äöü")
        with self.assertRaises(OSError):
            vault.unseal({"dpapi": "AAAA"})                  # fremde Daten: Fehler statt Unsinn

    def test_config_keeps_secrets_readable(self):
        import json
        from leeroy import config
        cfg = config.load_config()
        cfg["github_token"] = "ghp_test"
        config.save_config(cfg)
        raw = json.loads(config.CONFIG_FILE.read_text(encoding="utf-8"))
        self.assertIsInstance(raw["github_token"], dict)
        self.assertEqual(config.load_config()["github_token"], "ghp_test")

    def test_process_list(self):
        from leeroy import wow
        names = wow.process_names()
        self.assertIn(Path(sys.executable).name.lower(), names)
        self.assertGreater(len(wow._toolhelp_names()), 5)
        self.assertIsInstance(wow.running_wow(str(TMP), "_forever_"), list)

    def test_wait_for_exit(self):
        from leeroy import selfupdate
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1)"])
        t = time.time()
        self.assertFalse(selfupdate.wait_for_exit(p.pid, 0.2))
        self.assertTrue(selfupdate.wait_for_exit(p.pid, 20))
        self.assertLess(time.time() - t, 15)
        p.wait()
        self.assertTrue(selfupdate.wait_for_exit(p.pid, 1))      # beendet: sofort True

    def test_authenticode(self):
        from leeroy import selfupdate
        status, signer = selfupdate.authenticode(Path(sys.executable))     # python.exe ist signiert
        self.assertEqual(status, "Valid")
        self.assertIn("Python Software Foundation", signer)
        plain = TMP / "plain.exe"
        plain.write_bytes(b"MZ" + b"\0" * 4096)
        status, signer = selfupdate.authenticode(plain)
        self.assertNotEqual(status, "Valid")
        self.assertEqual(signer, "")
        # signierte Version -> nicht signierte neue: abgelehnt; signierte -> gleich signierte: erlaubt
        with self.assertRaises(ValueError):
            selfupdate.check_update_signature(plain, Path(sys.executable))
        selfupdate.check_update_signature(Path(sys.executable), Path(sys.executable))

    def test_replace_exe(self):
        from leeroy import selfupdate
        src, dst = TMP / "new.bin", TMP / "old.bin"
        src.write_bytes(b"neu")
        dst.write_bytes(b"alt")
        selfupdate.replace_exe(src, dst)
        self.assertEqual(dst.read_bytes(), b"neu")
        self.assertFalse((TMP / "old.bin.tmp").exists())


if __name__ == "__main__":
    unittest.main()
