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
its log and debug recordings with their reports. Double-click or Enter starts an app, Delete asks to uninstall it.
Install a new APK with **+** or by dropping it anywhere onto the window. The window takes the desktop's GTK theme,
colour scheme and accent colour and keeps the desktop's own title bar (plain GTK 4); it remembers its size, the sort
order and the chosen app. Every installed app also gets its own menu entry.

The apps get a title bar with a back button, drawn in the desktop's GTK theme, so it looks like the title bars of
the other windows; Escape and the back button of the mouse go back as well. **Title bar from the system** in the
main menu (☰) gives them the desktop's own title bar instead (where the compositor draws one, e.g. KDE Plasma),
without the back button.

The same is available on the command line:

```
apk-starter                          # open the window
apk-starter @@ app.apk @@            # start an app (@@ lets Flatpak pass the file into the sandbox)
apk-starter --install @@ app.apk @@  # add it to the application menu (asks via the desktop portal)
apk-starter --list                   # installed apps
apk-starter --remove PACKAGE [--purge]
apk-starter --remove-all [--purge]   # remove every installed app and its menu entry
apk-starter --log @@ app.apk @@      # write a log that atl-doctor can read
apk-starter --log=FILE @@ app.apk @@ # same, to a chosen file (must be writable inside the sandbox,
                                     # e.g. under ~/.var/app/io.github.Jimbambuli.ApkStarter/)
apk-starter --debug @@ app.apk @@    # record the app for atl-doctor (much slower), --debug=DIR for a folder
apk-starter --report [FILE|DIR|PACKAGE]  # report of the newest (or the given) log/debug recording
apk-starter --sdk N @@ app.apk @@    # override the SDK level passed to ATL
apk-starter --size WxH @@ app.apk @@ # window size (remembered per app)
apk-starter @@ app.apk @@ -- ARGS    # everything after the APK is passed on to ATL
apk-starter --version
```

Exit codes: 0 ok, 1 error (e.g. cannot write), 2 wrong usage, 3 APK not readable; if the app is ended by a signal,
128+N.

Without the alias use `flatpak run --file-forwarding io.github.Jimbambuli.ApkStarter …` instead of `apk-starter`.
Double-clicking an APK in the file manager and starting an installed app from the menu work without any command.

Data lives under `~/.var/app/io.github.Jimbambuli.ApkStarter/data/`: `apps/` (installed APKs), `data/<package>/`
(app data), `logs/` (logs from `--log`, folders from `--debug`).

Diagnose levels, for finding out why an app misbehaves under ATL (with
[atl-doctor](https://github.com/Jimbambuli/atl-doctor)): a normal start loads nothing extra. **Log** (`--log`,
«Start with log») writes ATL's output plus what atl-doctor's JVMTI agent sees on the fast runtime (lifecycle
callbacks; ART reports thrown exceptions to the agent only while code runs instrumented, so stacks of exceptions
come reliably with Debug); the app runs at almost full speed. **Debug** (`--debug`, «Start with debug») records a folder with ART's method trace
(hit ATL stubs), the view trees and a verbose Android log; the app runs much slower. **Show report** in the window or
`apk-starter --report` turns a recording into a report (crash cause, ATL gaps, exceptions; in English), the same one
`atl-doctor report --log FILE` or `atl-doctor report DIR` gives.

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
over the desktop's GTK theme (`settings.ini` of GTK: theme, icons, cursor, font and the order of the window buttons;
the theme's files; KDE's colour scheme and window buttons in `colors.css` and `window_decorations.css`), for its
window and for the apps' title bars, and to follow a change of them at once:

- `--filesystem=host-os:ro`: the system's files under `/run/host` (read only) – the themes and icons in
  `/usr/share/themes` and `/usr/share/icons`;
- `--filesystem=xdg-config/gtk-4.0:ro` and `--filesystem=xdg-config/gtk-3.0:ro`: your GTK settings (read only);
  KDE Plasma keeps the images of the window buttons only in `gtk-3.0`.

Without them (e.g. `flatpak override --user --nofilesystem=host-os io.github.Jimbambuli.ApkStarter`) everything
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
