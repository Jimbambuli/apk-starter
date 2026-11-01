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

Open **APK Starter** from the application menu (or run `apk-starter` without arguments) to get a small window that
lists the installed apps with their icon, version and data size. From there you can start an app (also with a log
or with debug, see below), show the report of such a start, open the log folder, delete an app's data, uninstall it, and install a new APK with **Install APK…** or by dropping
the file onto the window. Every installed app also gets its own menu entry.

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
«Start with log») writes ATL's output plus, through atl-doctor's JVMTI agent, every thrown exception with its stack;
the app runs at almost full speed. **Debug** (`--debug`, «Start with debug») records a folder with ART's method trace
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
