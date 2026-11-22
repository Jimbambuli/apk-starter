import os
from pathlib import Path

import pytest

from apk_starter import isolation
from apk_starter.paths import Paths


def sandbox(tmp_path, pkg="org.example.a"):
    p = Paths(tmp_path / "data", tmp_path / "cache", Path("/app"))
    p.data(pkg).mkdir(parents=True)
    p.data("org.example.b").mkdir(parents=True)
    env = {"HOME": str(tmp_path), "XDG_CONFIG_HOME": str(tmp_path / "cfg"), "FLATPAK_ID": "io.example.S"}
    return isolation.Sandbox(p, pkg, f"io.example.S.{pkg}", env), p


def test_command_exposes_only_own_data_and_shared_read_only(tmp_path):
    sb, p = sandbox(tmp_path)
    cmd = sb.command(["android-translation-layer", "x.apk"], {"WAYLAND_DISPLAY": "w", "ATL_X": "1", "LANG": "C"})
    rw = [a.split("=", 1)[1] for a in cmd if a.startswith("--sandbox-expose-path=")]
    ro = [a.split("=", 1)[1] for a in cmd if a.startswith("--sandbox-expose-path-ro=")]
    assert str(p.data("org.example.a")) in rw
    assert not any("org.example.b" in x for x in rw + ro)
    assert ro == [str(p.apps_dir()), str(p.cache_home / "art")]
    assert "--sandbox-flag=share-display" in cmd and "--sandbox" in cmd
    envs = [a for a in cmd if a.startswith("--env=")]
    assert "--env=ATL_X=1" in envs and not any(a.startswith("--env=WAYLAND_DISPLAY=") for a in envs)
    assert f"--env=XDG_DATA_HOME={p.data_home / 'sandbox/org.example.a/data'}" in envs
    assert cmd[-2:] == ["android-translation-layer", "x.apk"]


def test_user_dirs(tmp_path):
    (tmp_path / "user-dirs.dirs").write_text('XDG_DOWNLOAD_DIR="$HOME/Dl"\nXDG_MUSIC_DIR="$HOME/"\n'
                                             'XDG_VIDEOS_DIR="/media/v"\n')
    assert isolation.user_dirs(tmp_path, Path("/home/u")) == {"DOWNLOAD": Path("/home/u/Dl"),
                                                              "VIDEOS": Path("/media/v")}


@pytest.mark.parametrize("argv", [["--list"], ["--remove", "org.example.b"], ["--details", "../x"],
                                  ["org.example.b", "--size", "1x1"], ["--install-request"]])
def test_relay_refuses_what_is_no_app_request(tmp_path, argv):
    sb, _p = sandbox(tmp_path)
    r, w = os.pipe()
    try:
        with pytest.raises(ValueError):
            sb.run_request({"argv": argv}, [w, w])
    finally:
        os.close(r)
        os.close(w)


def test_relay_round_trip_passes_caller_identity_and_files(tmp_path, monkeypatch):
    sb, p = sandbox(tmp_path)
    seen = {}

    def fake_run(argv, env, **kw):
        seen["argv"], seen["installer"] = argv, env.get("ATL_INSTALLER_PACKAGE")
        seen["content"] = Path(argv[2]).read_bytes()
        return type("R", (), {"returncode": 3})()
    monkeypatch.setattr(isolation.subprocess, "run", fake_run)
    sb.sockets.mkdir(parents=True)
    sb._start_relay()
    try:
        apk = tmp_path / "base.apk"
        apk.write_bytes(b"PK-data")
        rc = isolation.relay(["--install-request", str(apk)],
                             {isolation.RELAY_ENV: str(sb.relay), "ATL_INSTALLER_PACKAGE": "forged"})
    finally:
        sb.close()
    assert rc == 3
    assert seen["argv"][:2] == [str(p.program()), "--install-request"] and seen["argv"][2].endswith("0-base.apk")
    assert seen["installer"] == "org.example.a" and seen["content"] == b"PK-data"
