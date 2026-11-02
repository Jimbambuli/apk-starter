"""An app store installs an app (Aurora Store through ATL's PackageInstaller, see ATL_INSTALL_COMMAND): ask the
user like Android's PackageInstallerActivity, then install like --install.

The exit status is the PackageInstaller status ATL hands back to the app: 0 installed, 3 aborted by the user,
4 not a valid APK, 7 incompatible (no x86_64 code), 1 other failure; the last line on stderr is its message."""
import os
import sys
from collections.abc import Mapping
from pathlib import Path

from .apkinfo import ApkInfo
from .errors import ApkError, StarterError, describe_error
from .i18n import _
from .paths import Paths
from . import splits

STATUS_SUCCESS, STATUS_FAILURE, STATUS_ABORTED, STATUS_INVALID, STATUS_INCOMPATIBLE = 0, 1, 3, 4, 7


def _say(text: str) -> None:
    print(f"apk-starter: {text}", file=sys.stderr, flush=True)


def ask(info: ApkInfo, installer: str, update: bool, env: Mapping[str, str]) -> bool:
    """The question in a small window of its own (not the management window: that may be closed, or open in
    another instance). True: install."""
    if not any(env.get(v) for v in ("WAYLAND_DISPLAY", "DISPLAY")):
        raise StarterError(_("no display to ask whether to install {app}").format(app=info.label))
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gio, Gtk
    from .gui import Dialog, install_styles
    answer = []
    app = Gtk.Application(application_id="io.github.Jimbambuli.ApkStarter.Install",
                          flags=Gio.ApplicationFlags.NON_UNIQUE)

    def activate(a: Gtk.Application) -> None:
        install_styles(Gtk.Window().get_display())
        owner = Gtk.ApplicationWindow(application=a, title="APK Starter")    # stays hidden: the dialog's parent
        verb = _("Update") if update else _("Install")
        heading = (_("Update {app}?") if update else _("Install {app}?")).format(app=info.label)
        body = _("{installer} wants to install {app} {version}. It gets a menu entry like every installed "
                 "app.").format(installer=installer, app=info.label, version=info.version_name or "")
        d = Dialog(owner, heading, body, [("cancel", _("Cancel")), ("install", verb)],
                   then=lambda rid: (answer.append(rid == "install"), a.quit()))
        d.set_application(a)
        d.present()
    app.connect("activate", activate)
    app.run([sys.argv[0]])
    return bool(answer and answer[0])


def run(paths: Paths, env: Mapping[str, str], files: list[Path]) -> int:
    from .cli import install_parts, lang_from_env, parts_dir, read_parts
    installer = env.get("ATL_INSTALLER_LABEL") or env.get("ATL_INSTALLER_PACKAGE") or _("An app")
    lang = lang_from_env(env)
    try:
        with parts_dir(paths) as tmp:
            info, parts = read_parts(paths, files, lang, Path(tmp))
            update = paths.meta(info.package).is_file()
            if not ask(info, installer, update, env):
                _say(_("Installation of {app} cancelled").format(app=info.label))
                return STATUS_ABORTED
            try:
                install_parts(paths, info, parts.splits, lang, env)
            except StarterError as e:
                if not paths.meta(info.package).is_file():
                    raise
                _say(describe_error(e))         # installed, only the menu entry is missing: the store may open it
        return STATUS_SUCCESS
    except splits.IncompatibleError as e:
        _say(describe_error(e))
        return STATUS_INCOMPATIBLE
    except ApkError as e:
        _say(describe_error(e))
        return STATUS_INVALID
    except (StarterError, OSError) as e:
        _say(describe_error(e))
        return STATUS_FAILURE
