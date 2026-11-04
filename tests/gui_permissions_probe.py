"""The ⚙ menu of an app shows its dangerous permissions by group, each with a switch; switching one off is
remembered for the app. Usage: gui_permissions_probe.py (environment: XDG dirs with the app installed). JSON."""
import json
import os

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from apk_starter import gui  # noqa: E402
from apk_starter.paths import Paths  # noqa: E402
from apk_starter.settings import load_settings  # noqa: E402

report: dict = {}
paths = Paths.from_env(os.environ)


def activate(a) -> None:
    win = gui.Window(a, paths, None, ["true"])
    win.present()

    def check() -> bool:
        if not win.rows:
            return True
        switches = win.permission_switches
        report["shown"] = [(key, s.get_active()) for key, s in switches.items()]
        report["labels"] = [s.get_parent().get_first_child().get_text() for s in switches.values()]
        switches["camera"].set_active(False)
        report["saved"] = list(load_settings(paths.settings()).app("org.example.demo").denied)
        a.quit()
        return False
    GLib.timeout_add(50, check)
    GLib.timeout_add(20000, lambda: a.quit() or False)


app = Gtk.Application(application_id="io.github.Jimbambuli.ApkStarter.PermProbe", flags=Gio.ApplicationFlags.NON_UNIQUE)
app.connect("activate", activate)
app.run([])
print(json.dumps(report))
