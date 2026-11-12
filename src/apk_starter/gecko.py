"""GeckoView apps (Firefox, Focus, …): no GPU process.

Gecko draws pages in a separate GPU process by default. In ATL that process gets the window of the app only as a
pointer into the main process, so it draws nothing and pages stay empty. With the pref
layers.gpu-process.enabled=false Gecko composites in the main process and pages show. Gecko reads prefs from the
user.js in the profile folder at every start; the profile folder is listed in <files>/mozilla/profiles.ini, which
Gecko creates with a random name at the first start. So before the first start the starter writes that profiles.ini
itself, with a profile folder of its own name, and Gecko takes it. An existing user.js is never overwritten: the line
is added only if the file does not name the pref at all (a user's own setting, also a commented one, stays as it is).
"""
import configparser
import os
from pathlib import Path

PREF = "layers.gpu-process.enabled"
LINE = f'user_pref("{PREF}", false);  // APK Starter: ATL cannot show a GPU process\n'
PROFILE = "apkstarter.default"
INI = f"""[General]
StartWithLastProfile=1
Version=2

[Profile0]
Name=default
IsRelative=1
Path={PROFILE}
Default=1
"""


def _write_new(path: Path, text: str) -> None:
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def profiles(mozilla: Path) -> list[Path]:
    """The profile folders profiles.ini lists ([] without a readable profiles.ini)."""
    ini = configparser.ConfigParser(interpolation=None)
    try:
        ini.read_string((mozilla / "profiles.ini").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, configparser.Error):
        return []
    found = []
    for name in ini.sections():
        if name.startswith("Profile") and ini.has_option(name, "Path"):
            p = Path(ini.get(name, "Path"))
            found.append(mozilla / p if ini.get(name, "IsRelative", fallback="1") == "1" else p)
    return found


def no_gpu_process(app_data: Path) -> list[Path]:
    """Sees that every profile of the GeckoView app with the data folder app_data (<pkg>.apk_) has the pref in its
    user.js; before the first start creates profiles.ini with one profile. Returns the user.js files written."""
    mozilla = app_data / "files" / "mozilla"
    if not (mozilla / "profiles.ini").exists():
        (mozilla / PROFILE).mkdir(parents=True, exist_ok=True)
        _write_new(mozilla / "profiles.ini", INI)
    written = []
    for profile in profiles(mozilla):
        user_js = profile / "user.js"
        try:
            old = user_js.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            old = None
        if old is not None and PREF in old:
            continue
        if not profile.is_dir():
            continue
        with open(user_js, "a", encoding="utf-8") as f:
            f.write(("\n" if old and not old.endswith("\n") else "") + LINE)
        written.append(user_js)
    return written
