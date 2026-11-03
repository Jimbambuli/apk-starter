# APK Starter

Run Android apps (APK files) on the Linux desktop – a Flatpak built on
[Android Translation Layer (ATL)](https://gitlab.com/android_translation_layer/android_translation_layer).

## Install

```
flatpak install --user https://jimbambuli.github.io/apk-starter/io.github.Jimbambuli.ApkStarter.flatpakref
```

The repository is signed (key: [pages/apk-starter.gpg](pages/apk-starter.gpg)). Flatpak exports only the app ID as a
command, so for a short command on the host add an alias:

```
alias apk-starter='flatpak run --file-forwarding io.github.Jimbambuli.ApkStarter'
```

## Use

Open **APK Starter** from the application menu (or run `apk-starter` without arguments): the installed apps are
listed on the left (search with Ctrl+F or by just typing, sort by name, last start, last install or data size), the
chosen app's details on the right – version, target SDK, dates, data size, whether it is prepared for this computer,
and its newest log and debug recordings. Double-click or Enter starts an app, Delete asks to uninstall it.
Install a new APK with **+** or by dropping it anywhere onto the window. The window takes the desktop's GTK theme,
colour scheme and accent colour and keeps the desktop's own title bar (plain GTK 4); it remembers its size, the sort
order and the chosen app. Every installed app also gets its own menu entry.

Next to **Start** stand **Delete data …**, **Uninstall …** and **⚙**. The ⚙ menu holds each app's own **Window bar**
(remembered): **With back button** (the default) is a title
bar drawn in the desktop's GTK theme with a back button; under KDE it gets the buttons and the height of the KWin
decoration. **From the system** is the desktop's own title bar (where the compositor draws one, e.g. KDE Plasma),
without the back button; **From the system + navigation bar** adds Android's navigation bar under the window (◁ back,
○ the app's first page, □ its open pages). Escape and the back button of the mouse always go back. **Debug** (also
remembered per app, until you untick it) records every start of the app, also from its menu entry; a dot on ⚙ shows
that it is on. The menu lists the Debug recordings and offers **Open log folder**. Every start of an app, with or
without Debug, also keeps a plain log of ATL's output (the last 3 per app).
When an app crashes, a message says what its exit code means; **Open log folder** shows the log of that start.

The same is available on the command line:

```
apk-starter                          # open the window
apk-starter @@ app.apk @@            # start an app (@@ lets Flatpak pass the file into the sandbox)
apk-starter --install @@ app.apk @@  # add it to the application menu (asks via the desktop portal)
apk-starter --install @@ base.apk @@ @@ split_config.x86_64.apk @@ ...  # a split APK: the base and its parts
apk-starter --install @@ app.apks @@ # a bundle (.apks/.xapk); parts for ARM only are left out
apk-starter --setup-aurora           # download Aurora Store from F-Droid, check its signature, install it
apk-starter --list                   # installed apps
apk-starter --remove PACKAGE [--purge]
apk-starter --remove-all [--purge]   # remove every installed app and its menu entry
apk-starter --setup-microg           # download microG from its repository, check it, install it
apk-starter --debug @@ app.apk @@    # record the app for atl-doctor (a little slower), --debug=DIR for a folder
apk-starter --sdk N @@ app.apk @@    # override the SDK level passed to ATL
apk-starter --size WxH @@ app.apk @@ # window size (remembered per app)
apk-starter --uri URI PACKAGE        # open a link in the app (in the running app, if it runs)
apk-starter @@ app.apk @@ -- ARGS    # everything after the APK is passed on to ATL
apk-starter --version
```

Exit codes: 0 ok, 1 error (e.g. cannot write), 2 wrong usage, 3 APK not readable, 7 the app has native code only
for other processors (e.g. ARM); if the app is ended by a signal, 128+N.

### Links

The menu entry of an installed app offers it for the links its activities accept (Android intent filters with
VIEW and BROWSABLE): its own schemes (e.g. `jwlibrary:`) and web links (`https:`, e.g. YouTube links for NewPipe)
under **Open with**; the web browser stays the default for web links. A link goes to the running app,
which gets it like on Android (`onNewIntent`). Apps installed before this need `--install` again.

### App stores (Aurora Store)

**+ → Set up Aurora Store…** (or `--setup-aurora`) downloads the official Aurora Store from F-Droid, checks its APK
signature against Aurora OSS's certificate and installs it like any app; Aurora Store is not part of the Flatpak.
When Aurora Store (or another store using Android's PackageInstaller) installs an app, ATL hands its APKs to APK
Starter, which asks **Install …?** and installs it with a menu entry; the store then sees it as installed. Split APKs
from Google Play (base and configuration parts) are kept together; apps with native code only for ARM are refused
with a message. Sign in anonymously in Aurora Store: signing in with your own Google account may break Google's terms
of service.

### microG (Google services for apps)

**+ → Set up microG…** (or `--setup-microg`) works like Aurora Store: it downloads GmsCore and GsfProxy from the
official microG repository (microg.org/fdroid), checks the SHA-256 checksums and GmsCore's APK signature against the
NOGAPPS Project's certificate, and installs GmsCore as the app «microG settings»; GsfProxy goes into the app folder
for ATL. microG is not part of the Flatpak. Once it is installed, the entry disappears from the menu (Aurora Store's
too). Every app then shares microG's data. In «microG settings» switch on «Google device registration» and «Cloud
Messaging»; to sign in: Accounts → Add Google account.

Without the alias use `flatpak run --file-forwarding io.github.Jimbambuli.ApkStarter …` instead of `apk-starter`.
Double-clicking an APK in the file manager and starting an installed app from the menu work without any command.

Data lives under `~/.var/app/io.github.Jimbambuli.ApkStarter/data/`: `apps/` (installed APKs), `data/<package>/`
(app data), `logs/` (a plain log of every start, folders from Debug).

Logs and recordings, for finding out why an app misbehaves under ATL (with
[atl-doctor](https://github.com/Jimbambuli/atl-doctor)): every start writes ATL's text output (stdout, stderr, Android
log) to a plain log in `logs/`, without any extra process; the last 3 per app stay. **Debug** (`--debug`, «Debug»
ticked) records a folder instead: the agent attached as soon as the
runtime runs (exceptions with stack, also caught ones, lifecycle), the calls of ATL's stub methods (counted by ATL
itself), a time line from ART's sampling profiler, the view trees and a verbose Android log; the app runs only a
little slower (no full method trace, which made apps lose their own start-up races). Of the Debug recordings the last
5 per app stay, and together at most 200 MB per app (the older go first, the newest always stays).

APK Starter only records; [atl-doctor](https://github.com/Jimbambuli/atl-doctor) evaluates the recordings (crash
cause, ATL gaps, exceptions; it compares the stub hits with Android's source so that methods as trivial in Android
are not counted as gaps). Install it once with `pipx install git+https://github.com/Jimbambuli/atl-doctor`, then:

```
cd ~/.var/app/io.github.Jimbambuli.ApkStarter/data/logs
atl-doctor report --log org.example.app-20261102-101010.log    # a plain log
atl-doctor report org.example.app-20261102-101010              # a Debug recording (folder)
atl-doctor report -f html -o report.html org.example.app-20261102-101010   # as a page, e.g. for a bug report
```

Like Android, installing compiles an app for your processor (shown as «Preparing …»), so its first start is as
fast as later ones; the Android runtime itself is prepared once and again after an update of APK Starter. An APK
started without installing is compiled quickly at its first start and fully in the background while it runs. The
compiled code lives in `~/.var/app/io.github.Jimbambuli.ApkStarter/cache/art/`; deleting it is safe, it is made
again.

Uninstalling: run `apk-starter --remove-all` before `flatpak uninstall io.github.Jimbambuli.ApkStarter`.
`flatpak uninstall` does not remove the menu entries of the installed apps; desktops hide them once APK Starter is
gone, but the files stay in `~/.local/share/xdg-desktop-portal/` (and come back if you install APK Starter again).

Bring your own APK – this project ships ATL and its free dependencies only, no apps. Apps that need Google Play
Services, Jetpack Compose or ARM-only native libraries are not expected to work.

## Permissions

Besides the display, sound, network and GPU, APK Starter reads – never writes – these parts of your system, to take
over the desktop's look on GTK's own Adwaita (`settings.ini` of GTK 4: icons, cursor, font and the order of the window
buttons; KDE's colour scheme in `colors.css`), for its window and for the apps' title bars, and to follow a change of
them at once:

- `--filesystem=xdg-config/gtk-4.0:ro`: your GTK 4 settings and colours (read only);
- `--filesystem=xdg-config/gtk-3.0/assets:ro`: under KDE the pictures of the KWin window buttons, for the apps'
  title bars (read only);
- `--filesystem=xdg-config/kdeglobals:ro`, `xdg-config/kwinrc:ro` and `xdg-config/breezerc:ro`: KDE's title font,
  window decoration and Breeze button size (read only), so the apps' title bars are as high as KWin's.

The system's own themes and icons are not visible in the sandbox. Your icon theme is used when it is installed as a
Flatpak extension (e.g. `flatpak install flathub org.freedesktop.Platform.Icontheme.Paper`); otherwise the icons
are GTK's Adwaita icons.

For the apps' media in the desktop's media controls and its media keys (MPRIS), APK Starter may own the D-Bus names
`org.mpris.MediaPlayer2.*` (`--own-name`).

Without them (e.g. `flatpak override --user --nofilesystem=xdg-config/gtk-4.0 io.github.Jimbambuli.ApkStarter`) everything
works with GTK's default theme in the desktop's light or dark variant.

## Translations

The window, `--help`, the messages and the menu/AppStream texts follow the system language. Included: German,
French, Italian, Spanish, Brazilian Portuguese, Dutch, Polish, Czech, Ukrainian, Russian, Japanese and simplified
Chinese; English is the source language. The translations are machine-assisted – corrections are welcome as issues or
pull requests against `po/<language>.po`.

After changing texts, update the template and the catalogs:

```
xgettext --from-code=UTF-8 --keyword=_ --keyword=N_ --add-comments=Translators --package-name=apk-starter \
  -f <(grep -v '^#' po/POTFILES) -o po/apk-starter.pot
for l in $(cat po/LINGUAS); do msgmerge --update --backup=none po/$l.po po/apk-starter.pot; done
```

**Status:** pre-release, not yet published.

## Credits and license

License: GPL-3.0-or-later (same as ATL), see [LICENSE](LICENSE). Not affiliated with Google; Android is a trademark
of Google LLC.

The app icon (`data/icons/io.github.Jimbambuli.ApkStarter.svg`) is licensed under
[CC BY 3.0](https://creativecommons.org/licenses/by/3.0/). The Android robot is reproduced or modified from work
created and shared by Google and used according to terms described in the Creative Commons 3.0 Attribution License.
The letters "APK" in the icon are outlines of DejaVu Sans Bold (Bitstream Vera/DejaVu font license).
