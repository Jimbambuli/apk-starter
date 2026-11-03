"""XDG desktop portal DynamicLauncher (menu entries). `gi` is imported only inside functions."""
import re
import uuid
from collections.abc import Iterable

from .apkinfo import PACKAGE_RE
from .axml import LinkFilter
from .cli import ENTRY_PREFIX
from .errors import StarterError, UsageError
from .i18n import _

RESPONSE_TIMEOUT = 300
PORTAL_NAME = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
UNAVAILABLE_ERRORS = ("ServiceUnknown", "UnknownMethod", "UnknownInterface", "UnknownObject")


class PortalUnavailable(StarterError):
    pass


def desktop_file_id(pkg: str) -> str:
    return f"{ENTRY_PREFIX}.{pkg}.desktop"


def _check_pkg(pkg: str) -> str:
    if len(pkg) > 255 or not PACKAGE_RE.fullmatch(pkg):
        raise StarterError(_("invalid package name: {package}").format(package=repr(pkg)))
    return pkg


# RFC 3986 scheme; not links: files, inline content, script and Android-internal intents
_SCHEME_RE = re.compile(r"[a-z][a-z0-9+.-]*")
_NOT_LINKS = {"content", "file", "data", "javascript", "vbscript", "about", "blob", "intent"}


def link_mime_types(filters: Iterable[LinkFilter]) -> list[str]:
    """x-scheme-handler types for the menu entry from the app's link filters (VIEW + BROWSABLE). MimeType only offers
    the app (http(s) under «Open with», like Android's chooser); the default stays what mimeapps.list says, for
    web links the browser."""
    out: set[str] = set()
    for f in filters:
        for scheme in (s.lower() for s in f.schemes):
            if not _SCHEME_RE.fullmatch(scheme) or scheme in _NOT_LINKS:
                continue
            out.add(f"x-scheme-handler/{scheme}")
    return sorted(out)


def desktop_entry(pkg: str, label: str, mime_types: list[str] = ()) -> str:
    _check_pkg(pkg)
    text = re.sub(r"\r\n|\r|\n", " ", label)
    text = "".join(c for c in text if c == " " or c.isprintable())[:100].strip() or pkg
    # a link: the desktop passes it with %u, the starter hands it to the app (--uri), empty from the menu
    exec_line = f"apk-starter --uri %u {pkg}" if mime_types else f"apk-starter {pkg}"
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={text}\n"
        f"Exec={exec_line}\n"
        "Terminal=false\n"
        "Categories=Utility;\n"
        f"StartupWMClass={ENTRY_PREFIX}.{pkg}\n"
        f"X-ApkStarter-Package={pkg}\n"
        + (f"MimeType={';'.join(mime_types)};\n" if mime_types else "")
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
        raise StarterError(_("portal call {method} failed: {reason}").format(method=method, reason=e.message)) from None


def _request(conn, Gio, GLib, iface: str, method: str, make_params):
    """Call a portal method that answers through a Request object; returns (response, results)."""
    # Also called from a worker thread of the window: loop.run() there only waits; the signal is
    # dispatched by whoever runs the default main context (the GTK main loop) and on_response quits
    # this loop from there. tests/fake_portal.py covers this case. The package lock stays held meanwhile.
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
            raise StarterError(_("portal did not answer"))
        return answer[0]
    finally:
        conn.signal_unsubscribe(sub)


def install_launcher(pkg: str, label: str, icon: bytes, mime_types: list[str] = ()) -> None:
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
        raise StarterError(_("menu entry not created (cancelled)"))
    _call(conn, Gio, GLib, iface, "Install",
          GLib.Variant("(sssa{sv})", (results["token"], desktop_file_id(pkg), desktop_entry(pkg, label, mime_types), {})))


def launcher_entry(pkg: str) -> str | None:
    """The menu entry as the portal keeps it (its Exec and Icon rewritten), None when there is none."""
    _check_pkg(pkg)
    Gio, GLib = _gi()
    conn = _connect(Gio, GLib)
    try:
        reply = _call(conn, Gio, GLib, "org.freedesktop.portal.DynamicLauncher", "GetDesktopEntry",
                      GLib.Variant("(s)", (desktop_file_id(pkg),)))
    except PortalUnavailable:
        raise
    except StarterError:
        return None                     # the portal answers with the file's error: removed or never made
    return reply.unpack()[0]


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

