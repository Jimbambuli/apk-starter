"""View model of the management window: what the list shows, how it filters and sorts, what keys do and what
the window remembers. No GTK here."""
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from . import dexopt
from .cli import data_size, list_installed
from .paths import Paths

SORT_KEYS = ("name", "started", "installed", "size")
NARROW_WIDTH = 640          # below: only the list, the details as a second page with «Back»
MIN_SIZE = 360


@dataclass(frozen=True)
class AppRow:
    package: str
    label: str
    version: str
    size: int
    icon: Path | None
    target_sdk: int | None
    installed: float | None     # seconds since the epoch
    started: float | None
    prepared: bool              # compiled for this computer (Spec 11)


def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def load_rows(paths: Paths) -> list[AppRow]:
    rows = []
    for d in list_installed(paths):
        pkg = d["package"]
        icon = paths.icon(pkg)
        sdk = d.get("target_sdk")
        rows.append(AppRow(pkg, str(d.get("label") or pkg), str(d.get("version_name") or ""),
                           data_size(paths, pkg), icon if icon.is_file() else None,
                           sdk if isinstance(sdk, int) and not isinstance(sdk, bool) else None,
                           _mtime(paths.apk(pkg)), _mtime(paths.started(pkg)),
                           dexopt.app_filter(paths, str(paths.apk(pkg))) == dexopt.FULL))
    return sort_rows(rows, "name")


def filter_rows(rows: list[AppRow], query: str) -> list[AppRow]:
    q = query.strip().casefold()
    return [r for r in rows if q in r.label.casefold() or q in r.package.casefold()] if q else list(rows)


def sort_rows(rows: list[AppRow], key: str) -> list[AppRow]:
    """Name A–Z; dates newest first and sizes largest first, ties (and «never») by name."""
    by_name = sorted(rows, key=lambda r: (r.label.casefold(), r.package))
    if key == "started":
        return sorted(by_name, key=lambda r: -(r.started or float("-inf")))
    if key == "installed":
        return sorted(by_name, key=lambda r: -(r.installed or float("-inf")))
    if key == "size":
        return sorted(by_name, key=lambda r: -r.size)
    return by_name


@dataclass(frozen=True)
class WindowState:
    width: int = 920
    height: int = 640
    sort: str = "name"
    selected: str | None = None


def load_state(path: Path) -> WindowState:
    """Every value that is missing or wrong falls back to its default."""
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return WindowState()
    if not isinstance(d, dict):
        return WindowState()
    good = {"width": lambda v: type(v) is int and v >= MIN_SIZE, "height": lambda v: type(v) is int and v >= MIN_SIZE,
            "sort": lambda v: v in SORT_KEYS, "selected": lambda v: isinstance(v, str)}
    return WindowState(**{k: d[k] for k, ok in good.items() if k in d and ok(d[k])})


def save_state(path: Path, state: WindowState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(asdict(state)), encoding="utf-8")
    os.replace(tmp, path)


def key_action(key: str, ctrl: bool, in_search: bool) -> str | None:
    """Window keys (GDK key names). Plain typing needs no action: the search captures it."""
    if ctrl and key.lower() == "f":
        return "search"
    if ctrl:
        return None
    if key in ("Return", "KP_Enter", "ISO_Enter"):
        return "to-list" if in_search else "start"
    if key in ("Delete", "KP_Delete") and not in_search:
        return "uninstall"
    if key == "Escape" and not in_search:
        return "back"
    return None


def is_narrow(width: int) -> bool:
    return width < NARROW_WIDTH


def start_argv(launcher: list[str], pkg: str, level: str | None) -> list[str]:
    """Apps run as a separate `apk-starter PACKAGE` process, exactly as from their menu entry; level: None,
    "log" or "debug" (Spec 12)."""
    return launcher + ([f"--{level}", "--", pkg] if level else [pkg])
