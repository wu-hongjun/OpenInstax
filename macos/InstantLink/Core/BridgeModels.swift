import Foundation

// MARK: - API Envelope

struct BridgeAPIEnvelope: Codable, Equatable {
    var schemaVersion: Int
    var requestID: String
    var ok: Bool
    var errorCode: BridgeErrorCode?
    var error: BridgeErrorPayload?

    var device: BridgeDevice?
    var devices: [BridgeDevice]?
    var status: BridgeStatus?
    var management: BridgeManagementInfo?
    var networkLabels: [BridgeNetworkLabel]?
    var pairing: BridgePairingStatus?
    var pairingCompletion: BridgePairingCompletion?
    var preflight: BridgeUpdatePreflight?
    var update: BridgeUpdateState?
    var event: BridgeUpdateEvent?
    var backup: BridgeBackupResult?
    var restore: BridgeBackupRestoreResult?
    var upload: BridgeUploadResult?
    var config: BridgeConfig?
    var supportBundle: BridgeSupportBundleResult?

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case requestID = "request_id"
        case ok
        case errorCode = "error_code"
        case error
        case device
        case devices
        case status
        case management
        case networkLabels = "network_labels"
        case pairing
        case pairingCompletion = "pairing_completion"
        case preflight
        case update
        case event
        case backup
        case restore
        case upload
        case config
        case supportBundle = "support_bundle"
    }

    func requireOK() throws {
        guard ok else {
            throw BridgeAPIError(
                requestID: requestID,
                code: errorCode ?? .unknown,
                payload: error ?? BridgeErrorPayload(message: "Bridge request failed")
            )
        }
    }

    func requireDevice() throws -> BridgeDevice {
        try requireOK()
        guard let device else {
            throw BridgeAPIError.missingPayload(requestID: requestID, payloadName: "device")
        }
        return device
    }

    func requireDevices() throws -> [BridgeDevice] {
        try requireOK()
        guard let devices else {
            throw BridgeAPIError.missingPayload(requestID: requestID, payloadName: "devices")
        }
        return devices
    }

    func requireStatus() throws -> BridgeStatus {
        try requireOK()
        guard let status else {
            throw BridgeAPIError.missingPayload(requestID: requestID, payloadName: "status")
        }
        return status
    }

    func requirePairingStatus() throws -> BridgePairingStatus {
        try requireOK()
        guard let pairing else {
            throw BridgeAPIError.missingPayload(requestID: requestID, payloadName: "pairing")
        }
        return pairing
    }

    func requirePairingCompletion() throws -> BridgePairingCompletion {
        try requireOK()
        guard let pairingCompletion else {
            throw BridgeAPIError.missingPayload(requestID: requestID, payloadName: "pairing_completion")
        }
        return pairingCompletion
    }

    func requirePreflight() throws -> BridgeUpdatePreflight {
        try requireOK()
        guard let preflight else {
            throw BridgeAPIError.missingPayload(requestID: requestID, payloadName: "preflight")
        }
        return preflight
    }

    func requireUpdateState() throws -> BridgeUpdateState {
        try requireOK()
        guard let update else {
            throw BridgeAPIError.missingPayload(requestID: requestID, payloadName: "update")
        }
        return update
    }

    func requireUpdateEvent() throws -> BridgeUpdateEvent {
        try requireOK()
        guard let event else {
            throw BridgeAPIError.missingPayload(requestID: requestID, payloadName: "event")
        }
        return event
    }

    func requireBackup() throws -> BridgeBackupResult {
        try requireOK()
        guard let backup else {
            throw BridgeAPIError.missingPayload(requestID: requestID, payloadName: "backup")
        }
        return backup
    }

    func requireBackupRestore() throws -> BridgeBackupRestoreResult {
        try requireOK()
        guard let restore else {
            throw BridgeAPIError.missingPayload(requestID: requestID, payloadName: "restore")
        }
        return restore
    }

    func requireUpload() throws -> BridgeUploadResult {
        try requireOK()
        guard let upload else {
            throw BridgeAPIError.missingPayload(requestID: requestID, payloadName: "upload")
        }
        return upload
    }

    func requireConfig() throws -> BridgeConfig {
        try requireOK()
        guard let config else {
            throw BridgeAPIError.missingPayload(requestID: requestID, payloadName: "config")
        }
        return config
    }
}

// MARK: - Devices And Status

struct BridgeManagementInfo: Codable, Equatable {
    var service: String?
    var authImplemented: Bool
    var adminRoutes: String?
    var pairingOpen: Bool
    var publicKeyFingerprint: String?

    enum CodingKeys: String, CodingKey {
        case service
        case authImplemented = "auth_implemented"
        case adminRoutes = "admin_routes"
        case pairingOpen = "pairing_open"
        case publicKeyFingerprint = "public_key_fingerprint"
    }
}

struct BridgeNetworkLabel: Codable, Equatable {
    var key: String
    var label: String
    var address: String?
    var enabled: Bool

    enum CodingKeys: String, CodingKey {
        case key
        case label
        case address
        case enabled
    }
}

struct BridgeDevice: Codable, Equatable, Identifiable {
    var id: String { deviceID }

    var deviceID: String
    var displayName: String
    var softwareVersion: String
    var apiVersion: String
    var managementPublicKeyFingerprint: String?
    var pairingOpen: Bool
    var networkLabels: [String]
    var endpointURL: URL?
    var isPaired: Bool

    enum CodingKeys: String, CodingKey {
        case deviceID = "device_id"
        case displayName = "display_name"
        case softwareVersion = "software_version"
        case apiVersion = "api_version"
        case managementPublicKeyFingerprint = "management_public_key_fingerprint"
        case pairingOpen = "pairing_open"
        case networkLabels = "network_labels"
        case endpointURL = "endpoint_url"
        case isPaired = "is_paired"
    }

    init(
        deviceID: String,
        displayName: String,
        softwareVersion: String,
        apiVersion: String,
        managementPublicKeyFingerprint: String? = nil,
        pairingOpen: Bool = false,
        networkLabels: [String] = [],
        endpointURL: URL? = nil,
        isPaired: Bool = false
    ) {
        self.deviceID = deviceID
        self.displayName = displayName
        self.softwareVersion = softwareVersion
        self.apiVersion = apiVersion
        self.managementPublicKeyFingerprint = managementPublicKeyFingerprint
        self.pairingOpen = pairingOpen
        self.networkLabels = networkLabels
        self.endpointURL = endpointURL
        self.isPaired = isPaired
    }
}

enum BridgeReadiness: String, Codable, Equatable {
    case ready
    case setupNeeded = "setup_needed"
    case needsAttention = "needs_attention"
    case updating
    case unavailable
    case unknown
}

enum BridgeUploadMode: String, Codable, Equatable {
    case bridgeWiFi = "bridge_wifi"
    case sameWiFi = "same_wifi"
    case usbDebug = "usb_debug"
    case disabled
    case unknown
}

struct BridgeStatus: Codable, Equatable {
    var deviceID: String
    var displayName: String
    var bridgeVersion: String
    var apiVersion: String
    var readiness: BridgeReadiness
    var activeUploadMode: BridgeUploadMode
    var uptimeSeconds: Int?
    var network: BridgeNetworkStatus?
    var printer: BridgePrinterStatus?
    var update: BridgeUpdateSummary?
    var lastUpload: BridgeUploadRecord?
    var lastError: BridgeErrorPayload?

    enum CodingKeys: String, CodingKey {
        case deviceID = "device_id"
        case displayName = "display_name"
        case bridgeVersion = "bridge_version"
        case apiVersion = "api_version"
        case readiness
        case activeUploadMode = "active_upload_mode"
        case uptimeSeconds = "uptime_seconds"
        case network
        case printer
        case update
        case lastUpload = "last_upload"
        case lastError = "last_error"
    }
}

struct BridgeNetworkStatus: Codable, Equatable {
    var mode: BridgeUploadMode
    var label: String
    var address: String?
    var connected: Bool

    enum CodingKeys: String, CodingKey {
        case mode
        case label
        case address
        case connected
    }
}

struct BridgePrinterStatus: Codable, Equatable {
    var displayName: String?
    var model: String?
    var filmRemaining: Int?
    var batteryPercent: Int?
    var charging: Bool?
    var batteryMinutesRemaining: Int?
    var printStatus: String?
    var connected: Bool
    var busy: Bool
    var lastError: BridgeErrorPayload?

    enum CodingKeys: String, CodingKey {
        case displayName = "display_name"
        case model
        case filmRemaining = "film_remaining"
        case batteryPercent = "battery_percent"
        case charging
        case batteryMinutesRemaining = "battery_minutes_remaining"
        case printStatus = "print_status"
        case connected
        case busy
        case lastError = "last_error"
    }

    init(
        displayName: String? = nil,
        model: String? = nil,
        filmRemaining: Int? = nil,
        batteryPercent: Int? = nil,
        charging: Bool? = nil,
        batteryMinutesRemaining: Int? = nil,
        printStatus: String? = nil,
        connected: Bool = false,
        busy: Bool = false,
        lastError: BridgeErrorPayload? = nil
    ) {
        self.displayName = displayName
        self.model = model
        self.filmRemaining = filmRemaining
        self.batteryPercent = batteryPercent
        self.charging = charging
        self.batteryMinutesRemaining = batteryMinutesRemaining
        self.printStatus = printStatus
        self.connected = connected
        self.busy = busy
        self.lastError = lastError
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        self.displayName = try container.decodeIfPresent(String.self, forKey: .displayName)
        self.model = try container.decodeIfPresent(String.self, forKey: .model)
        self.filmRemaining = try container.decodeIfPresent(Int.self, forKey: .filmRemaining)
        self.batteryPercent = try container.decodeIfPresent(Int.self, forKey: .batteryPercent)
        self.charging = try container.decodeIfPresent(Bool.self, forKey: .charging)
        self.batteryMinutesRemaining = try container.decodeIfPresent(
            Int.self, forKey: .batteryMinutesRemaining)
        self.printStatus = try container.decodeIfPresent(String.self, forKey: .printStatus)
        self.connected = try container.decodeIfPresent(Bool.self, forKey: .connected) ?? false
        self.busy = try container.decodeIfPresent(Bool.self, forKey: .busy) ?? false
        self.lastError = try container.decodeIfPresent(BridgeErrorPayload.self, forKey: .lastError)
    }
}

struct BridgeUploadRecord: Codable, Equatable {
    var filename: String?
    var receivedAt: String?
    var printedAt: String?
    var status: String

    enum CodingKeys: String, CodingKey {
        case filename
        case receivedAt = "received_at"
        case printedAt = "printed_at"
        case status
    }
}

// MARK: - Pairing And Local Management Auth

enum BridgeClientKeyAlgorithm: String, Codable, Equatable {
    case ed25519
    case p256SHA256 = "p256_sha256"
}

struct BridgePairingStatus: Codable, Equatable {
    var open: Bool
    var authImplemented: Bool
    var confirmationCodeRequired: Bool
    var expiresAt: Int?
    var expiresInSeconds: Int?
    var pairedClientID: String?
    var authorizedClientCount: Int?

    enum CodingKeys: String, CodingKey {
        case open
        case authImplemented = "auth_implemented"
        case confirmationCodeRequired = "confirmation_code_required"
        case expiresAt = "expires_at"
        case expiresInSeconds = "expires_in_seconds"
        case pairedClientID = "paired_client_id"
        case authorizedClientCount = "authorized_client_count"
    }
}

struct BridgePairingCompleteRequest: Codable, Equatable {
    var clientID: String
    var clientName: String
    var publicKey: String
    var publicKeyAlgorithm: BridgeClientKeyAlgorithm
    var confirmationCode: String
    var expectedDeviceID: String?
    var expectedManagementPublicKeyFingerprint: String?

    enum CodingKeys: String, CodingKey {
        case clientID = "client_id"
        case clientName = "client_name"
        case publicKey = "public_key"
        case publicKeyAlgorithm = "public_key_algorithm"
        case confirmationCode = "confirmation_code"
        case expectedDeviceID = "expected_device_id"
        case expectedManagementPublicKeyFingerprint = "expected_management_public_key_fingerprint"
    }
}

/// Body for `POST /v1/pairing/usb_auto_trust`. Mirrors the pairing-complete shape
/// but omits the confirmation code — the bridge enforces physical USB presence
/// via the listening-interface check rather than an LCD-displayed code.
struct BridgeUSBAutoTrustRequest: Codable, Equatable {
    var clientID: String
    var clientName: String
    var publicKey: String
    var publicKeyAlgorithm: BridgeClientKeyAlgorithm
    var expectedDeviceID: String?

    enum CodingKeys: String, CodingKey {
        case clientID = "client_id"
        case clientName = "client_name"
        case publicKey = "public_key"
        case publicKeyAlgorithm = "public_key_algorithm"
        case expectedDeviceID = "expected_device_id"
    }
}

struct BridgePairingCompletion: Codable, Equatable {
    var clientID: String
    var clientName: String?
    var paired: Bool
    var publicKeyAlgorithm: BridgeClientKeyAlgorithm?
    var createdAt: String?
    var message: String?

    enum CodingKeys: String, CodingKey {
        case clientID = "client_id"
        case clientName = "client_name"
        case paired
        case publicKeyAlgorithm = "public_key_algorithm"
        case createdAt = "created_at"
        case message
    }
}

// MARK: - Update Packages

struct BridgeUpdatePackage: Codable, Equatable {
    var packageKind: String
    var version: String
    var target: String
    var archiveURL: URL
    var archiveSHA256: String
    var manifestURL: URL
    var manifestSHA256: String
    var checksumURL: URL
    var keyID: String?
    var requiredBridgeAPIVersion: Int?
    var minimumRollbackVersion: String?
    var manifestSignatureURL: URL?

    enum CodingKeys: String, CodingKey {
        case packageKind = "package_kind"
        case version
        case target
        case archiveURL = "archive_url"
        case archiveSHA256 = "archive_sha256"
        case manifestURL = "manifest_url"
        case manifestSHA256 = "manifest_sha256"
        case checksumURL = "checksum_url"
        case keyID = "key_id"
        case requiredBridgeAPIVersion = "required_bridge_api_version"
        case minimumRollbackVersion = "minimum_rollback_version"
        case manifestSignatureURL = "manifest_signature_url"
    }

    init(
        packageKind: String = "instantlink_bridge_firmware",
        version: String,
        target: String,
        archiveURL: URL,
        archiveSHA256: String,
        manifestURL: URL,
        manifestSHA256: String,
        checksumURL: URL,
        keyID: String? = nil,
        requiredBridgeAPIVersion: Int? = nil,
        minimumRollbackVersion: String? = nil,
        manifestSignatureURL: URL? = nil
    ) {
        self.packageKind = packageKind
        self.version = version
        self.target = target
        self.archiveURL = archiveURL
        self.archiveSHA256 = archiveSHA256
        self.manifestURL = manifestURL
        self.manifestSHA256 = manifestSHA256
        self.checksumURL = checksumURL
        self.keyID = keyID
        self.requiredBridgeAPIVersion = requiredBridgeAPIVersion
        self.minimumRollbackVersion = minimumRollbackVersion
        self.manifestSignatureURL = manifestSignatureURL
    }

    init(firmwarePackage: BridgeFirmwarePackage) {
        self.init(
            version: firmwarePackage.version,
            target: firmwarePackage.target,
            archiveURL: firmwarePackage.archiveURL,
            archiveSHA256: firmwarePackage.archiveSHA256,
            manifestURL: firmwarePackage.manifestURL,
            manifestSHA256: firmwarePackage.manifestSHA256,
            checksumURL: firmwarePackage.checksumURL,
            manifestSignatureURL: firmwarePackage.manifestSignatureURL
        )
    }

    static func bundledFirmwarePackage(bundle: Bundle = .main) -> BridgeUpdatePackage? {
        BridgeFirmwareBundleService.bundledPackage(bundle: bundle).map(BridgeUpdatePackage.init(firmwarePackage:))
    }
}

extension BridgeFirmwarePackage {
    var updatePackage: BridgeUpdatePackage {
        BridgeUpdatePackage(firmwarePackage: self)
    }
}

// MARK: - Update State

enum BridgeUpdatePhase: String, Codable, Equatable {
    case idle
    case checkingBridge = "checking_bridge"
    case backingUpSettings = "backing_up_settings"
    case verifyingUpdate = "verifying_update"
    case uploadingUpdate = "uploading_update"
    case installingUpdate = "installing_update"
    case restartingBridge = "restarting_bridge"
    case reconnecting
    case verifyingBridge = "verifying_bridge"
    case pendingVerification = "pending_verification"
    case done
    case failed
    case rolledBack = "rolled_back"
    case needsRecovery = "needs_recovery"
}

enum BridgeUpdateSafeState: String, Codable, Equatable {
    case unknown
    case updateNotInstalled = "update_not_installed"
    case installed
    case previousVersionRestored = "previous_version_restored"
    case bridgeNeedsRecovery = "bridge_needs_recovery"
}

enum BridgeUpdatePreflightCheckStatus: String, Codable, Equatable {
    case pass
    case warning
    case fail
}

struct BridgeUpdatePreflightCheck: Codable, Equatable {
    var name: String
    var status: BridgeUpdatePreflightCheckStatus
    var message: String?

    enum CodingKeys: String, CodingKey {
        case name
        case status
        case message
    }
}

struct BridgeUpdatePreflight: Codable, Equatable {
    var package: BridgeUpdatePackage
    var allowed: Bool
    var backupRequired: Bool
    var rollbackAvailable: Bool
    var checks: [BridgeUpdatePreflightCheck]
    var operationID: String?

    enum CodingKeys: String, CodingKey {
        case package
        case allowed
        case backupRequired = "backup_required"
        case rollbackAvailable = "rollback_available"
        case checks
        case operationID = "operation_id"
    }
}

struct BridgeUpdateSummary: Codable, Equatable {
    var currentVersion: String
    var availableVersion: String?
    var canUpdate: Bool
    var operationID: String?
    var phase: BridgeUpdatePhase?
    /// Version the bridge would restore to if a rollback is requested. The
    /// rollback affordance on the Mac is only shown when this is populated.
    /// Decoded from the bridge's `previous_version` key on the update summary;
    /// older bridges that do not emit the field decode as nil and the
    /// rollback UI stays hidden.
    var previousVersion: String?

    enum CodingKeys: String, CodingKey {
        case currentVersion = "current_version"
        case availableVersion = "available_version"
        case canUpdate = "can_update"
        case operationID = "operation_id"
        case phase
        case previousVersion = "previous_version"
    }
}

struct BridgeUpdateState: Codable, Equatable {
    var operationID: String
    var phase: BridgeUpdatePhase
    var progress: Double?
    var message: String?
    var safeState: BridgeUpdateSafeState
    var installedVersion: String?
    var error: BridgeErrorPayload?

    var isTerminal: Bool {
        switch phase {
        case .done, .failed, .rolledBack, .needsRecovery:
            return true
        case .idle, .checkingBridge, .backingUpSettings, .verifyingUpdate, .uploadingUpdate,
             .installingUpdate, .restartingBridge, .reconnecting, .verifyingBridge, .pendingVerification:
            return false
        }
    }

    enum CodingKeys: String, CodingKey {
        case operationID = "operation_id"
        case phase
        case progress
        case message
        case safeState = "safe_state"
        case installedVersion = "installed_version"
        case error
    }
}

struct BridgeUpdateEvent: Codable, Equatable, Identifiable {
    var id: String { eventID }

    var eventID: String
    var operationID: String
    var phase: BridgeUpdatePhase
    var progress: Double?
    var message: String?
    var state: BridgeUpdateState?

    enum CodingKeys: String, CodingKey {
        case eventID = "event_id"
        case operationID = "operation_id"
        case phase
        case progress
        case message
        case state
    }
}

// MARK: - Backup And Upload

struct BridgeBackupResult: Codable, Equatable {
    var backupID: String
    var manifestPath: String
    var archivePath: String
    var archiveSHA256: String
    var verified: Bool

    enum CodingKeys: String, CodingKey {
        case backupID = "backup_id"
        case manifestPath = "manifest_path"
        case archivePath = "archive_path"
        case archiveSHA256 = "archive_sha256"
        case verified
    }
}

struct BridgeBackupRestoreResult: Codable, Equatable {
    var backupID: String
    var restoredPaths: [String]
    var restoredCount: Int

    enum CodingKeys: String, CodingKey {
        case backupID = "backup_id"
        case restoredPaths = "restored_paths"
        case restoredCount = "restored_count"
    }
}

struct BridgeUploadResult: Codable, Equatable {
    var filename: String
    var storedPath: String
    var sizeBytes: Int
    var sha256: String

    enum CodingKeys: String, CodingKey {
        case filename
        case storedPath = "stored_path"
        case sizeBytes = "size_bytes"
        case sha256
    }
}

// MARK: - Diagnostics

/// Severity classification surfaced by the Bridge logs SSE stream.
enum BridgeLogLevel: String, Codable, Equatable, Hashable, CaseIterable {
    case info
    case warning
    case error

    var displayLabel: String {
        switch self {
        case .info: return "Info"
        case .warning: return "Warning"
        case .error: return "Error"
        }
    }
}

/// One redacted log entry from the Bridge management `/v1/logs/stream` SSE feed.
struct BridgeLogEvent: Codable, Equatable, Identifiable, Hashable {
    var id: String
    var timestamp: String
    var level: BridgeLogLevel
    var message: String

    init(id: String, timestamp: String, level: BridgeLogLevel, message: String) {
        self.id = id
        self.timestamp = timestamp
        self.level = level
        self.message = message
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        self.id = try container.decode(String.self, forKey: .id)
        self.timestamp = try container.decode(String.self, forKey: .timestamp)
        if let level = try? container.decode(BridgeLogLevel.self, forKey: .level) {
            self.level = level
        } else {
            // Unknown levels (e.g. "debug" or "trace") collapse to .info so a
            // future bridge level doesn't break the Mac client.
            self.level = .info
        }
        self.message = try container.decode(String.self, forKey: .message)
    }
}

/// Result of `POST /v1/support-bundle/create` — bundle ID plus archive location.
struct BridgeSupportBundleResult: Codable, Equatable {
    var schemaVersion: Int
    var bundleID: String
    var archivePath: String
    var sizeBytes: Int64
    var sha256: String
    var contents: [String]
    var createdAt: String

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case bundleID = "bundle_id"
        case archivePath = "archive_path"
        case sizeBytes = "size_bytes"
        case sha256
        case contents
        case createdAt = "created_at"
    }
}

// MARK: - Errors

struct BridgeErrorCode: RawRepresentable, Codable, Equatable, Hashable, ExpressibleByStringLiteral {
    var rawValue: String

    init(rawValue: String) {
        self.rawValue = rawValue
    }

    init(_ rawValue: String) {
        self.rawValue = rawValue
    }

    init(stringLiteral value: String) {
        self.rawValue = value
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        self.rawValue = try container.decode(String.self)
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        try container.encode(rawValue)
    }

    static let authRequired = BridgeErrorCode("auth_required")
    static let pairingRequired = BridgeErrorCode("pairing_required")
    static let invalidSignature = BridgeErrorCode("invalid_signature")
    static let preflightFailed = BridgeErrorCode("preflight_failed")
    static let updateInProgress = BridgeErrorCode("update_in_progress")
    static let deviceUnavailable = BridgeErrorCode("device_unavailable")
    static let missingPayload = BridgeErrorCode("missing_payload")
    static let unknown = BridgeErrorCode("unknown")
}

struct BridgeErrorPayload: Codable, Equatable {
    var message: String
    var details: [String: BridgeJSONValue]
    var retryAfterSeconds: Int?

    enum CodingKeys: String, CodingKey {
        case message
        case details
        case retryAfterSeconds = "retry_after_seconds"
    }

    init(
        message: String,
        details: [String: BridgeJSONValue] = [:],
        retryAfterSeconds: Int? = nil
    ) {
        self.message = message
        self.details = details
        self.retryAfterSeconds = retryAfterSeconds
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        self.message = try container.decode(String.self, forKey: .message)
        self.details = try container.decodeIfPresent([String: BridgeJSONValue].self, forKey: .details) ?? [:]
        self.retryAfterSeconds = try container.decodeIfPresent(Int.self, forKey: .retryAfterSeconds)
    }
}

struct BridgeAPIError: Error, Equatable, LocalizedError {
    var requestID: String
    var code: BridgeErrorCode
    var payload: BridgeErrorPayload

    var errorDescription: String? {
        payload.message
    }

    static func missingPayload(requestID: String, payloadName: String) -> BridgeAPIError {
        BridgeAPIError(
            requestID: requestID,
            code: .missingPayload,
            payload: BridgeErrorPayload(
                message: "Bridge response was missing \(payloadName)",
                details: ["payload": .string(payloadName)]
            )
        )
    }
}

enum BridgeJSONValue: Codable, Equatable {
    case string(String)
    case number(Double)
    case bool(Bool)
    case object([String: BridgeJSONValue])
    case array([BridgeJSONValue])
    case null

    init(from decoder: Decoder) throws {
        if var arrayContainer = try? decoder.unkeyedContainer() {
            var values: [BridgeJSONValue] = []
            while !arrayContainer.isAtEnd {
                values.append(try arrayContainer.decode(BridgeJSONValue.self))
            }
            self = .array(values)
            return
        }

        if let objectContainer = try? decoder.container(keyedBy: BridgeDynamicCodingKey.self) {
            var values: [String: BridgeJSONValue] = [:]
            for key in objectContainer.allKeys {
                values[key.stringValue] = try objectContainer.decode(BridgeJSONValue.self, forKey: key)
            }
            self = .object(values)
            return
        }

        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            self = .null
        } else if let value = try? container.decode(Bool.self) {
            self = .bool(value)
        } else if let value = try? container.decode(Double.self) {
            self = .number(value)
        } else {
            self = .string(try container.decode(String.self))
        }
    }

    func encode(to encoder: Encoder) throws {
        switch self {
        case .string(let value):
            var container = encoder.singleValueContainer()
            try container.encode(value)
        case .number(let value):
            var container = encoder.singleValueContainer()
            try container.encode(value)
        case .bool(let value):
            var container = encoder.singleValueContainer()
            try container.encode(value)
        case .object(let value):
            var container = encoder.container(keyedBy: BridgeDynamicCodingKey.self)
            for (key, nestedValue) in value {
                try container.encode(nestedValue, forKey: BridgeDynamicCodingKey(stringValue: key))
            }
        case .array(let values):
            var container = encoder.unkeyedContainer()
            for value in values {
                try container.encode(value)
            }
        case .null:
            var container = encoder.singleValueContainer()
            try container.encodeNil()
        }
    }
}

struct BridgeDynamicCodingKey: CodingKey {
    var stringValue: String
    var intValue: Int?

    init(stringValue: String) {
        self.stringValue = stringValue
        self.intValue = nil
    }

    init(intValue: Int) {
        self.stringValue = "\(intValue)"
        self.intValue = intValue
    }
}
