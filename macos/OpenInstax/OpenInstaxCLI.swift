import Foundation

/// Process wrapper for the bundled OpenInstax CLI binary.
/// Mirrors the StatusLight pattern: the macOS app bundles the CLI
/// and calls it via Process for all printer operations.
class OpenInstaxCLI {
    /// Path to the bundled CLI binary.
    private let cliPath: String

    init() {
        // Look for the CLI binary next to the app executable
        let bundle = Bundle.main
        if let path = bundle.path(forAuxiliaryExecutable: "openinstax") {
            self.cliPath = path
        } else {
            // Fallback: assume it's in the same directory
            let execDir = bundle.bundlePath + "/Contents/MacOS"
            self.cliPath = execDir + "/openinstax"
        }
    }

    // MARK: - Scanning

    /// Scan for nearby Instax printers.
    func scan() async -> [String] {
        guard let output = await run(["scan", "--json"]) else { return [] }
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
        let filmRemaining: Int
        let printCount: Int

        enum CodingKeys: String, CodingKey {
            case name, model, battery
            case filmRemaining = "film_remaining"
            case printCount = "print_count"
        }
    }

    /// Get printer info (battery, film, model, print count).
    func info(device: String? = nil) async -> PrinterInfo? {
        var args = ["info", "--json"]
        if let device = device {
            args += ["--device", device]
        }
        guard let output = await run(args),
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
        let output = await run(args)
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
        let filmRemaining: Int?
        let printCount: Int?

        enum CodingKeys: String, CodingKey {
            case connected, name, model, battery
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
    private func run(_ arguments: [String]) async -> String? {
        return await withCheckedContinuation { continuation in
            DispatchQueue.global(qos: .userInitiated).async { [cliPath] in
                let process = Process()
                process.executableURL = URL(fileURLWithPath: cliPath)
                process.arguments = arguments

                let stdout = Pipe()
                let stderr = Pipe()
                process.standardOutput = stdout
                process.standardError = stderr

                // Watchdog timer (15 seconds)
                let timer = DispatchSource.makeTimerSource(queue: .global())
                timer.schedule(deadline: .now() + 15)
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
}
