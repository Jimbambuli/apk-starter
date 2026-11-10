"""Settings of the starter that apply to every start of an app, also from the menu (settings.json): per app, the
title bar of its window, whether it always starts with Debug or Debug full (Spec 14) the permissions switched off and the Android version (SDK level) it runs as. An old «log» value is ignored."""
import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path

# ATL_TITLEBAR: ATL's title bar with the back button (default), the desktop's, the desktop's + navigation bar
TITLEBARS = ("atl", "system", "system-navigation")
SDK_CHOICES = (34, 35, 36)              # the Android versions to pin an app to (anything else is ignored)


@dataclass(frozen=True)
class AppSettings:
    titlebar: str = "atl"
    debug: bool = False                 # «Debug» ticked: every start of the app is recorded (archive cleaned)
    debug_full: bool = False            # «Debug full» ticked: likewise, the archive keeps everything (it wins over debug)
    denied: tuple[str, ...] = ()        # permission groups switched off (permissions.GROUPS)
    sdk: int | None = None              # Android version pinned for the app; None = its targetSdk (launch.sdk_for)


@dataclass(frozen=True)
class Settings:
    apps: dict[str, AppSettings] = field(default_factory=dict)
    default_titlebar: str = "atl"       # from the old global «Title bar from the system» (Testversion 2)
    aurora_updates: bool = False        # the window tells the updates Aurora Store knows (notification)

    def app(self, pkg: str) -> AppSettings:
        return self.apps.get(pkg, AppSettings(titlebar=self.default_titlebar))

    def with_app(self, pkg: str, **changes) -> "Settings":
        return replace(self, apps={**self.apps, pkg: replace(self.app(pkg), **changes)})


def _app(d: object, default_titlebar: str) -> AppSettings:
    if not isinstance(d, dict):
        return AppSettings(titlebar=default_titlebar)
    titlebar = d.get("titlebar") if d.get("titlebar") in TITLEBARS else default_titlebar
    denied = d.get("denied") if isinstance(d.get("denied"), list) else []
    return AppSettings(titlebar=titlebar, debug=d.get("debug") is True,
                       debug_full=d.get("debug_full") is True,
                       denied=tuple(x for x in denied if isinstance(x, str)),
                       sdk=d["sdk"] if type(d.get("sdk")) is int and d["sdk"] in SDK_CHOICES else None)


def load_settings(path: Path) -> Settings:
    """Every value that is missing or wrong falls back to its default."""
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Settings()
    if not isinstance(d, dict):
        return Settings()
    default = "system" if d.get("system_titlebar") is True else "atl"
    apps = d.get("apps") if isinstance(d.get("apps"), dict) else {}
    return Settings(apps={str(k): _app(v, default) for k, v in apps.items()}, default_titlebar=default,
                    aurora_updates=d.get("aurora_updates") is True)


def save_settings(path: Path, settings: Settings) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    d = {"apps": {k: {"titlebar": v.titlebar, "debug": v.debug, **({"debug_full": True} if v.debug_full else {}),
                     **({"denied": list(v.denied)} if v.denied else {}),
                     **({"sdk": v.sdk} if v.sdk is not None else {})}
                  for k, v in settings.apps.items()}}
    if settings.default_titlebar == "system":
        d["system_titlebar"] = True
    if settings.aurora_updates:
        d["aurora_updates"] = True
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(d), encoding="utf-8")
    os.replace(tmp, path)
