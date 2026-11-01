"""Settings of the starter that also apply to starts from the menu (settings.json)."""
from apk_starter.settings import Settings, load_settings, save_settings


def test_default_roundtrip_and_garbage(tmp_path):
    path = tmp_path / "d" / "settings.json"
    assert load_settings(path) == Settings(system_titlebar=False)
    save_settings(path, Settings(system_titlebar=True))
    assert load_settings(path) == Settings(system_titlebar=True)
    path.write_text('{"system_titlebar": "yes"}')
    assert load_settings(path) == Settings()
    path.write_text("[1]")
    assert load_settings(path) == Settings()
