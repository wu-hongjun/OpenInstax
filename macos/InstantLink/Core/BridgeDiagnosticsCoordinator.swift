import Combine
import Foundation

// MARK: - Public state

/// Snapshot exposed to SwiftUI views driving the Diagnostics tab.
///
/// The coordinator owns three loosely-coupled state machines:
/// log streaming, support-bundle creation, and recovery (driven by
/// `/v1/hello` health). Each step mutates this snapshot which the
/// view observes via `@ObservedObject`.
struct BridgeDiagnosticsSnapshot: Equatable {
    var logTail: [BridgeLogEvent]
    var logLevelFilter: BridgeLogLevel
    var streamState: StreamState
    var supportBundle: SupportBundleState
    var recovery: RecoveryState

    enum StreamState: Equatable {
        case idle
        case connecting
        case live(connectedAt: Date)
        case paused
        case disconnected(reason: String?, at: Date)
    }

    enum SupportBundleState: Equatable {
        case idle
        case creating(startedAt: Date)
        case ready(BridgeSupportBundleResult, savedTo: URL?, at: Date)
        case failed(reason: String, at: Date)
    }

    enum RecoveryState: Equatable {
        case ok
        case checking
        case managementUnavailable(since: Date, attempts: Int)
        case restartInFlight
        case recovered(at: Date)
        case unrecoverable(reason: String, at: Date)
    }

    static let empty = BridgeDiagnosticsSnapshot(
        logTail: [],
        logLevelFilter: .info,
        streamState: .idle,
        supportBundle: .idle,
        recovery: .ok
    )
}

// MARK: - Configuration

/// Tuning knobs for the diagnostics flow. Defaults match the plan 038
/// Phase E spec: cap the in-memory log buffer at 200 entries.
struct BridgeDiagnosticsCoordinatorConfig {
    var maxLogTail: Int
    /// Number of consecutive `/v1/hello` failures before the recovery
    /// banner surfaces. The Control coordinator drives this counter.
    var managementUnavailableThreshold: Int

    static let `default` = BridgeDiagnosticsCoordinatorConfig(
        maxLogTail: 200,
        managementUnavailableThreshold: 3
    )
}

// MARK: - Coordinator

/// Owns the Bridge diagnostics lifecycle. Composed by the Diagnostics tab;
/// receives a `BridgeTransport` so tests can swap an in-memory mock.
///
/// Three responsibilities:
///
/// * Live logs: subscribe to `GET /v1/logs/stream` (SSE) and stream events
///   into a capped tail. Pause/resume is local; filtering is driven by
///   the bridge query parameter.
/// * Support bundle: POST `/v1/support-bundle/create`, then optionally
///   write the bridge-side archive metadata to a user-selected location.
/// * Recovery: when the Control coordinator reports 3+ consecutive
///   `/v1/hello` failures with a USB carrier up, surface a banner that
///   offers a "Restart management service" affordance + power-cycle
///   instructions.
@MainActor
final class BridgeDiagnosticsCoordinator: ObservableObject {
    @Published private(set) var snapshot: BridgeDiagnosticsSnapshot

    private let transport: BridgeTransport
    private let config: BridgeDiagnosticsCoordinatorConfig
    private let now: () -> Date
    private let fileManager: FileManager

    private var streamTask: Task<Void, Never>?
    private var currentStreamingDevice: BridgeDevice?

    init(
        transport: BridgeTransport,
        config: BridgeDiagnosticsCoordinatorConfig = .default,
        now: @escaping () -> Date = Date.init,
        fileManager: FileManager = .default
    ) {
        self.transport = transport
        self.config = config
        self.now = now
        self.fileManager = fileManager
        self.snapshot = .empty
    }

    deinit {
        streamTask?.cancel()
    }

    // MARK: Log streaming

    /// Begin streaming logs from the supplied device. Cancels any existing
    /// stream first.
    func startStreaming(device: BridgeDevice) {
        stopStreaming()
        currentStreamingDevice = device
        mutate { snapshot in
            snapshot.streamState = .connecting
        }
        let level = snapshot.logLevelFilter
        streamTask = Task { [weak self] in
            await self?.runStreamingLoop(device: device, level: level)
        }
    }

    /// Stop streaming and mark the snapshot as paused (so the view can
    /// distinguish "user paused" from "stream lost"). The buffered tail
    /// is preserved.
    func stopStreaming() {
        streamTask?.cancel()
        streamTask = nil
        if currentStreamingDevice != nil {
            mutate { snapshot in
                snapshot.streamState = .paused
            }
        }
    }

    /// Drop the buffered tail without dropping the stream.
    func clearTail() {
        mutate { snapshot in
            snapshot.logTail = []
        }
    }

    /// Change the level filter. If a stream is currently live the stream
    /// is restarted so the new filter takes effect server-side.
    func setFilter(_ level: BridgeLogLevel) {
        guard snapshot.logLevelFilter != level else { return }
        mutate { snapshot in
            snapshot.logLevelFilter = level
        }
        if let device = currentStreamingDevice {
            startStreaming(device: device)
        }
    }

    private func runStreamingLoop(device: BridgeDevice, level: BridgeLogLevel) async {
        let stream = transport.streamLogs(device: device, level: level)
        mutate { snapshot in
            snapshot.streamState = .live(connectedAt: self.now())
        }
        do {
            for try await event in stream {
                if Task.isCancelled { break }
                appendLogEvent(event)
            }
            if !Task.isCancelled {
                let when = self.now()
                mutate { snapshot in
                    snapshot.streamState = .disconnected(reason: nil, at: when)
                }
            }
        } catch is CancellationError {
            // Cancellation is the user pausing; leave the state alone so
            // stopStreaming() can set `.paused`.
        } catch {
            let when = self.now()
            let reason = Self.message(for: error)
            mutate { snapshot in
                snapshot.streamState = .disconnected(reason: reason, at: when)
            }
        }
    }

    private func appendLogEvent(_ event: BridgeLogEvent) {
        mutate { snapshot in
            snapshot.logTail.append(event)
            if snapshot.logTail.count > self.config.maxLogTail {
                let overflow = snapshot.logTail.count - self.config.maxLogTail
                snapshot.logTail.removeFirst(overflow)
            }
        }
    }

    // MARK: Support bundle

    /// Ask the bridge to stage a support bundle, then optionally copy the
    /// returned archive metadata as a sidecar file at `destinationURL`. The
    /// archive itself lives on the bridge filesystem; the sidecar lets the
    /// user attach it to a support email without having to SCP.
    func createSupportBundle(device: BridgeDevice, destinationURL: URL?) async {
        let started = now()
        mutate { snapshot in
            snapshot.supportBundle = .creating(startedAt: started)
        }
        let result: BridgeSupportBundleResult
        do {
            result = try await transport.createSupportBundle(device: device)
        } catch {
            let when = self.now()
            mutate { snapshot in
                snapshot.supportBundle = .failed(reason: Self.message(for: error), at: when)
            }
            return
        }

        var savedTo: URL?
        if let destinationURL {
            do {
                try writeBundleSidecar(result, deviceID: device.deviceID, to: destinationURL)
                savedTo = destinationURL
            } catch {
                let when = self.now()
                mutate { snapshot in
                    snapshot.supportBundle = .failed(reason: Self.message(for: error), at: when)
                }
                return
            }
        }

        let when = self.now()
        mutate { snapshot in
            snapshot.supportBundle = .ready(result, savedTo: savedTo, at: when)
        }
    }

    /// Clear the support bundle result so the toast disappears.
    func clearSupportBundle() {
        mutate { snapshot in
            snapshot.supportBundle = .idle
        }
    }

    /// On-disk sidecar format. The bridge's archive lives at
    /// `result.archivePath` server-side; the sidecar carries enough metadata
    /// for support to retrieve the actual archive over SCP/USB-debug.
    private struct SupportBundleSidecar: Codable {
        var schemaVersion: Int
        var bundleKind: String
        var bridgeDeviceID: String
        var bundle: BridgeSupportBundleResult

        enum CodingKeys: String, CodingKey {
            case schemaVersion = "schema_version"
            case bundleKind = "bundle_kind"
            case bridgeDeviceID = "bridge_device_id"
            case bundle
        }
    }

    private func writeBundleSidecar(
        _ bundle: BridgeSupportBundleResult,
        deviceID: String,
        to destinationURL: URL
    ) throws {
        let sidecar = SupportBundleSidecar(
            schemaVersion: 1,
            bundleKind: "instantlink_bridge_support_sidecar",
            bridgeDeviceID: deviceID,
            bundle: bundle
        )
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .prettyPrinted]
        let data = try encoder.encode(sidecar)
        let parent = destinationURL.deletingLastPathComponent()
        let tempURL = parent.appendingPathComponent(
            ".bridge-support-\(UUID().uuidString).tmp"
        )
        do {
            try data.write(to: tempURL, options: [.atomic])
            if fileManager.fileExists(atPath: destinationURL.path) {
                try fileManager.removeItem(at: destinationURL)
            }
            try fileManager.moveItem(at: tempURL, to: destinationURL)
        } catch {
            try? fileManager.removeItem(at: tempURL)
            throw BridgeDiagnosticsCoordinatorError.fileWriteFailed(error.localizedDescription)
        }
    }

    // MARK: Recovery

    /// Called by `BridgeControlCoordinator` on every `/v1/hello` failure so
    /// the diagnostics coordinator can decide whether to surface the
    /// recovery banner. The Control coordinator owns the failure counter
    /// because it already polls `/v1/hello` for discovery; this method
    /// simply applies the threshold.
    func evaluateHealthOnHelloFailure(consecutiveFailures: Int, hasUSBCarrier: Bool) {
        let threshold = config.managementUnavailableThreshold
        guard consecutiveFailures >= threshold, hasUSBCarrier else { return }
        switch snapshot.recovery {
        case .managementUnavailable, .restartInFlight, .unrecoverable:
            // Already surfaced; bump the attempt count so the banner can
            // explain "tried N times".
            mutate { snapshot in
                if case .managementUnavailable(let since, _) = snapshot.recovery {
                    snapshot.recovery = .managementUnavailable(
                        since: since,
                        attempts: consecutiveFailures
                    )
                }
            }
        default:
            let when = self.now()
            mutate { snapshot in
                snapshot.recovery = .managementUnavailable(
                    since: when,
                    attempts: consecutiveFailures
                )
            }
        }
    }

    /// Called by `BridgeControlCoordinator` whenever `/v1/hello` succeeds so
    /// the banner can auto-dismiss.
    func evaluateHealthOnHelloSuccess() {
        switch snapshot.recovery {
        case .managementUnavailable, .restartInFlight:
            let when = self.now()
            mutate { snapshot in
                snapshot.recovery = .recovered(at: when)
            }
        default:
            break
        }
    }

    /// Attempt recovery by asking the bridge to restart its management
    /// service. If the route is missing or rejected we fall through to
    /// `.unrecoverable` so the view can surface the "power-cycle the
    /// Bridge" copy.
    func attemptRecovery(device: BridgeDevice) async {
        mutate { snapshot in
            snapshot.recovery = .restartInFlight
        }
        do {
            try await transport.restartManagement(device: device)
        } catch {
            let when = self.now()
            let reason = Self.message(for: error)
            mutate { snapshot in
                snapshot.recovery = .unrecoverable(reason: reason, at: when)
            }
            return
        }
        // The bridge accepted the restart; the Control coordinator's
        // discovery loop will flip us to `.recovered` once `/v1/hello`
        // succeeds again. Until then stay in `.restartInFlight`.
    }

    /// Reset the recovery state from `.recovered` or `.unrecoverable` back
    /// to `.ok` so the banner auto-dismisses after the toast lifetime.
    func dismissRecovery() {
        switch snapshot.recovery {
        case .recovered, .unrecoverable:
            mutate { snapshot in
                snapshot.recovery = .ok
            }
        default:
            break
        }
    }

    // MARK: Helpers

    private func mutate(_ change: (inout BridgeDiagnosticsSnapshot) -> Void) {
        var copy = snapshot
        change(&copy)
        if copy != snapshot {
            snapshot = copy
        }
    }

    private static func message(for error: Error) -> String {
        if let api = error as? BridgeAPIError {
            return api.payload.message
        }
        if let coordinatorError = error as? BridgeDiagnosticsCoordinatorError {
            return coordinatorError.localizedDescription
        }
        if let httpError = error as? BridgeHTTPTransportError {
            switch httpError {
            case .invalidResponse: return "Bridge response was invalid."
            case .invalidURL(let value): return "Invalid bridge address: \(value)"
            case .httpStatus(let code): return "Bridge HTTP error (\(code))."
            }
        }
        if let transportError = error as? BridgeTransportError {
            switch transportError {
            case .deviceNotFound(let id): return "Bridge \(id) was not found."
            case .updateOperationNotFound(let id): return "Update operation \(id) was not found."
            case .updateScriptEmpty: return "Bridge returned no update steps."
            case .updatePreflightFailed: return "Preflight checks did not pass."
            case .localAuthNotFound(let id): return "Local auth missing for bridge \(id)."
            case .helloProbeFailed(let host): return "Bridge \(host) is not reachable."
            case .managementRestartFailed(let message): return message
            case .supportBundleFailed(let message): return message
            }
        }
        return "\(error.localizedDescription)"
    }
}

// MARK: - Errors

enum BridgeDiagnosticsCoordinatorError: Error, Equatable, LocalizedError {
    case noDevice
    case fileWriteFailed(String)

    var errorDescription: String? {
        switch self {
        case .noDevice:
            return L("No Bridge is currently connected.")
        case .fileWriteFailed(let detail):
            return String(format: L("Could not write the support bundle file: %@"), detail)
        }
    }
}
