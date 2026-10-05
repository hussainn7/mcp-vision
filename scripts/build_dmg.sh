#!/usr/bin/env bash
# Build dist/Plip-<version>.dmg: a self-contained Plip.app (no Python needed) on a drag-to-Applications disk image.
#
#   scripts/build_dmg.sh                 # ad-hoc signed (testers right-click > Open the first time)
#   SIGN_ID="Developer ID Application: Your Name (TEAMID)" NOTARY_PROFILE=plip scripts/build_dmg.sh
#                                        # signed + notarized + stapled: opens with no warnings
#
# See docs/RELEASING.md for the one-time Apple setup.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$PWD
PY=${PY:-$ROOT/.venv/bin/python}
VERSION=$("$PY" -c 'import mcp_vision; print(mcp_vision.__version__)')
SIGN_ID=${SIGN_ID:--}
DMG=dist/Plip-$VERSION.dmg

echo "==> Settings UI"
(cd ui && npm ci --no-audit --no-fund >/dev/null && npm run -s build)

echo "==> Icon"
mkdir -p build
"$PY" - <<'PY'
from PIL import Image
Image.open("assets/plip-icon.png").save("build/Plip.icns", format="ICNS")
PY

echo "==> Plip.app"
rm -rf build/plip dist/Plip dist/Plip.app
"$PY" -m PyInstaller --noconfirm --clean --distpath dist --workpath build/plip packaging/plip.spec

echo "==> Sign ($SIGN_ID)"
APP=dist/Plip.app
if [ "$SIGN_ID" = "-" ]; then
  codesign --force --deep --sign - "$APP"
else
  # Inside-out: every nested binary, then the app, all with the hardened runtime and a timestamp.
  find "$APP/Contents" -type f \( -name '*.so' -o -name '*.dylib' -o -perm -111 \) -print0 |
    xargs -0 codesign --force --timestamp --options runtime --entitlements packaging/entitlements.plist --sign "$SIGN_ID"
  codesign --force --timestamp --options runtime --entitlements packaging/entitlements.plist --sign "$SIGN_ID" "$APP"
fi
codesign --verify --deep --strict "$APP"

echo "==> $DMG"
STAGE=build/dmg
rm -rf "$STAGE" "$DMG" && mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -volname "Plip" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null
[ "$SIGN_ID" = "-" ] || codesign --force --timestamp --sign "$SIGN_ID" "$DMG"

if [ -n "${NOTARY_PROFILE:-}" ]; then
  echo "==> Notarize (a few minutes)"
  xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait
  xcrun stapler staple "$DMG"
  spctl --assess --type open --context context:primary-signature -v "$DMG"
fi
shasum -a 256 "$DMG"
echo "Done: $DMG"
