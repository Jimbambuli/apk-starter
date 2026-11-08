"""Recordings (plain logs, Debug) and their clean-up."""
from datetime import datetime
from pathlib import Path

import pytest

from apk_starter import diag
from apk_starter.paths import Paths

def _paths(tmp_path) -> Paths:
    return Paths(data_home=tmp_path / "d", cache_home=tmp_path / "c", prefix=tmp_path / "p")


def _make(p: Paths, pkg: str, n: int, kind: str, size: int = 10) -> Path:
    """Recording number n (the n-th second of 2026-11-01 10:00) of kind "log" or "debug"."""
    logs = p.logs_dir()
    logs.mkdir(parents=True, exist_ok=True)
    name = f"{pkg}-20261101-1000{n:02d}"
    if kind == "log":
        f = logs / f"{name}.log"
        f.write_bytes(b"x" * size)
        return f
    (logs / name).mkdir()
    (logs / name / "manifest.json").write_text("{}")
    (logs / name / "atl.log").write_bytes(b"x" * size)
    return logs / name


def test_prune_keeps_the_last_three_logs_and_five_debug_recordings_per_app(tmp_path):
    p = _paths(tmp_path)
    logs = [_make(p, "org.a", n, "log") for n in range(6)]
    debugs = [_make(p, "org.a", n + 10, "debug") for n in range(8)]
    other = [_make(p, "org.b", n, "log") for n in range(5)] + [_make(p, "org.b", 20, "debug")]
    (p.logs_dir() / "notes.txt").write_text("mine")
    diag.prune(p, "org.a")
    assert [f.exists() for f in logs] == [False] * 3 + [True] * 3        # the newest stay
    assert [f.exists() for f in debugs] == [False] * 3 + [True] * 5
    assert all(f.exists() for f in other) and (p.logs_dir() / "notes.txt").exists()


def test_prune_debug_by_size_older_first_but_the_newest_stays(tmp_path, monkeypatch):
    p = _paths(tmp_path)
    monkeypatch.setattr(diag, "DEBUG_MAX_BYTES", 100)
    old, mid, new = (_make(p, "org.a", n, "debug", size) for n, size in ((1, 60), (2, 60), (3, 30)))
    diag.prune(p, "org.a")
    assert (old.exists(), mid.exists(), new.exists()) == (False, True, True)       # 30 + 60 + manifest <= 100
    monkeypatch.setattr(diag, "DEBUG_MAX_BYTES", 10)
    diag.prune(p, "org.a")
    assert (mid.exists(), new.exists()) == (False, True)                           # one alone may exceed it


def _recordings(p: Paths) -> dict[str, Path]:
    logs = p.logs_dir()
    logs.mkdir(parents=True)
    made = {}
    for name in ("org.a-20261101-101010.log", "org.ab-20261101-121212.log", "junk.log", "org.a-1.log"):
        made[name] = logs / name
        made[name].write_text("x\n")
    for name, manifest in (("org.a-20261101-111111", True), ("org.a-20261101-131313", False)):
        made[name] = logs / name
        made[name].mkdir()
        if manifest:
            (made[name] / "manifest.json").write_text("{}")
    return made


def test_recordings_newest_first_per_package(tmp_path):
    p = _paths(tmp_path)
    m = _recordings(p)
    got = diag.recordings(p, "org.a")
    assert [(r.path, r.kind) for r in got] == [(m["org.a-20261101-111111"], "debug"),
                                               (m["org.a-20261101-101010.log"], "log")]
    assert got[0].started == datetime(2026, 11, 1, 11, 11, 11) and got[0].package == "org.a"
    assert [r.path.name for r in diag.recordings(p)][:1] == ["org.ab-20261101-121212.log"]
    assert diag.recordings(_paths(tmp_path / "none"), "org.a") == []


def test_debug_options_for_the_recorder(tmp_path):
    pytest.importorskip("atl_doctor")
    p = _paths(tmp_path)
    cmd = ["android-translation-layer", "/c/launch/org.a.apk", "-w", "800", "--gapplication-app-id", "x"]
    opts = diag.record_options(p, cmd, {"ANDROID_APP_DATA_DIR": "/d"}, tmp_path / "rec")
    assert opts.apk == Path("/c/launch/org.a.apk") and opts.atl_args == cmd[2:] and opts.prefix == p.prefix
    assert opts.env == {"ANDROID_APP_DATA_DIR": "/d", "ANDROID_LOG_TAGS": "*:v [bionic_linker]:i"} and opts.trace
    assert opts.light  # attached agent, stub hits from ATL, sampling: the app keeps its speed
    assert opts.stub_list is None
    p.stub_list().parent.mkdir(parents=True)
    p.stub_list().write_text("x")
    assert diag.record_options(p, cmd, {}, tmp_path / "rec").stub_list == p.stub_list()


def test_debug_dir_name(tmp_path):
    p = _paths(tmp_path)
    assert diag.default_debug_dir(p, "org.a", datetime(2026, 11, 1, 9, 5, 7)) == p.logs_dir() / "org.a-20261101-090507"


def test_debug_restart_records_again(tmp_path):
    """Exit 75 (the app restarts itself) under Debug: recorded again into a new recording, like run.run_atl."""
    p = _paths(tmp_path)
    p.logs_dir().mkdir(parents=True, exist_ok=True)
    seen = []

    def once(paths, cmd, env, out):
        out.mkdir()
        seen.append(out)
        return 75 if len(seen) == 1 else 3
    first = diag.default_debug_dir(p, "org.a", datetime(2026, 11, 3, 6, 0, 0))
    assert diag.record_restarting(p, ["atl"], {}, first, "org.a", record_once=once) == 3
    assert seen[0] == first and len(seen) == 2 and seen[1] != first
    assert seen[1].name.startswith("org.a-")


def test_debug_restart_is_limited(tmp_path, monkeypatch):
    p = _paths(tmp_path)
    calls = []
    monkeypatch.setattr(diag, "default_debug_dir", lambda paths, pkg, now: tmp_path / f"r{len(calls)}")
    assert diag.record_restarting(p, ["atl"], {}, tmp_path / "first", "org.a",
                                  record_once=lambda *a: calls.append(1) or 75) == 75
    from apk_starter.run import MAX_RESTARTS
    assert len(calls) == MAX_RESTARTS + 1


# -- packed after the run: <name>.tar.gz or <name>-FULL.tar.gz through atl-doctor pack ----------------
import subprocess  # noqa: E402
import sys  # noqa: E402

from apk_starter.errors import StarterError  # noqa: E402


def _archive(p: Paths, pkg: str, n: int, full: bool = False) -> Path:
    name = f"{pkg}-20261101-1000{n:02d}" + ("-FULL" if full else "") + ".tar.gz"
    p.logs_dir().mkdir(parents=True, exist_ok=True)
    f = p.logs_dir() / name
    f.write_bytes(b"x")
    return f


def test_full_archives_are_recordings_and_say_so(tmp_path):
    p = _paths(tmp_path)
    plain, full = _archive(p, "org.a", 1), _archive(p, "org.a", 2, full=True)
    assert [(r.path, r.kind, r.full) for r in diag.recordings(p, "org.a")] == [(full, "debug", True),
                                                                              (plain, "debug", False)]


def test_pack_runs_atl_doctor_and_removes_the_folder(tmp_path):
    p = _paths(tmp_path)
    folder, calls = _make(p, "org.a", 3, "debug"), []

    def run(cmd, **kw):
        calls.append((cmd, kw["cwd"]))
        out = folder.with_name(folder.name + ("-FULL" if "--full" in cmd else "") + ".tar.gz")
        out.write_bytes(b"x")
        return subprocess.CompletedProcess(cmd, 0, stdout=f"{out}\n", stderr="")
    archive = diag.pack(p, folder, full=True, run=run)
    assert archive == folder.with_name(folder.name + "-FULL.tar.gz") and not folder.exists()
    cmd, cwd = calls[0]
    assert cmd == [sys.executable, "-m", "atl_doctor", "pack", "--full", str(folder)] and (cwd / "atl_doctor").is_dir()


def test_pack_failure_keeps_the_folder(tmp_path):
    p = _paths(tmp_path)
    folder = _make(p, "org.a", 3, "debug")
    run = lambda cmd, **kw: subprocess.CompletedProcess(cmd, 3, stdout="", stderr="boom\n")  # noqa: E731
    with pytest.raises(StarterError, match=r"stays in .*org\.a-20261101-100003: boom"):
        diag.pack(p, folder, run=run)
    assert folder.is_dir()


def test_pack_leaves_what_is_not_a_recording(tmp_path):
    p = _paths(tmp_path)
    mine = tmp_path / "chosen-folder"                  # --debug=DIR: the user's folder stays as it is
    mine.mkdir()
    assert diag.pack(p, mine) is None and mine.is_dir()


def test_prune_counts_the_archives(tmp_path):
    p = _paths(tmp_path)
    debugs = [_archive(p, "org.a", n, full=n % 2 == 0) for n in range(7)]
    diag.prune(p, "org.a")
    assert [f.exists() for f in debugs] == [False] * 2 + [True] * 5


# -- the screenshot of the view tree ----------------------------------------------------------------
def test_shot_is_named_like_the_tree_of_its_mark(tmp_path):
    pytest.importorskip("atl_doctor")
    rec = tmp_path / "rec"
    rec.mkdir()
    from atl_doctor.capture import Capture, Manifest
    Capture(rec).write_manifest(Manifest())
    (rec / "marks.tsv").write_text("0\t1\t2\tstart\n1\t3\t4\trunning\n")
    taken = []
    got = diag.shot_for_mark(rec, "running", lambda dest: taken.append(dest) or dest.parent.mkdir(exist_ok=True) or True)
    assert got == rec / "shots/01-running.png" and taken == [got]
    assert diag.shot_for_mark(rec, "nope", lambda d: True, wait_s=0.2) is None
    assert diag.shot_for_mark(rec, "running", lambda d: False) is None


def test_portal_screenshot_copies_the_file_and_fails_quietly(tmp_path, monkeypatch):
    from apk_starter import portal
    src = tmp_path / "Screenshot 1.png"
    src.write_bytes(b"png")
    monkeypatch.setattr(portal, "screenshot", lambda: src.as_uri())
    dest = tmp_path / "shots/01-running.png"
    assert diag.portal_screenshot(dest) is True and dest.read_bytes() == b"png" and not src.exists()

    def unavailable():
        raise portal.PortalUnavailable("no portal")
    monkeypatch.setattr(portal, "screenshot", unavailable)
    assert diag.portal_screenshot(tmp_path / "x.png") is False
