# Installation

## From Releases

Prebuilt artifacts are published on the [Releases](https://github.com/wu-hongjun/InstantLink/releases) page for each tagged version:

- `InstantLink-vX.Y.Z-aarch64-apple-darwin.dmg` for the macOS app
- `InstantLink-CLI-vX.Y.Z.zip` for the standalone CLI
- `InstantLink-FFI-vX.Y.Z.zip` for FFI consumers

The app release targets **macOS 15+ on Apple Silicon**. Download the `.dmg`, mount it, and drag `InstantLink.app` into `/Applications`.

## From Source

### Prerequisites

- [Rust](https://rustup.rs/) stable with edition `2024`
- macOS 15+ for the SwiftUI app and BLE via CoreBluetooth
- Linux is supported for CLI development and BLE via BlueZ

### Build the Workspace

```bash
git clone https://github.com/wu-hongjun/InstantLink.git
cd InstantLink
cargo build --workspace --release
```

The CLI binary will be at `target/release/instantlink`.

### Install the CLI

```bash
cargo install --path crates/instantlink-cli
```

Or copy the release binary manually:

```bash
cp target/release/instantlink /usr/local/bin/
```

## Build the macOS App

The app bundle embeds the CLI binary and the FFI dylib. Runtime printer/device operations use the bundled FFI dylib; the bundled CLI is kept only for lightweight metadata such as version reporting.

```bash
bash scripts/build-app.sh 0.1.3
```

`scripts/build-app.sh` requires a semver version argument. It builds the Rust workspace, compiles the SwiftUI launcher, bundles localizations and fonts, codesigns the app, and creates a `.dmg` when `create-dmg` is installed (`brew install create-dmg`).

The resulting bundle is written to `target/release/InstantLink.app`.

## Build the FFI Header

The checked-in header at `crates/instantlink-ffi/include/instantlink.h` is only refreshed when `INSTANTLINK_UPDATE_HEADER` is set:

```bash
INSTANTLINK_UPDATE_HEADER=1 cargo build --release -p instantlink-ffi
```

Without that environment variable, cbindgen writes the generated header into Cargo's `OUT_DIR`.

!!! note "Bluetooth Permissions"
    `InstantLink.app` includes `NSBluetoothAlwaysUsageDescription`. When running the CLI directly outside the app bundle, macOS will prompt for Bluetooth permission on first use.

## Verify Installation

```bash
instantlink --version
instantlink scan
```
