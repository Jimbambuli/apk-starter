"""tools/check-native-pins.py: the native build's wraps and jars equal the Flatpak manifest."""
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / "tools/check-native-pins.py"


def run(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, TOOL, repo], capture_output=True, text=True)


def test_native_pins_equal_the_manifest():
    r = run(REPO)
    assert r.returncode == 0, r.stderr


def test_a_changed_manifest_commit_is_reported(tmp_path):
    (tmp_path / "native/subprojects").mkdir(parents=True)
    for w in (REPO / "native/subprojects").glob("*.wrap"):
        shutil.copy(w, tmp_path / "native/subprojects" / w.name)
    shutil.copy(REPO / "native/tool-jars.txt", tmp_path / "native/tool-jars.txt")
    text = (REPO / "io.github.Jimbambuli.ApkStarter.yml").read_text(encoding="utf-8")
    old = "commit: 0f37cc5f32d3e82aa01fe1fbb643e9178efafd55"
    assert old in text
    (tmp_path / "io.github.Jimbambuli.ApkStarter.yml").write_text(text.replace(old, "commit: " + "1" * 40),
                                                                  encoding="utf-8")
    r = run(tmp_path)
    assert r.returncode == 1
    assert "manifest source without a wrap: https://gitlab.com/Jimbambuli/android_translation_layer.git 1111" in r.stderr
    assert "wrap not in the manifest: https://gitlab.com/Jimbambuli/android_translation_layer.git 0f37cc5f" in r.stderr
