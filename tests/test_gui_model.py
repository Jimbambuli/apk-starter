"""View model of the management window: plain data, no GTK."""
import json
import os

from apk_starter.gui_model import (AppRow, WindowState, filter_rows, is_narrow, key_action, load_rows, load_state,
                                   save_state, sort_rows, start_argv)
from apk_starter.paths import Paths


def _paths(tmp_path) -> Paths:
    return Paths(data_home=tmp_path / "d", cache_home=tmp_path / "c", prefix=tmp_path / "p")


def _meta(p: Paths, pkg: str, **kw) -> None:
    p.apps_dir().mkdir(parents=True, exist_ok=True)
    p.meta(pkg).write_text(json.dumps({"package": pkg, **kw}))


def _row(pkg, label, size=0, installed=None, started=None) -> AppRow:
    return AppRow(pkg, label, "", size, None, None, installed, started, False)


def test_rows_sorted_by_name_with_icon_version_size_and_dates(tmp_path):
    p = _paths(tmp_path)
    _meta(p, "org.b.app", label="Bravo", version_name="2.0", target_sdk=33)
    p.apk("org.b.app").write_bytes(b"apk")
    os.utime(p.apk("org.b.app"), (1000, 1000))
    p.started("org.b.app").touch()
    os.utime(p.started("org.b.app"), (2000, 2000))
    _meta(p, "org.a.app", label=None, version_name=None)
    p.icon("org.b.app").write_bytes(b"png")
    p.data("org.b.app").mkdir(parents=True)
    (p.data("org.b.app") / "f").write_bytes(b"x" * 10)
    _meta(p, "org.c.app", label="alpha", target_sdk="x")
    assert load_rows(p) == [
        AppRow("org.c.app", "alpha", "", 0, None, None, None, None, False),
        AppRow("org.b.app", "Bravo", "2.0", 10, p.icon("org.b.app"), 33, 1000.0, 2000.0, False),
        AppRow("org.a.app", "org.a.app", "", 0, None, None, None, None, False),
    ]


def test_filter_matches_name_and_package_ignoring_case():
    rows = [_row("org.mozilla.fennec", "Fennec"), _row("net.gsantner.markor", "Markor")]
    assert filter_rows(rows, "") == rows
    assert filter_rows(rows, "  ") == rows
    assert filter_rows(rows, "FEN") == rows[:1]
    assert filter_rows(rows, "gsantner") == rows[1:]
    assert filter_rows(rows, "zzz") == []


def test_sort_by_name_started_installed_and_size():
    a = _row("a.a", "alpha", size=5, installed=30, started=None)
    b = _row("b.b", "Bravo", size=50, installed=10, started=200)
    c = _row("c.c", "charlie", size=0, installed=20, started=100)
    rows = [c, b, a]
    assert sort_rows(rows, "name") == [a, b, c]
    assert sort_rows(rows, "started") == [b, c, a]          # newest first, never started last
    assert sort_rows(rows, "installed") == [a, c, b]
    assert sort_rows(rows, "size") == [b, a, c]
    assert sort_rows(rows, "nonsense") == [a, b, c]


def test_window_state_round_trip_and_defaults(tmp_path):
    f = tmp_path / "x" / "window.json"
    assert load_state(f) == WindowState()
    save_state(f, WindowState(width=1000, height=700, sort="size", selected="org.a.app"))
    assert load_state(f) == WindowState(1000, 700, "size", "org.a.app")
    f.write_text('{"width": "big", "height": 50, "sort": "bogus", "selected": 7}')
    assert load_state(f) == WindowState()           # each bad value falls back
    f.write_text("not json")
    assert load_state(f) == WindowState()


def test_keys():
    assert key_action("f", ctrl=True, in_search=False) == "search"
    assert key_action("f", ctrl=True, in_search=True) == "search"
    assert key_action("Return", ctrl=False, in_search=False) == "start"
    assert key_action("KP_Enter", ctrl=False, in_search=False) == "start"
    assert key_action("Return", ctrl=False, in_search=True) == "to-list"
    assert key_action("Delete", ctrl=False, in_search=False) == "uninstall"
    assert key_action("Delete", ctrl=False, in_search=True) is None     # deletes text in the search
    assert key_action("Escape", ctrl=False, in_search=False) == "back"
    assert key_action("a", ctrl=False, in_search=False) is None         # typing goes to the search by itself


def test_narrow():
    assert is_narrow(560) and not is_narrow(720)


def test_start_argv():
    assert start_argv(["/app/bin/apk-starter"], "org.a.app", None) == ["/app/bin/apk-starter", "org.a.app"]
    assert start_argv(["/app/bin/apk-starter"], "org.a.app", "log") == \
        ["/app/bin/apk-starter", "--log", "--", "org.a.app"]
    assert start_argv(["/app/bin/apk-starter"], "org.a.app", "debug") == \
        ["/app/bin/apk-starter", "--debug", "--", "org.a.app"]
