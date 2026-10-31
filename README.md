# APK Starter

Run Android apps (APK files) on the Linux desktop – a Flatpak built on
[Android Translation Layer (ATL)](https://gitlab.com/android_translation_layer/android_translation_layer).

```
apk-starter app.apk            # start an app
apk-starter --install app.apk  # add it to the application menu
apk-starter --log app.apk      # optional log (readable by atl-doctor)
```

Bring your own APK – this project ships ATL and its free dependencies only, no apps. Apps that need Google Play
Services, Jetpack Compose or ARM-only native libraries are not expected to work.

**Status:** design phase, no code yet.

License: GPL-3.0-or-later (same as ATL), see [LICENSE](LICENSE). Not affiliated with Google; Android is a trademark
of Google LLC.
