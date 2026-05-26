import Foundation

final class BridgeTransportTests {
    func testInMemoryTransportThrowsAuthRequiredForManagedEndpoints() async throws {
        let device = makeDevice()
        let transport = InMemoryBridgeTransport(
            devices: [device],
            statuses: [device.deviceID: makeStatus()],
            authRequiredDeviceIDs: [device.deviceID]
        )

        let discovered = try await transport.discover()
        try expectEqual(discovered, [device])

        do {
            _ = try await transport.status(device: device)
            throw MacTestFailure(
                file: #filePath,
                line: #line,
                message: "Expected managed status endpoint to require auth"
            )
        } catch let error as BridgeAPIError {
            try expectEqual(error.code, .authRequired)
            try expectEqual(error.payload.details["device_id"], .string(device.deviceID))
        }
    }

    func testInMemoryTransportUpdateFlowAdvancesToDone() async throws {
        let device = makeDevice()
        let package = makePackage(version: "0.2.0")
        let transport = InMemoryBridgeTransport(
            devices: [device],
            statuses: [device.deviceID: makeStatus()]
        )

        let preflight = try await transport.preflightUpdate(device: device, package: package)
        try expectTrue(preflight.allowed)
        try expectEqual(preflight.package.version, "0.2.0")

        var state = try await transport.startUpdate(device: device, package: package)
        var phases = [state.phase]

        while !state.isTerminal {
            state = try await transport.updateStatus(device: device, operationID: state.operationID)
            phases.append(state.phase)
        }

        try expectEqual(state.phase, .done)
        try expectEqual(state.installedVersion, "0.2.0")
        try expectTrue(phases.contains(.backingUpSettings))
        try expectTrue(phases.contains(.uploadingUpdate))
        try expectTrue(phases.contains(.installingUpdate))
        try expectTrue(phases.contains(.verifyingBridge))

        let updatedStatus = try await transport.status(device: device)
        try expectEqual(updatedStatus.bridgeVersion, "0.2.0")
        try expectEqual(updatedStatus.readiness, .ready)
        try expectEqual(updatedStatus.update?.canUpdate, false)
    }

    private func makeDevice() -> BridgeDevice {
        BridgeDevice(
            deviceID: "IB-12345678",
            displayName: "InstantLink Bridge IB-12345678",
            softwareVersion: "0.1.0",
            apiVersion: "1",
            managementPublicKeyFingerprint: "SHA256:abc123",
            pairingOpen: false,
            networkLabels: ["Bridge Wi-Fi", "USB debug"],
            endpointURL: URL(string: "https://192.168.8.1"),
            isPaired: true
        )
    }

    private func makeStatus(version: String = "0.1.0") -> BridgeStatus {
        BridgeStatus(
            deviceID: "IB-12345678",
            displayName: "InstantLink Bridge IB-12345678",
            bridgeVersion: version,
            apiVersion: "1",
            readiness: .ready,
            activeUploadMode: .bridgeWiFi,
            uptimeSeconds: 10,
            network: BridgeNetworkStatus(
                mode: .bridgeWiFi,
                label: "Bridge Wi-Fi",
                address: "192.168.8.1",
                connected: true
            ),
            printer: nil,
            update: nil,
            lastUpload: nil,
            lastError: nil
        )
    }

    private func makePackage(version: String) -> BridgeUpdatePackage {
        BridgeUpdatePackage(
            version: version,
            target: "linux-aarch64",
            archiveURL: URL(fileURLWithPath: "/tmp/InstantLinkBridgeFirmware-v\(version)-linux-aarch64.tar.gz"),
            archiveSHA256: "archive-sha",
            manifestURL: URL(fileURLWithPath: "/tmp/InstantLinkBridgeFirmware-v\(version)-linux-aarch64.manifest.json"),
            manifestSHA256: "manifest-sha",
            checksumURL: URL(fileURLWithPath: "/tmp/InstantLinkBridgeFirmware-v\(version)-linux-aarch64.tar.gz.sha256")
        )
    }
}
