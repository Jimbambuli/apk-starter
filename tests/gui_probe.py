"""Drive the management window headless (GDK_BACKEND=broadway) and print what it shows as JSON.

Usage: gui_probe.py DATA_HOME APK START_RECORD [SCREENSHOT.png]
Steps (no portal on the bus): list -> install a broken file (error dialog) -> drop the APK (installed without a
menu entry) -> start -> delete data through the menu action -> uninstall with data through the menu action
(warning: menu entry not removed); optional screenshot of the first list.
"""
import json
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, Gio, GLib, Graphene, Gsk, Gtk  # noqa: E402

from apk_starter import gui  # noqa: E402
from apk_starter.paths import Paths  # noqa: E402

data_home, apk, record = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
shot = sys.argv[4] if len(sys.argv) > 4 else None
paths = Paths(data_home=data_home, cache_home=data_home / "cache", prefix=data_home / "prefix")
report: dict = {}


def titles(win) -> list[str]:
    out, child = [], win.listbox.get_first_child()
    while child is not None:
        out.append(child.title)
        child = child.get_next_sibling()
    return out


def wait(cond, then, tries=100) -> None:
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


def screenshot(win, path: str) -> None:
    content = win.get_content()
    w, h = content.get_width(), content.get_height()
    rect = Graphene.Rect().init(0, 0, w, h)
    bg = Gdk.RGBA()
    bg.parse("#fafafb")
    snap = Gtk.Snapshot()
    snap.append_color(bg, rect)
    Gtk.WidgetPaintable.new(content).snapshot(snap, w, h)
    renderer = Gsk.CairoRenderer()
    renderer.realize_for_display(Gdk.Display.get_default())
    try:
        renderer.render_texture(snap.to_node(), rect).save_to_png(path)
    finally:
        renderer.unrealize()


def activate(a) -> None:
    gui.install_styles(Gdk.Display.get_default())
    Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).add_search_path(
        str(Path(__file__).resolve().parent.parent / "data/icons"))
    shown = []
    original = gui.Window.show_rows
    gui.Window.show_rows = lambda self, rows: (original(self, rows), shown.append(len(rows)))
    win = gui.Window(a, paths, None, ["sh", "-c", f'echo "$@" > {record}', "sh"])
    win.present()

    def listed():
        report["listed"] = titles(win)
        report["page"] = win.stack.get_visible_child_name()
        if shot:                                    # let one frame draw the list first
            GLib.timeout_add(700, lambda: (screenshot(win, shot), install())[1])
        else:
            install()

    def dialog():
        """Text of the dialog on top, then close it."""
        d = win.dialog
        text = [d.heading, d.body]
        d.respond("close")
        return text

    def install():
        broken = apk.with_name("broken.apk")
        broken.write_bytes(b"not a zip")
        win.install(broken)
        wait(lambda: win.dialog is not None, failed)
        return False

    def failed():
        report["install_error"] = dialog()
        files = Gdk.FileList.new_from_list([Gio.File.new_for_path(str(apk))])
        report["drop_accepted"] = win.on_drop(None, files, 0, 0)
        wait(lambda: win.rows and win.dialog is not None, installed)

    def installed():
        report["after_install"] = titles(win)
        report["install_warning"] = dialog()
        report["pkg"] = pkg = "org.example.demo"
        win.activate_action("win.start", GLib.Variant("s", pkg))
        wait(lambda: Path(record).exists() and Path(record).read_text(), lambda: started(pkg))

    def started(pkg):
        report["started"] = Path(record).read_text().split()
        paths.data(pkg).mkdir(parents=True, exist_ok=True)
        (paths.data(pkg) / "settings").write_text("x")
        win.activate_action("win.clear-data", GLib.Variant("s", pkg))
        d = win.dialog
        report["clear_dialog"] = d.heading
        d.respond("ok")
        wait(lambda: not paths.data(pkg).exists() and win.rows[pkg].size == 0, lambda: cleared(pkg))

    def cleared(pkg):
        paths.data(pkg).mkdir(parents=True, exist_ok=True)
        win.activate_action("win.uninstall", GLib.Variant("s", pkg))
        d = win.dialog
        report["dialog"] = d.heading
        d.extra.set_active(True)
        d.respond("ok")
        wait(lambda: pkg not in win.rows and win.dialog is not None, removed)

    def removed():
        report["after_remove"] = titles(win)
        report["remove_warning"] = dialog()
        report["page_end"] = win.stack.get_visible_child_name()
        report["data_left"] = paths.data(report["pkg"]).exists()
        report["busy_end"] = win.spinner.get_visible()
        app.quit()

    wait(lambda: shown and win.get_width() > 0, listed)


app = Gtk.Application(application_id="io.github.Jimbambuli.ApkStarter.Probe", flags=Gio.ApplicationFlags.NON_UNIQUE)
app.connect("activate", activate)
app.run([sys.argv[0]])
print(json.dumps(report))
