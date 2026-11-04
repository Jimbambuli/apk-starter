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


# -- packed after the run (Marcel 4.11.2026): <name>.log.gz and <name>.tar.gz ---------------------------------------
import gzip  # noqa: E402
import tarfile  # noqa: E402


def test_pack_compresses_the_log_and_the_debug_folder(tmp_path):
    p = _paths(tmp_path)
    log, debug = _make(p, "org.a", 1, "log", 5000), _make(p, "org.a", 2, "debug", 5000)
    assert diag.pack(p, log) == log.with_name(log.name + ".gz") and not log.exists()
    assert gzip.decompress(log.with_name(log.name + ".gz").read_bytes()) == b"x" * 5000
    packed = diag.pack(p, debug)
    assert packed == debug.with_name(debug.name + ".tar.gz") and not debug.exists()
    with tarfile.open(packed) as t:
        assert sorted(t.getnames()) == [debug.name, f"{debug.name}/atl.log", f"{debug.name}/manifest.json"]
    assert [(r.path, r.kind) for r in diag.recordings(p, "org.a")] == [(packed, "debug"),
                                                                      (log.with_name(log.name + ".gz"), "log")]


def test_prune_counts_the_archives(tmp_path):
    p = _paths(tmp_path)
    logs = [diag.pack(p, _make(p, "org.a", n, "log")) for n in range(5)]
    debugs = [diag.pack(p, _make(p, "org.a", n + 10, "debug")) for n in range(7)]
    diag.prune(p, "org.a")
    assert [f.exists() for f in logs] == [False] * 2 + [True] * 3
    assert [f.exists() for f in debugs] == [False] * 2 + [True] * 5


def test_pack_leaves_what_is_not_a_recording(tmp_path):
    p = _paths(tmp_path)
    mine = tmp_path / "chosen-folder"                  # --debug=DIR: the user's folder stays as it is
    mine.mkdir()
    assert diag.pack(p, mine) is None and mine.is_dir()
