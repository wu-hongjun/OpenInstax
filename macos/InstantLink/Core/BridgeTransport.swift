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
    func getConfig(device: BridgeDevice) async throws -> BridgeConfig
    func putConfig(device: BridgeDevice, diff: [String: Any]) async throws -> BridgeConfig
    func preflightUpdate(device: BridgeDevice, package: BridgeUpdatePackage) async throws -> BridgeUpdatePreflight
    func uploadUpdate(device: BridgeDevice, package: BridgeUpdatePackage) async throws -> BridgeUploadResult
    func startUpdate(device: BridgeDevice, package: BridgeUpdatePackage) async throws -> BridgeUpdateState
    func updateStatus(device: BridgeDevice, operationID: String) async throws -> BridgeUpdateState
    func updateEvents(device: BridgeDevice, operationID: String) async throws -> AsyncThrowingStream<BridgeUpdateEvent, Error>
    func markUpdateGood(device: BridgeDevice) async throws -> BridgeUpdateState
    func rollbackUpdate(device: BridgeDevice, reason: String) async throws -> BridgeUpdateState
    func createBackup(device: BridgeDevice) async throws -> BridgeBackupResult
    /// Phase D: create a backup with a user-supplied passphrase. The
    /// passphrase is shipped in the `POST /v1/backup/create` body so the
    /// bridge can encrypt the archive before returning a download URL. The
    /// no-passphrase variant is kept as a default-implementation thunk for
    /// callers that predate Phase D (e.g. the Updates flow's implicit
    /// backup step, which uses server-side encryption-at-rest only).
    func createBackup(device: BridgeDevice, passphrase: String) async throws -> BridgeBackupResult
    func restoreBackup(device: BridgeDevice, backupID: String) async throws -> BridgeBackupRestoreResult
    /// Phase D: restore an uploaded backup using the same passphrase used at
    /// create-time. Mirrors `createBackup(device:passphrase:)`.
    func restoreBackup(
        device: BridgeDevice,
        backupID: String,
        passphrase: String
    ) async throws -> BridgeBackupRestoreResult
    // MARK: Phase E — diagnostics + recovery
    /// Open an SSE stream against `/v1/logs/stream` and yield each
    /// `BridgeLogEvent` until the bridge ends the stream or the consuming
    /// `Task` is cancelled. `level` filters the events server-side; pass
    /// `.info` to receive all events.
    func streamLogs(
        device: BridgeDevice,
        level: BridgeLogLevel
    ) -> AsyncThrowingStream<BridgeLogEvent, Error>
    /// Ask the bridge to stage a redacted support bundle and return its
    /// archive location + sha256. The Mac surfaces the location through the
    /// UI; the file lives on the bridge filesystem.
    func createSupportBundle(device: BridgeDevice) async throws -> BridgeSupportBundleResult
    /// Ask the bridge to restart its management service. Used by the recovery
    /// banner when `/v1/hello` is unreachable. Returns when the bridge
    /// acknowledges; the caller is responsible for polling `/v1/hello` to
    /// detect that the management service is back.
    func restartManagement(device: BridgeDevice) async throws
    /// Anonymous probe of `/v1/hello` for the supplied endpoint. Used by the
    /// diagnostics coordinator to confirm the management service is alive
    /// (without requiring a paired identity).
    func helloProbe(endpoint: URL) async throws -> BridgeDevice
}

extension BridgeTransport {
    func createBackup(device: BridgeDevice, passphrase: String) async throws -> BridgeBackupResult {
        try await createBackup(device: device)
    }

    func restoreBackup(
        device: BridgeDevice,
        backupID: String,
        passphrase _: String
    ) async throws -> BridgeBackupRestoreResult {
        try await restoreBackup(device: device, backupID: backupID)
    }
}

enum BridgeTransportError: Error, Equatable {
    case deviceNotFound(String)
    case updateOperationNotFound(String)
    case updateScriptEmpty
    case updatePreflightFailed
    case localAuthNotFound(String)
    case helloProbeFailed(String)
    case managementRestartFailed(String)
    case supportBundleFailed(String)
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
    private var configs: [String: BridgeConfig]
    private var configValidationErrors: [String: [String: String]]
    private(set) var putConfigCalls: Int
    // MARK: Phase D — backup / restore test scaffolding
    private(set) var createBackupCalls: Int
    private(set) var lastCreateBackupPassphrase: String?
    private(set) var restoreBackupCalls: Int
    private(set) var lastRestoreBackupPassphrase: String?
    private(set) var lastRestoreBackupID: String?
    private var createBackupShouldFailDeviceIDs: Set<String>
    private var restoreBackupShouldFailDeviceIDs: Set<String>
    private var backupSourceDeviceIDs: [String: String]
    // MARK: Phase E — diagnostics test scaffolding
    private var logScripts: [String: [BridgeLogEvent]]
    private var logStreamError: [String: Error]
    private var supportBundleResults: [String: BridgeSupportBundleResult]
    private var supportBundleShouldFailDeviceIDs: Set<String>
    private var restartManagementShouldFailDeviceIDs: Set<String>
    private(set) var restartManagementCalls: Int
    private(set) var createSupportBundleCalls: Int
    private(set) var streamLogsCalls: Int
    private(set) var helloProbeCalls: Int
    private var helloProbeResults: [URL: BridgeDevice]
    private var helloProbeFailures: Set<URL>

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
        self.configs = [:]
        self.configValidationErrors = [:]
        self.putConfigCalls = 0
        self.createBackupCalls = 0
        self.lastCreateBackupPassphrase = nil
        self.restoreBackupCalls = 0
        self.lastRestoreBackupPassphrase = nil
        self.lastRestoreBackupID = nil
        self.createBackupShouldFailDeviceIDs = []
        self.restoreBackupShouldFailDeviceIDs = []
        self.backupSourceDeviceIDs = [:]
        self.logScripts = [:]
        self.logStreamError = [:]
        self.supportBundleResults = [:]
        self.supportBundleShouldFailDeviceIDs = []
        self.restartManagementShouldFailDeviceIDs = []
        self.restartManagementCalls = 0
        self.createSupportBundleCalls = 0
        self.streamLogsCalls = 0
        self.helloProbeCalls = 0
        self.helloProbeResults = [:]
        self.helloProbeFailures = []
    }

    // MARK: Phase E — diagnostics test helpers

    func setLogScript(_ events: [BridgeLogEvent], for deviceID: String) {
        logScripts[deviceID] = events
    }

    func setLogStreamError(_ error: Error?, for deviceID: String) {
        if let error {
            logStreamError[deviceID] = error
        } else {
            logStreamError.removeValue(forKey: deviceID)
        }
    }

    func setSupportBundleResult(_ result: BridgeSupportBundleResult, for deviceID: String) {
        supportBundleResults[deviceID] = result
    }

    func setSupportBundleShouldFail(_ shouldFail: Bool, for deviceID: String) {
        if shouldFail {
            supportBundleShouldFailDeviceIDs.insert(deviceID)
        } else {
            supportBundleShouldFailDeviceIDs.remove(deviceID)
        }
    }

    func setRestartManagementShouldFail(_ shouldFail: Bool, for deviceID: String) {
        if shouldFail {
            restartManagementShouldFailDeviceIDs.insert(deviceID)
        } else {
            restartManagementShouldFailDeviceIDs.remove(deviceID)
        }
    }

    func setHelloProbeResult(_ device: BridgeDevice, for endpoint: URL) {
        helloProbeResults[endpoint] = device
        helloProbeFailures.remove(endpoint)
    }

    func setHelloProbeShouldFail(_ shouldFail: Bool, for endpoint: URL) {
        if shouldFail {
            helloProbeFailures.insert(endpoint)
        } else {
            helloProbeFailures.remove(endpoint)
        }
    }

    // MARK: Phase D — backup / restore test helpers

    func setCreateBackupShouldFail(_ shouldFail: Bool, for deviceID: String) {
        if shouldFail {
            createBackupShouldFailDeviceIDs.insert(deviceID)
        } else {
            createBackupShouldFailDeviceIDs.remove(deviceID)
        }
    }

    func setRestoreBackupShouldFail(_ shouldFail: Bool, for deviceID: String) {
        if shouldFail {
            restoreBackupShouldFailDeviceIDs.insert(deviceID)
        } else {
            restoreBackupShouldFailDeviceIDs.remove(deviceID)
        }
    }

    /// When the coordinator calls `createBackup` against a device whose
    /// source-id has been pinned here, the returned `backup_id` embeds the
    /// pinned identifier so cross-bridge restore tests can verify the
    /// extra-confirmation path. The default is the device's own deviceID.
    func setBackupSourceDeviceID(_ sourceID: String, for deviceID: String) {
        backupSourceDeviceIDs[deviceID] = sourceID
    }

    func backupSourceDeviceID(for deviceID: String) -> String {
        backupSourceDeviceIDs[deviceID] ?? deviceID
    }

    func setConfig(_ config: BridgeConfig, for deviceID: String) {
        configs[deviceID] = config
    }

    /// Script the bridge to respond with a typed validation error on the
    /// next `putConfig` call for a given device, simulating a 422
    /// `config_validation_failed` response from the management API.
    func setConfigValidationError(_ errors: [String: String], for deviceID: String) {
        configValidationErrors[deviceID] = errors
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

    func getConfig(device: BridgeDevice) async throws -> BridgeConfig {
        try requireAuthorized(device)
        return configs[device.deviceID] ?? .defaults
    }

    func putConfig(device: BridgeDevice, diff: [String: Any]) async throws -> BridgeConfig {
        try requireAuthorized(device)
        putConfigCalls += 1
        if let scriptedErrors = configValidationErrors.removeValue(forKey: device.deviceID) {
            throw BridgeConfigValidationError(
                fieldErrors: scriptedErrors,
                message: "Configuration validation failed."
            )
        }
        var current = configs[device.deviceID] ?? .defaults
        Self.apply(diff: diff, to: &current)
        configs[device.deviceID] = current
        return current
    }

    /// Minimal in-memory diff application that mirrors the bridge's
    /// allow-list. The mock intentionally accepts only the same editable
    /// surface the real bridge handler exposes so tests cannot pass with
    /// fields the real bridge would reject.
    private static func apply(diff: [String: Any], to config: inout BridgeConfig) {
        if let printer = diff["printer"] as? [String: Any] {
            if let raw = printer["model"] as? String { config.printer.model = raw }
            if let raw = printer["fit"] as? String { config.printer.fit = raw }
            if let raw = printer["quality"] as? Int { config.printer.quality = raw }
            if let raw = printer["keepalive_interval_s"] as? Double {
                config.printer.keepaliveIntervalSeconds = raw
            }
            if let raw = printer["search_interval_s"] as? Double {
                config.printer.searchIntervalSeconds = raw
            }
        }
        if let ftp = diff["ftp"] as? [String: Any] {
            if let raw = ftp["mode"] as? String, let mode = BridgeFTPReceiveMode(rawValue: raw) {
                config.ftp.mode = mode
            }
            if let raw = ftp["username"] as? String { config.ftp.username = raw }
            if (ftp["password"] as? String)?.isEmpty == false { config.ftp.passwordSet = true }
        }
        if let workflow = diff["workflow"] as? [String: Any] {
            if let raw = workflow["auto_print_delay_s"] {
                if let string = raw as? String, string.lowercased() == "off" {
                    config.workflow.autoPrintDelaySeconds = nil
                } else if let value = raw as? Double {
                    config.workflow.autoPrintDelaySeconds = value
                } else if let value = raw as? Int {
                    config.workflow.autoPrintDelaySeconds = Double(value)
                }
            }
            if let value = workflow["allow_print_without_film"] as? Bool {
                config.workflow.allowPrintWithoutFilm = value
            }
        }
        if let power = diff["power"] as? [String: Any] {
            if let value = power["idle_poweroff_enabled"] as? Bool {
                config.power.idlePoweroffEnabled = value
            }
            if let value = power["idle_poweroff_after_s"] as? Double {
                config.power.idlePoweroffAfterSeconds = value
            }
        }
        if let ui = diff["ui"] as? [String: Any] {
            if let raw = ui["appearance"] as? String,
               let value = BridgeUIAppearance(rawValue: raw) {
                config.ui.appearance = value
            }
            if let raw = ui["font_size"] as? String,
               let value = BridgeFontSize(rawValue: raw) {
                config.ui.fontSize = value
            }
            if let raw = ui["language"] as? String,
               let value = BridgeUILanguage(rawValue: raw) {
                config.ui.language = value
            }
        }
        if let adj = diff["adjustments"] as? [String: Any] {
            if let raw = adj["watermark_text"] as? String { config.adjustments.watermarkText = raw }
            if let raw = adj["datestamp_format"] as? String,
               let value = BridgeDatestampFormat(rawValue: raw) {
                config.adjustments.datestampFormat = value
            }
        }
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
        try await createBackup(device: device, passphrase: "")
    }

    func createBackup(device: BridgeDevice, passphrase: String) async throws -> BridgeBackupResult {
        try requireAuthorized(device)
        createBackupCalls += 1
        lastCreateBackupPassphrase = passphrase
        if createBackupShouldFailDeviceIDs.contains(device.deviceID) {
            throw BridgeAPIError(
                requestID: "in-memory-\(UUID().uuidString)",
                code: "backup_failed",
                payload: BridgeErrorPayload(
                    message: "Bridge could not create the backup archive.",
                    details: ["device_id": .string(device.deviceID)]
                )
            )
        }
        let sourceID = backupSourceDeviceIDs[device.deviceID] ?? device.deviceID
        let backupID = "update-inmemory-\(sourceID)-\(nextOperationNumber)"
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
        try await restoreBackup(device: device, backupID: backupID, passphrase: "")
    }

    func restoreBackup(
        device: BridgeDevice,
        backupID: String,
        passphrase: String
    ) async throws -> BridgeBackupRestoreResult {
        try requireAuthorized(device)
        restoreBackupCalls += 1
        lastRestoreBackupPassphrase = passphrase
        lastRestoreBackupID = backupID
        if restoreBackupShouldFailDeviceIDs.contains(device.deviceID) {
            throw BridgeAPIError(
                requestID: "in-memory-\(UUID().uuidString)",
                code: "restore_failed",
                payload: BridgeErrorPayload(
                    message: "Bridge could not restore the backup archive.",
                    details: ["device_id": .string(device.deviceID)]
                )
            )
        }
        return BridgeBackupRestoreResult(
            backupID: backupID,
            restoredPaths: ["/etc/InstantLinkBridge/config.toml"],
            restoredCount: 1
        )
    }

    // MARK: Phase E — diagnostics + recovery

    nonisolated func streamLogs(
        device: BridgeDevice,
        level: BridgeLogLevel
    ) -> AsyncThrowingStream<BridgeLogEvent, Error> {
        AsyncThrowingStream { continuation in
            Task { [weak self] in
                guard let self else {
                    continuation.finish()
                    return
                }
                let scripted = await self.recordStreamLogsCall(deviceID: device.deviceID)
                if let error = scripted.error {
                    continuation.finish(throwing: error)
                    return
                }
                let filtered = scripted.events.filter { event in
                    Self.eventMatchesFilter(event.level, requested: level)
                }
                for event in filtered {
                    if Task.isCancelled { break }
                    continuation.yield(event)
                }
                continuation.finish()
            }
        }
    }

    private func recordStreamLogsCall(
        deviceID: String
    ) -> (events: [BridgeLogEvent], error: Error?) {
        streamLogsCalls += 1
        let error = logStreamError[deviceID]
        let events = logScripts[deviceID] ?? []
        return (events, error)
    }

    /// Apply the same min-level filter the bridge uses server-side: `.info`
    /// surfaces every event, `.warning` drops info, `.error` only allows
    /// errors.
    private static func eventMatchesFilter(_ level: BridgeLogLevel, requested: BridgeLogLevel) -> Bool {
        switch requested {
        case .info:
            return true
        case .warning:
            return level != .info
        case .error:
            return level == .error
        }
    }

    func createSupportBundle(device: BridgeDevice) async throws -> BridgeSupportBundleResult {
        try requireAuthorized(device)
        createSupportBundleCalls += 1
        if supportBundleShouldFailDeviceIDs.contains(device.deviceID) {
            throw BridgeAPIError(
                requestID: "in-memory-\(UUID().uuidString)",
                code: "support_bundle_failed",
                payload: BridgeErrorPayload(
                    message: "Bridge could not stage a support bundle.",
                    details: ["device_id": .string(device.deviceID)]
                )
            )
        }
        if let scripted = supportBundleResults[device.deviceID] {
            return scripted
        }
        return BridgeSupportBundleResult(
            schemaVersion: 1,
            bundleID: "support-inmemory-\(device.deviceID)",
            archivePath: "/var/lib/InstantLinkBridge/support-bundles/support-inmemory-\(device.deviceID).zip",
            sizeBytes: 1024,
            sha256: String(repeating: "0", count: 64),
            contents: ["manifest.json", "etc/InstantLinkBridge/config.toml"],
            createdAt: "2026-05-30T10:00:00Z"
        )
    }

    func restartManagement(device: BridgeDevice) async throws {
        try requireAuthorized(device)
        restartManagementCalls += 1
        if restartManagementShouldFailDeviceIDs.contains(device.deviceID) {
            throw BridgeAPIError(
                requestID: "in-memory-\(UUID().uuidString)",
                code: "not_implemented",
                payload: BridgeErrorPayload(
                    message: "Bridge does not support the restart route.",
                    details: ["device_id": .string(device.deviceID)]
                )
            )
        }
    }

    func helloProbe(endpoint: URL) async throws -> BridgeDevice {
        helloProbeCalls += 1
        if helloProbeFailures.contains(endpoint) {
            throw BridgeTransportError.helloProbeFailed(endpoint.absoluteString)
        }
        if let scripted = helloProbeResults[endpoint] {
            return scripted
        }
        // Default: surface the first known device with this endpoint.
        for deviceID in orderedDeviceIDs {
            if let device = devices[deviceID], device.endpointURL == endpoint {
                return device
            }
        }
        throw BridgeTransportError.helloProbeFailed(endpoint.absoluteString)
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
