"""Translations: gettext domain `apk-starter`, catalogs in <prefix>/share/locale (/app/share/locale in the Flatpak).

The language follows LANGUAGE, LC_ALL, LC_MESSAGES and LANG; English is the source language and the fallback."""
import gettext
from pathlib import Path

DOMAIN = "apk-starter"


def setup(prefix: Path) -> None:
    """Make our catalog the default domain; argparse's own texts ("usage: ", ...) are looked up there too."""
    gettext.bindtextdomain(DOMAIN, str(prefix / "share" / "locale"))
    gettext.textdomain(DOMAIN)


def follow_desktop(env) -> None:
    """Called back by an app (install from an app store, «Open»): ATL has set LC_ALL=C.UTF-8 for the apps; the
    desktop's locale came along in APK_STARTER_LOCALE (launch.build_env)."""
    saved = env.get("APK_STARTER_LOCALE")
    if saved and env.get("LC_ALL", "").split(".")[0] in ("C", "POSIX"):
        env["LC_ALL"] = saved


def _(message: str) -> str:
    return gettext.dgettext(DOMAIN, message)


def N_(message: str) -> str:
    """Mark for extraction only."""
    return message


# argparse texts the user sees; listed here only so that xgettext puts them into the catalog.
ARGPARSE_TEXTS = (
    N_("usage: "), N_("options"), N_("show this help message and exit"), N_("%(prog)s: error: %(message)s\n"),
    N_("unrecognized arguments: %s"), N_("expected one argument"), N_("argument %(argument_name)s: %(message)s"),
    N_("not allowed with argument %s"), N_("invalid %(type)s value: %(value)r"),
)
