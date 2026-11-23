"""Open an app's details headless in a phone-wide window (360 px) in the language from LANGUAGE; print as JSON the
window width and, per button of the action row, its left/right edge and top.

Usage: gui_actions_probe.py DATA_HOME PREFIX (DATA_HOME holds one installed app and window.json 360 px wide,
PREFIX/share/locale the compiled catalogs)
"""
import json
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from apk_starter import gui, i18n  # noqa: E402
from apk_starter.paths import Paths  # noqa: E402

data_home, prefix = Path(sys.argv[1]), Path(sys.argv[2])
i18n.setup(prefix)
report: dict = {}


def activate(a) -> None:
    gui.install_styles(Gdk.Display.get_default())
    win = gui.Window(a, Paths(data_home=data_home, cache_home=data_home / "c", prefix=prefix), None, ["true"])
    win.present()

    def opened() -> bool:
        if not (win.rows and win.narrow):
            return True
        win.listbox.emit("row-activated", win.listbox.get_row_at_index(0))
        win.debug_badge.set_visible(True)          # the longest row: with the note «Debug is on»
        GLib.timeout_add(100, measure)
        return False

    def measure() -> bool:
        if not win.start_button.get_mapped() or win.start_button.get_width() == 0:
            return True                             # not laid out yet
        row = win.start_button.get_parent()
        if isinstance(row, Gtk.FlowBoxChild):
            row = row.get_parent()
        buttons, w = [], row.get_first_child()
        while w is not None:
            inner = w.get_child() if isinstance(w, Gtk.FlowBoxChild) else w
            ok, b = inner.compute_bounds(win)
            buttons.append([round(b.get_x()), round(b.get_x() + b.get_width()), round(b.get_y())])
            w = w.get_next_sibling()
        report["width"], report["buttons"] = win.get_width(), buttons
        a.quit()
        return False
    GLib.timeout_add(50, opened)
    GLib.timeout_add(20000, lambda: a.quit() or False)


app = Gtk.Application(application_id="io.github.Jimbambuli.ApkStarter.ActionsProbe",
                      flags=Gio.ApplicationFlags.NON_UNIQUE)
app.connect("activate", activate)
app.run([sys.argv[0]])
print(json.dumps(report))
