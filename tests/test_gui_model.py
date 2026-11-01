"""View model of the management window: plain data, no GTK."""
import json

from apk_starter.gui_model import AppRow, load_rows, start_argv
from apk_starter.paths import Paths


def _paths(tmp_path) -> Paths:
    return Paths(data_home=tmp_path / "d", cache_home=tmp_path / "c", prefix=tmp_path / "p")


def _meta(p: Paths, pkg: str, **kw) -> None:
    p.apps_dir().mkdir(parents=True, exist_ok=True)
    p.meta(pkg).write_text(json.dumps({"package": pkg, **kw}))


def test_rows_sorted_by_name_with_icon_version_and_size(tmp_path):
    p = _paths(tmp_path)
    _meta(p, "org.b.app", label="Bravo", version_name="2.0")
    _meta(p, "org.a.app", label=None, version_name=None)
    p.icon("org.b.app").write_bytes(b"png")
    p.data("org.b.app").mkdir(parents=True)
    (p.data("org.b.app") / "f").write_bytes(b"x" * 10)
    _meta(p, "org.c.app", label="alpha")
    assert load_rows(p) == [
        AppRow("org.c.app", "alpha", "", 0, None),
        AppRow("org.b.app", "Bravo", "2.0", 10, p.icon("org.b.app")),
        AppRow("org.a.app", "org.a.app", "", 0, None),
    ]


def test_start_argv():
    assert start_argv(["/app/bin/apk-starter"], "org.a.app", log=False) == ["/app/bin/apk-starter", "org.a.app"]
    assert start_argv(["/app/bin/apk-starter"], "org.a.app", log=True) == \
        ["/app/bin/apk-starter", "--log", "--", "org.a.app"]
