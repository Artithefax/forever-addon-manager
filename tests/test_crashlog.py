"""Absturz-Protokoll: Markierung der laufenden Sitzung, Erkennen eines unsauberen Endes, Fehler-Hooks."""
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="lz-crash-"))
os.environ["APPDATA"] = str(TMP / "appdata")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from leeroy import crashlog  # noqa: E402
from leeroy.config import config_dir, setup_logging, LOG_FILE  # noqa: E402


class CrashLogTests(unittest.TestCase):
    def setUp(self):
        setup_logging()
        self.hooks = (sys.excepthook, threading.excepthook)
        (config_dir() / crashlog.MARKER_FILE).unlink(missing_ok=True)

    def tearDown(self):
        crashlog.clean_exit()
        sys.excepthook, threading.excepthook = self.hooks

    def test_clean_run_leaves_no_marker(self):
        self.assertFalse(crashlog.start()["unclean"])
        self.assertTrue((config_dir() / crashlog.MARKER_FILE).exists())
        crashlog.clean_exit()
        self.assertFalse((config_dir() / crashlog.MARKER_FILE).exists())
        self.assertFalse(crashlog.start()["unclean"])

    def test_unclean_end_is_reported_with_crash_tail(self):
        (config_dir() / crashlog.MARKER_FILE).write_text("999999|12.11.2026 18:55|2.0.0", encoding="utf-8")
        with open(config_dir() / crashlog.CRASH_FILE, "a", encoding="utf-8") as f:
            f.write("\n=== 2026-11-12 18:55:34 Leeroyzan 2.0.0 gestartet (PID 999999) ===\n"
                    "Windows fatal exception: access violation\n\nThread 0x1 (most recent call first):\n")
        info = crashlog.start()
        self.assertTrue(info["unclean"])
        self.assertIn("12.11.2026 18:55", info["since"])
        self.assertIn("access violation", info["crash"])

    def test_running_second_instance_is_not_a_crash(self):
        import subprocess
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
        try:
            (config_dir() / crashlog.MARKER_FILE).write_text(f"{p.pid}|x|2.0.0", encoding="utf-8")
            self.assertFalse(crashlog.start()["unclean"])
        finally:
            p.kill()
            p.wait()

    def test_thread_errors_are_logged(self):
        crashlog.start()
        t = threading.Thread(target=lambda: 1 / 0, name="probe-thread")
        t.start()
        t.join()
        text = LOG_FILE.read_text(encoding="utf-8")
        self.assertIn("probe-thread", text)
        self.assertIn("ZeroDivisionError", text)


if __name__ == "__main__":
    unittest.main()
