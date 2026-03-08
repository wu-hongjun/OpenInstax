# Installation

## From Releases

Pre-built `.dmg` releases are available for **macOS Apple Silicon** (ARM64) on the [Releases](https://github.com/wu-hongjun/InstantLink/releases) page. Download, mount, and drag the app to Applications.

## From Source

### Prerequisites

- [Rust](https://rustup.rs/) (stable, 2021 edition)
- macOS (for BLE via CoreBluetooth) or Linux (with BlueZ)

### Build

```bash
git clone https://github.com/wu-hongjun/InstantLink.git
cd InstantLink
cargo build --workspace --release
```

The CLI binary will be at `target/release/instantlink`.

### Install CLI

```bash
cargo install --path crates/instantlink-cli
```

Or copy manually:

```bash
cp target/release/instantlink /usr/local/bin/
```

## macOS App

The macOS app bundles the CLI binary and provides a native SwiftUI menu bar interface with drag-and-drop printing.

```bash
# Build the Rust workspace first
cargo build --workspace --release

# Build the app bundle (requires macOS)
bash scripts/build-app.sh 0.1.0
```

The `.app` bundle is created at `target/release/InstantLink.app`. The script:

1. Copies the CLI binary into the bundle (renamed `instantlink-cli`)
2. Compiles the SwiftUI launcher with `swiftc`
3. Generates `Info.plist` with version and BLE permission
4. Ad-hoc codesigns the bundle
5. Optionally creates a `.dmg` (if `create-dmg` is installed: `brew install create-dmg`)

!!! note "Bluetooth Permissions"
    The app includes `NSBluetoothAlwaysUsageDescription` in its `Info.plist` for BLE access. When running the CLI directly (outside the app bundle), macOS will prompt for Bluetooth permission on first use.

## Verify Installation

```bash
instantlink --version
instantlink scan
```
