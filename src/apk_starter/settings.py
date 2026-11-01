"""Settings of the starter that apply to every start of an app, also from the menu (settings.json)."""
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    system_titlebar: bool = False       # the desktop's title bar instead of ATL's with the back button (Spec 13)


def load_settings(path: Path) -> Settings:
    """Every value that is missing or wrong falls back to its default."""
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Settings()
    if not isinstance(d, dict):
        return Settings()
    return Settings(**{k: d[k] for k in ("system_titlebar",) if type(d.get(k)) is bool})


def save_settings(path: Path, settings: Settings) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(asdict(settings)), encoding="utf-8")
    os.replace(tmp, path)
