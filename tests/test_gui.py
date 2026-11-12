"""Smoke test for the window: builds Leeroyzan in every layout and language and shows each page once.

Needs a display (on Linux: xvfb-run python3 -m unittest tests.test_gui). Without tkinter or a
display the tests are skipped. Every exception from a Tk callback counts as a failure.
"""
import json
import logging
import os
import shutil
import sys
import tempfile
import time
import traceback
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="lz-gui-"))
os.environ["APPDATA"] = str(TMP / "appdata")
sys.path.insert(0, str(ROOT))

try:
    import tkinter
    tkinter.Tk().destroy()
    HAS_DISPLAY = True
except Exception:  # noqa: BLE001 - no tkinter, no display
    HAS_DISPLAY = False

if HAS_DISPLAY:
    import leeroy.api as lz


def make_wow() -> Path:
    wow = TMP / "World of Warcraft"
    addons = wow / "_forever_" / "Interface" / "AddOns"
    addons.mkdir(parents=True, exist_ok=True)
    if not (addons / "Leeroylink").exists():
        shutil.copytree(ROOT / "Leeroylink", addons / "Leeroylink")
    demo = addons / "DemoAddon"
    demo.mkdir(exist_ok=True)
    (demo / "DemoAddon.toc").write_text("## Interface: 16001\n## Title: Demo\n## Version: 1.0\nDemo.lua\n", encoding="utf-8")
    (wow / "_forever_" / "WTF" / "Account" / "1#1" / "SavedVariables").mkdir(parents=True, exist_ok=True)
    (wow / ".build.info").write_text("Branch!STRING:0|Version!STRING:0|Product!STRING:0\neu|1.60.1.1|wow_forever\n")
    return wow


def write_config(layout: str, language: str) -> None:
    cfg_dir = Path(os.environ["APPDATA"]) / "Leeroyzan"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.json").write_text(json.dumps({
        "wow_root": str(make_wow()), "flavor": "_forever_", "language": language, "layout": layout,
        "check_on_start": False, "self_update_mode": "off"}), encoding="utf-8")


@unittest.skipUnless(HAS_DISPLAY, "needs tkinter and a display")
class WindowSmokeTests(unittest.TestCase):
    def run_app(self, layout: str, language: str) -> list[str]:
        write_config(layout, language)
        errors: list[str] = []
        lz.setup_logging()

        class Collect(logging.Handler):          # Fehler, die Leeroyzan abfängt und nur ins Log schreibt
            def emit(self, record):
                if record.levelno >= logging.ERROR and record.exc_info:
                    errors.append(self.format(record))
        handler = Collect()
        handler.setFormatter(logging.Formatter("%(message)s"))
        lz.log.addHandler(handler)
        app = lz.App()
        app.report_callback_exception = lambda e, v, tb: errors.append("".join(traceback.format_exception(e, v, tb)))
        try:
            app.update()
            for key in list(app.pages):
                try:
                    app.show_page(key)
                except Exception:  # noqa: BLE001
                    errors.append(f"{key}: {traceback.format_exc()}")
                app.update()
            app.refresh()
            app._housekeeping()
            deadline = time.time() + 1.5
            while time.time() < deadline:       # background jobs and timers
                app.update()
                time.sleep(0.02)
            other = "en" if language == "de" else "de"
            app.change_language(other)
            app.update()
            for key in list(app.pages):
                app.show_page(key)
                app.update()
        finally:
            for job in app.tk.splitlist(app.tk.call("after", "info")):
                app.after_cancel(job)
            app.destroy()
            lz.log.removeHandler(handler)
        return errors

    def test_layouts_and_languages(self):
        for layout in ("klassisch", "seitenleiste", "launcher"):
            for language in ("de", "en"):
                with self.subTest(layout=layout, language=language):
                    errors = self.run_app(layout, language)
                    self.assertEqual(errors, [], "\n".join(errors))


if __name__ == "__main__":
    unittest.main()
