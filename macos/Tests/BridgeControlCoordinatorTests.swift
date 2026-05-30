import Foundation

/// Scripted discovery probe that returns canned results so tests can drive the
/// coordinator without touching real URLSession.
@MainActor
final class ScriptedBridgeDiscoveryProbe: BridgeDiscoveryProbe {
    private(set) var calls: Int = 0
    var result: Result<BridgeDevice, Error>

    init(initial: Result<BridgeDevice, Error>) {
        self.result = initial
    }

    nonisolated func probe(endpoint: URL) async throws -> BridgeDevice {
        await Task { @MainActor in self.calls += 1 }.value
        let snapshot: Result<BridgeDevice, Error> = await Task { @MainActor in self.result }.value
        switch snapshot {
        case .success(let device): return device
        case .failure(let error): throw error
        }
    }
}

final class BridgeControlCoordinatorTests {

    // MARK: - Helpers

    @MainActor
    private func makeDevice(
        deviceID: String = "IB-TESTABCD",
        endpointURL: URL? = URL(string: "http://192.168.7.1:8742"),
        networkLabels: [String] = ["USB IP"]
    ) -> BridgeDevice {
        BridgeDevice(
            deviceID: deviceID,
            displayName: "InstantLink Bridge \(deviceID)",
            softwareVersion: "0.1.17",
            apiVersion: "v1",
            managementPublicKeyFingerprint: nil,
            pairingOpen: false,
            networkLabels: networkLabels,
            endpointURL: endpointURL,
            isPaired: false
        )
    }

    @MainActor
    private func makeStatus(deviceID: String = "IB-TESTABCD") -> BridgeStatus {
        BridgeStatus(
            deviceID: deviceID,
            displayName: "InstantLink Bridge \(deviceID)",
            bridgeVersion: "0.1.17",
            apiVersion: "v1",
            readiness: .ready,
            activeUploadMode: .usbDebug,
            uptimeSeconds: 42,
            network: BridgeNetworkStatus(mode: .usbDebug, label: "USB IP", address: "192.168.7.1", connected: true),
            printer: nil,
            update: nil,
            lastUpload: nil,
            lastError: nil
        )
    }

    @MainActor
    private func makeCoordinator(
        device: BridgeDevice,
        status: BridgeStatus,
        authRequired: Bool = false,
        probeResult: Result<BridgeDevice, Error>? = nil,
        keychainBackend: BridgeKeychainBackend = InMemoryBridgeKeychainBackend(),
        config: BridgeControlCoordinatorConfig = .init(
            probeEndpoints: [URL(string: "http://192.168.7.1:8742")!],
            discoveryIntervalUnpaired: 0.05,
            discoveryIntervalPaired: 0.5,
            statusInterval: 0.05,
            pairingPollInterval: 0.05,
            staleAfter: 0.1
        )
    ) async -> (BridgeControlCoordinator, InMemoryBridgeTransport, ScriptedBridgeDiscoveryProbe) {
        let transport = InMemoryBridgeTransport(
            devices: [device],
            statuses: [device.deviceID: status],
            authRequiredDeviceIDs: authRequired ? [device.deviceID] : []
        )
        let probe = ScriptedBridgeDiscoveryProbe(initial: probeResult ?? .success(device))
        let keychain = BridgeKeychain(backend: keychainBackend)
        let coordinator = BridgeControlCoordinator(
            transport: transport,
            keychain: keychain,
            probe: probe,
            config: config,
            clientNameProvider: { "Test Mac" }
        )
        return (coordinator, transport, probe)
    }

    @MainActor
    private func waitForSnapshot(
        _ coordinator: BridgeControlCoordinator,
        timeout: TimeInterval = 2.0,
        _ matches: @MainActor @escaping (BridgeControlSnapshot) -> Bool
    ) async -> Bool {
        await waitUntil(timeout: timeout) { matches(coordinator.snapshot) }
    }

    // MARK: - Tests

    @MainActor
    func testDiscoveryFoundEmitsDeviceSnapshot() async throws {
        let device = makeDevice()
        let (coordinator, _, _) = await makeCoordinator(device: device, status: makeStatus())
        coordinator.start()
        defer { coordinator.stop() }

        let found = await waitForSnapshot(coordinator) { snapshot in
            if case .found(let d, _) = snapshot.discovery, d.deviceID == device.deviceID { return true }
            return false
        }
        try expectTrue(found)
    }

    @MainActor
    func testDiscoveryLossClearsSnapshot() async throws {
        let device = makeDevice()
        let (coordinator, _, probe) = await makeCoordinator(device: device, status: makeStatus())
        coordinator.start()
        defer { coordinator.stop() }

        let foundFirst = await waitForSnapshot(coordinator) { snapshot in
            if case .found = snapshot.discovery { return true }
            return false
        }
        try expectTrue(foundFirst)

        probe.result = .failure(BridgeHTTPTransportError.invalidResponse)

        let lost = await waitForSnapshot(coordinator, timeout: 3.0) { snapshot in
            if case .lost(let last, _) = snapshot.discovery, last?.deviceID == device.deviceID { return true }
            return false
        }
        try expectTrue(lost)
    }

    @MainActor
    func testPairingStateProgressesOnSuccessfulCompletion() async throws {
        // Use a Wi-Fi-host bridge so the LCD-code pairing path applies. USB
        // hosts now auto-trust silently (plan 038 phase A.1); the LCD-code
        // flow only fires for Bridge Wi-Fi / Same-Wi-Fi paths.
        let device = makeDevice(
            endpointURL: URL(string: "http://192.168.8.1:8742"),
            networkLabels: ["Bridge Wi-Fi"]
        )
        let status = makeStatus()
        let transport = InMemoryBridgeTransport(
            devices: [device],
            statuses: [device.deviceID: status],
            authRequiredDeviceIDs: [device.deviceID]
        )
        await transport.setPairingStatus(
            BridgePairingStatus(
                open: true,
                authImplemented: true,
                confirmationCodeRequired: true,
                expiresAt: Int(Date().addingTimeInterval(60).timeIntervalSince1970),
                expiresInSeconds: 60,
                pairedClientID: nil,
                authorizedClientCount: 0
            ),
            for: device.deviceID
        )

        let probe = ScriptedBridgeDiscoveryProbe(initial: .success(device))
        let keychain = BridgeKeychain(backend: InMemoryBridgeKeychainBackend())
        let coordinator = BridgeControlCoordinator(
            transport: transport,
            keychain: keychain,
            probe: probe,
            config: .init(
                probeEndpoints: [URL(string: "http://192.168.8.1:8742")!],
                discoveryIntervalUnpaired: 0.05,
                discoveryIntervalPaired: 0.5,
                statusInterval: 0.05,
                pairingPollInterval: 0.05,
                staleAfter: 1.0
            ),
            clientNameProvider: { "Test Mac" }
        )
        // Probe endpoints above target the Wi-Fi listener so the LCD-pairing
        // path applies (USB triggers auto-trust, plan 038 A.1).
        coordinator.start()
        defer { coordinator.stop() }

        let windowOpen = await waitForSnapshot(coordinator) { snapshot in
            if case .pairingWindowOpen = snapshot.pairing { return true }
            return false
        }
        try expectTrue(windowOpen)

        coordinator.acknowledgePairingWindowOpen()
        let ok = await coordinator.pair(code: "123456", displayName: "Test Mac")
        try expectTrue(ok)

        let paired: Bool
        if case .paired(let identity) = coordinator.snapshot.pairing,
           identity.deviceID == device.deviceID {
            paired = true
        } else {
            paired = false
        }
        try expectTrue(paired)
    }

    @MainActor
    func testPairingFailureSurfaceErrorAndStaysUnpaired() async throws {
        // Use a Wi-Fi-host bridge so the LCD-code pairing path applies. USB
        // hosts now auto-trust silently (plan 038 phase A.1); the LCD-code
        // flow only fires for Bridge Wi-Fi / Same-Wi-Fi paths.
        let device = makeDevice(
            endpointURL: URL(string: "http://192.168.8.1:8742"),
            networkLabels: ["Bridge Wi-Fi"]
        )
        let transport = InMemoryBridgeTransport(
            devices: [device],
            statuses: [device.deviceID: makeStatus()],
            authRequiredDeviceIDs: [device.deviceID]
        )
        // Pairing window closed — completePairing will throw pairing_not_open.
        await transport.setPairingStatus(
            BridgePairingStatus(
                open: false,
                authImplemented: true,
                confirmationCodeRequired: true,
                expiresAt: nil,
                expiresInSeconds: nil,
                pairedClientID: nil,
                authorizedClientCount: 0
            ),
            for: device.deviceID
        )
        let probe = ScriptedBridgeDiscoveryProbe(initial: .success(device))
        let keychainBackend = InMemoryBridgeKeychainBackend()
        let keychain = BridgeKeychain(backend: keychainBackend)
        let coordinator = BridgeControlCoordinator(
            transport: transport,
            keychain: keychain,
            probe: probe,
            config: .init(
                probeEndpoints: [URL(string: "http://192.168.8.1:8742")!],
                discoveryIntervalUnpaired: 0.05,
                discoveryIntervalPaired: 0.5,
                statusInterval: 0.05,
                pairingPollInterval: 0.05,
                staleAfter: 1.0
            )
        )
        coordinator.start()
        defer { coordinator.stop() }

        let discovered = await waitForSnapshot(coordinator) { snapshot in
            if case .found = snapshot.discovery { return true }
            return false
        }
        try expectTrue(discovered)

        let ok = await coordinator.pair(code: "000000", displayName: "Test")
        try expectFalse(ok)
        switch coordinator.snapshot.pairing {
        case .failed:
            break
        default:
            throw MacTestFailure(file: #filePath, line: #line, message: "expected pairing to land in .failed")
        }
        let identities = try keychain.listIdentities()
        try expectEqual(identities.count, 0)
    }

    @MainActor
    func testStatusPollingPopulatesStatusWhenPaired() async throws {
        let device = makeDevice()
        let status = makeStatus()
        let transport = InMemoryBridgeTransport(
            devices: [device],
            statuses: [device.deviceID: status]
        )
        let probe = ScriptedBridgeDiscoveryProbe(initial: .success(device))
        let keychain = BridgeKeychain(backend: InMemoryBridgeKeychainBackend())

        // Pre-seed an identity so the coordinator transitions to .paired on discovery hit.
        try keychain.saveIdentity(
            BridgeIdentity(
                deviceID: device.deviceID,
                displayName: device.displayName,
                pairedAt: Date(),
                clientID: "macbook",
                clientName: "Test Mac"
            ),
            privateKey: .init()
        )

        let coordinator = BridgeControlCoordinator(
            transport: transport,
            keychain: keychain,
            probe: probe,
            config: .init(
                probeEndpoints: [URL(string: "http://192.168.7.1:8742")!],
                discoveryIntervalUnpaired: 0.05,
                discoveryIntervalPaired: 0.5,
                statusInterval: 0.05,
                pairingPollInterval: 0.05,
                staleAfter: 1.0
            )
        )
        coordinator.start()
        defer { coordinator.stop() }

        let populated = await waitForSnapshot(coordinator) { snapshot in
            snapshot.status?.deviceID == device.deviceID
        }
        try expectTrue(populated)
    }

    @MainActor
    func testForgetClearsIdentityAndReturnsToUnpaired() async throws {
        let device = makeDevice()
        let transport = InMemoryBridgeTransport(
            devices: [device],
            statuses: [device.deviceID: makeStatus()]
        )
        let backend = InMemoryBridgeKeychainBackend()
        let keychain = BridgeKeychain(backend: backend)
        try keychain.saveIdentity(
            BridgeIdentity(
                deviceID: device.deviceID,
                displayName: device.displayName,
                pairedAt: Date(),
                clientID: "macbook",
                clientName: "Test Mac"
            ),
            privateKey: .init()
        )
        let probe = ScriptedBridgeDiscoveryProbe(initial: .success(device))
        let coordinator = BridgeControlCoordinator(
            transport: transport,
            keychain: keychain,
            probe: probe,
            config: .init(
                probeEndpoints: [URL(string: "http://192.168.7.1:8742")!],
                discoveryIntervalUnpaired: 0.05,
                discoveryIntervalPaired: 0.5,
                statusInterval: 0.05,
                pairingPollInterval: 0.05,
                staleAfter: 1.0
            )
        )
        coordinator.start()
        defer { coordinator.stop() }

        let paired = await waitForSnapshot(coordinator) { snapshot in
            if case .paired = snapshot.pairing { return true }
            return false
        }
        try expectTrue(paired)

        await coordinator.forget()

        let unpaired: Bool
        switch coordinator.snapshot.pairing {
        case .unpaired, .pairingWindowOpen, .failed:
            unpaired = true
        default:
            unpaired = false
        }
        try expectTrue(unpaired)

        let identities = try keychain.listIdentities()
        try expectEqual(identities.count, 0)
    }

    // MARK: - Plan 038 phase A.1: USB auto-trust

    @MainActor
    func testDiscoveryOverUSBTriggersAutoTrust() async throws {
        // USB device hosted at 192.168.7.1 → should silently auto-trust.
        let device = makeDevice() // endpointURL is http://192.168.7.1:8742 (see makeDevice).
        let transport = InMemoryBridgeTransport(
            devices: [device],
            statuses: [device.deviceID: makeStatus()],
            authRequiredDeviceIDs: [device.deviceID]
        )
        let probe = ScriptedBridgeDiscoveryProbe(initial: .success(device))
        let keychain = BridgeKeychain(backend: InMemoryBridgeKeychainBackend())
        let coordinator = BridgeControlCoordinator(
            transport: transport,
            keychain: keychain,
            probe: probe,
            config: .init(
                probeEndpoints: [URL(string: "http://192.168.7.1:8742")!],
                discoveryIntervalUnpaired: 0.05,
                discoveryIntervalPaired: 0.5,
                statusInterval: 0.05,
                pairingPollInterval: 0.05,
                staleAfter: 1.0
            ),
            clientNameProvider: { "Test Mac" }
        )
        coordinator.start()
        defer { coordinator.stop() }

        let paired = await waitForSnapshot(coordinator) { snapshot in
            if case .paired = snapshot.pairing { return true }
            return false
        }
        try expectTrue(paired)

        let calls = await transport.usbAutoTrustCalls
        try expectTrue(calls >= 1)
    }

    @MainActor
    func testDiscoveryOverWiFiDoesNotAutoTrust() async throws {
        // Bridge Wi-Fi at 192.168.8.1 → should NOT call usbAutoTrust.
        let device = BridgeDevice(
            deviceID: "IB-WIFI0001",
            displayName: "InstantLink Bridge IB-WIFI0001",
            softwareVersion: "0.1.17",
            apiVersion: "v1",
            networkLabels: ["Bridge Wi-Fi"],
            endpointURL: URL(string: "http://192.168.8.1:8742"),
            isPaired: false
        )
        let transport = InMemoryBridgeTransport(
            devices: [device],
            statuses: [device.deviceID: BridgeStatus(
                deviceID: device.deviceID,
                displayName: device.displayName,
                bridgeVersion: "0.1.17",
                apiVersion: "v1",
                readiness: .ready,
                activeUploadMode: .bridgeWiFi
            )],
            authRequiredDeviceIDs: [device.deviceID]
        )
        await transport.setPairingStatus(
            BridgePairingStatus(
                open: false,
                authImplemented: true,
                confirmationCodeRequired: true,
                expiresAt: nil,
                expiresInSeconds: nil,
                pairedClientID: nil,
                authorizedClientCount: 0
            ),
            for: device.deviceID
        )
        let probe = ScriptedBridgeDiscoveryProbe(initial: .success(device))
        let keychain = BridgeKeychain(backend: InMemoryBridgeKeychainBackend())
        let coordinator = BridgeControlCoordinator(
            transport: transport,
            keychain: keychain,
            probe: probe,
            config: .init(
                probeEndpoints: [URL(string: "http://192.168.8.1:8742")!],
                discoveryIntervalUnpaired: 0.05,
                discoveryIntervalPaired: 0.5,
                statusInterval: 0.05,
                pairingPollInterval: 0.05,
                staleAfter: 1.0
            ),
            clientNameProvider: { "Test Mac" }
        )
        coordinator.start()
        defer { coordinator.stop() }

        let discovered = await waitForSnapshot(coordinator) { snapshot in
            if case .found(_, let medium) = snapshot.discovery, medium == .bridgeWiFi { return true }
            return false
        }
        try expectTrue(discovered)

        // Give the coordinator a beat to run any auto-trust path.
        try await Task.sleep(nanoseconds: 200_000_000)

        let calls = await transport.usbAutoTrustCalls
        try expectEqual(calls, 0)
        // Pairing must remain on a non-paired branch.
        switch coordinator.snapshot.pairing {
        case .paired:
            throw MacTestFailure(file: #filePath, line: #line, message: "Wi-Fi must not auto-trust")
        default:
            break
        }
    }

    @MainActor
    func testAutoTrustFailureLeavesPairingUnpaired() async throws {
        let device = makeDevice()
        let transport = InMemoryBridgeTransport(
            devices: [device],
            statuses: [device.deviceID: makeStatus()],
            authRequiredDeviceIDs: [device.deviceID]
        )
        await transport.setUSBAutoTrustShouldReject(true, for: device.deviceID)
        let probe = ScriptedBridgeDiscoveryProbe(initial: .success(device))
        let keychain = BridgeKeychain(backend: InMemoryBridgeKeychainBackend())
        let coordinator = BridgeControlCoordinator(
            transport: transport,
            keychain: keychain,
            probe: probe,
            config: .init(
                probeEndpoints: [URL(string: "http://192.168.7.1:8742")!],
                discoveryIntervalUnpaired: 0.05,
                discoveryIntervalPaired: 0.5,
                statusInterval: 0.05,
                pairingPollInterval: 0.05,
                staleAfter: 1.0
            ),
            clientNameProvider: { "Test Mac" }
        )
        coordinator.start()
        defer { coordinator.stop() }

        // Discovery should land.
        let found = await waitForSnapshot(coordinator) { snapshot in
            if case .found(_, let medium) = snapshot.discovery, medium == .usb { return true }
            return false
        }
        try expectTrue(found)

        // Give the auto-trust call time to run and fail.
        try await Task.sleep(nanoseconds: 300_000_000)

        // Pairing must NOT be in .paired.
        switch coordinator.snapshot.pairing {
        case .paired:
            throw MacTestFailure(file: #filePath, line: #line, message: "expected pairing to stay unpaired")
        default:
            break
        }
        try expectTrue(coordinator.snapshot.lastError != nil)
    }

    @MainActor
    func testAutoTrustSkippedWhenIdentityAlreadySaved() async throws {
        let device = makeDevice()
        let transport = InMemoryBridgeTransport(
            devices: [device],
            statuses: [device.deviceID: makeStatus()]
        )
        let keychain = BridgeKeychain(backend: InMemoryBridgeKeychainBackend())
        // Pre-seed a saved identity for this device.
        try keychain.saveIdentity(
            BridgeIdentity(
                deviceID: device.deviceID,
                displayName: device.displayName,
                pairedAt: Date(),
                clientID: "macbook",
                clientName: "Test Mac"
            ),
            privateKey: .init()
        )
        let probe = ScriptedBridgeDiscoveryProbe(initial: .success(device))
        let coordinator = BridgeControlCoordinator(
            transport: transport,
            keychain: keychain,
            probe: probe,
            config: .init(
                probeEndpoints: [URL(string: "http://192.168.7.1:8742")!],
                discoveryIntervalUnpaired: 0.05,
                discoveryIntervalPaired: 0.5,
                statusInterval: 0.05,
                pairingPollInterval: 0.05,
                staleAfter: 1.0
            ),
            clientNameProvider: { "Test Mac" }
        )
        coordinator.start()
        defer { coordinator.stop() }

        let paired = await waitForSnapshot(coordinator) { snapshot in
            if case .paired = snapshot.pairing { return true }
            return false
        }
        try expectTrue(paired)

        // Auto-trust must NOT have been called: the saved identity should win.
        let calls = await transport.usbAutoTrustCalls
        try expectEqual(calls, 0)
    }

    @MainActor
    func testLastAutoTrustEventPublishedOnSuccess() async throws {
        let device = makeDevice()
        let transport = InMemoryBridgeTransport(
            devices: [device],
            statuses: [device.deviceID: makeStatus()],
            authRequiredDeviceIDs: [device.deviceID]
        )
        let probe = ScriptedBridgeDiscoveryProbe(initial: .success(device))
        let keychain = BridgeKeychain(backend: InMemoryBridgeKeychainBackend())
        let coordinator = BridgeControlCoordinator(
            transport: transport,
            keychain: keychain,
            probe: probe,
            config: .init(
                probeEndpoints: [URL(string: "http://192.168.7.1:8742")!],
                discoveryIntervalUnpaired: 0.05,
                discoveryIntervalPaired: 0.5,
                statusInterval: 0.05,
                pairingPollInterval: 0.05,
                staleAfter: 1.0
            ),
            clientNameProvider: { "Test Mac" }
        )
        coordinator.start()
        defer { coordinator.stop() }

        let published = await waitForSnapshot(coordinator) { snapshot in
            snapshot.lastAutoTrustEvent != nil
        }
        try expectTrue(published)
    }

    @MainActor
    func testCoordinatorPausesPollingWhenWindowHides() async throws {
        let device = makeDevice()
        let transport = InMemoryBridgeTransport(
            devices: [device],
            statuses: [device.deviceID: makeStatus()]
        )
        let probe = ScriptedBridgeDiscoveryProbe(initial: .success(device))
        let keychain = BridgeKeychain(backend: InMemoryBridgeKeychainBackend())
        try keychain.saveIdentity(
            BridgeIdentity(
                deviceID: device.deviceID,
                displayName: device.displayName,
                pairedAt: Date(),
                clientID: "macbook",
                clientName: "Test Mac"
            ),
            privateKey: .init()
        )
        let coordinator = BridgeControlCoordinator(
            transport: transport,
            keychain: keychain,
            probe: probe,
            config: .init(
                probeEndpoints: [URL(string: "http://192.168.7.1:8742")!],
                discoveryIntervalUnpaired: 0.05,
                discoveryIntervalPaired: 0.5,
                statusInterval: 0.05,
                pairingPollInterval: 0.05,
                staleAfter: 1.0
            )
        )
        coordinator.start()
        defer { coordinator.stop() }

        let populated = await waitForSnapshot(coordinator) { snapshot in
            snapshot.status?.deviceID == device.deviceID
        }
        try expectTrue(populated)

        coordinator.onWindowVisibilityChanged(false)
        // Drain a status interval to confirm no further reads when paused.
        try await Task.sleep(nanoseconds: 200_000_000)
        // Mutate the upstream status; if polling were still active, snapshot would update.
        await transport.addDevice(
            BridgeDevice(
                deviceID: device.deviceID,
                displayName: "Renamed Bridge",
                softwareVersion: "0.1.99",
                apiVersion: "v1"
            ),
            status: BridgeStatus(
                deviceID: device.deviceID,
                displayName: "Renamed Bridge",
                bridgeVersion: "0.1.99",
                apiVersion: "v1",
                readiness: .ready,
                activeUploadMode: .usbDebug
            )
        )
        try await Task.sleep(nanoseconds: 300_000_000)
        try expectEqual(coordinator.snapshot.status?.bridgeVersion, "0.1.17")
    }
}
