import Combine
import CryptoKit
import Foundation

// MARK: - Public state

/// Which physical/transport medium a Bridge discovery hit arrived on.
///
/// The medium is inferred from the discovered device's endpoint URL host:
///   * `192.168.7.1` — USB gadget (`usb0`) point-to-point link.
///   * `192.168.8.1` — Bridge Wi-Fi (the Pi's hotspot).
///   * anything else (e.g. `bridge.local` over Same-Wi-Fi) — Wi-Fi advertised
///     on the user's existing network.
///
/// The medium drives the pairing UX. USB hits trigger silent auto-trust
/// because physical USB presence on the user's own Mac is the approval
/// signal; Wi-Fi hits keep the LCD-code wizard.
enum BridgeTransportMedium: String, Equatable {
    case usb
    case bridgeWiFi
    case sameWiFi
    case unknown

    static func from(endpoint: URL?) -> BridgeTransportMedium {
        guard let host = endpoint?.host else { return .unknown }
        if host == "192.168.7.1" { return .usb }
        if host == "192.168.8.1" { return .bridgeWiFi }
        return .sameWiFi
    }
}

/// What discovery currently thinks about the Bridge attached to USB / Wi-Fi.
enum BridgeDiscoveryState: Equatable {
    case searching
    case found(BridgeDevice, medium: BridgeTransportMedium)
    case lost(lastDevice: BridgeDevice?, lostAt: Date)
}

/// Pairing state machine driven by `/v1/pairing/status` polling and the
/// pairing wizard's `pair(code:)` call.
enum BridgePairingPhase: Equatable {
    case unpaired
    case pairingWindowOpen(expiresAt: Date?)
    case awaitingCode(expiresAt: Date?)
    case completing
    case paired(BridgeIdentity)
    case failed(reason: BridgePairingFailureReason)
}

enum BridgePairingFailureReason: Equatable {
    case windowClosed
    case codeRejected(String)
    case bridgeUnreachable
    case timeout
    case other(String)

    var localizedMessage: String {
        switch self {
        case .windowClosed: return L("bridge_pairing_error_window_closed")
        case .codeRejected: return L("bridge_pairing_error_code_rejected")
        case .bridgeUnreachable: return L("bridge_pairing_error_unreachable")
        case .timeout: return L("bridge_pairing_error_timeout")
        case .other(let message): return message
        }
    }
}

/// Snapshot exposed to SwiftUI views.
struct BridgeControlSnapshot: Equatable {
    var discovery: BridgeDiscoveryState
    var pairing: BridgePairingPhase
    var status: BridgeStatus?
    var lastError: BridgeErrorPayload?
    var lastUpdated: Date?
    var pairingStatus: BridgePairingStatus?
    /// Set when USB-physical auto-trust most recently succeeded. The discovery
    /// banner watches this to render a brief "Bridge connected and authorized"
    /// toast that fades after 5 s.
    var lastAutoTrustEvent: Date?

    static let empty = BridgeControlSnapshot(
        discovery: .searching,
        pairing: .unpaired,
        status: nil,
        lastError: nil,
        lastUpdated: nil,
        pairingStatus: nil,
        lastAutoTrustEvent: nil
    )
}

// MARK: - Configuration

struct BridgeControlCoordinatorConfig {
    var probeEndpoints: [URL]
    var discoveryIntervalUnpaired: TimeInterval
    var discoveryIntervalPaired: TimeInterval
    var statusInterval: TimeInterval
    var pairingPollInterval: TimeInterval
    var staleAfter: TimeInterval

    static let `default` = BridgeControlCoordinatorConfig(
        probeEndpoints: [
            URL(string: "http://192.168.7.1:8742")!,
            URL(string: "http://192.168.8.1:8742")!,
        ],
        discoveryIntervalUnpaired: 5.0,
        discoveryIntervalPaired: 30.0,
        statusInterval: 5.0,
        pairingPollInterval: 1.0,
        staleAfter: 30.0
    )
}

/// Discovery probe surface — abstracted so tests can swap in deterministic implementations.
protocol BridgeDiscoveryProbe: AnyObject {
    func probe(endpoint: URL) async throws -> BridgeDevice
}

/// Concrete probe that hits `/v1/hello` over plain URLSession on a single endpoint.
final class HTTPBridgeDiscoveryProbe: BridgeDiscoveryProbe {
    private let session: URLSession
    private let decoder: JSONDecoder
    private let timeout: TimeInterval

    init(session: URLSession = .shared, timeout: TimeInterval = 2.0) {
        let config = session.configuration.copy() as? URLSessionConfiguration ?? .ephemeral
        config.timeoutIntervalForRequest = timeout
        config.timeoutIntervalForResource = timeout
        self.session = URLSession(configuration: config)
        self.decoder = JSONDecoder()
        self.timeout = timeout
    }

    func probe(endpoint: URL) async throws -> BridgeDevice {
        var components = URLComponents(url: endpoint, resolvingAgainstBaseURL: false)
        let basePath = components?.percentEncodedPath ?? ""
        components?.percentEncodedPath = basePath.hasSuffix("/") ? basePath + "v1/hello" : basePath + "/v1/hello"
        guard let url = components?.url else {
            throw BridgeHTTPTransportError.invalidURL(endpoint.absoluteString)
        }
        var request = URLRequest(url: url)
        request.timeoutInterval = timeout
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            throw BridgeHTTPTransportError.invalidResponse
        }
        let envelope = try decoder.decode(BridgeAPIEnvelope.self, from: data)
        var device = try envelope.requireDevice()
        if device.endpointURL == nil {
            device.endpointURL = endpoint
        }
        return device
    }
}

// MARK: - Coordinator

@MainActor
final class BridgeControlCoordinator: ObservableObject {
    @Published private(set) var snapshot: BridgeControlSnapshot
    private(set) var transport: BridgeTransport
    /// Owns the Bridge update lifecycle (preflight, upload, install,
    /// reconnect, mark-good, rollback). Composed here so the Updates tab
    /// and the Overview rollback affordance share one source of truth.
    let updateCoordinator: BridgeUpdateCoordinator
    /// Owns the Bridge backup + restore lifecycle. Composed here so the
    /// Backup tab and any future Settings affordance share one source of
    /// truth. Mirrors the `updateCoordinator` precedent from Phase C.
    let backupCoordinator: BridgeBackupCoordinator

    private let clientStore: BridgeClientFileStore
    private let probe: BridgeDiscoveryProbe
    private let config: BridgeControlCoordinatorConfig
    private let now: () -> Date
    private let clientNameProvider: () -> String

    private var discoveryTask: Task<Void, Never>?
    private var pairingPollTask: Task<Void, Never>?
    private var statusTask: Task<Void, Never>?

    private var lastSuccessfulDiscovery: Date?
    private var isWindowVisible: Bool = true
    private var isStarted: Bool = false

    private var currentTransportEndpoint: URL?

    init(
        transport: BridgeTransport,
        clientStore: BridgeClientFileStore = BridgeClientFileStore(),
        probe: BridgeDiscoveryProbe = HTTPBridgeDiscoveryProbe(),
        config: BridgeControlCoordinatorConfig = .default,
        clientNameProvider: @escaping () -> String = {
            Host.current().localizedName ?? ProcessInfo.processInfo.hostName
        },
        now: @escaping () -> Date = Date.init,
        updateCoordinator: BridgeUpdateCoordinator? = nil,
        backupCoordinator: BridgeBackupCoordinator? = nil
    ) {
        self.transport = transport
        self.clientStore = clientStore
        self.probe = probe
        self.config = config
        self.clientNameProvider = clientNameProvider
        self.now = now
        self.snapshot = .empty
        self.updateCoordinator = updateCoordinator ?? BridgeUpdateCoordinator(transport: transport)
        self.backupCoordinator = backupCoordinator ?? BridgeBackupCoordinator(transport: transport)
    }

    deinit {
        discoveryTask?.cancel()
        pairingPollTask?.cancel()
        statusTask?.cancel()
    }

    // MARK: Lifecycle

    func start() {
        guard !isStarted else { return }
        isStarted = true
        startDiscoveryLoop()
    }

    func stop() {
        isStarted = false
        discoveryTask?.cancel()
        discoveryTask = nil
        pairingPollTask?.cancel()
        pairingPollTask = nil
        statusTask?.cancel()
        statusTask = nil
    }

    /// Called by the BridgeControlWindow when its visibility changes so polling
    /// pauses while the window is hidden.
    func onWindowVisibilityChanged(_ visible: Bool) {
        isWindowVisible = visible
        if visible {
            if statusTask == nil, case .paired = snapshot.pairing {
                startStatusPolling()
            }
        } else {
            statusTask?.cancel()
            statusTask = nil
        }
    }

    // MARK: Discovery

    private func startDiscoveryLoop() {
        discoveryTask?.cancel()
        discoveryTask = Task { [weak self] in
            await self?.discoveryLoopBody()
        }
    }

    private func discoveryLoopBody() async {
        // Immediate first probe so the UI updates within ~3 s of `start()`.
        await runDiscoveryProbe()

        while !Task.isCancelled {
            let interval: TimeInterval
            if case .paired = snapshot.pairing {
                interval = config.discoveryIntervalPaired
            } else {
                interval = config.discoveryIntervalUnpaired
            }
            let nanos = UInt64(max(0.1, interval) * 1_000_000_000)
            try? await Task.sleep(nanoseconds: nanos)
            if Task.isCancelled { break }
            await runDiscoveryProbe()
        }
    }

    /// Probe all known endpoints in parallel; first success wins.
    func runDiscoveryProbe() async {
        let endpoints = config.probeEndpoints
        guard !endpoints.isEmpty else { return }

        let probe = self.probe
        let device = await withTaskGroup(of: BridgeDevice?.self) { group -> BridgeDevice? in
            for endpoint in endpoints {
                group.addTask {
                    do {
                        return try await probe.probe(endpoint: endpoint)
                    } catch {
                        return nil
                    }
                }
            }
            for await result in group {
                if let result {
                    group.cancelAll()
                    return result
                }
            }
            return nil
        }

        if let device {
            handleDiscoveryHit(device)
        } else {
            handleDiscoveryMiss()
        }
    }

    private func handleDiscoveryHit(_ device: BridgeDevice) {
        let nowDate = now()
        lastSuccessfulDiscovery = nowDate

        // Rotate transport endpoint if a fresh one is provided.
        if let endpoint = device.endpointURL, endpoint != currentTransportEndpoint {
            currentTransportEndpoint = endpoint
        }

        let medium = BridgeTransportMedium.from(endpoint: device.endpointURL)
        var mutated = snapshot
        mutated.discovery = .found(device, medium: medium)
        mutated.lastUpdated = nowDate
        snapshot = mutated

        // Try to recover identity from the on-disk client store so we transition
        // directly to paired without forcing the user back through the wizard.
        if case .paired = snapshot.pairing {
            // Already paired in-memory; nothing to do.
        } else if let restored = try? clientStore.loadIdentity(deviceID: device.deviceID) {
            mutateSnapshot { snapshot in
                snapshot.pairing = .paired(restored.0)
            }
            startStatusPolling()
        } else if medium == .usb {
            // Plan 038 phase A.1: USB physical presence on the user's own Mac IS
            // the approval signal. Skip the LCD-code wizard, call the bridge's
            // auto-trust route silently, and persist the identity ourselves.
            startUSBAutoTrust(device: device)
        } else {
            // Wi-Fi paths still require LCD-code confirmation. Start polling the
            // pairing status so we can react when the user opens the window on
            // the LCD.
            startPairingPolling(device: device)
        }
    }

    private func startUSBAutoTrust(device: BridgeDevice) {
        // Cancel any in-flight pairing polling: USB auto-trust supersedes it.
        pairingPollTask?.cancel()
        pairingPollTask = nil
        Task { [weak self] in
            await self?.runUSBAutoTrust(device: device)
        }
    }

    private func runUSBAutoTrust(device: BridgeDevice) async {
        // If a different branch already paired us in the meantime, skip.
        if case .paired = snapshot.pairing { return }
        let clientName = clientNameProvider()
        do {
            let completion = try await transport.usbAutoTrust(
                device: device,
                clientName: clientName
            )
            guard completion.paired else {
                mutateSnapshot { snapshot in
                    snapshot.pairing = .failed(reason: .other(completion.message ?? "auto_trust_rejected"))
                }
                return
            }
            let identity = BridgeIdentity(
                deviceID: device.deviceID,
                displayName: device.displayName,
                pairedAt: now(),
                clientID: completion.clientID,
                clientName: clientName
            )
            // The transport's BridgeClientFileStore already persisted the signing
            // material under the same on-disk record. Refresh the display
            // metadata fields (display_name, paired_at) so the file mirrors the
            // current pairing event.
            do {
                if let existing = try clientStore.loadIdentity(deviceID: device.deviceID) {
                    try clientStore.saveIdentity(identity, privateKey: existing.1)
                }
            } catch {
                mutateSnapshot { snapshot in
                    snapshot.lastError = BridgeErrorPayload(
                        message: "Failed to cache identity metadata: \(error)"
                    )
                }
            }
            mutateSnapshot { snapshot in
                snapshot.pairing = .paired(identity)
                snapshot.lastError = nil
                snapshot.lastAutoTrustEvent = self.now()
            }
            startStatusPolling()
        } catch let error as BridgeAPIError {
            mutateSnapshot { snapshot in
                snapshot.lastError = error.payload
                snapshot.pairing = .unpaired
            }
        } catch {
            mutateSnapshot { snapshot in
                snapshot.lastError = BridgeErrorPayload(message: "\(error)")
                snapshot.pairing = .unpaired
            }
        }
    }

    private func handleDiscoveryMiss() {
        let nowDate = now()
        let last = lastSuccessfulDiscovery
        // If we never had a hit, stay in `searching`.
        guard let last else {
            return
        }
        // Wait `staleAfter` seconds before flipping to lost.
        guard nowDate.timeIntervalSince(last) >= config.staleAfter else { return }

        var lastDevice: BridgeDevice?
        switch snapshot.discovery {
        case .found(let device, _):
            lastDevice = device
        case .lost(let device, _):
            lastDevice = device
        case .searching:
            lastDevice = nil
        }

        mutateSnapshot { snapshot in
            snapshot.discovery = .lost(lastDevice: lastDevice, lostAt: nowDate)
        }
    }

    // MARK: Pairing

    private func startPairingPolling(device: BridgeDevice) {
        pairingPollTask?.cancel()
        pairingPollTask = Task { [weak self] in
            await self?.pairingPollBody(device: device)
        }
    }

    private func pairingPollBody(device: BridgeDevice) async {
        while !Task.isCancelled {
            do {
                let status = try await transport.pairingStatus(device: device)
                mutateSnapshot { snapshot in
                    snapshot.pairingStatus = status
                    let expiresAt: Date? = status.expiresAt.map { Date(timeIntervalSince1970: TimeInterval($0)) }
                    if status.open {
                        switch snapshot.pairing {
                        case .awaitingCode, .completing:
                            break // user is mid-flow; don't reset
                        case .paired:
                            break
                        default:
                            snapshot.pairing = .pairingWindowOpen(expiresAt: expiresAt)
                        }
                    } else {
                        switch snapshot.pairing {
                        case .paired:
                            break
                        case .completing:
                            break
                        case .pairingWindowOpen, .awaitingCode:
                            snapshot.pairing = .failed(reason: .windowClosed)
                        case .failed, .unpaired:
                            snapshot.pairing = .unpaired
                        }
                    }
                }
            } catch {
                // Transient network noise during discovery is normal; do not surface.
            }
            let nanos = UInt64(max(0.2, config.pairingPollInterval) * 1_000_000_000)
            try? await Task.sleep(nanoseconds: nanos)
        }
    }

    /// Manually advance from `pairingWindowOpen` → `awaitingCode` (called when the
    /// user dismisses the introductory step and starts typing).
    func acknowledgePairingWindowOpen() {
        mutateSnapshot { snapshot in
            if case .pairingWindowOpen(let expires) = snapshot.pairing {
                snapshot.pairing = .awaitingCode(expiresAt: expires)
            }
        }
    }

    /// Submit a 6-digit code. Returns `true` on success.
    @discardableResult
    func pair(code: String, displayName: String? = nil) async -> Bool {
        guard let device = currentDevice() else {
            mutateSnapshot { snapshot in
                snapshot.pairing = .failed(reason: .bridgeUnreachable)
            }
            return false
        }

        mutateSnapshot { snapshot in
            snapshot.pairing = .completing
        }

        let clientName = displayName?.trimmingCharacters(in: .whitespacesAndNewlines).nonEmpty
            ?? clientNameProvider()

        do {
            let completion = try await transport.completePairing(
                device: device,
                confirmationCode: code,
                clientName: clientName
            )
            guard completion.paired else {
                mutateSnapshot { snapshot in
                    snapshot.pairing = .failed(reason: .codeRejected(completion.message ?? "rejected"))
                }
                return false
            }

            let identity = BridgeIdentity(
                deviceID: device.deviceID,
                displayName: device.displayName,
                pairedAt: now(),
                clientID: completion.clientID,
                clientName: clientName
            )

            // Identity is persisted by the transport's BridgeClientFileStore.
            // Refresh display metadata on the shared on-disk record so the file
            // mirrors the latest pairing event (display name + paired_at).
            do {
                if let existing = try clientStore.loadIdentity(deviceID: device.deviceID) {
                    try clientStore.saveIdentity(identity, privateKey: existing.1)
                }
            } catch {
                mutateSnapshot { snapshot in
                    snapshot.lastError = BridgeErrorPayload(message: "Failed to cache identity metadata: \(error)")
                }
            }

            mutateSnapshot { snapshot in
                snapshot.pairing = .paired(identity)
                snapshot.lastError = nil
            }
            startStatusPolling()
            // Stop polling pairing status now that we are paired.
            pairingPollTask?.cancel()
            pairingPollTask = nil
            return true
        } catch let error as BridgeAPIError {
            mutateSnapshot { snapshot in
                snapshot.lastError = error.payload
                if error.code == "pairing_not_open" {
                    snapshot.pairing = .failed(reason: .windowClosed)
                } else if error.code == "invalid_code" || error.code == "bad_code" {
                    snapshot.pairing = .failed(reason: .codeRejected(error.payload.message))
                } else {
                    snapshot.pairing = .failed(reason: .other(error.payload.message))
                }
            }
            return false
        } catch {
            mutateSnapshot { snapshot in
                snapshot.pairing = .failed(reason: .bridgeUnreachable)
                snapshot.lastError = BridgeErrorPayload(message: "\(error)")
            }
            return false
        }
    }

    /// Forget the locally-stored identity for the current Bridge.
    func forget() async {
        guard let device = currentDevice() else { return }
        statusTask?.cancel()
        statusTask = nil
        do {
            try await transport.forgetLocalAuth(device: device)
            try clientStore.deleteIdentity(deviceID: device.deviceID)
        } catch {
            mutateSnapshot { snapshot in
                snapshot.lastError = BridgeErrorPayload(message: "Failed to forget: \(error)")
            }
        }
        mutateSnapshot { snapshot in
            snapshot.pairing = .unpaired
            snapshot.status = nil
        }
        startPairingPolling(device: device)
    }

    // MARK: Status polling

    private func startStatusPolling() {
        guard isWindowVisible else { return }
        statusTask?.cancel()
        statusTask = Task { [weak self] in
            await self?.statusPollBody()
        }
    }

    private func statusPollBody() async {
        // Run once immediately so the overview populates promptly.
        await runStatusFetch()
        while !Task.isCancelled {
            let nanos = UInt64(max(0.5, config.statusInterval) * 1_000_000_000)
            try? await Task.sleep(nanoseconds: nanos)
            if Task.isCancelled { break }
            if !isWindowVisible { break }
            await runStatusFetch()
        }
    }

    /// Force a status fetch on demand (e.g. the manual "Refresh" button).
    func refreshNow() async {
        await runStatusFetch()
    }

    // MARK: Config

    /// Fetch the current bridge config via signed `GET /v1/config`. Called by
    /// the Settings tab on `.onAppear` and after a successful Apply.
    func fetchConfig() async throws -> BridgeConfig {
        guard let device = currentDevice() else {
            throw BridgeAPIError(
                requestID: "local-no-device",
                code: .deviceUnavailable,
                payload: BridgeErrorPayload(message: "No Bridge is currently discovered.")
            )
        }
        return try await transport.getConfig(device: device)
    }

    /// Apply a partial config diff via signed `PUT /v1/config`. Returns the
    /// bridge's fresh canonical state on success; throws
    /// ``BridgeConfigValidationError`` on 422 and ``BridgeAPIError`` on
    /// other failures.
    func applyConfig(diff: [String: Any]) async throws -> BridgeConfig {
        guard let device = currentDevice() else {
            throw BridgeAPIError(
                requestID: "local-no-device",
                code: .deviceUnavailable,
                payload: BridgeErrorPayload(message: "No Bridge is currently discovered.")
            )
        }
        return try await transport.putConfig(device: device, diff: diff)
    }

    private func runStatusFetch() async {
        guard case .paired = snapshot.pairing, let device = currentDevice() else { return }
        do {
            let status = try await transport.status(device: device)
            mutateSnapshot { snapshot in
                snapshot.status = status
                snapshot.lastUpdated = now()
                snapshot.lastError = nil
            }
        } catch let error as BridgeAPIError {
            mutateSnapshot { snapshot in
                snapshot.lastError = error.payload
            }
        } catch {
            mutateSnapshot { snapshot in
                snapshot.lastError = BridgeErrorPayload(message: "\(error)")
            }
        }
    }

    // MARK: Helpers

    private func currentDevice() -> BridgeDevice? {
        switch snapshot.discovery {
        case .found(let device, _): return device
        case .lost(let device, _): return device
        case .searching: return nil
        }
    }

    private func mutateSnapshot(_ mutate: (inout BridgeControlSnapshot) -> Void) {
        var copy = snapshot
        mutate(&copy)
        if copy != snapshot {
            snapshot = copy
        }
    }
}

private extension String {
    var nonEmpty: String? {
        isEmpty ? nil : self
    }
}
