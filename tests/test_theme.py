"""Colour scheme, accent and font from the settings portal (Spec 13), without GTK."""
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
