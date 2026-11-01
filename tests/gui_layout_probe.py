"""Drive list, search, sort, keys, the narrow layout and the remembered state headless; print JSON.

Usage: gui_layout_probe.py DATA_HOME START_RECORD
DATA_HOME holds three installed apps (Alpha, Bravo, Charlie) and window.json (Bravo chosen, 900 px wide).
"""
import json
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from apk_starter import gui  # noqa: E402
from apk_starter.paths import Paths  # noqa: E402

data_home, record = Path(sys.argv[1]), sys.argv[2]
paths = Paths(data_home=data_home, cache_home=data_home / "cache", prefix=data_home / "prefix")
report: dict = {}
CTRL = Gdk.ModifierType.CONTROL_MASK
NONE = Gdk.ModifierType(0)


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


def titles(win) -> list[str]:
    out, child = [], win.listbox.get_first_child()
    while child is not None:
        out.append(child.title)
        child = child.get_next_sibling()
    return out


def key(win, name: str, mods=NONE) -> bool:
    return win.on_key(None, Gdk.keyval_from_name(name), 0, mods)


def activate(a) -> None:
    a.hold()                                        # the app lives on while the first window is closed
    gui.install_styles(Gdk.Display.get_default())
    win = gui.Window(a, paths, None, ["sh", "-c", f'echo "$@" > {record}', "sh"])
    win.present()

    def listed():
        report["restored"] = [win.selected, win.details_title.get_text(), titles(win)]
        win.search.set_text("CHA")
        wait(lambda: titles(win) == ["Charlie"], searched)

    def searched():
        report["search"] = [titles(win), win.selected]
        win.search.set_text("")
        wait(lambda: len(titles(win)) == 3, cleared)

    def cleared():
        report["kept_after_search"] = win.selected
        win.activate_action("win.sort", GLib.Variant("s", "size"))
        report["by_size"] = titles(win)
        win.activate_action("win.sort", GLib.Variant("s", "started"))
        report["by_started"] = titles(win)
        key(win, "f", CTRL)
        focus = win.get_focus()
        report["ctrl_f_in_search"] = focus is not None and (focus is win.search or focus.is_ancestor(win.search))
        win.search.emit("activate")                 # Enter in the search: to the list
        report["enter_to_list"] = isinstance(win.get_focus(), Gtk.ListBoxRow)
        report["delete_key"] = key(win, "Delete") and win.dialog.heading
        win.dialog.respond("cancel")
        win.listbox.emit("row-activated", win.listbox.get_selected_row())     # double click / Enter
        wait(lambda: Path(record).exists() and Path(record).read_text(), started)

    def started():
        report["started"] = Path(record).read_text().split()
        win.set_default_size(500, 600)              # remembered at close, used by the next window
        win.close()
        GLib.timeout_add(100, reopen)

    def reopen():
        report["saved"] = json.loads(paths.window_state().read_text())
        w2 = gui.Window(a, paths, None, ["true"])
        w2.present()
        wait(lambda: w2.rows and w2.narrow, lambda: narrow(w2))
        return False

    def narrow(w2):
        report["narrow_list"] = [w2.side.get_visible(), w2.detail_side.get_visible()]
        w2.listbox.emit("row-activated", w2.listbox.get_row_at_index(0))
        report["narrow_details"] = [w2.side.get_visible(), w2.detail_side.get_visible(), w2.back.get_visible(),
                                    w2.details_title.get_text()]
        key(w2, "Escape")
        report["narrow_back"] = [w2.side.get_visible(), w2.detail_side.get_visible()]
        app.quit()

    wait(lambda: win.rows and win.get_width() > 0, listed)


app = Gtk.Application(application_id="io.github.Jimbambuli.ApkStarter.LayoutProbe",
                      flags=Gio.ApplicationFlags.NON_UNIQUE)
app.connect("activate", activate)
app.run([sys.argv[0]])
print(json.dumps(report))
