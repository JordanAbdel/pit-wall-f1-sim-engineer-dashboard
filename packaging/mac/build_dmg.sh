#!/bin/bash
# Build dist/PitWall.dmg: the native PitWall.app (apple/) with the
# PyInstaller engine inside it. Run from anywhere after
#   build-venv/bin/python -m PyInstaller --noconfirm --clean PitWall.spec
#
# Signing: unset = ad-hoc (runs on this Mac; other Macs need right-click →
# Open). For a download that opens cleanly anywhere, pass your Developer ID:
#   TEAM_ID=XXXXXXXXXX SIGN_IDENTITY="Developer ID Application: Name (XXXXXXXXXX)" \
#     packaging/mac/build_dmg.sh
# then notarize the DMG (see apple/README.md).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
OUT="$ROOT/dist/dmg"
APP="$OUT/PitWall.app"
DD="$ROOT/apple/build/dd"
[ -x "$ROOT/dist/PitWall/PitWall" ] || { echo "run PyInstaller first"; exit 1; }

SIGN=(CODE_SIGN_IDENTITY=-)
if [ -n "${SIGN_IDENTITY:-}" ]; then
  SIGN=(CODE_SIGN_STYLE=Manual "CODE_SIGN_IDENTITY=$SIGN_IDENTITY"
        "DEVELOPMENT_TEAM=${TEAM_ID:?set TEAM_ID with SIGN_IDENTITY}"
        "OTHER_CODE_SIGN_FLAGS=--timestamp")
fi
xcodebuild -quiet -project "$ROOT/apple/PitWall.xcodeproj" -scheme PitWall \
  -configuration Release -derivedDataPath "$DD" "${SIGN[@]}" build

rm -rf "$OUT" "$ROOT/dist/PitWall.dmg"
mkdir -p "$OUT"
ditto "$DD/Build/Products/Release/PitWall.app" "$APP"
codesign --verify --deep --strict "$APP"
ln -s /Applications "$OUT/Applications"
hdiutil create -volname PitWall -srcfolder "$OUT" -fs HFS+ -format UDZO -ov "$ROOT/dist/PitWall.dmg"
echo "built $ROOT/dist/PitWall.dmg"
