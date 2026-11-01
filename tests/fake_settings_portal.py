"""Minimal settings portal on a private session bus: light scheme and an orange accent; 300 ms after the first
ReadAll it switches to dark with a blue accent (SettingChanged), as when the user changes the desktop's look."""
import sys

from gi.repository import Gio, GLib

XML = """<node><interface name="org.freedesktop.portal.Settings">
<method name="ReadAll"><arg type="as" direction="in"/><arg type="a{sa{sv}}" direction="out"/></method>
<signal name="SettingChanged"><arg type="s"/><arg type="s"/><arg type="v"/></signal>
<property name="version" type="u" access="read"/></interface></node>"""
PATH = "/org/freedesktop/portal/desktop"
NS = "org.freedesktop.appearance"


def on_call(conn, _sender, _path, _iface, _method, _params, inv):
    inv.return_value(GLib.Variant("(a{sa{sv}})", ({NS: {
        "color-scheme": GLib.Variant("u", 2), "accent-color": GLib.Variant("(ddd)", (1.0, 0.5, 0.0))}},)))

    def change():
        for key, value in (("color-scheme", GLib.Variant("u", 1)),
                           ("accent-color", GLib.Variant("(ddd)", (0.0, 0.0, 1.0)))):
            conn.emit_signal(None, PATH, "org.freedesktop.portal.Settings", "SettingChanged",
                             GLib.Variant("(ssv)", (NS, key, value)))
        return False
    GLib.timeout_add(300, change)


def on_bus(conn, _name):
    conn.register_object(PATH, Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0], on_call)


Gio.bus_own_name(Gio.BusType.SESSION, "org.freedesktop.portal.Desktop", Gio.BusNameOwnerFlags.NONE,
                 on_bus, lambda *a: print("ready", flush=True), lambda *a: sys.exit(1))
GLib.MainLoop().run()
