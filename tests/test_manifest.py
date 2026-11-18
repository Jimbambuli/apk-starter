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
                             # saving goes through the file chooser portal); Downloads writable for
                             # MediaStore.Downloads
                             "--filesystem=xdg-download", "--filesystem=xdg-pictures:ro",
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


def test_atl_from_the_fix_r20_branch():
    text = (ROOT / "io.github.Jimbambuli.ApkStarter.yml").read_text()
    module = text[text.index("- name: android_translation_layer"):text.index("- name: atl-doctor")]
    # fix-r6 + merges reste, kindstart, compose-klein, de-texte, webview, popups, surface-puffer, activity-prozess,
    # fehlersuche (fix-r7 4e932390c); fix-r8 4104a23b4: + lebenszyklus, apps-rest, jellyfin, kleinkram, firefox; fix-r9 4034931c9: + firefox2, gmail; 8e73b691d: + argv[0] = process name, MediaCodec, Build.SOC_MODEL; c15bc7b08: + MediaCodec input offset/size, remote SurfaceTexture; c0903f9fd: + fdroid (BitmapFactory InputStream exception); 56b5b3739: + whatsapp2, ffvideo, receivers per Context; 36ebcf64f: + scan-a..f, onReceive gets the registering Context, compile fixes; fix-r10 f70088a27: + scan2-a..f, wa3; fix-r11 ad107ee3c: + hierarchie, nativ, voll-0..9; fix-r12 3d01a9eef: + Oeffi fix, all new classes in meson.build, wa6 d5a7cc8e7; fix-r13 017f2677b: + wa6 260888d23 (AOSP style constructors, TabHost); fix-r14 92952cffa: + wa7 (saveLayer/Porter-Duff, inverse fill), wa8 (TextShaper, PdfRenderer), var-par (overloads, Parcelable); fix-r15 09c723673: + EditText cursor move reported after the TextWatchers; fix-r16 e21f2988a: + fix-optik, fix-tasks, fr2-p1..p7, p9, p10, p12; fix-r17 f477ea48a: + fr3-q1..q8, q4-window, q6b, fr4-r1, r3; fix-r18 d8d33799c: + fr4-r2, r4, r5b (no hinting), r6, fr5-s1..s3 (getCookie(null), shouldOverrideUrlLoading only for page navigations, getTitle() never null), ViewGroup drawChild reverted; fix-r19 fd43c47e5: + fr6-t1..t8 (Flutter eglGetProcAddress, permission rationale, compound drawables, Bcp47 locale script, GradientColor, AAudio, service name, layout flags); fix-r20 d3334f600: + fr7 (Bitmap.getPixel, shadow layer, RGB_565, BitmapShader round rect, DrawableContainer callback, AudioTrack non-blocking, activity title, ReplacementSpan.draw)
    assert "branch: fix-r20" in module
    assert text.count(f"commit: {manifest_commits()['android_translation_layer']}") == 2      # also atl-src
