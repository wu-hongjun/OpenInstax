import Foundation

/// Process wrapper for the bundled InstantLink CLI binary.
/// Mirrors the StatusLight pattern: the macOS app bundles the CLI
/// and calls it via Process for all printer operations.
class InstantLinkCLI {
    /// Path to the bundled CLI binary.
    private let cliPath: String

    init() {
        // Look for the CLI binary next to the app executable.
        // The CLI is renamed to instantlink-cli inside the bundle to avoid
        // case-insensitive collision with the SwiftUI launcher (InstantLink).
        let bundle = Bundle.main
        if let path = bundle.path(forAuxiliaryExecutable: "instantlink-cli") {
            self.cliPath = path
        } else {
            // Fallback: assume it's in the same directory
            let execDir = bundle.bundlePath + "/Contents/MacOS"
            self.cliPath = execDir + "/instantlink-cli"
        }
    }

    // MARK: - Scanning

    /// Scan for nearby Instax printers.
    func scan(duration: Int = 5) async -> [String] {
        guard let output = await run(["scan", "--json", "--duration", "\(duration)"]) else { return [] }
        guard let data = output.data(using: .utf8),
              let names = try? JSONDecoder().decode([String].self, from: data) else {
            return []
        }
        return names
    }

    // MARK: - Printer Info

    struct PrinterInfo: Codable {
        let name: String
        let model: String
        let battery: Int
        let isCharging: Bool
        let filmRemaining: Int
        let printCount: Int

        enum CodingKeys: String, CodingKey {
            case name, model, battery
            case isCharging = "is_charging"
            case filmRemaining = "film_remaining"
            case printCount = "print_count"
        }
    }

    /// Get printer info (battery, film, model, print count).
    func info(device: String? = nil, duration: Int = 5) async -> PrinterInfo? {
        return await info(device: device, duration: duration, progress: nil)
    }

    /// Get printer info with progress updates via stderr.
    func info(device: String? = nil, duration: Int = 5, progress: ((String) -> Void)?) async -> PrinterInfo? {
        var args = ["info", "--json", "--duration", "\(duration)"]
        if let device = device {
            args += ["--device", device]
        }
        // BLE scan + connect + multiple reads can take a while
        guard let output = await runWithProgress(args, timeout: 30, progress: progress),
              let data = output.data(using: .utf8) else { return nil }
        return try? JSONDecoder().decode(PrinterInfo.self, from: data)
    }

    // MARK: - Printing

    /// Print an image file.
    func printImage(path: String, quality: Int = 97, fit: String = "crop", device: String? = nil) async -> Bool {
        var args = ["print", path, "--quality", "\(quality)", "--fit", fit, "--json"]
        if let device = device {
            args += ["--device", device]
        }
        let output = await run(args, timeout: 120)
        return output != nil
    }

    // MARK: - LED Control

    /// Set the LED color and pattern.
    func setLed(color: String, pattern: String = "solid", device: String? = nil) async -> Bool {
        var args = ["led", "set", color, "--pattern", pattern]
        if let device = device {
            args += ["--device", device]
        }
        let output = await run(args)
        return output != nil
    }

    /// Turn off the LED.
    func ledOff(device: String? = nil) async -> Bool {
        var args = ["led", "off"]
        if let device = device {
            args += ["--device", device]
        }
        let output = await run(args)
        return output != nil
    }

    // MARK: - Status

    struct StatusInfo: Codable {
        let connected: Bool
        let name: String?
        let model: String?
        let battery: Int?
        let isCharging: Bool?
        let filmRemaining: Int?
        let printCount: Int?

        enum CodingKeys: String, CodingKey {
            case connected, name, model, battery
            case isCharging = "is_charging"
            case filmRemaining = "film_remaining"
            case printCount = "print_count"
        }
    }

    /// Get printer status (connectivity + info).
    func status(device: String? = nil) async -> StatusInfo? {
        var args = ["status", "--json"]
        if let device = device {
            args += ["--device", device]
        }
        guard let output = await run(args),
              let data = output.data(using: .utf8) else { return nil }
        return try? JSONDecoder().decode(StatusInfo.self, from: data)
    }

    // MARK: - Process Runner

    /// Run the CLI with the given arguments and return stdout.
    private func run(_ arguments: [String], timeout: TimeInterval = 15) async -> String? {
        return await withCheckedContinuation { continuation in
            DispatchQueue.global(qos: .userInitiated).async { [cliPath] in
                let process = Process()
                process.executableURL = URL(fileURLWithPath: cliPath)
                process.arguments = arguments

                let stdout = Pipe()
                let stderr = Pipe()
                process.standardOutput = stdout
                process.standardError = stderr

                // Watchdog timer
                let timer = DispatchSource.makeTimerSource(queue: .global())
                timer.schedule(deadline: .now() + timeout)
                timer.setEventHandler {
                    if process.isRunning {
                        process.terminate()
                    }
                }
                timer.resume()

                do {
                    try process.run()
                    process.waitUntilExit()
                } catch {
                    timer.cancel()
                    continuation.resume(returning: nil)
                    return
                }

                timer.cancel()

                guard process.terminationStatus == 0 else {
                    continuation.resume(returning: nil)
                    return
                }

                let data = stdout.fileHandleForReading.readDataToEndOfFile()
                let output = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines)
                continuation.resume(returning: output)
            }
        }
    }

    /// Run the CLI with real-time stderr progress reporting.
    private func runWithProgress(_ arguments: [String], timeout: TimeInterval = 15, progress: ((String) -> Void)?) async -> String? {
        return await withCheckedContinuation { continuation in
            DispatchQueue.global(qos: .userInitiated).async { [cliPath] in
                let process = Process()
                process.executableURL = URL(fileURLWithPath: cliPath)
                process.arguments = arguments

                let stdout = Pipe()
                let stderr = Pipe()
                process.standardOutput = stdout
                process.standardError = stderr

                // Stream stderr lines for progress updates
                if let progress = progress {
                    stderr.fileHandleForReading.readabilityHandler = { handle in
                        let data = handle.availableData
                        guard !data.isEmpty,
                              let line = String(data: data, encoding: .utf8) else { return }
                        for part in line.components(separatedBy: "\n") {
                            let trimmed = part.trimmingCharacters(in: .whitespacesAndNewlines)
                            if trimmed.hasPrefix("progress: ") {
                                let msg = String(trimmed.dropFirst("progress: ".count))
                                DispatchQueue.main.async { progress(msg) }
                            }
                        }
                    }
                }

                // Watchdog timer
                let timer = DispatchSource.makeTimerSource(queue: .global())
                timer.schedule(deadline: .now() + timeout)
                timer.setEventHandler {
                    if process.isRunning {
                        process.terminate()
                    }
                }
                timer.resume()

                do {
                    try process.run()
                    process.waitUntilExit()
                } catch {
                    timer.cancel()
                    stderr.fileHandleForReading.readabilityHandler = nil
                    continuation.resume(returning: nil)
                    return
                }

                timer.cancel()
                stderr.fileHandleForReading.readabilityHandler = nil

                guard process.terminationStatus == 0 else {
                    continuation.resume(returning: nil)
                    return
                }

                let data = stdout.fileHandleForReading.readDataToEndOfFile()
                let output = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines)
                continuation.resume(returning: output)
            }
        }
    }
}
