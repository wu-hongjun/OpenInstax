import Foundation

final class BridgeDiagnosticsCoordinatorTests {

    // MARK: - Helpers

    @MainActor
    private func makeDevice(deviceID: String = "IB-DIAGTEST") -> BridgeDevice {
        BridgeDevice(
            deviceID: deviceID,
            displayName: "Diagnostics Test Bridge",
            softwareVersion: "0.1.23",
            apiVersion: "v1",
            networkLabels: ["USB IP"],
            endpointURL: URL(string: "http://192.168.7.1:8742"),
            isPaired: true
        )
    }

    @MainActor
    private func makeStatus(deviceID: String = "IB-DIAGTEST") -> BridgeStatus {
        BridgeStatus(
            deviceID: deviceID,
            displayName: "Diagnostics Test Bridge",
            bridgeVersion: "0.1.23",
            apiVersion: "v1",
            readiness: .ready,
            activeUploadMode: .usbDebug
        )
    }

    @MainActor
    private func makeTransport(device: BridgeDevice) -> InMemoryBridgeTransport {
        InMemoryBridgeTransport(
            devices: [device],
            statuses: [device.deviceID: makeStatus(deviceID: device.deviceID)]
        )
    }

    @MainActor
    private func makeCoordinator(
        transport: BridgeTransport,
        nowBase: Date = Date(timeIntervalSince1970: 1_700_000_000),
        config: BridgeDiagnosticsCoordinatorConfig = .default
    ) -> BridgeDiagnosticsCoordinator {
        BridgeDiagnosticsCoordinator(
            transport: transport,
            config: config,
            now: { nowBase }
        )
    }

    private func makeLogEvents() -> [BridgeLogEvent] {
        [
            BridgeLogEvent(
                id: "evt-1",
                timestamp: "2026-05-30T10:00:00Z",
                level: .info,
                message: "bridge.boot ready"
            ),
            BridgeLogEvent(
                id: "evt-2",
                timestamp: "2026-05-30T10:00:01Z",
                level: .warning,
                message: "bridge.printer reconnect_pending"
            ),
            BridgeLogEvent(
                id: "evt-3",
                timestamp: "2026-05-30T10:00:02Z",
                level: .error,
                message: "bridge.ftp upload_failed reason=timeout"
            ),
        ]
    }

    // MARK: - Log streaming

    @MainActor
    func testStartStreamingAppendsScriptedEventsAndCapsAtMaxTail() async throws {
        let device = makeDevice()
        let transport = makeTransport(device: device)
        // Script a few more events than the tail cap so we can prove the
        // circular-buffer behaviour.
        var events: [BridgeLogEvent] = []
        for i in 0..<210 {
            events.append(
                BridgeLogEvent(
                    id: "evt-\(i)",
                    timestamp: "2026-05-30T10:00:00Z",
                    level: .info,
                    message: "bridge.evt \(i)"
                )
            )
        }
        await transport.setLogScript(events, for: device.deviceID)
        let coordinator = makeCoordinator(transport: transport)

        coordinator.startStreaming(device: device)
        let drained = await waitUntil(timeout: 2.0) {
            coordinator.snapshot.logTail.count == 200
        }
        try expectTrue(drained, "expected coordinator to drain all scripted events into the capped tail")
        try expectEqual(coordinator.snapshot.logTail.count, 200)
        // First entry should be evt-10 (the first overflow drop trims the
        // oldest events first), last entry evt-209.
        try expectEqual(coordinator.snapshot.logTail.first?.id, "evt-10")
        try expectEqual(coordinator.snapshot.logTail.last?.id, "evt-209")
    }

    @MainActor
    func testStartStreamingTransitionsLiveToDisconnectedWhenSourceFinishes() async throws {
        let device = makeDevice()
        let transport = makeTransport(device: device)
        await transport.setLogScript(makeLogEvents(), for: device.deviceID)
        let coordinator = makeCoordinator(transport: transport)

        coordinator.startStreaming(device: device)
        let reached = await waitUntil(timeout: 2.0) {
            if case .disconnected = coordinator.snapshot.streamState { return true }
            return false
        }
        try expectTrue(reached, "expected stream to flip to disconnected when source finishes")
        try expectEqual(coordinator.snapshot.logTail.count, 3)
    }

    @MainActor
    func testStopStreamingMarksSnapshotPausedAndPreservesTail() async throws {
        let device = makeDevice()
        let transport = makeTransport(device: device)
        await transport.setLogScript(makeLogEvents(), for: device.deviceID)
        let coordinator = makeCoordinator(transport: transport)

        coordinator.startStreaming(device: device)
        _ = await waitUntil(timeout: 2.0) {
            coordinator.snapshot.logTail.count >= 1
        }
        coordinator.stopStreaming()
        guard case .paused = coordinator.snapshot.streamState else {
            throw MacTestFailure(
                file: #filePath,
                line: #line,
                message: "expected paused state, got \(coordinator.snapshot.streamState)"
            )
        }
        try expectTrue(!coordinator.snapshot.logTail.isEmpty, "expected tail to survive stopStreaming")
    }

    @MainActor
    func testSetFilterUpdatesSnapshotAndRestartsStreamWhenLive() async throws {
        let device = makeDevice()
        let transport = makeTransport(device: device)
        await transport.setLogScript(makeLogEvents(), for: device.deviceID)
        let coordinator = makeCoordinator(transport: transport)

        coordinator.startStreaming(device: device)
        _ = await waitUntil(timeout: 2.0) {
            coordinator.snapshot.logTail.count == 3
        }
        // Clear the tail so the filter-restart accumulation is observable.
        coordinator.clearTail()

        coordinator.setFilter(.error)
        try expectEqual(coordinator.snapshot.logLevelFilter, .error)
        // After restart with .error filter the InMemoryBridgeTransport applies
        // its min-level filter; only the single error event should populate
        // the tail.
        let observed = await waitUntil(timeout: 2.0) {
            coordinator.snapshot.logTail.contains { $0.level == .error }
                && coordinator.snapshot.logTail.allSatisfy { $0.level == .error }
        }
        try expectTrue(observed, "expected stream restart to filter down to only error events")
        try expectTrue(coordinator.snapshot.logTail.count >= 1)
    }

    @MainActor
    func testStreamingErrorSurfacesDisconnectedWithReason() async throws {
        let device = makeDevice()
        let transport = makeTransport(device: device)
        await transport.setLogStreamError(BridgeHTTPTransportError.invalidResponse, for: device.deviceID)
        let coordinator = makeCoordinator(transport: transport)

        coordinator.startStreaming(device: device)
        let surfaced = await waitUntil(timeout: 2.0) {
            if case .disconnected(let reason, _) = coordinator.snapshot.streamState {
                return reason != nil
            }
            return false
        }
        try expectTrue(surfaced, "expected stream error to surface a reason on the snapshot")
    }

    // MARK: - Support bundle

    @MainActor
    func testCreateSupportBundleHappyPathReturnsResult() async throws {
        let device = makeDevice()
        let transport = makeTransport(device: device)
        let coordinator = makeCoordinator(transport: transport)

        await coordinator.createSupportBundle(device: device, destinationURL: nil)
        guard case .ready(let bundle, let savedTo, _) = coordinator.snapshot.supportBundle else {
            throw MacTestFailure(
                file: #filePath,
                line: #line,
                message: "expected ready, got \(coordinator.snapshot.supportBundle)"
            )
        }
        try expectEqual(bundle.bundleID, "support-inmemory-\(device.deviceID)")
        try expectTrue(savedTo == nil, "expected no sidecar to be written when destinationURL is nil")
        let calls = await transport.createSupportBundleCalls
        try expectEqual(calls, 1)
    }

    @MainActor
    func testCreateSupportBundleWritesSidecarToDestination() async throws {
        let device = makeDevice()
        let transport = makeTransport(device: device)
        let coordinator = makeCoordinator(transport: transport)
        let destination = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("bridge-support-test-\(UUID().uuidString).json")
        defer { try? FileManager.default.removeItem(at: destination) }

        await coordinator.createSupportBundle(device: device, destinationURL: destination)
        guard case .ready(_, let savedTo, _) = coordinator.snapshot.supportBundle else {
            throw MacTestFailure(
                file: #filePath,
                line: #line,
                message: "expected ready, got \(coordinator.snapshot.supportBundle)"
            )
        }
        try expectTrue(savedTo == destination, "expected sidecar to be written to the supplied URL")
        try expectTrue(FileManager.default.fileExists(atPath: destination.path))
    }

    @MainActor
    func testCreateSupportBundleSurfacesError() async throws {
        let device = makeDevice()
        let transport = makeTransport(device: device)
        await transport.setSupportBundleShouldFail(true, for: device.deviceID)
        let coordinator = makeCoordinator(transport: transport)

        await coordinator.createSupportBundle(device: device, destinationURL: nil)
        guard case .failed = coordinator.snapshot.supportBundle else {
            throw MacTestFailure(file: #filePath, line: #line, message: "expected failed bundle state")
        }
    }

    // MARK: - Recovery state machine

    @MainActor
    func testEvaluateHealthOnHelloFailureCrossesThresholdAndSurfacesBanner() async throws {
        let device = makeDevice()
        let transport = makeTransport(device: device)
        let coordinator = makeCoordinator(transport: transport)

        coordinator.evaluateHealthOnHelloFailure(consecutiveFailures: 1, hasUSBCarrier: true)
        try expectEqual(coordinator.snapshot.recovery, .ok)
        coordinator.evaluateHealthOnHelloFailure(consecutiveFailures: 3, hasUSBCarrier: true)
        guard case .managementUnavailable = coordinator.snapshot.recovery else {
            throw MacTestFailure(
                file: #filePath,
                line: #line,
                message: "expected managementUnavailable after threshold, got \(coordinator.snapshot.recovery)"
            )
        }
    }

    @MainActor
    func testEvaluateHealthOnHelloFailureWithoutUSBCarrierDoesNotSurfaceBanner() async throws {
        let device = makeDevice()
        let transport = makeTransport(device: device)
        let coordinator = makeCoordinator(transport: transport)
        coordinator.evaluateHealthOnHelloFailure(consecutiveFailures: 5, hasUSBCarrier: false)
        try expectEqual(coordinator.snapshot.recovery, .ok)
    }

    @MainActor
    func testEvaluateHealthOnHelloSuccessTransitionsToRecovered() async throws {
        let device = makeDevice()
        let transport = makeTransport(device: device)
        let coordinator = makeCoordinator(transport: transport)
        coordinator.evaluateHealthOnHelloFailure(consecutiveFailures: 3, hasUSBCarrier: true)
        coordinator.evaluateHealthOnHelloSuccess()
        guard case .recovered = coordinator.snapshot.recovery else {
            throw MacTestFailure(
                file: #filePath,
                line: #line,
                message: "expected recovered, got \(coordinator.snapshot.recovery)"
            )
        }
    }

    @MainActor
    func testAttemptRecoveryHappyPathStaysInRestartUntilHelloSucceeds() async throws {
        let device = makeDevice()
        let transport = makeTransport(device: device)
        let coordinator = makeCoordinator(transport: transport)
        coordinator.evaluateHealthOnHelloFailure(consecutiveFailures: 3, hasUSBCarrier: true)

        await coordinator.attemptRecovery(device: device)
        guard case .restartInFlight = coordinator.snapshot.recovery else {
            throw MacTestFailure(
                file: #filePath,
                line: #line,
                message: "expected restartInFlight, got \(coordinator.snapshot.recovery)"
            )
        }
        let calls = await transport.restartManagementCalls
        try expectEqual(calls, 1)

        coordinator.evaluateHealthOnHelloSuccess()
        guard case .recovered = coordinator.snapshot.recovery else {
            throw MacTestFailure(
                file: #filePath,
                line: #line,
                message: "expected recovered after hello success"
            )
        }
    }

    @MainActor
    func testAttemptRecoveryRestartFailureSurfacesUnrecoverable() async throws {
        let device = makeDevice()
        let transport = makeTransport(device: device)
        await transport.setRestartManagementShouldFail(true, for: device.deviceID)
        let coordinator = makeCoordinator(transport: transport)

        await coordinator.attemptRecovery(device: device)
        guard case .unrecoverable = coordinator.snapshot.recovery else {
            throw MacTestFailure(
                file: #filePath,
                line: #line,
                message: "expected unrecoverable, got \(coordinator.snapshot.recovery)"
            )
        }
    }

    @MainActor
    func testDismissRecoveryFromRecoveredReturnsToOK() async throws {
        let device = makeDevice()
        let transport = makeTransport(device: device)
        let coordinator = makeCoordinator(transport: transport)
        coordinator.evaluateHealthOnHelloFailure(consecutiveFailures: 3, hasUSBCarrier: true)
        coordinator.evaluateHealthOnHelloSuccess()
        coordinator.dismissRecovery()
        try expectEqual(coordinator.snapshot.recovery, .ok)
    }
}
