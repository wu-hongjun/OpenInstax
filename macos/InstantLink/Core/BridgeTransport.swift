import Foundation

protocol BridgeTransport {
    func discover() async throws -> [BridgeDevice]
    func status(device: BridgeDevice) async throws -> BridgeStatus
    func preflightUpdate(device: BridgeDevice, package: BridgeUpdatePackage) async throws -> BridgeUpdatePreflight
    func startUpdate(device: BridgeDevice, package: BridgeUpdatePackage) async throws -> BridgeUpdateState
    func updateStatus(device: BridgeDevice, operationID: String) async throws -> BridgeUpdateState
    func updateEvents(device: BridgeDevice, operationID: String) async throws -> AsyncThrowingStream<BridgeUpdateEvent, Error>
}

enum BridgeTransportError: Error, Equatable {
    case deviceNotFound(String)
    case updateOperationNotFound(String)
    case updateScriptEmpty
    case updatePreflightFailed
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
    private var preflights: [String: BridgeUpdatePreflight]
    private var updateScripts: [String: [BridgeUpdateState]]
    private var operations: [String: UpdateOperation]
    private var nextOperationNumber: Int

    init(
        devices: [BridgeDevice] = [],
        statuses: [String: BridgeStatus] = [:],
        authRequiredDeviceIDs: Set<String> = []
    ) {
        self.devices = Dictionary(uniqueKeysWithValues: devices.map { ($0.deviceID, $0) })
        self.orderedDeviceIDs = devices.map(\.deviceID)
        self.statuses = statuses
        self.authRequiredDeviceIDs = authRequiredDeviceIDs
        self.preflights = [:]
        self.updateScripts = [:]
        self.operations = [:]
        self.nextOperationNumber = 1
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

    func setPreflight(_ preflight: BridgeUpdatePreflight, for deviceID: String) {
        preflights[deviceID] = preflight
    }

    func setUpdateScript(_ script: [BridgeUpdateState], for deviceID: String) {
        updateScripts[deviceID] = script
    }

    func discover() async throws -> [BridgeDevice] {
        orderedDeviceIDs.compactMap { devices[$0] }
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
