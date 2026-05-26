import Foundation

final class BridgeFirmwareBundleTests {
    func testPackageLoadsBundledFirmwareMetadata() throws {
        let directory = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }

        let archiveURL = directory.appendingPathComponent("InstantLinkBridgeFirmware-v0.1.0-linux-aarch64.tar.gz")
        let manifestURL = directory.appendingPathComponent("InstantLinkBridgeFirmware-v0.1.0-linux-aarch64.manifest.json")
        let checksumURL = directory.appendingPathComponent("InstantLinkBridgeFirmware-v0.1.0-linux-aarch64.tar.gz.sha256")

        try Data("archive".utf8).write(to: archiveURL)
        try Data("{}".utf8).write(to: manifestURL)
        try Data("abc  archive\n".utf8).write(to: checksumURL)
        try Data(
            """
            {
              "schema_version": 1,
              "package_kind": "instantlink_bridge_firmware",
              "bridge_version": "0.1.0",
              "target": "linux-aarch64",
              "archive_name": "\(archiveURL.lastPathComponent)",
              "archive_sha256": "archive-sha",
              "manifest_name": "\(manifestURL.lastPathComponent)",
              "manifest_sha256": "manifest-sha",
              "checksum_name": "\(checksumURL.lastPathComponent)",
              "checksum_sha256": "checksum-sha"
            }
            """.utf8
        ).write(to: directory.appendingPathComponent("latest.json"))

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
        try expectEqual(package.archiveSHA256, "archive-sha")
        try expectEqual(package.manifestURL.lastPathComponent, manifestURL.lastPathComponent)
        try expectEqual(package.manifestSHA256, "manifest-sha")
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

    private func makeTemporaryDirectory() throws -> URL {
        let directory = URL(fileURLWithPath: NSTemporaryDirectory())
            .appendingPathComponent("InstantLinkBridgeFirmwareTests-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false)
        return directory
    }
}
