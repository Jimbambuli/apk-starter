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

```
apk-starter @@ app.apk @@            # start an app (@@ lets Flatpak pass the file into the sandbox)
apk-starter --install @@ app.apk @@  # add it to the application menu (asks via the desktop portal)
apk-starter --list                   # installed apps
apk-starter --remove PACKAGE [--purge]
apk-starter --log @@ app.apk @@      # write a log that atl-doctor can read
apk-starter --log=FILE @@ app.apk @@ # same, to a chosen file (must be writable inside the sandbox,
                                     # e.g. under ~/.var/app/io.github.Jimbambuli.ApkStarter/)
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
(app data), `logs/` (logs from `--log`).

Bring your own APK – this project ships ATL and its free dependencies only, no apps. Apps that need Google Play
Services, Jetpack Compose or ARM-only native libraries are not expected to work.

**Status:** pre-release, not yet published.

License: GPL-3.0-or-later (same as ATL), see [LICENSE](LICENSE). Not affiliated with Google; Android is a trademark
of Google LLC.
