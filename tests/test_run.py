import os
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from apk_starter.apkinfo import ApkInfo
from apk_starter.errors import StarterError
from apk_starter.run import header_fields, read_build_info, run_atl, runtime_name

FAKE = Path(__file__).with_name("fake_atl.py")


def fake_cmd():
    return [sys.executable, str(FAKE)]


def fake_env(mode):
    return {**os.environ, "FAKE_ATL_MODE": mode}


def test_exit_code_passed_through(tmp_path):
    assert run_atl(fake_cmd(), fake_env("exit:3"), None, {}) == 3


def test_restart_status_starts_atl_again_into_the_same_log(tmp_path):
    """ATL ends with 75 when the app restarts itself (ProcessPhoenix): the starter starts it again."""
    log = tmp_path / "a.log"
    assert run_atl(fake_cmd(), fake_env(f"restart:{tmp_path / 'once'}"), log, {"format": "1"}) == 0
    text = log.read_text()
    assert text.count("fake atl output\n") == 2 and text.count("# apk-starter: format=1") == 1
    assert "# apk-starter: restart\n" in text and text.endswith(text[text.rindex("# apk-starter: exit=0"):])


def test_restart_is_limited(tmp_path, monkeypatch):
    import apk_starter.run as run
    monkeypatch.setattr(run, "MAX_RESTARTS", 2)
    calls = tmp_path / "calls"
    cmd = [sys.executable, "-c", f"open({str(calls)!r}, 'a').write('x'); raise SystemExit(75)"]
    assert run_atl(cmd, dict(os.environ), None, {}) == 75
    assert calls.read_text() == "xxx"


def test_signal_maps_to_128_plus_n_and_trailer(tmp_path, capsys):
    log = tmp_path / "a.log"
    assert run_atl(fake_cmd(), fake_env("signal:11"), log, {"format": "1"}) == 139
    lines = log.read_text().splitlines()
    assert lines[0] == "# apk-starter: format=1" and "# apk-starter: signal=SIGSEGV" in lines
    assert re.fullmatch(r"# apk-starter: duration=\d+\.\d", lines[-1])
    assert "killed by SIGSEGV" in capsys.readouterr().err


def test_trailer_on_new_line(tmp_path):
    log = tmp_path / "a.log"
    run_atl(fake_cmd(), fake_env("noeol"), log, {})
    assert "tail-ohne-umbruch\n# apk-starter: exit=0\n" in log.read_text()


def test_header_fields_order_and_values():
    info = ApkInfo(Path("/h/e.apk"), "org.example.demo", "Demo", None, 7, 21, 34, None)
    started = datetime(2026, 11, 1, 7, 0, 5, tzinfo=ZoneInfo("Europe/Zurich"))
    h = header_fields(info, {"atl_commit": "a" * 40, "art_commit": "b" * 40}, "runtime/x/y/1",
                      ["atl", "a b\nc"], started, "0.8.3")
    assert list(h) == ["format", "launcher_version", "apk", "package", "version_name", "version_code",
                       "min_sdk", "target_sdk", "atl_commit", "art_commit", "runtime", "started", "args"]
    assert h["format"] == "1" and h["version_name"] == "" and h["started"] == "2026-11-01T07:00:05+01:00"
    assert "\n" not in h["args"]


def test_build_info_and_runtime_fallbacks(tmp_path):
    assert read_build_info(tmp_path / "nope") == {"atl_commit": "unknown", "art_commit": "unknown"}
    assert runtime_name(tmp_path / "nope") == "none"
    (tmp_path / "bi").write_text("atl_commit=abc\nart_commit=def\n")
    assert read_build_info(tmp_path / "bi")["atl_commit"] == "abc"
    (tmp_path / "fi").write_text("[Application]\nname=x\nruntime=runtime/org.gnome.Platform/x86_64/51\n")
    assert runtime_name(tmp_path / "fi") == "runtime/org.gnome.Platform/x86_64/51"


def test_missing_atl_binary(tmp_path):
    with pytest.raises(StarterError, match="not found"):
        run_atl(["/nonexistent/atl"], {}, None, {})
