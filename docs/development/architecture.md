# Architecture

InstantLink uses a layered Rust core with thin CLI and FFI frontends, plus a native SwiftUI macOS app.

## Dependency Graph

```text
instantlink-cli ──→ instantlink-core
instantlink-ffi ──→ instantlink-core
macOS app ───────→ instantlink-ffi (device operations, via dlopen, bundled dylib)
macOS app ───────→ bundled instantlink-cli (version metadata only)
```

## `instantlink-core`

The core crate owns BLE communication, model detection, protocol encoding, and image preparation. It is fully async with `tokio` and `btleplug`.

### Module Layers

```text
printer.rs     ← High-level API (scan, connect, print_file)
    ↓
device.rs      ← PrinterDevice trait + BlePrinterDevice
    ↓
transport.rs   ← BLE GATT transport (btleplug)
    ↓
commands.rs    ← Command/Response enums
    ↓
protocol.rs    ← Packet build/parse, checksum, fragmentation
    ↓
models.rs      ← PrinterModel enum + per-model specs
error.rs       ← PrinterError + Result alias
image.rs       ← Load, resize, encode, chunk
```

### Key Decisions

- **Async throughout**: `btleplug` requires async BLE access, so the core stays async end to end.
- **Model-aware behavior**: `BlePrinterDevice::new` detects the printer model from `IMAGE_SUPPORT_INFO`, and uses the DIS model hint (`FI033`) first to distinguish Mini Link 3 from earlier Mini models.
- **ACK-based transfer**: each image chunk is sent only after the previous chunk is acknowledged.
- **Model-specific JPEG limits**: quality reduction targets each model's own image-size cap instead of a single global threshold.
- **Transport abstraction**: `transport::Transport` allows mock transports in tests without requiring hardware.

## `instantlink-cli`

The CLI is a thin `clap` frontend around `instantlink_core::printer`.

- Commands: `scan`, `info`, `print`, `led set`, `led off`, `status`
- JSON output is implemented for `scan`, `info`, and `status`
- Human-readable flows use spinners and progress text rather than mirroring raw protocol events

## `instantlink-ffi`

The FFI crate exposes the Rust core through a C ABI.

- Global `tokio` runtime via `OnceLock<Runtime>`
- `Mutex`-protected connected device handle
- `catch_unwind` on all extern entry points
- 19 exported functions, including lifecycle, status, printing, LED control, and printer `shutdown` / `reset`

## macOS App

The macOS app lives in `macos/InstantLink/` and is split by responsibility.

- `App/` holds the SwiftUI app entry point, restart helper, and app delegate
- `Core/` holds shared app state such as `ViewModel`, printer orchestration, queue state, and print pipeline logic
- `Features/` groups UI by workflow: `Camera/`, `Editor/`, `Main/`, and `Settings/`
- `Support/` holds reusable UI primitives such as film frames, preview helpers, overlay canvas rendering, and shared visual styles
- `OverlayModels.swift` defines the overlay data model and typed payloads
- `InstantLinkFFI.swift` loads `libinstantlink_ffi.dylib` and resolves the FFI symbols at runtime
- `.lproj/Localizable.strings` bundles provide 12-language localization

The app no longer compiles a Swift CLI wrapper or keeps a CLI fallback path for printer operations. Runtime printer/device work goes through `InstantLinkFFI.swift` into the bundled `libinstantlink_ffi.dylib`. The bundled `instantlink-cli` binary remains in the app only for lightweight metadata lookups such as version display.

### macOS Editing Model

The macOS app keeps per-photo edits in queue item state rather than one shared editor state.

- Crop, rotation, flip, fit mode, and film orientation are stored per queue item
- Overlays are first-class data: text, QR code, timestamp, imported image, and location
- Preview and print use the same overlay composition pipeline so edited output matches what the user sees
- New-photo defaults seed future imports without mutating existing queue items

## No Daemon

InstantLink does not need a background daemon. Printing is a short-lived connect-transfer-print-disconnect workflow, and the macOS app talks to the printer directly through the bundled FFI dylib for runtime operations.
