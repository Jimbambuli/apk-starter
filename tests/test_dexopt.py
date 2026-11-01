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
    assert vm["args"][1:] == ["-cp", "/app/lib/art/../java/dex/android_translation_layer/api-impl.jar:"
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
