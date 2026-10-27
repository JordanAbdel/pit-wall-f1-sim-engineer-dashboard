#!/bin/bash
# Build dist/PitWall.dmg: the PyInstaller engine wrapped in a stay-open
# launcher .app with the PitWall icon. Run from anywhere after
#   build-venv/bin/python -m PyInstaller --noconfirm --clean PitWall.spec
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
OUT="$ROOT/dist/dmg"
APP="$OUT/PitWall.app"
[ -x "$ROOT/dist/PitWall/PitWall" ] || { echo "run PyInstaller first"; exit 1; }

rm -rf "$OUT" "$ROOT/dist/PitWall.dmg"
mkdir -p "$OUT"
osacompile -s -o "$APP" "$HERE/PitWall-dist.applescript"
cp "$HERE/PitWall.icns" "$APP/Contents/Resources/applet.icns"
rm -f "$APP/Contents/Resources/Assets.car"
PL="$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Delete :CFBundleIconName" "$PL" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier com.jordanabdel.pitwall" "$PL" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Add :CFBundleIdentifier string com.jordanabdel.pitwall" "$PL"
/usr/libexec/PlistBuddy -c "Add :CFBundleName string PitWall" "$PL" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :LSUIElement bool false" "$PL" 2>/dev/null || true
ditto "$ROOT/dist/PitWall" "$APP/Contents/Resources/PitWall"
codesign --force --deep -s - "$APP"
ln -s /Applications "$OUT/Applications"
hdiutil create -volname PitWall -srcfolder "$OUT" -fs HFS+ -format UDZO -ov "$ROOT/dist/PitWall.dmg"
echo "built $ROOT/dist/PitWall.dmg"
