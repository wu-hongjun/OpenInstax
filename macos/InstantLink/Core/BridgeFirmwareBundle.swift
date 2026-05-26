import Foundation
import CryptoKit

struct BridgeFirmwarePackage: Equatable {
    let version: String
    let target: String
    let latestURL: URL
    let latestSignatureURL: URL
    let archiveURL: URL
    let archiveSHA256: String
    let manifestURL: URL
    let manifestSHA256: String
    let manifestSignatureURL: URL
    let checksumURL: URL
    let checksumSHA256: String
}

enum BridgeFirmwareBundleService {
    static let resourceDirectoryName = "BridgeFirmware"

    static func bundledPackage(bundle: Bundle = .main) -> BridgeFirmwarePackage? {
        guard let resourceURL = bundle.resourceURL else { return nil }
        return package(in: resourceURL.appendingPathComponent(resourceDirectoryName, isDirectory: true))
    }

    static func package(in directory: URL) -> BridgeFirmwarePackage? {
        let manifestURL = directory.appendingPathComponent("latest.json")
        guard let data = try? Data(contentsOf: manifestURL),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let version = json["bridge_version"] as? String,
              let target = json["target"] as? String,
              let archiveName = json["archive_name"] as? String,
              let archiveSHA256 = json["archive_sha256"] as? String,
              let packageManifestName = json["manifest_name"] as? String,
              let packageManifestSHA256 = json["manifest_sha256"] as? String,
              let checksumName = json["checksum_name"] as? String,
              let checksumSHA256 = json["checksum_sha256"] as? String,
              json["schema_version"] as? Int == 1,
              json["package_kind"] as? String == "instantlink_bridge_firmware",
              isSafeArtifactName(archiveName),
              isSafeArtifactName(packageManifestName),
              isSafeArtifactName(checksumName) else {
            return nil
        }

        let archiveURL = directory.appendingPathComponent(archiveName)
        let packageManifestURL = directory.appendingPathComponent(packageManifestName)
        let packageManifestSignatureURL = packageManifestURL
            .deletingPathExtension()
            .deletingPathExtension()
            .appendingPathExtension("manifest.sig")
        let checksumURL = directory.appendingPathComponent(checksumName)
        let latestSignatureURL = manifestURL.appendingPathExtension("sig")

        guard FileManager.default.fileExists(atPath: archiveURL.path),
              FileManager.default.fileExists(atPath: packageManifestURL.path),
              FileManager.default.fileExists(atPath: packageManifestSignatureURL.path),
              FileManager.default.fileExists(atPath: checksumURL.path),
              FileManager.default.fileExists(atPath: latestSignatureURL.path),
              sha256Hex(archiveURL) == archiveSHA256,
              sha256Hex(packageManifestURL) == packageManifestSHA256,
              sha256Hex(checksumURL) == checksumSHA256 else {
            return nil
        }

        return BridgeFirmwarePackage(
            version: version,
            target: target,
            latestURL: manifestURL,
            latestSignatureURL: latestSignatureURL,
            archiveURL: archiveURL,
            archiveSHA256: archiveSHA256,
            manifestURL: packageManifestURL,
            manifestSHA256: packageManifestSHA256,
            manifestSignatureURL: packageManifestSignatureURL,
            checksumURL: checksumURL,
            checksumSHA256: checksumSHA256
        )
    }

    private static func isSafeArtifactName(_ value: String) -> Bool {
        !value.isEmpty && !value.contains("/") && !value.contains("\\") && value != "." && value != ".."
    }

    private static func sha256Hex(_ url: URL) -> String? {
        guard let data = try? Data(contentsOf: url) else { return nil }
        return SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }
}
