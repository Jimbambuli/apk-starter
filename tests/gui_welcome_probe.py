"""The first run headless: the welcome page, microG offered after installing an app with Google services (once),
«Get apps» starting an installed Aurora Store; print JSON. With a picture folder: also the welcome page as PNG
(welcome.png; under Xvfb, see picture(); light or dark by GTK_THEME).

Usage: gui_welcome_probe.py DATA_HOME GMS_APK START_RECORD [PICTURE_DIR]
"""
import json
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from apk_starter import cli, gui, i18n  # noqa: E402
from apk_starter.paths import Paths  # noqa: E402

cli.MENU_ENTRIES = False                            # no launcher portal on the test bus: install without menu entry

data_home, gms_apk, record = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
pictures = Path(sys.argv[4]) if len(sys.argv) > 4 else None
paths = Paths(data_home=data_home, cache_home=data_home / "cache", prefix=data_home / "prefix")
i18n.setup(paths.prefix)                            # English unless the prefix has catalogs (pictures in German)
report: dict = {}


def wait(cond, then, tries=400) -> None:
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


def picture(win, name: str) -> None:
    """The screen as PNG (ImageMagick «import»; run under Xvfb with GDK_BACKEND=x11 for this)."""
    import subprocess
    subprocess.run(["import", "-window", "root", str(pictures / name)], check=True)


def activate(a) -> None:
    a.hold()
    gui.install_styles(Gdk.Display.get_default())
    win = gui.Window(a, paths, None, ["sh", "-c", f'echo "$@" >> {record}', "sh"])
    win.set_default_size(900, 640)
    win.present()

    def welcome():
        report["welcome"] = [win.welcome.get_mapped(), [b.get_label() for b in win.welcome_buttons]]
        if pictures is not None:                    # after the page's cross-fade
            GLib.timeout_add(1500, lambda: picture(win, "welcome.png") or install() or False)
        else:
            install()

    def install():
        win.install(gms_apk)
        wait(lambda: win.dialog is not None and win.busy == 0, offered)

    def offered():
        d = win.dialog
        report["offered"] = [d.heading, [b.get_label() for b in buttons(d)]]
        d.respond("cancel")                         # «Not now»
        win.install(gms_apk)                        # the same app again: an update, not asked again
        wait(lambda: win.busy == 0 and "org.example.demo" in win.rows, updated)

    def updated():
        report["asked_on_update"] = win.dialog is not None
        paths.meta("com.aurora.store").write_text(json.dumps({"package": "com.aurora.store", "label": "Aurora"}))
        win.activate_action("win.get-apps", None)   # installed: «Get apps» starts it, no question
        wait(lambda: record.exists() and "com.aurora.store" in record.read_text(), started)

    def started():
        report["get_apps"] = [record.read_text().split(), win.dialog is not None]
        app.quit()

    def buttons(d):
        row = d.get_child().get_last_child()
        out, b = [], row.get_first_child()
        while b is not None:
            out.append(b)
            b = b.get_next_sibling()
        return out

    wait(lambda: win.get_width() > 0 and win.welcome.get_mapped(), welcome)


app = Gtk.Application(application_id="io.github.Jimbambuli.ApkStarter.WelcomeProbe",
                      flags=Gio.ApplicationFlags.NON_UNIQUE)
app.connect("activate", activate)
app.run([sys.argv[0]])
print(json.dumps(report))
