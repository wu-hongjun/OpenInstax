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

use std::ffi::CStr;
use std::os::raw::c_char;
use std::sync::{Mutex, Once, OnceLock};

use openinstax_core::error::InstaxError;

static INIT: Once = Once::new();
static RUNTIME: OnceLock<tokio::runtime::Runtime> = OnceLock::new();
static DEVICE: OnceLock<Mutex<Option<Box<dyn openinstax_core::InstaxDevice>>>> = OnceLock::new();

fn get_runtime() -> &'static tokio::runtime::Runtime {
    RUNTIME.get_or_init(|| {
        tokio::runtime::Builder::new_multi_thread()
            .enable_all()
            .build()
            .expect("failed to create tokio runtime")
    })
}

fn get_device_lock() -> &'static Mutex<Option<Box<dyn openinstax_core::InstaxDevice>>> {
    DEVICE.get_or_init(|| Mutex::new(None))
}

/// Map an [`InstaxError`] to an FFI error code.
fn error_code(e: &InstaxError) -> i32 {
    match e {
        InstaxError::PrinterNotFound => -1,
        InstaxError::MultiplePrinters { .. } => -2,
        InstaxError::Ble(_) => -3,
        InstaxError::Timeout => -4,
        InstaxError::Image(_) | InstaxError::ImageTooLarge { .. } => -6,
        InstaxError::PrintRejected(_) | InstaxError::UnexpectedResponse(_) => -7,
        InstaxError::NoFilm => -8,
        InstaxError::LowBattery { .. } => -9,
        InstaxError::Protocol(_) => -3,
        InstaxError::Io(_) => -3,
    }
}

/// Initialize the FFI layer (logging + runtime). Safe to call multiple times.
#[no_mangle]
pub extern "C" fn openinstax_init() {
    INIT.call_once(|| {
        let _ = env_logger::try_init();
        let _ = get_runtime();
    });
}

/// Connect to the first available Instax printer.
/// Returns 0 on success, negative error code on failure.
#[no_mangle]
pub extern "C" fn openinstax_connect() -> i32 {
    std::panic::catch_unwind(|| {
        let rt = get_runtime();
        match rt.block_on(openinstax_core::printer::connect_any(None)) {
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

/// Connect to a specific printer by name.
///
/// # Safety
///
/// `name` must be a valid, non-null, null-terminated UTF-8 C string.
#[no_mangle]
pub unsafe extern "C" fn openinstax_connect_named(name: *const c_char) -> i32 {
    std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        if name.is_null() {
            return -5;
        }
        let c_str = CStr::from_ptr(name);
        let s = match c_str.to_str() {
            Ok(s) => s,
            Err(_) => return -5,
        };
        let rt = get_runtime();
        match rt.block_on(openinstax_core::printer::connect(s, None)) {
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
#[no_mangle]
pub extern "C" fn openinstax_disconnect() -> i32 {
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
#[no_mangle]
pub extern "C" fn openinstax_battery() -> i32 {
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
#[no_mangle]
pub extern "C" fn openinstax_film_remaining() -> i32 {
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

/// Print an image file. Returns 0 on success, negative error code on failure.
///
/// # Safety
///
/// `path` must be a valid, non-null, null-terminated UTF-8 C string.
#[no_mangle]
pub unsafe extern "C" fn openinstax_print(path: *const c_char, quality: u8, fit_mode: u8) -> i32 {
    std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        if path.is_null() {
            return -5;
        }
        let c_str = CStr::from_ptr(path);
        let s = match c_str.to_str() {
            Ok(s) => s,
            Err(_) => return -5,
        };

        let fit = match fit_mode {
            1 => openinstax_core::FitMode::Contain,
            2 => openinstax_core::FitMode::Stretch,
            _ => openinstax_core::FitMode::Crop,
        };

        let lock = get_device_lock();
        if let Ok(guard) = lock.lock() {
            if let Some(ref device) = *guard {
                let rt = get_runtime();
                let path = std::path::Path::new(s);
                match rt.block_on(device.print_file(path, fit, quality, None)) {
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

/// Set LED color and pattern.
#[no_mangle]
pub extern "C" fn openinstax_set_led(r: u8, g: u8, b: u8, pattern: u8) -> i32 {
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
#[no_mangle]
pub extern "C" fn openinstax_led_off() -> i32 {
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
/// Returns 1 if connected, 0 if not.
#[no_mangle]
pub extern "C" fn openinstax_is_connected() -> i32 {
    std::panic::catch_unwind(|| {
        let lock = get_device_lock();
        if let Ok(guard) = lock.lock() {
            i32::from(guard.is_some())
        } else {
            0
        }
    })
    .unwrap_or(0)
}
