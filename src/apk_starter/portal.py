"""XDG desktop portals: DynamicLauncher (menu entries) and FileChooser. `gi` is imported only inside functions."""
import re
import uuid
from pathlib import Path

from .apkinfo import PACKAGE_RE
from .cli import APP_ID
from .errors import StarterError, UsageError

RESPONSE_TIMEOUT = 300
PORTAL_NAME = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
UNAVAILABLE_ERRORS = ("ServiceUnknown", "UnknownMethod", "UnknownInterface", "UnknownObject")


class PortalUnavailable(StarterError):
    pass


def desktop_file_id(pkg: str) -> str:
    return f"{APP_ID}.{pkg}.desktop"


def _check_pkg(pkg: str) -> str:
    if len(pkg) > 255 or not PACKAGE_RE.fullmatch(pkg):
        raise StarterError(f"invalid package name: {pkg!r}")
    return pkg


def desktop_entry(pkg: str, label: str) -> str:
    _check_pkg(pkg)
    text = re.sub(r"\r\n|\r|\n", " ", label)
    text = "".join(c for c in text if c == " " or c.isprintable())[:100].strip() or pkg
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={text}\n"
        f"Exec=apk-starter {pkg}\n"
        "Terminal=false\n"
        "Categories=Utility;\n"
        f"StartupWMClass={pkg}\n"
        f"X-ApkStarter-Package={pkg}\n"
    )


def request_path(unique_name: str, token: str) -> str:
    return f"{PORTAL_PATH}/request/{unique_name.lstrip(':').replace('.', '_')}/{token}"


def _gi():
    try:
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib
    except (ImportError, ValueError) as e:
        raise PortalUnavailable(f"PyGObject is not available: {e}") from None
    return Gio, GLib


def _connect(Gio, GLib):
    try:
        return Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except GLib.Error as e:
        raise PortalUnavailable(f"no session bus: {e.message}") from None


def _call(conn, Gio, GLib, iface: str, method: str, params):
    try:
        return conn.call_sync(PORTAL_NAME, PORTAL_PATH, iface, method, params, None,
                              Gio.DBusCallFlags.NONE, -1, None)
    except GLib.Error as e:
        if any(name in e.message for name in UNAVAILABLE_ERRORS):
            raise PortalUnavailable(f"{iface} is not available") from None
        raise StarterError(f"portal call {method} failed: {e.message}") from None


def _request(conn, Gio, GLib, iface: str, method: str, make_params):
    """Call a portal method that answers through a Request object; returns (response, results)."""
    token = "apkstarter" + uuid.uuid4().hex
    path = request_path(conn.get_unique_name(), token)
    loop = GLib.MainLoop()
    answer: list = []

    def on_response(_c, _s, _p, _i, _sig, params):
        answer.append(params.unpack())
        loop.quit()

    sub = conn.signal_subscribe(PORTAL_NAME, "org.freedesktop.portal.Request", "Response", path, None,
                                Gio.DBusSignalFlags.NONE, on_response)
    try:
        _call(conn, Gio, GLib, iface, method, make_params(token))
        if not answer:
            GLib.timeout_add_seconds(RESPONSE_TIMEOUT, loop.quit)
            loop.run()
        if not answer:
            raise StarterError("portal did not answer")
        return answer[0]
    finally:
        conn.signal_unsubscribe(sub)


def install_launcher(pkg: str, label: str, icon: bytes) -> None:
    _check_pkg(pkg)
    Gio, GLib = _gi()
    conn = _connect(Gio, GLib)
    iface = "org.freedesktop.portal.DynamicLauncher"
    serialized = Gio.BytesIcon.new(GLib.Bytes.new(icon)).serialize()

    def prepare(token: str):
        opts = {"handle_token": GLib.Variant("s", token), "launcher_type": GLib.Variant("u", 1),
                "editable_name": GLib.Variant("b", True), "editable_icon": GLib.Variant("b", False)}
        return GLib.Variant("(ssva{sv})", ("", label, serialized, opts))

    response, results = _request(conn, Gio, GLib, iface, "PrepareInstall", prepare)
    if response != 0 or "token" not in results:
        raise StarterError("menu entry not created (cancelled)")
    _call(conn, Gio, GLib, iface, "Install",
          GLib.Variant("(sssa{sv})", (results["token"], desktop_file_id(pkg), desktop_entry(pkg, label), {})))


def uninstall_launcher(pkg: str) -> None:
    _check_pkg(pkg)
    Gio, GLib = _gi()
    conn = _connect(Gio, GLib)
    try:
        _call(conn, Gio, GLib, "org.freedesktop.portal.DynamicLauncher", "Uninstall",
              GLib.Variant("(sa{sv})", (desktop_file_id(pkg), {})))
    except StarterError as e:
        not_found = "not found" in str(e).lower() or "NotFound" in str(e)
        if isinstance(e, PortalUnavailable) or not not_found:
            raise


def choose_apk() -> Path:
    Gio, GLib = _gi()
    conn = _connect(Gio, GLib)

    def open_file(token: str):
        opts = {"handle_token": GLib.Variant("s", token), "modal": GLib.Variant("b", True),
                "filters": GLib.Variant("a(sa(us))", [("Android apps", [(0, "*.apk"),
                                                                        (1, "application/vnd.android.package-archive")])])}
        return GLib.Variant("(ssa{sv})", ("", "Open APK", opts))

    response, results = _request(conn, Gio, GLib, "org.freedesktop.portal.FileChooser", "OpenFile", open_file)
    uris = results.get("uris") or []
    path = Gio.File.new_for_uri(uris[0]).get_path() if response == 0 and uris else None
    if not path:
        raise UsageError("no APK selected")
    return Path(path)
