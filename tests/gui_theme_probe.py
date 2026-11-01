"""Follow the (fake) settings portal headless and print the applied look as JSON: first, then after the change."""
import json
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from apk_starter import gui  # noqa: E402

Gtk.init()
watcher = gui.ThemeWatcher(Gdk.Display.get_default())
settings = Gtk.Settings.get_default()


def look() -> list:
    if settings.find_property("gtk-interface-color-scheme") is not None:
        dark = settings.get_property("gtk-interface-color-scheme") == Gtk.InterfaceColorScheme.DARK
    else:
        dark = settings.get_property("gtk-application-prefer-dark-theme")
    css = watcher.css.to_string()
    return [dark, "rgb(255,128,0)" in css.replace(" ", ""), "rgb(0,0,255)" in css.replace(" ", "")]


report = {"first": look()}
loop = GLib.MainLoop()
GLib.timeout_add(1500, lambda: loop.quit() or False)
loop.run()
report["changed"] = look()
print(json.dumps(report))
