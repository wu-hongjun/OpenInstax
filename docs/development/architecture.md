# Architecture

OpenInstax follows a layered architecture mirroring [StatusLight](https://github.com/wu-hongjun/StatusLight).

## Crate Dependency Graph

```
openinstax-cli ──→ openinstax-core
openinstax-ffi ──→ openinstax-core
macOS app ─────→ openinstax-cli (via Process)
```

## openinstax-core

The core library handles all BLE communication and image processing. It is fully async (tokio + btleplug).

### Module Layers

```
printer.rs     ← High-level API (scan, connect, print_file)
    ↓
device.rs      ← InstaxDevice trait + BleInstaxDevice (ACK flow)
    ↓
transport.rs   ← BLE GATT transport (btleplug)
    ↓
commands.rs    ← Command/Response enums, encode/decode
    ↓
protocol.rs    ← Packet build/parse, checksum, fragmentation
    ↓
models.rs      ← PrinterModel enum + specs
error.rs       ← InstaxError + Result alias
image.rs       ← Load, resize, JPEG encode, chunk
```

### Key Design Decisions

**Async throughout**: btleplug requires tokio, so the entire core is async. The `InstaxDevice` trait uses `async_trait`.

**Model auto-detection**: After connecting, we query `IMAGE_SUPPORT_INFO` and match the returned width/height to a `PrinterModel`. This determines image dimensions and chunk sizes.

**ACK-based flow**: Each data chunk requires an ACK from the printer before sending the next. This is handled in `BleInstaxDevice::send_image_data`.

**Automatic quality reduction**: If the JPEG exceeds 105KB, quality is reduced in steps of 5 until it fits.

**Transport trait**: `transport::Transport` is a trait, enabling future mock implementations for testing without hardware.

## openinstax-cli

Thin CLI layer using clap for argument parsing and indicatif for progress bars. All printer operations delegate to `openinstax_core::printer`.

Supports `--json` output on all commands for machine consumption (used by the macOS app).

## openinstax-ffi

C FFI bindings using cbindgen. Manages a global tokio runtime (`OnceLock<Runtime>`) and a `Mutex`-protected device handle. All functions use `catch_unwind` to prevent Rust panics from crossing the FFI boundary.

## macOS App

SwiftUI app with menu bar extra and full window. Uses `OpenInstaxCLI.swift` to call the bundled CLI binary via `Process` (same pattern as StatusLight). Communication is via `--json` output parsing.

### Why Process Instead of FFI?

Following StatusLight's pattern, the macOS app wraps the CLI binary rather than linking the FFI directly. This provides:

- Simpler deployment (single binary to bundle)
- Process isolation (crashes don't take down the app)
- Same interface as the CLI (JSON output)
- Easier debugging (can test CLI commands independently)

## No Daemon

Unlike StatusLight, OpenInstax has no daemon crate. Instax printing is inherently one-shot: connect, transfer image, print, disconnect. There's no need for a persistent background service.
