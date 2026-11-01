"""Settings of the starter that apply to every start of an app, also from the menu (settings.json): per app, the
title bar of its window and whether every start writes a log (Spec 14)."""
import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path

# ATL_TITLEBAR: ATL's title bar with the back button (default), the desktop's, the desktop's + navigation bar
TITLEBARS = ("atl", "system", "system-navigation")


@dataclass(frozen=True)
class AppSettings:
    titlebar: str = "atl"
    log: bool = False                   # «Log» ticked: every start of the app writes a log


@dataclass(frozen=True)
class Settings:
    apps: dict[str, AppSettings] = field(default_factory=dict)
    default_titlebar: str = "atl"       # from the old global «Title bar from the system» (Testversion 2)

    def app(self, pkg: str) -> AppSettings:
        return self.apps.get(pkg, AppSettings(titlebar=self.default_titlebar))

    def with_app(self, pkg: str, **changes) -> "Settings":
        return replace(self, apps={**self.apps, pkg: replace(self.app(pkg), **changes)})


def _app(d: object, default_titlebar: str) -> AppSettings:
    if not isinstance(d, dict):
        return AppSettings(titlebar=default_titlebar)
    titlebar = d.get("titlebar") if d.get("titlebar") in TITLEBARS else default_titlebar
    return AppSettings(titlebar=titlebar, log=d.get("log") is True)


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
    return Settings(apps={str(k): _app(v, default) for k, v in apps.items()}, default_titlebar=default)


def save_settings(path: Path, settings: Settings) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    d = {"apps": {k: {"titlebar": v.titlebar, "log": v.log} for k, v in settings.apps.items()}}
    if settings.default_titlebar == "system":
        d["system_titlebar"] = True
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(d), encoding="utf-8")
    os.replace(tmp, path)
