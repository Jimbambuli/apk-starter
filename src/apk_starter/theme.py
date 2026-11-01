"""Colour scheme, accent colour and font of the desktop from the XDG settings portal (Spec 13), without
libadwaita: plain GTK 4 keeps the compositor's own title bar (Breeze under KWin). No GTK in this module."""
from collections.abc import Mapping
from dataclasses import dataclass

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
