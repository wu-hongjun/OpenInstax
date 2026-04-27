import AppKit
import CoreText
import CryptoKit
import Foundation

enum BundledFontRegistrar {
    static func register() {
        guard let resourcePath = Bundle.main.resourcePath else { return }
        let fontsDir = (resourcePath as NSString).appendingPathComponent("Fonts")
        guard let fontFiles = try? FileManager.default.contentsOfDirectory(atPath: fontsDir) else { return }

        for file in fontFiles where file.hasSuffix(".ttf") {
            let fontURL = URL(fileURLWithPath: (fontsDir as NSString).appendingPathComponent(file)) as CFURL
            CTFontManagerRegisterFontsForURL(fontURL, .process, nil)
        }
    }
}

enum AppAppearanceService {
    static func apply(_ appearance: AppAppearance) {
        NSApp.appearance = appearance.nsAppearance
    }
}

enum AppVersionService {
    static var currentAppVersion: String {
        Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "0.0.0"
    }

    static func loadBundledCoreVersion() async -> String {
        let bundle = Bundle.main
        let cliPath = bundle.path(forAuxiliaryExecutable: "instantlink-cli")
            ?? (bundle.executableURL?.deletingLastPathComponent().path ?? "") + "/instantlink-cli"

        return await withCheckedContinuation { continuation in
            DispatchQueue.global(qos: .utility).async {
                let process = Process()
                process.executableURL = URL(fileURLWithPath: cliPath)
                process.arguments = ["--version"]
                let pipe = Pipe()
                process.standardOutput = pipe
                process.standardError = FileHandle.nullDevice

                do {
                    try process.run()
                    let data = pipe.fileHandleForReading.readDataToEndOfFile()
                    process.waitUntilExit()

                    guard process.terminationStatus == 0 else {
                        continuation.resume(returning: "?")
                        return
                    }

                    let output = String(data: data, encoding: .utf8)?
                        .trimmingCharacters(in: .whitespacesAndNewlines)

                    if let output, !output.isEmpty {
                        continuation.resume(returning: output.replacingOccurrences(of: "instantlink ", with: "v"))
                    } else {
                        continuation.resume(returning: "?")
                    }
                } catch {
                    continuation.resume(returning: "?")
                }
            }
        }
    }
}

struct AppUpdateInfo {
    let version: String
    let downloadURL: String?
    let checksumURL: String?
}

enum AppUpdateService {
    private static func versionComponents(from version: String) -> [Int]? {
        let normalized = version
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .replacingOccurrences(of: "instantlink ", with: "")
        let bareVersion = normalized.hasPrefix("v") ? String(normalized.dropFirst()) : normalized
        let coreVersion = bareVersion.split(separator: "-", maxSplits: 1, omittingEmptySubsequences: true).first
            .map(String.init) ?? bareVersion
        let parts = coreVersion.split(separator: ".")

        guard !parts.isEmpty, parts.allSatisfy({ Int($0) != nil }) else {
            return nil
        }

        return parts.map(String.init).compactMap(Int.init)
    }

    static func compareVersions(_ a: String, _ b: String) -> Int? {
        guard let partsA = versionComponents(from: a),
              let partsB = versionComponents(from: b) else {
            return nil
        }
        let count = max(partsA.count, partsB.count)

        for index in 0..<count {
            let lhs = index < partsA.count ? partsA[index] : 0
            let rhs = index < partsB.count ? partsB[index] : 0
            if lhs < rhs { return -1 }
            if lhs > rhs { return 1 }
        }

        return 0
    }

    static func checkForUpdates(
        currentAppVersion: String,
        currentCoreVersion: String
    ) async -> AppUpdateInfo? {
        guard let url = URL(string: "https://api.github.com/repos/wu-hongjun/InstantLink/releases/latest") else {
            return nil
        }

        var request = URLRequest(url: url)
        request.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
        request.timeoutInterval = 10

        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            guard let httpResponse = response as? HTTPURLResponse, httpResponse.statusCode == 200 else {
                return nil
            }

            guard let json = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let tagName = json["tag_name"] as? String,
                  let assets = json["assets"] as? [[String: Any]] else {
                return nil
            }

            let remoteVersion = tagName.hasPrefix("v") ? String(tagName.dropFirst()) : tagName
            let appBehind = compareVersions(currentAppVersion, remoteVersion).map { $0 < 0 } ?? false
            let coreBehind = compareVersions(currentCoreVersion, remoteVersion).map { $0 < 0 } ?? false
            guard appBehind || coreBehind else { return nil }

            let dmgAsset = assets.first { asset in
                guard let name = asset["name"] as? String else { return false }
                return name.hasSuffix(".dmg")
            }

            let dmgName = dmgAsset?["name"] as? String
            let checksumAsset = dmgName.flatMap { name in
                assets.first { asset in
                    (asset["name"] as? String) == "\(name).sha256"
                }
            }

            return AppUpdateInfo(
                version: remoteVersion,
                downloadURL: dmgAsset?["browser_download_url"] as? String,
                checksumURL: checksumAsset?["browser_download_url"] as? String
            )
        } catch {
            return nil
        }
    }

    static func installUpdate(
        from downloadURL: String,
        checksumURL: String?,
        onProgress: @escaping @MainActor (Double) -> Void,
        onFailure: @escaping @MainActor (String) -> Void
    ) {
        guard let url = URL(string: downloadURL) else {
            reportFailure(L("update_error_invalid_url"), onFailure: onFailure)
            return
        }

        let delegate = AppUpdateDownloadDelegate { progress in
            Task { @MainActor in
                onProgress(progress)
            }
        }

        let session = URLSession(configuration: .default, delegate: delegate, delegateQueue: nil)
        let task = session.downloadTask(with: url) { tempURL, _, error in
            if let error {
                reportFailure(error.localizedDescription, onFailure: onFailure)
                return
            }

            guard let tempURL else {
                reportFailure(L("update_error_download_failed"), onFailure: onFailure)
                return
            }

            let dmgPath = NSTemporaryDirectory() + "InstantLink-update.dmg"
            try? FileManager.default.removeItem(atPath: dmgPath)

            do {
                try FileManager.default.copyItem(at: tempURL, to: URL(fileURLWithPath: dmgPath))
            } catch {
                reportFailure(error.localizedDescription, onFailure: onFailure)
                return
            }

            verifyAndInstall(
                dmgPath: dmgPath,
                checksumURL: checksumURL,
                onProgress: onProgress,
                onFailure: onFailure
            )
        }
        task.resume()
    }

    private static func verifyAndInstall(
        dmgPath: String,
        checksumURL: String?,
        onProgress: @escaping @MainActor (Double) -> Void,
        onFailure: @escaping @MainActor (String) -> Void
    ) {
        guard let checksumURLString = checksumURL,
              let checksumURL = URL(string: checksumURLString) else {
            try? FileManager.default.removeItem(atPath: dmgPath)
            reportFailure(L("update_error_checksum_missing"), onFailure: onFailure)
            return
        }

        var request = URLRequest(url: checksumURL)
        request.timeoutInterval = 10

        let checksumTask = URLSession.shared.dataTask(with: request) { data, response, error in
            if let error {
                try? FileManager.default.removeItem(atPath: dmgPath)
                reportFailure(error.localizedDescription, onFailure: onFailure)
                return
            }

            guard let data,
                  let httpResponse = response as? HTTPURLResponse,
                  httpResponse.statusCode == 200,
                  let checksumLine = String(data: data, encoding: .utf8)?
                      .trimmingCharacters(in: .whitespacesAndNewlines),
                  !checksumLine.isEmpty else {
                try? FileManager.default.removeItem(atPath: dmgPath)
                reportFailure(L("update_error_checksum_missing"), onFailure: onFailure)
                return
            }

            // shasum -a 256 output: "<hex>  <filename>" — take the first token
            let expectedHex = checksumLine
                .split(separator: " ", maxSplits: 1, omittingEmptySubsequences: true)
                .first
                .map(String.init) ?? checksumLine

            guard let dmgData = try? Data(contentsOf: URL(fileURLWithPath: dmgPath)) else {
                try? FileManager.default.removeItem(atPath: dmgPath)
                reportFailure(L("update_error_download_failed"), onFailure: onFailure)
                return
            }

            let actualHex = SHA256.hexDigest(of: dmgData)

            guard actualHex == expectedHex else {
                try? FileManager.default.removeItem(atPath: dmgPath)
                reportFailure(L("update_error_checksum_mismatch"), onFailure: onFailure)
                return
            }

            installDownloadedApp(fromDMGAt: dmgPath, onProgress: onProgress, onFailure: onFailure)
        }
        checksumTask.resume()
    }

    private static func installDownloadedApp(
        fromDMGAt dmgPath: String,
        onProgress: @escaping @MainActor (Double) -> Void,
        onFailure: @escaping @MainActor (String) -> Void
    ) {
        Task { @MainActor in
            onProgress(1.0)
        }

        DispatchQueue.global(qos: .userInitiated).async {
            let mountProcess = Process()
            mountProcess.executableURL = URL(fileURLWithPath: "/usr/bin/hdiutil")
            mountProcess.arguments = ["attach", "-nobrowse", "-readonly", "-plist", dmgPath]
            let mountPipe = Pipe()
            mountProcess.standardOutput = mountPipe
            mountProcess.standardError = FileHandle.nullDevice

            do {
                try mountProcess.run()
                mountProcess.waitUntilExit()
            } catch {
                reportFailure("Failed to mount DMG: \(error.localizedDescription)", onFailure: onFailure)
                return
            }

            guard mountProcess.terminationStatus == 0 else {
                reportFailure("Failed to mount DMG", onFailure: onFailure)
                return
            }

            let mountData = mountPipe.fileHandleForReading.readDataToEndOfFile()
            guard let plist = try? PropertyListSerialization.propertyList(from: mountData, format: nil) as? [String: Any],
                  let entities = plist["system-entities"] as? [[String: Any]],
                  let mountPoint = entities.compactMap({ $0["mount-point"] as? String }).first else {
                reportFailure("Could not determine mount point", onFailure: onFailure)
                return
            }

            defer {
                let detach = Process()
                detach.executableURL = URL(fileURLWithPath: "/usr/bin/hdiutil")
                detach.arguments = ["detach", mountPoint, "-quiet"]
                try? detach.run()
                detach.waitUntilExit()
                try? FileManager.default.removeItem(atPath: dmgPath)
            }

            guard let contents = try? FileManager.default.contentsOfDirectory(atPath: mountPoint),
                  let appName = contents.first(where: { $0.hasSuffix(".app") }) else {
                reportFailure("No .app found in DMG", onFailure: onFailure)
                return
            }

            let sourceApp = (mountPoint as NSString).appendingPathComponent(appName)
            let tempApp = NSTemporaryDirectory() + "InstantLink-update.app"
            let currentApp = Bundle.main.bundlePath

            try? FileManager.default.removeItem(atPath: tempApp)
            do {
                try FileManager.default.copyItem(atPath: sourceApp, toPath: tempApp)
            } catch {
                reportFailure("Failed to copy app: \(error.localizedDescription)", onFailure: onFailure)
                return
            }

            let oldApp = currentApp + ".old"
            try? FileManager.default.removeItem(atPath: oldApp)

            do {
                try FileManager.default.moveItem(atPath: currentApp, toPath: oldApp)
                try FileManager.default.moveItem(atPath: tempApp, toPath: currentApp)
                try? FileManager.default.removeItem(atPath: oldApp)
            } catch {
                try? FileManager.default.moveItem(atPath: oldApp, toPath: currentApp)
                reportFailure("Failed to install update: \(error.localizedDescription)", onFailure: onFailure)
                return
            }

            Task { @MainActor in
                AppRelauncher.relaunchCurrentApp()
            }
        }
    }

    private static func reportFailure(
        _ message: String,
        onFailure: @escaping @MainActor (String) -> Void
    ) {
        Task { @MainActor in
            onFailure(message)
        }
    }
}

private enum SHA256 {
    static func hexDigest(of data: Data) -> String {
        let digest = CryptoKit.SHA256.hash(data: data)
        return digest.map { String(format: "%02x", $0) }.joined()
    }
}

private final class AppUpdateDownloadDelegate: NSObject, URLSessionDownloadDelegate {
    let onProgress: (Double) -> Void

    init(onProgress: @escaping (Double) -> Void) {
        self.onProgress = onProgress
    }

    func urlSession(
        _ session: URLSession,
        downloadTask: URLSessionDownloadTask,
        didWriteData bytesWritten: Int64,
        totalBytesWritten: Int64,
        totalBytesExpectedToWrite: Int64
    ) {
        guard totalBytesExpectedToWrite > 0 else { return }
        onProgress(Double(totalBytesWritten) / Double(totalBytesExpectedToWrite))
    }

    func urlSession(
        _ session: URLSession,
        downloadTask: URLSessionDownloadTask,
        didFinishDownloadingTo location: URL
    ) {
        // Handled in the completion handler of downloadTask.
    }
}
