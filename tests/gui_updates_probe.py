"""With «Report updates» on, the window tells the updates Aurora Store knows as a desktop notification; a click
starts Aurora Store. Usage: gui_updates_probe.py START_RECORD (environment: XDG dirs). Prints JSON."""
import json
import os
import sys

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from apk_starter import gui  # noqa: E402
from apk_starter.paths import Paths  # noqa: E402

record = sys.argv[1]
report: dict = {}
sent = []
Gtk.Application.send_notification = lambda self, nid, n: sent.append((nid, n))


def activate(a) -> None:
    win = gui.Window(a, Paths.from_env(os.environ), None, ["sh", "-c", f'echo "$@" > {record}', "sh"])
    win.present()

    def check() -> bool:
        if not sent:
            return True
        nid, n = sent[0]
        report["id"] = nid
        report["toggle"] = win.aurora_updates_check.get_label() if win.aurora_updates_check else None
        a.activate_action("aurora-updates", None)          # what the notification's click does
        GLib.timeout_add(500, done)
        return False

    def done() -> bool:
        report["started"] = open(record).read().split() if os.path.exists(record) else None
        a.quit()
        return False
    GLib.timeout_add(50, check)
    GLib.timeout_add(20000, lambda: a.quit() or False)


app = Gtk.Application(application_id="io.github.Jimbambuli.ApkStarter.UpdProbe", flags=Gio.ApplicationFlags.NON_UNIQUE)
app.connect("activate", activate)
app.run([])
print(json.dumps(report))
