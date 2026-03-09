import AVFoundation
import CoreText
import SwiftUI
import UniformTypeIdentifiers
import ImageIO

private enum AppRelauncher {
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

// MARK: - App Delegate (menu bar icon + window management)

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

// MARK: - Printer Profile

struct PrinterProfile: Codable, Equatable, Identifiable {
    var id: String { bleIdentifier }
    let bleIdentifier: String
    let serialNumber: String?
    var detectedModel: String
    var overriddenModel: String?
    var deviceColor: String?
    var customName: String?

    var displayName: String {
        if let name = customName, !name.isEmpty { return name }
        let model = effectiveModel
        if let color = deviceColor {
            return "\(model) (\(color))"
        }
        return model
    }
    var effectiveModel: String { overriddenModel ?? detectedModel }

    static let availableModels = [
        "Instax Mini Link", "Instax Mini Link 2", "Instax Mini Link 3",
        "Instax Square Link", "Instax Wide Link"
    ]
    static let availableColors = [
        "White", "Pink", "Blue", "Green", "Gray", "Black", "Beige"
    ]

    static func parseSerialNumber(from bleIdentifier: String) -> String? {
        var s = bleIdentifier
        if s.hasPrefix("INSTAX-") { s = String(s.dropFirst(7)) }
        let digits = String(s.prefix(while: { $0.isNumber }))
        return digits.isEmpty ? nil : digits
    }

    // MARK: UserDefaults persistence

    private static let defaultsKey = "printerProfiles"

    static func loadAll() -> [String: PrinterProfile] {
        guard let data = UserDefaults.standard.data(forKey: defaultsKey),
              let profiles = try? JSONDecoder().decode([String: PrinterProfile].self, from: data)
        else { return [:] }
        return profiles
    }

    static func save(_ profiles: [String: PrinterProfile]) {
        if let data = try? JSONEncoder().encode(profiles) {
            UserDefaults.standard.set(data, forKey: defaultsKey)
        }
    }

    static func save(_ profile: PrinterProfile) {
        var all = loadAll()
        all[profile.bleIdentifier] = profile
        save(all)
    }
}

// MARK: - Queue Item

struct NewPhotoDefaults: Codable, Equatable {
    static let storageKey = "newPhotoDefaults"

    var fitMode: String = "crop"
    var dateStampEnabled: Bool = false
    var showTimeRow: Bool = true
    var dateStampPosition: String = "bottomRight"
    var dateStampStyle: String = "classic"
    var dateStampFormat: String = "ymd"
    var lightBleedEnabled: Bool = false
    var filmOrientation: String = "default"

    static func load() -> Self {
        if let data = UserDefaults.standard.data(forKey: storageKey),
           let decoded = try? JSONDecoder().decode(Self.self, from: data) {
            return decoded
        }

        var defaults = Self()
        defaults.fitMode = UserDefaults.standard.string(forKey: "defaultFitMode") ?? defaults.fitMode
        return defaults
    }

    func save() {
        if let data = try? JSONEncoder().encode(self) {
            UserDefaults.standard.set(data, forKey: Self.storageKey)
            UserDefaults.standard.removeObject(forKey: "defaultFitMode")
        }
    }
}

private let initialNewPhotoDefaults = NewPhotoDefaults.load()

struct QueueItemEditState: Equatable {
    var fitMode: String
    var cropOffset: CGSize = .zero
    var cropZoom: CGFloat = 1.0
    var rotationAngle: Int = 0
    var dateStampEnabled: Bool = false
    var showTimeRow: Bool = true
    var dateStampPosition: String = "bottomRight"
    var dateStampStyle: String = "classic"
    var dateStampFormat: String = "ymd"
    var lightBleedEnabled: Bool = false
    var filmOrientation: String = "default"
}

struct QueueItem: Identifiable, Equatable {
    let id: UUID
    let url: URL
    let image: NSImage
    let imageDate: Date?
    var editState: QueueItemEditState

    init(
        id: UUID = UUID(),
        url: URL,
        image: NSImage,
        imageDate: Date?,
        editState: QueueItemEditState
    ) {
        self.id = id
        self.url = url
        self.image = image
        self.imageDate = imageDate
        self.editState = editState
    }
}

// MARK: - View Model

class ViewModel: ObservableObject {
    static let maxQueueItems = 20

    let ffi: InstantLinkFFI
    private var isApplyingQueueItemEditState = false

    // Printer state
    @Published var isConnected = false
    @Published var printerName: String?
    @Published var printerModel: String?
    @Published var battery: Int = 0
    @Published var isCharging: Bool = false
    @Published var filmRemaining: Int = 0
    @Published var printCount: Int = 0

    // Image queue
    @Published var queue: [QueueItem] = []
    @Published var selectedQueueIndex: Int = 0
    @Published var batchPrintIndex: Int = 0
    @Published var batchPrintTotal: Int = 0
    @Published var newPhotoDefaults: NewPhotoDefaults = initialNewPhotoDefaults {
        didSet {
            newPhotoDefaults.save()
            if queue.isEmpty {
                applyDefaultQueueItemEditState()
            }
        }
    }

    var selectedImage: NSImage? { queue.indices.contains(selectedQueueIndex) ? queue[selectedQueueIndex].image : nil }
    var selectedImagePath: String? { queue.indices.contains(selectedQueueIndex) ? queue[selectedQueueIndex].url.path : nil }
    var imageDate: Date? { queue.indices.contains(selectedQueueIndex) ? queue[selectedQueueIndex].imageDate : nil }

    // Print options
    @Published var fitMode: String = initialNewPhotoDefaults.fitMode {
        didSet {
            guard !isApplyingQueueItemEditState else { return }
            if fitMode != oldValue {
                resetCropAdjustments()
            }
            persistSelectedQueueItemEditState()
        }
    }

    // Crop interaction (pan & zoom)
    @Published var cropOffset: CGSize = .zero {
        didSet { persistSelectedQueueItemEditState() }
    }
    @Published var cropZoom: CGFloat = 1.0 {
        didSet { persistSelectedQueueItemEditState() }
    }
    var cropFrameSize: CGSize = .zero

    // Rotation
    @Published var rotationAngle: Int = 0 {  // 0, 90, 180, 270
        didSet { persistSelectedQueueItemEditState() }
    }

    // Date stamp
    @Published var dateStampEnabled: Bool = initialNewPhotoDefaults.dateStampEnabled {
        didSet { persistSelectedQueueItemEditState() }
    }
    @Published var showTimeRow: Bool = initialNewPhotoDefaults.showTimeRow {
        didSet { persistSelectedQueueItemEditState() }
    }
    @Published var dateStampPosition: String = initialNewPhotoDefaults.dateStampPosition {
        didSet { persistSelectedQueueItemEditState() }
    }
    @Published var dateStampStyle: String = initialNewPhotoDefaults.dateStampStyle {
        didSet { persistSelectedQueueItemEditState() }
    }
    @Published var dateStampFormat: String = initialNewPhotoDefaults.dateStampFormat {
        didSet { persistSelectedQueueItemEditState() }
    }
    @Published var lightBleedEnabled: Bool = initialNewPhotoDefaults.lightBleedEnabled {
        didSet { persistSelectedQueueItemEditState() }
    }

    // Camera mode
    @Published var captureMode: CaptureMode = .file
    @Published var cameraState: CameraState = .viewfinder
    @Published var capturedImage: NSImage?
    @Published var availableCameras: [AVCaptureDevice] = []
    @Published var selectedCamera: AVCaptureDevice?
    var captureSession: AVCaptureSession?
    private var photoOutput: AVCapturePhotoOutput?
    private var photoDelegate: CameraPhotoCaptureDelegate?

    // Self-timer
    @Published var timerMode: Int = 0          // 0=off, 2=2s, 10=10s
    @Published var timerCountdown: Int? = nil  // nil = not counting down
    var timerTask: Task<Void, Never>? = nil

    // Film orientation
    @Published var filmOrientation: String = initialNewPhotoDefaults.filmOrientation {  // "default" or "rotated"
        didSet { persistSelectedQueueItemEditState() }
    }
    // Capture and print — auto-commit and print after photo is taken
    var autoPrintAfterCapture = false

    // Printer selection (for multi-printer switching)
    @Published var availablePrinters: [String] = []
    @Published var selectedPrinter: String?

    // Printer profiles
    @Published var printerProfiles: [String: PrinterProfile] = PrinterProfile.loadAll()
    @Published var showProfileSheet = false
    @Published var showProfileEditor = false
    @Published var editingProfile: PrinterProfile?

    // Printer aspect ratio for crop preview
    var printerAspectRatio: CGFloat? {
        let model: String?
        if let bleId = printerName, let profile = printerProfiles[bleId] {
            model = profile.effectiveModel
        } else {
            model = printerModel
        }
        switch model {
        case "Instax Square Link":  return 1.0          // 800×800
        case "Instax Mini Link",
             "Instax Mini Link 2",
             "Instax Mini Link 3":  return 600.0/800.0  // 600×800
        case "Instax Wide Link":    return 1260.0/840.0  // 1260×840
        default: return nil
        }
    }

    /// Aspect ratio adjusted for film orientation.
    /// When "rotated", swaps width/height for non-square films.
    var orientedAspectRatio: CGFloat? {
        guard let ar = printerAspectRatio else { return nil }
        if filmOrientation == "rotated" && ar != 1.0 {
            return 1.0 / ar
        }
        return ar
    }

    var printerModelTag: String? {
        let model: String?
        if let bleId = printerName, let profile = printerProfiles[bleId] {
            model = profile.effectiveModel
        } else {
            model = printerModel
        }
        switch model {
        case "Instax Square Link":  return "Sqre"
        case "Instax Mini Link",
             "Instax Mini Link 2",
             "Instax Mini Link 3":  return "Mini"
        case "Instax Wide Link":    return "Wide"
        default: return nil
        }
    }

    // UI state
    @Published var isPrinting = false
    @Published var printProgress: (sent: Int, total: Int)?
    @Published var isSearching = false
    @Published var isRefreshing = false
    @Published var statusMessage: String?
    @Published var coreVersion: String = "..."
    @Published var hasSearchedOnce = false

    // Sheets
    @Published var showSettings = false
    @Published var showImageEditor = false
    @Published var showPrinterPicker = false

    // Printer picker
    @Published var nearbyPrinters: [String] = []
    @Published var isScanning = false

    // Update state
    @Published var updateAvailable: String?
    @Published var updateDownloadURL: String?
    @Published var isUpdating = false
    @Published var updateProgress: Double = 0
    @Published var updateError: String?

    // Pairing mode
    @Published var isPairing = false
    @Published var pairingAttempt = 0
    @Published var pairingStatus: String = L("Scanning...")
    private var pairingTask: Task<Void, Never>?

    private var autoRefreshTimer: Timer?

    init() {
        guard let f = InstantLinkFFI() else {
            fatalError("Failed to load InstantLink native library. The app bundle may be corrupted.")
        }
        ffi = f
        loadCoreVersion()
        autoRefreshTimer = Timer.scheduledTimer(withTimeInterval: 10, repeats: true) { [weak self] _ in
            guard let self = self, self.isConnected else { return }
            Task { await self.refreshStatus() }
        }
    }

    deinit {
        autoRefreshTimer?.invalidate()
        ffi.disconnectSync()
    }

    // MARK: - Pairing Mode (continuous scan loop)

    func startPairing() {
        // Cancel any existing pairing task
        pairingTask?.cancel()

        isPairing = true
        pairingAttempt = 0
        pairingStatus = L("Scanning...")
        statusMessage = nil

        pairingTask = Task { [weak self] in
            guard let self = self else { return }

            while !Task.isCancelled {
                await MainActor.run {
                    self.pairingAttempt += 1
                    self.pairingStatus = L("Scanning...")
                }

                // Phase 1: lightweight scan to discover printers
                let printers = await self.ffi.scan(duration: 3)

                if Task.isCancelled { break }

                // Pick the target printer (or first found)
                let device = await MainActor.run { self.selectedPrinter }
                let target: String?
                if let device = device, printers.contains(device) {
                    target = device
                } else {
                    target = printers.first
                }

                if let target = target {
                    // Phase 2: connect via FFI (persistent connection)
                    await MainActor.run {
                        self.pairingStatus = L("connecting_to", target)
                    }

                    // Disconnect any existing connection first
                    if self.ffi.isConnected() {
                        await self.ffi.disconnect()
                    }

                    let connected = await self.ffi.connect(device: target, duration: 3)

                    if Task.isCancelled { break }

                    if connected {
                        // Phase 3: single combined status call
                        let model = self.ffi.deviceModel() ?? "Unknown"
                        let s = await self.ffi.status()

                        if Task.isCancelled { break }

                        await MainActor.run {
                            self.isConnected = true
                            self.isPairing = false
                            self.printerName = target
                            self.printerModel = model
                            self.battery = s?.battery ?? 0
                            self.isCharging = s?.isCharging ?? false
                            self.filmRemaining = s?.film ?? 0
                            self.printCount = s?.printCount ?? 0
                            self.selectedPrinter = target
                            self.hasSearchedOnce = true
                            if !self.availablePrinters.contains(target) {
                                self.availablePrinters.append(target)
                            }

                            // Profile management
                            if var existing = self.printerProfiles[target] {
                                existing.detectedModel = model
                                self.saveProfile(existing)
                            } else {
                                let profile = PrinterProfile(
                                    bleIdentifier: target,
                                    serialNumber: PrinterProfile.parseSerialNumber(from: target),
                                    detectedModel: model
                                )
                                self.saveProfile(profile)
                                self.editingProfile = profile
                                self.showProfileSheet = true
                            }
                        }
                        return
                    }
                }

                // Brief pause before retrying
                try? await Task.sleep(nanoseconds: 500_000_000)
            }

            // Cancelled
            await MainActor.run {
                self.isPairing = false
                self.hasSearchedOnce = true
            }
        }
    }

    func stopPairing() {
        pairingTask?.cancel()
        pairingTask = nil
        isPairing = false
    }

    // MARK: - Printer Profiles

    var currentPrinterDisplayName: String? {
        guard let bleId = printerName else { return nil }
        return printerProfiles[bleId]?.displayName ?? bleId
    }

    func saveProfile(_ profile: PrinterProfile) {
        printerProfiles[profile.bleIdentifier] = profile
        PrinterProfile.save(printerProfiles)
        editingProfile = nil
    }

    func deleteProfile(_ bleIdentifier: String) {
        printerProfiles.removeValue(forKey: bleIdentifier)
        PrinterProfile.save(printerProfiles)
    }

    // MARK: - Refresh (quiet — no "searching" spinner, just update numbers)

    func refreshStatus() async {
        await MainActor.run { isRefreshing = true }

        // Query printer status via BLE — will fail/timeout if printer is offline
        let s = await ffi.status()

        await MainActor.run {
            isRefreshing = false
            if let s = s {
                isConnected = true
                battery = s.battery
                filmRemaining = s.film
                isCharging = s.isCharging
                printCount = s.printCount
            } else {
                isConnected = false
            }
        }
    }

    // MARK: - Scan (discover all printers for the picker, then connect)

    func scanAllPrinters() async {
        await MainActor.run { isSearching = true }
        let printers = await ffi.scan()
        await MainActor.run {
            availablePrinters = printers
            isSearching = false
            hasSearchedOnce = true

            if printers.isEmpty {
                showStatus(L("No printers found"))
                return
            }
            if printers.count == 1 {
                showStatus(L("found_one_printer"))
            } else {
                showStatus(L("found_n_printers", printers.count))
            }
            if selectedPrinter == nil || !printers.contains(selectedPrinter!) {
                selectedPrinter = printers.first
            }
        }
        if !printers.isEmpty {
            startPairing()
        }
    }

    // MARK: - Switch printer (from footer picker)

    func switchPrinter(to name: String) {
        guard name != selectedPrinter else { return }
        selectedPrinter = name
        // Disconnect existing connection before connecting to new printer
        Task {
            if ffi.isConnected() {
                await ffi.disconnect()
            }
            await MainActor.run { isConnected = false }
            startPairing()
        }
    }

    // MARK: - Scan Nearby (one-shot for picker)

    func scanNearby() {
        isScanning = true
        nearbyPrinters = []
        Task {
            let printers = await ffi.scan(duration: 3)
            await MainActor.run {
                let savedKeys = Set(printerProfiles.keys)
                nearbyPrinters = printers.filter { !savedKeys.contains($0) }
                // Also update the full available list
                for p in printers where !availablePrinters.contains(p) {
                    availablePrinters.append(p)
                }
                isScanning = false
            }
        }
    }

    // MARK: - Queue Management

    func addImages(from urls: [URL]) {
        guard !urls.isEmpty else { return }
        let hadSelectedQueueItem = queue.indices.contains(selectedQueueIndex)
        if hadSelectedQueueItem {
            persistSelectedQueueItemEditState()
        }
        let initialCount = queue.count
        for url in urls {
            if queue.count >= Self.maxQueueItems { break }
            guard let image = NSImage(contentsOf: url) else { continue }
            let date = Self.extractImageDate(from: url)
            queue.append(QueueItem(
                url: url,
                image: image,
                imageDate: date,
                editState: makeNewQueueItemEditState()
            ))
        }
        if !queue.isEmpty {
            selectedQueueIndex = queue.count - 1
            applyQueueItemEditState(queue[selectedQueueIndex].editState)
        }
        let addedCount = queue.count - initialCount
        if addedCount == 0 && initialCount >= Self.maxQueueItems {
            showStatus("Queue limit reached (\(Self.maxQueueItems) images max)")
        } else if addedCount < urls.count {
            showStatus("Added \(addedCount) of \(urls.count) images (\(Self.maxQueueItems) max in queue)")
        }
    }

    func removeQueueItem(at index: Int) {
        guard queue.indices.contains(index) else { return }
        persistSelectedQueueItemEditState()
        let wasSelected = index == selectedQueueIndex
        queue.remove(at: index)
        if queue.isEmpty {
            selectedQueueIndex = 0
            applyDefaultQueueItemEditState()
        } else {
            let nextIndex: Int
            if wasSelected {
                nextIndex = min(index, queue.count - 1)
            } else if index < selectedQueueIndex {
                nextIndex = selectedQueueIndex - 1
            } else {
                nextIndex = selectedQueueIndex
            }
            selectedQueueIndex = nextIndex
            applyQueueItemEditState(queue[nextIndex].editState)
        }
    }

    func selectQueueItem(at index: Int) {
        guard queue.indices.contains(index) else { return }
        persistSelectedQueueItemEditState()
        selectedQueueIndex = index
        applyQueueItemEditState(queue[index].editState)
    }

    func moveQueueItem(from source: Int, to destination: Int) {
        guard queue.indices.contains(source), destination >= 0, destination < queue.count else { return }
        persistSelectedQueueItemEditState()
        let item = queue.remove(at: source)
        queue.insert(item, at: destination)
        // Follow the moved item
        selectedQueueIndex = destination
        applyQueueItemEditState(queue[destination].editState)
    }

    // MARK: - Image Selection

    func selectImage() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.png, .jpeg, .heic, .tiff, .webP]
        panel.allowsMultipleSelection = true
        panel.canChooseDirectories = false
        panel.message = L("Select an image to print")
        if panel.runModal() == .OK {
            addImages(from: panel.urls)
        }
    }

    func loadImage(from url: URL) {
        addImages(from: [url])
    }

    private static func extractImageDate(from url: URL) -> Date? {
        guard let source = CGImageSourceCreateWithURL(url as CFURL, nil) else {
            return fileModificationDate(url)
        }
        guard let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [String: Any],
              let exif = properties[kCGImagePropertyExifDictionary as String] as? [String: Any],
              let dateString = exif[kCGImagePropertyExifDateTimeOriginal as String] as? String else {
            return fileModificationDate(url)
        }
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy:MM:dd HH:mm:ss"
        return formatter.date(from: dateString) ?? fileModificationDate(url)
    }

    private static func fileModificationDate(_ url: URL) -> Date? {
        try? FileManager.default.attributesOfItem(atPath: url.path)[.modificationDate] as? Date
    }

    func clearImage() {
        queue.removeAll()
        selectedQueueIndex = 0
        applyDefaultQueueItemEditState()
    }

    func resetCropAdjustments() {
        cropOffset = .zero
        cropZoom = 1.0
    }

    func saveCurrentSettingsAsNewPhotoDefaults() {
        newPhotoDefaults = NewPhotoDefaults(
            fitMode: fitMode,
            dateStampEnabled: dateStampEnabled,
            showTimeRow: showTimeRow,
            dateStampPosition: dateStampPosition,
            dateStampStyle: dateStampStyle,
            dateStampFormat: dateStampFormat,
            lightBleedEnabled: lightBleedEnabled,
            filmOrientation: filmOrientation
        )
    }

    func resetNewPhotoDefaults() {
        newPhotoDefaults = NewPhotoDefaults()
    }

    private func makeCurrentQueueItemEditState() -> QueueItemEditState {
        QueueItemEditState(
            fitMode: fitMode,
            cropOffset: cropOffset,
            cropZoom: cropZoom,
            rotationAngle: rotationAngle,
            dateStampEnabled: dateStampEnabled,
            showTimeRow: showTimeRow,
            dateStampPosition: dateStampPosition,
            dateStampStyle: dateStampStyle,
            dateStampFormat: dateStampFormat,
            lightBleedEnabled: lightBleedEnabled,
            filmOrientation: filmOrientation
        )
    }

    private func makeQueueItemEditStateFromDefaults() -> QueueItemEditState {
        QueueItemEditState(
            fitMode: newPhotoDefaults.fitMode,
            dateStampEnabled: newPhotoDefaults.dateStampEnabled,
            showTimeRow: newPhotoDefaults.showTimeRow,
            dateStampPosition: newPhotoDefaults.dateStampPosition,
            dateStampStyle: newPhotoDefaults.dateStampStyle,
            dateStampFormat: newPhotoDefaults.dateStampFormat,
            lightBleedEnabled: newPhotoDefaults.lightBleedEnabled,
            filmOrientation: newPhotoDefaults.filmOrientation
        )
    }

    private func makeNewQueueItemEditState() -> QueueItemEditState {
        makeQueueItemEditStateFromDefaults()
    }

    private func applyDefaultQueueItemEditState() {
        applyQueueItemEditState(makeQueueItemEditStateFromDefaults())
    }

    private func applyQueueItemEditState(_ editState: QueueItemEditState) {
        isApplyingQueueItemEditState = true
        fitMode = editState.fitMode
        cropOffset = editState.cropOffset
        cropZoom = editState.cropZoom
        rotationAngle = editState.rotationAngle
        dateStampEnabled = editState.dateStampEnabled
        showTimeRow = editState.showTimeRow
        dateStampPosition = editState.dateStampPosition
        dateStampStyle = editState.dateStampStyle
        dateStampFormat = editState.dateStampFormat
        lightBleedEnabled = editState.lightBleedEnabled
        filmOrientation = editState.filmOrientation
        isApplyingQueueItemEditState = false
    }

    private func persistSelectedQueueItemEditState() {
        guard !isApplyingQueueItemEditState,
              queue.indices.contains(selectedQueueIndex) else { return }
        queue[selectedQueueIndex].editState = makeCurrentQueueItemEditState()
    }

    // MARK: - Camera Session

    func requestCameraAccessAndStart() {
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized:
            discoverCameras()
            startCameraSession()
        case .notDetermined:
            AVCaptureDevice.requestAccess(for: .video) { [weak self] granted in
                DispatchQueue.main.async {
                    guard let self = self else { return }
                    if granted {
                        self.discoverCameras()
                        self.startCameraSession()
                    } else {
                        self.captureMode = .file
                        self.statusMessage = L("Camera access denied")
                    }
                }
            }
        default:
            captureMode = .file
            statusMessage = L("Camera access denied")
        }
    }

    func discoverCameras() {
        let discovery = AVCaptureDevice.DiscoverySession(
            deviceTypes: [.builtInWideAngleCamera, .externalUnknown],
            mediaType: .video,
            position: .unspecified
        )
        availableCameras = discovery.devices
        if selectedCamera == nil || !availableCameras.contains(where: { $0.uniqueID == selectedCamera?.uniqueID }) {
            selectedCamera = availableCameras.first
        }
    }

    func startCameraSession() {
        guard let camera = selectedCamera else { return }
        let session = AVCaptureSession()
        session.sessionPreset = .photo

        guard let input = try? AVCaptureDeviceInput(device: camera) else { return }
        if session.canAddInput(input) { session.addInput(input) }

        let output = AVCapturePhotoOutput()
        if session.canAddOutput(output) { session.addOutput(output) }
        photoOutput = output

        captureSession = session
        cameraState = .viewfinder
        capturedImage = nil

        DispatchQueue.global(qos: .userInitiated).async {
            session.startRunning()
        }
    }

    func stopCameraSession() {
        cancelTimer()
        captureSession?.stopRunning()
        captureSession = nil
        photoOutput = nil
    }

    func switchCamera(to device: AVCaptureDevice) {
        selectedCamera = device
        stopCameraSession()
        startCameraSession()
    }

    func capturePhoto() {
        guard let output = photoOutput else { return }
        // Always use JPEG — HEVC produces HEIF data with lazy decoding that can crash
        // (EXC_BAD_ACCESS in HEIFReadPlugin when QuartzCore renders the NSImage)
        let settings = AVCapturePhotoSettings(format: [AVVideoCodecKey: AVVideoCodecType.jpeg])
        let delegate = CameraPhotoCaptureDelegate { [weak self] image in
            DispatchQueue.main.async {
                guard let self = self else { return }
                self.capturedImage = image
                self.cameraState = .preview
                if self.autoPrintAfterCapture {
                    self.autoPrintAfterCapture = false
                    if self.commitCapture() {
                        Task { await self.printSelectedImage() }
                    }
                }
            }
        }
        photoDelegate = delegate
        output.capturePhoto(with: settings, delegate: delegate)
    }

    func retakePhoto() {
        cancelTimer()
        capturedImage = nil
        cameraState = .viewfinder
    }

    func captureWithTimer() {
        if timerCountdown != nil {
            cancelTimer()
            return
        }
        if timerMode == 0 {
            capturePhoto()
            return
        }
        timerCountdown = timerMode
        timerTask = Task { @MainActor in
            while let remaining = timerCountdown, remaining > 0 {
                try? await Task.sleep(nanoseconds: 1_000_000_000)
                if Task.isCancelled { break }
                timerCountdown = remaining - 1
            }
            if !Task.isCancelled, timerCountdown == 0 {
                capturePhoto()
            }
            timerCountdown = nil
        }
    }

    func cancelTimer() {
        timerTask?.cancel()
        timerTask = nil
        timerCountdown = nil
        autoPrintAfterCapture = false
    }

    @discardableResult
    func commitCapture() -> Bool {
        guard let image = capturedImage,
              let tiffData = image.tiffRepresentation,
              let bitmap = NSBitmapImageRep(data: tiffData) else { return false }

        // Center-crop to native printer aspect ratio (orientation rotation happens in prepareImageForPrint)
        var outputBitmap = bitmap
        if let ar = printerAspectRatio {
            let srcW = CGFloat(bitmap.pixelsWide)
            let srcH = CGFloat(bitmap.pixelsHigh)
            let srcAR = srcW / srcH

            let cropW: CGFloat
            let cropH: CGFloat
            if srcAR > ar {
                // Source is wider — crop sides
                cropH = srcH
                cropW = srcH * ar
            } else {
                // Source is taller — crop top/bottom
                cropW = srcW
                cropH = srcW / ar
            }
            let cropX = (srcW - cropW) / 2
            let cropY = (srcH - cropH) / 2
            let cropRect = CGRect(x: cropX, y: cropY, width: cropW, height: cropH)

            if let cgImage = bitmap.cgImage?.cropping(to: cropRect) {
                outputBitmap = NSBitmapImageRep(cgImage: cgImage)
            }
        }

        guard queue.count < Self.maxQueueItems else {
            showStatus("Queue limit reached (\(Self.maxQueueItems) images max)")
            return false
        }

        guard let jpegData = outputBitmap.representation(
            using: .jpeg, properties: [.compressionFactor: 0.9]
        ) else { return false }

        let tempURL = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString)
            .appendingPathExtension("jpg")
        do {
            try jpegData.write(to: tempURL)
        } catch {
            showStatus(L("Failed to save captured image: \(error.localizedDescription)"))
            return false
        }

        // Append to queue so the file-mode preview matches
        let finalImage = NSImage(data: jpegData) ?? image
        if queue.indices.contains(selectedQueueIndex) {
            persistSelectedQueueItemEditState()
        }
        queue.append(QueueItem(
            url: tempURL,
            image: finalImage,
            imageDate: Date(),
            editState: makeNewQueueItemEditState()
        ))
        selectedQueueIndex = queue.count - 1
        applyQueueItemEditState(queue[selectedQueueIndex].editState)
        captureMode = .file
        stopCameraSession()
        capturedImage = nil
        cameraState = .viewfinder
        return true
    }

    func rotateClockwise() { rotationAngle = (rotationAngle + 90) % 360 }
    func rotateCounterClockwise() { rotationAngle = (rotationAngle + 270) % 360 }

    private func cropCGImage(_ cgImage: CGImage) -> CGImage? {
        guard printerAspectRatio != nil,
              (cropOffset != .zero || cropZoom != 1.0) else { return nil }

        let pixelW = CGFloat(cgImage.width)
        let pixelH = CGFloat(cgImage.height)
        let frameW = cropFrameSize.width
        let frameH = cropFrameSize.height
        guard frameW > 0, frameH > 0 else { return nil }

        let imageAR = pixelW / pixelH
        let frameAR = frameW / frameH

        let displayW: CGFloat
        let displayH: CGFloat
        if imageAR > frameAR {
            displayH = frameH
            displayW = frameH * imageAR
        } else {
            displayW = frameW
            displayH = frameW / imageAR
        }

        let ppsp = pixelW / (displayW * cropZoom)
        let visX = (displayW * cropZoom - frameW) / 2 - cropOffset.width
        let visY = (displayH * cropZoom - frameH) / 2 - cropOffset.height

        let cropRect = CGRect(
            x: visX * ppsp,
            y: visY * ppsp,
            width: frameW * ppsp,
            height: frameH * ppsp
        )

        let bounds = CGRect(x: 0, y: 0, width: pixelW, height: pixelH)
        let clampedRect = cropRect.intersection(bounds)
        guard !clampedRect.isEmpty else { return nil }

        return cgImage.cropping(to: clampedRect)
    }

    // MARK: - Date Stamp Style Presets

    struct DateStampPreset {
        let displayName: String
        let fontFamily: String
        let sizePercent: CGFloat
        let tracking: CGFloat
        let separator: String
        let color: (CGFloat, CGFloat, CGFloat)
        let glowColor: (CGFloat, CGFloat, CGFloat)
        let glowRadius: CGFloat
        let defaultLightBleed: Bool
    }

    static let presetOrder: [String] = ["classic", "modern", "dotMatrix", "labPrint", "machinePrint"]

    static let dateStampPresets: [String: DateStampPreset] = [
        "classic": DateStampPreset(
            displayName: "Quartz Date", fontFamily: "DSEG7ClassicMini-Regular",
            sizePercent: 0.026, tracking: 0.05, separator: ".",
            color: (0.961, 0.541, 0.122), glowColor: (0.961, 0.541, 0.122),
            glowRadius: 0.15, defaultLightBleed: true
        ),
        "modern": DateStampPreset(
            displayName: "Modern", fontFamily: "DSEG7ModernMini-Regular",
            sizePercent: 0.026, tracking: 0.05, separator: ".",
            color: (0.180, 0.871, 0.412), glowColor: (0.180, 0.871, 0.412),
            glowRadius: 0.12, defaultLightBleed: true
        ),
        "dotMatrix": DateStampPreset(
            displayName: "Data Back", fontFamily: "MatrixSansScreen",
            sizePercent: 0.024, tracking: 0.08, separator: ".",
            color: (1.0, 0.435, 0.165), glowColor: (1.0, 0.435, 0.165),
            glowRadius: 0.10, defaultLightBleed: true
        ),
        "labPrint": DateStampPreset(
            displayName: "Lab Print", fontFamily: "MatrixSansPrint",
            sizePercent: 0.022, tracking: 0.06, separator: "-",
            color: (0.953, 0.933, 0.890), glowColor: (0.953, 0.933, 0.890),
            glowRadius: 0.0, defaultLightBleed: false
        ),
        "machinePrint": DateStampPreset(
            displayName: "Machine", fontFamily: "IBMPlexMono-Medium",
            sizePercent: 0.020, tracking: 0.03, separator: "-",
            color: (0.953, 0.933, 0.890), glowColor: (0.953, 0.933, 0.890),
            glowRadius: 0.0, defaultLightBleed: false
        ),
    ]

    // MARK: - Bundled Font Registration

    static func registerBundledFonts() {
        guard let resourcePath = Bundle.main.resourcePath else { return }
        let fontsDir = (resourcePath as NSString).appendingPathComponent("Fonts")
        guard let fontFiles = try? FileManager.default.contentsOfDirectory(atPath: fontsDir) else { return }
        for file in fontFiles where file.hasSuffix(".ttf") {
            let fontURL = URL(fileURLWithPath: (fontsDir as NSString).appendingPathComponent(file)) as CFURL
            CTFontManagerRegisterFontsForURL(fontURL, .process, nil)
        }
    }

    // MARK: - Date Stamp Text Formatting

    func dateStampText(from date: Date) -> String {
        let cal = Calendar.current
        let y = cal.component(.year, from: date) % 100
        let m = cal.component(.month, from: date)
        let d = cal.component(.day, from: date)
        let preset = Self.dateStampPresets[dateStampStyle] ?? Self.dateStampPresets["classic"]!
        let s = preset.separator
        let (yy, mm, dd) = (String(format: "%02d", y), String(format: "%02d", m), String(format: "%02d", d))
        switch dateStampFormat {
        case "mdy": return "\(mm)\(s)\(dd)\(s)\(yy)"
        case "dmy": return "\(dd)\(s)\(mm)\(s)\(yy)"
        default:    return "\(yy)\(s)\(mm)\(s)\(dd)"
        }
    }

    func timeStampText(from date: Date) -> String {
        let cal = Calendar.current
        return String(format: "%02d:%02d", cal.component(.hour, from: date), cal.component(.minute, from: date))
    }

    // MARK: - Date Stamp Rendering

    func stampImage(_ cgImage: CGImage) -> CGImage? {
        let width = cgImage.width
        let height = cgImage.height
        let colorSpace = CGColorSpaceCreateDeviceRGB()

        guard let context = CGContext(
            data: nil,
            width: width,
            height: height,
            bitsPerComponent: 8,
            bytesPerRow: 0,
            space: colorSpace,
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        ) else { return nil }

        context.draw(cgImage, in: CGRect(x: 0, y: 0, width: width, height: height))

        let date = imageDate ?? Date()
        let preset = Self.dateStampPresets[dateStampStyle]
            ?? Self.dateStampPresets["classic"]!
        let fontSize = CGFloat(height) * preset.sizePercent
        let padding = fontSize * 0.8
        let rowGap = fontSize * 0.3
        let kern = fontSize * preset.tracking

        let (r, g, b) = preset.color
        let (gr, gg, gb) = preset.glowColor

        let ctFont = CTFontCreateWithName(preset.fontFamily as CFString, fontSize, nil)
        let textColor = CGColor(srgbRed: r, green: g, blue: b, alpha: 1.0)

        let dateText = dateStampText(from: date)
        let timeText = timeStampText(from: date)

        let attrs: [NSAttributedString.Key: Any] = [
            .font: ctFont,
            .foregroundColor: textColor,
            .kern: kern,
        ]

        let dateLine = CTLineCreateWithAttributedString(NSAttributedString(string: dateText, attributes: attrs))
        var dateAscent: CGFloat = 0, dateDescent: CGFloat = 0, dateLeading: CGFloat = 0
        let dateWidth = CGFloat(CTLineGetTypographicBounds(dateLine, &dateAscent, &dateDescent, &dateLeading))
        let dateLineHeight = dateAscent + dateDescent

        var timeLineWidth: CGFloat = 0
        var timeLineObj: CTLine?
        var timeLineHeight: CGFloat = 0
        if showTimeRow {
            let tLine = CTLineCreateWithAttributedString(NSAttributedString(string: timeText, attributes: attrs))
            var tAscent: CGFloat = 0, tDescent: CGFloat = 0, tLeading: CGFloat = 0
            timeLineWidth = CGFloat(CTLineGetTypographicBounds(tLine, &tAscent, &tDescent, &tLeading))
            timeLineHeight = tAscent + tDescent
            timeLineObj = tLine
        }

        let maxWidth = max(dateWidth, timeLineWidth)
        let totalHeight = dateLineHeight + (showTimeRow ? timeLineHeight + rowGap : 0)

        // Compute origin (Core Graphics: origin at bottom-left)
        let blockX: CGFloat
        let blockY: CGFloat
        switch dateStampPosition {
        case "topLeft":
            blockX = padding
            blockY = CGFloat(height) - padding - totalHeight
        case "topRight":
            blockX = CGFloat(width) - padding - maxWidth
            blockY = CGFloat(height) - padding - totalHeight
        case "bottomLeft":
            blockX = padding
            blockY = padding
        default:
            blockX = CGFloat(width) - padding - maxWidth
            blockY = padding
        }

        // Drawing closure for glow + sharp pass
        func drawText() {
            // Date row (top row)
            let dateY = blockY + (showTimeRow ? timeLineHeight + rowGap : 0)
            context.textPosition = CGPoint(x: blockX, y: dateY)
            CTLineDraw(dateLine, context)
            // Time row (below date)
            if showTimeRow, let tLine = timeLineObj {
                context.textPosition = CGPoint(x: blockX, y: blockY)
                CTLineDraw(tLine, context)
            }
        }

        if lightBleedEnabled && preset.glowRadius > 0 {
            // Glow pass: draw with shadow
            context.saveGState()
            context.setShadow(
                offset: .zero,
                blur: fontSize * preset.glowRadius,
                color: CGColor(srgbRed: gr, green: gg, blue: gb, alpha: 0.6)
            )
            drawText()
            context.restoreGState()

            // Sharp overdraw
            drawText()
        } else {
            drawText()
        }

        return context.makeImage()
    }

    // MARK: - Image Rotation

    private func rotateCGImage(_ cgImage: CGImage, degrees: Int) -> CGImage? {
        let w = cgImage.width
        let h = cgImage.height
        let colorSpace = CGColorSpaceCreateDeviceRGB()

        let newW: Int
        let newH: Int
        if degrees == 90 || degrees == 270 {
            newW = h; newH = w
        } else {
            newW = w; newH = h
        }

        guard let context = CGContext(
            data: nil, width: newW, height: newH,
            bitsPerComponent: 8, bytesPerRow: 0, space: colorSpace,
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        ) else { return nil }

        switch degrees {
        case 90:
            context.translateBy(x: CGFloat(newW), y: 0)
            context.rotate(by: .pi / 2)
        case 180:
            context.translateBy(x: CGFloat(newW), y: CGFloat(newH))
            context.rotate(by: .pi)
        case 270:
            context.translateBy(x: 0, y: CGFloat(newH))
            context.rotate(by: -.pi / 2)
        default:
            break
        }

        context.draw(cgImage, in: CGRect(x: 0, y: 0, width: w, height: h))
        return context.makeImage()
    }

    // MARK: - Print Preparation

    func prepareImageForPrint() -> (path: String, fit: String, tempFile: String?)? {
        guard let path = selectedImagePath,
              let image = selectedImage,
              let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else { return nil }

        var currentCG = cgImage
        var processed = false

        if fitMode == "crop", let cropped = cropCGImage(currentCG) {
            currentCG = cropped
            processed = true
        }

        if rotationAngle != 0, let rotated = rotateCGImage(currentCG, degrees: rotationAngle) {
            currentCG = rotated
            processed = true
        }

        if dateStampEnabled, let stamped = stampImage(currentCG) {
            currentCG = stamped
            processed = true
        }

        // If film orientation is rotated, rotate 90° to fit native pixel layout
        if filmOrientation == "rotated", let ar = printerAspectRatio, ar != 1.0 {
            if let rotated = rotateCGImage(currentCG, degrees: 90) {
                currentCG = rotated
                processed = true
            }
        }

        if processed {
            let tempURL = FileManager.default.temporaryDirectory
                .appendingPathComponent("instantlink_print_\(UUID().uuidString).jpg")
            let bitmapRep = NSBitmapImageRep(cgImage: currentCG)
            guard let jpegData = bitmapRep.representation(
                using: .jpeg, properties: [.compressionFactor: 0.95]
            ) else { return nil }
            do {
                try jpegData.write(to: tempURL)
                return (path: tempURL.path, fit: "stretch", tempFile: tempURL.path)
            } catch {
                return nil
            }
        }

        return (path: path, fit: fitMode, tempFile: nil)
    }

    // MARK: - Printing

    func printSelectedImage() async {
        guard let prepared = prepareImageForPrint() else { return }
        await MainActor.run {
            isPrinting = true
            printProgress = nil
        }

        let success = await ffi.printImage(
            path: prepared.path,
            quality: 100,
            fit: prepared.fit
        ) { [weak self] sent, total in
            DispatchQueue.main.async {
                self?.printProgress = (sent: Int(sent), total: Int(total))
            }
        }

        if let temp = prepared.tempFile {
            try? FileManager.default.removeItem(atPath: temp)
        }

        await MainActor.run {
            isPrinting = false
            printProgress = nil
            showStatus(success ? L("Printed!") : L("Print failed"))
        }
        await refreshStatus()
    }

    // MARK: - Batch Printing

    func printQueue() async {
        let count = min(queue.count, filmRemaining)
        guard count > 0 else { return }

        await MainActor.run {
            isPrinting = true
            printProgress = nil
            batchPrintIndex = 0
            batchPrintTotal = count
        }

        for i in 0..<count {
            await MainActor.run {
                batchPrintIndex = i + 1
                selectQueueItem(at: i)
            }

            guard let prepared = prepareImageForPrint() else {
                await MainActor.run {
                    isPrinting = false
                    batchPrintTotal = 0
                    showStatus(L("print_failed_at", i + 1, count))
                }
                return
            }

            let success = await ffi.printImage(
                path: prepared.path,
                quality: 100,
                fit: prepared.fit
            ) { [weak self] sent, total in
                DispatchQueue.main.async {
                    self?.printProgress = (sent: Int(sent), total: Int(total))
                }
            }

            if let temp = prepared.tempFile {
                try? FileManager.default.removeItem(atPath: temp)
            }

            if !success {
                await MainActor.run {
                    isPrinting = false
                    batchPrintTotal = 0
                    showStatus(L("print_failed_at", i + 1, count))
                }
                return
            }

            await refreshStatus()

            let remaining = await MainActor.run { filmRemaining }
            if remaining <= 0 && i < count - 1 {
                await MainActor.run {
                    isPrinting = false
                    batchPrintTotal = 0
                    showStatus(L("film_ran_out", i + 1, count))
                }
                return
            }
        }

        await MainActor.run {
            isPrinting = false
            printProgress = nil
            batchPrintTotal = 0
            showStatus(L("printed_n_images", count))
        }
    }

    // MARK: - Core Version

    func loadCoreVersion() {
        let bundle = Bundle.main
        let cliPath = bundle.path(forAuxiliaryExecutable: "instantlink-cli")
            ?? (bundle.executableURL?.deletingLastPathComponent().path ?? "") + "/instantlink-cli"
        DispatchQueue.global(qos: .utility).async { [weak self] in
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
                    DispatchQueue.main.async { self?.coreVersion = "?" }
                    return
                }
                if let output = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines),
                   !output.isEmpty {
                    let version = output.replacingOccurrences(of: "instantlink ", with: "v")
                    DispatchQueue.main.async { self?.coreVersion = version }
                } else {
                    DispatchQueue.main.async { self?.coreVersion = "?" }
                }
            } catch {
                DispatchQueue.main.async { self?.coreVersion = "?" }
            }
        }
    }

    // MARK: - Status Message

    func showStatus(_ message: String) {
        statusMessage = message
        DispatchQueue.main.asyncAfter(deadline: .now() + 4) { [weak self] in
            if self?.statusMessage == message { self?.statusMessage = nil }
        }
    }

    // MARK: - Update Checking

    static func compareVersions(_ a: String, _ b: String) -> Int {
        let partsA = a.split(separator: ".").compactMap { Int($0) }
        let partsB = b.split(separator: ".").compactMap { Int($0) }
        let count = max(partsA.count, partsB.count)
        for i in 0..<count {
            let va = i < partsA.count ? partsA[i] : 0
            let vb = i < partsB.count ? partsB[i] : 0
            if va < vb { return -1 }
            if va > vb { return 1 }
        }
        return 0
    }

    func checkForUpdates() async {
        guard let url = URL(string: "https://api.github.com/repos/wu-hongjun/InstantLink/releases/latest") else { return }
        var request = URLRequest(url: url)
        request.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
        request.timeoutInterval = 10

        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            guard let httpResponse = response as? HTTPURLResponse, httpResponse.statusCode == 200 else { return }
            guard let json = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let tagName = json["tag_name"] as? String,
                  let assets = json["assets"] as? [[String: Any]] else { return }

            let remoteVersion = tagName.hasPrefix("v") ? String(tagName.dropFirst()) : tagName
            let currentAppVersion = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "0.0.0"
            let currentCoreVersion = await MainActor.run {
                self.coreVersion.hasPrefix("v") ? String(self.coreVersion.dropFirst()) : self.coreVersion
            }

            // Update needed if either app or core version is behind
            let appBehind = Self.compareVersions(currentAppVersion, remoteVersion) < 0
            let coreBehind = Self.compareVersions(currentCoreVersion, remoteVersion) < 0

            guard appBehind || coreBehind else {
                await MainActor.run {
                    self.updateAvailable = nil
                    self.updateDownloadURL = nil
                }
                return
            }

            // Find the DMG asset
            let dmgAsset = assets.first { asset in
                guard let name = asset["name"] as? String else { return false }
                return name.hasSuffix(".dmg")
            }
            let downloadURL = dmgAsset?["browser_download_url"] as? String

            await MainActor.run {
                self.updateAvailable = remoteVersion
                self.updateDownloadURL = downloadURL
            }
        } catch {
            // Silent failure — don't bother the user with network errors
        }
    }

    func performUpdate() {
        guard let urlString = updateDownloadURL, let url = URL(string: urlString) else { return }
        isUpdating = true
        updateProgress = 0
        updateError = nil

        let delegate = UpdateDownloadDelegate { [weak self] progress in
            DispatchQueue.main.async { self?.updateProgress = progress }
        }

        let session = URLSession(configuration: .default, delegate: delegate, delegateQueue: nil)
        let task = session.downloadTask(with: url) { [weak self] tempURL, response, error in
            guard let self = self else { return }
            if let error = error {
                DispatchQueue.main.async {
                    self.isUpdating = false
                    self.updateError = error.localizedDescription
                }
                return
            }
            guard let tempURL = tempURL else {
                DispatchQueue.main.async {
                    self.isUpdating = false
                    self.updateError = "Download failed"
                }
                return
            }

            // Copy to a stable temp location (the original tempURL is deleted after this block)
            let dmgPath = NSTemporaryDirectory() + "InstantLink-update.dmg"
            try? FileManager.default.removeItem(atPath: dmgPath)
            do {
                try FileManager.default.copyItem(at: tempURL, to: URL(fileURLWithPath: dmgPath))
            } catch {
                DispatchQueue.main.async {
                    self.isUpdating = false
                    self.updateError = error.localizedDescription
                }
                return
            }

            self.installUpdate(dmgPath: dmgPath)
        }
        task.resume()
    }

    private func installUpdate(dmgPath: String) {
        DispatchQueue.main.async { [weak self] in self?.updateProgress = 1.0 }

        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self = self else { return }

            // Mount DMG
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
                self.failUpdate("Failed to mount DMG: \(error.localizedDescription)")
                return
            }

            guard mountProcess.terminationStatus == 0 else {
                self.failUpdate("Failed to mount DMG")
                return
            }

            let mountData = mountPipe.fileHandleForReading.readDataToEndOfFile()
            guard let plist = try? PropertyListSerialization.propertyList(from: mountData, format: nil) as? [String: Any],
                  let entities = plist["system-entities"] as? [[String: Any]],
                  let mountPoint = entities.compactMap({ $0["mount-point"] as? String }).first else {
                self.failUpdate("Could not determine mount point")
                return
            }

            defer {
                // Unmount
                let detach = Process()
                detach.executableURL = URL(fileURLWithPath: "/usr/bin/hdiutil")
                detach.arguments = ["detach", mountPoint, "-quiet"]
                try? detach.run()
                detach.waitUntilExit()
                try? FileManager.default.removeItem(atPath: dmgPath)
            }

            // Find .app in mounted volume
            guard let contents = try? FileManager.default.contentsOfDirectory(atPath: mountPoint),
                  let appName = contents.first(where: { $0.hasSuffix(".app") }) else {
                self.failUpdate("No .app found in DMG")
                return
            }

            let sourceApp = (mountPoint as NSString).appendingPathComponent(appName)
            let tempApp = NSTemporaryDirectory() + "InstantLink-update.app"
            let currentApp = Bundle.main.bundlePath

            // Copy to temp
            try? FileManager.default.removeItem(atPath: tempApp)
            do {
                try FileManager.default.copyItem(atPath: sourceApp, toPath: tempApp)
            } catch {
                self.failUpdate("Failed to copy app: \(error.localizedDescription)")
                return
            }

            // Atomic swap: current → .old, temp → current, remove .old
            let oldApp = currentApp + ".old"
            try? FileManager.default.removeItem(atPath: oldApp)
            do {
                try FileManager.default.moveItem(atPath: currentApp, toPath: oldApp)
                try FileManager.default.moveItem(atPath: tempApp, toPath: currentApp)
                try? FileManager.default.removeItem(atPath: oldApp)
            } catch {
                // Try to restore
                try? FileManager.default.moveItem(atPath: oldApp, toPath: currentApp)
                self.failUpdate("Failed to install update: \(error.localizedDescription)")
                return
            }

            // Relaunch
            let relaunch = Process()
            relaunch.executableURL = URL(fileURLWithPath: "/usr/bin/open")
            relaunch.arguments = [currentApp]
            try? relaunch.run()

            DispatchQueue.main.async {
                NSApplication.shared.terminate(nil)
            }
        }
    }

    private func failUpdate(_ message: String) {
        DispatchQueue.main.async {
            self.isUpdating = false
            self.updateError = message
        }
    }
}

// MARK: - Update Download Delegate

class UpdateDownloadDelegate: NSObject, URLSessionDownloadDelegate {
    let onProgress: (Double) -> Void

    init(onProgress: @escaping (Double) -> Void) {
        self.onProgress = onProgress
    }

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didWriteData bytesWritten: Int64, totalBytesWritten: Int64, totalBytesExpectedToWrite: Int64) {
        guard totalBytesExpectedToWrite > 0 else { return }
        onProgress(Double(totalBytesWritten) / Double(totalBytesExpectedToWrite))
    }

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didFinishDownloadingTo location: URL) {
        // Handled in the completion handler of downloadTask
    }
}

// MARK: - Camera Photo Capture Delegate

class CameraPhotoCaptureDelegate: NSObject, AVCapturePhotoCaptureDelegate {
    private let completion: (NSImage?) -> Void

    init(completion: @escaping (NSImage?) -> Void) {
        self.completion = completion
    }

    func photoOutput(_ output: AVCapturePhotoOutput, didFinishProcessingPhoto photo: AVCapturePhoto, error: Error?) {
        guard error == nil,
              let data = photo.fileDataRepresentation(),
              let image = NSImage(data: data)
        else {
            completion(nil)
            return
        }
        completion(image)
    }
}

// MARK: - Film Frame View

struct FilmFrameView<Content: View>: View {
    let filmModel: String?   // "Mini", "Sqre", "Wide", or nil
    let isRotated: Bool
    let content: () -> Content

    // Proportions relative to image area height
    private var topBorder: CGFloat { 0.129 }
    private var bottomBorder: CGFloat { 0.258 }
    private var sideBorder: CGFloat { 0.087 }

    init(filmModel: String?, isRotated: Bool, @ViewBuilder content: @escaping () -> Content) {
        self.filmModel = filmModel
        self.isRotated = isRotated
        self.content = content
    }

    private var imageAR: CGFloat {
        switch filmModel {
        case "Mini": return 46.0 / 62.0
        case "Wide": return 99.0 / 62.0
        default:     return 1.0
        }
    }

    private func layout(availW: CGFloat, availH: CGFloat) -> (cardW: CGFloat, cardH: CGFloat, imgW: CGFloat, imgH: CGFloat, offsetX: CGFloat, offsetY: CGFloat) {
        let tb = topBorder
        let bb = bottomBorder
        let sb = sideBorder
        let iar = imageAR

        let cardH_ratio = tb + 1.0 + bb
        let cardW_ratio = iar + 2.0 * sb
        let cardAR = cardW_ratio / cardH_ratio
        let effectiveCardAR = isRotated ? (1.0 / cardAR) : cardAR

        let fitW: CGFloat
        let fitH: CGFloat
        if availH > 0 && availW / availH > effectiveCardAR {
            fitH = availH
            fitW = availH * effectiveCardAR
        } else {
            fitW = availW
            fitH = availW > 0 && effectiveCardAR > 0 ? availW / effectiveCardAR : availH
        }

        let divisor = isRotated ? cardW_ratio : cardH_ratio
        let imageAreaH = divisor > 0 ? fitH / divisor : fitH
        let imageAreaW = imageAreaH * iar

        let imgW = isRotated ? imageAreaH : imageAreaW
        let imgH = isRotated ? imageAreaW : imageAreaH

        // Offset the image within the card to create the asymmetric thick border.
        // Non-rotated: thick border at bottom → shift image up (negative Y).
        // Rotated: thick border moves to the right → shift image left (negative X).
        let borderDelta = (tb - bb) / cardH_ratio / 2
        let offsetX = isRotated ? borderDelta * fitW : CGFloat(0)
        let offsetY = isRotated ? CGFloat(0) : borderDelta * fitH

        return (fitW, fitH, imgW, imgH, offsetX, offsetY)
    }

    var body: some View {
        if filmModel != nil {
            GeometryReader { geo in
                let l = layout(availW: geo.size.width, availH: geo.size.height)
                ZStack {
                    RoundedRectangle(cornerRadius: 3)
                        .fill(Color.white)
                        .frame(width: l.cardW, height: l.cardH)
                        .shadow(color: .black.opacity(0.15), radius: 4, y: 2)

                    content()
                        .frame(width: l.imgW, height: l.imgH)
                        .clipped()
                        .offset(x: l.offsetX, y: l.offsetY)
                }
                .position(x: geo.size.width / 2, y: geo.size.height / 2)
            }
        } else {
            content()
        }
    }
}

// MARK: - Camera Preview View (NSViewRepresentable)

class CameraPreviewNSView: NSView {
    let previewLayer = AVCaptureVideoPreviewLayer()

    override init(frame: CGRect) {
        super.init(frame: frame)
        wantsLayer = true
        layer?.addSublayer(previewLayer)
    }

    required init?(coder: NSCoder) { fatalError() }

    override func layout() {
        super.layout()
        previewLayer.frame = bounds
    }
}

struct CameraPreviewView: NSViewRepresentable {
    let session: AVCaptureSession
    var isMirrored: Bool = false

    func makeNSView(context: Context) -> CameraPreviewNSView {
        let view = CameraPreviewNSView()
        view.previewLayer.session = session
        view.previewLayer.videoGravity = .resizeAspectFill
        if let connection = view.previewLayer.connection {
            connection.automaticallyAdjustsVideoMirroring = false
            connection.isVideoMirrored = isMirrored
        }
        return view
    }

    func updateNSView(_ nsView: CameraPreviewNSView, context: Context) {
        nsView.previewLayer.session = session
        if let connection = nsView.previewLayer.connection {
            connection.automaticallyAdjustsVideoMirroring = false
            connection.isVideoMirrored = isMirrored
        }
    }
}

// MARK: - Camera View

struct CameraView: View {
    @EnvironmentObject var viewModel: ViewModel
    @State private var showFlash = false

    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 8)
                .fill(Color(nsColor: .controlBackgroundColor))
            RoundedRectangle(cornerRadius: 8)
                .strokeBorder(style: StrokeStyle(lineWidth: 2), antialiased: true)
                .foregroundColor(.secondary.opacity(0.5))

            if viewModel.cameraState == .viewfinder {
                if let session = viewModel.captureSession {
                    let isFront = viewModel.selectedCamera?.position == .front
                    FilmFrameView(filmModel: viewModel.printerModelTag,
                                  isRotated: viewModel.filmOrientation == "rotated") {
                        if let ar = viewModel.orientedAspectRatio {
                            CameraPreviewView(session: session, isMirrored: isFront)
                                .aspectRatio(ar, contentMode: .fill)
                                .overlay(alignment: stampAlignmentFor(viewModel)) {
                                    DateStampOverlayView()
                                }
                                .clipped()
                        } else {
                            CameraPreviewView(session: session, isMirrored: isFront)
                        }
                    }
                    .padding(4)

                    // Countdown overlay
                    if let count = viewModel.timerCountdown, count > 0 {
                        Text("\(count)")
                            .font(.system(size: 72, weight: .bold, design: .rounded))
                            .foregroundColor(.white)
                            .shadow(color: .black.opacity(0.5), radius: 8)
                            .transition(.scale.combined(with: .opacity))
                            .animation(.easeInOut(duration: 0.3), value: count)
                    }
                } else {
                    VStack(spacing: 8) {
                        Image(systemName: "camera.badge.ellipsis")
                            .font(.largeTitle)
                            .foregroundColor(.secondary)
                        Text(L("No camera available"))
                            .font(.callout)
                            .foregroundColor(.secondary)
                    }
                }
            } else if let image = viewModel.capturedImage {
                FilmFrameView(filmModel: viewModel.printerModelTag,
                              isRotated: viewModel.filmOrientation == "rotated") {
                    if let ar = viewModel.orientedAspectRatio {
                        Image(nsImage: image)
                            .resizable()
                            .aspectRatio(contentMode: .fill)
                            .frame(maxWidth: .infinity, maxHeight: .infinity)
                            .aspectRatio(ar, contentMode: .fit)
                            .overlay(alignment: stampAlignmentFor(viewModel)) {
                                DateStampOverlayView()
                            }
                            .clipped()
                    } else {
                        Image(nsImage: image)
                            .resizable()
                            .aspectRatio(contentMode: .fit)
                    }
                }
                .padding(4)
            }
        }
        .overlay(showFlash ? Color.white.opacity(0.8) : Color.clear)
        .frame(minHeight: 120, maxHeight: .infinity)
        .onChange(of: viewModel.cameraState) { newState in
            if newState == .preview {
                showFlash = true
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) {
                    withAnimation(.easeOut(duration: 0.2)) { showFlash = false }
                }
            }
        }
    }
}

// MARK: - Camera Actions View

struct CameraActionsView: View {
    @EnvironmentObject var viewModel: ViewModel

    var body: some View {
        VStack(spacing: 10) {
            if viewModel.cameraState == .viewfinder {
                // All controls in one row: [Camera picker] [Timer] [Orientation]
                HStack(spacing: 8) {
                    if viewModel.availableCameras.count > 1 {
                        Picker(L("Camera"), selection: Binding(
                            get: { viewModel.selectedCamera?.uniqueID ?? "" },
                            set: { id in
                                if let device = viewModel.availableCameras.first(where: { $0.uniqueID == id }) {
                                    viewModel.switchCamera(to: device)
                                }
                            }
                        )) {
                            ForEach(viewModel.availableCameras, id: \.uniqueID) { device in
                                Text(device.localizedName).tag(device.uniqueID)
                            }
                        }
                        .labelsHidden()
                    }

                    Picker(L("Timer"), selection: $viewModel.timerMode) {
                        Text(L("Off")).tag(0)
                        Text("2s").tag(2)
                        Text("10s").tag(10)
                    }
                    .pickerStyle(.segmented)
                    .labelsHidden()
                    .frame(maxWidth: 140)

                    if viewModel.printerAspectRatio != nil {
                        Button {
                            viewModel.filmOrientation = viewModel.filmOrientation == "default" ? "rotated" : "default"
                        } label: {
                            Image(systemName: viewModel.filmOrientation == "default"
                                ? "rectangle.portrait.arrowtriangle.2.outward" : "rectangle.landscape.arrowtriangle.2.outward")
                                .font(.callout)
                        }
                        .help(L("Film Orientation"))
                    }
                }

                if viewModel.timerCountdown != nil {
                    Button {
                        viewModel.cancelTimer()
                    } label: {
                        HStack {
                            Image(systemName: "xmark")
                            Text(L("Cancel"))
                        }
                        .frame(maxWidth: .infinity)
                    }
                    .controlSize(.large)
                } else {
                    HStack(spacing: 10) {
                        Button {
                            viewModel.autoPrintAfterCapture = false
                            viewModel.captureWithTimer()
                        } label: {
                            HStack {
                                Image(systemName: "camera.shutter.button")
                                Text(L("Capture"))
                            }
                            .frame(maxWidth: .infinity)
                        }
                        .controlSize(.large)
                        .disabled(viewModel.captureSession == nil)

                        Button {
                            viewModel.autoPrintAfterCapture = true
                            viewModel.captureWithTimer()
                        } label: {
                            HStack {
                                Image(systemName: "printer.fill")
                                Text(L("Capture & Print"))
                            }
                            .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.borderedProminent)
                        .controlSize(.large)
                        .disabled(viewModel.captureSession == nil || viewModel.printerName == nil)
                    }
                }
            } else {
                HStack(spacing: 10) {
                    Button {
                        viewModel.retakePhoto()
                    } label: {
                        HStack {
                            Image(systemName: "arrow.counterclockwise")
                            Text(L("Retake"))
                        }
                        .frame(maxWidth: .infinity)
                    }
                    .controlSize(.large)

                    Button {
                        if viewModel.commitCapture() {
                            viewModel.showImageEditor = true
                        }
                    } label: {
                        HStack {
                            Image(systemName: "slider.horizontal.3")
                            Text(L("Edit Image"))
                        }
                        .frame(maxWidth: .infinity)
                    }
                    .controlSize(.large)

                    Button {
                        if viewModel.commitCapture() {
                            Task { await viewModel.printSelectedImage() }
                        }
                    } label: {
                        HStack {
                            Image(systemName: "printer.fill")
                            Text(L("Print"))
                        }
                        .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
                }
            }
        }
    }
}

// MARK: - Main Window View

struct MainView: View {
    @EnvironmentObject var viewModel: ViewModel

    var body: some View {
        VStack(spacing: 0) {
            // Update banner
            if let error = viewModel.updateError {
                HStack(spacing: 6) {
                    Image(systemName: "exclamationmark.triangle")
                        .font(.caption)
                    Text(L("update_failed", error))
                        .font(.caption)
                        .lineLimit(1)
                    Spacer()
                    Button(L("Dismiss")) {
                        viewModel.updateError = nil
                    }
                    .font(.caption)
                    .buttonStyle(.plain)
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 6)
                .background(Color.red.opacity(0.15))
            } else if viewModel.isUpdating {
                HStack(spacing: 6) {
                    if viewModel.updateProgress >= 1.0 {
                        ProgressView()
                            .controlSize(.small)
                        Text(L("Installing update..."))
                            .font(.caption)
                    } else {
                        Image(systemName: "arrow.down.circle")
                            .font(.caption)
                        Text(L("Downloading update..."))
                            .font(.caption)
                        Spacer()
                        ProgressView(value: viewModel.updateProgress)
                            .frame(width: 60)
                        Text("\(Int(viewModel.updateProgress * 100))%")
                            .font(.caption)
                            .monospacedDigit()
                    }
                    Spacer()
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 6)
                .background(Color.blue.opacity(0.1))
            } else if let version = viewModel.updateAvailable {
                HStack(spacing: 6) {
                    Image(systemName: "arrow.up.circle.fill")
                        .font(.caption)
                        .foregroundColor(.blue)
                    Text(L("update_available_version", version))
                        .font(.caption)
                    Spacer()
                    Button(L("Update Now")) {
                        viewModel.performUpdate()
                    }
                    .font(.caption)
                    .buttonStyle(.borderedProminent)
                    .controlSize(.small)
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 6)
                .background(Color.blue.opacity(0.1))
            }

            if viewModel.isConnected {
                // Status bar
                HStack(spacing: 12) {
                    Button {
                        if let bleId = viewModel.printerName,
                           let profile = viewModel.printerProfiles[bleId] {
                            viewModel.editingProfile = profile
                            viewModel.showProfileEditor = true
                        }
                    } label: {
                        HStack(spacing: 4) {
                            Circle()
                                .fill(.green)
                                .frame(width: 8, height: 8)
                            Text(viewModel.currentPrinterDisplayName ?? L("Connected"))
                                .font(.caption)
                                .fontWeight(.medium)
                            if let tag = viewModel.printerModelTag {
                                Text(tag)
                                    .font(.system(size: 9, weight: .semibold))
                                    .foregroundColor(.white)
                                    .padding(.horizontal, 5)
                                    .padding(.vertical, 1)
                                    .background(Capsule().fill(.secondary))
                            }
                        }
                    }
                    .buttonStyle(.plain)

                    Button {
                        viewModel.showPrinterPicker = true
                    } label: {
                        Image(systemName: "chevron.down")
                            .font(.system(size: 8, weight: .semibold))
                            .foregroundColor(.secondary)
                            .frame(width: 16, height: 16)
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)

                    Spacer()

                    Picker("", selection: $viewModel.captureMode) {
                        Image(systemName: "photo.on.rectangle").tag(CaptureMode.file)
                        Image(systemName: "camera").tag(CaptureMode.camera)
                    }
                    .pickerStyle(.segmented)
                    .labelsHidden()
                    .frame(width: 60)
                    .onChange(of: viewModel.captureMode) { newMode in
                        if newMode == .camera {
                            viewModel.requestCameraAccessAndStart()
                        } else {
                            viewModel.cancelTimer()
                            viewModel.stopCameraSession()
                            viewModel.cameraState = .viewfinder
                            viewModel.capturedImage = nil
                        }
                    }

                    Button {
                        Task { await viewModel.refreshStatus() }
                    } label: {
                        StatusItem(
                            icon: viewModel.isCharging ? "battery.100.bolt" : "battery.100",
                            value: viewModel.isCharging ? L("Charging") : L("battery_percent", viewModel.battery)
                        )
                    }
                    .buttonStyle(.plain)
                    .disabled(viewModel.isRefreshing)

                    Button {
                        Task { await viewModel.refreshStatus() }
                    } label: {
                        StatusItem(icon: "film", value: L("film_remaining", viewModel.filmRemaining))
                    }
                    .buttonStyle(.plain)
                    .disabled(viewModel.isRefreshing)

                    Button {
                        viewModel.showSettings = true
                    } label: {
                        Image(systemName: "gearshape")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                    .buttonStyle(.plain)

                    if let msg = viewModel.statusMessage {
                        Text(msg)
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 10)

                Divider()

                if viewModel.captureMode == .file {
                    MainPreviewView(openEditor: { viewModel.showImageEditor = true })
                        .padding(.horizontal, 14)
                        .padding(.top, 16)
                        .layoutPriority(-1)
                } else {
                    CameraView()
                        .padding(.horizontal, 14)
                        .padding(.top, 16)
                        .layoutPriority(-1)
                }

                if viewModel.captureMode == .file && viewModel.queue.count > 1 {
                    QueueStripView()
                        .padding(.horizontal, 14)
                        .padding(.top, 8)
                }

                Spacer(minLength: 0)

                if viewModel.captureMode == .file {
                    MainActionsView(openEditor: { viewModel.showImageEditor = true })
                        .padding(.horizontal, 14)
                        .padding(.vertical, 12)
                } else {
                    CameraActionsView()
                        .padding(.horizontal, 14)
                        .padding(.vertical, 12)
                }

            } else {
                // Disconnected — pairing mode
                ZStack(alignment: .topTrailing) {
                    Button {
                        viewModel.showSettings = true
                    } label: {
                        Image(systemName: "gearshape")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                    .buttonStyle(.plain)
                    .padding(10)

                VStack(spacing: 16) {
                    Spacer()

                    if viewModel.isPairing {
                        // Active pairing
                        ProgressView()
                            .controlSize(.regular)
                        Text(viewModel.pairingStatus)
                            .font(.callout)
                            .foregroundColor(.secondary)
                        VStack(alignment: .leading, spacing: 4) {
                            Label(L("Make sure your printer is turned on"), systemImage: "1.circle")
                            Label(L("Press the button to enable Bluetooth"), systemImage: "2.circle")
                            Label(L("Keep the printer nearby"), systemImage: "3.circle")
                        }
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .padding(.vertical, 4)
                        Button(L("Cancel")) {
                            viewModel.stopPairing()
                        }
                        .controlSize(.large)
                    } else if viewModel.hasSearchedOnce {
                        // Pairing cancelled / failed
                        Image(systemName: "printer.dotmatrix")
                            .font(.system(size: 40))
                            .foregroundColor(.secondary)
                        Text(L("No printer found"))
                            .font(.headline)
                        VStack(alignment: .leading, spacing: 4) {
                            Label(L("Make sure your printer is turned on"), systemImage: "1.circle")
                            Label(L("Press the button to enable Bluetooth"), systemImage: "2.circle")
                            Label(L("Keep the printer nearby"), systemImage: "3.circle")
                        }
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .padding(.vertical, 4)
                        Button(L("Try Again")) {
                            viewModel.startPairing()
                        }
                        .controlSize(.large)
                    } else {
                        // First launch
                        Image(systemName: "printer.dotmatrix")
                            .font(.system(size: 40))
                            .foregroundColor(.secondary)
                        Text(L("Connect to your printer"))
                            .font(.headline)
                        VStack(alignment: .leading, spacing: 4) {
                            Label(L("Turn on your Instax printer"), systemImage: "1.circle")
                            Label(L("Press the button to enable Bluetooth"), systemImage: "2.circle")
                        }
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .padding(.vertical, 4)
                        Button(L("Find Printer")) {
                            viewModel.startPairing()
                        }
                        .buttonStyle(.borderedProminent)
                        .controlSize(.large)
                    }

                    Spacer()
                }
                .frame(maxWidth: .infinity)
                .padding(.horizontal, 14)
                } // ZStack
            }
        }
        .frame(minWidth: 240, idealWidth: 260, minHeight: 380)
        .navigationTitle("InstantLink v\(Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "0.0.0")")
        .sheet(isPresented: $viewModel.showPrinterPicker) {
            PrinterPickerSheet()
                .environmentObject(viewModel)
        }
        .sheet(isPresented: $viewModel.showProfileSheet) {
            PrinterProfileSheet(isPostPairing: true)
                .environmentObject(viewModel)
        }
        .sheet(isPresented: $viewModel.showProfileEditor) {
            PrinterProfileSheet(isPostPairing: false)
                .environmentObject(viewModel)
        }
        .sheet(isPresented: $viewModel.showSettings) {
            SettingsView()
                .environmentObject(viewModel)
        }
        .sheet(isPresented: $viewModel.showImageEditor) {
            ImageEditorView()
                .environmentObject(viewModel)
        }
        .onAppear {
            // Auto-start pairing on launch
            if !viewModel.isConnected && !viewModel.isPairing {
                viewModel.startPairing()
            }
            // Silent update check on launch
            Task { await viewModel.checkForUpdates() }
        }
        .onReceive(NotificationCenter.default.publisher(for: .findPrinter)) { _ in
            NSApplication.shared.activate(ignoringOtherApps: true)
            for window in NSApplication.shared.windows where window.canBecomeMain {
                window.makeKeyAndOrderFront(nil)
            }
            viewModel.showPrinterPicker = true
        }
        .onReceive(NotificationCenter.default.publisher(for: .refreshStatus)) { _ in
            Task { await viewModel.refreshStatus() }
        }
        .onReceive(NotificationCenter.default.publisher(for: .openSettings)) { _ in
            viewModel.showSettings = true
        }
        .onReceive(NotificationCenter.default.publisher(for: .checkForUpdates)) { _ in
            Task { await viewModel.checkForUpdates() }
        }
        .onDisappear {
            if viewModel.captureMode == .camera {
                viewModel.stopCameraSession()
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didResignActiveNotification)) { _ in
            if let session = viewModel.captureSession {
                DispatchQueue.global(qos: .userInitiated).async {
                    session.stopRunning()
                }
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in
            if viewModel.captureMode == .camera, let session = viewModel.captureSession, !session.isRunning {
                DispatchQueue.global(qos: .userInitiated).async {
                    session.startRunning()
                }
            }
        }
    }
}

// MARK: - Status Item

struct StatusItem: View {
    let icon: String
    let value: String

    var body: some View {
        HStack(spacing: 4) {
            Image(systemName: icon)
                .font(.caption)
                .foregroundColor(.secondary)
            Text(value)
                .font(.caption)
                .fontWeight(.medium)
        }
    }
}

// MARK: - Preference Keys

private struct CropFrameSizeKey: PreferenceKey {
    static var defaultValue: CGSize = .zero
    static func reduce(value: inout CGSize, nextValue: () -> CGSize) {
        value = nextValue()
    }
}

// MARK: - Date Stamp Overlay (shared)

struct DateStampOverlayView: View {
    @EnvironmentObject var viewModel: ViewModel
    var digitHeight: CGFloat = 11

    var body: some View {
        if viewModel.dateStampEnabled {
            FontStampView(viewModel: viewModel, digitHeight: digitHeight)
                .padding(4)
        }
    }
}

struct FontStampView: View {
    @ObservedObject var viewModel: ViewModel
    var digitHeight: CGFloat = 11

    var body: some View {
        let date = viewModel.imageDate ?? Date()
        let preset = ViewModel.dateStampPresets[viewModel.dateStampStyle]
            ?? ViewModel.dateStampPresets["classic"]!
        let (r, g, b) = preset.color
        let stampColor = Color(red: r, green: g, blue: b)

        VStack(alignment: .leading, spacing: digitHeight * 0.15) {
            Text(viewModel.dateStampText(from: date))
                .font(.custom(preset.fontFamily, size: digitHeight))
                .tracking(digitHeight * preset.tracking)
                .foregroundColor(stampColor)
            if viewModel.showTimeRow {
                Text(viewModel.timeStampText(from: date))
                    .font(.custom(preset.fontFamily, size: digitHeight))
                    .tracking(digitHeight * preset.tracking)
                    .foregroundColor(stampColor)
            }
        }
        .shadow(
            color: viewModel.lightBleedEnabled && preset.glowRadius > 0
                ? stampColor.opacity(0.8) : .clear,
            radius: viewModel.lightBleedEnabled ? digitHeight * preset.glowRadius * 0.5 : 0
        )
    }
}

struct PresetCard: View {
    let preset: ViewModel.DateStampPreset
    let isSelected: Bool
    var body: some View {
        Text(L(preset.displayName))
            .font(.system(size: 9, weight: .medium))
            .foregroundColor(isSelected ? Color(red: preset.color.0, green: preset.color.1, blue: preset.color.2) : .secondary)
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(RoundedRectangle(cornerRadius: 4).fill(
                isSelected ? Color(red: preset.color.0, green: preset.color.1, blue: preset.color.2).opacity(0.15) : Color.clear
            ))
            .overlay(RoundedRectangle(cornerRadius: 4).stroke(
                isSelected ? Color(red: preset.color.0, green: preset.color.1, blue: preset.color.2).opacity(0.5) : Color.gray.opacity(0.3), lineWidth: 1
            ))
    }
}

// MARK: - Stamp Alignment Helper

private var stampAlignmentFor: (ViewModel) -> Alignment = { viewModel in
    switch viewModel.dateStampPosition {
    case "topLeft": return .topLeading
    case "topRight": return .topTrailing
    case "bottomLeft": return .bottomLeading
    default: return .bottomTrailing
    }
}

// MARK: - Main Preview View (read-only, in main window)

struct MainPreviewView: View {
    @EnvironmentObject var viewModel: ViewModel
    @State private var isTargeted = false
    @GestureState private var dragDelta: CGSize = .zero
    @GestureState private var magnifyDelta: CGFloat = 1.0
    @State private var localFrameSize: CGSize = .zero
    var openEditor: () -> Void

    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 8)
                .fill(Color(nsColor: .controlBackgroundColor))
            RoundedRectangle(cornerRadius: 8)
                .strokeBorder(
                    style: StrokeStyle(
                        lineWidth: 2,
                        dash: viewModel.selectedImage == nil ? [6] : []
                    ),
                    antialiased: true
                )
                .foregroundColor(isTargeted ? .accentColor : .secondary.opacity(0.5))

            if viewModel.isPrinting {
                VStack(spacing: 8) {
                    if viewModel.batchPrintTotal > 1 {
                        Text(L("printing_n_of_m", viewModel.batchPrintIndex, viewModel.batchPrintTotal))
                            .font(.caption)
                            .fontWeight(.medium)
                            .foregroundColor(.secondary)
                    }
                    if let p = viewModel.printProgress {
                        ProgressView(value: Double(p.sent), total: Double(p.total))
                            .progressViewStyle(.linear)
                            .frame(width: 120)
                        Text(L("transfer_progress", p.sent, p.total))
                            .font(.caption)
                            .foregroundColor(.secondary)
                    } else {
                        ProgressView().controlSize(.regular)
                        Text(L("Preparing..."))
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                }
            } else if let image = viewModel.selectedImage {
                ZStack(alignment: .topTrailing) {
                    FilmFrameView(filmModel: viewModel.printerModelTag,
                                  isRotated: viewModel.filmOrientation == "rotated") {
                        if viewModel.fitMode == "crop", let ar = viewModel.orientedAspectRatio {
                            Color.clear
                                .aspectRatio(ar, contentMode: .fit)
                                .background(
                                    GeometryReader { geo in
                                        Color.clear.preference(key: CropFrameSizeKey.self, value: geo.size)
                                    }
                                )
                                .onPreferenceChange(CropFrameSizeKey.self) { size in
                                    localFrameSize = size
                                    viewModel.cropFrameSize = size
                                }
                                .overlay(
                                    Image(nsImage: image)
                                        .resizable()
                                        .aspectRatio(contentMode: .fill)
                                        .scaleEffect(effectiveZoom)
                                        .offset(effectiveOffset(imageSize: image.size))
                                        .rotationEffect(.degrees(Double(viewModel.rotationAngle)))
                                )
                                .overlay(alignment: stampAlignmentFor(viewModel)) {
                                    DateStampOverlayView()
                                }
                                .clipped()
                                .contentShape(Rectangle())
                                .gesture(
                                    DragGesture()
                                        .updating($dragDelta) { value, state, _ in
                                            state = value.translation
                                        }
                                        .onEnded { value in
                                            let raw = CGSize(
                                                width: viewModel.cropOffset.width + value.translation.width,
                                                height: viewModel.cropOffset.height + value.translation.height
                                            )
                                            viewModel.cropOffset = clampedOffset(
                                                raw: raw,
                                                imageSize: image.size,
                                                frameSize: localFrameSize,
                                                zoom: viewModel.cropZoom
                                            )
                                        }
                                )
                                .simultaneousGesture(
                                    MagnificationGesture()
                                        .updating($magnifyDelta) { value, state, _ in
                                            state = value
                                        }
                                        .onEnded { value in
                                            let newZoom = min(max(viewModel.cropZoom * value, 1.0), 5.0)
                                            viewModel.cropZoom = newZoom
                                            viewModel.cropOffset = clampedOffset(
                                                raw: viewModel.cropOffset,
                                                imageSize: image.size,
                                                frameSize: localFrameSize,
                                                zoom: newZoom
                                            )
                                        }
                                )
                                .onTapGesture(count: 2) { openEditor() }
                        } else if viewModel.fitMode == "contain", let ar = viewModel.orientedAspectRatio {
                            Color.white
                                .aspectRatio(ar, contentMode: .fit)
                                .overlay(
                                    Image(nsImage: image)
                                        .resizable()
                                        .aspectRatio(contentMode: .fit)
                                        .rotationEffect(.degrees(Double(viewModel.rotationAngle)))
                                )
                                .overlay(alignment: stampAlignmentFor(viewModel)) {
                                    DateStampOverlayView()
                                }
                                .clipped()
                                .onTapGesture(count: 2) { openEditor() }
                        } else {
                            Image(nsImage: image)
                                .resizable()
                                .aspectRatio(contentMode: .fit)
                                .rotationEffect(.degrees(Double(viewModel.rotationAngle)))
                                .overlay(alignment: stampAlignmentFor(viewModel)) {
                                    DateStampOverlayView()
                                }
                                .onTapGesture(count: 2) { openEditor() }
                        }
                    }
                    .padding(4)

                    Button { viewModel.clearImage() } label: {
                        Image(systemName: "xmark.circle.fill")
                            .font(.title3)
                            .symbolRenderingMode(.hierarchical)
                            .foregroundColor(.secondary)
                    }
                    .buttonStyle(.plain)
                    .padding(8)
                }
            } else {
                VStack(spacing: 8) {
                    Image(systemName: "photo.on.rectangle.angled")
                        .font(.largeTitle)
                        .foregroundColor(.secondary)
                    Text(L("Drop images or click Open File"))
                        .font(.callout)
                        .foregroundColor(.secondary)
                    Button(L("Open File")) { viewModel.selectImage() }
                        .controlSize(.small)
                }
            }
        }
        .frame(minHeight: 120, maxHeight: .infinity)
        .onDrop(of: [.fileURL], isTargeted: $isTargeted) { providers in
            guard !providers.isEmpty else { return false }
            for provider in providers {
                _ = provider.loadObject(ofClass: URL.self) { url, _ in
                    guard let url = url else { return }
                    DispatchQueue.main.async { viewModel.addImages(from: [url]) }
                }
            }
            return true
        }
    }

    // MARK: - Crop gesture helpers

    private var effectiveZoom: CGFloat {
        min(max(viewModel.cropZoom * magnifyDelta, 1.0), 5.0)
    }

    private func effectiveOffset(imageSize: CGSize) -> CGSize {
        let raw = CGSize(
            width: viewModel.cropOffset.width + dragDelta.width,
            height: viewModel.cropOffset.height + dragDelta.height
        )
        return clampedOffset(raw: raw, imageSize: imageSize, frameSize: localFrameSize, zoom: effectiveZoom)
    }

    private func maxOffset(imageSize: CGSize, frameSize: CGSize, zoom: CGFloat) -> CGSize {
        guard frameSize.width > 0, frameSize.height > 0 else { return .zero }
        let imageAR = imageSize.width / imageSize.height
        let frameAR = frameSize.width / frameSize.height

        let displayW: CGFloat
        let displayH: CGFloat
        if imageAR > frameAR {
            displayH = frameSize.height
            displayW = frameSize.height * imageAR
        } else {
            displayW = frameSize.width
            displayH = frameSize.width / imageAR
        }

        return CGSize(
            width: max(0, (displayW * zoom - frameSize.width) / 2),
            height: max(0, (displayH * zoom - frameSize.height) / 2)
        )
    }

    private func clampedOffset(raw: CGSize, imageSize: CGSize, frameSize: CGSize, zoom: CGFloat) -> CGSize {
        let maxOff = maxOffset(imageSize: imageSize, frameSize: frameSize, zoom: zoom)
        return CGSize(
            width: min(max(raw.width, -maxOff.width), maxOff.width),
            height: min(max(raw.height, -maxOff.height), maxOff.height)
        )
    }
}

// MARK: - Queue Strip View

struct QueueStripView: View {
    @EnvironmentObject var viewModel: ViewModel
    @State private var draggingItemID: UUID?

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    ForEach(Array(viewModel.queue.enumerated()), id: \.element.id) { index, item in
                        QueueThumbnailView(
                            item: item,
                            isSelected: index == viewModel.selectedQueueIndex,
                            isDragging: draggingItemID == item.id
                        )
                        .id(item.id)
                        .onTapGesture { viewModel.selectQueueItem(at: index) }
                        .contextMenu {
                            if index > 0 {
                                Button(L("Move Left")) {
                                    withAnimation { viewModel.moveQueueItem(from: index, to: index - 1) }
                                }
                            }
                            if index < viewModel.queue.count - 1 {
                                Button(L("Move Right")) {
                                    withAnimation { viewModel.moveQueueItem(from: index, to: index + 1) }
                                }
                            }
                            Divider()
                            Button(L("Remove")) {
                                withAnimation { viewModel.removeQueueItem(at: index) }
                            }
                        }
                        .onDrag {
                            draggingItemID = item.id
                            return NSItemProvider(object: item.id.uuidString as NSString)
                        }
                        .onDrop(of: [.text], delegate: QueueDropDelegate(
                            targetIndex: index,
                            viewModel: viewModel,
                            draggingItemID: $draggingItemID
                        ))
                    }

                    // Add button
                    Button { viewModel.selectImage() } label: {
                        Image(systemName: "plus")
                            .font(.system(size: 14, weight: .medium))
                            .foregroundColor(.secondary)
                            .frame(width: 36, height: 44)
                            .background(RoundedRectangle(cornerRadius: 4).strokeBorder(style: StrokeStyle(lineWidth: 1, dash: [3])).foregroundColor(.secondary.opacity(0.4)))
                    }
                    .buttonStyle(.plain)
                }
                .padding(.horizontal, 4)
                .padding(.vertical, 4)
            }
            .onChange(of: viewModel.selectedQueueIndex) { _ in
                if viewModel.queue.indices.contains(viewModel.selectedQueueIndex) {
                    withAnimation(.easeInOut(duration: 0.2)) {
                        proxy.scrollTo(viewModel.queue[viewModel.selectedQueueIndex].id, anchor: .center)
                    }
                }
            }
        }
    }
}

struct QueueDropDelegate: DropDelegate {
    let targetIndex: Int
    let viewModel: ViewModel
    @Binding var draggingItemID: UUID?

    func performDrop(info: DropInfo) -> Bool {
        draggingItemID = nil
        return true
    }

    func dropEntered(info: DropInfo) {
        guard let dragID = draggingItemID,
              let sourceIndex = viewModel.queue.firstIndex(where: { $0.id == dragID }),
              sourceIndex != targetIndex else { return }
        withAnimation(.easeInOut(duration: 0.2)) {
            viewModel.moveQueueItem(from: sourceIndex, to: targetIndex)
        }
    }

    func dropUpdated(info: DropInfo) -> DropProposal? {
        DropProposal(operation: .move)
    }
}

struct QueueThumbnailView: View {
    let item: QueueItem
    let isSelected: Bool
    var isDragging: Bool = false

    var body: some View {
        Image(nsImage: item.image)
            .resizable()
            .aspectRatio(contentMode: .fill)
            .frame(width: 36, height: 44)
            .clipShape(RoundedRectangle(cornerRadius: 4))
            .overlay(
                RoundedRectangle(cornerRadius: 4)
                    .stroke(isSelected ? Color.accentColor : Color.clear, lineWidth: 2)
            )
            .shadow(color: isSelected ? Color.accentColor.opacity(0.3) : .clear, radius: 3)
            .scaleEffect(isSelected ? 1.05 : 1.0)
            .opacity(isDragging ? 0.5 : 1.0)
            .animation(.easeInOut(duration: 0.15), value: isSelected)
    }
}

// MARK: - Main Actions View (Edit + Print buttons, in main window)

struct MainActionsView: View {
    @EnvironmentObject var viewModel: ViewModel
    var openEditor: () -> Void

    private var printLabel: String {
        if viewModel.isPrinting {
            if viewModel.batchPrintTotal > 1 {
                return L("printing_n_of_m", viewModel.batchPrintIndex, viewModel.batchPrintTotal)
            }
            return viewModel.printProgress.map { L("transfer_progress", $0.sent, $0.total) } ?? L("Preparing...")
        }
        if viewModel.queue.count > 1 {
            let count = min(viewModel.queue.count, viewModel.filmRemaining)
            return L("print_n_images", count)
        }
        return L("Print")
    }

    var body: some View {
        VStack(spacing: 10) {
            Button {
                openEditor()
            } label: {
                HStack {
                    Image(systemName: "slider.horizontal.3")
                    Text(L("Edit Image"))
                }
                .frame(maxWidth: .infinity)
            }
            .controlSize(.large)
            .disabled(viewModel.selectedImage == nil)

            Button {
                if viewModel.queue.count > 1 {
                    Task { await viewModel.printQueue() }
                } else {
                    Task { await viewModel.printSelectedImage() }
                }
            } label: {
                HStack {
                    if viewModel.isPrinting {
                        ProgressView()
                            .controlSize(.small)
                            .padding(.trailing, 2)
                    } else {
                        Image(systemName: "printer.fill")
                    }
                    Text(printLabel)
                }
                .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .disabled(viewModel.selectedImage == nil || !viewModel.isConnected || viewModel.isPrinting)
        }
    }
}

// MARK: - Image Editor View (editor window root)

struct ImageEditorView: View {
    @EnvironmentObject var viewModel: ViewModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(spacing: 0) {
            // Header with Done button
            HStack {
                Text(L("Edit Image"))
                    .font(.headline)
                Spacer()
                Button(L("Done")) {
                    dismiss()
                }
                .keyboardShortcut(.return, modifiers: [])
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 10)

            Divider()

            if viewModel.selectedImage != nil {
                HSplitView {
                    EditorPreviewView()
                        .padding(12)
                        .layoutPriority(1)

                    EditorSidebarView()
                }
            } else {
                VStack(spacing: 12) {
                    Spacer()
                    Image(systemName: "photo.on.rectangle.angled")
                        .font(.system(size: 40))
                        .foregroundColor(.secondary)
                    Text(L("No image selected"))
                        .font(.headline)
                        .foregroundColor(.secondary)
                    Button(L("Open File")) { viewModel.selectImage() }
                        .controlSize(.large)
                    Spacer()
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .frame(minWidth: 600, minHeight: 400)
    }
}

// MARK: - Editor Preview View (interactive crop/zoom, in editor window)

struct EditorPreviewView: View {
    @EnvironmentObject var viewModel: ViewModel
    @State private var isTargeted = false
    @GestureState private var dragDelta: CGSize = .zero
    @GestureState private var magnifyDelta: CGFloat = 1.0
    @State private var localFrameSize: CGSize = .zero

    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 8)
                .fill(Color(nsColor: .controlBackgroundColor))
            RoundedRectangle(cornerRadius: 8)
                .strokeBorder(
                    style: StrokeStyle(
                        lineWidth: 2,
                        dash: viewModel.selectedImage == nil ? [6] : []
                    ),
                    antialiased: true
                )
                .foregroundColor(isTargeted ? .accentColor : .secondary.opacity(0.5))

            if let image = viewModel.selectedImage {
                FilmFrameView(filmModel: viewModel.printerModelTag,
                              isRotated: viewModel.filmOrientation == "rotated") {
                    if viewModel.fitMode == "crop", let ar = viewModel.orientedAspectRatio {
                        Color.clear
                            .aspectRatio(ar, contentMode: .fit)
                            .background(
                                GeometryReader { geo in
                                    Color.clear.preference(key: CropFrameSizeKey.self, value: geo.size)
                                }
                            )
                            .onPreferenceChange(CropFrameSizeKey.self) { size in
                                localFrameSize = size
                                viewModel.cropFrameSize = size
                            }
                            .overlay(
                                Image(nsImage: image)
                                    .resizable()
                                    .aspectRatio(contentMode: .fill)
                                    .scaleEffect(effectiveZoom)
                                    .offset(effectiveOffset(imageSize: image.size))
                                    .rotationEffect(.degrees(Double(viewModel.rotationAngle)))
                            )
                            .overlay(alignment: stampAlignmentFor(viewModel)) {
                                DateStampOverlayView()
                            }
                            .clipped()
                            .contentShape(Rectangle())
                            .gesture(
                                DragGesture()
                                    .updating($dragDelta) { value, state, _ in
                                        state = value.translation
                                    }
                                    .onEnded { value in
                                        let raw = CGSize(
                                            width: viewModel.cropOffset.width + value.translation.width,
                                            height: viewModel.cropOffset.height + value.translation.height
                                        )
                                        viewModel.cropOffset = clampedOffset(
                                            raw: raw,
                                            imageSize: image.size,
                                            frameSize: localFrameSize,
                                            zoom: viewModel.cropZoom
                                        )
                                    }
                            )
                            .simultaneousGesture(
                                MagnificationGesture()
                                    .updating($magnifyDelta) { value, state, _ in
                                        state = value
                                    }
                                    .onEnded { value in
                                        let newZoom = min(max(viewModel.cropZoom * value, 1.0), 5.0)
                                        viewModel.cropZoom = newZoom
                                        viewModel.cropOffset = clampedOffset(
                                            raw: viewModel.cropOffset,
                                            imageSize: image.size,
                                            frameSize: localFrameSize,
                                            zoom: newZoom
                                        )
                                    }
                            )
                    } else if viewModel.fitMode == "contain", let ar = viewModel.orientedAspectRatio {
                        Color.white
                            .aspectRatio(ar, contentMode: .fit)
                            .overlay(
                                Image(nsImage: image)
                                    .resizable()
                                    .aspectRatio(contentMode: .fit)
                                    .rotationEffect(.degrees(Double(viewModel.rotationAngle)))
                            )
                            .overlay(alignment: stampAlignmentFor(viewModel)) {
                                DateStampOverlayView()
                            }
                            .clipped()
                    } else {
                        Image(nsImage: image)
                            .resizable()
                            .aspectRatio(contentMode: .fit)
                            .rotationEffect(.degrees(Double(viewModel.rotationAngle)))
                            .overlay(alignment: stampAlignmentFor(viewModel)) {
                                DateStampOverlayView()
                            }
                    }
                }
                .padding(4)
            }
        }
        .frame(minHeight: 250, idealHeight: 350)
        .onDrop(of: [.fileURL], isTargeted: $isTargeted) { providers in
            guard !providers.isEmpty else { return false }
            for provider in providers {
                _ = provider.loadObject(ofClass: URL.self) { url, _ in
                    guard let url = url else { return }
                    DispatchQueue.main.async { viewModel.addImages(from: [url]) }
                }
            }
            return true
        }
    }

    // MARK: - Crop gesture helpers

    private var effectiveZoom: CGFloat {
        min(max(viewModel.cropZoom * magnifyDelta, 1.0), 5.0)
    }

    private func effectiveOffset(imageSize: CGSize) -> CGSize {
        let raw = CGSize(
            width: viewModel.cropOffset.width + dragDelta.width,
            height: viewModel.cropOffset.height + dragDelta.height
        )
        return clampedOffset(raw: raw, imageSize: imageSize, frameSize: localFrameSize, zoom: effectiveZoom)
    }

    private func maxOffset(imageSize: CGSize, frameSize: CGSize, zoom: CGFloat) -> CGSize {
        guard frameSize.width > 0, frameSize.height > 0 else { return .zero }
        let imageAR = imageSize.width / imageSize.height
        let frameAR = frameSize.width / frameSize.height

        let displayW: CGFloat
        let displayH: CGFloat
        if imageAR > frameAR {
            displayH = frameSize.height
            displayW = frameSize.height * imageAR
        } else {
            displayW = frameSize.width
            displayH = frameSize.width / imageAR
        }

        return CGSize(
            width: max(0, (displayW * zoom - frameSize.width) / 2),
            height: max(0, (displayH * zoom - frameSize.height) / 2)
        )
    }

    private func clampedOffset(raw: CGSize, imageSize: CGSize, frameSize: CGSize, zoom: CGFloat) -> CGSize {
        let maxOff = maxOffset(imageSize: imageSize, frameSize: frameSize, zoom: zoom)
        return CGSize(
            width: min(max(raw.width, -maxOff.width), maxOff.width),
            height: min(max(raw.height, -maxOff.height), maxOff.height)
        )
    }
}

// MARK: - Accordion Section (reusable collapsible view)

struct AccordionSection<Content: View>: View {
    let title: String
    let icon: String
    @State private var isExpanded: Bool
    @ViewBuilder let content: () -> Content

    init(_ title: String, icon: String, expanded: Bool = true,
         @ViewBuilder content: @escaping () -> Content) {
        self.title = title
        self.icon = icon
        self._isExpanded = State(initialValue: expanded)
        self.content = content
    }

    var body: some View {
        VStack(spacing: 0) {
            Button {
                withAnimation(.easeInOut(duration: 0.2)) { isExpanded.toggle() }
            } label: {
                HStack {
                    Image(systemName: icon)
                        .frame(width: 16)
                    Text(title)
                        .font(.callout)
                        .fontWeight(.medium)
                    Spacer()
                    Image(systemName: "chevron.right")
                        .rotationEffect(.degrees(isExpanded ? 90 : 0))
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                .padding(.vertical, 8)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            if isExpanded {
                VStack(spacing: 8) {
                    content()
                }
                .padding(.bottom, 8)
            }
        }
    }
}

// MARK: - Editor Sidebar View (right sidebar in editor window)

struct EditorSidebarView: View {
    @EnvironmentObject var viewModel: ViewModel
    @State private var showDefaultsPopover = false

    var body: some View {
        ScrollView {
            VStack(spacing: 0) {
                // Fit Mode
                AccordionSection(L("Fit Mode"), icon: "crop") {
                    Picker("", selection: $viewModel.fitMode) {
                        Text(L("Crop")).tag("crop")
                        Text(L("Contain")).tag("contain")
                        Text(L("Stretch")).tag("stretch")
                    }
                    .pickerStyle(.segmented)
                    .labelsHidden()

                    if viewModel.fitMode == "crop" {
                        Button(L("Reset Crop")) {
                            viewModel.resetCropAdjustments()
                        }
                        .controlSize(.small)
                        .disabled(viewModel.cropOffset == .zero && viewModel.cropZoom == 1.0)
                        .frame(maxWidth: .infinity, alignment: .trailing)
                    }
                }

                Divider()

                // Rotate
                AccordionSection(L("Rotate"), icon: "rotate.right") {
                    HStack(spacing: 12) {
                        Button {
                            viewModel.rotateCounterClockwise()
                        } label: {
                            Label(L("Rotate Left"), systemImage: "rotate.left")
                        }
                        .controlSize(.small)

                        Button {
                            viewModel.rotateClockwise()
                        } label: {
                            Label(L("Rotate Right"), systemImage: "rotate.right")
                        }
                        .controlSize(.small)

                        Spacer()
                    }
                }

                Divider()

                // Date Stamp
                AccordionSection(L("Date Stamp"), icon: "calendar", expanded: false) {
                    Toggle(L("Enabled"), isOn: $viewModel.dateStampEnabled)
                        .font(.callout)

                    if viewModel.dateStampEnabled {
                        // Live preview
                        RoundedRectangle(cornerRadius: 6)
                            .fill(Color(white: 0.15))
                            .frame(height: 48)
                            .overlay(FontStampView(viewModel: viewModel, digitHeight: 13))

                        // Preset cards — horizontal scrolling strip
                        ScrollView(.horizontal, showsIndicators: false) {
                            HStack(spacing: 6) {
                                ForEach(ViewModel.presetOrder, id: \.self) { key in
                                    PresetCard(preset: ViewModel.dateStampPresets[key]!,
                                               isSelected: viewModel.dateStampStyle == key)
                                    .onTapGesture {
                                        viewModel.dateStampStyle = key
                                        viewModel.lightBleedEnabled = ViewModel.dateStampPresets[key]!.defaultLightBleed
                                    }
                                }
                            }
                        }

                        // Format + Position — compact menu pickers on one row
                        HStack(spacing: 8) {
                            Picker(L("Format"), selection: $viewModel.dateStampFormat) {
                                Text("YY.MM.DD").tag("ymd")
                                Text("MM.DD.YY").tag("mdy")
                                Text("DD.MM.YY").tag("dmy")
                            }
                            .pickerStyle(.menu).font(.callout)

                            Picker(L("Position"), selection: $viewModel.dateStampPosition) {
                                Text("\u{2198} BR").tag("bottomRight")
                                Text("\u{2199} BL").tag("bottomLeft")
                                Text("\u{2197} TR").tag("topRight")
                                Text("\u{2196} TL").tag("topLeft")
                            }
                            .pickerStyle(.menu).font(.callout)
                        }

                        // Toggles on one row
                        HStack {
                            Toggle(L("Time"), isOn: $viewModel.showTimeRow).font(.callout)
                            Spacer()
                            Toggle(L("Glow"), isOn: $viewModel.lightBleedEnabled).font(.callout)
                        }
                    }
                }

                Divider()

                Button {
                    showDefaultsPopover = true
                } label: {
                    HStack(alignment: .top, spacing: 10) {
                        Image(systemName: "slider.horizontal.3")
                            .font(.callout)
                            .foregroundColor(.accentColor)
                            .frame(width: 18, height: 18)

                        VStack(alignment: .leading, spacing: 4) {
                            Text(L("Defaults For New Photos"))
                                .font(.callout)
                                .fontWeight(.medium)
                                .foregroundColor(.primary)
                            Text(L("Applies to photos added after this change. Existing queue items stay unchanged."))
                                .font(.caption)
                                .foregroundColor(.secondary)
                                .multilineTextAlignment(.leading)
                        }

                        Spacer(minLength: 8)

                        Image(systemName: "chevron.right")
                            .font(.caption)
                            .foregroundColor(.secondary)
                            .padding(.top, 2)
                    }
                    .padding(.vertical, 10)
                    .padding(.horizontal, 10)
                    .background(
                        RoundedRectangle(cornerRadius: 8)
                            .fill(Color.secondary.opacity(0.08))
                    )
                }
                .buttonStyle(.plain)
                .padding(.top, 12)
                .popover(isPresented: $showDefaultsPopover, arrowEdge: .leading) {
                    NewPhotoDefaultsPopover()
                        .environmentObject(viewModel)
                }
            }
            .padding(12)
        }
        .frame(minWidth: 200, idealWidth: 220, maxWidth: 260)
    }

}

struct NewPhotoDefaultsPopover: View {
    @EnvironmentObject var viewModel: ViewModel

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(L("Defaults For New Photos"))
                .font(.headline)

            Text(L("Applies to photos added after this change. Existing queue items stay unchanged."))
                .font(.caption)
                .foregroundColor(.secondary)

            VStack(alignment: .leading, spacing: 8) {
                Text(L("Fit Mode"))
                    .font(.caption)
                    .foregroundColor(.secondary)

                Picker("", selection: $viewModel.newPhotoDefaults.fitMode) {
                    Text(L("Crop")).tag("crop")
                    Text(L("Contain")).tag("contain")
                    Text(L("Stretch")).tag("stretch")
                }
                .pickerStyle(.segmented)
                .labelsHidden()
            }

            if let aspectRatio = viewModel.printerAspectRatio, aspectRatio != 1.0 {
                VStack(alignment: .leading, spacing: 8) {
                    Text(L("Film Orientation"))
                        .font(.caption)
                        .foregroundColor(.secondary)

                    Picker("", selection: $viewModel.newPhotoDefaults.filmOrientation) {
                        Text(L("Standard")).tag("default")
                        Text(L("Rotated")).tag("rotated")
                    }
                    .pickerStyle(.segmented)
                    .labelsHidden()
                }
            }

            VStack(alignment: .leading, spacing: 8) {
                Text(L("Date Stamp"))
                    .font(.caption)
                    .foregroundColor(.secondary)

                Toggle(L("Enabled"), isOn: $viewModel.newPhotoDefaults.dateStampEnabled)
                    .font(.callout)

                if viewModel.newPhotoDefaults.dateStampEnabled {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 6) {
                            ForEach(ViewModel.presetOrder, id: \.self) { key in
                                PresetCard(
                                    preset: ViewModel.dateStampPresets[key]!,
                                    isSelected: viewModel.newPhotoDefaults.dateStampStyle == key
                                )
                                .onTapGesture {
                                    viewModel.newPhotoDefaults.dateStampStyle = key
                                    viewModel.newPhotoDefaults.lightBleedEnabled =
                                        ViewModel.dateStampPresets[key]!.defaultLightBleed
                                }
                            }
                        }
                    }

                    HStack(spacing: 8) {
                        Picker(L("Format"), selection: $viewModel.newPhotoDefaults.dateStampFormat) {
                            Text("YY.MM.DD").tag("ymd")
                            Text("MM.DD.YY").tag("mdy")
                            Text("DD.MM.YY").tag("dmy")
                        }
                        .pickerStyle(.menu)
                        .font(.callout)

                        Picker(L("Position"), selection: $viewModel.newPhotoDefaults.dateStampPosition) {
                            Text("\u{2198} BR").tag("bottomRight")
                            Text("\u{2199} BL").tag("bottomLeft")
                            Text("\u{2197} TR").tag("topRight")
                            Text("\u{2196} TL").tag("topLeft")
                        }
                        .pickerStyle(.menu)
                        .font(.callout)
                    }

                    HStack {
                        Toggle(L("Time"), isOn: $viewModel.newPhotoDefaults.showTimeRow)
                            .font(.callout)
                        Spacer()
                        Toggle(L("Glow"), isOn: $viewModel.newPhotoDefaults.lightBleedEnabled)
                            .font(.callout)
                    }
                }
            }

            Divider()

            HStack {
                Button(L("Use Current Photo Settings")) {
                    viewModel.saveCurrentSettingsAsNewPhotoDefaults()
                }
                .disabled(viewModel.selectedImage == nil)

                Spacer()

                Button(L("Reset Defaults")) {
                    viewModel.resetNewPhotoDefaults()
                }
                .disabled(viewModel.newPhotoDefaults == NewPhotoDefaults())
            }
        }
        .padding(16)
        .frame(width: 320)
    }
}

// MARK: - Printer Picker Sheet

struct PrinterPickerSheet: View {
    @EnvironmentObject var viewModel: ViewModel
    @Environment(\.dismiss) private var dismiss

    private var sortedProfiles: [PrinterProfile] {
        viewModel.printerProfiles.values.sorted { $0.bleIdentifier < $1.bleIdentifier }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            // Header
            HStack {
                Text(L("Switch Printer"))
                    .font(.headline)
                Spacer()
                Button {
                    dismiss()
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .foregroundColor(.secondary)
                }
                .buttonStyle(.plain)
            }

            // Saved Printers
            if !sortedProfiles.isEmpty {
                VStack(alignment: .leading, spacing: 8) {
                    Text(L("Saved Printers"))
                        .font(.subheadline)
                        .foregroundColor(.secondary)

                    ForEach(sortedProfiles, id: \.bleIdentifier) { profile in
                        let isCurrentConnected = profile.bleIdentifier == viewModel.printerName && viewModel.isConnected
                        Button {
                            if !isCurrentConnected {
                                viewModel.switchPrinter(to: profile.bleIdentifier)
                                dismiss()
                            }
                        } label: {
                            HStack(spacing: 8) {
                                Circle()
                                    .fill(isCurrentConnected ? Color.green : Color.gray.opacity(0.4))
                                    .frame(width: 8, height: 8)

                                VStack(alignment: .leading, spacing: 2) {
                                    Text(profile.displayName)
                                        .font(.body)
                                        .foregroundColor(.primary)
                                    Text(profile.effectiveModel)
                                        .font(.caption)
                                        .foregroundColor(.secondary)
                                }

                                Spacer()

                                if isCurrentConnected {
                                    Image(systemName: "checkmark")
                                        .font(.caption)
                                        .foregroundColor(.green)
                                }
                            }
                            .padding(.vertical, 6)
                            .padding(.horizontal, 10)
                            .background(
                                RoundedRectangle(cornerRadius: 6)
                                    .fill(Color(nsColor: .controlBackgroundColor))
                            )
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                    }
                }
            }

            // Nearby Printers
            if !viewModel.nearbyPrinters.isEmpty {
                VStack(alignment: .leading, spacing: 8) {
                    Text(L("Nearby Printers"))
                        .font(.subheadline)
                        .foregroundColor(.secondary)

                    ForEach(viewModel.nearbyPrinters, id: \.self) { bleId in
                        Button {
                            viewModel.selectedPrinter = bleId
                            dismiss()
                            viewModel.startPairing()
                        } label: {
                            HStack(spacing: 8) {
                                Image(systemName: "antenna.radiowaves.left.and.right")
                                    .font(.caption)
                                    .foregroundColor(.secondary)

                                VStack(alignment: .leading, spacing: 2) {
                                    Text(bleId)
                                        .font(.body)
                                        .foregroundColor(.primary)
                                    Text(L("Tap to connect"))
                                        .font(.caption)
                                        .foregroundColor(.secondary)
                                }

                                Spacer()
                            }
                            .padding(.vertical, 6)
                            .padding(.horizontal, 10)
                            .background(
                                RoundedRectangle(cornerRadius: 6)
                                    .fill(Color(nsColor: .controlBackgroundColor))
                            )
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                    }
                }
            }

            // Scan button
            VStack(spacing: 6) {
                Button {
                    viewModel.scanNearby()
                } label: {
                    HStack(spacing: 6) {
                        if viewModel.isScanning {
                            ProgressView()
                                .controlSize(.small)
                        } else {
                            Image(systemName: "magnifyingglass")
                        }
                        Text(viewModel.isScanning ? L("Scanning...") : L("Scan for Printers"))
                    }
                    .frame(maxWidth: .infinity)
                }
                .controlSize(.large)
                .disabled(viewModel.isScanning)

                if !viewModel.isScanning && viewModel.nearbyPrinters.isEmpty && sortedProfiles.isEmpty {
                    Text(L("No new printers found"))
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
            }
        }
        .padding(24)
        .frame(width: 340)
    }
}

// MARK: - Printer Profile Sheet

struct PrinterProfileSheet: View {
    @EnvironmentObject var viewModel: ViewModel
    let isPostPairing: Bool

    @State private var customName: String = ""
    @State private var selectedModel: String = ""
    @State private var selectedColor: String = ""

    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(isPostPairing ? L("Printer Connected") : L("Edit Printer"))
                .font(.headline)

            if let profile = viewModel.editingProfile {
                VStack(alignment: .leading, spacing: 8) {
                    if let serial = profile.serialNumber {
                        VStack(alignment: .leading, spacing: 2) {
                            HStack {
                                Text(L("Serial Number:"))
                                    .foregroundColor(.secondary)
                                Text(serial)
                                    .fontWeight(.medium)
                                    .textSelection(.enabled)
                            }
                            Text(L("Verify this matches the serial number on the bottom of your device"))
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                    }

                    HStack {
                        Text(L("BLE Name:"))
                            .foregroundColor(.secondary)
                        Text(profile.bleIdentifier)
                            .font(.caption)
                            .textSelection(.enabled)
                    }
                }

                Divider()

                VStack(alignment: .leading, spacing: 10) {
                    HStack {
                        Text(L("Model:"))
                            .frame(width: 50, alignment: .leading)
                        Picker("", selection: $selectedModel) {
                            ForEach(PrinterProfile.availableModels, id: \.self) { model in
                                Text(model).tag(model)
                            }
                        }
                        .labelsHidden()
                    }

                    HStack {
                        Text(L("Color:"))
                            .frame(width: 50, alignment: .leading)
                        Picker("", selection: $selectedColor) {
                            Text(L("None")).tag("")
                            ForEach(PrinterProfile.availableColors, id: \.self) { color in
                                Text(L(color)).tag(color)
                            }
                        }
                        .labelsHidden()
                    }

                    HStack {
                        Text(L("Name:"))
                            .frame(width: 50, alignment: .leading)
                        TextField(L("Custom display name"), text: $customName)
                            .textFieldStyle(.roundedBorder)
                    }
                }
            }

            Divider()

            HStack {
                Spacer()
                Button(L("Cancel")) { dismiss() }
                    .keyboardShortcut(.cancelAction)
                Button(L("Save")) { saveAndDismiss() }
                    .keyboardShortcut(.defaultAction)
                    .buttonStyle(.borderedProminent)
            }
        }
        .padding(24)
        .frame(width: 380)
        .onAppear {
            if let profile = viewModel.editingProfile {
                customName = profile.customName ?? ""
                selectedModel = profile.effectiveModel
                selectedColor = profile.deviceColor ?? ""
            }
        }
    }

    private func saveAndDismiss() {
        guard var profile = viewModel.editingProfile else { return }
        profile.customName = customName.isEmpty ? nil : customName
        profile.overriddenModel = selectedModel == profile.detectedModel ? nil : selectedModel
        profile.deviceColor = selectedColor.isEmpty ? nil : selectedColor
        viewModel.saveProfile(profile)
        dismiss()
    }
}

// MARK: - Settings Window

struct SettingsView: View {
    @EnvironmentObject var viewModel: ViewModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text(L("Settings"))
                    .font(.headline)
                Spacer()
                Button {
                    dismiss()
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .foregroundColor(.secondary)
                }
                .buttonStyle(.plain)
            }
            .padding(.horizontal, 24)
            .padding(.top, 20)
            .padding(.bottom, 8)

            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    AboutSection()
                    Divider().padding(.vertical, 12)
                    LanguageSection()
                    Divider().padding(.vertical, 12)
                    PrinterManagementSection()
                }
                .padding(.horizontal, 24)
                .padding(.bottom, 24)
            }
        }
        .frame(width: 380, height: 500)
    }
}

// MARK: - About Section

struct AboutSection: View {
    @EnvironmentObject var viewModel: ViewModel
    @State private var isChecking = false
    @State private var checkResult: String?
    @State private var checkResultIsUpdate = false

    private var versionSummary: String {
        "\(L("App:")) v\(Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "0.0.0") | \(L("Core:")) \(viewModel.coreVersion)"
    }

    private var updateButtonTitle: String {
        if viewModel.updateAvailable != nil || checkResultIsUpdate {
            return L("Update Now")
        }
        return checkResult ?? L("Check for Updates")
    }

    var body: some View {
        VStack(spacing: 8) {
            if let icon = NSApplication.shared.applicationIconImage {
                Image(nsImage: icon)
                    .resizable()
                    .frame(width: 64, height: 64)
            }
            Text("InstantLink")
                .font(.title2)
                .fontWeight(.bold)
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(versionSummary)
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .lineLimit(1)
                    .minimumScaleFactor(0.8)
                Spacer(minLength: 8)
                Button {
                    if viewModel.updateAvailable != nil || checkResultIsUpdate {
                        viewModel.performUpdate()
                    } else {
                        isChecking = true
                        checkResult = nil
                        Task {
                            await viewModel.checkForUpdates()
                            await MainActor.run {
                                isChecking = false
                                if viewModel.updateAvailable != nil {
                                    checkResult = nil
                                    checkResultIsUpdate = true
                                } else {
                                    checkResult = L("Up to date")
                                    checkResultIsUpdate = false
                                }
                            }
                        }
                    }
                } label: {
                    HStack(spacing: 4) {
                        if isChecking {
                            ProgressView()
                                .controlSize(.small)
                        } else if viewModel.updateAvailable != nil || checkResultIsUpdate {
                            Image(systemName: "arrow.up.circle.fill")
                        } else {
                            Image(systemName: "arrow.triangle.2.circlepath")
                        }
                        Text(updateButtonTitle)
                    }
                    .font(.caption)
                }
                .buttonStyle(.link)
                .disabled(isChecking)
            }
            .frame(maxWidth: .infinity)

            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text("\u{00A9} 2026 InstantLink")
                    .font(.caption2)
                    .foregroundColor(.secondary)
                Spacer(minLength: 8)
                Button {
                    if let url = URL(string: "https://github.com/wu-hongjun/instantlink") {
                        NSWorkspace.shared.open(url)
                    }
                } label: {
                    HStack(spacing: 4) {
                        Image(systemName: "link")
                        Text(L("GitHub"))
                    }
                    .font(.caption2)
                }
                .buttonStyle(.link)
            }
        }
        .frame(maxWidth: .infinity)
    }
}

// MARK: - Language Section

struct LanguageSection: View {
    private static let supportedLanguages = [
        "en", "de", "es", "fr", "it", "ja", "ko", "pt-BR", "zh-Hans", "zh-Hant", "ar", "he"
    ]

    @Environment(\.dismiss) private var dismiss

    private let initialLanguage: String

    @State private var selectedLanguage: String

    init() {
        let saved: String
        if let langs = UserDefaults.standard.array(forKey: "AppleLanguages") as? [String],
           let first = langs.first,
           Self.supportedLanguages.contains(first) {
            saved = first
        } else {
            saved = ""
        }
        self.initialLanguage = saved
        self._selectedLanguage = State(initialValue: saved)
    }

    private var languageChanged: Bool {
        selectedLanguage != initialLanguage
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(L("Language"))
                .font(.headline)

            Picker("", selection: $selectedLanguage) {
                Text(L("System Default")).tag("")
                ForEach(Self.supportedLanguages, id: \.self) { code in
                    Text(Self.displayName(for: code)).tag(code)
                }
            }
            .labelsHidden()
            .onChange(of: selectedLanguage) { newValue in
                if newValue.isEmpty {
                    UserDefaults.standard.removeObject(forKey: "AppleLanguages")
                } else {
                    UserDefaults.standard.set([newValue], forKey: "AppleLanguages")
                }
            }

            if languageChanged {
                HStack {
                    Text(L("language_restart_note"))
                        .font(.caption)
                        .foregroundColor(.secondary)
                    Spacer()
                    Button(L("Restart")) {
                        dismiss()
                        DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
                            AppRelauncher.relaunchCurrentApp()
                        }
                    }
                    .controlSize(.small)
                }
            } else {
                Text(L("language_restart_note"))
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
    }

    private static func displayName(for code: String) -> String {
        let locale = Locale(identifier: code)
        return locale.localizedString(forIdentifier: code)?.localizedCapitalized ?? code
    }
}

// MARK: - Printer Management Section

struct PrinterManagementSection: View {
    @EnvironmentObject var viewModel: ViewModel
    @State private var editingSettingsProfile: PrinterProfile?
    @State private var deletingBleId: String?

    private var sortedProfiles: [PrinterProfile] {
        viewModel.printerProfiles.values.sorted { $0.bleIdentifier < $1.bleIdentifier }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(L("Saved Printers"))
                .font(.headline)

            if sortedProfiles.isEmpty {
                Text(L("No saved printers"))
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .padding(.vertical, 4)
            } else {
                ForEach(sortedProfiles, id: \.bleIdentifier) { profile in
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(profile.displayName)
                                .font(.body)
                            Text(profile.effectiveModel)
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }

                        Spacer()

                        Button {
                            editingSettingsProfile = profile
                        } label: {
                            Image(systemName: "pencil")
                        }
                        .buttonStyle(.borderless)

                        Button {
                            deletingBleId = profile.bleIdentifier
                        } label: {
                            Image(systemName: "trash")
                        }
                        .buttonStyle(.borderless)
                    }
                    .padding(.vertical, 4)
                }
            }
        }
        .sheet(item: Binding<PrinterProfile?>(
            get: { editingSettingsProfile },
            set: { editingSettingsProfile = $0 }
        )) { profile in
            SettingsProfileEditor(profile: profile)
                .environmentObject(viewModel)
        }
        .confirmationDialog(
            L("delete_printer_confirm"),
            isPresented: Binding(
                get: { deletingBleId != nil },
                set: { if !$0 { deletingBleId = nil } }
            ),
            titleVisibility: .visible
        ) {
            Button(L("Delete"), role: .destructive) {
                if let bleId = deletingBleId {
                    viewModel.deleteProfile(bleId)
                    deletingBleId = nil
                }
            }
            Button(L("Cancel"), role: .cancel) {
                deletingBleId = nil
            }
        }
    }
}

// MARK: - Settings Profile Editor (sheet)

struct SettingsProfileEditor: View {
    @EnvironmentObject var viewModel: ViewModel
    let profile: PrinterProfile

    @State private var customName: String = ""
    @State private var selectedModel: String = ""
    @State private var selectedColor: String = ""

    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(L("Edit Printer"))
                .font(.headline)

            if let serial = profile.serialNumber {
                HStack {
                    Text(L("Serial Number:"))
                        .foregroundColor(.secondary)
                    Text(serial)
                        .fontWeight(.medium)
                        .textSelection(.enabled)
                }
            }

            HStack {
                Text(L("BLE Name:"))
                    .foregroundColor(.secondary)
                Text(profile.bleIdentifier)
                    .font(.caption)
                    .textSelection(.enabled)
            }

            Divider()

            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    Text(L("Model:"))
                        .frame(width: 50, alignment: .leading)
                    Picker("", selection: $selectedModel) {
                        ForEach(PrinterProfile.availableModels, id: \.self) { model in
                            Text(model).tag(model)
                        }
                    }
                    .labelsHidden()
                }

                HStack {
                    Text(L("Color:"))
                        .frame(width: 50, alignment: .leading)
                    Picker("", selection: $selectedColor) {
                        Text(L("None")).tag("")
                        ForEach(PrinterProfile.availableColors, id: \.self) { color in
                            Text(L(color)).tag(color)
                        }
                    }
                    .labelsHidden()
                }

                HStack {
                    Text(L("Name:"))
                        .frame(width: 50, alignment: .leading)
                    TextField(L("Custom display name"), text: $customName)
                        .textFieldStyle(.roundedBorder)
                }
            }

            Divider()

            HStack {
                Spacer()
                Button(L("Cancel")) { dismiss() }
                    .keyboardShortcut(.cancelAction)
                Button(L("Save")) { saveAndDismiss() }
                    .keyboardShortcut(.defaultAction)
                    .buttonStyle(.borderedProminent)
            }
        }
        .padding(24)
        .frame(width: 380)
        .onAppear {
            customName = profile.customName ?? ""
            selectedModel = profile.effectiveModel
            selectedColor = profile.deviceColor ?? ""
        }
    }

    private func saveAndDismiss() {
        var updated = profile
        updated.customName = customName.isEmpty ? nil : customName
        updated.overriddenModel = selectedModel == profile.detectedModel ? nil : selectedModel
        updated.deviceColor = selectedColor.isEmpty ? nil : selectedColor
        viewModel.saveProfile(updated)
        dismiss()
    }
}
