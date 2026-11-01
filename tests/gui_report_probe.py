"""Drive «Show report» headless (GDK_BACKEND=broadway) and print what the dialog shows as JSON.

Usage: gui_report_probe.py DATA_HOME SAVE_TO
DATA_HOME holds two installed apps: org.example.demo with two logs, org.example.none without recordings.
"""
import json
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from apk_starter import gui  # noqa: E402
from apk_starter.paths import Paths  # noqa: E402

data_home, save_to = Path(sys.argv[1]), Path(sys.argv[2])
paths = Paths(data_home=data_home, cache_home=data_home / "cache", prefix=data_home / "prefix")
report: dict = {}


def wait(cond, then, tries=200) -> None:
    def tick():
        try:
            ok = cond()
            if ok:
                then()
        except Exception as e:                      # never hang: report and stop
            report["error"] = repr(e)
            app.quit()
            return False
        if not ok:
            if tries <= 1:
                report["timeout"] = then.__name__
                app.quit()
            else:
                wait(cond, then, tries - 1)
        return False
    GLib.timeout_add(50, tick)


def shown(d) -> list:
    """Section titles and their item titles."""
    out = []
    group = d.sections.get_first_child()
    while group is not None:
        items = group.get_last_child()
        rows, row = [], items.get_first_child()
        while row is not None:
            rows.append(row.title)
            row = row.get_next_sibling()
        out.append([group.title, rows])
        group = group.get_next_sibling()
    return out


def activate(a) -> None:
    win = gui.Window(a, paths, None, ["true"])
    win.present()

    def listed():
        win.activate_action("win.show-report", GLib.Variant("s", "org.example.demo"))
        d = win.report_window
        report["title"] = d.get_title()
        report["choices"] = [d.choice.get_model().get_string(i) for i in range(d.choice.get_model().get_n_items())]
        wait(lambda: d.report is not None and d.stack.get_visible_child_name() == "report", lambda: first(d))

    def first(d):
        report["newest"] = shown(d)
        report["save_enabled"] = d.save.get_sensitive()
        d.choice.set_selected(1)
        wait(lambda: d.report is not None and d.stack.get_visible_child_name() == "report", lambda: second(d))

    def second(d):
        report["older"] = shown(d)
        d.save_to(save_to)
        d.destroy()
        win.activate_action("win.show-report", GLib.Variant("s", "org.example.none"))
        e = win.report_window
        report["empty_page"] = e.stack.get_visible_child_name()
        report["empty_choice_visible"] = e.choice.get_visible()
        e.destroy()
        app.quit()

    wait(lambda: win.rows, listed)


app = Gtk.Application(application_id="io.github.Jimbambuli.ApkStarter.ReportProbe",
                      flags=Gio.ApplicationFlags.NON_UNIQUE)
app.connect("activate", activate)
app.run([sys.argv[0]])
print(json.dumps(report))
