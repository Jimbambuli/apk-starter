"""Settings of the starter that also apply to starts from the menu (settings.json): per app, the title bar of
its window and whether every start is recorded with Debug."""
from apk_starter.settings import AppSettings, Settings, load_settings, save_settings


def test_default_roundtrip_and_garbage(tmp_path):
    path = tmp_path / "d" / "settings.json"
    assert load_settings(path) == Settings()
    assert load_settings(path).app("org.a") == AppSettings(titlebar="atl", debug=False)
    s = Settings().with_app("org.a", titlebar="system-navigation").with_app("org.b", debug=True)
    save_settings(path, s)
    assert load_settings(path) == s
    assert load_settings(path).app("org.a") == AppSettings("system-navigation", False)
    assert load_settings(path).app("org.b") == AppSettings("atl", True)
    path.write_text('{"apps": {"org.a": {"titlebar": "nonsense", "debug": "yes"}, "org.b": 3}}')
    assert load_settings(path).app("org.a") == AppSettings() and load_settings(path).app("org.b") == AppSettings()
    path.write_text("[1]")
    assert load_settings(path) == Settings()


def test_old_log_value_is_ignored_and_dropped_at_the_next_save(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"apps": {"org.a": {"titlebar": "system", "log": true}}}')
    s = load_settings(path)
    assert s.app("org.a") == AppSettings("system", False)
    save_settings(path, s)
    assert "log" not in path.read_text()


def test_old_global_system_titlebar_becomes_the_default_of_every_app(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"system_titlebar": true, "apps": {"org.b": {"titlebar": "atl"}}}')
    s = load_settings(path)
    assert s.app("org.a").titlebar == "system" and s.app("org.b").titlebar == "atl"


def test_aurora_updates_is_off_by_default_and_remembered(tmp_path):
    from dataclasses import replace
    from apk_starter.settings import Settings, load_settings, save_settings
    f = tmp_path / "settings.json"
    assert load_settings(f).aurora_updates is False
    save_settings(f, replace(Settings(), aurora_updates=True))
    assert load_settings(f).aurora_updates is True
