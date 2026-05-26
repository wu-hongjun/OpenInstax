import Foundation

struct BridgeFirmwarePackage: Equatable {
    let version: String
    let target: String
    let archiveURL: URL
    let archiveSHA256: String
    let manifestURL: URL
    let manifestSHA256: String
    let checksumURL: URL
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
              let checksumName = json["checksum_name"] as? String else {
            return nil
        }

        let archiveURL = directory.appendingPathComponent(archiveName)
        let packageManifestURL = directory.appendingPathComponent(packageManifestName)
        let checksumURL = directory.appendingPathComponent(checksumName)

        guard FileManager.default.fileExists(atPath: archiveURL.path),
              FileManager.default.fileExists(atPath: packageManifestURL.path),
              FileManager.default.fileExists(atPath: checksumURL.path) else {
            return nil
        }

        return BridgeFirmwarePackage(
            version: version,
            target: target,
            archiveURL: archiveURL,
            archiveSHA256: archiveSHA256,
            manifestURL: packageManifestURL,
            manifestSHA256: packageManifestSHA256,
            checksumURL: checksumURL
        )
    }
}
