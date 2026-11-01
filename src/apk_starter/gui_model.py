"""View model of the management window: what the list shows and what a click runs. No GTK here."""
from dataclasses import dataclass
from pathlib import Path

from .cli import data_size, list_installed
from .paths import Paths


@dataclass(frozen=True)
class AppRow:
    package: str
    label: str
    version: str
    size: int
    icon: Path | None


def load_rows(paths: Paths) -> list[AppRow]:
    rows = []
    for d in list_installed(paths):
        pkg = d["package"]
        icon = paths.icon(pkg)
        rows.append(AppRow(pkg, str(d.get("label") or pkg), str(d.get("version_name") or ""),
                           data_size(paths, pkg), icon if icon.is_file() else None))
    return sorted(rows, key=lambda r: (r.label.casefold(), r.package))


def start_argv(launcher: list[str], pkg: str, log: bool) -> list[str]:
    """Apps run as a separate `apk-starter PACKAGE` process, exactly as from their menu entry."""
    return launcher + (["--log", "--", pkg] if log else [pkg])
