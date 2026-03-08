# InstantLink

**InstantLink** is an open-source Rust CLI and native macOS app for printing to Fujifilm Instax Link printers (Mini, Square, Wide) via Bluetooth Low Energy.

The Instax Link BLE protocol has been fully reverse-engineered by the open-source community ([javl/InstaxBLE](https://github.com/javl/InstaxBLE), [linssenste/instax-link-web](https://github.com/linssenste/instax-link-web)). InstantLink provides a clean, well-engineered Rust implementation.

## Components

| Crate | Description |
|-------|-------------|
| **instantlink-core** | BLE protocol, image processing, device communication |
| **instantlink-cli** | Command-line interface with progress bars and JSON output |
| **instantlink-ffi** | C FFI bindings for native GUIs (Swift, etc.) |

## Supported Printers

| Model | Resolution | Film Type |
|-------|-----------|-----------|
| Instax Mini Link | 600x800 | Instax Mini |
| Instax Square Link | 800x800 | Instax Square |
| Instax Wide Link | 1260x840 | Instax Wide |

## Features

- Print any image (JPEG, PNG, etc.) to Instax Link printers
- Auto-resize with crop, contain, or stretch fit modes
- Auto JPEG quality reduction to fit printer limits
- Battery level, film count, and print history queries
- LED color control with solid, blink, and breathe patterns
- BLE scanner to discover nearby printers
- JSON output mode for integration with other tools
- Native macOS app with menu bar and drag-and-drop printing
- C FFI for building native UIs

## Architecture

InstantLink mirrors the architecture of [StatusLight](https://github.com/wu-hongjun/StatusLight), with the core library providing async BLE communication via btleplug, a CLI that calls core directly, and an FFI layer for the SwiftUI macOS app.

Unlike StatusLight, there is no daemon crate. Instax printing is a one-shot operation (connect, print, disconnect) rather than a continuous service.
