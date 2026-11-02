"""Colour scheme, accent and font from the settings portal (Spec 13), without GTK."""
from pathlib import Path

from apk_starter.theme import Theme, accent_css, from_settings, kde_font


def test_scheme_accent_and_font_from_portal_values():
    t = from_settings({("org.freedesktop.appearance", "color-scheme"): 1,
                       ("org.freedesktop.appearance", "accent-color"): (0.24, 0.68, 0.91),
                       ("org.kde.kdeglobals.General", "font"): "Noto Sans,10,-1,5,400,0,0,0,0,0,0,0,0,0,0,1"})
    assert t == Theme(dark=True, accent=(0.24, 0.68, 0.91), font="Noto Sans 10")
    assert from_settings({("org.freedesktop.appearance", "color-scheme"): 2}).dark is False
    assert from_settings({("org.freedesktop.appearance", "color-scheme"): 0}) == Theme()   # no preference


def test_out_of_range_accent_means_none():
    assert from_settings({("org.freedesktop.appearance", "accent-color"): (1.5, 0.0, 0.0)}).accent is None
    assert from_settings({("org.freedesktop.appearance", "accent-color"): (0.1, 0.2)}).accent is None


def test_kde_font():
    assert kde_font("Inter,11,-1,5,50,0,0,0,0,0") == "Inter 11"
    assert kde_font("Noto Sans,10.5,-1,5,400") == "Noto Sans 10.5"
    assert kde_font("") is None and kde_font("Inter") is None and kde_font("Inter,big") is None


def test_accent_css_uses_the_colour_and_a_readable_text_colour():
    css = accent_css((0.24, 0.68, 0.91))
    assert "#3dade8" in css and "suggested-action" in css and ":selected" in css
    assert "color: #000000" in css                  # light blue: dark text
    assert "color: #ffffff" in accent_css((0.1, 0.1, 0.4))
    assert accent_css(None) == ""


# --- GTK theme of the system (Spec 14) ---
from apk_starter.theme import (SystemTheme, host_config_dir, icon_theme_available, icon_theme_dirs, read_system,  # noqa: E402
                               theme_dirs, window_css)


def _theme(root, name):
    d = root / name / "gtk-4.0"
    d.mkdir(parents=True)
    (d / "gtk.css").write_text("")


def test_system_settings_from_gtk4_only(tmp_path):
    cfg, themes = tmp_path / "cfg", tmp_path / "themes"
    _theme(themes, "Breeze")
    (cfg / "gtk-3.0").mkdir(parents=True)
    (cfg / "gtk-3.0" / "settings.ini").write_text("[Settings]\ngtk-theme-name=Breeze\n")
    assert read_system(cfg, [themes]) == SystemTheme()          # GTK 3's settings are not read (no access)
    (cfg / "gtk-4.0").mkdir()
    (cfg / "gtk-4.0" / "settings.ini").write_text(
        "[Settings]\ngtk-theme-name=Breeze\ngtk-icon-theme-name=breeze\ngtk-cursor-theme-name=breeze_cursors\n"
        "gtk-cursor-theme-size=36\ngtk-font-name=Noto Sans,  10\ngtk-xft-dpi=196608\n"
        "gtk-application-prefer-dark-theme=true\n")
    (cfg / "gtk-4.0" / "gtk.css").write_text("@import 'colors.css';\n")
    assert read_system(cfg, [themes]) == SystemTheme(
        theme_name="Breeze", icon_theme="breeze", cursor_theme="breeze_cursors", cursor_size=36,
        font="Noto Sans,  10", gtk_css=cfg / "gtk-4.0" / "gtk.css", found=True)


def test_missing_or_unknown_theme_is_not_found(tmp_path):
    assert read_system(tmp_path / "nothing", [tmp_path]) == SystemTheme()
    cfg = tmp_path / "cfg" / "gtk-4.0"
    cfg.mkdir(parents=True)
    (cfg / "settings.ini").write_text("[Settings]\ngtk-theme-name=Arc\ngtk-cursor-theme-size=big\n")
    s = read_system(tmp_path / "cfg", [tmp_path / "themes"])
    assert s.theme_name == "Arc" and not s.found and s.cursor_size is None
    (tmp_path / "themes" / "Arc" / "gtk-3.0").mkdir(parents=True)          # GTK 3 only: no use for GTK 4
    assert not read_system(tmp_path / "cfg", [tmp_path / "themes"]).found
    (cfg / "settings.ini").write_text("[Settings]\ngtk-theme-name=Default\n")      # built into GTK
    assert read_system(tmp_path / "cfg", []).found
    (cfg / "settings.ini").write_text("no ini at all")
    assert read_system(tmp_path / "cfg", []) == SystemTheme()


def test_config_dir_and_theme_dirs():
    assert host_config_dir({"HOME": "/home/m"}) == Path("/home/m/.config")
    assert host_config_dir({"HOME": "/home/m", "HOST_XDG_CONFIG_HOME": "/cfg"}) == Path("/cfg")
    assert theme_dirs({"HOME": "/home/m", "XDG_DATA_DIRS": "/app/share:/usr/share"}) == [
        Path("/home/m/.local/share/themes"), Path("/home/m/.themes"), Path("/app/share/themes"),
        Path("/usr/share/themes")]


def test_icon_theme_only_when_installed(tmp_path):
    dirs = icon_theme_dirs({"HOME": str(tmp_path), "XDG_DATA_DIRS": f"{tmp_path}/runtime/share:/usr/share"})
    assert dirs[0] == tmp_path / ".local/share/icons" and dirs[2] == tmp_path / "runtime/share/icons"
    assert not icon_theme_available("breeze", dirs) and not icon_theme_available(None, dirs)
    (tmp_path / "runtime/share/icons/breeze").mkdir(parents=True)
    assert not icon_theme_available("breeze", dirs)                      # a folder without index.theme
    (tmp_path / "runtime/share/icons/breeze/index.theme").write_text("[Icon Theme]\n")
    assert icon_theme_available("breeze", dirs)


def test_own_accent_only_without_a_system_theme():
    t = Theme(dark=False, accent=(0.24, 0.68, 0.91))
    assert "#3dade8" in window_css(t, system_theme=False) and ".danger-text" in window_css(t, system_theme=False)
    assert "#3dade8" not in window_css(t, system_theme=True) and ".danger-text" in window_css(t, system_theme=True)
