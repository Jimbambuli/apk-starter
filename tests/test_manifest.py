import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "io.github.Jimbambuli.ApkStarter.yml"


def manifest_commits() -> dict[str, str]:
    """Module name -> pinned commit of its (first) git source (regex, no YAML library)."""
    pins: dict[str, str] = {}
    module = None
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*- name: (\S+)", line)
        if m:
            module = m.group(1)
        m = re.match(r"\s*commit: (\S+)", line)
        if m and module is not None:
            pins.setdefault(module, m.group(1))
    return pins


def finish_args() -> set[str]:
    text = MANIFEST.read_text(encoding="utf-8")
    block = re.search(r"^finish-args:\n((?:  [-#].*\n)+)", text, re.M)       # comment lines in between
    return {l.strip()[2:] for l in block.group(1).splitlines() if l.strip().startswith("- ")}


def test_build_info_matches_manifest():
    pins = manifest_commits()
    bi = dict(l.split("=", 1) for l in (ROOT / "data/build-info").read_text().split())
    assert bi["atl_commit"] == pins["android_translation_layer"] and bi["art_commit"] == pins["art_standalone"]
    assert bi["bionic_commit"] == pins["bionic_translation"]


def test_finish_args_exact():
    assert finish_args() == {"--socket=wayland", "--socket=fallback-x11", "--device=dri",
                             "--socket=pulseaudio", "--share=network", "--share=ipc",
                             # the desktop's GTK 4 settings and colours, read only (Spec 14, README «Permissions»)
                             "--filesystem=xdg-config/gtk-4.0:ro",
                             # the pictures of the KWin window buttons for ATL's title bar, read only
                             "--filesystem=xdg-config/gtk-3.0/assets:ro",
                             # KWin's title font/decoration/button size for the title bar's height, read only
                             "--filesystem=xdg-config/kdeglobals:ro", "--filesystem=xdg-config/kwinrc:ro",
                             "--filesystem=xdg-config/breezerc:ro",
                             # the apps' media in the desktop's media controls (MPRIS)
                             "--own-name=org.mpris.MediaPlayer2.*",
                             # microG: accounts in the secret store
                             "--talk-name=org.freedesktop.secrets",
                             # «Open with» an app for a file there (standard folders, read only;
                             # saving goes through the file chooser portal)
                             "--filesystem=xdg-download:ro", "--filesystem=xdg-pictures:ro",
                             "--filesystem=xdg-documents:ro", "--filesystem=xdg-music:ro",
                             "--filesystem=xdg-videos:ro"}


def test_all_git_sources_pinned():
    pins = manifest_commits()
    assert {"android_translation_layer", "art_standalone", "bionic_translation", "wolfssl"} <= pins.keys()
    assert all(re.fullmatch(r"[0-9a-f]{40}", c) for c in pins.values())


def test_atl_doctor_pinned_with_the_same_atl_source():
    """Agent, trace counter and recorder come from one atl-doctor commit; the stub list is made from the
    very ATL source the Flatpak runs."""
    pins = manifest_commits()
    bi = dict(l.split("=", 1) for l in (ROOT / "data/build-info").read_text().split())
    text = MANIFEST.read_text(encoding="utf-8")
    module = text[text.index("- name: atl-doctor"):text.index("- name: apk-starter")]
    assert bi["atl_doctor_commit"] == pins["atl-doctor"] and pins["android_translation_layer"] in module
    art = text[text.index("- name: art_standalone"):text.index("- name: jni-upcall-deps")]
    assert f"commit: {pins['atl-doctor']}" in art and "libopenjdkjvmti.so" in art   # JVMTI plugin, same atl-doctor


def test_atl_from_the_fix_r5_branch():
    text = (ROOT / "io.github.Jimbambuli.ApkStarter.yml").read_text()
    module = text[text.index("- name: android_translation_layer"):text.index("- name: atl-doctor")]
    # fix-r4 + android.atl.InputTrace (fix-r5) + ATLViewDump reading "request" and saving the window image on request (612123322)
    assert "branch: fix-r5" in module
    assert text.count(f"commit: {manifest_commits()['android_translation_layer']}") == 2      # also atl-src
