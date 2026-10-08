# Releasing Plip

## Build the DMG on your Mac

```bash
.venv/bin/python scripts/build_dmg.py
```

You get `dist/Plip.app` and `dist/Plip-<version>.dmg`: a self-contained app (uv's
CPython 3.12, Plip and its dependencies inside, a small launcher, hardened runtime,
every binary signed) on a drag-to-Applications disk image. It needs an Apple-silicon
Mac, the Xcode command line tools and `uv`. The DMG window's background comes from
`cd ui && npm run brand`.

With `PLIP_SUPABASE_URL` and `PLIP_SUPABASE_KEY` set (your environment or `~/.config/mcp-vision/.env`), the app
asks people to sign in with Google first; it prints which project it baked in.

Without your Apple certificate it is signed **ad hoc**: the first time people open it,
macOS says it can't check Plip, and they go to System Settings → Privacy & Security →
**Open Anyway**. The README and the DMG window both say so.

## Ship a release

1. Bump the version in `pyproject.toml`, `src/mcp_vision/__init__.py` and `ui/package.json`, then run
   `uv lock` (the DMG installs the versions in `uv.lock` and stops if it's out of date).
2. Commit, then `git tag v0.8.0 && git push origin v0.8.0`.
3. GitHub Actions (`.github/workflows/release.yml`) builds the DMG on a Mac and attaches it to
   the release. The README links to `releases/latest`, so the download link never changes.
4. Everyone on a Plip that has the update check (`src/mcp_vision/buddy/updates.py`, after 0.8.0) hears about it
   within a day: the notch says so once, and the menu bar, Home and Settings → General offer **Download**, which
   opens the new DMG. It reads GitHub's latest release, so drafts and pre-releases (`v1.0-rc1`) never show up,
   and a release without a DMG yet points at its page. Copies from 0.8.0 or before don't check, so tell those
   people another way once.

## Verified by Apple (no warning)

Apple trusts an app that's signed with a **Developer ID** certificate and then **notarized**.
That needs the Apple Developer Program. The app's bundle ID is `dev.plip.oss`
(`src/mcp_vision/identity.py`); you don't need to register it anywhere for a DMG.

One-time setup:

1. **Certificate.** Xcode → Settings → Accounts → sign in with your developer Apple ID →
   your team → Manage Certificates → **+** → *Developer ID Application*. Check it:
   `security find-identity -v -p codesigning` shows `Developer ID Application: Your Name (TEAMID)`.
2. **App-specific password.** <https://account.apple.com> → Sign-In and Security →
   App-Specific Passwords → make one called "notary".
3. **Notary login, saved in your keychain:**
   ```bash
   xcrun notarytool store-credentials plip --apple-id you@example.com --team-id TEAMID --password abcd-efgh-ijkl-mnop
   ```

Then build a release:

```bash
.venv/bin/python scripts/build_dmg.py --sign "Developer ID Application: Your Name (TEAMID)" --notarize plip
```

It checks the certificate and the profile before building and tells you what's missing. Then it
signs every binary with your Developer ID, notarizes and staples the app, signs, notarizes and
staples the DMG, and asks Gatekeeper about both (`accepted`, `source=Notarized Developer ID`).
Apple usually answers in 2–10 minutes. If it says no, the script prints Apple's log.

### Every tag notarized by GitHub Actions

Add these repository secrets (Settings → Secrets and variables → Actions):

| Secret | Value |
|---|---|
| `APPLE_CERT_P12` | Keychain Access → export the Developer ID cert as .p12 → `base64 -i cert.p12 \| pbcopy` |
| `APPLE_CERT_PASSWORD` | the password you set on that .p12 |
| `APPLE_SIGN_ID` | `Developer ID Application: Your Name (TEAMID)` |
| `APPLE_ID` | your Apple ID email |
| `APPLE_TEAM_ID` | your 10-character team ID |
| `APPLE_APP_PASSWORD` | the app-specific password from step 2 |
| `PLIP_SUPABASE_URL` | the sign-in project, `https://<project>.supabase.co` ([CONTRIBUTING.md](../CONTRIBUTING.md#sign-in)) |
| `PLIP_SUPABASE_KEY` | its **publishable** key (`sb_publishable_…`); the build refuses a secret one |

Without the two Supabase secrets the DMG doesn't ask anyone to sign in.

Without the Apple secrets a tag stops instead of publishing an unsigned DMG. Run the workflow by hand
(Actions → release dmg → Run workflow) for an ad hoc test build; its DMG is kept on the run's page.
