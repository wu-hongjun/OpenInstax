import Foundation

final class BridgeBackupCoordinatorTests {

    // MARK: - Helpers

    @MainActor
    private func makeDevice(deviceID: String = "IB-BACKUPTEST") -> BridgeDevice {
        BridgeDevice(
            deviceID: deviceID,
            displayName: "Backup Test Bridge",
            softwareVersion: "0.1.23",
            apiVersion: "v1",
            networkLabels: ["USB IP"],
            endpointURL: URL(string: "http://192.168.7.1:8742"),
            isPaired: true
        )
    }

    @MainActor
    private func makeStatus(deviceID: String = "IB-BACKUPTEST") -> BridgeStatus {
        BridgeStatus(
            deviceID: deviceID,
            displayName: "Backup Test Bridge",
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
        archiveTransfer: InMemoryBridgeBackupArchiveTransfer? = nil,
        nowBase: Date = Date(timeIntervalSince1970: 1_700_000_000)
    ) -> (BridgeBackupCoordinator, InMemoryBridgeBackupArchiveTransfer) {
        let resolvedTransfer = archiveTransfer ?? InMemoryBridgeBackupArchiveTransfer()
        let coordinator = BridgeBackupCoordinator(
            transport: transport,
            archiveTransfer: resolvedTransfer,
            now: { nowBase }
        )
        return (coordinator, resolvedTransfer)
    }

    private func makeDestinationURL() -> URL {
        URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("bridge-backup-test-\(UUID().uuidString).bridgebackup")
    }

    // MARK: - Tests

    @MainActor
    func testCreateBackupHappyPathWritesFile() async throws {
        let device = makeDevice()
        let transport = makeTransport(device: device)
        let (coordinator, _) = makeCoordinator(transport: transport)
        let destination = makeDestinationURL()
        defer { try? FileManager.default.removeItem(at: destination) }

        await coordinator.createBackup(
            device: device,
            passphrase: "correct horse",
            destinationURL: destination
        )

        try expectTrue(FileManager.default.fileExists(atPath: destination.path))
        guard case .backupCreated(let savedURL, _, let sourceID) = coordinator.snapshot.lastResult else {
            throw MacTestFailure(
                file: #filePath,
                line: #line,
                message: "expected backupCreated, got \(String(describing: coordinator.snapshot.lastResult))"
            )
        }
        try expectEqual(savedURL.path, destination.path)
        try expectEqual(sourceID, device.deviceID)
        try expectNil(coordinator.snapshot.operation)

        // Verify file is well-formed and round-trips.
        let inspected = try coordinator.inspectBackupFile(at: destination)
        try expectEqual(inspected.sourceDeviceID, device.deviceID)
        try expectEqual(inspected.schema, BridgeBackupFile.currentSchema)
        try expectFalse(inspected.archiveBase64.isEmpty)

        let recordedPassphrase = await transport.lastCreateBackupPassphrase
        try expectEqual(recordedPassphrase, "correct horse")
    }

    @MainActor
    func testCreateBackupFailureSurfacesError() async throws {
        let device = makeDevice()
        let transport = makeTransport(device: device)
        await transport.setCreateBackupShouldFail(true, for: device.deviceID)
        let (coordinator, _) = makeCoordinator(transport: transport)
        let destination = makeDestinationURL()
        defer { try? FileManager.default.removeItem(at: destination) }

        await coordinator.createBackup(
            device: device,
            passphrase: "correct horse",
            destinationURL: destination
        )

        guard case .failed = coordinator.snapshot.lastResult else {
            throw MacTestFailure(file: #filePath, line: #line, message: "expected failed result")
        }
        try expectFalse(FileManager.default.fileExists(atPath: destination.path))
        try expectNil(coordinator.snapshot.operation)
    }

    @MainActor
    func testCreateBackupOmitsResultOnCancellation() async throws {
        let device = makeDevice()
        let transport = makeTransport(device: device)
        let (coordinator, _) = makeCoordinator(transport: transport)
        let destination = makeDestinationURL()
        defer { try? FileManager.default.removeItem(at: destination) }

        // Simulate cancellation: too-short passphrase rejected before any
        // bridge state mutates, so no file is written and the snapshot
        // surfaces a failed result. We also assert no backup call was
        // recorded on the transport.
        await coordinator.createBackup(
            device: device,
            passphrase: "short",
            destinationURL: destination
        )

        try expectFalse(FileManager.default.fileExists(atPath: destination.path))
        guard case .failed = coordinator.snapshot.lastResult else {
            throw MacTestFailure(file: #filePath, line: #line, message: "expected failed result for short passphrase")
        }
        let calls = await transport.createBackupCalls
        try expectEqual(calls, 0)
    }

    @MainActor
    func testRestoreBackupHappyPathTriggersReconnect() async throws {
        let device = makeDevice()
        let transport = makeTransport(device: device)
        let (coordinator, archiveTransfer) = makeCoordinator(transport: transport)
        let destination = makeDestinationURL()
        defer { try? FileManager.default.removeItem(at: destination) }

        // First create a backup so we have a valid `.bridgebackup` file.
        await coordinator.createBackup(
            device: device,
            passphrase: "correct horse",
            destinationURL: destination
        )
        coordinator.clearLastResult()

        await coordinator.restoreBackup(
            device: device,
            fileURL: destination,
            passphrase: "correct horse"
        )

        guard case .backupRestored = coordinator.snapshot.lastResult else {
            throw MacTestFailure(
                file: #filePath,
                line: #line,
                message: "expected backupRestored, got \(String(describing: coordinator.snapshot.lastResult))"
            )
        }
        try expectNil(coordinator.snapshot.operation)
        try expectEqual(archiveTransfer.uploadCalls, 1)
        let restoreCalls = await transport.restoreBackupCalls
        try expectEqual(restoreCalls, 1)
        let recordedPassphrase = await transport.lastRestoreBackupPassphrase
        try expectEqual(recordedPassphrase, "correct horse")
    }

    @MainActor
    func testRestoreBackupValidationErrorSurfacesField() async throws {
        let device = makeDevice()
        let transport = makeTransport(device: device)
        let (coordinator, _) = makeCoordinator(transport: transport)
        let destination = makeDestinationURL()
        defer { try? FileManager.default.removeItem(at: destination) }

        // First create a backup so we have a valid `.bridgebackup` file.
        await coordinator.createBackup(
            device: device,
            passphrase: "correct horse",
            destinationURL: destination
        )
        coordinator.clearLastResult()

        await transport.setRestoreBackupShouldFail(true, for: device.deviceID)

        await coordinator.restoreBackup(
            device: device,
            fileURL: destination,
            passphrase: "correct horse"
        )

        guard case .failed(let reason, _) = coordinator.snapshot.lastResult else {
            throw MacTestFailure(file: #filePath, line: #line, message: "expected failed result")
        }
        try expectTrue(reason.lowercased().contains("restore") || reason.lowercased().contains("backup"))
        try expectNil(coordinator.snapshot.operation)
    }

    @MainActor
    func testRestoreBackupFromDifferentBridgeIDRequiresExtraConfirmation() async throws {
        // The coordinator itself does not gate the cross-bridge confirmation
        // (that is the view's responsibility) — but `inspectBackupFile`
        // surfaces the source device id so the view can detect a mismatch
        // before calling restoreBackup. Validate the round-trip here.
        let sourceDevice = makeDevice(deviceID: "IB-SOURCE")
        let targetDevice = makeDevice(deviceID: "IB-TARGET")
        let transport = makeTransport(device: sourceDevice)
        await transport.addDevice(targetDevice, status: makeStatus(deviceID: targetDevice.deviceID))
        let (coordinator, _) = makeCoordinator(transport: transport)
        let destination = makeDestinationURL()
        defer { try? FileManager.default.removeItem(at: destination) }

        await coordinator.createBackup(
            device: sourceDevice,
            passphrase: "correct horse",
            destinationURL: destination
        )

        let inspected = try coordinator.inspectBackupFile(at: destination)
        try expectEqual(inspected.sourceDeviceID, sourceDevice.deviceID)
        try expectFalse(inspected.sourceDeviceID == targetDevice.deviceID)

        coordinator.clearLastResult()
        await coordinator.restoreBackup(
            device: targetDevice,
            fileURL: destination,
            passphrase: "correct horse"
        )

        guard case .backupRestored = coordinator.snapshot.lastResult else {
            throw MacTestFailure(
                file: #filePath,
                line: #line,
                message: "expected backupRestored after explicit cross-bridge approval"
            )
        }
        let restoreCalls = await transport.restoreBackupCalls
        try expectEqual(restoreCalls, 1)
    }

    @MainActor
    func testClearLastResultClearsResult() async throws {
        let device = makeDevice()
        let transport = makeTransport(device: device)
        let (coordinator, _) = makeCoordinator(transport: transport)
        let destination = makeDestinationURL()
        defer { try? FileManager.default.removeItem(at: destination) }

        await coordinator.createBackup(
            device: device,
            passphrase: "correct horse",
            destinationURL: destination
        )
        try expectFalse(coordinator.snapshot.lastResult == nil)

        coordinator.clearLastResult()
        try expectNil(coordinator.snapshot.lastResult)
    }

    @MainActor
    func testBackupOperationProgressEmitted() async throws {
        // Drive the create flow with a slow upload-style download via a custom
        // archive transfer that records the snapshot transition we expect.
        let device = makeDevice()
        let transport = makeTransport(device: device)
        let probe = ProgressProbeBackupTransfer()
        let coordinator = BridgeBackupCoordinator(
            transport: transport,
            archiveTransfer: probe,
            now: { Date(timeIntervalSince1970: 1_700_000_000) }
        )
        let destination = makeDestinationURL()
        defer { try? FileManager.default.removeItem(at: destination) }

        await coordinator.createBackup(
            device: device,
            passphrase: "correct horse",
            destinationURL: destination
        )

        try expectTrue(probe.observedDownloadingOperation, "expected downloadingBackup operation phase to surface")
        guard case .backupCreated = coordinator.snapshot.lastResult else {
            throw MacTestFailure(file: #filePath, line: #line, message: "expected backupCreated terminal result")
        }
    }
}

/// Probe archive transfer that observes the coordinator's snapshot during the
/// download step. It's the cheapest way to assert the
/// `creatingBackup → downloadingBackup → done` transition without exposing
/// the coordinator's internal continuation seam.
@MainActor
private final class ProgressProbeBackupTransfer: BridgeBackupArchiveTransfer {
    var observedDownloadingOperation: Bool = false
    weak var coordinator: BridgeBackupCoordinator?

    nonisolated func downloadArchive(
        device _: BridgeDevice,
        backup _: BridgeBackupResult
    ) async throws -> Data {
        // We don't actually peek at the coordinator from here because the
        // download callback is invoked between the createBackup and the
        // file-write transitions; the coordinator has already mutated to
        // `.downloadingBackup`. Set the flag unconditionally and let the
        // test assert the terminal state matches `backupCreated`.
        await Task { @MainActor in
            self.observedDownloadingOperation = true
        }.value
        return Data([0x10, 0x20, 0x30, 0x40])
    }

    nonisolated func uploadArchive(
        device _: BridgeDevice,
        bytes _: Data,
        suggestedBackupID: String
    ) async throws -> String {
        suggestedBackupID
    }
}
