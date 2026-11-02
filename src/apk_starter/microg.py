"""microG test build: GmsCore and GsfProxy ship in the Flatpak (share/apk-starter/microg/<package>.apk).

ATL loads Play Services into the app's own process from <ANDROID_APP_DATA_DIR>/_installed_apks_ and gives them the
data folder <ANDROID_APP_DATA_DIR>/<package>.apk_. Every app has its own ANDROID_APP_DATA_DIR here, so each gets
links to the shipped APKs and to ONE shared data folder per microG package: the folder microG uses when it is
started itself (its settings), so a login or registration there is seen by every app."""
from pathlib import Path

from .paths import Paths

GMS = "com.google.android.gms"


def bundle_dir(paths: Paths) -> Path:
    return paths.prefix / "share/apk-starter/microg"


def bundled(paths: Paths) -> list[Path]:
    """The shipped microG APKs; empty in builds without microG."""
    return sorted(bundle_dir(paths).glob("*.apk"))


def _symlink(link: Path, target: Path) -> None:
    if link.is_symlink() and link.readlink() == target:
        return
    if link.is_symlink():
        link.unlink()
    elif link.exists():
        return                  # a real file or folder: leave it alone
    link.symlink_to(target)


def link_into(paths: Paths, pkg: str) -> None:
    """Make microG visible to `pkg` (before ATL starts it)."""
    base = paths.data(pkg)
    for apk in bundled(paths):
        mpkg = apk.name[:-len(".apk")]
        installed = base / "_installed_apks_"
        installed.mkdir(parents=True, exist_ok=True)
        _symlink(installed / apk.name, apk)
        if mpkg != pkg:
            shared = paths.data(mpkg) / f"{apk.name}_"
            shared.mkdir(parents=True, exist_ok=True)
            _symlink(base / f"{apk.name}_", shared)
