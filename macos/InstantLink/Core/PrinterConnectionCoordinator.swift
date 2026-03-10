import Combine
import Foundation

enum PrinterPairingPhase: Equatable {
    case idle
    case scanning
    case connecting
}

struct PrinterConnectionFFIStatus: Equatable {
    var battery: Int
    var filmRemaining: Int
    var isCharging: Bool
    var printCount: Int
}

protocol PrinterConnectionFFIBoundary: AnyObject {
    func scanPrinters(duration: Int) async -> [String]
    func connectNamedPrinter(_ device: String, duration: Int) async -> Bool
    func disconnectPrinter() async
    func isPrinterConnected() -> Bool
    func fetchConnectionStatus() async -> PrinterConnectionFFIStatus?
    func fetchConnectedPrinterModel() -> String?
}

extension InstantLinkFFI: PrinterConnectionFFIBoundary {
    func scanPrinters(duration: Int) async -> [String] {
        await scan(duration: duration)
    }

    func connectNamedPrinter(_ device: String, duration: Int) async -> Bool {
        await connect(device: device, duration: duration)
    }

    func disconnectPrinter() async {
        await disconnect()
    }

    func isPrinterConnected() -> Bool {
        isConnected()
    }

    func fetchConnectionStatus() async -> PrinterConnectionFFIStatus? {
        guard let status = await status() else { return nil }
        return PrinterConnectionFFIStatus(
            battery: status.battery,
            filmRemaining: status.film,
            isCharging: status.isCharging,
            printCount: status.printCount
        )
    }

    func fetchConnectedPrinterModel() -> String? {
        deviceModel()
    }
}

struct PrinterConnectionSnapshot: Equatable {
    var isConnected = false
    var printerName: String?
    var printerModel: String?
    var battery = 0
    var isCharging = false
    var filmRemaining = 0
    var printCount = 0

    var availablePrinters: [String] = []
    var selectedPrinter: String?
    var nearbyPrinters: [String] = []

    var isSearching = false
    var isRefreshing = false
    var isScanning = false
    var isPairing = false
    var pairingPhase: PrinterPairingPhase = .idle
    var pairingAttempt = 0
    var pairingStatus: String?
    var hasSearchedOnce = false
}

struct PrinterConnectionStatusMessage {
    var text: String
    var tone: StatusMessageTone = .info
    var autoDismiss = true
    var duration: TimeInterval = 4
}

enum PrinterConnectionStatusEvent {
    case dismiss
    case show(PrinterConnectionStatusMessage)
}

struct PrinterConnectionCoordinatorCallbacks {
    var onSnapshotChange: @MainActor (PrinterConnectionSnapshot) -> Void
    var onProfilesChanged: @MainActor ([String: PrinterProfile]) -> Void
    var onProfileBootstrapRequested: @MainActor (PrinterProfile) -> Void
    var onStatusEvent: @MainActor (PrinterConnectionStatusEvent) -> Void

    init(
        onSnapshotChange: @escaping @MainActor (PrinterConnectionSnapshot) -> Void = { _ in },
        onProfilesChanged: @escaping @MainActor ([String: PrinterProfile]) -> Void = { _ in },
        onProfileBootstrapRequested: @escaping @MainActor (PrinterProfile) -> Void = { _ in },
        onStatusEvent: @escaping @MainActor (PrinterConnectionStatusEvent) -> Void = { _ in }
    ) {
        self.onSnapshotChange = onSnapshotChange
        self.onProfilesChanged = onProfilesChanged
        self.onProfileBootstrapRequested = onProfileBootstrapRequested
        self.onStatusEvent = onStatusEvent
    }
}

@MainActor
final class PrinterConnectionCoordinator: ObservableObject {
    private let ffi: any PrinterConnectionFFIBoundary
    private let callbacks: PrinterConnectionCoordinatorCallbacks

    @Published private(set) var snapshot: PrinterConnectionSnapshot
    private(set) var profiles: [String: PrinterProfile]

    private var pairingTask: Task<Void, Never>?

    init(
        ffi: any PrinterConnectionFFIBoundary,
        initialSnapshot: PrinterConnectionSnapshot = PrinterConnectionSnapshot(),
        initialProfiles: [String: PrinterProfile] = [:],
        callbacks: PrinterConnectionCoordinatorCallbacks = PrinterConnectionCoordinatorCallbacks()
    ) {
        self.ffi = ffi
        self.snapshot = initialSnapshot
        self.profiles = initialProfiles
        self.callbacks = callbacks
    }

    deinit {
        pairingTask?.cancel()
    }

    func replaceProfiles(_ profiles: [String: PrinterProfile]) {
        self.profiles = profiles
    }

    func deleteProfile(_ bleIdentifier: String) {
        guard profiles.removeValue(forKey: bleIdentifier) != nil else { return }
        callbacks.onProfilesChanged(profiles)

        let deletedConnectedPrinter = snapshot.printerName == bleIdentifier
        let deletedSelectedPrinter = snapshot.selectedPrinter == bleIdentifier
        let fallbackSelection = nextSelectedPrinter(excluding: bleIdentifier)

        mutateSnapshot { snapshot in
            snapshot.availablePrinters.removeAll { $0 == bleIdentifier }
            snapshot.nearbyPrinters.removeAll { $0 == bleIdentifier }

            if deletedSelectedPrinter {
                snapshot.selectedPrinter = fallbackSelection
            }

            if deletedConnectedPrinter {
                snapshot.isConnected = false
                snapshot.printerName = nil
                snapshot.printerModel = nil
                snapshot.battery = 0
                snapshot.isCharging = false
                snapshot.filmRemaining = 0
                snapshot.printCount = 0
            }
        }

        guard deletedConnectedPrinter || (deletedSelectedPrinter && snapshot.isConnected == false) else {
            return
        }

        startPairingLoop(disconnectCurrentPrinter: deletedConnectedPrinter)
    }

    func setSelectedPrinter(_ printer: String?) {
        mutateSnapshot { snapshot in
            snapshot.selectedPrinter = printer
        }
    }

    func startPairingLoop(
        scanDuration: Int = 3,
        connectDuration: Int = 3,
        disconnectCurrentPrinter: Bool = false
    ) {
        pairingTask?.cancel()

        mutateSnapshot { snapshot in
            snapshot.isPairing = true
            snapshot.pairingPhase = .scanning
            snapshot.pairingAttempt = 0
            snapshot.pairingStatus = L("Scanning...")
        }
        emitStatus(.dismiss)

        pairingTask = Task { [weak self] in
            guard let self else { return }

            if disconnectCurrentPrinter, self.ffi.isPrinterConnected() {
                await self.ffi.disconnectPrinter()
                if Task.isCancelled {
                    self.mutateSnapshot { snapshot in
                        snapshot.isPairing = false
                        snapshot.pairingPhase = .idle
                        snapshot.hasSearchedOnce = true
                    }
                    self.pairingTask = nil
                    return
                }
            }

            while !Task.isCancelled {
                self.mutateSnapshot { snapshot in
                    snapshot.pairingAttempt += 1
                    snapshot.pairingPhase = .scanning
                    snapshot.pairingStatus = L("Scanning...")
                }

                let printers = await self.ffi.scanPrinters(duration: scanDuration)
                if Task.isCancelled { break }

                let target: String?
                if let selected = self.snapshot.selectedPrinter, printers.contains(selected) {
                    target = selected
                } else {
                    target = printers.first
                }

                guard let target else {
                    try? await Task.sleep(nanoseconds: 500_000_000)
                    continue
                }

                self.mutateSnapshot { snapshot in
                    snapshot.pairingPhase = .connecting
                    snapshot.pairingStatus = L("connecting_to", target)
                }

                if self.ffi.isPrinterConnected() {
                    await self.ffi.disconnectPrinter()
                }

                let connected = await self.ffi.connectNamedPrinter(target, duration: connectDuration)
                if Task.isCancelled { break }
                guard connected else {
                    try? await Task.sleep(nanoseconds: 500_000_000)
                    continue
                }

                let model = self.ffi.fetchConnectedPrinterModel() ?? "Unknown"
                let status = await self.ffi.fetchConnectionStatus()
                if Task.isCancelled { break }

                self.mutateSnapshot { snapshot in
                    snapshot.isConnected = true
                    snapshot.isPairing = false
                    snapshot.pairingPhase = .idle
                    snapshot.printerName = target
                    snapshot.printerModel = model
                    snapshot.battery = status?.battery ?? 0
                    snapshot.isCharging = status?.isCharging ?? false
                    snapshot.filmRemaining = status?.filmRemaining ?? 0
                    snapshot.printCount = status?.printCount ?? 0
                    snapshot.selectedPrinter = target
                    snapshot.hasSearchedOnce = true
                    if snapshot.availablePrinters.contains(target) == false {
                        snapshot.availablePrinters.append(target)
                    }
                }

                self.bootstrapOrUpdateProfile(for: target, detectedModel: model)
                self.pairingTask = nil
                return
            }

            self.mutateSnapshot { snapshot in
                snapshot.isPairing = false
                snapshot.pairingPhase = .idle
                snapshot.hasSearchedOnce = true
            }
            self.pairingTask = nil
        }
    }

    func stopPairingLoop() {
        pairingTask?.cancel()
        pairingTask = nil
        mutateSnapshot { snapshot in
            snapshot.isPairing = false
            snapshot.pairingPhase = .idle
        }
    }

    func refresh() async {
        mutateSnapshot { snapshot in
            snapshot.isRefreshing = true
        }

        let status = await ffi.fetchConnectionStatus()

        mutateSnapshot { snapshot in
            snapshot.isRefreshing = false
            if let status {
                snapshot.isConnected = true
                snapshot.battery = status.battery
                snapshot.filmRemaining = status.filmRemaining
                snapshot.isCharging = status.isCharging
                snapshot.printCount = status.printCount
            } else {
                snapshot.isConnected = false
            }
        }
    }

    func scanNearby(duration: Int = 3) async {
        mutateSnapshot { snapshot in
            snapshot.isScanning = true
            snapshot.nearbyPrinters = []
        }

        let printers = await ffi.scanPrinters(duration: duration)
        let savedIdentifiers = Set(profiles.keys)

        mutateSnapshot { snapshot in
            snapshot.nearbyPrinters = printers.filter { savedIdentifiers.contains($0) == false }
            for printer in printers where snapshot.availablePrinters.contains(printer) == false {
                snapshot.availablePrinters.append(printer)
            }
            snapshot.isScanning = false
        }
    }

    func scanAll(duration: Int = 5, startPairingAfterScan: Bool = true) async {
        mutateSnapshot { snapshot in
            snapshot.isSearching = true
        }

        let printers = await ffi.scanPrinters(duration: duration)

        mutateSnapshot { snapshot in
            snapshot.availablePrinters = printers
            snapshot.isSearching = false
            snapshot.hasSearchedOnce = true

            if snapshot.selectedPrinter == nil || printers.contains(snapshot.selectedPrinter ?? "") == false {
                snapshot.selectedPrinter = printers.first
            }
        }

        if printers.isEmpty {
            emitStatus(.show(PrinterConnectionStatusMessage(text: L("No printers found"))))
            return
        }

        if printers.count == 1 {
            emitStatus(.show(PrinterConnectionStatusMessage(text: L("found_one_printer"))))
        } else {
            emitStatus(.show(PrinterConnectionStatusMessage(text: L("found_n_printers", printers.count))))
        }

        if startPairingAfterScan {
            startPairingLoop()
        }
    }

    func switchPrinter(to name: String, connectDuration: Int = 3) async {
        guard name != snapshot.selectedPrinter else { return }

        mutateSnapshot { snapshot in
            snapshot.selectedPrinter = name
        }

        if ffi.isPrinterConnected() {
            await ffi.disconnectPrinter()
        }

        mutateSnapshot { snapshot in
            snapshot.isConnected = false
        }

        startPairingLoop(connectDuration: connectDuration)
    }

    private func bootstrapOrUpdateProfile(for bleIdentifier: String, detectedModel: String) {
        if var existing = profiles[bleIdentifier] {
            guard existing.detectedModel != detectedModel else { return }
            existing.detectedModel = detectedModel
            profiles[bleIdentifier] = existing
            callbacks.onProfilesChanged(profiles)
            return
        }

        let profile = PrinterProfile(
            bleIdentifier: bleIdentifier,
            serialNumber: PrinterProfile.parseSerialNumber(from: bleIdentifier),
            detectedModel: detectedModel
        )
        profiles[bleIdentifier] = profile
        callbacks.onProfilesChanged(profiles)
        callbacks.onProfileBootstrapRequested(profile)
    }

    private func nextSelectedPrinter(excluding bleIdentifier: String) -> String? {
        let remainingSaved = Set(profiles.keys)

        if let availableSaved = snapshot.availablePrinters.first(where: { $0 != bleIdentifier && remainingSaved.contains($0) }) {
            return availableSaved
        }

        if let nearbySaved = snapshot.nearbyPrinters.first(where: { $0 != bleIdentifier && remainingSaved.contains($0) }) {
            return nearbySaved
        }

        return profiles.keys.sorted().first
    }

    private func mutateSnapshot(_ mutate: (inout PrinterConnectionSnapshot) -> Void) {
        mutate(&snapshot)
        callbacks.onSnapshotChange(snapshot)
    }

    private func emitStatus(_ event: PrinterConnectionStatusEvent) {
        callbacks.onStatusEvent(event)
    }
}
