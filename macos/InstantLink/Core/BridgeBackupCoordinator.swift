import Combine
import Foundation

// MARK: - Public state

/// Snapshot exposed to SwiftUI views driving the Backup tab.
///
/// The coordinator owns a single state machine that walks through:
/// createBackup → download archive → write `.bridgebackup` file (on backup),
/// or read `.bridgebackup` file → upload archive → restoreBackup → reconnect
/// (on restore). Each step mutates this snapshot which the view observes via
/// `@ObservedObject`.
struct BridgeBackupSnapshot: Equatable {
    var operation: Operation?
    var lastResult: Result?

    enum Operation: Equatable {
        case creatingBackup(progress: Double?)
        case downloadingBackup(progress: Double?)
        case restoringBackup(phase: RestorePhase)
    }

    enum RestorePhase: Equatable {
        case uploading
        case applying
        case restarting
        case verifying
    }

    enum Result: Equatable {
        case backupCreated(path: URL, at: Date, deviceID: String)
        case backupRestored(at: Date)
        case failed(reason: String, at: Date)
    }

    static let empty = BridgeBackupSnapshot(operation: nil, lastResult: nil)
}

// MARK: - File format

/// On-disk wrapper for a `.bridgebackup` file. We don't crack the bridge's
/// raw archive open; we wrap it with metadata so cross-bridge restore can ask
/// the user to confirm before overwriting a different bridge's identity.
struct BridgeBackupFile: Codable, Equatable {
    static let currentSchema: Int = 1
    static let fileExtension: String = "bridgebackup"

    var schema: Int
    var sourceDeviceID: String
    var sourceDisplayName: String?
    var createdAt: Date
    var bridgeBackupID: String
    var archiveSHA256: String
    /// Base64-encoded copy of the archive bytes returned by the bridge.
    /// The bridge has already encrypted the bytes with the user's passphrase
    /// (server-side encryption per the contract) so the on-disk payload is
    /// safe to ship, but we still surface a "treat like a password" warning
    /// in the UI because it contains the bridge's signing identity.
    var archiveBase64: String

    enum CodingKeys: String, CodingKey {
        case schema
        case sourceDeviceID = "source_device_id"
        case sourceDisplayName = "source_display_name"
        case createdAt = "created_at"
        case bridgeBackupID = "bridge_backup_id"
        case archiveSHA256 = "archive_sha256"
        case archiveBase64 = "archive_base64"
    }
}

// MARK: - Archive transfer

/// Transfer surface used by the coordinator to ship the archive bytes between
/// the bridge and the Mac. Abstracted so tests can swap in deterministic
/// implementations that don't need URLSession or a filesystem on the bridge.
protocol BridgeBackupArchiveTransfer: AnyObject {
    /// Download an archive that the bridge previously published via
    /// `createBackup`. The Mac saves the bytes inside the `.bridgebackup`
    /// wrapper before persisting it to the user's chosen path.
    func downloadArchive(device: BridgeDevice, backup: BridgeBackupResult) async throws -> Data

    /// Upload archive bytes to the bridge ahead of `restoreBackup`. Returns
    /// the server-side `backup_id` the bridge will recognize in the restore
    /// call.
    func uploadArchive(
        device: BridgeDevice,
        bytes: Data,
        suggestedBackupID: String
    ) async throws -> String
}

/// Default transfer that performs the download/upload via URLSession against
/// the bridge's signed archive routes. The real bridge contract for
/// download/upload is not finalized yet (the existing routes only expose
/// `archive_path` and `backup_id`); in production this is wired up once the
/// bridge ships the file-transfer endpoints. Tests use an in-memory
/// implementation that bypasses the network entirely.
final class HTTPBridgeBackupArchiveTransfer: BridgeBackupArchiveTransfer {
    private let session: URLSession

    init(session: URLSession = .shared) {
        self.session = session
    }

    func downloadArchive(device _: BridgeDevice, backup: BridgeBackupResult) async throws -> Data {
        // The bridge's current contract returns `archive_path` (a server-side
        // filesystem path) rather than a download URL. Until the bridge ships
        // a signed download endpoint we surface a clear error so the UI can
        // explain that download requires a newer bridge.
        throw BridgeBackupCoordinatorError.downloadNotSupported(archivePath: backup.archivePath)
    }

    func uploadArchive(device _: BridgeDevice, bytes _: Data, suggestedBackupID: String) async throws -> String {
        // Same story as download: upload requires a future bridge contract.
        throw BridgeBackupCoordinatorError.uploadNotSupported(suggestedBackupID: suggestedBackupID)
    }
}

// MARK: - Errors

enum BridgeBackupCoordinatorError: Error, Equatable, LocalizedError {
    case noDevice
    case passphraseTooShort
    case fileReadFailed(String)
    case fileWriteFailed(String)
    case invalidBackupFile(String)
    case downloadNotSupported(archivePath: String)
    case uploadNotSupported(suggestedBackupID: String)

    var errorDescription: String? {
        switch self {
        case .noDevice:
            return L("No Bridge is currently connected.")
        case .passphraseTooShort:
            return L("Passphrase must be at least 8 characters.")
        case .fileReadFailed(let detail):
            return String(format: L("Could not read the backup file: %@"), detail)
        case .fileWriteFailed(let detail):
            return String(format: L("Could not write the backup file: %@"), detail)
        case .invalidBackupFile(let detail):
            return String(format: L("Backup file is not valid: %@"), detail)
        case .downloadNotSupported:
            return L("This Bridge does not yet support backup download. Update the Bridge to use this feature.")
        case .uploadNotSupported:
            return L("This Bridge does not yet support backup upload. Update the Bridge to use this feature.")
        }
    }
}

// MARK: - Coordinator

/// Owns the Bridge backup + restore lifecycle. Composed by the Backup tab;
/// receives a `BridgeTransport` so tests use the in-memory mock and inject
/// scripted backup/restore results.
@MainActor
final class BridgeBackupCoordinator: ObservableObject {
    @Published private(set) var snapshot: BridgeBackupSnapshot

    private let transport: BridgeTransport
    private let archiveTransfer: BridgeBackupArchiveTransfer
    private let fileManager: FileManager
    private let now: () -> Date

    static let minimumPassphraseLength: Int = 8

    init(
        transport: BridgeTransport,
        archiveTransfer: BridgeBackupArchiveTransfer = HTTPBridgeBackupArchiveTransfer(),
        fileManager: FileManager = .default,
        now: @escaping () -> Date = Date.init
    ) {
        self.transport = transport
        self.archiveTransfer = archiveTransfer
        self.fileManager = fileManager
        self.now = now
        self.snapshot = .empty
    }

    // MARK: Backup

    /// Walk the create → download → save pipeline. Reports progress through
    /// `snapshot.operation` and surfaces success or failure via
    /// `snapshot.lastResult`. The destination is overwritten atomically.
    func createBackup(
        device: BridgeDevice,
        passphrase: String,
        destinationURL: URL
    ) async {
        guard passphrase.count >= Self.minimumPassphraseLength else {
            failOperation(reason: BridgeBackupCoordinatorError.passphraseTooShort.localizedDescription)
            return
        }

        mutate { snapshot in
            snapshot.lastResult = nil
            snapshot.operation = .creatingBackup(progress: nil)
        }

        let backup: BridgeBackupResult
        do {
            backup = try await transport.createBackup(device: device, passphrase: passphrase)
        } catch {
            failOperation(reason: Self.message(for: error))
            return
        }

        mutate { snapshot in
            snapshot.operation = .downloadingBackup(progress: nil)
        }

        let archiveBytes: Data
        do {
            archiveBytes = try await archiveTransfer.downloadArchive(device: device, backup: backup)
        } catch {
            failOperation(reason: Self.message(for: error))
            return
        }

        let file = BridgeBackupFile(
            schema: BridgeBackupFile.currentSchema,
            sourceDeviceID: device.deviceID,
            sourceDisplayName: device.displayName,
            createdAt: now(),
            bridgeBackupID: backup.backupID,
            archiveSHA256: backup.archiveSHA256,
            archiveBase64: archiveBytes.base64EncodedString()
        )

        do {
            try writeBackupFile(file, to: destinationURL)
        } catch {
            failOperation(reason: Self.message(for: error))
            return
        }

        mutate { snapshot in
            snapshot.operation = nil
            snapshot.lastResult = .backupCreated(
                path: destinationURL,
                at: now(),
                deviceID: device.deviceID
            )
        }
    }

    // MARK: Restore

    /// Read a `.bridgebackup` file and report what we know about it without
    /// kicking off a restore. Used by the view to render the
    /// restore-to-different-bridge confirmation copy with the right source ID.
    func inspectBackupFile(at url: URL) throws -> BridgeBackupFile {
        let data: Data
        do {
            data = try Data(contentsOf: url)
        } catch {
            throw BridgeBackupCoordinatorError.fileReadFailed("\(error.localizedDescription)")
        }
        do {
            let decoder = JSONDecoder()
            decoder.dateDecodingStrategy = .iso8601
            return try decoder.decode(BridgeBackupFile.self, from: data)
        } catch {
            throw BridgeBackupCoordinatorError.invalidBackupFile("\(error.localizedDescription)")
        }
    }

    /// Walk the read-file → upload → restore pipeline. Reports progress
    /// through `snapshot.operation` and surfaces success/failure via
    /// `snapshot.lastResult`. The caller is responsible for the
    /// restore-to-different-bridge confirmation; this method assumes the user
    /// has approved any identity-overwrite prompt already.
    func restoreBackup(
        device: BridgeDevice,
        fileURL: URL,
        passphrase: String
    ) async {
        guard passphrase.count >= Self.minimumPassphraseLength else {
            failOperation(reason: BridgeBackupCoordinatorError.passphraseTooShort.localizedDescription)
            return
        }

        mutate { snapshot in
            snapshot.lastResult = nil
            snapshot.operation = .restoringBackup(phase: .uploading)
        }

        let backupFile: BridgeBackupFile
        do {
            backupFile = try inspectBackupFile(at: fileURL)
        } catch {
            failOperation(reason: Self.message(for: error))
            return
        }

        guard let archiveBytes = Data(base64Encoded: backupFile.archiveBase64) else {
            failOperation(
                reason: BridgeBackupCoordinatorError.invalidBackupFile("archive payload is not valid base64")
                    .localizedDescription
            )
            return
        }

        let uploadedBackupID: String
        do {
            uploadedBackupID = try await archiveTransfer.uploadArchive(
                device: device,
                bytes: archiveBytes,
                suggestedBackupID: backupFile.bridgeBackupID
            )
        } catch {
            failOperation(reason: Self.message(for: error))
            return
        }

        mutate { snapshot in
            snapshot.operation = .restoringBackup(phase: .applying)
        }

        do {
            _ = try await transport.restoreBackup(
                device: device,
                backupID: uploadedBackupID,
                passphrase: passphrase
            )
        } catch {
            failOperation(reason: Self.message(for: error))
            return
        }

        mutate { snapshot in
            snapshot.operation = .restoringBackup(phase: .restarting)
        }

        mutate { snapshot in
            snapshot.operation = .restoringBackup(phase: .verifying)
        }

        mutate { snapshot in
            snapshot.operation = nil
            snapshot.lastResult = .backupRestored(at: now())
        }
    }

    /// Clear the last result so the toast disappears.
    func clearLastResult() {
        mutate { snapshot in
            snapshot.lastResult = nil
        }
    }

    // MARK: Helpers

    private func writeBackupFile(_ file: BridgeBackupFile, to destinationURL: URL) throws {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .prettyPrinted]
        encoder.dateEncodingStrategy = .iso8601
        let data: Data
        do {
            data = try encoder.encode(file)
        } catch {
            throw BridgeBackupCoordinatorError.fileWriteFailed("\(error.localizedDescription)")
        }

        let parent = destinationURL.deletingLastPathComponent()
        let tempURL = parent.appendingPathComponent(
            ".bridgebackup-\(UUID().uuidString).tmp"
        )
        do {
            try data.write(to: tempURL, options: [.atomic])
            if fileManager.fileExists(atPath: destinationURL.path) {
                try fileManager.removeItem(at: destinationURL)
            }
            try fileManager.moveItem(at: tempURL, to: destinationURL)
        } catch {
            try? fileManager.removeItem(at: tempURL)
            throw BridgeBackupCoordinatorError.fileWriteFailed("\(error.localizedDescription)")
        }
    }

    private func failOperation(reason: String) {
        mutate { snapshot in
            snapshot.operation = nil
            snapshot.lastResult = .failed(reason: reason, at: now())
        }
    }

    private func mutate(_ change: (inout BridgeBackupSnapshot) -> Void) {
        var copy = snapshot
        change(&copy)
        if copy != snapshot {
            snapshot = copy
        }
    }

    private static func message(for error: Error) -> String {
        if let api = error as? BridgeAPIError {
            return api.payload.message
        }
        if let coordinatorError = error as? BridgeBackupCoordinatorError {
            return coordinatorError.localizedDescription
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
            }
        }
        return "\(error.localizedDescription)"
    }
}

// MARK: - Test transfer

/// In-memory archive transfer used by tests. The download returns canned
/// bytes; the upload records them and returns either the suggested backup id
/// or a scripted override.
@MainActor
final class InMemoryBridgeBackupArchiveTransfer: BridgeBackupArchiveTransfer {
    var archiveBytes: Data
    var uploadReturnsBackupID: String?
    var shouldFailDownload: Bool
    var shouldFailUpload: Bool
    private(set) var downloadCalls: Int = 0
    private(set) var uploadCalls: Int = 0
    private(set) var lastUploadedBytes: Data?

    init(
        archiveBytes: Data = Data([0x01, 0x02, 0x03, 0x04]),
        uploadReturnsBackupID: String? = nil,
        shouldFailDownload: Bool = false,
        shouldFailUpload: Bool = false
    ) {
        self.archiveBytes = archiveBytes
        self.uploadReturnsBackupID = uploadReturnsBackupID
        self.shouldFailDownload = shouldFailDownload
        self.shouldFailUpload = shouldFailUpload
    }

    nonisolated func downloadArchive(device _: BridgeDevice, backup _: BridgeBackupResult) async throws -> Data {
        let snapshot = await Task { @MainActor in
            self.downloadCalls += 1
            return (self.shouldFailDownload, self.archiveBytes)
        }.value
        if snapshot.0 {
            throw BridgeHTTPTransportError.invalidResponse
        }
        return snapshot.1
    }

    nonisolated func uploadArchive(
        device _: BridgeDevice,
        bytes: Data,
        suggestedBackupID: String
    ) async throws -> String {
        let snapshot = await Task { @MainActor in
            self.uploadCalls += 1
            self.lastUploadedBytes = bytes
            return (self.shouldFailUpload, self.uploadReturnsBackupID ?? suggestedBackupID)
        }.value
        if snapshot.0 {
            throw BridgeHTTPTransportError.invalidResponse
        }
        return snapshot.1
    }
}
