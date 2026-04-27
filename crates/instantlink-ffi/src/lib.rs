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

use std::ffi::{CStr, CString, c_void};
use std::os::raw::c_char;
use std::sync::{Mutex, Once, OnceLock};
use std::time::Duration;

use instantlink_core::connect_progress::{ConnectProgressEvent, ConnectStage};
use instantlink_core::error::PrinterError;

static INIT: Once = Once::new();
static RUNTIME: OnceLock<tokio::runtime::Runtime> = OnceLock::new();
static DEVICE: OnceLock<Mutex<Option<Box<dyn instantlink_core::PrinterDevice>>>> = OnceLock::new();
const DISCONNECT_TIMEOUT: Duration = Duration::from_secs(3);

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

fn disconnect_device(
    rt: &tokio::runtime::Runtime,
    device: Box<dyn instantlink_core::PrinterDevice>,
) -> Result<(), PrinterError> {
    rt.block_on(async {
        tokio::time::timeout(DISCONNECT_TIMEOUT, device.disconnect())
            .await
            .map_err(|_| PrinterError::Timeout)?
    })
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
        PrinterError::PayloadTooLarge { .. } => -3,
    }
}

fn connect_stage_code(stage: ConnectStage) -> i32 {
    stage as i32
}

fn emit_connect_progress(
    progress_cb: Option<extern "C" fn(i32, *const c_char)>,
    event: ConnectProgressEvent,
) {
    let Some(progress_cb) = progress_cb else {
        return;
    };
    let detail = event.detail.and_then(|detail| CString::new(detail).ok());
    let detail_ptr = detail
        .as_ref()
        .map_or(std::ptr::null(), |detail| detail.as_ptr());
    progress_cb(connect_stage_code(event.stage), detail_ptr);
}

fn emit_connect_progress_with_context(
    progress_cb: Option<extern "C" fn(i32, *const c_char, *mut c_void)>,
    context: *mut c_void,
    event: ConnectProgressEvent,
) {
    let Some(progress_cb) = progress_cb else {
        return;
    };
    let detail = event.detail.and_then(|detail| CString::new(detail).ok());
    let detail_ptr = detail
        .as_ref()
        .map_or(std::ptr::null(), |detail| detail.as_ptr());
    progress_cb(connect_stage_code(event.stage), detail_ptr, context);
}

fn connect_named_internal(
    device_name: &str,
    duration: Option<Duration>,
    progress: Option<Box<dyn Fn(ConnectProgressEvent) + Send + Sync>>,
) -> i32 {
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
        let _ = disconnect_device(rt, old_device);
    }

    let progress_ref = progress
        .as_ref()
        .map(|callback| callback.as_ref() as &(dyn Fn(ConnectProgressEvent) + Send + Sync));
    match rt.block_on(instantlink_core::printer::connect_with_progress(
        device_name,
        duration,
        progress_ref,
    )) {
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
}

fn print_with_progress_internal(
    path: &str,
    quality: u8,
    fit_mode: u8,
    print_option: u8,
    progress: Option<Box<dyn Fn(usize, usize) + Send + Sync>>,
) -> i32 {
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
    let path = std::path::Path::new(path);
    let progress_ref = progress
        .as_ref()
        .map(|callback| callback.as_ref() as &(dyn Fn(usize, usize) + Send + Sync));
    let print_result =
        rt.block_on(device.print_file(path, fit, quality, print_option, progress_ref));

    if let Ok(mut guard) = lock.lock() {
        *guard = Some(device);
    } else {
        return -3;
    }

    match print_result {
        Ok(()) => 0,
        Err(e) => error_code(&e),
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
            let _ = disconnect_device(rt, old_device);
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
            let _ = disconnect_device(rt, old_device);
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

/// Connect to a specific printer by name with configurable scan duration and progress callback.
///
/// Progress callback stage codes:
/// 0 scan_started, 1 scan_finished, 2 device_matched, 3 ble_connecting,
/// 4 service_discovery, 5 characteristic_lookup, 6 notification_subscribe,
/// 7 model_detecting, 8 status_fetching, 9 connected, 10 failed.
///
/// # Safety
///
/// `name` must be a valid, non-null, null-terminated UTF-8 C string.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn instantlink_connect_named_with_progress(
    name: *const c_char,
    duration_secs: i32,
    progress_cb: Option<extern "C" fn(i32, *const c_char)>,
) -> i32 {
    std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        let s = match cstr_to_str(name) {
            Ok(s) => s,
            Err(code) => return code,
        };
        let duration = if duration_secs > 0 {
            Some(Duration::from_secs(duration_secs as u64))
        } else {
            None
        };
        let progress = progress_cb.map(|progress_cb| {
            Box::new(move |event: ConnectProgressEvent| {
                emit_connect_progress(Some(progress_cb), event)
            }) as Box<dyn Fn(ConnectProgressEvent) + Send + Sync>
        });
        connect_named_internal(s, duration, progress)
    }))
    .unwrap_or(-3)
}

/// Connect to a specific printer by name with configurable scan duration and progress callback
/// plus an opaque context pointer.
///
/// This is the context-safe variant used by the macOS app so callback state
/// remains per-call instead of global.
///
/// # Safety
///
/// `name` must be a valid, non-null, null-terminated UTF-8 C string.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn instantlink_connect_named_with_progress_ctx(
    name: *const c_char,
    duration_secs: i32,
    progress_cb: Option<extern "C" fn(i32, *const c_char, *mut c_void)>,
    context: *mut c_void,
) -> i32 {
    std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        let s = match cstr_to_str(name) {
            Ok(s) => s,
            Err(code) => return code,
        };
        let duration = if duration_secs > 0 {
            Some(Duration::from_secs(duration_secs as u64))
        } else {
            None
        };
        let context = context as usize;
        let progress = progress_cb.map(|progress_cb| {
            Box::new(move |event: ConnectProgressEvent| {
                emit_connect_progress_with_context(Some(progress_cb), context as *mut c_void, event)
            }) as Box<dyn Fn(ConnectProgressEvent) + Send + Sync>
        });
        connect_named_internal(s, duration, progress)
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
                match disconnect_device(rt, device) {
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

        let progress: Option<Box<dyn Fn(usize, usize) + Send + Sync>> = progress_cb.map(|cb| {
            Box::new(move |sent: usize, total: usize| {
                cb(sent as u32, total as u32);
            }) as Box<dyn Fn(usize, usize) + Send + Sync>
        });
        print_with_progress_internal(s, quality, fit_mode, print_option, progress)
    }))
    .unwrap_or(-3)
}

/// Print an image file with progress callback and opaque context pointer.
///
/// # Safety
///
/// `path` must be a valid, non-null, null-terminated UTF-8 C string.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn instantlink_print_with_progress_ctx(
    path: *const c_char,
    quality: u8,
    fit_mode: u8,
    print_option: u8,
    progress_cb: Option<extern "C" fn(u32, u32, *mut c_void)>,
    context: *mut c_void,
) -> i32 {
    std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        let s = match cstr_to_str(path) {
            Ok(s) => s,
            Err(code) => return code,
        };

        let context = context as usize;
        let progress: Option<Box<dyn Fn(usize, usize) + Send + Sync>> = progress_cb.map(|cb| {
            Box::new(move |sent: usize, total: usize| {
                cb(sent as u32, total as u32, context as *mut c_void);
            }) as Box<dyn Fn(usize, usize) + Send + Sync>
        });
        print_with_progress_internal(s, quality, fit_mode, print_option, progress)
    }))
    .unwrap_or(-3)
}

#[cfg(test)]
mod tests {
    use super::*;
    use instantlink_core::{FitMode, PrinterDevice, PrinterModel, PrinterStatus};
    use std::ffi::CString;
    use std::future::Future;
    use std::path::Path;
    use std::pin::Pin;
    use std::sync::atomic::{AtomicI32, AtomicU32, Ordering};
    use std::sync::{Arc, Mutex as StdMutex, MutexGuard};

    fn ffi_test_lock() -> &'static StdMutex<()> {
        static LOCK: OnceLock<StdMutex<()>> = OnceLock::new();
        LOCK.get_or_init(|| StdMutex::new(()))
    }

    struct TestDeviceGuard<'a> {
        _lock: MutexGuard<'a, ()>,
        previous: Option<Box<dyn PrinterDevice>>,
    }

    impl Drop for TestDeviceGuard<'_> {
        fn drop(&mut self) {
            let lock = get_device_lock();
            let mut guard = lock.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
            *guard = self.previous.take();
        }
    }

    fn install_test_device(device: Option<Box<dyn PrinterDevice>>) -> TestDeviceGuard<'static> {
        let lock_guard = ffi_test_lock()
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let lock = get_device_lock();
        let mut guard = lock.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
        let previous = guard.take();
        *guard = device;
        drop(guard);
        TestDeviceGuard {
            _lock: lock_guard,
            previous,
        }
    }

    #[derive(Debug, Clone, PartialEq, Eq)]
    struct PrintInvocation {
        path: String,
        fit: FitMode,
        quality: u8,
        print_option: u8,
        progress_enabled: bool,
    }

    #[derive(Debug, Default)]
    struct FakeFailures {
        status: Option<PrinterError>,
        battery: Option<PrinterError>,
        film_and_charging: Option<PrinterError>,
        print_count: Option<PrinterError>,
        print_file: Option<PrinterError>,
        set_led: Option<PrinterError>,
        disconnect: Option<PrinterError>,
    }

    struct FakeDevice {
        name: String,
        model: PrinterModel,
        status: PrinterStatus,
        print_calls: Arc<StdMutex<Vec<PrintInvocation>>>,
        led_calls: Arc<StdMutex<Vec<(u8, u8, u8, u8)>>>,
        shutdown_calls: Arc<AtomicU32>,
        reset_calls: Arc<AtomicU32>,
        failures: Arc<StdMutex<FakeFailures>>,
    }

    impl FakeDevice {
        fn new(name: &str, model: PrinterModel) -> Self {
            Self {
                name: name.to_string(),
                model,
                status: PrinterStatus {
                    battery: 82,
                    is_charging: true,
                    film_remaining: 7,
                    print_count: 123,
                    model,
                    name: name.to_string(),
                },
                print_calls: Arc::new(StdMutex::new(Vec::new())),
                led_calls: Arc::new(StdMutex::new(Vec::new())),
                shutdown_calls: Arc::new(AtomicU32::new(0)),
                reset_calls: Arc::new(AtomicU32::new(0)),
                failures: Arc::new(StdMutex::new(FakeFailures::default())),
            }
        }

        fn with_failures(self, failures: FakeFailures) -> Self {
            *self.failures.lock().unwrap() = failures;
            self
        }
    }

    impl PrinterDevice for FakeDevice {
        fn status<'life0, 'async_trait>(
            &'life0 self,
        ) -> Pin<
            Box<dyn Future<Output = instantlink_core::Result<PrinterStatus>> + Send + 'async_trait>,
        >
        where
            'life0: 'async_trait,
            Self: 'async_trait,
        {
            let result = self
                .failures
                .lock()
                .unwrap()
                .status
                .take()
                .map_or_else(|| Ok(self.status.clone()), Err);
            Box::pin(std::future::ready(result))
        }

        fn battery<'life0, 'async_trait>(
            &'life0 self,
        ) -> Pin<Box<dyn Future<Output = instantlink_core::Result<u8>> + Send + 'async_trait>>
        where
            'life0: 'async_trait,
            Self: 'async_trait,
        {
            let result = self
                .failures
                .lock()
                .unwrap()
                .battery
                .take()
                .map_or_else(|| Ok(self.status.battery), Err);
            Box::pin(std::future::ready(result))
        }

        fn film_and_charging<'life0, 'async_trait>(
            &'life0 self,
        ) -> Pin<Box<dyn Future<Output = instantlink_core::Result<(u8, bool)>> + Send + 'async_trait>>
        where
            'life0: 'async_trait,
            Self: 'async_trait,
        {
            let result = self
                .failures
                .lock()
                .unwrap()
                .film_and_charging
                .take()
                .map_or_else(
                    || Ok((self.status.film_remaining, self.status.is_charging)),
                    Err,
                );
            Box::pin(std::future::ready(result))
        }

        fn print_count<'life0, 'async_trait>(
            &'life0 self,
        ) -> Pin<Box<dyn Future<Output = instantlink_core::Result<u16>> + Send + 'async_trait>>
        where
            'life0: 'async_trait,
            Self: 'async_trait,
        {
            let result = self
                .failures
                .lock()
                .unwrap()
                .print_count
                .take()
                .map_or_else(|| Ok(self.status.print_count), Err);
            Box::pin(std::future::ready(result))
        }

        fn model(&self) -> PrinterModel {
            self.model
        }

        fn name(&self) -> &str {
            &self.name
        }

        fn print_file<'life0, 'life1, 'life2, 'async_trait>(
            &'life0 self,
            _path: &'life1 Path,
            _fit: FitMode,
            _quality: u8,
            _print_option: u8,
            _progress: Option<&'life2 (dyn Fn(usize, usize) + Send + Sync)>,
        ) -> Pin<Box<dyn Future<Output = instantlink_core::Result<()>> + Send + 'async_trait>>
        where
            'life0: 'async_trait,
            'life1: 'async_trait,
            'life2: 'async_trait,
            Self: 'async_trait,
        {
            self.print_calls.lock().unwrap().push(PrintInvocation {
                path: _path.display().to_string(),
                fit: _fit,
                quality: _quality,
                print_option: _print_option,
                progress_enabled: _progress.is_some(),
            });
            if let Some(progress) = _progress {
                progress(2, 5);
            }
            let result = self
                .failures
                .lock()
                .unwrap()
                .print_file
                .take()
                .map_or(Ok(()), Err);
            Box::pin(std::future::ready(result))
        }

        fn print_bytes<'life0, 'life1, 'life2, 'async_trait>(
            &'life0 self,
            _data: &'life1 [u8],
            _fit: FitMode,
            _quality: u8,
            _print_option: u8,
            _progress: Option<&'life2 (dyn Fn(usize, usize) + Send + Sync)>,
        ) -> Pin<Box<dyn Future<Output = instantlink_core::Result<()>> + Send + 'async_trait>>
        where
            'life0: 'async_trait,
            'life1: 'async_trait,
            'life2: 'async_trait,
            Self: 'async_trait,
        {
            Box::pin(async move { Ok(()) })
        }

        fn set_led<'life0, 'async_trait>(
            &'life0 self,
            _r: u8,
            _g: u8,
            _b: u8,
            _pattern: u8,
        ) -> Pin<Box<dyn Future<Output = instantlink_core::Result<()>> + Send + 'async_trait>>
        where
            'life0: 'async_trait,
            Self: 'async_trait,
        {
            let led_calls = Arc::clone(&self.led_calls);
            let result = self.failures.lock().unwrap().set_led.take();
            Box::pin(async move {
                led_calls.lock().unwrap().push((_r, _g, _b, _pattern));
                match result {
                    Some(err) => Err(err),
                    None => Ok(()),
                }
            })
        }

        fn shutdown<'life0, 'async_trait>(
            &'life0 self,
        ) -> Pin<Box<dyn Future<Output = instantlink_core::Result<()>> + Send + 'async_trait>>
        where
            'life0: 'async_trait,
            Self: 'async_trait,
        {
            let shutdown_calls = Arc::clone(&self.shutdown_calls);
            Box::pin(async move {
                shutdown_calls.fetch_add(1, Ordering::SeqCst);
                Ok(())
            })
        }

        fn reset<'life0, 'async_trait>(
            &'life0 self,
        ) -> Pin<Box<dyn Future<Output = instantlink_core::Result<()>> + Send + 'async_trait>>
        where
            'life0: 'async_trait,
            Self: 'async_trait,
        {
            let reset_calls = Arc::clone(&self.reset_calls);
            Box::pin(async move {
                reset_calls.fetch_add(1, Ordering::SeqCst);
                Ok(())
            })
        }

        fn disconnect<'life0, 'async_trait>(
            &'life0 self,
        ) -> Pin<Box<dyn Future<Output = instantlink_core::Result<()>> + Send + 'async_trait>>
        where
            'life0: 'async_trait,
            Self: 'async_trait,
        {
            let result = self.failures.lock().unwrap().disconnect.take();
            Box::pin(async move {
                match result {
                    Some(err) => Err(err),
                    None => Ok(()),
                }
            })
        }
    }

    extern "C" fn connect_progress_recorder(
        stage: i32,
        detail: *const c_char,
        context: *mut c_void,
    ) {
        let stage_slot = unsafe { &*(context as *const AtomicI32) };
        stage_slot.store(stage, Ordering::SeqCst);
        assert!(!detail.is_null());
    }

    extern "C" fn connect_progress_noop(_stage: i32, _detail: *const c_char) {}

    extern "C" fn connect_progress_with_context_noop(
        _stage: i32,
        _detail: *const c_char,
        _context: *mut c_void,
    ) {
    }

    extern "C" fn print_progress_recorder(sent: u32, total: u32, context: *mut c_void) {
        let slots = unsafe { &*(context as *const (AtomicU32, AtomicU32)) };
        slots.0.store(sent, Ordering::SeqCst);
        slots.1.store(total, Ordering::SeqCst);
    }

    #[test]
    fn emit_connect_progress_with_context_passes_stage_and_detail() {
        let stage_slot = AtomicI32::new(-1);
        emit_connect_progress_with_context(
            Some(connect_progress_recorder),
            (&stage_slot as *const AtomicI32).cast_mut().cast(),
            ConnectProgressEvent {
                stage: ConnectStage::DeviceMatched,
                detail: Some("INSTAX-12345678".into()),
            },
        );
        assert_eq!(
            stage_slot.load(Ordering::SeqCst),
            connect_stage_code(ConnectStage::DeviceMatched)
        );
    }

    #[test]
    fn print_progress_context_callback_can_observe_context() {
        let slots = (AtomicU32::new(0), AtomicU32::new(0));
        print_progress_recorder(
            7,
            52,
            (&slots as *const (AtomicU32, AtomicU32)).cast_mut().cast(),
        );
        assert_eq!(slots.0.load(Ordering::SeqCst), 7);
        assert_eq!(slots.1.load(Ordering::SeqCst), 52);
    }

    #[test]
    fn ffi_scan_rejects_null_output_buffer() {
        let _guard = install_test_device(None);
        let code = unsafe { instantlink_scan(0, std::ptr::null_mut(), 16) };
        assert_eq!(code, -5);
    }

    #[test]
    fn ffi_string_helpers_reject_invalid_inputs() {
        assert_eq!(unsafe { cstr_to_str(std::ptr::null()) }, Err(-5));

        let invalid = [0x66_u8 as c_char, -1_i8 as c_char, 0];
        assert_eq!(unsafe { cstr_to_str(invalid.as_ptr()) }, Err(-5));

        let mut buffer = [0 as c_char; 4];
        assert_eq!(
            unsafe { write_str_to_buf("abc", std::ptr::null_mut(), 8) },
            -5
        );
        assert_eq!(
            unsafe { write_str_to_buf("abc", buffer.as_mut_ptr(), 0) },
            -5
        );
    }

    #[test]
    fn write_str_to_buf_truncates_and_nul_terminates() {
        let mut buffer = [b'x' as c_char; 4];
        let written = unsafe { write_str_to_buf("printer", buffer.as_mut_ptr(), 4) };
        assert_eq!(written, 3);
        assert_eq!(buffer[3], 0);
        let bytes: Vec<u8> = buffer[..3].iter().map(|byte| *byte as u8).collect();
        assert_eq!(String::from_utf8(bytes).unwrap(), "pri");
    }

    #[test]
    fn ffi_status_rejects_null_pointers() {
        let _guard = install_test_device(None);
        let mut battery = 0;
        let mut film = 0;
        let mut charging = 0;
        let code = unsafe {
            instantlink_status(&mut battery, &mut film, &mut charging, std::ptr::null_mut())
        };
        assert_eq!(code, -5);
    }

    #[test]
    fn ffi_status_returns_not_found_without_device() {
        let _guard = install_test_device(None);
        let (mut battery, mut film, mut charging, mut print_count) = (0, 0, 0, 0);
        let code =
            unsafe { instantlink_status(&mut battery, &mut film, &mut charging, &mut print_count) };
        assert_eq!(code, -1);
    }

    #[test]
    fn ffi_status_writes_outputs_for_connected_device() {
        let _guard = install_test_device(Some(Box::new(FakeDevice::new(
            "INSTAX-12345678",
            PrinterModel::MiniLink3,
        ))));
        let (mut battery, mut film, mut charging, mut print_count) = (0, 0, 0, 0);
        let code =
            unsafe { instantlink_status(&mut battery, &mut film, &mut charging, &mut print_count) };
        assert_eq!(code, 0);
        assert_eq!((battery, film, charging, print_count), (82, 7, 1, 123));
    }

    #[test]
    fn ffi_simple_getters_return_connected_values() {
        let _guard = install_test_device(Some(Box::new(FakeDevice::new(
            "INSTAX-12345678",
            PrinterModel::MiniLink3,
        ))));

        assert_eq!(instantlink_battery(), 82);
        assert_eq!(instantlink_film_remaining(), 7);
        assert_eq!(instantlink_print_count(), 123);

        let (mut film, mut charging) = (0, 0);
        let code = unsafe { instantlink_film_and_charging(&mut film, &mut charging) };
        assert_eq!(code, 0);
        assert_eq!((film, charging), (7, 1));
    }

    #[test]
    fn ffi_simple_getters_require_a_connected_device() {
        let _guard = install_test_device(None);
        assert_eq!(instantlink_battery(), -1);
        assert_eq!(instantlink_film_remaining(), -1);
        assert_eq!(instantlink_print_count(), -1);

        let (mut film, mut charging) = (0, 0);
        let code = unsafe { instantlink_film_and_charging(&mut film, &mut charging) };
        assert_eq!(code, -1);
    }

    #[test]
    fn ffi_getters_map_device_errors() {
        let device =
            FakeDevice::new("INSTAX-12345678", PrinterModel::Mini).with_failures(FakeFailures {
                battery: Some(PrinterError::LowBattery { percent: 4 }),
                film_and_charging: Some(PrinterError::NoFilm),
                print_count: Some(PrinterError::PrinterBusy),
                status: Some(PrinterError::CoverOpen),
                ..FakeFailures::default()
            });
        let _guard = install_test_device(Some(Box::new(device)));

        assert_eq!(instantlink_battery(), -9);
        assert_eq!(instantlink_film_remaining(), -8);
        assert_eq!(instantlink_print_count(), -11);

        let (mut battery, mut film, mut charging, mut print_count) = (0, 0, 0, 0);
        let status_code =
            unsafe { instantlink_status(&mut battery, &mut film, &mut charging, &mut print_count) };
        assert_eq!(status_code, -10);
    }

    #[test]
    fn ffi_device_name_truncates_and_nul_terminates() {
        let _guard = install_test_device(Some(Box::new(FakeDevice::new(
            "INSTAX-12345678",
            PrinterModel::Mini,
        ))));
        let mut buffer = [b'x' as c_char; 8];
        let written = unsafe { instantlink_device_name(buffer.as_mut_ptr(), buffer.len() as i32) };
        assert_eq!(written, 7);
        assert_eq!(buffer[7], 0);
        let bytes: Vec<u8> = buffer[..7].iter().map(|byte| *byte as u8).collect();
        assert_eq!(String::from_utf8(bytes).unwrap(), "INSTAX-");
    }

    #[test]
    fn ffi_device_model_writes_model_name() {
        let _guard = install_test_device(Some(Box::new(FakeDevice::new(
            "INSTAX-12345678",
            PrinterModel::MiniLink3,
        ))));
        let mut buffer = [0 as c_char; 32];
        let written = unsafe { instantlink_device_model(buffer.as_mut_ptr(), buffer.len() as i32) };
        assert!(written > 0);
        let bytes: Vec<u8> = buffer[..written as usize]
            .iter()
            .map(|byte| *byte as u8)
            .collect();
        assert_eq!(String::from_utf8(bytes).unwrap(), "Instax Mini Link 3");
    }

    #[test]
    fn ffi_device_name_and_model_validate_buffers_and_connection() {
        let mut buffer = [0 as c_char; 8];
        {
            let _guard = install_test_device(None);
            assert_eq!(
                unsafe { instantlink_device_name(buffer.as_mut_ptr(), 8) },
                -1
            );
            assert_eq!(
                unsafe { instantlink_device_model(buffer.as_mut_ptr(), 8) },
                -1
            );
        }

        let _guard = install_test_device(Some(Box::new(FakeDevice::new(
            "INSTAX-12345678",
            PrinterModel::Mini,
        ))));
        assert_eq!(
            unsafe { instantlink_device_name(std::ptr::null_mut(), 8) },
            -5
        );
        assert_eq!(
            unsafe { instantlink_device_model(std::ptr::null_mut(), 8) },
            -5
        );
    }

    #[test]
    fn ffi_disconnect_returns_not_found_when_no_device() {
        let _guard = install_test_device(None);
        assert_eq!(instantlink_disconnect(), -1);
    }

    #[test]
    fn ffi_disconnect_surfaces_device_error_codes() {
        let device =
            FakeDevice::new("INSTAX-12345678", PrinterModel::Mini).with_failures(FakeFailures {
                disconnect: Some(PrinterError::Timeout),
                ..FakeFailures::default()
            });
        let _guard = install_test_device(Some(Box::new(device)));
        assert_eq!(instantlink_disconnect(), -4);
    }

    #[test]
    fn ffi_is_connected_reflects_device_presence() {
        let _guard = install_test_device(Some(Box::new(FakeDevice::new(
            "INSTAX-12345678",
            PrinterModel::Mini,
        ))));
        assert_eq!(instantlink_is_connected(), 1);
    }

    #[test]
    fn ffi_is_connected_returns_zero_without_device() {
        let _guard = install_test_device(None);
        assert_eq!(instantlink_is_connected(), 0);
    }

    #[test]
    fn ffi_error_code_mapping_is_stable() {
        assert_eq!(error_code(&PrinterError::PrinterNotFound), -1);
        assert_eq!(error_code(&PrinterError::MultiplePrinters { count: 2 }), -2);
        assert_eq!(error_code(&PrinterError::Timeout), -4);
        assert_eq!(error_code(&PrinterError::NoFilm), -8);
        assert_eq!(error_code(&PrinterError::LowBattery { percent: 10 }), -9);
        assert_eq!(error_code(&PrinterError::CoverOpen), -10);
        assert_eq!(error_code(&PrinterError::PrinterBusy), -11);
        assert_eq!(
            error_code(&PrinterError::UnexpectedResponse("bad packet".into())),
            -7
        );
    }

    #[test]
    fn ffi_connect_progress_wrapper_accepts_null_callback() {
        let _guard = install_test_device(None);
        let code = unsafe { instantlink_connect_named_with_progress(std::ptr::null(), 0, None) };
        assert_eq!(code, -5);
    }

    #[test]
    fn ffi_connect_named_variants_reject_invalid_utf8() {
        let invalid = [0x66_u8 as c_char, -1_i8 as c_char, 0];
        let _guard = install_test_device(None);
        assert_eq!(
            unsafe { instantlink_connect_named(invalid.as_ptr(), 0) },
            -5
        );
        assert_eq!(
            unsafe {
                instantlink_connect_named_with_progress(
                    invalid.as_ptr(),
                    0,
                    Some(connect_progress_noop),
                )
            },
            -5
        );
        assert_eq!(
            unsafe {
                instantlink_connect_named_with_progress_ctx(
                    invalid.as_ptr(),
                    0,
                    Some(connect_progress_with_context_noop),
                    std::ptr::null_mut(),
                )
            },
            -5
        );
    }

    #[test]
    fn ffi_print_progress_wrapper_accepts_null_callback_without_device() {
        let _guard = install_test_device(None);
        let path = CString::new("photo.jpg").unwrap();
        let code = unsafe { instantlink_print_with_progress(path.as_ptr(), 97, 0, 0, None) };
        assert_eq!(code, -1);
    }

    #[test]
    fn ffi_print_progress_ctx_accepts_null_callback_without_device() {
        let _guard = install_test_device(None);
        let path = CString::new("photo.jpg").unwrap();
        let code = unsafe {
            instantlink_print_with_progress_ctx(path.as_ptr(), 97, 0, 0, None, std::ptr::null_mut())
        };
        assert_eq!(code, -1);
    }

    #[test]
    fn ffi_print_variants_reject_invalid_utf8_paths() {
        let invalid = [0x66_u8 as c_char, -1_i8 as c_char, 0];
        let _guard = install_test_device(None);
        assert_eq!(unsafe { instantlink_print(invalid.as_ptr(), 97, 0, 0) }, -5);
        assert_eq!(
            unsafe { instantlink_print_with_progress(invalid.as_ptr(), 97, 0, 0, None) },
            -5
        );
        assert_eq!(
            unsafe {
                instantlink_print_with_progress_ctx(
                    invalid.as_ptr(),
                    97,
                    0,
                    0,
                    Some(print_progress_recorder),
                    std::ptr::null_mut(),
                )
            },
            -5
        );
    }

    #[test]
    fn ffi_print_with_progress_ctx_maps_fit_and_reports_progress() {
        let device = FakeDevice::new("INSTAX-12345678", PrinterModel::Square);
        let print_calls = Arc::clone(&device.print_calls);
        let _guard = install_test_device(Some(Box::new(device)));
        let path = CString::new("photo.jpg").unwrap();
        let progress = (AtomicU32::new(0), AtomicU32::new(0));
        let code = unsafe {
            instantlink_print_with_progress_ctx(
                path.as_ptr(),
                88,
                2,
                1,
                Some(print_progress_recorder),
                (&progress as *const (AtomicU32, AtomicU32))
                    .cast_mut()
                    .cast(),
            )
        };
        assert_eq!(code, 0);
        assert_eq!(progress.0.load(Ordering::SeqCst), 2);
        assert_eq!(progress.1.load(Ordering::SeqCst), 5);
        let recorded = print_calls.lock().unwrap().clone();
        assert_eq!(
            recorded,
            vec![PrintInvocation {
                path: "photo.jpg".to_string(),
                fit: FitMode::Stretch,
                quality: 88,
                print_option: 1,
                progress_enabled: true,
            }]
        );
        assert_eq!(instantlink_is_connected(), 1);
    }

    #[test]
    fn ffi_print_and_led_calls_surface_device_errors() {
        let device =
            FakeDevice::new("INSTAX-12345678", PrinterModel::Mini).with_failures(FakeFailures {
                print_file: Some(PrinterError::NoFilm),
                set_led: Some(PrinterError::PrinterBusy),
                ..FakeFailures::default()
            });
        let print_calls = Arc::clone(&device.print_calls);
        let led_calls = Arc::clone(&device.led_calls);
        let _guard = install_test_device(Some(Box::new(device)));
        let path = CString::new("photo.jpg").unwrap();

        assert_eq!(unsafe { instantlink_print(path.as_ptr(), 90, 1, 0) }, -8);
        assert_eq!(instantlink_set_led(255, 0, 0, 2), -11);
        assert_eq!(instantlink_is_connected(), 1);

        assert_eq!(
            print_calls.lock().unwrap().clone(),
            vec![PrintInvocation {
                path: "photo.jpg".to_string(),
                fit: FitMode::Contain,
                quality: 90,
                print_option: 0,
                progress_enabled: false,
            }]
        );
        assert_eq!(led_calls.lock().unwrap().clone(), vec![(255, 0, 0, 2)]);
    }

    #[test]
    fn ffi_led_shutdown_and_reset_delegate_to_connected_device() {
        let device = FakeDevice::new("INSTAX-12345678", PrinterModel::Mini);
        let led_calls = Arc::clone(&device.led_calls);
        let shutdown_calls = Arc::clone(&device.shutdown_calls);
        let reset_calls = Arc::clone(&device.reset_calls);
        let _guard = install_test_device(Some(Box::new(device)));

        assert_eq!(instantlink_set_led(255, 128, 64, 2), 0);
        assert_eq!(instantlink_led_off(), 0);
        assert_eq!(instantlink_shutdown(), 0);
        assert_eq!(instantlink_reset(), 0);

        let recorded_led_calls = led_calls.lock().unwrap().clone();
        assert_eq!(recorded_led_calls, vec![(255, 128, 64, 2), (0, 0, 0, 0)]);
        assert_eq!(shutdown_calls.load(Ordering::SeqCst), 1);
        assert_eq!(reset_calls.load(Ordering::SeqCst), 1);
    }
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
