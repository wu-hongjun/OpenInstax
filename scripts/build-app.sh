#!/usr/bin/env bash
#
# build-app.sh — Build InstantLink.app bundle and DMG
#
# Usage: bash scripts/build-app.sh <version>
#   e.g. bash scripts/build-app.sh 0.1.0
#
# Compiles the Rust workspace (release) and SwiftUI launcher, then
# assembles the .app bundle. Produces target/release/InstantLink.app/
# and optionally a DMG.

set -euo pipefail

# Ensure cargo is in PATH (rustup default location)
if [[ -f "$HOME/.cargo/env" ]]; then
  source "$HOME/.cargo/env"
fi

VERSION="${1:?Usage: build-app.sh <version>}"
# Strip leading 'v' if present for plist version strings
PLIST_VERSION="${VERSION#v}"

# Validate semver format (MAJOR.MINOR.PATCH, optional pre-release/build metadata)
if [[ ! "$PLIST_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9.]+)?(\+[a-zA-Z0-9.]+)?$ ]]; then
  echo "Error: Invalid semver version: '$PLIST_VERSION'" >&2
  echo "Expected format: MAJOR.MINOR.PATCH (e.g., 0.1.0, 1.2.3-beta.1)" >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$REPO_ROOT/target/release/InstantLink.app"
CONTENTS="$APP/Contents"
MACOS_DIR="$CONTENTS/MacOS"

echo "==> Building InstantLink.app (version ${PLIST_VERSION})"

# --- Build Rust workspace (release) --------------------------------------
echo "==> Compiling Rust workspace..."
cargo build --workspace --release

# --- Clean previous app bundle -------------------------------------------
rm -rf "$APP"
mkdir -p "$MACOS_DIR"

# --- Copy CLI binary ------------------------------------------------------
CLI_SRC="$REPO_ROOT/target/release/instantlink"
# Rename to instantlink-cli inside the bundle to avoid case-insensitive
# collision with the SwiftUI launcher binary (InstantLink).
cp "$CLI_SRC" "$MACOS_DIR/instantlink-cli"

# --- Bundle FFI dylib into Frameworks/ ------------------------------------
FRAMEWORKS_DIR="$CONTENTS/Frameworks"
mkdir -p "$FRAMEWORKS_DIR"
DYLIB_SRC="$REPO_ROOT/target/release/libinstantlink_ffi.dylib"
cp "$DYLIB_SRC" "$FRAMEWORKS_DIR/"
install_name_tool -id @rpath/libinstantlink_ffi.dylib "$FRAMEWORKS_DIR/libinstantlink_ffi.dylib"

# --- Info.plist -----------------------------------------------------------
sed "s/\${VERSION}/${PLIST_VERSION}/g" \
  "$REPO_ROOT/macos/Info.plist.template" > "$CONTENTS/Info.plist"

# --- PkgInfo --------------------------------------------------------------
printf 'APPL????' > "$CONTENTS/PkgInfo"

# --- Resources ------------------------------------------------------------
RESOURCES_DIR="$CONTENTS/Resources"
mkdir -p "$RESOURCES_DIR"

# Copy app icon if it exists
ICON_SRC="$REPO_ROOT/macos/Resources/AppIcon.icns"
if [[ -f "$ICON_SRC" ]]; then
  cp "$ICON_SRC" "$RESOURCES_DIR/AppIcon.icns"
fi

# Copy localization files
for LPROJ in "$REPO_ROOT/macos/Resources"/*.lproj; do
  if [[ -d "$LPROJ" ]]; then
    cp -R "$LPROJ" "$RESOURCES_DIR/"
  fi
done

# Copy bundled fonts
FONTS_SRC="$REPO_ROOT/macos/Resources/Fonts"
if [[ -d "$FONTS_SRC" ]]; then
  FONTS_DST="$RESOURCES_DIR/Fonts"
  mkdir -p "$FONTS_DST"
  shopt -s nullglob
  TTF_FILES=("$FONTS_SRC"/*.ttf)
  shopt -u nullglob
  if [[ ${#TTF_FILES[@]} -gt 0 ]]; then
    cp "${TTF_FILES[@]}" "$FONTS_DST/"
  fi
fi

# --- Compile SwiftUI launcher (Contents/MacOS/InstantLink) -----------------
echo "==> Compiling SwiftUI launcher..."
SWIFT_SOURCES=()
while IFS= read -r source; do
  SWIFT_SOURCES+=("$source")
done < <(find "$REPO_ROOT/macos/InstantLink" -name '*.swift' | sort)

if [[ ${#SWIFT_SOURCES[@]} -eq 0 ]]; then
  echo "Error: No Swift sources found under macos/InstantLink" >&2
  exit 1
fi

swiftc \
  -target arm64-apple-macosx13.0 \
  -O \
  -o "$MACOS_DIR/InstantLink" \
  "${SWIFT_SOURCES[@]}" \
  -framework SwiftUI \
  -framework AppKit \
  -framework UniformTypeIdentifiers \
  -framework AVFoundation \
  -framework CoreText \
  -framework CoreImage \
  -Xlinker -rpath -Xlinker @executable_path/../Frameworks \
  -parse-as-library

# --- Ad-hoc codesign (prevents "damaged" Gatekeeper error) ----------------
echo "==> Ad-hoc signing app bundle..."
codesign --force --deep -s - "$APP"

echo "==> App bundle created at: $APP"

# --- Build DMG (if create-dmg is available) -------------------------------
if command -v create-dmg &>/dev/null; then
  TAG="v${PLIST_VERSION}"
  DMG_NAME="InstantLink-${TAG}-aarch64-apple-darwin.dmg"
  DMG_PATH="$REPO_ROOT/$DMG_NAME"

  echo "==> Building DMG: $DMG_NAME"

  DMG_STAGE="$REPO_ROOT/target/release/dmg-stage"
  rm -rf "$DMG_STAGE"
  mkdir -p "$DMG_STAGE"
  cp -R "$APP" "$DMG_STAGE/"

  # create-dmg fails if target exists
  rm -f "$DMG_PATH"

  create-dmg \
    --volname "InstantLink ${TAG}" \
    --window-size 500 340 \
    --icon-size 80 \
    --app-drop-link 350 120 \
    --icon "InstantLink.app" 150 120 \
    --no-internet-enable \
    "$DMG_PATH" \
    "$DMG_STAGE"

  rm -rf "$DMG_STAGE"

  echo "==> DMG created at: $DMG_PATH"
else
  echo "==> Skipping DMG (install create-dmg: brew install create-dmg)"
fi
