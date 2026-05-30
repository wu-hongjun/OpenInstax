#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD_DIR="$REPO_ROOT/target/macos-tests"
MODULE_CACHE="${TMPDIR:-/tmp}/instantlink-swift-test-module-cache"
SDK_PATH="$(xcrun --sdk macosx --show-sdk-path)"
PLATFORM_PATH="$(xcrun --sdk macosx --show-sdk-platform-path)"
PLATFORM_FRAMEWORKS="$PLATFORM_PATH/Developer/Library/Frameworks"
TEST_BIN="$BUILD_DIR/InstantLinkMacTests"

mkdir -p "$BUILD_DIR" "$MODULE_CACHE"

SOURCES=(
  "$REPO_ROOT/macos/InstantLink/Localization.swift"
  "$REPO_ROOT/macos/InstantLink/OverlayModels.swift"
  "$REPO_ROOT/macos/InstantLink/InstantLinkFFI.swift"
  "$REPO_ROOT/macos/InstantLink/Core/AppModels.swift"
  "$REPO_ROOT/macos/InstantLink/Core/BridgeFirmwareBundle.swift"
  "$REPO_ROOT/macos/InstantLink/Core/BridgeConfig.swift"
  "$REPO_ROOT/macos/InstantLink/Core/BridgeModels.swift"
  "$REPO_ROOT/macos/InstantLink/Core/BridgeAuth.swift"
  "$REPO_ROOT/macos/InstantLink/Core/BridgeKeychain.swift"
  "$REPO_ROOT/macos/InstantLink/Core/BridgeTransport.swift"
  "$REPO_ROOT/macos/InstantLink/Core/BridgeHTTPTransport.swift"
  "$REPO_ROOT/macos/InstantLink/Core/BridgeControlCoordinator.swift"
  "$REPO_ROOT/macos/InstantLink/Core/BridgeSettingsDraft.swift"
  "$REPO_ROOT/macos/InstantLink/Core/AppRuntimeServices.swift"
  "$REPO_ROOT/macos/InstantLink/Core/QueueEditCoordinator.swift"
  "$REPO_ROOT/macos/InstantLink/Core/PrinterConnectionCoordinator.swift"
  "$REPO_ROOT/macos/InstantLink/Features/Bridge/BridgeConnectionIndicator.swift"
  "$REPO_ROOT/macos/InstantLink/Features/Bridge/BridgeDiscoveryBanner.swift"
  "$REPO_ROOT/macos/InstantLink/Features/Bridge/BridgeOverviewView.swift"
  "$REPO_ROOT/macos/InstantLink/Features/Bridge/BridgePairingView.swift"
  "$REPO_ROOT/macos/InstantLink/Features/Bridge/BridgeSettingsSection.swift"
  "$REPO_ROOT/macos/InstantLink/Features/Bridge/BridgeSettingsView.swift"
  "$REPO_ROOT/macos/InstantLink/Features/Bridge/BridgeControlWindow.swift"
)

TESTS=(
  "$REPO_ROOT/macos/Tests/TestSupport.swift"
  "$REPO_ROOT/macos/Tests/AppModelsTests.swift"
  "$REPO_ROOT/macos/Tests/BridgeFirmwareBundleTests.swift"
  "$REPO_ROOT/macos/Tests/BridgeModelsTests.swift"
  "$REPO_ROOT/macos/Tests/BridgeHTTPTransportTests.swift"
  "$REPO_ROOT/macos/Tests/BridgeTransportTests.swift"
  "$REPO_ROOT/macos/Tests/BridgeKeychainTests.swift"
  "$REPO_ROOT/macos/Tests/BridgePairingViewModelTests.swift"
  "$REPO_ROOT/macos/Tests/BridgeControlCoordinatorTests.swift"
  "$REPO_ROOT/macos/Tests/BridgeConfigTests.swift"
  "$REPO_ROOT/macos/Tests/BridgeSettingsDraftTests.swift"
  "$REPO_ROOT/macos/Tests/BridgeSettingsApplyFlowTests.swift"
  "$REPO_ROOT/macos/Tests/AppRuntimeServicesTests.swift"
  "$REPO_ROOT/macos/Tests/QueueEditCoordinatorTests.swift"
  "$REPO_ROOT/macos/Tests/PrinterConnectionCoordinatorTests.swift"
  "$REPO_ROOT/macos/Tests/TestMain.swift"
)

swiftc \
  -sdk "$SDK_PATH" \
  -target arm64-apple-macosx15.0 \
  -module-cache-path "$MODULE_CACHE" \
  -O \
  -F "$PLATFORM_FRAMEWORKS" \
  -o "$TEST_BIN" \
  "${SOURCES[@]}" \
  "${TESTS[@]}" \
  -framework AppKit \
  -framework SwiftUI \
  -framework Security \
  -framework CoreText

"$TEST_BIN"
