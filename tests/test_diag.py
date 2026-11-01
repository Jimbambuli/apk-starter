"""Diagnose levels (Log, Debug) and reports of recordings."""
from datetime import datetime
from pathlib import Path

import pytest

from apk_starter import diag
from apk_starter.errors import StarterError, UsageError
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


def test_find_recording(tmp_path):
    p = _paths(tmp_path)
    m = _recordings(p)
    assert diag.find_recording(p, None) == m["org.ab-20261101-121212.log"]
    assert diag.find_recording(p, "org.a") == m["org.a-20261101-111111"]
    assert diag.find_recording(p, str(m["junk.log"])) == m["junk.log"]
    with pytest.raises(UsageError):
        diag.find_recording(p, "org.nothing")
    with pytest.raises(StarterError):
        diag.find_recording(_paths(tmp_path / "none"), None)


def _log_with_agent(tmp_path) -> Path:
    log = tmp_path / "org.example.demo-20261101-101010.log"
    log.write_text("# apk-starter: package=org.example.demo\n# apk-starter: version_name=1.2\n"
                   "W/AssetManager(1): AssetManager.destroy(): STUB\n"
                   "java.lang.NoSuchMethodError: No virtual method foo()V in class Landroid/view/View; or its "
                   "super classes\n# apk-starter: signal=SIGSEGV\n# apk-starter: exit=139\n"
                   f"# atl-doctor-agent: {AGENT_E}\n")
    return log


def test_report_of_a_log_and_its_view(tmp_path):
    pytest.importorskip("atl_doctor")
    r = diag.load_report(_log_with_agent(tmp_path))
    assert "IllegalStateException: boom" in diag.report_text(r)
    view = diag.report_view(r)
    assert [s.title for s in view] == ["Summary", "Crash", "ATL gaps", "Exceptions"]
    summary, crash, gaps, exc = view
    assert [i.title for i in summary.items] == ["org.example.demo 1.2", "killed by SIGSEGV (exit status 139)"]
    assert crash.items[0].title == "fatal signal SIGSEGV"
    assert [i.title for i in gaps.items] == ["missing method android.view.View#foo()V",
                                             "AssetManager: AssetManager.destroy(): STUB"]
    assert exc.items[0].title == "java.lang.IllegalStateException: boom"
    assert "at org.example.demo.Main.onCreate" in exc.items[0].details


def test_report_view_without_findings(tmp_path):
    pytest.importorskip("atl_doctor")
    log = tmp_path / "x.log"
    log.write_text("# apk-starter: package=org.example.demo\n# apk-starter: exit=0\nall fine\n")
    view = diag.report_view(diag.load_report(log))
    assert [s.title for s in view] == ["Summary", "Findings"]
    assert view[1].items[0].title == "No crash, ATL gap or exception was found."
    assert view[0].items[-1].title == "exit status 0"


def test_report_of_a_broken_recording_is_one_error(tmp_path):
    pytest.importorskip("atl_doctor")
    d = tmp_path / "rec"
    d.mkdir()
    (d / "manifest.json").write_text("not json")
    with pytest.raises(StarterError):
        diag.load_report(d)
    with pytest.raises(StarterError):
        diag.load_report(tmp_path / "missing.log")


def test_debug_options_for_the_recorder(tmp_path):
    pytest.importorskip("atl_doctor")
    p = _paths(tmp_path)
    cmd = ["android-translation-layer", "/c/launch/org.a.apk", "-w", "800", "--gapplication-app-id", "x"]
    opts = diag.record_options(p, cmd, {"ANDROID_APP_DATA_DIR": "/d"}, tmp_path / "rec")
    assert opts.apk == Path("/c/launch/org.a.apk") and opts.atl_args == cmd[2:] and opts.prefix == p.prefix
    assert opts.env == {"ANDROID_APP_DATA_DIR": "/d", "ANDROID_LOG_TAGS": "*:v"} and opts.trace
    assert opts.light  # attached agent, stub hits from ATL, sampling: the app keeps its speed
    assert opts.stub_list is None
    p.stub_list().parent.mkdir(parents=True)
    p.stub_list().write_text("x")
    assert diag.record_options(p, cmd, {}, tmp_path / "rec").stub_list == p.stub_list()


def test_debug_dir_name(tmp_path):
    p = _paths(tmp_path)
    assert diag.default_debug_dir(p, "org.a", datetime(2026, 11, 1, 9, 5, 7)) == p.logs_dir() / "org.a-20261101-090507"
