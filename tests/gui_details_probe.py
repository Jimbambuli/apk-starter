"""apk-starter --details PKG (an app's «App info»): the window opens with that app's details. Prints JSON.

Usage: gui_details_probe.py PACKAGE (environment: XDG dirs with the app installed)."""
import json
import os
import sys

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib  # noqa: E402

from apk_starter import gui  # noqa: E402
from apk_starter.paths import Paths  # noqa: E402

report: dict = {}
original = gui.Window.show_rows


def show_rows(self, rows):
    original(self, rows)
    if self.rows:
        report["selected"] = self.selected
        report["title"] = self.details_title.get_text()
        GLib.idle_add(lambda: self.get_application().quit() or False)


gui.Window.show_rows = show_rows
GLib.timeout_add(20000, lambda: os._exit(3))
gui.run(Paths.from_env(os.environ), os.environ, ["true"], show=sys.argv[1])
print(json.dumps(report))
