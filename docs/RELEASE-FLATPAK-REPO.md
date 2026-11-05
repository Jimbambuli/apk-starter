# Flatpak repository on GitHub Pages

`.github/workflows/build.yml` does this on a tag `v*` (nothing runs without a tag):
tests -> flatpak build -> smoke test -> job `publish`: sign the commit and the summary with GPG,
`flatpak build-update-repo` (static deltas), then the site is deployed to GitHub Pages:
`/repo/` (ostree), `apk-starter.flatpakrepo`, `io.github.Jimbambuli.ApkStarter.flatpakref`, `apk-starter.gpg`.

## One-time setup (by hand, the key is never in the repository)

1. Create a signing key on your own machine (no passphrase, or one you accept as a secret):
   `gpg --quick-generate-key "<key name>" ed25519 sign 3y`
2. Repository Settings > Secrets and variables > Actions:
   - secret `FLATPAK_GPG_PRIVATE_KEY` = `gpg --armor --export-secret-keys <KEYID>`
   - variable `FLATPAK_GPG_KEY_ID` = the key ID
3. Settings > Pages > Source: GitHub Actions.
4. Put the public key into `pages/apk-starter.gpg` (`gpg --export <KEYID>`) and its base64 (one line, no
   breaks) as `GPGKey=` into both `pages/*.flatpakrepo` and `pages/*.flatpakref`.
5. Release: set the date in the metainfo, `git tag v0.1.0 && git push origin v0.1.0`.

Check: `flatpak install --user <url of the flatpakref>` on a clean user.
Key change: replace the public key in the three files; users must re-add the remote.
