# APK Starter

Run Android apps (APK files) on the Linux desktop – a Flatpak built on
[Android Translation Layer (ATL)](https://gitlab.com/android_translation_layer/android_translation_layer).

Works: JW Library, Jetpack Compose apps, Aurora Store, microG (Google sign-in for apps), most free apps from F-Droid.
Limits: x86_64 APKs only, no ARM-only apps; camera, Bluetooth and telephony are not supported.

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
Install a new APK with **+** or by dropping it anywhere onto the window; while no app is installed, the window
offers **Get apps** (Aurora Store) and **Open APK…** as big buttons. The window takes the desktop's GTK theme,
colour scheme and accent colour and keeps the desktop's own title bar (plain GTK 4); it remembers its size, the sort
order and the chosen app. Every installed app also gets its own menu entry.

Next to **Start** stand **Delete data …**, **Uninstall …** and **⚙**. The ⚙ menu holds each app's own **Window bar**
(remembered): **With back button** (the default) is a title
bar drawn in the desktop's GTK theme with a back button; under KDE it gets the buttons and the height of the KWin
decoration. **From the system** is the desktop's own title bar (where the compositor draws one, e.g. KDE Plasma),
without the back button; **From the system + navigation bar** adds Android's navigation bar under the window (◁ back,
○ the app's first page, □ its open pages). Escape and the back button of the mouse always go back. **Debug** and
**Debug full** (also remembered per app, until you untick them; one at a time) record every start of the app, also
from its menu entry; a dot on ⚙ shows that one is on. The archive of **Debug** is cleaned of personal data;
**Debug full** keeps everything in plain text, also passwords (a tooltip says so): do not pass that archive on. The
menu lists the recordings and offers **Open log folder**. A normal start writes no log.
When an app crashes, a message says what its exit code means; **Open log folder** shows the recording of that start.

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
apk-starter --debug @@ app.apk @@    # record the app for atl-doctor, --debug=DIR for a folder
apk-starter --debug-full @@ app.apk @@  # likewise, the archive keeps everything in plain text (do not pass it on)
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

**+ → Get apps (Aurora Store)** (or `--setup-aurora`) downloads the official Aurora Store from F-Droid, checks its APK
signature against Aurora OSS's certificate, installs it like any app and starts it; once it is installed, the entry
just starts it. Aurora Store is not part of the Flatpak.
When Aurora Store (or another store using Android's PackageInstaller) installs an app, ATL hands its APKs to APK
Starter, which asks **Install …?** and installs it with a menu entry; the store then sees it as installed. Split APKs
from Google Play (base and configuration parts) are kept together; apps with native code only for ARM are refused
with a message. Sign in anonymously in Aurora Store: signing in with your own Google account may break Google's terms
of service.

### microG (Google services for apps)

**+ → Set up microG…** (or `--setup-microg`) works like Aurora Store: it downloads GmsCore and GsfProxy from the
official microG repository (microg.org/fdroid), checks the SHA-256 checksums and GmsCore's APK signature against the
NOGAPPS Project's certificate, and installs GmsCore as the app «microG settings»; GsfProxy goes into the app folder
for ATL. microG is not part of the Flatpak. Once it is installed, the entry disappears from the menu. When a newly
installed app uses Google Play Services (its manifest has the meta-data `com.google.android.gms.version`) and microG
is missing, APK Starter asks once whether to set it up. Every app then shares microG's data. In «microG settings» switch on «Google device registration» and «Cloud
Messaging»; to sign in: Accounts → Add Google account.

Without the alias use `flatpak run --file-forwarding io.github.Jimbambuli.ApkStarter …` instead of `apk-starter`.
Double-clicking an APK in the file manager and starting an installed app from the menu work without any command.

Data lives under `~/.var/app/io.github.Jimbambuli.ApkStarter/data/`: `apps/` (installed APKs), `data/<package>/`
(app data), `logs/` (the archives of Debug).

Recordings, for finding out why an app misbehaves under ATL (with
[atl-doctor](https://github.com/Jimbambuli/atl-doctor)): a normal start writes nothing. **Debug** (`--debug`,
«Debug» ticked) records the agent attached as soon as the runtime runs (exceptions with stack, also caught ones,
lifecycle), the calls of ATL's stub methods (counted by ATL itself), a time line from ART's sampling profiler, the
view trees (no screenshots: none is made), what is clicked, typed (as a length
only) and scrolled (`input.jsonl`) and a verbose Android log; the app runs without a full method trace (it
made apps lose their own start-up races). When the app has ended, `atl-doctor pack` makes one archive
`<package>-<date>-<time>.tar.gz` of it, cleaned of paths, user and host name, network addresses, tokens, passwords
and e-mail addresses (`SANITIZED.txt` in it says what was done). **Debug full**
(`--debug-full`) makes `<package>-<date>-<time>-FULL.tar.gz` with everything as recorded and, only there, an image of
the app window taken ten seconds after the start (ATL itself renders the window; no screen capture, no portal, nothing
of other windows; the image shows what the app shows, also typed text). If packing fails, the folder
stays and a message says why. Of the recordings the last 5 per app stay, and together at most 200 MB per app (the
older go first, the newest always stays).

APK Starter only records and packs; [atl-doctor](https://github.com/Jimbambuli/atl-doctor) evaluates the recordings
(crash cause, ATL gaps, exceptions; it compares the stub hits with Android's source so that methods as trivial in
Android are not counted as gaps). Install it once with `pipx install git+https://github.com/Jimbambuli/atl-doctor`, then:

```
cd ~/.var/app/io.github.Jimbambuli.ApkStarter/data/logs
atl-doctor report org.example.app-20261102-101010.tar.gz          # a Debug archive
atl-doctor report -f html -o report.html org.example.app-20261102-101010.tar.gz   # as a page, e.g. for a bug report
```

Like Android, installing compiles an app for your processor (shown as «Preparing …»), so its first start is as
fast as later ones; the Android runtime itself is prepared once and again after an update of APK Starter. Opening
an APK file (for example with a double click) installs it the first time and again when it is another version; otherwise the installed app starts. The
compiled code lives in `~/.var/app/io.github.Jimbambuli.ApkStarter/cache/art/`; deleting it is safe, it is made
again.

Uninstalling: run `apk-starter --remove-all` before `flatpak uninstall io.github.Jimbambuli.ApkStarter`.
`flatpak uninstall` does not remove the menu entries of the installed apps; desktops hide them once APK Starter is
gone, but the files stay in `~/.local/share/xdg-desktop-portal/` (and come back if you install APK Starter again).

Bring your own APK – this project ships ATL and its free dependencies only, no apps. Only apps with x86_64 native libraries (or none) run;
ARM-only apps are refused. Apps that use Google Play Services need microG (see above).

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

To open a file with an app («Open with» in the file manager), APK Starter reads – never writes – the standard
folders Downloads, Pictures, Documents, Music and Videos (`--filesystem=xdg-download:ro` etc.); a file elsewhere can
be dropped onto the app. Apps save and export through the desktop's file dialog (portal), which writes only where you
choose.

The apps see Pictures, Music, Videos and Downloads the way Android apps see the phone's shared storage: a gallery or
music player lists your pictures, songs and videos (Android's MediaStore), read only. Apps cannot write there: a
picture an app saves through MediaStore lands in the app's data folder, saving music, videos or downloads that way
fails (the file dialog saves where you choose). Documents is not listed.

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

**Status:** preview 0.8 (own Flatpak repository, not on Flathub yet).

## Credits and license

License: GPL-3.0-or-later (same as ATL), see [LICENSE](LICENSE). Built from: ATL (GPL-3.0), art_standalone
(Apache-2.0), bionic_translation, wolfSSL (GPL). Aurora Store and microG are not shipped; on request they are
downloaded from their official sources and checked. Not affiliated with Google; Android is a trademark
of Google LLC.

The app icon (`data/icons/io.github.Jimbambuli.ApkStarter.svg`) is licensed under
[CC BY 3.0](https://creativecommons.org/licenses/by/3.0/). The Android robot is reproduced or modified from work
created and shared by Google and used according to terms described in the Creative Commons 3.0 Attribution License.
The letters "APK" in the icon are outlines of DejaVu Sans Bold (Bitstream Vera/DejaVu font license).
