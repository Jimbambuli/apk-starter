"""microG: GmsCore and GsfProxy ship in the Flatpak (share/apk-starter/microg/<package>.apk).

ATL loads Play Services into the app's own process from its installed APKs, which the starter points to apps/
(ATL_INSTALLED_APKS_DIR, as for app stores), and gives them the data folder <ANDROID_APP_DATA_DIR>/<package>.apk_.
So apps/ gets links to the shipped APKs (as on Android, Play Services count as installed), and every app, which has
its own ANDROID_APP_DATA_DIR here, links to ONE shared data folder per microG package: the folder microG uses when
it is started itself (its settings), so a login or registration there is seen by every app."""
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
        paths.apps_dir().mkdir(parents=True, exist_ok=True)
        _symlink(paths.apk(mpkg), apk)          # «microG settings» installed from the bundle: the same file
        if mpkg != pkg:
            shared = paths.data(mpkg) / f"{apk.name}_"
            shared.mkdir(parents=True, exist_ok=True)
            _symlink(base / f"{apk.name}_", shared)
