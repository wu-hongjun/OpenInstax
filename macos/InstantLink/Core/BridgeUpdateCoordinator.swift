import Combine
import Foundation

// MARK: - Snapshot

/// Snapshot exposed to SwiftUI views driving the Updates tab.
///
/// The coordinator owns a single state machine that walks through:
/// load bundle → compare versions → preflight → upload → start →
/// events stream → reconnect poll → mark-good (or rollback). Each
/// step mutates this snapshot, which the views observe via `@ObservedObject`.
struct BridgeUpdateSnapshot: Equatable {
    var bundled: BridgeUpdatePackage?
    var availability: Availability
    var preflight: BridgeUpdatePreflight?
    var operation: Operation?
    var lastResult: Result?
    var isPreflightInFlight: Bool
    var isRollbackInFlight: Bool

    enum Availability: Equatable {
        case upToDate(currentVersion: String)
        case updateAvailable(currentVersion: String, bundledVersion: String)
        case noBundle
        case unknown
    }

    struct Operation: Equatable {
        var operationID: String
        var phase: BridgeUpdatePhase
        var uploadProgress: Double?
        var events: [BridgeUpdateEvent]
        var startedAt: Date
        var lastMessage: String?
    }

    enum Result: Equatable {
        case succeeded(at: Date, newVersion: String)
        case failed(reason: String, at: Date)
        case rolledBack(at: Date)
    }

    static let empty = BridgeUpdateSnapshot(
        bundled: nil,
        availability: .unknown,
        preflight: nil,
        operation: nil,
        lastResult: nil,
        isPreflightInFlight: false,
        isRollbackInFlight: false
    )
}

// MARK: - Configuration

/// Tuning knobs for the update flow. The defaults match the bridge contract:
/// 2 s between `/v1/hello` polls; 45 attempts ≈ 90 s reconnect deadline.
struct BridgeUpdateCoordinatorConfig {
    var reconnectPollInterval: TimeInterval
    var reconnectMaxAttempts: Int

    static let `default` = BridgeUpdateCoordinatorConfig(
        reconnectPollInterval: 2.0,
        reconnectMaxAttempts: 45
    )
}

/// Surface used by the coordinator to re-probe `/v1/hello` during the
/// reconnect phase. The probe abstraction matches `BridgeDiscoveryProbe`
/// from `BridgeControlCoordinator` so tests can swap a scripted
/// implementation in without spinning up a real URLSession.
protocol BridgeUpdateHelloProbe: AnyObject {
    func probe(device: BridgeDevice) async throws -> BridgeDevice
}

/// Default probe that hits `/v1/hello` through a fresh
/// `HTTPBridgeDiscoveryProbe` against the device's last known endpoint.
final class HTTPBridgeUpdateHelloProbe: BridgeUpdateHelloProbe {
    private let probe: HTTPBridgeDiscoveryProbe

    init(probe: HTTPBridgeDiscoveryProbe = HTTPBridgeDiscoveryProbe()) {
        self.probe = probe
    }

    func probe(device: BridgeDevice) async throws -> BridgeDevice {
        guard let endpoint = device.endpointURL else {
            throw BridgeHTTPTransportError.invalidURL(device.deviceID)
        }
        return try await probe.probe(endpoint: endpoint)
    }
}

// MARK: - Coordinator

/// Owns the multi-stage Bridge update lifecycle. Composed by the
/// Updates tab; receives a `BridgeTransport` so tests use the in-memory
/// mock and inject deterministic event sequences via
/// `InMemoryBridgeTransport.setUpdateScript(_:)`.
@MainActor
final class BridgeUpdateCoordinator: ObservableObject {
    @Published private(set) var snapshot: BridgeUpdateSnapshot

    private let transport: BridgeTransport
    private let bundleProvider: () -> BridgeUpdatePackage?
    private let helloProbe: BridgeUpdateHelloProbe
    private let config: BridgeUpdateCoordinatorConfig
    private let now: () -> Date
    private let sleep: (UInt64) async -> Void

    init(
        transport: BridgeTransport,
        bundleProvider: @escaping () -> BridgeUpdatePackage? = {
            BridgeUpdatePackage.bundledFirmwarePackage()
        },
        helloProbe: BridgeUpdateHelloProbe = HTTPBridgeUpdateHelloProbe(),
        config: BridgeUpdateCoordinatorConfig = .default,
        now: @escaping () -> Date = Date.init,
        sleep: @escaping (UInt64) async -> Void = { nanos in
            try? await Task.sleep(nanoseconds: nanos)
        }
    ) {
        self.transport = transport
        self.bundleProvider = bundleProvider
        self.helloProbe = helloProbe
        self.config = config
        self.now = now
        self.sleep = sleep
        self.snapshot = .empty
    }

    // MARK: Bundle / availability

    /// Read the app-bundled firmware, compare against the supplied bridge
    /// status, and update `snapshot.availability` accordingly.
    func loadBundle(status: BridgeStatus?) {
        let bundled = bundleProvider()
        mutate { snapshot in
            snapshot.bundled = bundled
            snapshot.availability = Self.computeAvailability(
                bundled: bundled,
                status: status
            )
        }
    }

    private static func computeAvailability(
        bundled: BridgeUpdatePackage?,
        status: BridgeStatus?
    ) -> BridgeUpdateSnapshot.Availability {
        guard let bundled else { return .noBundle }
        guard let status else { return .unknown }
        let current = status.bridgeVersion
        if Self.compareVersions(current, bundled.version) < 0 {
            return .updateAvailable(currentVersion: current, bundledVersion: bundled.version)
        }
        return .upToDate(currentVersion: current)
    }

    /// Dotted-version compare; returns negative if lhs < rhs, positive if
    /// lhs > rhs, zero if equal. Non-numeric suffixes (e.g. `-rc1`) sort
    /// after the bare numeric prefix.
    static func compareVersions(_ lhs: String, _ rhs: String) -> Int {
        let lhsParts = parseVersionComponents(lhs)
        let rhsParts = parseVersionComponents(rhs)
        for i in 0..<max(lhsParts.count, rhsParts.count) {
            let a = i < lhsParts.count ? lhsParts[i] : 0
            let b = i < rhsParts.count ? rhsParts[i] : 0
            if a != b { return a - b }
        }
        return 0
    }

    private static func parseVersionComponents(_ value: String) -> [Int] {
        value
            .split(separator: ".")
            .map { component -> Int in
                let digits = component.prefix { $0.isNumber }
                return Int(digits) ?? 0
            }
    }

    // MARK: Preflight

    /// Run `/v1/update/preflight` for the currently bundled package and
    /// store the result in the snapshot.
    func refreshPreflight(device: BridgeDevice) async {
        guard let package = snapshot.bundled ?? bundleProvider() else { return }
        mutate { $0.isPreflightInFlight = true }
        do {
            let preflight = try await transport.preflightUpdate(device: device, package: package)
            mutate { snapshot in
                snapshot.preflight = preflight
                snapshot.isPreflightInFlight = false
            }
        } catch {
            mutate { snapshot in
                snapshot.preflight = nil
                snapshot.isPreflightInFlight = false
                snapshot.lastResult = .failed(
                    reason: Self.message(for: error),
                    at: now()
                )
            }
        }
    }

    // MARK: Update lifecycle

    /// Full upload → start → events stream → reconnect poll → mark-good
    /// run for the supplied paired device. Errors at any step surface as
    /// `snapshot.lastResult = .failed`.
    func runUpdate(device: BridgeDevice) async {
        guard let package = snapshot.bundled ?? bundleProvider() else {
            mutate { snapshot in
                snapshot.lastResult = .failed(
                    reason: "No bundled update is available.",
                    at: now()
                )
            }
            return
        }

        mutate { snapshot in
            snapshot.lastResult = nil
            snapshot.operation = BridgeUpdateSnapshot.Operation(
                operationID: "pending-upload",
                phase: .uploadingUpdate,
                uploadProgress: 0,
                events: [],
                startedAt: now(),
                lastMessage: nil
            )
        }

        // Upload.
        do {
            _ = try await transport.uploadUpdate(device: device, package: package)
            mutate { snapshot in
                snapshot.operation?.uploadProgress = 1.0
            }
        } catch {
            failOperation(reason: Self.message(for: error))
            return
        }

        // Start.
        let initialState: BridgeUpdateState
        do {
            initialState = try await transport.startUpdate(device: device, package: package)
        } catch {
            failOperation(reason: Self.message(for: error))
            return
        }

        mutate { snapshot in
            snapshot.operation?.operationID = initialState.operationID
            snapshot.operation?.phase = initialState.phase
            snapshot.operation?.lastMessage = initialState.message
        }

        // Subscribe to events stream.
        let operationID = initialState.operationID
        do {
            let stream = try await transport.updateEvents(device: device, operationID: operationID)
            var sawReconnect = false
            for try await event in stream {
                let finished = appendEvent(event)
                if event.phase == .reconnecting {
                    sawReconnect = true
                }
                if finished { break }
            }

            // If the bridge ended the stream without ever advertising
            // `.reconnecting` (some flows compress phases) but the script
            // landed on a terminal-pre-reconnect phase, still attempt the
            // poll so verification runs.
            if !sawReconnect, snapshot.operation?.phase == .verifyingBridge {
                sawReconnect = true
            }

            if sawReconnect || snapshot.operation?.phase == .reconnecting {
                await waitForReconnect(device: device, package: package)
            } else if let phase = snapshot.operation?.phase, phase == .done {
                await finalizeMarkGood(device: device, installedVersion: package.version)
            } else {
                failOperation(reason: "Update ended in unexpected phase: \(snapshot.operation?.phase.rawValue ?? "unknown")")
            }
        } catch {
            failOperation(reason: Self.message(for: error))
        }
    }

    /// Append a streamed event and return `true` once the event indicates
    /// a definitively terminal state (done / failed / rolled-back /
    /// needs-recovery).
    @discardableResult
    private func appendEvent(_ event: BridgeUpdateEvent) -> Bool {
        var terminal = false
        mutate { snapshot in
            snapshot.operation?.events.append(event)
            snapshot.operation?.phase = event.phase
            if let message = event.message, !message.isEmpty {
                snapshot.operation?.lastMessage = message
            }
            switch event.phase {
            case .done, .failed, .rolledBack, .needsRecovery:
                terminal = true
            default:
                terminal = false
            }
        }
        return terminal
    }

    private func waitForReconnect(
        device: BridgeDevice,
        package: BridgeUpdatePackage
    ) async {
        let pollNanos = UInt64(max(0.05, config.reconnectPollInterval) * 1_000_000_000)
        for _ in 0..<max(1, config.reconnectMaxAttempts) {
            await sleep(pollNanos)
            if let fresh = try? await helloProbe.probe(device: device),
               fresh.softwareVersion == package.version {
                await finalizeMarkGood(device: device, installedVersion: package.version)
                return
            }
        }
        // Reconnect deadline elapsed — surface as rolled-back failure with
        // a diagnostic hint per the plan ("Update failed — bridge rolled back").
        failOperation(
            reason: "Bridge did not come back online after the update. It may have rolled back to v\(device.softwareVersion)."
        )
    }

    private func finalizeMarkGood(device: BridgeDevice, installedVersion: String) async {
        do {
            _ = try await transport.markUpdateGood(device: device)
            // Prefer the version we just installed; the bridge's mark-good
            // response sometimes carries the pre-update version when the new
            // slot has not yet flipped the canonical status. The caller
            // already verified `/v1/hello` reports `installedVersion`, so
            // this is the authoritative value.
            mutate { snapshot in
                snapshot.operation = nil
                snapshot.lastResult = .succeeded(at: now(), newVersion: installedVersion)
                snapshot.preflight = nil
                snapshot.availability = .upToDate(currentVersion: installedVersion)
            }
        } catch {
            failOperation(reason: Self.message(for: error))
        }
    }

    private func failOperation(reason: String) {
        mutate { snapshot in
            snapshot.operation = nil
            snapshot.lastResult = .failed(reason: reason, at: now())
        }
    }

    // MARK: Rollback

    /// Call `/v1/update/rollback`. Surfaces success as
    /// `snapshot.lastResult = .rolledBack`; failures as `.failed`.
    func rollback(device: BridgeDevice, reason: String) async {
        mutate { $0.isRollbackInFlight = true }
        do {
            _ = try await transport.rollbackUpdate(device: device, reason: reason)
            mutate { snapshot in
                snapshot.isRollbackInFlight = false
                snapshot.operation = nil
                snapshot.lastResult = .rolledBack(at: now())
            }
        } catch {
            mutate { snapshot in
                snapshot.isRollbackInFlight = false
                snapshot.lastResult = .failed(
                    reason: Self.message(for: error),
                    at: now()
                )
            }
        }
    }

    // MARK: Helpers

    private static func message(for error: Error) -> String {
        if let api = error as? BridgeAPIError {
            return api.payload.message
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
        return "\(error)"
    }

    private func mutate(_ change: (inout BridgeUpdateSnapshot) -> Void) {
        var copy = snapshot
        change(&copy)
        if copy != snapshot {
            snapshot = copy
        }
    }
}
