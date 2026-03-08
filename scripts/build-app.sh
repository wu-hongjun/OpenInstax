#!/bin/bash
# Build the OpenInstax macOS app bundle.
#
# Usage: ./scripts/build-app.sh [--release]
#
# This script:
# 1. Builds the Rust CLI binary
# 2. Builds the SwiftUI app
# 3. Copies the CLI into the app bundle

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Parse args
RELEASE=""
CARGO_PROFILE="debug"
if [[ "${1:-}" == "--release" ]]; then
    RELEASE="--release"
    CARGO_PROFILE="release"
fi

echo "==> Building Rust CLI ($CARGO_PROFILE)..."
cd "$PROJECT_DIR"
cargo build $RELEASE -p openinstax-cli

CLI_BINARY="$PROJECT_DIR/target/$CARGO_PROFILE/openinstax"

echo "==> Building SwiftUI app..."
cd "$PROJECT_DIR/macos"
xcodebuild -scheme OpenInstax -configuration "${CARGO_PROFILE^}" build \
    2>/dev/null || echo "Note: Xcode project not yet configured. Skipping SwiftUI build."

# If the app bundle exists, copy the CLI binary into it
APP_BUNDLE="$PROJECT_DIR/macos/build/OpenInstax.app"
if [[ -d "$APP_BUNDLE" ]]; then
    echo "==> Copying CLI into app bundle..."
    cp "$CLI_BINARY" "$APP_BUNDLE/Contents/MacOS/"
    echo "==> Done! App at: $APP_BUNDLE"
else
    echo "==> CLI binary at: $CLI_BINARY"
    echo "    (Xcode project needed for full app bundle)"
fi
