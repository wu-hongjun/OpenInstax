import Foundation

final class BridgeHTTPTransportTests {
    func testCanonicalRequestPayloadMatchesBridgeManagerContract() throws {
        let bodySHA256 = BridgeManagementAuth.sha256Hex(Data())
        let payload = try BridgeManagementAuth.canonicalRequestPayload(
            method: "get",
            path: "/v1/status?operation_id=update-1",
            bodySHA256: bodySHA256,
            timestamp: 1000,
            nonce: "nonce-0001"
        )

        try expectEqual(
            String(data: payload, encoding: .utf8),
            """
            instantlink-bridge-management-v1
            GET
            /v1/status?operation_id=update-1
            e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
            1000
            nonce-0001
            """
        )
    }

    func testHTTPTransportReadsHelloAndPairingStatusWithoutAuth() async throws {
        let session = makeSession { request in
            switch request.url?.path {
            case "/v1/hello":
                try expectNil(request.value(forHTTPHeaderField: BridgeManagementAuth.clientIDHeader))
                return .json(200, Self.helloEnvelope)
            case "/v1/pairing/status":
                try expectNil(request.value(forHTTPHeaderField: BridgeManagementAuth.clientIDHeader))
                return .json(200, Self.pairingStatusEnvelope)
            default:
                return .json(404, Self.errorEnvelope(code: "not_found"))
            }
        }
        let transport = BridgeHTTPTransport(
            baseURL: URL(string: "http://192.168.7.1:8742")!,
            session: session,
            keyStore: FakeBridgeClientKeyStore()
        )

        let devices = try await transport.discover()
        try expectEqual(devices.count, 1)
        try expectEqual(devices[0].deviceID, "IB-1234ABCD")
        try expectEqual(devices[0].endpointURL, URL(string: "http://192.168.7.1:8742")!)

        let pairing = try await transport.pairingStatus(device: devices[0])
        try expectFalse(pairing.open)
        try expectFalse(pairing.authImplemented)
        try expectTrue(pairing.confirmationCodeRequired)
    }

    func testHTTPTransportSignsAdminStatusRequest() async throws {
        let identity = try makeIdentity()
        let keyStore = FakeBridgeClientKeyStore(identity: identity)
        let session = makeSession { request in
            try expectEqual(request.url?.path, "/v1/status")
            try expectEqual(request.value(forHTTPHeaderField: BridgeManagementAuth.clientIDHeader), "macbook")
            try expectEqual(request.value(forHTTPHeaderField: BridgeManagementAuth.timestampHeader), "1000")
            try expectEqual(request.value(forHTTPHeaderField: BridgeManagementAuth.nonceHeader), "nonce-0001")
            try expectTrue(
                request.value(forHTTPHeaderField: BridgeManagementAuth.signatureHeader)?.isEmpty == false
            )
            return .json(200, Self.statusEnvelope)
        }
        let transport = BridgeHTTPTransport(
            baseURL: URL(string: "http://bridge.local:8742")!,
            session: session,
            keyStore: keyStore,
            now: { Date(timeIntervalSince1970: 1000) },
            nonce: { "nonce-0001" }
        )

        let status = try await transport.status(device: makeDevice())
        try expectEqual(status.deviceID, "IB-1234ABCD")
        try expectEqual(status.readiness, .ready)
    }

    func testCompletePairingSavesIdentityOnlyAfterServerAcceptsRequest() async throws {
        let identity = try makeIdentity()
        let keyStore = FakeBridgeClientKeyStore(createdIdentity: identity)
        let session = makeSession { request in
            try expectEqual(request.url?.path, "/v1/pairing/complete")
            let body = requestBody(from: request)
            let pairingRequest = try JSONDecoder().decode(BridgePairingCompleteRequest.self, from: body)
            try expectEqual(pairingRequest.clientID, "macbook")
            try expectEqual(pairingRequest.clientName, "Test Mac")
            try expectEqual(pairingRequest.publicKey, identity.publicKey)
            try expectEqual(pairingRequest.publicKeyAlgorithm, .ed25519)
            try expectEqual(pairingRequest.confirmationCode, "123456")
            try expectEqual(pairingRequest.expectedDeviceID, "IB-1234ABCD")
            return .json(200, Self.pairingCompleteEnvelope)
        }
        let transport = BridgeHTTPTransport(
            baseURL: URL(string: "http://bridge.local:8742")!,
            session: session,
            keyStore: keyStore
        )

        let completion = try await transport.completePairing(
            device: makeDevice(),
            confirmationCode: "123456",
            clientName: "Test Mac"
        )
        try expectEqual(completion.clientID, "macbook")
        try expectTrue(completion.paired)
        try expectEqual(keyStore.savedIdentity?.clientID, "macbook")
        try expectEqual(keyStore.savedBridgeID, "IB-1234ABCD")
    }

    func testCompletePairingDoesNotSaveIdentityWhenServerRouteIsNotReady() async throws {
        let identity = try makeIdentity()
        let keyStore = FakeBridgeClientKeyStore(createdIdentity: identity)
        let session = makeSession { _ in
            .json(423, Self.errorEnvelope(code: "pairing_not_open"))
        }
        let transport = BridgeHTTPTransport(
            baseURL: URL(string: "http://bridge.local:8742")!,
            session: session,
            keyStore: keyStore
        )

        do {
            _ = try await transport.completePairing(
                device: makeDevice(),
                confirmationCode: "123456",
                clientName: "Test Mac"
            )
            throw MacTestFailure(
                file: #filePath,
                line: #line,
                message: "Expected rejected pairing to throw"
            )
        } catch let error as BridgeAPIError {
            try expectEqual(error.code, "pairing_not_open")
            try expectNil(keyStore.savedIdentity)
        }
    }

    func testUSBAutoTrustSendsExpectedPayloadAndDoesNotSignRequest() async throws {
        let identity = try makeIdentity()
        let keyStore = FakeBridgeClientKeyStore(createdIdentity: identity)
        let session = makeSession { request in
            try expectEqual(request.url?.path, "/v1/pairing/usb_auto_trust")
            try expectEqual(request.httpMethod, "POST")
            // Auto-trust must NOT include the signing headers — the bridge
            // authorizes by listening-interface IP, not by signed request.
            try expectNil(request.value(forHTTPHeaderField: BridgeManagementAuth.clientIDHeader))
            try expectNil(request.value(forHTTPHeaderField: BridgeManagementAuth.timestampHeader))
            try expectNil(request.value(forHTTPHeaderField: BridgeManagementAuth.nonceHeader))
            try expectNil(request.value(forHTTPHeaderField: BridgeManagementAuth.signatureHeader))

            let body = requestBody(from: request)
            let parsed = try JSONDecoder().decode(BridgeUSBAutoTrustRequest.self, from: body)
            try expectEqual(parsed.clientID, "macbook")
            try expectEqual(parsed.clientName, "Test Mac")
            try expectEqual(parsed.publicKey, identity.publicKey)
            try expectEqual(parsed.publicKeyAlgorithm, .ed25519)
            try expectEqual(parsed.expectedDeviceID, "IB-1234ABCD")
            return .json(200, Self.pairingCompleteEnvelope)
        }
        let transport = BridgeHTTPTransport(
            baseURL: URL(string: "http://192.168.7.1:8742")!,
            session: session,
            keyStore: keyStore
        )

        let completion = try await transport.usbAutoTrust(
            device: makeDevice(),
            clientName: "Test Mac"
        )
        try expectTrue(completion.paired)
        try expectEqual(completion.clientID, "macbook")
        try expectEqual(keyStore.savedIdentity?.clientID, "macbook")
        try expectEqual(keyStore.savedBridgeID, "IB-1234ABCD")
    }

    func testUSBAutoTrustSurfacesRejectionError() async throws {
        let keyStore = FakeBridgeClientKeyStore(createdIdentity: try makeIdentity())
        let session = makeSession { _ in
            .json(403, Self.errorEnvelope(code: "not_usb_interface"))
        }
        let transport = BridgeHTTPTransport(
            baseURL: URL(string: "http://192.168.8.1:8742")!,
            session: session,
            keyStore: keyStore
        )

        do {
            _ = try await transport.usbAutoTrust(
                device: makeDevice(),
                clientName: "Test Mac"
            )
            throw MacTestFailure(
                file: #filePath,
                line: #line,
                message: "Expected rejected auto-trust to throw"
            )
        } catch let error as BridgeAPIError {
            try expectEqual(error.code, "not_usb_interface")
            try expectNil(keyStore.savedIdentity)
        }
    }

    func testForgetLocalAuthDeletesOnlyLocalIdentity() async throws {
        let keyStore = FakeBridgeClientKeyStore(identity: try makeIdentity())
        let transport = BridgeHTTPTransport(
            baseURL: URL(string: "http://bridge.local:8742")!,
            session: makeSession { _ in .json(500, Self.errorEnvelope(code: "unexpected_network")) },
            keyStore: keyStore
        )

        try await transport.forgetLocalAuth(device: makeDevice())
        try expectEqual(keyStore.deletedBridgeID, "IB-1234ABCD")
    }

    func testStartUpdateRequiresAllowedPreflightBeforeInstall() async throws {
        let keyStore = FakeBridgeClientKeyStore(identity: try makeIdentity())
        var requestedPaths: [String] = []
        let session = makeSession { request in
            requestedPaths.append(request.url?.path ?? "")
            switch request.url?.path {
            case "/v1/update/preflight":
                return .json(200, Self.preflightEnvelope(allowed: false))
            case "/v1/update/install":
                return .json(200, Self.updateEnvelope)
            default:
                return .json(404, Self.errorEnvelope(code: "not_found"))
            }
        }
        let transport = BridgeHTTPTransport(
            baseURL: URL(string: "http://bridge.local:8742")!,
            session: session,
            keyStore: keyStore,
            now: { Date(timeIntervalSince1970: 1000) },
            nonce: { "nonce-0001" }
        )

        do {
            _ = try await transport.startUpdate(device: makeDevice(), package: makePackage(version: "0.2.0"))
            throw MacTestFailure(
                file: #filePath,
                line: #line,
                message: "Expected denied preflight to stop install"
            )
        } catch BridgeTransportError.updatePreflightFailed {
            try expectEqual(requestedPaths, ["/v1/update/preflight"])
        }
    }

    func testUploadUpdateSignsAndPostsArchiveBytes() async throws {
        let keyStore = FakeBridgeClientKeyStore(identity: try makeIdentity())
        let archiveURL = FileManager.default.temporaryDirectory
            .appendingPathComponent("InstantLinkBridgeFirmware-v0.2.0-linux-aarch64.tar.gz")
        let archiveBytes = Data("firmware-archive-bytes".utf8)
        try archiveBytes.write(to: archiveURL)
        defer { try? FileManager.default.removeItem(at: archiveURL) }

        let session = makeSession { request in
            try expectEqual(request.url?.path, "/v1/update/upload")
            try expectEqual(request.httpMethod, "POST")
            try expectEqual(
                request.value(forHTTPHeaderField: BridgeHTTPTransport.uploadFilenameHeader),
                "InstantLinkBridgeFirmware-v0.2.0-linux-aarch64.tar.gz"
            )
            try expectEqual(requestBody(from: request), archiveBytes)
            try expectTrue(
                request.value(forHTTPHeaderField: BridgeManagementAuth.signatureHeader)?.isEmpty == false
            )
            return .json(200, Self.uploadEnvelope)
        }
        let transport = BridgeHTTPTransport(
            baseURL: URL(string: "http://bridge.local:8742")!,
            session: session,
            keyStore: keyStore,
            now: { Date(timeIntervalSince1970: 1000) },
            nonce: { "nonce-0001" }
        )

        var package = makePackage(version: "0.2.0")
        package.archiveURL = archiveURL
        let result = try await transport.uploadUpdate(device: makeDevice(), package: package)
        try expectEqual(result.filename, "InstantLinkBridgeFirmware-v0.2.0-linux-aarch64.tar.gz")
        try expectEqual(result.sizeBytes, 22)
        try expectEqual(result.sha256, "upload-sha")
    }

    func testCreateBackupSignsRequestAndDecodesResult() async throws {
        let keyStore = FakeBridgeClientKeyStore(identity: try makeIdentity())
        let session = makeSession { request in
            try expectEqual(request.url?.path, "/v1/backup/create")
            try expectEqual(request.httpMethod, "POST")
            try expectEqual(request.value(forHTTPHeaderField: BridgeManagementAuth.clientIDHeader), "macbook")
            try expectTrue(
                request.value(forHTTPHeaderField: BridgeManagementAuth.signatureHeader)?.isEmpty == false
            )
            return .json(200, Self.backupEnvelope)
        }
        let transport = BridgeHTTPTransport(
            baseURL: URL(string: "http://bridge.local:8742")!,
            session: session,
            keyStore: keyStore,
            now: { Date(timeIntervalSince1970: 1000) },
            nonce: { "nonce-0001" }
        )

        let result = try await transport.createBackup(device: makeDevice())
        try expectEqual(result.backupID, "update-20260526-153000-v0.1.0")
        try expectTrue(result.verified)
        try expectEqual(result.archiveSHA256, "backup-archive-sha")
    }

    func testRestoreBackupPostsBackupID() async throws {
        let keyStore = FakeBridgeClientKeyStore(identity: try makeIdentity())
        let session = makeSession { request in
            try expectEqual(request.url?.path, "/v1/backup/restore")
            let body = try JSONDecoder().decode([String: String].self, from: requestBody(from: request))
            try expectEqual(body["backup_id"], "update-20260526-153000-v0.1.0")
            return .json(200, Self.restoreEnvelope)
        }
        let transport = BridgeHTTPTransport(
            baseURL: URL(string: "http://bridge.local:8742")!,
            session: session,
            keyStore: keyStore,
            now: { Date(timeIntervalSince1970: 1000) },
            nonce: { "nonce-0001" }
        )

        let result = try await transport.restoreBackup(
            device: makeDevice(),
            backupID: "update-20260526-153000-v0.1.0"
        )
        try expectEqual(result.backupID, "update-20260526-153000-v0.1.0")
        try expectEqual(result.restoredCount, 2)
    }

    func testMarkUpdateGoodDecodesDoneState() async throws {
        let keyStore = FakeBridgeClientKeyStore(identity: try makeIdentity())
        let session = makeSession { request in
            try expectEqual(request.url?.path, "/v1/update/mark-good")
            try expectEqual(request.httpMethod, "POST")
            try expectTrue(
                request.value(forHTTPHeaderField: BridgeManagementAuth.signatureHeader)?.isEmpty == false
            )
            return .json(200, Self.markGoodEnvelope)
        }
        let transport = BridgeHTTPTransport(
            baseURL: URL(string: "http://bridge.local:8742")!,
            session: session,
            keyStore: keyStore,
            now: { Date(timeIntervalSince1970: 1000) },
            nonce: { "nonce-0001" }
        )

        let state = try await transport.markUpdateGood(device: makeDevice())
        try expectEqual(state.phase, .done)
        try expectEqual(state.safeState, .installed)
        try expectEqual(state.installedVersion, "0.2.0")
        try expectTrue(state.isTerminal)
    }

    func testRollbackUpdatePostsReasonAndDecodesState() async throws {
        let keyStore = FakeBridgeClientKeyStore(identity: try makeIdentity())
        let session = makeSession { request in
            try expectEqual(request.url?.path, "/v1/update/rollback")
            let body = try JSONDecoder().decode([String: String].self, from: requestBody(from: request))
            try expectEqual(body["reason"], "health_check_failed")
            return .json(200, Self.rollbackEnvelope)
        }
        let transport = BridgeHTTPTransport(
            baseURL: URL(string: "http://bridge.local:8742")!,
            session: session,
            keyStore: keyStore,
            now: { Date(timeIntervalSince1970: 1000) },
            nonce: { "nonce-0001" }
        )

        let state = try await transport.rollbackUpdate(device: makeDevice(), reason: "health_check_failed")
        try expectEqual(state.phase, .rolledBack)
        try expectEqual(state.safeState, .previousVersionRestored)
        try expectTrue(state.isTerminal)
    }

    private static let uploadEnvelope = """
    {
      "schema_version": 1,
      "request_id": "req-upload",
      "ok": true,
      "upload": {
        "filename": "InstantLinkBridgeFirmware-v0.2.0-linux-aarch64.tar.gz",
        "stored_path": "/var/lib/InstantLinkBridge/shared/uploads/InstantLinkBridgeFirmware-v0.2.0-linux-aarch64.tar.gz",
        "size_bytes": 22,
        "sha256": "upload-sha"
      }
    }
    """

    private static let backupEnvelope = """
    {
      "schema_version": 1,
      "request_id": "req-backup",
      "ok": true,
      "backup": {
        "backup_id": "update-20260526-153000-v0.1.0",
        "manifest_path": "/var/lib/InstantLinkBridge/backups/update-20260526-153000-v0.1.0.manifest.json",
        "archive_path": "/var/lib/InstantLinkBridge/backups/update-20260526-153000-v0.1.0.tar.gz",
        "archive_sha256": "backup-archive-sha",
        "verified": true
      }
    }
    """

    private static let restoreEnvelope = """
    {
      "schema_version": 1,
      "request_id": "req-restore",
      "ok": true,
      "restore": {
        "backup_id": "update-20260526-153000-v0.1.0",
        "restored_paths": ["/etc/InstantLinkBridge/config.toml", "/etc/InstantLinkBridge/printer.json"],
        "restored_count": 2
      }
    }
    """

    private static let markGoodEnvelope = """
    {
      "schema_version": 1,
      "request_id": "req-mark-good",
      "ok": true,
      "update": {
        "operation_id": "0.2.0",
        "phase": "done",
        "progress": 1.0,
        "message": "Done",
        "safe_state": "installed",
        "installed_version": "0.2.0"
      }
    }
    """

    private static let rollbackEnvelope = """
    {
      "schema_version": 1,
      "request_id": "req-rollback",
      "ok": true,
      "update": {
        "operation_id": "0.2.0",
        "phase": "rolled_back",
        "progress": 1.0,
        "message": "Update failed; restored previous version",
        "safe_state": "previous_version_restored",
        "installed_version": "0.1.0"
      }
    }
    """

    private static let helloEnvelope = """
    {
      "schema_version": 1,
      "request_id": "req-hello",
      "ok": true,
      "device": {
        "device_id": "IB-1234ABCD",
        "display_name": "InstantLink Bridge",
        "software_version": "0.1.0",
        "api_version": "v1",
        "management_public_key_fingerprint": null,
        "pairing_open": false,
        "network_labels": ["Bridge Wi-Fi", "USB debug"],
        "endpoint_url": null,
        "is_paired": false
      },
      "management": {
        "service": "instantlink-bridge-manager",
        "auth_implemented": false,
        "admin_routes": "auth_required",
        "pairing_open": false,
        "public_key_fingerprint": null
      }
    }
    """

    private static let pairingStatusEnvelope = """
    {
      "schema_version": 1,
      "request_id": "req-pairing",
      "ok": true,
      "pairing": {
        "open": false,
        "auth_implemented": false,
        "confirmation_code_required": true,
        "expires_at": null,
        "expires_in_seconds": null
      }
    }
    """

    private static let pairingCompleteEnvelope = """
    {
      "schema_version": 1,
      "request_id": "req-pairing-complete",
      "ok": true,
      "pairing_completion": {
        "client_id": "macbook",
        "client_name": "Test Mac",
        "paired": true,
        "public_key_algorithm": "ed25519",
        "created_at": "2026-05-26T15:30:00Z",
        "message": "Paired"
      }
    }
    """

    private static let statusEnvelope = """
    {
      "schema_version": 1,
      "request_id": "req-status",
      "ok": true,
      "status": {
        "device_id": "IB-1234ABCD",
        "display_name": "InstantLink Bridge",
        "bridge_version": "0.1.0",
        "api_version": "v1",
        "readiness": "ready",
        "active_upload_mode": "bridge_wifi"
      }
    }
    """

    private static let updateEnvelope = """
    {
      "schema_version": 1,
      "request_id": "req-update",
      "ok": true,
      "update": {
        "operation_id": "update-1",
        "phase": "checking_bridge",
        "progress": 0.1,
        "message": "Checking Bridge",
        "safe_state": "update_not_installed"
      }
    }
    """

    private static func preflightEnvelope(allowed: Bool) -> String {
        """
        {
          "schema_version": 1,
          "request_id": "req-preflight",
          "ok": true,
          "preflight": {
            "package": {
              "package_kind": "instantlink_bridge_firmware",
              "version": "0.2.0",
              "target": "linux-aarch64",
              "archive_url": "file:///tmp/bridge.tar.gz",
              "archive_sha256": "archive-sha",
              "manifest_url": "file:///tmp/bridge.manifest.json",
              "manifest_sha256": "manifest-sha",
              "checksum_url": "file:///tmp/bridge.tar.gz.sha256"
            },
            "allowed": \(allowed ? "true" : "false"),
            "backup_required": true,
            "rollback_available": true,
            "checks": []
          }
        }
        """
    }

    private static func errorEnvelope(code: String) -> String {
        """
        {
          "schema_version": 1,
          "request_id": "req-error",
          "ok": false,
          "error_code": "\(code)",
          "error": {
            "message": "\(code)"
          }
        }
        """
    }
}

private final class FakeBridgeClientKeyStore: BridgeClientKeyStore {
    var identity: BridgeSigningIdentity?
    var createdIdentity: BridgeSigningIdentity?
    var savedIdentity: BridgeSigningIdentity?
    var savedBridgeID: String?
    var deletedBridgeID: String?

    init(
        identity: BridgeSigningIdentity? = nil,
        createdIdentity: BridgeSigningIdentity? = nil
    ) {
        self.identity = identity
        self.createdIdentity = createdIdentity
    }

    func loadIdentity(for bridgeID: String) throws -> BridgeSigningIdentity? {
        identity
    }

    func createIdentity(for bridgeID: String, clientName: String) throws -> BridgeSigningIdentity {
        if let createdIdentity {
            return createdIdentity
        }
        return try BridgeSigningIdentity.generate(bridgeID: bridgeID, clientName: clientName)
    }

    func saveIdentity(_ identity: BridgeSigningIdentity, for bridgeID: String) throws {
        self.identity = identity
        self.savedIdentity = identity
        self.savedBridgeID = bridgeID
    }

    func deleteIdentity(for bridgeID: String) throws {
        identity = nil
        deletedBridgeID = bridgeID
    }
}

private final class BridgeHTTPURLProtocol: URLProtocol {
    static var handler: ((URLRequest) throws -> BridgeHTTPURLProtocolResponse)?

    override class func canInit(with request: URLRequest) -> Bool {
        true
    }

    override class func canonicalRequest(for request: URLRequest) -> URLRequest {
        request
    }

    override func startLoading() {
        guard let handler = Self.handler else {
            client?.urlProtocol(self, didFailWithError: BridgeHTTPTransportError.invalidResponse)
            return
        }

        do {
            let result = try handler(request)
            let response = HTTPURLResponse(
                url: request.url!,
                statusCode: result.statusCode,
                httpVersion: "HTTP/1.1",
                headerFields: result.headers
            )!
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: result.body)
            client?.urlProtocolDidFinishLoading(self)
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }

    override func stopLoading() {}
}

private struct BridgeHTTPURLProtocolResponse {
    var statusCode: Int
    var body: Data
    var headers: [String: String]

    static func json(_ statusCode: Int, _ body: String) -> BridgeHTTPURLProtocolResponse {
        BridgeHTTPURLProtocolResponse(
            statusCode: statusCode,
            body: Data(body.utf8),
            headers: ["Content-Type": "application/json"]
        )
    }
}

private func makeSession(
    handler: @escaping (URLRequest) throws -> BridgeHTTPURLProtocolResponse
) -> URLSession {
    BridgeHTTPURLProtocol.handler = handler
    let configuration = URLSessionConfiguration.ephemeral
    configuration.protocolClasses = [BridgeHTTPURLProtocol.self]
    return URLSession(configuration: configuration)
}

private func requestBody(from request: URLRequest) -> Data {
    if let body = request.httpBody {
        return body
    }
    guard let stream = request.httpBodyStream else {
        return Data()
    }

    stream.open()
    defer { stream.close() }

    var data = Data()
    var buffer = [UInt8](repeating: 0, count: 4096)
    while stream.hasBytesAvailable {
        let count = stream.read(&buffer, maxLength: buffer.count)
        if count > 0 {
            data.append(buffer, count: count)
        } else {
            break
        }
    }
    return data
}

private func makeIdentity() throws -> BridgeSigningIdentity {
    var identity = try BridgeSigningIdentity.generate(
        bridgeID: "IB-1234ABCD",
        clientName: "Test Mac"
    )
    identity.clientID = "macbook"
    return identity
}

private func makeDevice() -> BridgeDevice {
    BridgeDevice(
        deviceID: "IB-1234ABCD",
        displayName: "InstantLink Bridge",
        softwareVersion: "0.1.0",
        apiVersion: "v1",
        managementPublicKeyFingerprint: "SHA256:test",
        pairingOpen: false,
        networkLabels: ["Bridge Wi-Fi", "USB debug"],
        endpointURL: URL(string: "http://bridge.local:8742"),
        isPaired: true
    )
}

private func makePackage(version: String) -> BridgeUpdatePackage {
    BridgeUpdatePackage(
        version: version,
        target: "linux-aarch64",
        archiveURL: URL(fileURLWithPath: "/tmp/bridge.tar.gz"),
        archiveSHA256: "archive-sha",
        manifestURL: URL(fileURLWithPath: "/tmp/bridge.manifest.json"),
        manifestSHA256: "manifest-sha",
        checksumURL: URL(fileURLWithPath: "/tmp/bridge.tar.gz.sha256")
    )
}
