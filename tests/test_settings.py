"""Settings of the starter that also apply to starts from the menu (settings.json): per app, the title bar of
its window and whether every start writes a log."""
from apk_starter.settings import AppSettings, Settings, load_settings, save_settings


def test_default_roundtrip_and_garbage(tmp_path):
    path = tmp_path / "d" / "settings.json"
    assert load_settings(path) == Settings()
    assert load_settings(path).app("org.a") == AppSettings(titlebar="atl", log=False)
    s = Settings().with_app("org.a", titlebar="system-navigation").with_app("org.b", log=True)
    save_settings(path, s)
    assert load_settings(path) == s
    assert load_settings(path).app("org.a") == AppSettings("system-navigation", False)
    assert load_settings(path).app("org.b") == AppSettings("atl", True)
    path.write_text('{"apps": {"org.a": {"titlebar": "nonsense", "log": "yes"}, "org.b": 3}}')
    assert load_settings(path).app("org.a") == AppSettings() and load_settings(path).app("org.b") == AppSettings()
    path.write_text("[1]")
    assert load_settings(path) == Settings()


def test_old_global_system_titlebar_becomes_the_default_of_every_app(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"system_titlebar": true, "apps": {"org.b": {"titlebar": "atl"}}}')
    s = load_settings(path)
    assert s.app("org.a").titlebar == "system" and s.app("org.b").titlebar == "atl"
