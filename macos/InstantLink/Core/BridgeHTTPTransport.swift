import Foundation

enum BridgeHTTPTransportError: Error, Equatable {
    case invalidResponse
    case invalidURL(String)
    case httpStatus(Int)
}

final class BridgeHTTPTransport: BridgeTransport {
    static let uploadFilenameHeader = "X-Upload-Filename"

    private struct PackageRequest: Encodable {
        var package: BridgeUpdatePackage
    }

    private struct RollbackRequest: Encodable {
        var reason: String
    }

    private struct BackupRestoreRequest: Encodable {
        var backupID: String

        enum CodingKeys: String, CodingKey {
            case backupID = "backup_id"
        }
    }

    private let baseURL: URL
    private let session: URLSession
    private let keyStore: BridgeClientKeyStore
    private let clientName: String
    private let now: () -> Date
    private let nonce: () -> String
    private let encoder: JSONEncoder
    private let decoder: JSONDecoder

    init(
        baseURL: URL,
        session: URLSession = .shared,
        keyStore: BridgeClientKeyStore = KeychainBridgeClientKeyStore(),
        clientName: String = BridgeHTTPTransport.defaultClientName(),
        now: @escaping () -> Date = Date.init,
        nonce: @escaping () -> String = BridgeManagementAuth.makeNonce
    ) {
        self.baseURL = baseURL
        self.session = session
        self.keyStore = keyStore
        self.clientName = clientName
        self.now = now
        self.nonce = nonce
        self.encoder = JSONEncoder()
        self.decoder = JSONDecoder()
    }

    func discover() async throws -> [BridgeDevice] {
        let envelope = try await send(
            try makeRequest(method: "GET", path: "/v1/hello")
        )
        var device = try envelope.requireDevice()
        if device.endpointURL == nil {
            device.endpointURL = baseURL
        }
        return [device]
    }

    func pairingStatus(device: BridgeDevice) async throws -> BridgePairingStatus {
        let envelope = try await send(
            try makeRequest(method: "GET", path: "/v1/pairing/status", device: device)
        )
        return try envelope.requirePairingStatus()
    }

    func completePairing(
        device: BridgeDevice,
        confirmationCode: String,
        clientName: String
    ) async throws -> BridgePairingCompletion {
        let resolvedClientName = clientName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            ? self.clientName
            : clientName
        let identity = try keyStore.loadIdentity(for: device.deviceID) ?? keyStore.createIdentity(
            for: device.deviceID,
            clientName: resolvedClientName
        )
        let pairingRequest = BridgePairingCompleteRequest(
            clientID: identity.clientID,
            clientName: identity.clientName,
            publicKey: identity.publicKey,
            publicKeyAlgorithm: identity.pairingRequestPublicKeyAlgorithm,
            confirmationCode: confirmationCode,
            expectedDeviceID: device.deviceID,
            expectedManagementPublicKeyFingerprint: device.managementPublicKeyFingerprint
        )
        let envelope = try await send(
            try makeRequest(
                method: "POST",
                path: "/v1/pairing/complete",
                body: encoder.encode(pairingRequest),
                device: device
            )
        )
        let completion = try envelope.requirePairingCompletion()
        try keyStore.saveIdentity(identity, for: device.deviceID)
        return completion
    }

    func forgetLocalAuth(device: BridgeDevice) async throws {
        try keyStore.deleteIdentity(for: device.deviceID)
    }

    func status(device: BridgeDevice) async throws -> BridgeStatus {
        let envelope = try await send(
            try makeRequest(method: "GET", path: "/v1/status", signedFor: device)
        )
        return try envelope.requireStatus()
    }

    func preflightUpdate(device: BridgeDevice, package: BridgeUpdatePackage) async throws -> BridgeUpdatePreflight {
        let envelope = try await send(
            try makeRequest(
                method: "POST",
                path: "/v1/update/preflight",
                body: encoder.encode(PackageRequest(package: package)),
                signedFor: device
            )
        )
        return try envelope.requirePreflight()
    }

    func startUpdate(device: BridgeDevice, package: BridgeUpdatePackage) async throws -> BridgeUpdateState {
        let preflight = try await preflightUpdate(device: device, package: package)
        guard preflight.allowed else {
            throw BridgeTransportError.updatePreflightFailed
        }
        let envelope = try await send(
            try makeRequest(
                method: "POST",
                path: "/v1/update/install",
                body: encoder.encode(PackageRequest(package: package)),
                signedFor: device
            )
        )
        return try envelope.requireUpdateState()
    }

    func updateStatus(device: BridgeDevice, operationID: String) async throws -> BridgeUpdateState {
        let envelope = try await send(
            try makeRequest(
                method: "GET",
                path: "/v1/update/status",
                queryItems: [URLQueryItem(name: "operation_id", value: operationID)],
                signedFor: device
            )
        )
        return try envelope.requireUpdateState()
    }

    func updateEvents(device: BridgeDevice, operationID: String) async throws -> AsyncThrowingStream<BridgeUpdateEvent, Error> {
        let envelope = try await send(
            try makeRequest(
                method: "GET",
                path: "/v1/events",
                queryItems: [URLQueryItem(name: "operation_id", value: operationID)],
                signedFor: device
            )
        )
        let event = try envelope.requireUpdateEvent()
        return AsyncThrowingStream { continuation in
            continuation.yield(event)
            continuation.finish()
        }
    }

    func uploadUpdate(device: BridgeDevice, package: BridgeUpdatePackage) async throws -> BridgeUploadResult {
        let data = try Data(contentsOf: package.archiveURL)
        let filename = package.archiveURL.lastPathComponent
        let envelope = try await send(
            try makeRequest(
                method: "POST",
                path: "/v1/update/upload",
                body: data,
                contentType: "application/octet-stream",
                extraHeaders: [BridgeHTTPTransport.uploadFilenameHeader: filename],
                signedFor: device
            )
        )
        return try envelope.requireUpload()
    }

    func markUpdateGood(device: BridgeDevice) async throws -> BridgeUpdateState {
        let envelope = try await send(
            try makeRequest(method: "POST", path: "/v1/update/mark-good", signedFor: device)
        )
        return try envelope.requireUpdateState()
    }

    func rollbackUpdate(device: BridgeDevice, reason: String) async throws -> BridgeUpdateState {
        let envelope = try await send(
            try makeRequest(
                method: "POST",
                path: "/v1/update/rollback",
                body: encoder.encode(RollbackRequest(reason: reason)),
                signedFor: device
            )
        )
        return try envelope.requireUpdateState()
    }

    func createBackup(device: BridgeDevice) async throws -> BridgeBackupResult {
        let envelope = try await send(
            try makeRequest(method: "POST", path: "/v1/backup/create", signedFor: device)
        )
        return try envelope.requireBackup()
    }

    func restoreBackup(device: BridgeDevice, backupID: String) async throws -> BridgeBackupRestoreResult {
        let envelope = try await send(
            try makeRequest(
                method: "POST",
                path: "/v1/backup/restore",
                body: encoder.encode(BackupRestoreRequest(backupID: backupID)),
                signedFor: device
            )
        )
        return try envelope.requireBackupRestore()
    }

    private func makeRequest(
        method: String,
        path: String,
        queryItems: [URLQueryItem] = [],
        body: Data = Data(),
        contentType: String = "application/json",
        extraHeaders: [String: String] = [:],
        device: BridgeDevice? = nil,
        signedFor signedDevice: BridgeDevice? = nil
    ) throws -> URLRequest {
        let endpoint = signedDevice?.endpointURL ?? device?.endpointURL ?? baseURL
        let url = try makeURL(base: endpoint, path: path, queryItems: queryItems)
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue(UUID().uuidString, forHTTPHeaderField: BridgeManagementAuth.requestIDHeader)
        for (name, value) in extraHeaders {
            request.setValue(value, forHTTPHeaderField: name)
        }
        if !body.isEmpty {
            request.httpBody = body
            request.setValue(contentType, forHTTPHeaderField: "Content-Type")
        }

        if let signedDevice {
            guard let identity = try keyStore.loadIdentity(for: signedDevice.deviceID) else {
                throw BridgeTransportError.localAuthNotFound(signedDevice.deviceID)
            }
            let headers = try BridgeManagementAuth.signedHeaders(
                identity: identity,
                method: method,
                path: canonicalPath(for: url),
                body: body,
                timestamp: Int(now().timeIntervalSince1970),
                nonce: nonce()
            )
            for (name, value) in headers {
                request.setValue(value, forHTTPHeaderField: name)
            }
        }

        return request
    }

    private func send(_ request: URLRequest) async throws -> BridgeAPIEnvelope {
        let (data, response) = try await session.data(for: request)
        guard let httpResponse = response as? HTTPURLResponse else {
            throw BridgeHTTPTransportError.invalidResponse
        }

        if let envelope = try? decoder.decode(BridgeAPIEnvelope.self, from: data) {
            if (200..<300).contains(httpResponse.statusCode) {
                return envelope
            }
            try envelope.requireOK()
        }

        throw BridgeHTTPTransportError.httpStatus(httpResponse.statusCode)
    }

    private func makeURL(
        base: URL,
        path: String,
        queryItems: [URLQueryItem]
    ) throws -> URL {
        guard var components = URLComponents(url: base, resolvingAgainstBaseURL: false) else {
            throw BridgeHTTPTransportError.invalidURL(base.absoluteString)
        }
        let basePath = components.percentEncodedPath.trimmingTrailingSlash()
        let normalizedPath = path.hasPrefix("/") ? String(path.dropFirst()) : path
        components.percentEncodedPath = "\(basePath)/\(normalizedPath)"
        components.queryItems = queryItems.isEmpty ? nil : queryItems
        guard let url = components.url else {
            throw BridgeHTTPTransportError.invalidURL(base.absoluteString)
        }
        return url
    }

    private func canonicalPath(for url: URL) -> String {
        guard let components = URLComponents(url: url, resolvingAgainstBaseURL: false) else {
            return url.path
        }
        if let query = components.percentEncodedQuery, !query.isEmpty {
            return "\(components.percentEncodedPath)?\(query)"
        }
        return components.percentEncodedPath
    }

    private static func defaultClientName() -> String {
        let hostName = Host.current().localizedName ?? ProcessInfo.processInfo.hostName
        return hostName.isEmpty ? "InstantLink Mac" : hostName
    }
}

private extension String {
    func trimmingTrailingSlash() -> String {
        guard hasSuffix("/") else { return self }
        return String(dropLast()).trimmingTrailingSlash()
    }
}
