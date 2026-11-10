"""Build the android-translation-layer command line, environment and per-app data."""
import dataclasses
import hashlib
import json
import os
import re
from collections.abc import Callable, Mapping
from pathlib import Path

from . import microg, permissions
from .apkinfo import ApkInfo
from .errors import UsageError
from .i18n import _
from .paths import Paths
from .settings import load_settings

ATL_BIN = "android-translation-layer"
ATL_MAX_SDK = 36
SDK_FLOOR = 21
DEFAULT_SIZE = (1200, 850)
SIZE_MIN, SIZE_MAX = 200, 10000
_SIZE_RE = re.compile(r"([0-9]+)x([0-9]+)")


def parse_size(s: str) -> tuple[int, int]:
    m = _SIZE_RE.fullmatch(s)
    if m:
        w, h = int(m.group(1)), int(m.group(2))
        if SIZE_MIN <= w <= SIZE_MAX and SIZE_MIN <= h <= SIZE_MAX:
            return w, h
    raise UsageError(_("invalid --size, expected WxH: {value}").format(value=s))


def sdk_for(info: ApkInfo, override: int | None) -> int:
    """The override, else the level the app was built for: targetSdk up to ATL_MAX_SDK, not below its minSdk."""
    if override is not None:
        return override
    if info.target_sdk is None:
        return ATL_MAX_SDK
    return max(min(info.target_sdk, ATL_MAX_SDK), info.min_sdk or 0, SDK_FLOOR)


def _size_file(paths: Paths, pkg: str) -> Path:
    return paths.data(pkg) / "apk-starter.json"


def remembered_size(paths: Paths, pkg: str) -> tuple[int, int] | None:
    try:
        size = json.loads(_size_file(paths, pkg).read_text(encoding="utf-8"))["size"]
        w, h = size
        if type(w) is int and type(h) is int and SIZE_MIN <= w <= SIZE_MAX and SIZE_MIN <= h <= SIZE_MAX:
            return w, h
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def remember_size(paths: Paths, pkg: str, size: tuple[int, int]) -> None:
    f = _size_file(paths, pkg)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f"{f.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"size": list(size)}), encoding="utf-8")
    os.replace(tmp, f)


def prepare_apk(paths: Paths, info: ApkInfo) -> Path:
    """ATL names the data folder after the APK file name, so always start through <pkg>.apk."""
    own = paths.apk(info.package)
    if info.path.resolve() == own.resolve():
        return own
    link = paths.launch_dir() / own.name
    link.parent.mkdir(parents=True, exist_ok=True)
    tmp = link.with_name(f"{link.name}.{os.getpid()}.tmp")
    tmp.unlink(missing_ok=True)
    os.symlink(info.path.resolve(), tmp)
    os.replace(tmp, link)
    return link


def build_command(paths: Paths, apk_path: Path, *, size: tuple[int, int], sdk: int | None,
                  atl_args: list[str], app_id: str, splits: list[Path] = ()) -> list[str]:
    # the configuration parts of a split APK after the base: ATL loads them like Android's splitSourceDirs
    cmd = [ATL_BIN, str(apk_path), *map(str, splits), "-w", str(size[0]), "-h", str(size[1])]
    if sdk is not None:
        cmd += ["--sdk-int", str(sdk)]
    # Window app ID = menu entry ID: KWin only accepts IDs prefixed with the Flatpak ID.
    cmd += ["--gapplication-app-id", app_id]
    cmd += ["-X", f"-Djavax.net.ssl.trustStore={paths.cacerts()}"]
    return cmd + atl_args


def build_env(paths: Paths, pkg: str, base: Mapping[str, str]) -> dict[str, str]:
    env = dict(base)
    data = paths.data(pkg)
    data.mkdir(parents=True, exist_ok=True)
    env["ANDROID_APP_DATA_DIR"] = str(data)
    microg.link_into(paths, pkg)                    # microG: Play Services for every app
    env["ATL_UGLY_ENABLE_WEBVIEW"] = "1"
    env.pop("ATL_SYSTEM_TITLEBAR", None)
    app = load_settings(paths.settings()).app(pkg)
    env["ATL_TITLEBAR"] = app.titlebar                 # chosen in the window, per app
    env.pop("ATL_DENIED_PERMISSIONS", None)
    if app.denied:                                  # switched off in the window: ATL answers «denied»
        env["ATL_DENIED_PERMISSIONS"] = ",".join(permissions.denied_permissions(app.denied))
    # the tooltips of ATL's navigation bar ◁ ○ □ in the desktop's language (ATL has no translations)
    env["ATL_NAVIGATION_TOOLTIPS"] = "\n".join((_("Back"), _("Start page of the app"), _("Open pages of the app")))
    # app stores (Aurora Store): ATL finds the installed apps here and hands installs back to the starter
    env["ATL_INSTALLED_APKS_DIR"] = str(paths.apps_dir())
    env["ATL_INSTALL_COMMAND"] = f"{paths.program()} --install-request"
    # ATL sets LC_ALL=C.UTF-8 for the apps; the starter it calls back speaks the desktop's language (i18n)
    if locale := next((base[v] for v in ("LC_ALL", "LC_MESSAGES", "LANG") if base.get(v)), None):
        env["APK_STARTER_LOCALE"] = locale
    env["ATL_LAUNCH_COMMAND"] = str(paths.program())   # "Open" in the store: the starter starts the package
    env["ATL_UNINSTALL_COMMAND"] = f"{paths.program()} --uninstall-request"     # ACTION_DELETE (package:)
    env["ATL_APP_DETAILS_COMMAND"] = f"{paths.program()} --details"   # ACTION_APPLICATION_DETAILS_SETTINGS
    env.pop("ATL_APP_ICON", None)
    if paths.icon(pkg).is_file():
        env["ATL_APP_ICON"] = str(paths.icon(pkg))     # for ATL's title bar; ATL cannot draw adaptive icons
    art = f"{paths.prefix}/lib/art"
    old = base.get("LD_LIBRARY_PATH", "")
    env["LD_LIBRARY_PATH"] = f"{art}:{old}" if old else art
    return env


CACHE_FORMAT = 2                    # 2: with the permissions; older entries are read again


def cached_apk_info(paths: Paths, apk: Path, lang: str | None,
                    read: Callable[[Path, str | None], ApkInfo]) -> ApkInfo:
    """Like Android's package cache: parse an APK once, keyed by path, size, mtime and language (F182).
    The icon, the link filters and share types are not kept: starting does not need them."""
    path = os.path.abspath(apk)
    st = os.stat(path)
    key = [path, st.st_size, st.st_mtime_ns, lang, CACHE_FORMAT]
    f = paths.cache_home / "apk-starter" / "apkinfo" / (hashlib.sha256(path.encode()).hexdigest()[:32] + ".json")
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        if d.pop("key") == key:
            d["permissions"] = tuple(d.get("permissions", ()))
            return ApkInfo(path=Path(apk), icon=None, **d)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    info = read(apk, lang)
    d = {k: v for k, v in dataclasses.asdict(info).items() if k not in ("path", "icon", "link_filters", "share_types")}
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_name(f"{f.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"key": key, **d}), encoding="utf-8")
        os.replace(tmp, f)
    except OSError:
        pass
    return info
