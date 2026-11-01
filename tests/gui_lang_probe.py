"""Open the window headless in the language from LANGUAGE and print its visible texts as JSON.

Usage: gui_lang_probe.py DATA_HOME PREFIX (PREFIX/share/locale holds the compiled catalogs)
"""
import json
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib  # noqa: E402

from apk_starter import gui, i18n  # noqa: E402
from apk_starter.paths import Paths  # noqa: E402

data_home, prefix = Path(sys.argv[1]), Path(sys.argv[2])
i18n.setup(prefix)
report: dict = {}


def activate(a) -> None:
    win = gui.Window(a, Paths(data_home=data_home, cache_home=data_home / "c", prefix=prefix), None, ["true"])
    win.present()
    empty = win.stack.get_child_by_name("empty")
    report["title"], report["description"] = empty.get_title(), empty.get_description()
    broken = data_home / "broken.apk"
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_bytes(b"not a zip")
    win.install(broken)

    def check() -> bool:
        d = win.get_visible_dialog()
        if d is None:
            return True
        report["error"] = [d.get_heading(), d.get_body()]
        a.quit()
        return False
    GLib.timeout_add(50, check)
    GLib.timeout_add(20000, lambda: a.quit() or False)


app = Adw.Application(application_id="io.github.Jimbambuli.ApkStarter.LangProbe",
                      flags=Gio.ApplicationFlags.NON_UNIQUE)
app.connect("activate", activate)
app.run([sys.argv[0]])
print(json.dumps(report, ensure_ascii=False))
