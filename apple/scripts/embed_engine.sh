#!/bin/bash
# Xcode build phase: copy the PyInstaller engine (dist/PitWall) into
# PitWall.app/Contents/Resources/PitWall. Release builds must have it;
# Debug builds without one fall back to the source checkout (Engine.swift).
set -euo pipefail
ENGINE="$SRCROOT/../dist/PitWall"
DEST="$TARGET_BUILD_DIR/$UNLOCALIZED_RESOURCES_FOLDER_PATH/PitWall"

if [ ! -x "$ENGINE/PitWall" ]; then
  if [ "$CONFIGURATION" = "Release" ]; then
    echo "error: $ENGINE/PitWall is missing — build it first:"
    echo "error:   build-venv/bin/python -m PyInstaller --noconfirm --clean PitWall.spec"
    exit 1
  fi
  echo "note: no frozen engine in dist/ — this Debug build runs the source checkout"
  rm -rf "$DEST"
  exit 0
fi

# Debug builds run the source checkout even when a frozen engine exists,
# so code changes show up without re-running PyInstaller.
if [ "$CONFIGURATION" != "Release" ]; then
  rm -rf "$DEST"
  exit 0
fi

mkdir -p "$DEST"
rsync -a --delete "$ENGINE/" "$DEST/"

# Notarization needs every Mach-O inside the bundle signed with the
# hardened runtime by the same identity as the app. Ad-hoc ("-") builds
# keep PyInstaller's own ad-hoc signatures, which is enough to run locally.
IDENTITY="${EXPANDED_CODE_SIGN_IDENTITY:-}"
if [ -n "$IDENTITY" ] && [ "$IDENTITY" != "-" ]; then
  echo "signing engine with $IDENTITY"
  ENT="$SRCROOT/PitWallMac/Engine.entitlements"
  # Inner files first, then the framework bundles that contain them.
  find "$DEST" -type f -print0 | while IFS= read -r -d '' f; do
    if file -b "$f" | grep -q "Mach-O"; then
      codesign --force --timestamp --options runtime --entitlements "$ENT" -s "$IDENTITY" "$f"
    fi
  done
  find "$DEST" -type d -name "*.framework" -print0 | while IFS= read -r -d '' fw; do
    codesign --force --timestamp --options runtime -s "$IDENTITY" "$fw"
  done
fi
