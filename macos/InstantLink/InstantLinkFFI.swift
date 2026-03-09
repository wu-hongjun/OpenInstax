import Foundation

/// Direct FFI wrapper for `libinstantlink_ffi.dylib`.
///
/// Loads the bundled dylib via `dlopen` and exposes each C function as a
/// Swift async method. FFI calls block the calling thread (`block_on`),
/// so every call is dispatched to a background queue via `Task.detached`.
class InstantLinkFFI {
    private let handle: UnsafeMutableRawPointer

    // MARK: - Function pointers (resolved once at init)

    // Lifecycle
    private let _init: @convention(c) () -> Void
    private let _connect: @convention(c) () -> Int32
    private let _connect_named: @convention(c) (UnsafePointer<CChar>, Int32) -> Int32
    private let _disconnect: @convention(c) () -> Int32
    private let _is_connected: @convention(c) () -> Int32

    // Status queries
    private let _battery: @convention(c) () -> Int32
    private let _film_remaining: @convention(c) () -> Int32
    private let _film_and_charging: @convention(c) (UnsafeMutablePointer<Int32>, UnsafeMutablePointer<Int32>) -> Int32
    private let _print_count: @convention(c) () -> Int32
    private let _status: @convention(c) (UnsafeMutablePointer<Int32>, UnsafeMutablePointer<Int32>, UnsafeMutablePointer<Int32>, UnsafeMutablePointer<Int32>) -> Int32
    private let _device_name: @convention(c) (UnsafeMutablePointer<CChar>, Int32) -> Int32
    private let _device_model: @convention(c) (UnsafeMutablePointer<CChar>, Int32) -> Int32

    // Scanning
    private let _scan: @convention(c) (Int32, UnsafeMutablePointer<CChar>, Int32) -> Int32

    // Printing
    private let _print: @convention(c) (UnsafePointer<CChar>, UInt8, UInt8, UInt8) -> Int32
    private let _print_with_progress: @convention(c) (UnsafePointer<CChar>, UInt8, UInt8, UInt8, (@convention(c) (UInt32, UInt32) -> Void)?) -> Int32

    // LED
    private let _set_led: @convention(c) (UInt8, UInt8, UInt8, UInt8) -> Int32
    private let _led_off: @convention(c) () -> Int32

    // MARK: - Init

    init?() {
        // Look for the dylib in the app's Frameworks directory
        let bundle = Bundle.main
        let frameworksPath = bundle.bundlePath + "/Contents/Frameworks/libinstantlink_ffi.dylib"

        guard let h = dlopen(frameworksPath, RTLD_NOW) else {
            let err = String(cString: dlerror())
            print("[FFI] dlopen failed: \(err)")
            return nil
        }
        self.handle = h

        // Resolve all symbols
        guard let pInit = dlsym(h, "instantlink_init"),
              let pConnect = dlsym(h, "instantlink_connect"),
              let pConnectNamed = dlsym(h, "instantlink_connect_named"),
              let pDisconnect = dlsym(h, "instantlink_disconnect"),
              let pIsConnected = dlsym(h, "instantlink_is_connected"),
              let pBattery = dlsym(h, "instantlink_battery"),
              let pFilmRemaining = dlsym(h, "instantlink_film_remaining"),
              let pFilmAndCharging = dlsym(h, "instantlink_film_and_charging"),
              let pPrintCount = dlsym(h, "instantlink_print_count"),
              let pStatus = dlsym(h, "instantlink_status"),
              let pDeviceName = dlsym(h, "instantlink_device_name"),
              let pDeviceModel = dlsym(h, "instantlink_device_model"),
              let pScan = dlsym(h, "instantlink_scan"),
              let pPrint = dlsym(h, "instantlink_print"),
              let pPrintWithProgress = dlsym(h, "instantlink_print_with_progress"),
              let pSetLed = dlsym(h, "instantlink_set_led"),
              let pLedOff = dlsym(h, "instantlink_led_off")
        else {
            print("[FFI] Failed to resolve one or more symbols")
            dlclose(h)
            return nil
        }

        _init = unsafeBitCast(pInit, to: (@convention(c) () -> Void).self)
        _connect = unsafeBitCast(pConnect, to: (@convention(c) () -> Int32).self)
        _connect_named = unsafeBitCast(pConnectNamed, to: (@convention(c) (UnsafePointer<CChar>, Int32) -> Int32).self)
        _disconnect = unsafeBitCast(pDisconnect, to: (@convention(c) () -> Int32).self)
        _is_connected = unsafeBitCast(pIsConnected, to: (@convention(c) () -> Int32).self)
        _battery = unsafeBitCast(pBattery, to: (@convention(c) () -> Int32).self)
        _film_remaining = unsafeBitCast(pFilmRemaining, to: (@convention(c) () -> Int32).self)
        _film_and_charging = unsafeBitCast(pFilmAndCharging, to: (@convention(c) (UnsafeMutablePointer<Int32>, UnsafeMutablePointer<Int32>) -> Int32).self)
        _print_count = unsafeBitCast(pPrintCount, to: (@convention(c) () -> Int32).self)
        _status = unsafeBitCast(pStatus, to: (@convention(c) (UnsafeMutablePointer<Int32>, UnsafeMutablePointer<Int32>, UnsafeMutablePointer<Int32>, UnsafeMutablePointer<Int32>) -> Int32).self)
        _device_name = unsafeBitCast(pDeviceName, to: (@convention(c) (UnsafeMutablePointer<CChar>, Int32) -> Int32).self)
        _device_model = unsafeBitCast(pDeviceModel, to: (@convention(c) (UnsafeMutablePointer<CChar>, Int32) -> Int32).self)
        _scan = unsafeBitCast(pScan, to: (@convention(c) (Int32, UnsafeMutablePointer<CChar>, Int32) -> Int32).self)
        _print = unsafeBitCast(pPrint, to: (@convention(c) (UnsafePointer<CChar>, UInt8, UInt8, UInt8) -> Int32).self)
        _print_with_progress = unsafeBitCast(pPrintWithProgress, to: (@convention(c) (UnsafePointer<CChar>, UInt8, UInt8, UInt8, (@convention(c) (UInt32, UInt32) -> Void)?) -> Int32).self)
        _set_led = unsafeBitCast(pSetLed, to: (@convention(c) (UInt8, UInt8, UInt8, UInt8) -> Int32).self)
        _led_off = unsafeBitCast(pLedOff, to: (@convention(c) () -> Int32).self)

        // Initialize the runtime
        _init()
    }

    deinit {
        dlclose(handle)
    }

    // MARK: - Connection Lifecycle

    /// Connect to the first available printer.
    func connect() async -> Bool {
        await blocking { self._connect() == 0 }
    }

    /// Connect to a named printer with configurable scan duration.
    func connect(device: String, duration: Int = 5) async -> Bool {
        await blocking {
            device.withCString { cName in
                self._connect_named(cName, Int32(duration)) == 0
            }
        }
    }

    /// Disconnect from the current printer.
    func disconnect() async {
        _ = await blocking { self._disconnect() }
    }

    /// Synchronous disconnect for use in deinit/cleanup paths.
    func disconnectSync() {
        _ = _disconnect()
    }

    /// Check if a printer is currently connected (non-blocking).
    func isConnected() -> Bool {
        _is_connected() == 1
    }

    // MARK: - Status Queries

    /// Get battery level (0-100), or nil on error.
    func battery() async -> Int? {
        await blocking {
            let result = self._battery()
            return result >= 0 ? Int(result) : nil
        }
    }

    /// Get film remaining and charging state in one call.
    func filmAndCharging() async -> (film: Int, isCharging: Bool)? {
        await blocking {
            var film: Int32 = 0
            var charging: Int32 = 0
            let result = self._film_and_charging(&film, &charging)
            guard result == 0 else { return nil }
            return (film: Int(film), isCharging: charging != 0)
        }
    }

    /// Get total print count, or nil on error.
    func printCount() async -> Int? {
        await blocking {
            let result = self._print_count()
            return result >= 0 ? Int(result) : nil
        }
    }

    /// Combined status query — battery, film, charging, print count in one BLE round-trip.
    struct Status {
        let battery: Int
        let film: Int
        let isCharging: Bool
        let printCount: Int
    }

    func status() async -> Status? {
        await blocking {
            var battery: Int32 = 0
            var film: Int32 = 0
            var charging: Int32 = 0
            var printCount: Int32 = 0
            let result = self._status(&battery, &film, &charging, &printCount)
            guard result == 0 else { return nil }
            return Status(
                battery: Int(battery),
                film: Int(film),
                isCharging: charging != 0,
                printCount: Int(printCount)
            )
        }
    }

    /// Get the connected device's BLE name.
    func deviceName() -> String? {
        var buf = [CChar](repeating: 0, count: 256)
        let result = _device_name(&buf, Int32(buf.count))
        guard result > 0 else { return nil }
        return String(cString: buf)
    }

    /// Get the connected device's model string.
    func deviceModel() -> String? {
        var buf = [CChar](repeating: 0, count: 256)
        let result = _device_model(&buf, Int32(buf.count))
        guard result > 0 else { return nil }
        return String(cString: buf)
    }

    // MARK: - Scanning

    /// Scan for nearby printers, returning their BLE names.
    func scan(duration: Int = 5) async -> [String] {
        await blocking {
            let bufSize: Int32 = 4096
            var buf = [CChar](repeating: 0, count: Int(bufSize))
            let result = self._scan(Int32(duration), &buf, bufSize)
            guard result > 0 else { return [] }
            let json = String(cString: buf)
            guard let data = json.data(using: .utf8),
                  let names = try? JSONDecoder().decode([String].self, from: data)
            else { return [] }
            return names
        }
    }

    // MARK: - Printing

    /// Print an image file.
    ///
    /// - Parameters:
    ///   - path: Path to image file
    ///   - quality: JPEG quality 1-100
    ///   - fit: "crop" (0), "contain" (1), or "stretch" (2)
    ///   - printOption: 0 = Rich, 1 = Natural
    func printImage(path: String, quality: Int = 100, fit: String = "crop", printOption: Int = 0) async -> Bool {
        let fitMode: UInt8
        switch fit {
        case "contain": fitMode = 1
        case "stretch": fitMode = 2
        default: fitMode = 0 // crop
        }
        return await blocking {
            path.withCString { cPath in
                self._print(cPath, UInt8(quality), fitMode, UInt8(printOption)) == 0
            }
        }
    }

    /// Print an image file with progress callback.
    ///
    /// `progress` is called with (chunksSent, totalChunks) from a background thread.
    func printImage(path: String, quality: Int = 100, fit: String = "crop", printOption: Int = 0,
                    progress: @escaping @Sendable (UInt32, UInt32) -> Void) async -> Bool {
        let fitMode: UInt8
        switch fit {
        case "contain": fitMode = 1
        case "stretch": fitMode = 2
        default: fitMode = 0
        }

        // Store progress closure in a box so we can pass a C callback
        let box = ProgressBox(callback: progress)
        let boxPtr = Unmanaged.passRetained(box)

        // Set the global progress box for the C callback to use
        ProgressBox.current = boxPtr

        let cb: @convention(c) (UInt32, UInt32) -> Void = { sent, total in
            ProgressBox.current?.takeUnretainedValue().callback(sent, total)
        }

        let success = await blocking {
            path.withCString { cPath in
                self._print_with_progress(cPath, UInt8(quality), fitMode, UInt8(printOption), cb) == 0
            }
        }

        boxPtr.release()
        ProgressBox.current = nil
        return success
    }

    // MARK: - LED Control

    /// Set LED color and pattern.
    func setLed(r: UInt8, g: UInt8, b: UInt8, pattern: UInt8) async -> Bool {
        await blocking { self._set_led(r, g, b, pattern) == 0 }
    }

    /// Turn off the LED.
    func ledOff() async -> Bool {
        await blocking { self._led_off() == 0 }
    }

    // MARK: - Helper

    /// Run a blocking FFI call on a detached background thread.
    private func blocking<T: Sendable>(_ work: @escaping @Sendable () -> T) async -> T {
        await withCheckedContinuation { continuation in
            DispatchQueue.global(qos: .userInitiated).async {
                let result = work()
                continuation.resume(returning: result)
            }
        }
    }
}

/// Thread-safe box to bridge a Swift closure into a C callback context.
private final class ProgressBox: @unchecked Sendable {
    private static let lock = NSLock()
    private static var _current: Unmanaged<ProgressBox>?

    static var current: Unmanaged<ProgressBox>? {
        get {
            lock.lock()
            defer { lock.unlock() }
            return _current
        }
        set {
            lock.lock()
            defer { lock.unlock() }
            _current = newValue
        }
    }

    let callback: @Sendable (UInt32, UInt32) -> Void
    init(callback: @escaping @Sendable (UInt32, UInt32) -> Void) {
        self.callback = callback
    }
}
