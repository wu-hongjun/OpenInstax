# FFI Reference

The `instantlink-ffi` crate provides C-compatible bindings for controlling Instax printers from Swift, C, or any language with C FFI support.

## Build

The FFI crate produces both `cdylib` (shared) and `staticlib` (static) libraries:

```bash
cargo build --release -p instantlink-ffi
```

Output files:

- `target/release/libinstantlink_ffi.dylib` (macOS shared)
- `target/release/libinstantlink_ffi.a` (static)

The C header is auto-generated at `crates/instantlink-ffi/include/instantlink.h` during build via cbindgen.

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
void instantlink_init(void);

// Connect to the first available printer. Returns 0 on success.
int32_t instantlink_connect(void);

// Connect to a specific printer by name. Pass 0 for duration_secs to use the default.
int32_t instantlink_connect_named(const char *name, int32_t duration_secs);

// Disconnect from the current printer.
int32_t instantlink_disconnect(void);

// Check if a printer is currently connected. Returns 1 if yes, 0 if no.
int32_t instantlink_is_connected(void);
```

### Queries

```c
// Get battery level (0-100). Returns negative error code on failure.
int32_t instantlink_battery(void);

// Get remaining film count. Returns negative error code on failure.
int32_t instantlink_film_remaining(void);
```

### Printing

```c
// Print an image file.
// fit_mode: 0=crop, 1=contain, 2=stretch
// print_option: 0=Rich (vivid), 1=Natural (classic)
int32_t instantlink_print(const char *path, uint8_t quality, uint8_t fit_mode, uint8_t print_option);
```

### LED Control

```c
// Set LED color and pattern.
// pattern: 0=solid, 1=blink, 2=breathe
int32_t instantlink_set_led(uint8_t r, uint8_t g, uint8_t b, uint8_t pattern);

// Turn off the LED.
int32_t instantlink_led_off(void);
```

## Swift Usage

The macOS app uses `InstantLinkCLI.swift` (a Process wrapper around the CLI binary) rather than calling FFI directly. However, the FFI can be used from Swift:

```swift
import Foundation

// Link against libinstantlink_ffi.a

instantlink_init()

let result = instantlink_connect()
if result == 0 {
    let battery = instantlink_battery()
    print("Battery: \(battery)%")

    instantlink_print("/path/to/photo.jpg", 97, 0, 0)
    instantlink_disconnect()
}
```

## Thread Safety

The FFI layer maintains a global tokio runtime and a `Mutex`-protected device handle. All functions are safe to call from any thread. The `Mutex` serializes access to the printer.
