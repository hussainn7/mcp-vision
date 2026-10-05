# Releasing Plip

## Build a DMG on your Mac

```bash
scripts/build_dmg.sh
```

You get `dist/Plip-<version>.dmg`: a self-contained `Plip.app` (Python included) on a
drag-to-Applications disk image. Without an Apple certificate it is **ad-hoc signed**,
so the first time people open it macOS says it "can't be checked for malicious software".
They right-click Plip → **Open** → **Open** once (or System Settings → Privacy & Security →
**Open Anyway**). The README tells them this.

## Ship a release

1. Bump the version in `pyproject.toml`, `src/mcp_vision/__init__.py` and `ui/package.json`.
2. Commit, then `git tag v0.7.0 && git push --tags`.
3. GitHub Actions (`.github/workflows/release.yml`) builds the DMG and attaches it to the release.
   The README links to `releases/latest`, so the download link never changes.

## Verify with Apple (signing + notarization), so it opens with no warnings

Apple only trusts apps signed with a **Developer ID** certificate and then **notarized**
(scanned by Apple). This needs the Apple Developer Program: $99/year at
<https://developer.apple.com/programs/enroll/>. One-time setup:

1. **Certificate.** Xcode → Settings → Accounts → your Apple ID → Manage Certificates → **+** →
   *Developer ID Application*. Check it: `security find-identity -v -p codesigning` shows
   `Developer ID Application: Your Name (TEAMID)`.
2. **App-specific password.** <https://account.apple.com> → Sign-In and Security →
   App-Specific Passwords → generate one named "notary".
3. **Save notary credentials in your keychain** (once):
   ```bash
   xcrun notarytool store-credentials plip --apple-id you@example.com --team-id TEAMID --password abcd-efgh-ijkl-mnop
   ```
4. **Build signed + notarized:**
   ```bash
   SIGN_ID="Developer ID Application: Your Name (TEAMID)" NOTARY_PROFILE=plip scripts/build_dmg.sh
   ```
   The script signs every binary with the hardened runtime, submits to Apple, waits
   (usually 2–10 minutes), staples the ticket, and runs `spctl` to confirm macOS accepts it.

Check any DMG yourself:

```bash
spctl --assess --type open --context context:primary-signature -v dist/Plip-0.7.0.dmg
xcrun stapler validate dist/Plip-0.7.0.dmg
```

`accepted` + `source=Notarized Developer ID` means people can double-click with no warning.

### Notarize in GitHub Actions instead

Add these repository secrets (Settings → Secrets and variables → Actions) and every tag
release comes out notarized:

| Secret | Value |
|---|---|
| `APPLE_CERT_P12` | Keychain Access → export the Developer ID cert as .p12 → `base64 -i cert.p12 \| pbcopy` |
| `APPLE_CERT_PASSWORD` | the password you set on that .p12 |
| `APPLE_SIGN_ID` | `Developer ID Application: Your Name (TEAMID)` |
| `APPLE_ID` | your Apple ID email |
| `APPLE_TEAM_ID` | your 10-character team ID |
| `APPLE_APP_PASSWORD` | the app-specific password from step 2 |

If a notarization fails, see why with `xcrun notarytool log <submission-id> --keychain-profile plip`.
