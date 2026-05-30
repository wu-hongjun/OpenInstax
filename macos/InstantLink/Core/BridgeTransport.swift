import Foundation

protocol BridgeTransport {
    func discover() async throws -> [BridgeDevice]
    func pairingStatus(device: BridgeDevice) async throws -> BridgePairingStatus
    func completePairing(
        device: BridgeDevice,
        confirmationCode: String,
        clientName: String
    ) async throws -> BridgePairingCompletion
    func usbAutoTrust(
        device: BridgeDevice,
        clientName: String
    ) async throws -> BridgePairingCompletion
    func forgetLocalAuth(device: BridgeDevice) async throws
    func status(device: BridgeDevice) async throws -> BridgeStatus
    func preflightUpdate(device: BridgeDevice, package: BridgeUpdatePackage) async throws -> BridgeUpdatePreflight
    func uploadUpdate(device: BridgeDevice, package: BridgeUpdatePackage) async throws -> BridgeUploadResult
    func startUpdate(device: BridgeDevice, package: BridgeUpdatePackage) async throws -> BridgeUpdateState
    func updateStatus(device: BridgeDevice, operationID: String) async throws -> BridgeUpdateState
    func updateEvents(device: BridgeDevice, operationID: String) async throws -> AsyncThrowingStream<BridgeUpdateEvent, Error>
    func markUpdateGood(device: BridgeDevice) async throws -> BridgeUpdateState
    func rollbackUpdate(device: BridgeDevice, reason: String) async throws -> BridgeUpdateState
    func createBackup(device: BridgeDevice) async throws -> BridgeBackupResult
    func restoreBackup(device: BridgeDevice, backupID: String) async throws -> BridgeBackupRestoreResult
}

enum BridgeTransportError: Error, Equatable {
    case deviceNotFound(String)
    case updateOperationNotFound(String)
    case updateScriptEmpty
    case updatePreflightFailed
    case localAuthNotFound(String)
}

actor InMemoryBridgeTransport: BridgeTransport {
    private struct UpdateOperation {
        var package: BridgeUpdatePackage
        var script: [BridgeUpdateState]
        var nextStatusIndex: Int
    }

    private var devices: [String: BridgeDevice]
    private var orderedDeviceIDs: [String]
    private var statuses: [String: BridgeStatus]
    private var authRequiredDeviceIDs: Set<String>
    private var pairingStatuses: [String: BridgePairingStatus]
    private var preflights: [String: BridgeUpdatePreflight]
    private var updateScripts: [String: [BridgeUpdateState]]
    private var operations: [String: UpdateOperation]
    private var nextOperationNumber: Int
    private(set) var usbAutoTrustCalls: Int
    private var usbAutoTrustShouldRejectDeviceIDs: Set<String>

    init(
        devices: [BridgeDevice] = [],
        statuses: [String: BridgeStatus] = [:],
        authRequiredDeviceIDs: Set<String> = []
    ) {
        self.devices = Dictionary(uniqueKeysWithValues: devices.map { ($0.deviceID, $0) })
        self.orderedDeviceIDs = devices.map(\.deviceID)
        self.statuses = statuses
        self.authRequiredDeviceIDs = authRequiredDeviceIDs
        self.pairingStatuses = [:]
        self.preflights = [:]
        self.updateScripts = [:]
        self.operations = [:]
        self.nextOperationNumber = 1
        self.usbAutoTrustCalls = 0
        self.usbAutoTrustShouldRejectDeviceIDs = []
    }

    func setUSBAutoTrustShouldReject(_ reject: Bool, for deviceID: String) {
        if reject {
            usbAutoTrustShouldRejectDeviceIDs.insert(deviceID)
        } else {
            usbAutoTrustShouldRejectDeviceIDs.remove(deviceID)
        }
    }

    func addDevice(_ device: BridgeDevice, status: BridgeStatus) {
        if devices[device.deviceID] == nil {
            orderedDeviceIDs.append(device.deviceID)
        }
        devices[device.deviceID] = device
        statuses[device.deviceID] = status
    }

    func setAuthRequired(_ required: Bool, for deviceID: String) {
        if required {
            authRequiredDeviceIDs.insert(deviceID)
        } else {
            authRequiredDeviceIDs.remove(deviceID)
        }
    }

    func setPairingStatus(_ pairingStatus: BridgePairingStatus, for deviceID: String) {
        pairingStatuses[deviceID] = pairingStatus
    }

    func setPreflight(_ preflight: BridgeUpdatePreflight, for deviceID: String) {
        preflights[deviceID] = preflight
    }

    func setUpdateScript(_ script: [BridgeUpdateState], for deviceID: String) {
        updateScripts[deviceID] = script
    }

    func discover() async throws -> [BridgeDevice] {
        orderedDeviceIDs.compactMap { devices[$0] }
    }

    func pairingStatus(device: BridgeDevice) async throws -> BridgePairingStatus {
        guard devices[device.deviceID] != nil else {
            throw BridgeTransportError.deviceNotFound(device.deviceID)
        }
        return pairingStatuses[device.deviceID] ?? BridgePairingStatus(
            open: false,
            authImplemented: false,
            confirmationCodeRequired: true,
            expiresAt: nil,
            expiresInSeconds: nil,
            pairedClientID: devices[device.deviceID]?.isPaired == true ? "in-memory" : nil,
            authorizedClientCount: devices[device.deviceID]?.isPaired == true ? 1 : 0
        )
    }

    func completePairing(
        device: BridgeDevice,
        confirmationCode: String,
        clientName: String
    ) async throws -> BridgePairingCompletion {
        guard var storedDevice = devices[device.deviceID] else {
            throw BridgeTransportError.deviceNotFound(device.deviceID)
        }
        let currentStatus = try await pairingStatus(device: device)
        guard currentStatus.open else {
            throw BridgeAPIError(
                requestID: "in-memory-\(UUID().uuidString)",
                code: "pairing_not_open",
                payload: BridgeErrorPayload(
                    message: "Bridge access is not open for this Mac",
                    details: ["device_id": .string(device.deviceID)]
                )
            )
        }

        storedDevice.isPaired = true
        devices[device.deviceID] = storedDevice
        authRequiredDeviceIDs.remove(device.deviceID)
        return BridgePairingCompletion(
            clientID: "in-memory",
            clientName: clientName,
            paired: true,
            publicKeyAlgorithm: .ed25519,
            createdAt: nil,
            message: confirmationCode.isEmpty ? nil : "Paired"
        )
    }

    func usbAutoTrust(
        device: BridgeDevice,
        clientName: String
    ) async throws -> BridgePairingCompletion {
        guard var storedDevice = devices[device.deviceID] else {
            throw BridgeTransportError.deviceNotFound(device.deviceID)
        }
        if usbAutoTrustShouldRejectDeviceIDs.contains(device.deviceID) {
            throw BridgeAPIError(
                requestID: "in-memory-\(UUID().uuidString)",
                code: "not_usb_interface",
                payload: BridgeErrorPayload(
                    message: "usb_auto_trust is only available on the USB-tether interface.",
                    details: ["device_id": .string(device.deviceID)]
                )
            )
        }

        storedDevice.isPaired = true
        devices[device.deviceID] = storedDevice
        authRequiredDeviceIDs.remove(device.deviceID)
        usbAutoTrustCalls += 1
        return BridgePairingCompletion(
            clientID: "in-memory",
            clientName: clientName,
            paired: true,
            publicKeyAlgorithm: .ed25519,
            createdAt: nil,
            message: "Auto-trusted over USB"
        )
    }

    func forgetLocalAuth(device: BridgeDevice) async throws {
        guard var storedDevice = devices[device.deviceID] else {
            throw BridgeTransportError.deviceNotFound(device.deviceID)
        }
        storedDevice.isPaired = false
        devices[device.deviceID] = storedDevice
        authRequiredDeviceIDs.insert(device.deviceID)
    }

    func status(device: BridgeDevice) async throws -> BridgeStatus {
        try requireAuthorized(device)
        guard let status = statuses[device.deviceID] else {
            throw BridgeTransportError.deviceNotFound(device.deviceID)
        }
        return status
    }

    func preflightUpdate(device: BridgeDevice, package: BridgeUpdatePackage) async throws -> BridgeUpdatePreflight {
        try requireAuthorized(device)
        if let preflight = preflights[device.deviceID] {
            return preflight
        }
        return BridgeUpdatePreflight(
            package: package,
            allowed: true,
            backupRequired: true,
            rollbackAvailable: true,
            checks: [
                BridgeUpdatePreflightCheck(name: "service_health", status: .pass, message: nil),
                BridgeUpdatePreflightCheck(name: "backup_available", status: .pass, message: nil),
                BridgeUpdatePreflightCheck(name: "package", status: .pass, message: nil),
            ],
            operationID: nil
        )
    }

    func startUpdate(device: BridgeDevice, package: BridgeUpdatePackage) async throws -> BridgeUpdateState {
        try requireAuthorized(device)
        let preflight = try await preflightUpdate(device: device, package: package)
        guard preflight.allowed else {
            throw BridgeTransportError.updatePreflightFailed
        }

        let operationID = "update-\(nextOperationNumber)"
        nextOperationNumber += 1

        let script = updateScripts[device.deviceID] ?? Self.defaultUpdateScript(
            operationID: operationID,
            installedVersion: package.version
        )
        guard let firstState = script.first else {
            throw BridgeTransportError.updateScriptEmpty
        }

        operations[operationID] = UpdateOperation(
            package: package,
            script: script,
            nextStatusIndex: min(1, script.count)
        )
        applyUpdateState(firstState, to: device.deviceID)
        return firstState
    }

    func updateStatus(device: BridgeDevice, operationID: String) async throws -> BridgeUpdateState {
        try requireAuthorized(device)
        guard var operation = operations[operationID] else {
            throw BridgeTransportError.updateOperationNotFound(operationID)
        }
        guard !operation.script.isEmpty else {
            throw BridgeTransportError.updateScriptEmpty
        }

        let index = min(operation.nextStatusIndex, operation.script.count - 1)
        let state = operation.script[index]
        if operation.nextStatusIndex < operation.script.count - 1 {
            operation.nextStatusIndex += 1
        }
        operations[operationID] = operation
        applyUpdateState(state, to: device.deviceID)
        return state
    }

    func updateEvents(device: BridgeDevice, operationID: String) async throws -> AsyncThrowingStream<BridgeUpdateEvent, Error> {
        try requireAuthorized(device)
        guard let operation = operations[operationID] else {
            throw BridgeTransportError.updateOperationNotFound(operationID)
        }
        let events = operation.script.enumerated().map { index, state in
            BridgeUpdateEvent(
                eventID: "\(operationID)-event-\(index + 1)",
                operationID: operationID,
                phase: state.phase,
                progress: state.progress,
                message: state.message,
                state: state
            )
        }

        return AsyncThrowingStream { continuation in
            for event in events {
                continuation.yield(event)
            }
            continuation.finish()
        }
    }

    func uploadUpdate(device: BridgeDevice, package: BridgeUpdatePackage) async throws -> BridgeUploadResult {
        try requireAuthorized(device)
        let filename = package.archiveURL.lastPathComponent
        return BridgeUploadResult(
            filename: filename,
            storedPath: "/var/lib/InstantLinkBridge/shared/uploads/\(filename)",
            sizeBytes: 0,
            sha256: package.archiveSHA256
        )
    }

    func markUpdateGood(device: BridgeDevice) async throws -> BridgeUpdateState {
        try requireAuthorized(device)
        let summary = statuses[device.deviceID]?.update
        let installedVersion = summary?.availableVersion ?? statuses[device.deviceID]?.bridgeVersion
        let state = BridgeUpdateState(
            operationID: summary?.operationID ?? "update-good",
            phase: .done,
            progress: 1.0,
            message: "Done",
            safeState: .installed,
            installedVersion: installedVersion,
            error: nil
        )
        applyUpdateState(state, to: device.deviceID)
        return state
    }

    func rollbackUpdate(device: BridgeDevice, reason: String) async throws -> BridgeUpdateState {
        try requireAuthorized(device)
        let summary = statuses[device.deviceID]?.update
        let state = BridgeUpdateState(
            operationID: summary?.operationID ?? "update-rollback",
            phase: .rolledBack,
            progress: 1.0,
            message: reason.isEmpty ? "Update restored" : reason,
            safeState: .previousVersionRestored,
            installedVersion: statuses[device.deviceID]?.bridgeVersion,
            error: nil
        )
        applyUpdateState(state, to: device.deviceID)
        return state
    }

    func createBackup(device: BridgeDevice) async throws -> BridgeBackupResult {
        try requireAuthorized(device)
        let backupID = "update-inmemory-\(nextOperationNumber)"
        nextOperationNumber += 1
        return BridgeBackupResult(
            backupID: backupID,
            manifestPath: "/var/lib/InstantLinkBridge/backups/\(backupID).manifest.json",
            archivePath: "/var/lib/InstantLinkBridge/backups/\(backupID).tar.gz",
            archiveSHA256: String(repeating: "0", count: 64),
            verified: true
        )
    }

    func restoreBackup(device: BridgeDevice, backupID: String) async throws -> BridgeBackupRestoreResult {
        try requireAuthorized(device)
        return BridgeBackupRestoreResult(
            backupID: backupID,
            restoredPaths: ["/etc/InstantLinkBridge/config.toml"],
            restoredCount: 1
        )
    }

    private func requireAuthorized(_ device: BridgeDevice) throws {
        guard devices[device.deviceID] != nil else {
            throw BridgeTransportError.deviceNotFound(device.deviceID)
        }
        guard !authRequiredDeviceIDs.contains(device.deviceID) else {
            throw BridgeAPIError(
                requestID: "in-memory-\(UUID().uuidString)",
                code: .authRequired,
                payload: BridgeErrorPayload(
                    message: "Bridge access requires pairing",
                    details: ["device_id": .string(device.deviceID)]
                )
            )
        }
    }

    private func applyUpdateState(_ state: BridgeUpdateState, to deviceID: String) {
        guard var status = statuses[deviceID] else { return }

        status.update = BridgeUpdateSummary(
            currentVersion: status.bridgeVersion,
            availableVersion: state.installedVersion,
            canUpdate: !state.isTerminal,
            operationID: state.operationID,
            phase: state.phase
        )

        if state.phase == .done, let installedVersion = state.installedVersion {
            status.bridgeVersion = installedVersion
            status.readiness = .ready
            status.update?.currentVersion = installedVersion
            status.update?.availableVersion = nil
            status.update?.canUpdate = false
        } else if state.phase == .rolledBack {
            status.readiness = .needsAttention
        } else if state.phase == .needsRecovery || state.phase == .failed {
            status.readiness = .unavailable
        } else {
            status.readiness = .updating
        }

        statuses[deviceID] = status
    }

    private static func defaultUpdateScript(
        operationID: String,
        installedVersion: String
    ) -> [BridgeUpdateState] {
        [
            BridgeUpdateState(
                operationID: operationID,
                phase: .checkingBridge,
                progress: 0.1,
                message: "Checking Bridge",
                safeState: .updateNotInstalled,
                installedVersion: nil,
                error: nil
            ),
            BridgeUpdateState(
                operationID: operationID,
                phase: .backingUpSettings,
                progress: 0.25,
                message: "Backing up settings",
                safeState: .updateNotInstalled,
                installedVersion: nil,
                error: nil
            ),
            BridgeUpdateState(
                operationID: operationID,
                phase: .verifyingUpdate,
                progress: 0.4,
                message: "Verifying update",
                safeState: .updateNotInstalled,
                installedVersion: nil,
                error: nil
            ),
            BridgeUpdateState(
                operationID: operationID,
                phase: .uploadingUpdate,
                progress: 0.55,
                message: "Uploading update",
                safeState: .updateNotInstalled,
                installedVersion: nil,
                error: nil
            ),
            BridgeUpdateState(
                operationID: operationID,
                phase: .installingUpdate,
                progress: 0.7,
                message: "Installing update",
                safeState: .unknown,
                installedVersion: nil,
                error: nil
            ),
            BridgeUpdateState(
                operationID: operationID,
                phase: .restartingBridge,
                progress: 0.82,
                message: "Restarting Bridge",
                safeState: .unknown,
                installedVersion: nil,
                error: nil
            ),
            BridgeUpdateState(
                operationID: operationID,
                phase: .verifyingBridge,
                progress: 0.94,
                message: "Verifying Bridge",
                safeState: .installed,
                installedVersion: installedVersion,
                error: nil
            ),
            BridgeUpdateState(
                operationID: operationID,
                phase: .done,
                progress: 1.0,
                message: "Done",
                safeState: .installed,
                installedVersion: installedVersion,
                error: nil
            ),
        ]
    }
}
