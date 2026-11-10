"""An install and an uninstall by another starter process (as from Aurora Store) show up in the open window without
any action in it. Usage: gui_external_probe.py DATA_HOME APK. Prints JSON."""
import json
import os
import subprocess
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from apk_starter import gui  # noqa: E402
from apk_starter.paths import Paths  # noqa: E402

data_home, apk = Path(sys.argv[1]), sys.argv[2]
paths = Paths(data_home=data_home, cache_home=data_home / "cache", prefix=data_home / "prefix")
env = {**os.environ, "XDG_DATA_HOME": str(data_home), "XDG_CACHE_HOME": str(data_home / "cache"),
       "APK_STARTER_PREFIX": str(data_home / "prefix")}
report: dict = {}


def other_starter(*args: str) -> int:
    return subprocess.run([sys.executable, "-c", "import sys; from apk_starter.cli import main; sys.exit(main())",
                           *args], env=env, capture_output=True).returncode


def wait(cond, then, tries=200) -> None:
    def tick() -> bool:
        if cond():
            then()
        elif tries <= 1:
            report["timeout"] = then.__name__
            app.quit()
        else:
            wait(cond, then, tries - 1)
        return False
    GLib.timeout_add(50, tick)


def activate(a) -> None:
    win = gui.Window(a, paths, None, ["true"])
    win.present()

    def listed() -> None:
        report["before"] = sorted(win.rows)
        report["install_rc"] = other_starter("--install", apk)
        wait(lambda: win.rows, installed)

    def installed() -> None:
        report["after_install"] = sorted(win.rows)
        report["remove_rc"] = other_starter("--remove", "org.example.demo")
        wait(lambda: not win.rows, removed)

    def removed() -> None:
        report["after_remove"] = sorted(win.rows)
        a.quit()

    wait(lambda: win.stack.get_visible_child_name() == "list", listed)
    GLib.timeout_add(30000, lambda: a.quit() or False)


app = Gtk.Application(application_id="io.github.Jimbambuli.ApkStarter.ExtProbe", flags=Gio.ApplicationFlags.NON_UNIQUE)
app.connect("activate", activate)
app.run([])
print(json.dumps(report))
