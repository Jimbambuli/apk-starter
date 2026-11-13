"""Ahead-of-time preparation: boot image, app code (dex2oat) and native libraries, like Android's install dexopt."""
import json
import os
import time
import types
from pathlib import Path

import pytest

from apk_starter import dexopt
from apk_starter.dexopt import DexoptError
from apk_starter.paths import Paths
from apkgen import axml_manifest, make_apk

FAKE = Path(__file__).with_name("fake_art.py")
PKG = "org.example.demo"


@pytest.fixture
def art(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for prog in ("dalvikvm", "dex2oat"):
        (bindir / prog).symlink_to(FAKE)
    info = tmp_path / "flatpak-info"
    info.write_text("[Application]\nname=x\n\n[Instance]\napp-commit=aaaa\n")
    monkeypatch.setattr(dexopt, "FLATPAK_INFO", info)
    record = tmp_path / "art.jsonl"
    env = {"XDG_DATA_HOME": str(tmp_path / "data"), "XDG_CACHE_HOME": str(tmp_path / "cache"),
           "APK_STARTER_PREFIX": "/app", "PATH": f"{bindir}:{os.environ['PATH']}", "FAKE_ART_RECORD": str(record)}
    paths = Paths.from_env(env)

    def calls():
        return [json.loads(line) for line in record.read_text().splitlines()] if record.exists() else []
    return types.SimpleNamespace(env=env, paths=paths, calls=calls, info=info, tmp=tmp_path, record=record)


def arg(call, prefix):
    return next(a[len(prefix):] for a in call["args"] if a.startswith(prefix))


def test_cache_names_match_art():
    """Spelled exactly like ART logs them in the Flatpak (rundgang/startzeit-2026-11-01)."""
    p = Paths(Path("/d"), Path("/home/marcel/.var/app/X/cache"), Path("/app"))
    cache = "/home/marcel/.var/app/X/cache/art/x86_64/"
    assert str(dexopt.cache_file(p, dexopt.boot_image(p))) == cache + "app@lib@art@..@java@dex@art@oat@boot.art"
    assert str(dexopt.cache_file(p, dexopt.jar(p, "api-impl.jar"))) == \
        cache + "app@lib@art@..@java@dex@android_translation_layer@api-impl.jar@classes.dex"
    assert str(dexopt.cache_file(p, "/home/marcel/c/launch/org.jw.jwlibrary.mobile.apk")) == \
        cache + "home@marcel@c@launch@org.jw.jwlibrary.mobile.apk@classes.dex"
    assert dexopt.app_context(p) == ("PCL[/app/lib/art/../java/dex/android_translation_layer/ghax.jar];"
                                     "PCL[/app/lib/art/../java/dex/android_translation_layer/api-impl.jar]")


def test_android_data_moves_the_cache(art):
    p = Paths.from_env(art.env)
    assert dexopt.dalvik_cache(p, {"ANDROID_DATA": "/x"}) == Path("/x/dalvik-cache/x86_64")


def test_runtime_key_follows_the_flatpak_commit(art):
    k1 = dexopt.runtime_key(art.paths)
    art.info.write_text("[Instance]\napp-commit=bbbb\n")
    assert dexopt.runtime_key(art.paths) != k1


def test_runtime_key_without_flatpak_uses_build_info(art, tmp_path, monkeypatch):
    monkeypatch.setattr(dexopt, "FLATPAK_INFO", tmp_path / "missing")
    prefix = tmp_path / "prefix"
    (prefix / "share/apk-starter").mkdir(parents=True)
    (prefix / "share/apk-starter/build-info").write_text("atl_commit=1\n")
    p = Paths(art.paths.data_home, art.paths.cache_home, prefix)
    k1 = dexopt.runtime_key(p)
    (prefix / "share/apk-starter/build-info").write_text("atl_commit=2\n")
    assert dexopt.runtime_key(p) != k1


def test_runtime_is_prepared_once_by_art_itself(art):
    msgs = []
    dexopt.ensure_runtime(art.paths, art.env, msgs.append)
    vm, ghax = art.calls()
    assert vm["prog"] == "dalvikvm"
    assert vm["args"][0] == "-Ximage:/app/lib/art/../java/dex/art/oat/boot.art"
    jobs = f"-j{len(os.sched_getaffinity(0))}"      # ART's own dex2oat runs get the usable CPUs too
    assert vm["args"][1:] == ["-Ximage-compiler-option", jobs, "-Xcompiler-option", jobs,
                              "-cp", "/app/lib/art/../java/dex/android_translation_layer/api-impl.jar:"
                                     "/app/lib/art/../java/dex/android_translation_layer/framework-res.apk"]
    assert arg(ghax, "--dex-file=") == "/app/lib/art/../java/dex/android_translation_layer/ghax.jar"
    assert arg(ghax, "--class-loader-context=") == "PCL[];PCL[/app/lib/art/../java/dex/android_translation_layer/api-impl.jar]"
    assert arg(ghax, "--compiler-filter=") == "speed"
    assert dexopt.cache_file(art.paths, dexopt.jar(art.paths, "ghax.jar")).is_file()
    assert msgs and dexopt.runtime_ready(art.paths)
    dexopt.ensure_runtime(art.paths, art.env, msgs.append)
    assert len(art.calls()) == 2 and len(msgs) == 1


def test_flatpak_update_lets_art_check_the_runtime_again(art):
    dexopt.ensure_runtime(art.paths, art.env, lambda m: None)
    art.info.write_text("[Instance]\napp-commit=bbbb\n")
    assert not dexopt.runtime_ready(art.paths)
    dexopt.ensure_runtime(art.paths, art.env, lambda m: None)
    assert [c["prog"] for c in art.calls()] == ["dalvikvm", "dex2oat"] * 2


def test_runtime_not_ready_when_the_cache_was_cleaned(art):
    dexopt.ensure_runtime(art.paths, art.env, lambda m: None)
    dexopt.cache_file(art.paths, dexopt.boot_image(art.paths)).unlink()
    assert not dexopt.runtime_ready(art.paths)


def test_runtime_failure_is_reported_and_not_marked(art):
    art.env["FAKE_ART_FAIL"] = "dalvikvm"
    with pytest.raises(DexoptError, match="something went wrong"):
        dexopt.ensure_runtime(art.paths, art.env, lambda m: None)
    assert not dexopt.runtime_ready(art.paths)


def test_app_compile_replaces_files_and_remembers_the_filter(art, tmp_path):
    apk = make_apk(tmp_path / "demo.apk", manifest=axml_manifest())
    loc = str(apk)
    dexopt.ensure_runtime(art.paths, art.env, lambda m: None)
    assert dexopt.app_filter(art.paths, loc) is None
    dexopt.compile_app(art.paths, art.env, loc, "verify")
    call = art.calls()[-1]
    assert arg(call, "--dex-file=") == loc and arg(call, "--compiler-filter=") == "verify"
    assert arg(call, "--class-loader-context=") == dexopt.app_context(art.paths)
    assert arg(call, "--boot-image=") == "/app/lib/art/../java/dex/art/oat/boot.art"
    assert "--host" in call["args"] and arg(call, "--instruction-set=") == "x86_64"
    # as many threads as the process may use CPUs (taskset, cgroups), not as many as the computer has
    assert arg(call, "-j") == str(len(os.sched_getaffinity(0)))
    odex = dexopt.cache_file(art.paths, loc)
    assert odex.is_file() and odex.with_suffix(".vdex").is_file()
    assert sorted(p.name for p in odex.parent.iterdir() if p.is_dir()) == []      # no temp dir left
    assert dexopt.app_filter(art.paths, loc) == "verify"
    dexopt.compile_app(art.paths, art.env, loc, "speed")
    assert dexopt.app_filter(art.paths, loc) == "speed"
    os.utime(apk, ns=(time.time_ns(), time.time_ns() + 10**9))                   # APK replaced
    assert dexopt.app_filter(art.paths, loc) is None


def test_app_state_is_stale_after_a_flatpak_update(art, tmp_path):
    loc = str(make_apk(tmp_path / "demo.apk", manifest=axml_manifest()))
    dexopt.ensure_runtime(art.paths, art.env, lambda m: None)
    dexopt.compile_app(art.paths, art.env, loc, "speed")
    art.info.write_text("[Instance]\napp-commit=bbbb\n")
    assert dexopt.app_filter(art.paths, loc) is None


def test_failed_compile_keeps_the_old_code(art, tmp_path):
    loc = str(make_apk(tmp_path / "demo.apk", manifest=axml_manifest()))
    dexopt.ensure_runtime(art.paths, art.env, lambda m: None)
    dexopt.compile_app(art.paths, art.env, loc, "verify")
    old = dexopt.cache_file(art.paths, loc).read_bytes()
    art.env["FAKE_ART_FAIL"] = "dex2oat"
    with pytest.raises(DexoptError):
        dexopt.compile_app(art.paths, art.env, loc, "speed")
    assert dexopt.cache_file(art.paths, loc).read_bytes() == old
    assert dexopt.app_filter(art.paths, loc) == "verify"
    assert not [p for p in dexopt.cache_file(art.paths, loc).parent.iterdir() if p.is_dir()]


def test_missing_dex2oat_is_a_dexopt_error(art, tmp_path):
    loc = str(make_apk(tmp_path / "demo.apk", manifest=axml_manifest()))
    art.env["PATH"] = str(tmp_path / "nowhere")
    with pytest.raises(DexoptError):
        dexopt.compile_app(art.paths, art.env, loc, "verify")


def test_background_compile_runs_idle(art, tmp_path):
    loc = str(make_apk(tmp_path / "demo.apk", manifest=axml_manifest()))
    dexopt.compile_app(art.paths, art.env, loc, "speed", idle=True)
    assert art.calls()[-1]["sched_idle"]


def test_native_libraries_are_extracted_where_atl_looks(art, tmp_path):
    apk = make_apk(tmp_path / "demo.apk", manifest=axml_manifest(),
                   files={"lib/x86_64/libfoo.so": b"foo", "lib/arm64-v8a/libfoo.so": b"arm",
                          "lib/x86_64/../../evil.so": b"x", "assets/a": b"a"})
    lib = art.paths.data(PKG) / f"{PKG}.apk_" / "lib"
    dexopt.extract_natives(art.paths, PKG, apk)
    assert sorted(p.name for p in lib.iterdir()) == ["libfoo.so"]
    assert (lib / "libfoo.so").read_bytes() == b"foo"
    assert not (art.paths.data(PKG) / "evil.so").exists() and not (art.paths.data_home / "evil.so").exists()
    # newer than the APK: ATL keeps it, and so do we
    os.utime(lib / "libfoo.so", ns=(0, os.stat(apk).st_mtime_ns + 10**9))
    (lib / "libfoo.so").write_bytes(b"kept")
    os.utime(lib / "libfoo.so", ns=(0, os.stat(apk).st_mtime_ns + 10**9))
    dexopt.extract_natives(art.paths, PKG, apk)
    assert (lib / "libfoo.so").read_bytes() == b"kept"


def test_stale_installed_apps(art):
    apps = art.paths.apps_dir()
    apps.mkdir(parents=True)
    for pkg in ("org.example.a", "org.example.b"):
        make_apk(apps / f"{pkg}.apk", manifest=axml_manifest(package=pkg))
        (apps / f"{pkg}.json").write_text(json.dumps({"package": pkg}))
    dexopt.ensure_runtime(art.paths, art.env, lambda m: None)
    dexopt.compile_app(art.paths, art.env, str(apps / "org.example.a.apk"), "speed")
    assert dexopt.stale_installed(art.paths) == [str(apps / "org.example.b.apk")]


def _in_thread(fn):
    import threading
    t = threading.Thread(target=fn)
    t.start()
    return t


def _wait_for(cond, seconds=5.0):
    end = time.monotonic() + seconds
    while not cond() and time.monotonic() < end:
        time.sleep(0.02)
    assert cond()


def test_a_running_compile_pauses_while_an_app_is_started_and_goes_on_after(art, tmp_path):
    other = str(make_apk(tmp_path / "other.apk", manifest=axml_manifest()))
    started = str(make_apk(tmp_path / "started.apk", manifest=axml_manifest(package="org.example.s")))
    ticks = tmp_path / "ticks"
    art.env["FAKE_ART_TICKS"] = str(ticks)
    t = _in_thread(lambda: dexopt.compile_app(art.paths, art.env, other, "speed", idle=True))
    _wait_for(lambda: ticks.exists() and len(ticks.read_text()) >= 2)
    with dexopt.urgent(art.paths, started):
        time.sleep(0.5)                                 # the compile notices within PAUSE_POLL_S and stops
        frozen = len(ticks.read_text())
        time.sleep(0.6)
        assert len(ticks.read_text()) == frozen < 15
        assert t.is_alive()
    t.join(10)
    assert not t.is_alive() and len(ticks.read_text()) == 15
    assert dexopt.app_filter(art.paths, other) == "speed"


def test_the_started_app_is_not_paused_by_its_own_mark(art, tmp_path):
    loc = str(make_apk(tmp_path / "demo.apk", manifest=axml_manifest()))
    art.env["FAKE_ART_TICKS"] = str(tmp_path / "ticks")
    with dexopt.urgent(art.paths, loc):
        began = time.monotonic()
        dexopt.compile_app(art.paths, art.env, loc, "verify", is_urgent=True)
        assert time.monotonic() - began < 4
    assert dexopt.app_filter(art.paths, loc) == "verify"


def test_urgent_compile_does_not_stand_aside_for_another_process_on_the_same_app(art, tmp_path):
    loc = str(make_apk(tmp_path / "demo.apk", manifest=axml_manifest()))
    dexopt.ensure_runtime(art.paths, art.env, lambda m: None)
    with dexopt._flock(art.paths.cache_home / "apk-starter" / "locks" / (dexopt.cache_file(art.paths, loc).name + ".lock")):
        dexopt.compile_app(art.paths, art.env, loc, "speed", idle=True)         # an ordinary one waits for nobody: skipped
        assert dexopt.app_filter(art.paths, loc) is None
        dexopt.compile_app(art.paths, art.env, loc, "verify", is_urgent=True)   # the started one has its code now
        assert dexopt.app_filter(art.paths, loc) == "verify"
        n = len(art.calls())
        dexopt.compile_app(art.paths, art.env, loc, "verify", is_urgent=True)   # code is there: the holder improves it
        assert len(art.calls()) == n


def test_urgent_compile_keeps_better_code_that_arrived_meanwhile(art, tmp_path, monkeypatch):
    loc = str(make_apk(tmp_path / "demo.apk", manifest=axml_manifest()))
    dexopt.ensure_runtime(art.paths, art.env, lambda m: None)
    lock = art.paths.cache_home / "apk-starter" / "locks" / (dexopt.cache_file(art.paths, loc).name + ".lock")
    # the holder's FULL code arrives between the end of the quick compile and its rename
    state = {"n": 0}
    keep_real = dexopt._dex2oat

    def dex2oat(paths, env, location, context, flt, idle, keep=lambda: True):
        def keep_after_full():
            if state["n"] == 0:
                state["n"] = 1
                dexopt._write(dexopt._state_file(paths, location), json.dumps(
                    {"key": dexopt.runtime_key(paths), "apk": dexopt._apk_id(location), "filter": "speed"}))
                dexopt.cache_file(paths, location).write_bytes(b"full")
            return keep()
        return keep_real(paths, env, location, context, flt, idle, keep_after_full)
    monkeypatch.setattr(dexopt, "_dex2oat", dex2oat)
    with dexopt._flock(lock):
        dexopt.compile_app(art.paths, art.env, loc, "verify", is_urgent=True)
    assert dexopt.app_filter(art.paths, loc) == "speed"
    assert dexopt.cache_file(art.paths, loc).read_bytes() == b"full"


def test_the_runtime_is_not_paused_by_the_start_it_is_prepared_for(art, tmp_path):
    # the first start of an app holds its urgent mark while it prepares the runtime: ghax.jar must not stand aside
    started = str(make_apk(tmp_path / "started.apk", manifest=axml_manifest()))
    art.env["FAKE_ART_TICKS"] = str(tmp_path / "ticks")     # dex2oat runs longer than PAUSE_POLL_S
    with dexopt.urgent(art.paths, started):
        t = _in_thread(lambda: dexopt.ensure_runtime(art.paths, art.env, lambda _m: None))
        t.join(10)
        assert not t.is_alive()
    assert dexopt.runtime_ready(art.paths, art.env)
