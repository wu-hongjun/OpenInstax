# FFI Reference

`instantlink-ffi` exposes the Rust core through a C ABI for Swift, C, and other native callers.

## Build

```bash
cargo build --release -p instantlink-ffi
```

Artifacts:

- `target/release/libinstantlink_ffi.dylib`
- `target/release/libinstantlink_ffi.a`

By default, cbindgen writes the generated header into Cargo's `OUT_DIR`. To refresh the checked-in header at `crates/instantlink-ffi/include/instantlink.h`, build with:

```bash
INSTANTLINK_UPDATE_HEADER=1 cargo build --release -p instantlink-ffi
```

## Status Codes

All exported functions return `i32` status codes:

| Code | Meaning |
|------|---------|
| `0` | Success |
| `-1` | Printer not found or no device connected |
| `-2` | Multiple printers found |
| `-3` | BLE/protocol/internal error or panic caught |
| `-4` | Timeout |
| `-5` | Invalid argument |
| `-6` | Image processing error |
| `-7` | Print rejected or unexpected device response |
| `-8` | No film remaining |
| `-9` | Battery too low |
| `-10` | Printer cover is open |
| `-11` | Printer is busy |

## Connection Progress Callback

Specific reconnect/pairing flows can use `instantlink_connect_named_with_progress(...)` to receive connection-stage events.

```c
typedef void (*instantlink_connect_stage_cb)(int32_t stage, const char *detail);
```

| Stage | Code | Meaning |
|------|------|---------|
| `scan_started` | `0` | BLE scan started |
| `scan_finished` | `1` | BLE scan finished |
| `device_matched` | `2` | Matching printer advertisement found |
| `ble_connecting` | `3` | CoreBluetooth connection starting |
| `service_discovery` | `4` | GATT service discovery |
| `characteristic_lookup` | `5` | Write/notify characteristic resolution |
| `notification_subscribe` | `6` | Notification subscription |
| `model_detecting` | `7` | Printer model detection |
| `status_fetching` | `8` | Initial status fetch |
| `connected` | `9` | Connection is ready for use |
| `failed` | `10` | Connection failed |

`detail` is optional stage-specific context and is only valid during the callback invocation. Like the print progress callback, the connect-stage callback is invoked from the runtime thread, so UI callers must marshal updates back to the main thread.

## Exported Functions

### Lifecycle

```c
void instantlink_init(void);
int32_t instantlink_connect(void);
int32_t instantlink_connect_named(const char *name, int32_t duration_secs);
int32_t instantlink_connect_named_with_progress(const char *name,
                                                int32_t duration_secs,
                                                instantlink_connect_stage_cb progress_cb);
int32_t instantlink_disconnect(void);
int32_t instantlink_is_connected(void);
int32_t instantlink_shutdown(void);
int32_t instantlink_reset(void);
```

- `instantlink_is_connected` returns `1` when connected, `0` when disconnected, or a negative error code
- `instantlink_connect_named_with_progress` is the preferred entry point for UI-driven reconnect flows because it exposes real connection stages
- `instantlink_shutdown` powers off the connected printer
- `instantlink_reset` resets the connected printer

### Scanning

```c
int32_t instantlink_scan(int32_t duration_secs, char *out_json, int32_t out_len);
```

Writes a JSON array of printer names into `out_json` and returns the number of bytes written.

### Status Queries

```c
int32_t instantlink_battery(void);
int32_t instantlink_film_remaining(void);
int32_t instantlink_film_and_charging(int32_t *out_film, int32_t *out_charging);
int32_t instantlink_print_count(void);
int32_t instantlink_status(int32_t *out_battery, int32_t *out_film,
                           int32_t *out_charging, int32_t *out_print_count);
int32_t instantlink_device_name(char *out, int32_t out_len);
int32_t instantlink_device_model(char *out, int32_t out_len);
```

### Printing

```c
int32_t instantlink_print(const char *path, uint8_t quality,
                          uint8_t fit_mode, uint8_t print_option);
int32_t instantlink_print_with_progress(const char *path, uint8_t quality,
                                        uint8_t fit_mode, uint8_t print_option,
                                        void (*progress_cb)(uint32_t sent, uint32_t total));
```

- `fit_mode`: `0 = crop`, `1 = contain`, `2 = stretch`
- `print_option`: `0 = Rich`, `1 = Natural`
- `progress_cb` is optional and receives acknowledged chunk progress

### LED Control

```c
int32_t instantlink_set_led(uint8_t r, uint8_t g, uint8_t b, uint8_t pattern);
int32_t instantlink_led_off(void);
```

- `pattern`: `0 = solid`, `1 = blink`, `2 = breathe`

## Swift Usage

The macOS app loads the dylib via `dlopen` and resolves the exported symbols in `InstantLinkFFI.swift`.

```swift
import Foundation

let handle = dlopen("libinstantlink_ffi.dylib", RTLD_NOW)
let initFn = dlsym(handle, "instantlink_init")
let connectFn = dlsym(handle, "instantlink_connect")
// ... resolve the remaining symbols
```

## Thread Safety

The FFI layer owns:

- a global `tokio` runtime via `OnceLock<Runtime>`
- a `Mutex`-protected connected device handle
- `catch_unwind` guards on all exported functions

The macOS wrapper serializes FFI calls on a dedicated queue so reconnect, status, and print operations do not race each other.

The progress callback passed to `instantlink_print_with_progress` is invoked from the runtime thread. Native callers should synchronize any UI state they mutate from that callback.
