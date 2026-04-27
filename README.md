# InstantLink

[![CI](https://github.com/wu-hongjun/InstantLink/actions/workflows/ci.yml/badge.svg)](https://github.com/wu-hongjun/InstantLink/actions/workflows/ci.yml)

Open-source CLI and native macOS app for printing to Fujifilm Instax Link printers via Bluetooth Low Energy. Supports the Instax Mini Link, Square Link, and Wide Link.

<img width="1346" height="1362" alt="85f25fd250a079acc1ed95fa903955c9" src="https://github.com/user-attachments/assets/90b0f6a6-4400-4bd0-b468-e0afe79e15b5" />


The Instax Link BLE protocol was reverse-engineered by the open-source community ([javl/InstaxBLE](https://github.com/javl/InstaxBLE), [linssenste/instax-link-web](https://github.com/linssenste/instax-link-web)). InstantLink provides a clean Rust implementation.

## What's Included

| Crate | Description |
|-------|-------------|
| **instantlink-core** | Core library — BLE protocol, image processing, device communication |
| **instantlink-cli** (`instantlink` binary) | Command-line tool to scan, query, and print |
| **instantlink-ffi** | C FFI bindings for building native GUIs (Swift, etc.) |

## Install

Pre-built artifacts are published on the [Releases](https://github.com/wu-hongjun/InstantLink/releases) page for each tagged version.

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

# Print with natural color mode (classic film look)
instantlink print photo.jpg --color-mode natural

# Control the LED
instantlink led set "#FF6600" --pattern breathe
instantlink led off
```

Structured `--json` output is currently implemented for `scan`, `info`, and `status`.

## Supported Printers

| Model | Resolution | Film Type |
|-------|-----------|-----------|
| Instax Mini Link | 600x800 | Instax Mini |
| Instax Mini Link 3 | 600x800 | Instax Mini |
| Instax Square Link | 800x800 | Instax Square |
| Instax Wide Link | 1260x840 | Instax Wide |

The printer model is auto-detected after connecting.

## Features

- Print any image (JPEG, PNG, etc.) to Instax Link printers via BLE
- Auto-resize with crop, contain, or stretch fit modes
- Rich and Natural color modes (vivid vs classic film look)
- Automatic JPEG quality reduction to fit model-specific printer limits (Mini `105KB`, Mini Link 3 `55KB`, Square `105KB`, Wide `225KB`)
- Battery level, film count, charging state, and print history queries
- LED color control with solid, blink, and breathe patterns
- BLE scanner to discover nearby printers
- JSON output mode for integration with other tools
- Native macOS app with:
  - Menu bar interface with drag-and-drop printing
  - Built-in image editor with crop, rotate, overlays, and queue-aware defaults
  - Camera capture mode with self-timer (2s / 10s countdown)
  - Film orientation toggle (portrait/landscape) with print-time rotation
  - Film border preview showing the physical Instax film shape
  - Stage-aware pairing and reconnect UI
  - Multi-printer management with saved profiles
  - Experimental in-app LED diagnostics in Settings
  - Auto-update via GitHub releases
  - Localized in 12 languages
- C FFI (22 exported functions: see `docs/reference/ffi.md`) for building native UIs, including connection-stage callbacks

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

The macOS app is a native SwiftUI application with menu bar integration. It loads the Rust core library via FFI (`dlopen`/`dlsym`) for direct BLE communication. The bundled CLI remains in the app for lightweight metadata such as version reporting.

Features include drag-and-drop image printing, a built-in image editor, overlays, camera capture with self-timer, film orientation control, film border preview, printer profile management, and auto-updates.

```bash
# Build the app bundle
bash scripts/build-app.sh 0.1.5
```

## Documentation

Full docs are available at [instantlink.hongjunwu.com](https://instantlink.hongjunwu.com/).

## License

MIT
