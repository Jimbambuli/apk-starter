"""The look of the desktop (no GTK in this module):

- the GTK configuration of the system (Spec 14, Standardweg A, Marcel 2.11.2026): in the Flatpak, GTK only sees
  the runtime's theme (Adwaita, the base) and the app's own configuration. With read access to the host's GTK 4
  configuration (--filesystem=xdg-config/gtk-4.0:ro) the window takes icons, cursor and font from the host's
  settings.ini and loads the host's gtk.css (KDE: the colours of the colour scheme), as GTK does outside the
  sandbox. The host's theme itself is not visible (no host-os); an icon theme is there when its Flatpak extension
  (org.freedesktop.Platform.Icontheme.*) is installed, else GTK's Adwaita icons stay;
- colour scheme, accent colour and font from the XDG settings portal (Spec 13); the accent is put on the window's
  accented parts only without a system theme (the theme brings the desktop's accent itself).
Plain GTK 4 without libadwaita keeps the compositor's own title bar (Breeze under KWin)."""
import configparser
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

BUILTIN_THEMES = ("Default", "Adwaita", "HighContrast", "HighContrastInverse")

APPEARANCE = "org.freedesktop.appearance"
KDE_GENERAL = "org.kde.kdeglobals.General"
NAMESPACES = (APPEARANCE, KDE_GENERAL)


@dataclass(frozen=True)
class Theme:
    dark: bool | None = None                        # None: no preference, the toolkit's default
    accent: tuple[float, float, float] | None = None
    font: str | None = None                         # Pango font description, e.g. "Noto Sans 10"


def kde_font(value: str) -> str | None:
    """KDE's QFont string "Family,size,…" -> "Family size"."""
    parts = value.split(",")
    if len(parts) < 2 or not parts[0]:
        return None
    try:
        size = float(parts[1])
    except ValueError:
        return None
    return f"{parts[0]} {size:g}" if size > 0 else None


def from_settings(values: Mapping[tuple[str, str], object]) -> Theme:
    scheme = values.get((APPEARANCE, "color-scheme"))
    accent = values.get((APPEARANCE, "accent-color"))
    if not (isinstance(accent, tuple) and len(accent) == 3
            and all(isinstance(c, float) and 0.0 <= c <= 1.0 for c in accent)):
        accent = None
    font = values.get((KDE_GENERAL, "font"))
    return Theme(dark={1: True, 2: False}.get(scheme) if isinstance(scheme, int) else None, accent=accent,
                 font=kde_font(font) if isinstance(font, str) else None)


def _hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{round(c * 255):02x}" for c in rgb)


def _luminance(rgb: tuple[float, float, float]) -> float:
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def accent_css(accent: tuple[float, float, float] | None) -> str:
    """GTK's own theme has a fixed blue; these rules put the desktop's accent on the few accented parts."""
    if accent is None:
        return ""
    bg = _hex(accent)
    lum = _luminance(accent)
    fg = "#000000" if (lum + 0.05) / 0.05 > 1.05 / (lum + 0.05) else "#ffffff"
    return f"""
button.suggested-action, button.suggested-action:hover, button.suggested-action:active {{
  background-color: {bg}; background-image: none; color: {fg}; border-color: {bg}; }}
.navigation-sidebar > row:selected, .navigation-sidebar > row:selected:hover {{ background-color: {bg}; color: {fg}; }}
.navigation-sidebar > row:selected label {{ color: {fg}; }}
checkbutton check:checked {{ background-color: {bg}; border-color: {bg}; color: {fg}; background-image: none; }}
entry:focus-within, button:focus-visible, .navigation-sidebar > row:focus-visible {{ outline-color: alpha({bg}, 0.6); }}
.accent {{ color: {bg}; }}
"""


def window_css(t: Theme, system_theme: bool) -> str:
    danger = "#ff7b63" if t.dark else "#c01c28"           # readable red on either background
    accent = "" if system_theme else accent_css(t.accent)
    return accent + f".danger-text {{ color: {danger}; }}"


@dataclass(frozen=True)
class SystemTheme:
    theme_name: str | None = None
    icon_theme: str | None = None
    cursor_theme: str | None = None
    cursor_size: int | None = None
    font: str | None = None                         # Pango font description, e.g. "Noto Sans,  10"
    gtk_css: Path | None = None                     # the host's user CSS
    found: bool = False                             # GTK 4 can load the theme


def host_config_dir(env: Mapping[str, str]) -> Path:
    """Flatpak passes the host's XDG_CONFIG_HOME as HOST_XDG_CONFIG_HOME if it was set; the default is ~/.config."""
    host = env.get("HOST_XDG_CONFIG_HOME")
    return Path(host) if host else Path(env.get("HOME", "/")) / ".config"


def theme_dirs(env: Mapping[str, str]) -> list[Path]:
    """Where GTK looks for a theme by name (gtkcssprovider.c), the sandbox's own folders first."""
    home = Path(env.get("HOME", "/"))
    data_home = Path(env.get("XDG_DATA_HOME") or home / ".local/share")
    dirs = env.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    return [data_home / "themes", home / ".themes"] + [Path(d) / "themes" for d in dirs.split(":") if d]


def read_system(config_dir: Path, dirs: Sequence[Path]) -> SystemTheme:
    """settings.ini of gtk-4.0 in config_dir; not the dark variant (portal) and not gtk-xft-dpi (for X11, the
    compositor gives the scale)."""
    ini = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        ini.read(config_dir / "gtk-4.0" / "settings.ini", encoding="utf-8")
    except (configparser.Error, UnicodeDecodeError):
        return SystemTheme()
    if not ini.has_section("Settings"):
        return SystemTheme()
    sec = ini["Settings"]
    get = lambda key: (sec.get(key) or "").strip() or None      # noqa: E731
    name = get("gtk-theme-name")
    size = get("gtk-cursor-theme-size")
    found = name is not None and (name in BUILTIN_THEMES or any(
        (d / name / "gtk-4.0" / "gtk.css").is_file() for d in dirs))
    css = config_dir / "gtk-4.0" / "gtk.css"
    return SystemTheme(theme_name=name, icon_theme=get("gtk-icon-theme-name"),
                       cursor_theme=get("gtk-cursor-theme-name"),
                       cursor_size=int(size) if size and size.isdigit() else None, font=get("gtk-font-name"),
                       gtk_css=css if css.is_file() else None, found=found)
