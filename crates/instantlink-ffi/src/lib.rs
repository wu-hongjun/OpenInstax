//! C FFI bindings for controlling Instax Link printers from Swift/C.
//!
//! All functions return `i32` status codes:
//! -  `0` — success
//! - `-1` — printer not found
//! - `-2` — multiple printers found
//! - `-3` — BLE communication error (or panic caught)
//! - `-4` — timeout
//! - `-5` — invalid argument (null pointer, bad UTF-8)
//! - `-6` — image processing error
//! - `-7` — print rejected
//! - `-8` — no film remaining
//! - `-9` — battery too low
//! - `-10` — printer cover is open
//! - `-11` — printer is busy

#![allow(unsafe_op_in_unsafe_fn)]

use std::ffi::CStr;
use std::os::raw::c_char;
use std::sync::{Mutex, Once, OnceLock};
use std::time::Duration;

use instantlink_core::error::PrinterError;

static INIT: Once = Once::new();
static RUNTIME: OnceLock<tokio::runtime::Runtime> = OnceLock::new();
static DEVICE: OnceLock<Mutex<Option<Box<dyn instantlink_core::PrinterDevice>>>> = OnceLock::new();

fn get_runtime() -> &'static tokio::runtime::Runtime {
    RUNTIME.get_or_init(|| {
        tokio::runtime::Builder::new_multi_thread()
            .enable_all()
            .build()
            .expect("failed to create tokio runtime")
    })
}

fn get_device_lock() -> &'static Mutex<Option<Box<dyn instantlink_core::PrinterDevice>>> {
    DEVICE.get_or_init(|| Mutex::new(None))
}

/// Map an [`PrinterError`] to an FFI error code.
fn error_code(e: &PrinterError) -> i32 {
    match e {
        PrinterError::PrinterNotFound => -1,
        PrinterError::MultiplePrinters { .. } => -2,
        PrinterError::Ble(_) => -3,
        PrinterError::Timeout => -4,
        PrinterError::Image(_) | PrinterError::ImageTooLarge { .. } => -6,
        PrinterError::PrintRejected(_) | PrinterError::UnexpectedResponse(_) => -7,
        PrinterError::NoFilm => -8,
        PrinterError::LowBattery { .. } => -9,
        PrinterError::CoverOpen => -10,
        PrinterError::PrinterBusy => -11,
        PrinterError::Protocol(_) => -3,
        PrinterError::Io(_) => -3,
    }
}

/// Helper: read a C string pointer into a `&str`, returning `-5` on error.
///
/// # Safety
///
/// `ptr` must be a valid, non-null, null-terminated UTF-8 C string.
unsafe fn cstr_to_str<'a>(ptr: *const c_char) -> Result<&'a str, i32> {
    if ptr.is_null() {
        return Err(-5);
    }
    CStr::from_ptr(ptr).to_str().map_err(|_| -5)
}

/// Helper: write a Rust string into a caller-provided buffer.
///
/// Writes up to `out_len - 1` bytes plus a NUL terminator.
/// Returns the number of bytes written (excluding NUL), or a negative error.
///
/// # Safety
///
/// `out` must point to a buffer of at least `out_len` bytes.
unsafe fn write_str_to_buf(s: &str, out: *mut c_char, out_len: i32) -> i32 {
    if out.is_null() || out_len <= 0 {
        return -5;
    }
    let max = (out_len - 1) as usize;
    let bytes = s.as_bytes();
    let copy_len = bytes.len().min(max);
    std::ptr::copy_nonoverlapping(bytes.as_ptr(), out as *mut u8, copy_len);
    *out.add(copy_len) = 0; // NUL terminator
    copy_len as i32
}

/// Initialize the FFI layer (logging + runtime). Safe to call multiple times.
#[unsafe(no_mangle)]
pub extern "C" fn instantlink_init() {
    INIT.call_once(|| {
        let _ = env_logger::try_init();
        let _ = get_runtime();
    });
}

/// Scan for nearby Instax printers.
///
/// Writes a JSON array of printer name strings into `out_json`.
/// Returns the number of bytes written (excluding NUL), or a negative error code.
///
/// # Safety
///
/// `out_json` must point to a buffer of at least `out_len` bytes.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn instantlink_scan(
    duration_secs: i32,
    out_json: *mut c_char,
    out_len: i32,
) -> i32 {
    std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        if out_json.is_null() || out_len <= 0 {
            return -5;
        }
        let dur = if duration_secs > 0 {
            Some(Duration::from_secs(duration_secs as u64))
        } else {
            None
        };
        let rt = get_runtime();
        match rt.block_on(instantlink_core::printer::scan(dur)) {
            Ok(printers) => {
                let names: Vec<&str> = printers.iter().map(|p| p.name.as_str()).collect();
                let json = serde_json::to_string(&names).unwrap_or_else(|_| "[]".to_string());
                write_str_to_buf(&json, out_json, out_len)
            }
            Err(e) => error_code(&e),
        }
    }))
    .unwrap_or(-3)
}

/// Connect to the first available Instax printer.
/// Returns 0 on success, negative error code on failure.
#[unsafe(no_mangle)]
pub extern "C" fn instantlink_connect() -> i32 {
    std::panic::catch_unwind(|| {
        let rt = get_runtime();
        let old_device = {
            let lock = get_device_lock();
            if let Ok(mut guard) = lock.lock() {
                guard.take()
            } else {
                return -3;
            }
        };
        if let Some(old_device) = old_device {
            let _ = rt.block_on(old_device.disconnect());
        }

        match rt.block_on(instantlink_core::printer::connect_any(None)) {
            Ok(device) => {
                let lock = get_device_lock();
                if let Ok(mut guard) = lock.lock() {
                    *guard = Some(device);
                    0
                } else {
                    -3
                }
            }
            Err(e) => error_code(&e),
        }
    })
    .unwrap_or(-3)
}

/// Connect to a specific printer by name with configurable scan duration.
///
/// `duration_secs` sets the BLE scan duration; pass 0 for the default.
///
/// # Safety
///
/// `name` must be a valid, non-null, null-terminated UTF-8 C string.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn instantlink_connect_named(name: *const c_char, duration_secs: i32) -> i32 {
    std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        let s = match cstr_to_str(name) {
            Ok(s) => s,
            Err(code) => return code,
        };
        let dur = if duration_secs > 0 {
            Some(Duration::from_secs(duration_secs as u64))
        } else {
            None
        };
        let rt = get_runtime();
        let old_device = {
            let lock = get_device_lock();
            if let Ok(mut guard) = lock.lock() {
                guard.take()
            } else {
                return -3;
            }
        };
        if let Some(old_device) = old_device {
            let _ = rt.block_on(old_device.disconnect());
        }

        match rt.block_on(instantlink_core::printer::connect(s, dur)) {
            Ok(device) => {
                let lock = get_device_lock();
                if let Ok(mut guard) = lock.lock() {
                    *guard = Some(device);
                    0
                } else {
                    -3
                }
            }
            Err(e) => error_code(&e),
        }
    }))
    .unwrap_or(-3)
}

/// Disconnect from the current printer.
#[unsafe(no_mangle)]
pub extern "C" fn instantlink_disconnect() -> i32 {
    std::panic::catch_unwind(|| {
        let lock = get_device_lock();
        if let Ok(mut guard) = lock.lock() {
            if let Some(device) = guard.take() {
                let rt = get_runtime();
                match rt.block_on(device.disconnect()) {
                    Ok(()) => 0,
                    Err(e) => error_code(&e),
                }
            } else {
                -1 // no device connected
            }
        } else {
            -3
        }
    })
    .unwrap_or(-3)
}

/// Get battery level (0-100). Returns negative error code on failure.
#[unsafe(no_mangle)]
pub extern "C" fn instantlink_battery() -> i32 {
    std::panic::catch_unwind(|| {
        let lock = get_device_lock();
        if let Ok(guard) = lock.lock() {
            if let Some(ref device) = *guard {
                let rt = get_runtime();
                match rt.block_on(device.battery()) {
                    Ok(level) => level as i32,
                    Err(e) => error_code(&e),
                }
            } else {
                -1
            }
        } else {
            -3
        }
    })
    .unwrap_or(-3)
}

/// Get remaining film count. Returns negative error code on failure.
#[unsafe(no_mangle)]
pub extern "C" fn instantlink_film_remaining() -> i32 {
    std::panic::catch_unwind(|| {
        let lock = get_device_lock();
        if let Ok(guard) = lock.lock() {
            if let Some(ref device) = *guard {
                let rt = get_runtime();
                match rt.block_on(device.film_remaining()) {
                    Ok(count) => count as i32,
                    Err(e) => error_code(&e),
                }
            } else {
                -1
            }
        } else {
            -3
        }
    })
    .unwrap_or(-3)
}

/// Get film remaining and charging state in one call.
///
/// On success, writes film count to `*out_film` and charging flag (0 or 1)
/// to `*out_charging`, and returns 0.
///
/// # Safety
///
/// `out_film` and `out_charging` must be valid, non-null pointers.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn instantlink_film_and_charging(
    out_film: *mut i32,
    out_charging: *mut i32,
) -> i32 {
    std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        if out_film.is_null() || out_charging.is_null() {
            return -5;
        }
        let lock = get_device_lock();
        if let Ok(guard) = lock.lock() {
            if let Some(ref device) = *guard {
                let rt = get_runtime();
                match rt.block_on(device.film_and_charging()) {
                    Ok((film, charging)) => {
                        *out_film = film as i32;
                        *out_charging = i32::from(charging);
                        0
                    }
                    Err(e) => error_code(&e),
                }
            } else {
                -1
            }
        } else {
            -3
        }
    }))
    .unwrap_or(-3)
}

/// Get total print count. Returns negative error code on failure.
#[unsafe(no_mangle)]
pub extern "C" fn instantlink_print_count() -> i32 {
    std::panic::catch_unwind(|| {
        let lock = get_device_lock();
        if let Ok(guard) = lock.lock() {
            if let Some(ref device) = *guard {
                let rt = get_runtime();
                match rt.block_on(device.print_count()) {
                    Ok(count) => count as i32,
                    Err(e) => error_code(&e),
                }
            } else {
                -1
            }
        } else {
            -3
        }
    })
    .unwrap_or(-3)
}

/// Get all status fields in one call (battery, film, charging, print count).
///
/// This performs a single mutex lock and one `block_on` call, making it
/// significantly faster than calling the individual getters separately.
///
/// # Safety
///
/// All output pointers must be valid and non-null.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn instantlink_status(
    out_battery: *mut i32,
    out_film: *mut i32,
    out_charging: *mut i32,
    out_print_count: *mut i32,
) -> i32 {
    std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        if out_battery.is_null()
            || out_film.is_null()
            || out_charging.is_null()
            || out_print_count.is_null()
        {
            return -5;
        }
        let lock = get_device_lock();
        if let Ok(guard) = lock.lock() {
            if let Some(ref device) = *guard {
                let rt = get_runtime();
                match rt.block_on(device.status()) {
                    Ok(status) => {
                        *out_battery = status.battery as i32;
                        *out_film = status.film_remaining as i32;
                        *out_charging = i32::from(status.is_charging);
                        *out_print_count = status.print_count as i32;
                        0
                    }
                    Err(e) => error_code(&e),
                }
            } else {
                -1
            }
        } else {
            -3
        }
    }))
    .unwrap_or(-3)
}

/// Get the connected device's BLE name.
///
/// Returns number of bytes written (excluding NUL), or negative error.
///
/// # Safety
///
/// `out` must point to a buffer of at least `out_len` bytes.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn instantlink_device_name(out: *mut c_char, out_len: i32) -> i32 {
    std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        let lock = get_device_lock();
        if let Ok(guard) = lock.lock() {
            if let Some(ref device) = *guard {
                write_str_to_buf(device.name(), out, out_len)
            } else {
                -1
            }
        } else {
            -3
        }
    }))
    .unwrap_or(-3)
}

/// Get the connected device's model string (e.g. "Instax Mini Link").
///
/// Returns number of bytes written (excluding NUL), or negative error.
///
/// # Safety
///
/// `out` must point to a buffer of at least `out_len` bytes.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn instantlink_device_model(out: *mut c_char, out_len: i32) -> i32 {
    std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        let lock = get_device_lock();
        if let Ok(guard) = lock.lock() {
            if let Some(ref device) = *guard {
                let model_str = device.model().to_string();
                write_str_to_buf(&model_str, out, out_len)
            } else {
                -1
            }
        } else {
            -3
        }
    }))
    .unwrap_or(-3)
}

/// Print an image file. Returns 0 on success, negative error code on failure.
///
/// - `quality`: JPEG quality 1-100
/// - `fit_mode`: 0 = crop, 1 = contain, 2 = stretch
/// - `print_option`: 0 = default/Rich, 1 = Natural (passed to device)
///
/// # Safety
///
/// `path` must be a valid, non-null, null-terminated UTF-8 C string.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn instantlink_print(
    path: *const c_char,
    quality: u8,
    fit_mode: u8,
    print_option: u8,
) -> i32 {
    std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        let s = match cstr_to_str(path) {
            Ok(s) => s,
            Err(code) => return code,
        };

        let fit = match fit_mode {
            1 => instantlink_core::FitMode::Contain,
            2 => instantlink_core::FitMode::Stretch,
            _ => instantlink_core::FitMode::Crop,
        };

        let lock = get_device_lock();
        if let Ok(guard) = lock.lock() {
            if let Some(ref device) = *guard {
                let rt = get_runtime();
                let path = std::path::Path::new(s);
                match rt.block_on(device.print_file(path, fit, quality, print_option, None)) {
                    Ok(()) => 0,
                    Err(e) => error_code(&e),
                }
            } else {
                -1
            }
        } else {
            -3
        }
    }))
    .unwrap_or(-3)
}

/// Print an image file with progress callback. Returns 0 on success.
///
/// The callback receives (chunks_sent, total_chunks) after each chunk is ACK'd.
///
/// # Safety
///
/// `path` must be a valid, non-null, null-terminated UTF-8 C string.
/// `progress_cb` may be null (no progress reporting).
#[unsafe(no_mangle)]
pub unsafe extern "C" fn instantlink_print_with_progress(
    path: *const c_char,
    quality: u8,
    fit_mode: u8,
    print_option: u8,
    progress_cb: Option<extern "C" fn(u32, u32)>,
) -> i32 {
    std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        let s = match cstr_to_str(path) {
            Ok(s) => s,
            Err(code) => return code,
        };

        let fit = match fit_mode {
            1 => instantlink_core::FitMode::Contain,
            2 => instantlink_core::FitMode::Stretch,
            _ => instantlink_core::FitMode::Crop,
        };

        let lock = get_device_lock();
        let device = if let Ok(mut guard) = lock.lock() {
            if let Some(device) = guard.take() {
                device
            } else {
                return -1;
            }
        } else {
            return -3;
        };

        let rt = get_runtime();
        let path = std::path::Path::new(s);
        let progress: Option<Box<dyn Fn(usize, usize) + Send + Sync>> = progress_cb.map(|cb| {
            Box::new(move |sent: usize, total: usize| {
                cb(sent as u32, total as u32);
            }) as Box<dyn Fn(usize, usize) + Send + Sync>
        });
        let progress_ref = progress
            .as_ref()
            .map(|callback| callback.as_ref() as &(dyn Fn(usize, usize) + Send + Sync));
        let print_result =
            rt.block_on(device.print_file(path, fit, quality, print_option, progress_ref));

        if let Ok(mut guard) = lock.lock() {
            *guard = Some(device);
        } else {
            return -3;
        };

        match print_result {
            Ok(()) => 0,
            Err(e) => error_code(&e),
        }
    }))
    .unwrap_or(-3)
}

/// Set LED color and pattern.
#[unsafe(no_mangle)]
pub extern "C" fn instantlink_set_led(r: u8, g: u8, b: u8, pattern: u8) -> i32 {
    std::panic::catch_unwind(|| {
        let lock = get_device_lock();
        if let Ok(guard) = lock.lock() {
            if let Some(ref device) = *guard {
                let rt = get_runtime();
                match rt.block_on(device.set_led(r, g, b, pattern)) {
                    Ok(()) => 0,
                    Err(e) => error_code(&e),
                }
            } else {
                -1
            }
        } else {
            -3
        }
    })
    .unwrap_or(-3)
}

/// Turn off the LED.
#[unsafe(no_mangle)]
pub extern "C" fn instantlink_led_off() -> i32 {
    std::panic::catch_unwind(|| {
        let lock = get_device_lock();
        if let Ok(guard) = lock.lock() {
            if let Some(ref device) = *guard {
                let rt = get_runtime();
                match rt.block_on(device.led_off()) {
                    Ok(()) => 0,
                    Err(e) => error_code(&e),
                }
            } else {
                -1
            }
        } else {
            -3
        }
    })
    .unwrap_or(-3)
}

/// Check if a printer is currently connected.
/// Returns 1 if connected, 0 if not, -3 on error.
#[unsafe(no_mangle)]
pub extern "C" fn instantlink_is_connected() -> i32 {
    std::panic::catch_unwind(|| {
        let lock = get_device_lock();
        if let Ok(guard) = lock.lock() {
            i32::from(guard.is_some())
        } else {
            -3
        }
    })
    .unwrap_or(-3)
}

/// Shut down (power off) the connected printer.
/// Returns 0 on success, negative error code on failure.
#[unsafe(no_mangle)]
pub extern "C" fn instantlink_shutdown() -> i32 {
    std::panic::catch_unwind(|| {
        let lock = get_device_lock();
        if let Ok(guard) = lock.lock() {
            if let Some(ref device) = *guard {
                let rt = get_runtime();
                match rt.block_on(device.shutdown()) {
                    Ok(()) => 0,
                    Err(e) => error_code(&e),
                }
            } else {
                -1
            }
        } else {
            -3
        }
    })
    .unwrap_or(-3)
}

/// Reset the connected printer.
/// Returns 0 on success, negative error code on failure.
#[unsafe(no_mangle)]
pub extern "C" fn instantlink_reset() -> i32 {
    std::panic::catch_unwind(|| {
        let lock = get_device_lock();
        if let Ok(guard) = lock.lock() {
            if let Some(ref device) = *guard {
                let rt = get_runtime();
                match rt.block_on(device.reset()) {
                    Ok(()) => 0,
                    Err(e) => error_code(&e),
                }
            } else {
                -1
            }
        } else {
            -3
        }
    })
    .unwrap_or(-3)
}
