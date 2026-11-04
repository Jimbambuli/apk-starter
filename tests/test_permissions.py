"""Permissions per app (Spec 18, Phase 3): the dangerous ones from the manifest, by group, each with a switch;
a group switched off reaches ATL, which answers «denied»."""
from pathlib import Path

from apk_starter import permissions
from apk_starter.launch import build_env
from apk_starter.paths import Paths
from apk_starter.settings import Settings, load_settings, save_settings

ASKED = ["android.permission.INTERNET", "android.permission.ACCESS_FINE_LOCATION", "android.permission.CAMERA",
         "android.permission.POST_NOTIFICATIONS", "android.permission.RECORD_AUDIO",
         "android.permission.READ_CONTACTS", "android.permission.GET_ACCOUNTS",
         "android.permission.ACCESS_COARSE_LOCATION"]


def test_groups_in_android_order_only_dangerous_ones():
    assert permissions.groups(ASKED) == ["location", "camera", "microphone", "contacts", "accounts",
                                         "notifications"]
    assert permissions.groups(["android.permission.INTERNET"]) == []


def test_denied_groups_become_the_permissions_atl_denies():
    assert permissions.denied_permissions(["location", "nonsense"]) == [
        "android.permission.ACCESS_BACKGROUND_LOCATION", "android.permission.ACCESS_COARSE_LOCATION",
        "android.permission.ACCESS_FINE_LOCATION"]


def test_switch_is_remembered_per_app_and_reaches_atl(tmp_path):
    p = Paths(tmp_path / "d", tmp_path / "c", Path("/app"))
    assert load_settings(p.settings()).app("org.a").denied == ()           # default: as before, all allowed
    save_settings(p.settings(), Settings().with_app("org.a", denied=("camera", "location")))
    assert load_settings(p.settings()).app("org.a").denied == ("camera", "location")
    env = build_env(p, "org.a", {})
    assert env["ATL_DENIED_PERMISSIONS"].split(",") == [
        "android.permission.ACCESS_BACKGROUND_LOCATION", "android.permission.ACCESS_COARSE_LOCATION",
        "android.permission.ACCESS_FINE_LOCATION", "android.permission.CAMERA"]
    assert "ATL_DENIED_PERMISSIONS" not in build_env(p, "org.b", {"ATL_DENIED_PERMISSIONS": "x"})
