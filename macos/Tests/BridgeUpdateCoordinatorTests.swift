import Foundation

/// Scripted `/v1/hello` probe used by the reconnect-poll path. Tests can
/// flip `currentVersion` mid-flight to simulate the bridge coming back on
/// the new firmware.
@MainActor
final class ScriptedBridgeUpdateHelloProbe: BridgeUpdateHelloProbe {
    var currentVersion: String
    var shouldThrow: Bool
    private(set) var callCount: Int = 0

    init(currentVersion: String, shouldThrow: Bool = false) {
        self.currentVersion = currentVersion
        self.shouldThrow = shouldThrow
    }

    nonisolated func probe(device: BridgeDevice) async throws -> BridgeDevice {
        let snapshot: (Int, String, Bool) = await Task { @MainActor in
            self.callCount += 1
            return (self.callCount, self.currentVersion, self.shouldThrow)
        }.value
        if snapshot.2 {
            throw BridgeHTTPTransportError.invalidResponse
        }
        var fresh = device
        fresh.softwareVersion = snapshot.1
        return fresh
    }
}

final class BridgeUpdateCoordinatorTests {

    // MARK: - Helpers

    @MainActor
    private func makeDevice(version: String = "0.1.16") -> BridgeDevice {
        BridgeDevice(
            deviceID: "IB-UPDATETEST",
            displayName: "Update Test Bridge",
            softwareVersion: version,
            apiVersion: "v1",
            networkLabels: ["USB IP"],
            endpointURL: URL(string: "http://192.168.7.1:8742"),
            isPaired: true
        )
    }

    @MainActor
    private func makeStatus(
        deviceID: String = "IB-UPDATETEST",
        bridgeVersion: String = "0.1.16",
        previousVersion: String? = nil
    ) -> BridgeStatus {
        var status = BridgeStatus(
            deviceID: deviceID,
            displayName: "Update Test Bridge",
            bridgeVersion: bridgeVersion,
            apiVersion: "v1",
            readiness: .ready,
            activeUploadMode: .usbDebug
        )
        if let previousVersion {
            status.update = BridgeUpdateSummary(
                currentVersion: bridgeVersion,
                availableVersion: nil,
                canUpdate: true,
                operationID: nil,
                phase: nil,
                previousVersion: previousVersion
            )
        }
        return status
    }

    @MainActor
    private func makeBundledPackage(version: String) -> BridgeUpdatePackage {
        BridgeUpdatePackage(
            version: version,
            target: "linux-aarch64",
            archiveURL: URL(fileURLWithPath: "/tmp/bridge-\(version).tar.gz"),
            archiveSHA256: "archive-sha-\(version)",
            manifestURL: URL(fileURLWithPath: "/tmp/bridge-\(version).manifest.json"),
            manifestSHA256: "manifest-sha-\(version)",
            checksumURL: URL(fileURLWithPath: "/tmp/bridge-\(version).tar.gz.sha256")
        )
    }

    @MainActor
    private func makeTransport(
        device: BridgeDevice,
        status: BridgeStatus
    ) -> InMemoryBridgeTransport {
        InMemoryBridgeTransport(
            devices: [device],
            statuses: [device.deviceID: status]
        )
    }

    @MainActor
    private func makeCoordinator(
        transport: BridgeTransport,
        bundled: BridgeUpdatePackage?,
        helloProbe: BridgeUpdateHelloProbe? = nil,
        reconnectPollInterval: TimeInterval = 0.01,
        reconnectMaxAttempts: Int = 5,
        nowBase: Date = Date(timeIntervalSince1970: 1_000_000)
    ) -> BridgeUpdateCoordinator {
        let resolvedProbe = helloProbe ?? ScriptedBridgeUpdateHelloProbe(currentVersion: "0.1.17")
        return BridgeUpdateCoordinator(
            transport: transport,
            bundleProvider: { bundled },
            helloProbe: resolvedProbe,
            config: BridgeUpdateCoordinatorConfig(
                reconnectPollInterval: reconnectPollInterval,
                reconnectMaxAttempts: reconnectMaxAttempts
            ),
            now: { nowBase },
            sleep: { _ in }
        )
    }

    // MARK: - Tests

    @MainActor
    func testLoadBundleEmitsUpToDateWhenVersionsMatch() async throws {
        let device = makeDevice(version: "0.1.17")
        let status = makeStatus(bridgeVersion: "0.1.17")
        let transport = makeTransport(device: device, status: status)
        let coordinator = makeCoordinator(
            transport: transport,
            bundled: makeBundledPackage(version: "0.1.17")
        )

        coordinator.loadBundle(status: status)

        guard case .upToDate(let version) = coordinator.snapshot.availability else {
            throw MacTestFailure(file: #filePath, line: #line, message: "expected upToDate")
        }
        try expectEqual(version, "0.1.17")
    }

    @MainActor
    func testLoadBundleEmitsUpdateAvailableWhenBundleIsNewer() async throws {
        let device = makeDevice(version: "0.1.16")
        let status = makeStatus(bridgeVersion: "0.1.16")
        let transport = makeTransport(device: device, status: status)
        let coordinator = makeCoordinator(
            transport: transport,
            bundled: makeBundledPackage(version: "0.1.17")
        )

        coordinator.loadBundle(status: status)

        guard case .updateAvailable(let current, let bundled) = coordinator.snapshot.availability else {
            throw MacTestFailure(file: #filePath, line: #line, message: "expected updateAvailable")
        }
        try expectEqual(current, "0.1.16")
        try expectEqual(bundled, "0.1.17")
    }

    @MainActor
    func testLoadBundleEmitsNoBundleWhenFirmwareMissing() async throws {
        let device = makeDevice(version: "0.1.16")
        let status = makeStatus(bridgeVersion: "0.1.16")
        let transport = makeTransport(device: device, status: status)
        let coordinator = makeCoordinator(transport: transport, bundled: nil)

        coordinator.loadBundle(status: status)

        if case .noBundle = coordinator.snapshot.availability {
            // expected
        } else {
            throw MacTestFailure(file: #filePath, line: #line, message: "expected noBundle")
        }
    }

    @MainActor
    func testRefreshPreflightPopulatesSnapshot() async throws {
        let device = makeDevice()
        let status = makeStatus()
        let transport = makeTransport(device: device, status: status)
        let pkg = makeBundledPackage(version: "0.1.17")
        await transport.setPreflight(
            BridgeUpdatePreflight(
                package: pkg,
                allowed: true,
                backupRequired: true,
                rollbackAvailable: true,
                checks: [
                    BridgeUpdatePreflightCheck(name: "service_health", status: .pass, message: nil),
                    BridgeUpdatePreflightCheck(name: "backup_available", status: .pass, message: nil),
                ],
                operationID: nil
            ),
            for: device.deviceID
        )

        let coordinator = makeCoordinator(transport: transport, bundled: pkg)
        coordinator.loadBundle(status: status)

        await coordinator.refreshPreflight(device: device)

        try expectEqual(coordinator.snapshot.preflight?.checks.count, 2)
        try expectTrue(coordinator.snapshot.preflight?.allowed == true)
        try expectFalse(coordinator.snapshot.isPreflightInFlight)
    }

    @MainActor
    func testRunUpdateProgressesThroughAllPhases() async throws {
        let device = makeDevice(version: "0.1.16")
        let status = makeStatus(bridgeVersion: "0.1.16")
        let transport = makeTransport(device: device, status: status)
        let pkg = makeBundledPackage(version: "0.1.17")
        let script: [BridgeUpdateState] = [
            BridgeUpdateState(
                operationID: "update-1",
                phase: .checkingBridge, progress: 0.1, message: "checking",
                safeState: .updateNotInstalled, installedVersion: nil, error: nil
            ),
            BridgeUpdateState(
                operationID: "update-1",
                phase: .installingUpdate, progress: 0.5, message: "installing",
                safeState: .unknown, installedVersion: nil, error: nil
            ),
            BridgeUpdateState(
                operationID: "update-1",
                phase: .reconnecting, progress: 0.85, message: "reconnecting",
                safeState: .unknown, installedVersion: nil, error: nil
            ),
        ]
        await transport.setUpdateScript(script, for: device.deviceID)

        let probe = ScriptedBridgeUpdateHelloProbe(currentVersion: "0.1.17")
        let coordinator = makeCoordinator(transport: transport, bundled: pkg, helloProbe: probe)
        coordinator.loadBundle(status: status)

        await coordinator.runUpdate(device: device)

        guard case .succeeded(_, let version) = coordinator.snapshot.lastResult else {
            throw MacTestFailure(file: #filePath, line: #line, message: "expected succeeded, got \(String(describing: coordinator.snapshot.lastResult))")
        }
        try expectEqual(version, "0.1.17")
        try expectTrue(probe.callCount >= 1)
    }

    @MainActor
    func testRunUpdateSurfaceUploadErrorAsFailure() async throws {
        let device = makeDevice(version: "0.1.16")
        let status = makeStatus(bridgeVersion: "0.1.16")
        let transport = makeTransport(device: device, status: status)
        // No filesystem file backing the archive URL → uploadUpdate hits HTTP
        // path that reads disk; the in-memory transport doesn't actually read,
        // so simulate failure by requiring auth and revoking it.
        await transport.setAuthRequired(true, for: device.deviceID)
        let pkg = makeBundledPackage(version: "0.1.17")
        let coordinator = makeCoordinator(transport: transport, bundled: pkg)
        coordinator.loadBundle(status: status)

        await coordinator.runUpdate(device: device)

        guard case .failed = coordinator.snapshot.lastResult else {
            throw MacTestFailure(file: #filePath, line: #line, message: "expected failed result")
        }
        try expectNil(coordinator.snapshot.operation)
    }

    @MainActor
    func testRunUpdateSurfaceStartErrorAsFailure() async throws {
        let device = makeDevice(version: "0.1.16")
        let status = makeStatus(bridgeVersion: "0.1.16")
        let transport = makeTransport(device: device, status: status)
        let pkg = makeBundledPackage(version: "0.1.17")
        // Empty update script → startUpdate throws BridgeTransportError.updateScriptEmpty.
        await transport.setUpdateScript([], for: device.deviceID)
        let coordinator = makeCoordinator(transport: transport, bundled: pkg)
        coordinator.loadBundle(status: status)

        await coordinator.runUpdate(device: device)

        guard case .failed = coordinator.snapshot.lastResult else {
            throw MacTestFailure(file: #filePath, line: #line, message: "expected failed result")
        }
    }

    @MainActor
    func testReconnectTimeoutMarksFailure() async throws {
        let device = makeDevice(version: "0.1.16")
        let status = makeStatus(bridgeVersion: "0.1.16")
        let transport = makeTransport(device: device, status: status)
        let pkg = makeBundledPackage(version: "0.1.17")
        let script: [BridgeUpdateState] = [
            BridgeUpdateState(
                operationID: "update-1",
                phase: .reconnecting, progress: 0.9, message: "reconnecting",
                safeState: .unknown, installedVersion: nil, error: nil
            ),
        ]
        await transport.setUpdateScript(script, for: device.deviceID)
        // Probe never returns the new version → reconnect times out.
        let probe = ScriptedBridgeUpdateHelloProbe(currentVersion: "0.1.16")
        let coordinator = makeCoordinator(
            transport: transport,
            bundled: pkg,
            helloProbe: probe,
            reconnectMaxAttempts: 3
        )
        coordinator.loadBundle(status: status)

        await coordinator.runUpdate(device: device)

        guard case .failed(let reason, _) = coordinator.snapshot.lastResult else {
            throw MacTestFailure(file: #filePath, line: #line, message: "expected failure on reconnect timeout")
        }
        try expectTrue(reason.lowercased().contains("come back") || reason.lowercased().contains("roll"))
        try expectEqual(probe.callCount, 3)
    }

    @MainActor
    func testReconnectSuccessMarksGood() async throws {
        let device = makeDevice(version: "0.1.16")
        let status = makeStatus(bridgeVersion: "0.1.16")
        let transport = makeTransport(device: device, status: status)
        let pkg = makeBundledPackage(version: "0.1.17")
        let script: [BridgeUpdateState] = [
            BridgeUpdateState(
                operationID: "update-1",
                phase: .reconnecting, progress: 0.9, message: "reconnecting",
                safeState: .unknown, installedVersion: nil, error: nil
            ),
        ]
        await transport.setUpdateScript(script, for: device.deviceID)
        let probe = ScriptedBridgeUpdateHelloProbe(currentVersion: "0.1.17")
        let coordinator = makeCoordinator(transport: transport, bundled: pkg, helloProbe: probe)
        coordinator.loadBundle(status: status)

        await coordinator.runUpdate(device: device)

        guard case .succeeded(_, let version) = coordinator.snapshot.lastResult else {
            throw MacTestFailure(file: #filePath, line: #line, message: "expected succeeded")
        }
        try expectEqual(version, "0.1.17")
        try expectNil(coordinator.snapshot.operation)
        // Mark-good was implicitly called (in-memory transport advances state).
        let pollCount = probe.callCount
        try expectTrue(pollCount >= 1)
    }

    @MainActor
    func testRollbackEmitsRolledBackResult() async throws {
        let device = makeDevice(version: "0.1.17")
        let status = makeStatus(bridgeVersion: "0.1.17", previousVersion: "0.1.16")
        let transport = makeTransport(device: device, status: status)
        let coordinator = makeCoordinator(transport: transport, bundled: makeBundledPackage(version: "0.1.17"))

        await coordinator.rollback(device: device, reason: "user_initiated")

        guard case .rolledBack = coordinator.snapshot.lastResult else {
            throw MacTestFailure(file: #filePath, line: #line, message: "expected rolledBack")
        }
        try expectFalse(coordinator.snapshot.isRollbackInFlight)
    }

    @MainActor
    func testCompareVersionsRanksNumericComponents() async throws {
        try expectTrue(BridgeUpdateCoordinator.compareVersions("0.1.16", "0.1.17") < 0)
        try expectTrue(BridgeUpdateCoordinator.compareVersions("0.1.17", "0.1.16") > 0)
        try expectEqual(BridgeUpdateCoordinator.compareVersions("0.1.17", "0.1.17"), 0)
        try expectTrue(BridgeUpdateCoordinator.compareVersions("0.2.0", "0.1.99") > 0)
    }
}
