# Bridge Firmware Release Pipeline

InstantLink Bridge firmware is packaged as a versioned update bundle for Raspberry Pi OS arm64.
The bundle is designed to be published by GitHub Actions, embedded in the macOS app, and installed
by the future Bridge control panel after management auth, preflight, backup, and rollback are wired.

## Version Tags

- App releases use `vMAJOR.MINOR.PATCH` and build a same-version Bridge firmware bundle into
  `InstantLink.app/Contents/Resources/BridgeFirmware`.
- Bridge-only releases use `bridge-vMAJOR.MINOR.PATCH` and publish only the Bridge firmware assets.
- Workflow dispatch also accepts `0.1.0`, `v0.1.0`, or `bridge-v0.1.0`; the package normalizes all
  three to Bridge version `0.1.0`.

## Bundle Contents

`bridge/scripts/build-firmware-bundle.sh <version>` creates:

```text
target/bridge-firmware/dist/
|-- InstantLinkBridgeFirmware-vX.Y.Z-linux-aarch64.tar.gz
|-- InstantLinkBridgeFirmware-vX.Y.Z-linux-aarch64.tar.gz.sha256
|-- InstantLinkBridgeFirmware-vX.Y.Z-linux-aarch64.manifest.json
`-- latest.json
```

Inside the tarball:

```text
bridge/                         # Python runtime, configs, systemd, udev, scripts, docs
native/bin/instantlink           # Linux arm64 InstantLink CLI
native/lib/libinstantlink_ffi.so # Linux arm64 FFI backend
native/instantlink-artifacts-manifest.json
install-firmware-bundle.sh       # Pi-side installer
manifest.json                    # Package manifest
SHA256SUMS                       # In-bundle file checksums
```

The macOS app build copies the staged `BridgeFirmware` directory into app resources. App code can
read `latest.json` through `BridgeFirmwareBundleService`.

## CI Workflows

- `.github/workflows/bridge-firmware.yml` runs on `bridge-v*` tags and manual dispatch. It builds
  Linux arm64 native artifacts with `cargo zigbuild`, creates the firmware bundle, uploads it as a
  workflow artifact, and publishes it on the tag release.
- `.github/workflows/release.yml` runs on app `v*` tags. It builds the same-version Bridge firmware
  bundle before `scripts/build-app.sh`, embeds it in the app resources, and uploads the firmware
  assets beside the DMG, CLI zip, and FFI zip.

## Local Build

Install `zig` and `cargo-zigbuild`, then run:

```bash
cargo install cargo-zigbuild --locked
bridge/scripts/build-firmware-bundle.sh 0.1.0
```

To reuse already-built Linux arm64 artifacts:

```bash
INSTANTLINK_BRIDGE_BUILD_NATIVE=0 \
INSTANTLINK_BRIDGE_INSTANTLINK_ARTIFACT_DIR=target/aarch64-unknown-linux-gnu/release \
bridge/scripts/build-firmware-bundle.sh 0.1.0
```

## Installation Contract

The package contains `install-firmware-bundle.sh` for the Pi. The future app updater should:

1. Verify the archive SHA-256 from `latest.json`.
2. Upload and extract the bundle into a staging directory on the Bridge.
3. Run `install-firmware-bundle.sh <bundle-dir>` as the Bridge update helper.
4. Restart and verify `instantlink-bridge.service`.
5. Mark the update good only after service, FTP, LCD, network, and printer-status checks pass.

This is not yet a complete product updater. One-click updates must remain hidden until the Bridge
management API, local authorization, signed package trust chain, automatic backup, and rollback gate
from `docs/plans/029-bridge-control-panel.md` are implemented.
