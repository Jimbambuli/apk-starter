"""Minimal DynamicLauncher portal on a private session bus; records Install calls to argv[1]."""
import json
import sys

from gi.repository import Gio, GLib

XML = """<node><interface name="org.freedesktop.portal.DynamicLauncher">
<method name="PrepareInstall"><arg type="s" direction="in"/><arg type="s" direction="in"/>
<arg type="v" direction="in"/><arg type="a{sv}" direction="in"/><arg type="o" direction="out"/></method>
<method name="Install"><arg type="s" direction="in"/><arg type="s" direction="in"/><arg type="s" direction="in"/>
<arg type="a{sv}" direction="in"/></method></interface></node>"""
PATH = "/org/freedesktop/portal/desktop"


def on_call(conn, sender, _path, _iface, method, params, inv):
    if method == "PrepareInstall":
        token = params.unpack()[3]["handle_token"]
        req = f"{PATH}/request/{sender.lstrip(':').replace('.', '_')}/{token}"
        inv.return_value(GLib.Variant("(o)", (req,)))
        def answer():                                     # late, like a dialog the user confirms
            conn.emit_signal(sender, req, "org.freedesktop.portal.Request", "Response",
                             GLib.Variant("(ua{sv})", (0, {"token": GLib.Variant("s", "t1")})))
            return False
        GLib.timeout_add(300, answer)
    else:
        with open(sys.argv[1], "a") as f:
            f.write(json.dumps(params.unpack()[:2]) + "\n")
        inv.return_value(None)


def on_bus(conn, _name):
    conn.register_object(PATH, Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0], on_call)


Gio.bus_own_name(Gio.BusType.SESSION, "org.freedesktop.portal.Desktop", Gio.BusNameOwnerFlags.NONE,
                 on_bus, lambda *a: print("ready", flush=True), lambda *a: sys.exit(1))
GLib.MainLoop().run()
