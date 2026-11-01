import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "io.github.Jimbambuli.ApkStarter.yml"


def manifest_commits() -> dict[str, str]:
    """Module name -> pinned commit of its git source (regex, no YAML library)."""
    pins: dict[str, str] = {}
    module = None
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*- name: (\S+)", line)
        if m:
            module = m.group(1)
        m = re.match(r"\s*commit: (\S+)", line)
        if m and module is not None:
            pins[module] = m.group(1)
    return pins


def finish_args() -> set[str]:
    text = MANIFEST.read_text(encoding="utf-8")
    block = re.search(r"^finish-args:\n((?:  - .*\n)+)", text, re.M)
    return {l.strip()[2:] for l in block.group(1).splitlines()}


def test_build_info_matches_manifest():
    pins = manifest_commits()
    bi = dict(l.split("=", 1) for l in (ROOT / "data/build-info").read_text().split())
    assert bi["atl_commit"] == pins["android_translation_layer"] and bi["art_commit"] == pins["art_standalone"]
    assert bi["bionic_commit"] == pins["bionic_translation"]


def test_finish_args_exact():
    assert finish_args() == {"--socket=wayland", "--socket=fallback-x11", "--device=dri",
                             "--socket=pulseaudio", "--share=network", "--share=ipc"}


def test_all_git_sources_pinned():
    pins = manifest_commits()
    assert {"android_translation_layer", "art_standalone", "bionic_translation", "wolfssl"} <= pins.keys()
    assert all(re.fullmatch(r"[0-9a-f]{40}", c) for c in pins.values())
