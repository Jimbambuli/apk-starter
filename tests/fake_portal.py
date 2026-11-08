"""Minimal DynamicLauncher portal on a private session bus; records Install calls to argv[1], knows one entry."""
import json
import os
import sys

from gi.repository import Gio, GLib

XML = """<node><interface name="org.freedesktop.portal.DynamicLauncher">
<method name="PrepareInstall"><arg type="s" direction="in"/><arg type="s" direction="in"/>
<arg type="v" direction="in"/><arg type="a{sv}" direction="in"/><arg type="o" direction="out"/></method>
<method name="Install"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="s" direction="in"/>
<arg type="a{sv}" direction="in"/></method>
<method name="GetDesktopEntry"><arg type="s" direction="in"/><arg type="s" direction="out"/></method>
</interface>
<interface name="org.freedesktop.portal.Screenshot">
<method name="Screenshot"><arg type="s" direction="in"/><arg type="a{sv}" direction="in"/><arg type="o" direction="out"/></method>
</interface></node>"""
ENTRIES = {"io.github.Jimbambuli.ApkStarter.org.x.y.desktop": "[Desktop Entry]\nName=Demo\n"}
PATH = "/org/freedesktop/portal/desktop"


def on_call(conn, sender, _path, _iface, method, params, inv):
    if method == "Screenshot":                            # answers at once with the file named in the environment
        opts = params.unpack()[1]
        assert opts["interactive"] is False
        req = f"{PATH}/request/{sender.lstrip(':').replace('.', '_')}/{opts['handle_token']}"
        inv.return_value(GLib.Variant("(o)", (req,)))
        GLib.timeout_add(100, lambda: conn.emit_signal(
            sender, req, "org.freedesktop.portal.Request", "Response",
            GLib.Variant("(ua{sv})", (0, {"uri": GLib.Variant("s", os.environ["FAKE_SHOT_URI"])}))) and False)
    elif method == "PrepareInstall":
        token = params.unpack()[3]["handle_token"]
        req = f"{PATH}/request/{sender.lstrip(':').replace('.', '_')}/{token}"
        inv.return_value(GLib.Variant("(o)", (req,)))
        def answer():                                     # late, like a dialog the user confirms
            conn.emit_signal(sender, req, "org.freedesktop.portal.Request", "Response",
                             GLib.Variant("(ua{sv})", (0, {"token": GLib.Variant("s", "t1")})))
            return False
        GLib.timeout_add(300, answer)
    elif method == "GetDesktopEntry":
        name = params.unpack()[0]
        if name in ENTRIES:
            inv.return_value(GLib.Variant("(s)", (ENTRIES[name],)))
        else:                                             # like the portal: the file's GError, unmapped
            inv.return_dbus_error("org.gtk.GDBus.UnmappedGError.Quark._g_2dfile_2derror_2dquark.Code4",
                                  f"Failed to open file “{name}”: No such file or directory")
    else:
        with open(sys.argv[1], "a") as f:
            f.write(json.dumps(params.unpack()[:2]) + "\n")
        inv.return_value(None)


def on_bus(conn, _name):
    for iface in Gio.DBusNodeInfo.new_for_xml(XML).interfaces:
        conn.register_object(PATH, iface, on_call)


Gio.bus_own_name(Gio.BusType.SESSION, "org.freedesktop.portal.Desktop", Gio.BusNameOwnerFlags.NONE,
                 on_bus, lambda *a: print("ready", flush=True), lambda *a: sys.exit(1))
GLib.MainLoop().run()
