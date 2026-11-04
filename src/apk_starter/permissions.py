"""Permissions per app (Spec 18, Phase 3): Android's dangerous permissions by group, as the system's settings show
them. A group switched off in the window is remembered per app (settings.json) and reaches ATL as the permissions it
answers «denied» (ATL_DENIED_PERMISSIONS); the default is as before: all allowed."""
from collections.abc import Iterable

from .i18n import N_

P = "android.permission."
# (key, label, permissions) in the order of Android's permission settings
GROUPS = (
    ("location", N_("Location"), ("ACCESS_FINE_LOCATION", "ACCESS_COARSE_LOCATION", "ACCESS_BACKGROUND_LOCATION")),
    ("camera", N_("Camera"), ("CAMERA",)),
    ("microphone", N_("Microphone"), ("RECORD_AUDIO",)),
    ("contacts", N_("Contacts"), ("READ_CONTACTS", "WRITE_CONTACTS")),
    ("accounts", N_("Accounts"), ("GET_ACCOUNTS",)),
    ("calendar", N_("Calendar"), ("READ_CALENDAR", "WRITE_CALENDAR")),
    ("phone", N_("Phone"), ("READ_PHONE_STATE", "READ_PHONE_NUMBERS", "CALL_PHONE", "READ_CALL_LOG",
                            "WRITE_CALL_LOG", "ANSWER_PHONE_CALLS")),
    ("sms", N_("SMS"), ("SEND_SMS", "RECEIVE_SMS", "READ_SMS")),
    ("storage", N_("Files and media"), ("READ_EXTERNAL_STORAGE", "WRITE_EXTERNAL_STORAGE", "READ_MEDIA_IMAGES",
                                        "READ_MEDIA_VIDEO", "READ_MEDIA_AUDIO")),
    ("nearby", N_("Nearby devices"), ("BLUETOOTH_SCAN", "BLUETOOTH_CONNECT", "BLUETOOTH_ADVERTISE",
                                      "NEARBY_WIFI_DEVICES")),
    ("sensors", N_("Body sensors"), ("BODY_SENSORS",)),
    ("activity", N_("Physical activity"), ("ACTIVITY_RECOGNITION",)),
    ("notifications", N_("Notifications"), ("POST_NOTIFICATIONS",)),
)
LABELS = {key: label for key, label, _p in GROUPS}


def groups(requested: Iterable[str]) -> list[str]:
    """The groups of the app's <uses-permission> entries that Android asks the user for."""
    asked = set(requested)
    return [key for key, _label, perms in GROUPS if any(P + p in asked for p in perms)]


def denied_permissions(denied: Iterable[str]) -> list[str]:
    off = set(denied)
    return sorted(P + p for key, _label, perms in GROUPS if key in off for p in perms)
