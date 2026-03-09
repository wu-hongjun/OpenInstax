import AppKit
import SwiftUI

enum AppRelauncher {
    static func relaunchCurrentApp() {
        let appPath = Bundle.main.bundlePath
        let escapedAppPath = appPath.replacingOccurrences(of: "'", with: "'\\''")
        let tokenPath = (NSTemporaryDirectory() as NSString).appendingPathComponent("instantlink-relaunch-token")
        let escapedTokenPath = tokenPath.replacingOccurrences(of: "'", with: "'\\''")
        let token = UUID().uuidString

        try? token.write(toFile: tokenPath, atomically: true, encoding: .utf8)

        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/bin/sh")
        task.arguments = [
            "-c",
            "sleep 0.6; current=$(cat '\(escapedTokenPath)' 2>/dev/null || true); if [ \"$current\" = '\(token)' ]; then rm -f '\(escapedTokenPath)'; /usr/bin/open '\(escapedAppPath)'; fi"
        ]
        task.standardOutput = FileHandle.nullDevice
        task.standardError = FileHandle.nullDevice
        guard (try? task.run()) != nil else { return }

        NSRunningApplication.current.terminate()

        DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) {
            if !NSRunningApplication.current.isTerminated {
                NSRunningApplication.current.forceTerminate()
            }
        }
    }
}

@main
struct InstantLinkApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var appDelegate
    @StateObject private var viewModel = ViewModel()

    var body: some Scene {
        WindowGroup {
            MainView()
                .environmentObject(viewModel)
        }
    }
}

class AppDelegate: NSObject, NSApplicationDelegate {
    var statusItem: NSStatusItem?

    func applicationDidFinishLaunching(_ notification: Notification) {
        ViewModel.registerBundledFonts()
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        if let button = statusItem?.button {
            button.image = NSImage(systemSymbolName: "printer.fill", accessibilityDescription: L("InstantLink"))
        }

        let menu = NSMenu()
        menu.addItem(NSMenuItem(title: L("Show Window"), action: #selector(showWindow), keyEquivalent: ""))
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: L("Find Printer"), action: #selector(findPrinter), keyEquivalent: ""))
        menu.addItem(NSMenuItem(title: L("Refresh Status"), action: #selector(refreshStatus), keyEquivalent: ""))
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: L("Settings"), action: #selector(openSettings), keyEquivalent: ","))
        menu.addItem(NSMenuItem(title: L("Check for Updates"), action: #selector(checkForUpdates), keyEquivalent: ""))
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: L("Quit"), action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
        statusItem?.menu = menu
    }

    @objc func showWindow() {
        NSApplication.shared.activate(ignoringOtherApps: true)
        for window in NSApplication.shared.windows {
            if window.canBecomeMain {
                window.makeKeyAndOrderFront(nil)
            }
        }
    }

    @objc func findPrinter() {
        NotificationCenter.default.post(name: .findPrinter, object: nil)
    }

    @objc func refreshStatus() {
        NotificationCenter.default.post(name: .refreshStatus, object: nil)
    }

    @objc func openSettings() {
        NotificationCenter.default.post(name: .openSettings, object: nil)
    }

    @objc func checkForUpdates() {
        NotificationCenter.default.post(name: .checkForUpdates, object: nil)
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if !flag { showWindow() }
        return true
    }
}

extension Notification.Name {
    static let findPrinter = Notification.Name("findPrinter")
    static let refreshStatus = Notification.Name("refreshStatus")
    static let openSettings = Notification.Name("openSettings")
    static let checkForUpdates = Notification.Name("checkForUpdates")
}

enum CaptureMode { case file, camera }
enum CameraState { case viewfinder, preview }
