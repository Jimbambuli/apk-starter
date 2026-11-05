"""The window after a Flatpak update: a spinner in the row of each app worked on, the work in
its «Status», «Start» locked meanwhile, and afterwards nothing left over; a failure in «Status» and as a short note.

Usage: gui_prepare_probe.py FLATPAK_INFO (environment as the starter gets it: XDG dirs, PATH with the fake ART;
one app installed under the commit in FLATPAK_INFO, which this probe then changes). Prints JSON.
"""
import json
import sys
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from apk_starter import dexopt, gui  # noqa: E402
from apk_starter.paths import Paths  # noqa: E402
import os  # noqa: E402

dexopt.FLATPAK_INFO = Path(sys.argv[1])
dexopt.FLATPAK_INFO.write_text("[Instance]\napp-commit=new\n")          # the update
report: dict = {"seen": []}
gate = threading.Event()
real_prepare_all = gui.prepare_all


def held_prepare_all(paths, env, report_, working):
    """Holds the work at its first state until the probe has looked at the window."""
    def hold(w):
        working(w)
        if w and not gate.is_set():
            report["seen"].append(sorted(w.items()))
            gate.wait(10)
    return real_prepare_all(paths, env, report_, hold)


gui.prepare_all = held_prepare_all


def spinners(w) -> int:
    n, child = isinstance(w, Gtk.Spinner) and w.get_visible(), w.get_first_child()
    while child is not None:
        n += spinners(child)
        child = child.get_next_sibling()
    return n


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
        if ok:
            return False
        if tries <= 1:
            report["timeout"] = then.__name__
            app.quit()
        else:
            wait(cond, then, tries - 1)
        return False
    GLib.timeout_add(50, tick)


def activate(a) -> None:
    win = gui.Window(a, Paths.from_env(os.environ), None, ["true"])
    win.present()

    def working():
        report["during"] = {"spinners": spinners(win), "status": win.status_value.get_text(),
                            "start": win.start_button.get_sensitive()}
        gate.set()
        wait(lambda: not win.busy and not win.working, ended)

    def ended():
        GLib.timeout_add(200, after)                # the list is read again after the work

    def after():
        report["after"] = {"spinners": spinners(win), "status": win.status_value.get_text(),
                           "start": win.start_button.get_sensitive(), "toast": win.toast_label.get_text()
                           if win.toast.get_reveal_child() else None}
        a.quit()
        return False

    wait(lambda: win.status_value is not None and win.working, working)


app = Gtk.Application(application_id="io.github.Jimbambuli.ApkStarter.PrepareProbe",
                      flags=Gio.ApplicationFlags.NON_UNIQUE)
app.connect("activate", activate)
app.run([sys.argv[0]])
print(json.dumps(report, ensure_ascii=False))
