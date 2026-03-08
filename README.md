# InstantLink

[![CI](https://github.com/wu-hongjun/InstantLink/actions/workflows/ci.yml/badge.svg)](https://github.com/wu-hongjun/InstantLink/actions/workflows/ci.yml)

Open-source CLI and native macOS app for printing to Fujifilm Instax Link printers via Bluetooth Low Energy. Supports the Instax Mini Link, Square Link, and Wide Link.

The Instax Link BLE protocol was reverse-engineered by the open-source community ([javl/InstaxBLE](https://github.com/javl/InstaxBLE), [linssenste/instax-link-web](https://github.com/linssenste/instax-link-web)). InstantLink provides a clean Rust implementation.

## What's Included

| Crate | Description |
|-------|-------------|
| **instantlink-core** | Core library — BLE protocol, image processing, device communication |
| **instantlink** (CLI) | Command-line tool to scan, query, and print |
| **instantlink-ffi** | C FFI bindings for building native GUIs (Swift, etc.) |

## Install

Pre-built releases will be available on the [Releases](https://github.com/wu-hongjun/InstantLink/releases) page once tagged.

Build from source:

```bash
git clone https://github.com/wu-hongjun/InstantLink.git
cd InstantLink
cargo build --workspace --release

# Install the CLI
cargo install --path crates/instantlink-cli
```

## Quick Start

```bash
# Scan for nearby printers
instantlink scan

# Check printer status
instantlink status

# Print an image
instantlink print photo.jpg

# Print with specific fit mode and quality
instantlink print photo.jpg --fit contain --quality 90

# Control the LED
instantlink led set "#FF6600" --pattern breathe
instantlink led off
```

All commands support `--json` for machine-readable output.

## Supported Printers

| Model | Resolution | Film Type |
|-------|-----------|-----------|
| Instax Mini Link | 600x800 | Instax Mini |
| Instax Square Link | 800x800 | Instax Square |
| Instax Wide Link | 1260x840 | Instax Wide |

The printer model is auto-detected after connecting.

## Features

- Print any image (JPEG, PNG, etc.) to Instax Link printers via BLE
- Auto-resize with crop, contain, or stretch fit modes
- Automatic JPEG quality reduction to fit printer limits (105KB)
- Battery level, film count, and print history queries
- LED color control with solid, blink, and breathe patterns
- BLE scanner to discover nearby printers
- JSON output mode for integration with other tools
- Native macOS app with menu bar and drag-and-drop printing
- C FFI for building native UIs

## Project Structure

```
InstantLink/
├── Cargo.toml                    # Workspace root
├── mkdocs.yml                    # Documentation config
├── docs/                         # MkDocs source
├── crates/
│   ├── instantlink-core/          # Core library
│   ├── instantlink-cli/           # CLI binary
│   └── instantlink-ffi/           # C FFI
└── macos/                        # Native macOS app (SwiftUI)
    └── InstantLink/
```

## macOS App

The macOS app provides a menu bar interface with drag-and-drop printing. It bundles the CLI binary and communicates via `--json` output (same pattern as [StatusLight](https://github.com/wu-hongjun/StatusLight)).

```bash
# Build the app bundle
bash scripts/build-app.sh 0.1.0
```

## Documentation

Full docs are available at [wu-hongjun.github.io/InstantLink](https://wu-hongjun.github.io/InstantLink/).

## License

MIT
