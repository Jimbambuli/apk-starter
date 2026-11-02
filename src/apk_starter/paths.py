"""Folder layout: everything the starter writes lives under XDG_DATA_HOME / XDG_CACHE_HOME."""
import re
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from pathlib import Path

from .apkinfo import MAX_PACKAGE, PACKAGE_RE
from .errors import UsageError
from .i18n import _


PROFILE_RE = re.compile(r"[a-z0-9]{1,16}")


def apply_profile(env: MutableMapping[str, str]) -> None:
    """A test build (its own Flatpak branch, APK_STARTER_PROFILE in its metadata) keeps data and caches beside
    those of stable: <XDG_DATA_HOME>/../<profile>/data and .../cache, also for ATL and ART started from here."""
    profile = env.get("APK_STARTER_PROFILE", "")
    if not PROFILE_RE.fullmatch(profile) or env.get("APK_STARTER_PROFILE_APPLIED") == profile:
        return
    home = Path(env.get("HOME", "/"))
    base = Path(env.get("XDG_DATA_HOME") or home / ".local/share").parent / profile
    env["XDG_DATA_HOME"], env["XDG_CACHE_HOME"] = str(base / "data"), str(base / "cache")
    env["APK_STARTER_PROFILE_APPLIED"] = profile


def _safe(pkg: str) -> str:
    """Every path below is built from a package name, so refuse anything that is not one."""
    if len(pkg) > MAX_PACKAGE or not PACKAGE_RE.fullmatch(pkg):
        raise UsageError(_("invalid package name: {package}").format(package=repr(pkg)))
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

    def icon(self, pkg: str) -> Path:
        return self.apps_dir() / f"{_safe(pkg)}.png"

    def started(self, pkg: str) -> Path:
        """Its modification time is the last start of an installed app."""
        return self.apps_dir() / f"{_safe(pkg)}.started"

    def window_state(self) -> Path:
        return self.data_home / "window.json"

    def settings(self) -> Path:
        return self.data_home / "settings.json"

    def lock(self, pkg: str) -> Path:
        return self.cache_home / "apk-starter" / "locks" / f"{_safe(pkg)}.lock"

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

    def jvmti_plugin(self) -> Path:
        return self.prefix / "lib/art/libopenjdkjvmti.so"

    def agent(self) -> Path:
        return self.prefix / "lib/atl-doctor/libatl_doctor_agent.so"

    def stub_list(self) -> Path:
        return self.prefix / "share/apk-starter/stubs.tsv"

    def default_icon(self) -> Path:
        return self.prefix / "share/icons/hicolor/scalable/apps/io.github.Jimbambuli.ApkStarter.svg"
