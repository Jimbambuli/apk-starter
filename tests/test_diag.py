"""Diagnose levels (Log, Debug) and the recordings they leave."""
from datetime import datetime
from pathlib import Path

import pytest

from apk_starter import diag
from apk_starter.paths import Paths

AGENT_E = "E\t200\t7\tjava.lang.IllegalStateException\tboom\tB:android.view.View.measure\t" \
          "B:android.view.View.measure|org.example.demo.Main.onCreate"


def _paths(tmp_path) -> Paths:
    return Paths(data_home=tmp_path / "d", cache_home=tmp_path / "c", prefix=tmp_path / "p")


def _install_agent(p: Paths) -> None:
    for f in (p.agent(), p.jvmti_plugin()):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"")


def test_agent_args_only_when_agent_and_plugin_are_there(tmp_path):
    p = _paths(tmp_path)
    out = tmp_path / "x.log.agent"
    assert diag.agent_args(p, out) == []
    _install_agent(p)
    assert diag.agent_args(p, out) == [
        "-X", f"-agentpath:{p.agent()}={out},late={p.jvmti_plugin()},latesec=3"]
    assert diag.agent_args(p, tmp_path / "a,b.agent") == []        # the agent splits its options at commas


def test_agent_output_is_appended_to_the_log(tmp_path):
    log, out = tmp_path / "a.log", tmp_path / "a.log.agent"
    log.write_text("# apk-starter: exit=0\n")
    out.write_text(f"I\t1\t2\tlate: plugin loaded\n{AGENT_E}\nE\t3\t4\tpart")      # last line cut off
    diag.append_agent_output(log, out)
    assert log.read_text().splitlines() == ["# apk-starter: exit=0", "# atl-doctor-agent: I\t1\t2\tlate: plugin loaded",
                                            f"# atl-doctor-agent: {AGENT_E}"]
    assert not out.exists()
    diag.append_agent_output(log, out)                                  # no agent output: nothing happens
    assert len(log.read_text().splitlines()) == 3


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
