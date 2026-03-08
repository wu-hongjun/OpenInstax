# Installation

## From Source

### Prerequisites

- [Rust](https://rustup.rs/) (stable, 2021 edition)
- macOS (for BLE via CoreBluetooth) or Linux (with BlueZ)

### Build

```bash
git clone https://github.com/wu-hongjun/OpenInstax.git
cd OpenInstax
cargo build --release
```

The CLI binary will be at `target/release/openinstax`.

### Install

Copy the binary to your PATH:

```bash
cp target/release/openinstax /usr/local/bin/
```

## macOS App

The macOS app bundles the CLI binary and provides a native SwiftUI interface.

```bash
./scripts/build-app.sh --release
```

!!! note "Bluetooth Permissions"
    On macOS, the app requires Bluetooth permission. The `Info.plist` must include `NSBluetoothAlwaysUsageDescription`. When running the CLI directly, macOS will prompt for Bluetooth access on first use.

## Verify Installation

```bash
openinstax --version
openinstax scan
```
