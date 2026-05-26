import Foundation
import CryptoKit

final class BridgeFirmwareBundleTests {
    func testPackageLoadsBundledFirmwareMetadata() throws {
        let directory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }

        let archiveURL = directory.appendingPathComponent("InstantLinkBridgeFirmware-v0.1.0-linux-aarch64.tar.gz")
        let manifestURL = directory.appendingPathComponent("InstantLinkBridgeFirmware-v0.1.0-linux-aarch64.manifest.json")
        let manifestSignatureURL = directory.appendingPathComponent("InstantLinkBridgeFirmware-v0.1.0-linux-aarch64.manifest.sig")
        let checksumURL = directory.appendingPathComponent("InstantLinkBridgeFirmware-v0.1.0-linux-aarch64.tar.gz.sha256")

        try Data("archive".utf8).write(to: archiveURL)
        try Data("{\"schema_version\":1}\n".utf8).write(to: manifestURL)
        try Data("{\"signature\":\"manifest\"}\n".utf8).write(to: manifestSignatureURL)
        try Data("abc  archive\n".utf8).write(to: checksumURL)
        let archiveSHA = try sha256Hex(archiveURL)
        let manifestSHA = try sha256Hex(manifestURL)
        let checksumSHA = try sha256Hex(checksumURL)
        try Data(
            """
            {
              "schema_version": 1,
              "package_kind": "instantlink_bridge_firmware",
              "bridge_version": "0.1.0",
              "target": "linux-aarch64",
              "archive_name": "\(archiveURL.lastPathComponent)",
              "archive_sha256": "\(archiveSHA)",
              "manifest_name": "\(manifestURL.lastPathComponent)",
              "manifest_sha256": "\(manifestSHA)",
              "checksum_name": "\(checksumURL.lastPathComponent)",
              "checksum_sha256": "\(checksumSHA)"
            }
            """.utf8
        ).write(to: directory.appendingPathComponent("latest.json"))
        try Data("{\"signature\":\"latest\"}\n".utf8)
            .write(to: directory.appendingPathComponent("latest.json.sig"))

        guard let package = BridgeFirmwareBundleService.package(in: directory) else {
            throw MacTestFailure(
                file: #filePath,
                line: #line,
                message: "Expected bundled firmware package metadata to load"
            )
        }

        try expectEqual(package.version, "0.1.0")
        try expectEqual(package.target, "linux-aarch64")
        try expectEqual(package.archiveURL.lastPathComponent, archiveURL.lastPathComponent)
        try expectEqual(package.archiveSHA256, archiveSHA)
        try expectEqual(package.manifestURL.lastPathComponent, manifestURL.lastPathComponent)
        try expectEqual(package.manifestSHA256, manifestSHA)
        try expectEqual(package.manifestSignatureURL.lastPathComponent, manifestSignatureURL.lastPathComponent)
        try expectEqual(package.checksumURL.lastPathComponent, checksumURL.lastPathComponent)
    }

    func testPackageReturnsNilWhenSidecarsAreMissing() throws {
        let directory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }

        try Data(
            """
            {
              "bridge_version": "0.1.0",
              "target": "linux-aarch64",
              "archive_name": "missing.tar.gz",
              "archive_sha256": "archive-sha",
              "manifest_name": "missing.manifest.json",
              "manifest_sha256": "manifest-sha",
              "checksum_name": "missing.tar.gz.sha256"
            }
            """.utf8
        ).write(to: directory.appendingPathComponent("latest.json"))

        try expectNil(BridgeFirmwareBundleService.package(in: directory))
    }

    func testPackageMapsToBridgeUpdatePackage() throws {
        let directory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }

        let archiveURL = directory.appendingPathComponent("InstantLinkBridgeFirmware-v0.2.0-linux-aarch64.tar.gz")
        let manifestURL = directory.appendingPathComponent("InstantLinkBridgeFirmware-v0.2.0-linux-aarch64.manifest.json")
        let manifestSignatureURL = directory.appendingPathComponent("InstantLinkBridgeFirmware-v0.2.0-linux-aarch64.manifest.sig")
        let checksumURL = directory.appendingPathComponent("InstantLinkBridgeFirmware-v0.2.0-linux-aarch64.tar.gz.sha256")

        try Data("archive".utf8).write(to: archiveURL)
        try Data("{\"schema_version\":1}\n".utf8).write(to: manifestURL)
        try Data("{\"signature\":\"manifest\"}\n".utf8).write(to: manifestSignatureURL)
        try Data("abc  archive\n".utf8).write(to: checksumURL)
        let archiveSHA = try sha256Hex(archiveURL)
        let manifestSHA = try sha256Hex(manifestURL)
        let checksumSHA = try sha256Hex(checksumURL)
        try Data(
            """
            {
              "schema_version": 1,
              "package_kind": "instantlink_bridge_firmware",
              "bridge_version": "0.2.0",
              "target": "linux-aarch64",
              "archive_name": "\(archiveURL.lastPathComponent)",
              "archive_sha256": "\(archiveSHA)",
              "manifest_name": "\(manifestURL.lastPathComponent)",
              "manifest_sha256": "\(manifestSHA)",
              "checksum_name": "\(checksumURL.lastPathComponent)",
              "checksum_sha256": "\(checksumSHA)"
            }
            """.utf8
        ).write(to: directory.appendingPathComponent("latest.json"))
        try Data("{\"signature\":\"latest\"}\n".utf8)
            .write(to: directory.appendingPathComponent("latest.json.sig"))

        guard let firmwarePackage = BridgeFirmwareBundleService.package(in: directory) else {
            throw MacTestFailure(
                file: #filePath,
                line: #line,
                message: "Expected bundled firmware package metadata to load"
            )
        }

        let updatePackage = firmwarePackage.updatePackage

        try expectEqual(updatePackage.packageKind, "instantlink_bridge_firmware")
        try expectEqual(updatePackage.version, "0.2.0")
        try expectEqual(updatePackage.target, "linux-aarch64")
        try expectEqual(updatePackage.archiveURL, archiveURL)
        try expectEqual(updatePackage.archiveSHA256, archiveSHA)
        try expectEqual(updatePackage.manifestURL, manifestURL)
        try expectEqual(updatePackage.manifestSHA256, manifestSHA)
        try expectEqual(updatePackage.checksumURL, checksumURL)
        try expectEqual(updatePackage.manifestSignatureURL, manifestSignatureURL)
    }

    private func makeTemporaryDirectory() throws -> URL {
        let directory = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("InstantLinkBridgeFirmwareTests-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false)
        return directory
    }

    private func sha256Hex(_ url: URL) throws -> String {
        let data = try Data(contentsOf: url)
        return SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }
}
