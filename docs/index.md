# InstantLink

**InstantLink** is an open-source Rust CLI and native macOS app for printing to Fujifilm Instax Link printers (Mini, Mini Link 3, Square, Wide) over Bluetooth Low Energy.

The Instax Link BLE protocol was reverse-engineered by the open-source community ([javl/InstaxBLE](https://github.com/javl/InstaxBLE), [linssenste/instax-link-web](https://github.com/linssenste/instax-link-web)). InstantLink packages that protocol into a production-oriented Rust core, CLI, FFI surface, and native macOS UI.

## Components

| Crate | Description |
|-------|-------------|
| **instantlink-core** | BLE protocol, image processing, model detection, device communication |
| **instantlink-cli** | Command-line interface with progress output and JSON for `scan`, `info`, and `status` |
| **instantlink-ffi** | C FFI bindings for Swift and other native frontends |

## Supported Printers

| Model | Resolution | Film Type |
|-------|-----------|-----------|
| Instax Mini Link | 600x800 | Instax Mini |
| Instax Mini Link 3 | 600x800 | Instax Mini |
| Instax Square Link | 800x800 | Instax Square |
| Instax Wide Link | 1260x840 | Instax Wide |

## Features

- Print JPEG, PNG, and other common image formats
- Crop, contain, or stretch images to the selected printer model
- Rich and Natural color modes
- Automatic JPEG quality reduction to model-specific limits: Mini `105KB`, Mini Link 3 `55KB`, Square `105KB`, Wide `225KB`
- Battery, film remaining, charging, and print-count queries
- LED color control with solid, blink, and breathe patterns
- Native macOS app with camera capture, queue-based editing, film simulation, overlays for text/QR/timestamp/image/location, stage-aware reconnect UI, and experimental LED diagnostics in Settings
- C FFI with 20 exported functions for native app integrations, including connect-stage progress callbacks

## Architecture

InstantLink mirrors the structure of [StatusLight](https://github.com/wu-hongjun/StatusLight): a reusable async Rust core, a thin CLI, and an FFI layer loaded by the SwiftUI macOS app via `dlopen`/`dlsym`.

Unlike StatusLight, there is no daemon crate. Instax printing is a one-shot workflow: connect, transfer, print, disconnect.
