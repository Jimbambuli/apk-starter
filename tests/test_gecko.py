"""GeckoView apps: the GPU process is switched off in the profile's user.js, never overwriting the user's file."""
from apk_starter import gecko

PREF_LINE = 'user_pref("layers.gpu-process.enabled", false);'


def test_first_start_gets_a_profile_of_its_own(tmp_path):
    written = gecko.no_gpu_process(tmp_path)
    moz = tmp_path / "files" / "mozilla"
    assert gecko.profiles(moz) == [moz / "apkstarter.default"]
    assert written == [moz / "apkstarter.default" / "user.js"]
    assert PREF_LINE in written[0].read_text()
    assert gecko.no_gpu_process(tmp_path) == []                      # once only
    assert written[0].read_text().count("layers.gpu-process") == 1


def test_gecko_s_own_profile_keeps_its_user_js(tmp_path):
    moz = tmp_path / "files" / "mozilla"
    (moz / "ay6bd8u6.default").mkdir(parents=True)
    (moz / "profiles.ini").write_text("[General]\nStartWithLastProfile=1\n\n[Profile0]\nName=default\nIsRelative=1\n"
                                      "Path=ay6bd8u6.default\nDefault=1\n")
    user_js = moz / "ay6bd8u6.default" / "user.js"
    user_js.write_text('user_pref("browser.startup.page", 3);')     # no newline at the end
    gecko.no_gpu_process(tmp_path)
    assert user_js.read_text() == 'user_pref("browser.startup.page", 3);\n' + gecko.LINE
    assert not (moz / "apkstarter.default").exists()


def test_the_user_s_own_setting_wins(tmp_path):
    moz = tmp_path / "files" / "mozilla"
    gecko.no_gpu_process(tmp_path)
    user_js = moz / "apkstarter.default" / "user.js"
    user_js.write_text('user_pref("layers.gpu-process.enabled", true);\n')
    assert gecko.no_gpu_process(tmp_path) == []
    assert user_js.read_text() == 'user_pref("layers.gpu-process.enabled", true);\n'


def test_unreadable_profiles_ini_is_left_alone(tmp_path):
    moz = tmp_path / "files" / "mozilla"
    moz.mkdir(parents=True)
    (moz / "profiles.ini").write_text("not an ini file\n")
    assert gecko.no_gpu_process(tmp_path) == []
    assert sorted(p.name for p in moz.iterdir()) == ["profiles.ini"]
