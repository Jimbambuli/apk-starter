"""The system's GTK theme in the window, headless (Spec 14): HOST_XDG_CONFIG_HOME and XDG_DATA_DIRS point to a fake
host. Prints the applied look as JSON, first and after the host's files changed."""
import json
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from apk_starter import gui  # noqa: E402

cfg, share = Path(sys.argv[1]), Path(sys.argv[2])
Gtk.init()
watcher = gui.ThemeWatcher(Gdk.Display.get_default())
settings = Gtk.Settings.get_default()
win = Gtk.Window()
box = Gtk.Box()
probes = {}
for name in ("theme-probe", "user-probe"):
    probes[name] = Gtk.Label(label=name)
    probes[name].add_css_class(name)
    box.append(probes[name])
win.set_child(box)
win.present()


def rgb(w: Gtk.Widget) -> list:
    c = w.get_color()
    return [round(c.red * 255), round(c.green * 255), round(c.blue * 255)]


def look() -> dict:
    return {"theme": settings.get_property("gtk-theme-name"), "icons": settings.get_property("gtk-icon-theme-name"),
            "font": settings.get_property("gtk-font-name"), "theme_css": rgb(probes["theme-probe"]),
            "user_css": rgb(probes["user-probe"]), "found": watcher.system.found}


report = {"first": look()}
(cfg / "gtk-4.0" / "colors.css").write_text("label.user-probe { color: rgb(40,50,60); }\n")
(share / "themes" / "Zweites" / "gtk-4.0").mkdir(parents=True)
(share / "themes" / "Zweites" / "gtk-4.0" / "gtk.css").write_text("label.theme-probe { color: rgb(10,20,30); }\n")
(cfg / "gtk-4.0" / "settings.ini").write_text("[Settings]\ngtk-theme-name=Zweites\n")
loop = GLib.MainLoop()
GLib.timeout_add(1500, lambda: loop.quit() or False)
loop.run()
report["changed"] = look()
print(json.dumps(report))
