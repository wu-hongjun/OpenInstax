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
|-- InstantLinkBridgeFirmware-vX.Y.Z-linux-aarch64.manifest.sig   # signed builds only
|-- latest.json
`-- latest.json.sig                                                # signed builds only
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
read `latest.json` through `BridgeFirmwareBundleService`, which requires `latest.json.sig`, the
package `.manifest.sig`, and matching SHA-256 values before returning a bundled package.

Signed release builds add Ed25519 JSON signature sidecars for the package manifest and `latest.json`.
The signature payload is deterministic canonical JSON, not the tarball bytes or shell scripts. The
future macOS updater and Bridge manager must verify these signatures against embedded trusted public
keys before upload or install.

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

Local builds are unsigned by default. To produce signed release assets, provide an Ed25519 private
key path through the environment:

```bash
INSTANTLINK_BRIDGE_FIRMWARE_SIGNING_KEY=/secure/path/bridge-firmware-ed25519.pem \
INSTANTLINK_BRIDGE_FIRMWARE_SIGNING_KEY_ID=bridge-release-2026-05 \
bridge/scripts/build-firmware-bundle.sh 0.1.0
```

`INSTANTLINK_BRIDGE_FIRMWARE_SIGNING_KEY_ID` is optional; if omitted, the signer derives an
`ed25519-sha256:<digest>` key id from the public key. Encrypted PEM keys can be used by setting
`INSTANTLINK_BRIDGE_FIRMWARE_SIGNING_KEY_PASSWORD_ENV` to the name of an environment variable that
contains the password. The private key must live outside the repository and should be injected only
by protected release CI.

Tagged Bridge firmware and app releases require the `BRIDGE_FIRMWARE_SIGNING_KEY_PEM` secret.
`BRIDGE_FIRMWARE_SIGNING_KEY_ID` is optional but recommended so release assets carry a stable key
identifier. Workflow-dispatch development builds may remain unsigned; app-side bundle discovery is
fail-closed and ignores unsigned bundles.

For test keys only:

```bash
bridge/scripts/sign-firmware-manifest.py generate-test-key \
  --private-key /tmp/bridge-firmware-test.pem \
  --public-key /tmp/bridge-firmware-test.pub.pem
```

To reuse already-built Linux arm64 artifacts:

```bash
INSTANTLINK_BRIDGE_BUILD_NATIVE=0 \
INSTANTLINK_BRIDGE_INSTANTLINK_ARTIFACT_DIR=target/aarch64-unknown-linux-gnu/release \
bridge/scripts/build-firmware-bundle.sh 0.1.0
```

## Installation Contract

The package still contains `install-firmware-bundle.sh` for manual developer recovery installs.
The product updater must not run that root shell script. The Bridge manager should install from
declarative package metadata into release slots, with backup and rollback controlled by the manager.

The future app updater should:

1. Verify `latest.json.sig` against a trusted firmware signing public key.
2. Verify the package manifest `.manifest.sig` against the same trust store.
3. Reject unknown key ids, invalid signatures, malformed SHA-256 digests, or artifact names that are
   not clean relative paths/basenames.
4. Verify the archive SHA-256 from `latest.json`.
5. Confirm the live Bridge manager reports `auth`, `backup`, `release_slots`, `rollback`, and
   `health_gates` capabilities.
6. Upload and extract the bundle into a staging directory on the Bridge.
7. Ask the Bridge manager to create a backup, install into a new release slot, restart, and verify.
8. Mark the update good only after service, FTP, LCD, network, and printer-status checks pass.

This is not yet a complete product updater. One-click updates must remain hidden until the Bridge
management API, local authorization, signed package trust chain, automatic backup, and rollback gate
from `docs/plans/029-bridge-control-panel.md` and
`docs/plans/030-bridge-secure-management-updates.md` are implemented.
