"""Translations: catalogs compile, placeholders match, and the program speaks the chosen language."""
import gettext
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from apk_starter import i18n
from apk_starter.cli import main

ROOT = Path(__file__).resolve().parent.parent
PO = ROOT / "po"
LANGS = PO.joinpath("LINGUAS").read_text().split()
APP = "io.github.Jimbambuli.ApkStarter"
needs_msgfmt = pytest.mark.skipif(not shutil.which("msgfmt"), reason="no msgfmt (gettext)")


def entries(po: Path) -> list[tuple[str, str]]:
    """(msgid, msgstr) pairs of a .po/.pot file without the header."""
    text = po.read_text(encoding="utf-8")
    out = []
    for m in re.finditer(r'^msgid ((?:".*"\n)+)msgstr ((?:".*"\n?)+)', text, re.M):
        msgid, msgstr = ("".join(re.findall(r'"(.*)"', g)) for g in m.groups())
        if msgid:
            out.append((msgid, msgstr))
    return out


def placeholders(s: str) -> list[str]:
    return sorted(re.findall(r"\{\w+\}|%\(\w+\)[sr]|%[sr]|\\n$", s))


def test_exactly_the_twelve_languages():
    assert LANGS == ["de", "fr", "it", "es", "pt_BR", "nl", "pl", "cs", "uk", "ru", "ja", "zh_CN"]
    assert sorted(p.stem for p in PO.glob("*.po")) == sorted(LANGS)


@pytest.mark.parametrize("lang", LANGS)
def test_complete_and_placeholders_match(lang):
    template = [msgid for msgid, _ in entries(PO / "apk-starter.pot")]
    pairs = entries(PO / f"{lang}.po")
    assert [msgid for msgid, _ in pairs] == template
    for msgid, msgstr in pairs:
        assert msgstr, f"{lang}: untranslated {msgid!r}"
        assert placeholders(msgstr) == placeholders(msgid), f"{lang}: {msgid!r} -> {msgstr!r}"
    if lang == "de":
        assert not any("ß" in s for _, s in pairs)              # Swiss spelling


@needs_msgfmt
@pytest.mark.parametrize("lang", LANGS)
def test_po_compiles_with_check(lang, tmp_path):
    subprocess.run(["msgfmt", "--check", "-o", str(tmp_path / "x.mo"), str(PO / f"{lang}.po")], check=True)


@pytest.mark.skipif(not shutil.which("xgettext"), reason="no xgettext")
def test_template_is_up_to_date(tmp_path):
    files = [l for l in (PO / "POTFILES").read_text().splitlines() if l and not l.startswith("#")]
    subprocess.run(["xgettext", "--from-code=UTF-8", "--keyword=_", "--keyword=N_", "-o", str(tmp_path / "t.pot"),
                    *files], cwd=ROOT, check=True, capture_output=True)
    assert [m for m, _ in entries(tmp_path / "t.pot")] == [m for m, _ in entries(PO / "apk-starter.pot")]


@pytest.fixture
def prefix(tmp_path, monkeypatch):
    """A prefix with compiled catalogs, as the Flatpak build installs them."""
    if not shutil.which("msgfmt"):
        pytest.skip("no msgfmt (gettext)")
    for lang in ("de", "ja"):
        d = tmp_path / "prefix/share/locale" / lang / "LC_MESSAGES"
        d.mkdir(parents=True)
        subprocess.run(["msgfmt", "-o", str(d / "apk-starter.mo"), str(PO / f"{lang}.po")], check=True)
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(var, raising=False)
    yield tmp_path / "prefix"
    gettext.bindtextdomain(i18n.DOMAIN, str(tmp_path / "none"))     # later tests see English again


def _env(tmp_path, prefix):
    return {"XDG_DATA_HOME": str(tmp_path / "d"), "XDG_CACHE_HOME": str(tmp_path / "c"),
            "APK_STARTER_PREFIX": str(prefix)}


def test_help_and_errors_in_german(tmp_path, prefix, monkeypatch, capsys):
    monkeypatch.setenv("LANGUAGE", "de")
    assert main(["--help"], env=_env(tmp_path, prefix)) == 0
    out = capsys.readouterr().out
    assert out.startswith("Aufruf: apk-starter") and "Verwaltungsfenster öffnen" in out
    assert "installierte Apps auflisten" in out and "diese Hilfe anzeigen und beenden" in out
    assert main(["--remove", "org.example.none"], env=_env(tmp_path, prefix)) == 2
    assert capsys.readouterr().err == "apk-starter: nicht installiert: org.example.none\n"
    assert main(["--purge"], env=_env(tmp_path, prefix)) == 2
    assert "apk-starter: Fehler: --purge geht nur mit --remove oder --remove-all" in capsys.readouterr().err


def test_navigation_bar_tooltips_in_german(tmp_path, prefix, monkeypatch):
    from apk_starter.launch import build_env
    from apk_starter.paths import Paths
    monkeypatch.setenv("LANGUAGE", "de")
    i18n.setup(prefix)
    env = build_env(Paths(tmp_path / "d", tmp_path / "c", prefix), "org.example.demo", {})
    assert env["ATL_NAVIGATION_TOOLTIPS"] == "Zurück\nStartseite der App\nOffene Seiten der App"


def test_help_in_japanese_and_english_fallback(tmp_path, prefix, monkeypatch, capsys):
    monkeypatch.setenv("LANGUAGE", "ja")
    assert main(["--help"], env=_env(tmp_path, prefix)) == 0
    assert capsys.readouterr().out.startswith("使い方: apk-starter")
    monkeypatch.setenv("LANGUAGE", "eo")                             # no catalog: English
    assert main(["--help"], env=_env(tmp_path, prefix)) == 0
    assert capsys.readouterr().out.startswith("usage: apk-starter")


@needs_msgfmt
def test_desktop_and_metainfo_get_all_languages(tmp_path):
    desktop, metainfo = tmp_path / f"{APP}.desktop", tmp_path / f"{APP}.metainfo.xml"
    subprocess.run(["msgfmt", "--desktop", f"--template={ROOT}/data/{APP}.desktop.in", "-d", str(PO),
                    "-o", str(desktop)], check=True)
    subprocess.run(["msgfmt", "--xml", f"--template={ROOT}/data/{APP}.metainfo.xml.in", "-d", str(PO),
                    "-o", str(metainfo)], check=True, capture_output=True)
    d, m = desktop.read_text(), metainfo.read_text()
    assert "Comment[de]=Android-Apps auf dem Linux-Desktop ausführen" in d and "Keywords[ja]=" in d
    bcp47 = {"pt_BR": "pt-BR", "zh_CN": "zh-Hans-CN"}                 # how msgfmt --xml writes them
    for lang in LANGS:
        assert f"Comment[{lang}]=" in d and f'<summary xml:lang="{bcp47.get(lang, lang)}">' in m
    if shutil.which("desktop-file-validate"):
        subprocess.run(["desktop-file-validate", str(desktop)], check=True)
    if shutil.which("appstreamcli"):
        subprocess.run(["appstreamcli", "validate", "--no-net", str(metainfo)], check=True, capture_output=True)


def test_install_request_from_atl_speaks_the_desktop_language(tmp_path, prefix, monkeypatch, capsys):
    """ATL sets LC_ALL=C.UTF-8 for the apps; an install from Aurora comes from there."""
    from apk_starter import install_request
    from apk_starter.launch import build_env
    from apk_starter.paths import Paths
    from apkgen import axml_manifest, make_apk
    monkeypatch.delenv("LANGUAGE", raising=False)
    desktop = {**_env(tmp_path, prefix), "LANG": "de_CH.UTF-8"}
    app_env = build_env(Paths.from_env(desktop), "com.aurora.store", desktop)
    for k, v in {**app_env, "LC_ALL": "C.UTF-8", "ATL_INSTALLER_LABEL": "Aurora Store"}.items():
        monkeypatch.setenv(k, v)                    # what ATL hands to the install command
    asked = []
    monkeypatch.setattr(install_request, "ask", lambda info, installer, update, env: asked.append(
        install_request.question(info, installer, update)) or False)
    apk = make_apk(tmp_path / "w.apk", manifest=axml_manifest(version_name="2.26.42.74"))
    assert main(["--install-request", str(apk)]) == 3
    assert asked == [("Demo installieren?", "Von Aurora Store · Version 2.26.42.74", "Abbrechen", "Installieren")]
    assert capsys.readouterr().err.strip().endswith("abgebrochen")
