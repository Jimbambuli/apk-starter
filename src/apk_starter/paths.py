"""Folder layout: everything the starter writes lives under XDG_DATA_HOME / XDG_CACHE_HOME."""
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .apkinfo import MAX_PACKAGE, PACKAGE_RE
from .errors import UsageError


def _safe(pkg: str) -> str:
    """Every path below is built from a package name, so refuse anything that is not one."""
    if len(pkg) > MAX_PACKAGE or not PACKAGE_RE.fullmatch(pkg):
        raise UsageError(f"invalid package name: {pkg!r}")
    return pkg


@dataclass(frozen=True)
class Paths:
    data_home: Path
    cache_home: Path
    prefix: Path

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Paths":
        home = Path(env.get("HOME", "/"))
        return cls(
            data_home=Path(env.get("XDG_DATA_HOME") or home / ".local/share"),
            cache_home=Path(env.get("XDG_CACHE_HOME") or home / ".cache"),
            prefix=Path(env.get("APK_STARTER_PREFIX") or "/app"),
        )

    def apps_dir(self) -> Path:
        return self.data_home / "apps"

    def apk(self, pkg: str) -> Path:
        return self.apps_dir() / f"{_safe(pkg)}.apk"

    def meta(self, pkg: str) -> Path:
        return self.apps_dir() / f"{_safe(pkg)}.json"

    def data(self, pkg: str) -> Path:
        return self.data_home / "data" / _safe(pkg)

    def logs_dir(self) -> Path:
        return self.data_home / "logs"

    def launch_dir(self) -> Path:
        return self.cache_home / "apk-starter" / "launch"

    def build_info(self) -> Path:
        return self.prefix / "share/apk-starter/build-info"

    def cacerts(self) -> Path:
        return self.prefix / "share/apk-starter/cacerts"

    def default_icon(self) -> Path:
        return self.prefix / "share/icons/hicolor/scalable/apps/io.github.Jimbambuli.ApkStarter.svg"
