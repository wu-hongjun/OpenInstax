# FFI Reference

The `openinstax-ffi` crate provides C-compatible bindings for controlling Instax printers from Swift, C, or any language with C FFI support.

## Build

The FFI crate produces both `cdylib` (shared) and `staticlib` (static) libraries:

```bash
cargo build --release -p openinstax-ffi
```

Output files:

- `target/release/libopeninstax_ffi.dylib` (macOS shared)
- `target/release/libopeninstax_ffi.a` (static)

The C header is auto-generated at `crates/openinstax-ffi/include/openinstax.h` during build via cbindgen.

## Status Codes

All functions return `i32` status codes:

| Code | Meaning |
|------|---------|
| `0` | Success |
| `-1` | Printer not found |
| `-2` | Multiple printers found |
| `-3` | BLE communication error (or panic caught) |
| `-4` | Timeout |
| `-5` | Invalid argument (null pointer, bad UTF-8) |
| `-6` | Image processing error |
| `-7` | Print rejected |
| `-8` | No film remaining |
| `-9` | Battery too low |

## Functions

### Lifecycle

```c
// Initialize logging and runtime. Safe to call multiple times.
void openinstax_init(void);

// Connect to the first available printer. Returns 0 on success.
int32_t openinstax_connect(void);

// Connect to a specific printer by name.
int32_t openinstax_connect_named(const char *name);

// Disconnect from the current printer.
int32_t openinstax_disconnect(void);

// Check if a printer is currently connected. Returns 1 if yes, 0 if no.
int32_t openinstax_is_connected(void);
```

### Queries

```c
// Get battery level (0-100). Returns negative error code on failure.
int32_t openinstax_battery(void);

// Get remaining film count. Returns negative error code on failure.
int32_t openinstax_film_remaining(void);
```

### Printing

```c
// Print an image file.
// fit_mode: 0=crop, 1=contain, 2=stretch
int32_t openinstax_print(const char *path, uint8_t quality, uint8_t fit_mode);
```

### LED Control

```c
// Set LED color and pattern.
// pattern: 0=solid, 1=blink, 2=breathe
int32_t openinstax_set_led(uint8_t r, uint8_t g, uint8_t b, uint8_t pattern);

// Turn off the LED.
int32_t openinstax_led_off(void);
```

## Swift Usage

The macOS app uses `OpenInstaxCLI.swift` (a Process wrapper around the CLI binary) rather than calling FFI directly. However, the FFI can be used from Swift:

```swift
import Foundation

// Link against libopeninstax_ffi.a

openinstax_init()

let result = openinstax_connect()
if result == 0 {
    let battery = openinstax_battery()
    print("Battery: \(battery)%")

    openinstax_print("/path/to/photo.jpg", 97, 0)
    openinstax_disconnect()
}
```

## Thread Safety

The FFI layer maintains a global tokio runtime and a `Mutex`-protected device handle. All functions are safe to call from any thread. The `Mutex` serializes access to the printer.
