import AVFoundation
import CoreImage
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
    var overlays: [OverlayItem] = []
    var filmOrientation: String = "default"

    static func load() -> Self {
        if let data = UserDefaults.standard.data(forKey: storageKey),
           let decoded = try? JSONDecoder().decode(Self.self, from: data) {
            return decoded
        }
        return Self()
    }

    func save() {
        if let data = try? JSONEncoder().encode(self) {
            UserDefaults.standard.set(data, forKey: Self.storageKey)
        }
    }
}

private let initialNewPhotoDefaults = NewPhotoDefaults.load()

enum AppAppearance: String, CaseIterable, Codable, Identifiable {
    case system
    case light
    case dark

    static let storageKey = "appAppearance"

    var id: String { rawValue }

    var nsAppearance: NSAppearance? {
        switch self {
        case .system:
            return nil
        case .light:
            return NSAppearance(named: .aqua)
        case .dark:
            return NSAppearance(named: .darkAqua)
        }
    }

    static func load() -> Self {
        guard let rawValue = UserDefaults.standard.string(forKey: storageKey),
              let appearance = Self(rawValue: rawValue) else {
            return .system
        }
        return appearance
    }

    func save() {
        UserDefaults.standard.set(rawValue, forKey: Self.storageKey)
    }
}

struct QueueItemEditState: Equatable {
    var fitMode: String
    var cropOffset: CGSize = .zero
    var cropZoom: CGFloat = 1.0
    var rotationAngle: Int = 0
    var isHorizontallyFlipped: Bool = false
    var overlays: [OverlayItem] = []
    var filmOrientation: String = "default"
}

struct QueueItem: Identifiable, Equatable {
    let id: UUID
    let url: URL
    let image: NSImage
    let imageDate: Date?
    let imageLocation: ImageLocationMetadata?
    var editState: QueueItemEditState

    init(
        id: UUID = UUID(),
        url: URL,
        image: NSImage,
        imageDate: Date?,
        imageLocation: ImageLocationMetadata?,
        editState: QueueItemEditState
    ) {
        self.id = id
        self.url = url
        self.image = image
        self.imageDate = imageDate
        self.imageLocation = imageLocation
        self.editState = editState
    }
}

// MARK: - View Model

class ViewModel: ObservableObject {
    static let maxQueueItems = 20
    static let minCropZoom: CGFloat = 1.0
    static let maxCropZoom: CGFloat = 5.0
    static let quickCropZoomStep: CGFloat = 0.25

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
    @Published var appearancePreference: AppAppearance = AppAppearance.load() {
        didSet {
            appearancePreference.save()
            applyAppearancePreference()
        }
    }

    var selectedImage: NSImage? { queue.indices.contains(selectedQueueIndex) ? queue[selectedQueueIndex].image : nil }
    var selectedImagePath: String? { queue.indices.contains(selectedQueueIndex) ? queue[selectedQueueIndex].url.path : nil }
    var imageDate: Date? { queue.indices.contains(selectedQueueIndex) ? queue[selectedQueueIndex].imageDate : nil }
    var imageLocation: ImageLocationMetadata? { queue.indices.contains(selectedQueueIndex) ? queue[selectedQueueIndex].imageLocation : nil }

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
    @Published var isHorizontallyFlipped: Bool = false {
        didSet { persistSelectedQueueItemEditState() }
    }

    // Overlays
    @Published var overlays: [OverlayItem] = initialNewPhotoDefaults.overlays {
        didSet {
            if let selectedOverlayID,
               overlays.contains(where: { $0.id == selectedOverlayID }) == false {
                self.selectedOverlayID = overlays.last?.id
            } else if selectedOverlayID == nil {
                selectedOverlayID = overlays.last?.id
            }
            persistSelectedQueueItemEditState()
        }
    }
    @Published var selectedOverlayID: UUID?

    var selectedOverlayIndex: Int? {
        guard let selectedOverlayID else { return nil }
        return overlays.firstIndex(where: { $0.id == selectedOverlayID })
    }

    var selectedOverlay: OverlayItem? {
        guard let selectedOverlayIndex else { return nil }
        return overlays[selectedOverlayIndex]
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
    func orientedAspectRatio(for orientation: String) -> CGFloat? {
        guard let ar = printerAspectRatio else { return nil }
        if orientation == "rotated" && ar != 1.0 {
            return 1.0 / ar
        }
        return ar
    }

    var orientedAspectRatio: CGFloat? {
        orientedAspectRatio(for: filmOrientation)
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
        applyAppearancePreference()
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
            let metadata = Self.extractImageMetadata(from: url)
            queue.append(QueueItem(
                url: url,
                image: image,
                imageDate: metadata.date,
                imageLocation: metadata.location,
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

    private static func extractImageMetadata(from url: URL) -> (date: Date?, location: ImageLocationMetadata?) {
        guard let source = CGImageSourceCreateWithURL(url as CFURL, nil) else {
            return (fileModificationDate(url), nil)
        }
        guard let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [String: Any] else {
            return (fileModificationDate(url), nil)
        }
        let date: Date?
        if let exif = properties[kCGImagePropertyExifDictionary as String] as? [String: Any],
           let dateString = exif[kCGImagePropertyExifDateTimeOriginal as String] as? String {
            let formatter = DateFormatter()
            formatter.dateFormat = "yyyy:MM:dd HH:mm:ss"
            date = formatter.date(from: dateString) ?? fileModificationDate(url)
        } else {
            date = fileModificationDate(url)
        }
        return (date, extractImageLocation(from: properties))
    }

    private static func extractImageLocation(from properties: [String: Any]) -> ImageLocationMetadata? {
        guard let gps = properties[kCGImagePropertyGPSDictionary as String] as? [String: Any],
              let latitudeValue = gps[kCGImagePropertyGPSLatitude as String] as? Double,
              let longitudeValue = gps[kCGImagePropertyGPSLongitude as String] as? Double else {
            return nil
        }

        let latitudeRef = (gps[kCGImagePropertyGPSLatitudeRef as String] as? String) ?? "N"
        let longitudeRef = (gps[kCGImagePropertyGPSLongitudeRef as String] as? String) ?? "E"
        let latitude = latitudeRef.uppercased() == "S" ? -latitudeValue : latitudeValue
        let longitude = longitudeRef.uppercased() == "W" ? -longitudeValue : longitudeValue
        let coordinate = GeoCoordinate(latitude: latitude, longitude: longitude)
        guard coordinate.isValid else { return nil }

        let altitude = gps[kCGImagePropertyGPSAltitude as String] as? Double
        let speed = gps[kCGImagePropertyGPSSpeed as String] as? Double
        let timestamp: Date?
        if let dateString = gps[kCGImagePropertyGPSDateStamp as String] as? String,
           let timeString = gps[kCGImagePropertyGPSTimeStamp as String] as? String {
            let formatter = DateFormatter()
            formatter.dateFormat = "yyyy:MM:dd HH:mm:ss"
            timestamp = formatter.date(from: "\(dateString) \(timeString)")
        } else {
            timestamp = nil
        }

        return ImageLocationMetadata(
            coordinate: coordinate,
            altitude: altitude,
            speed: speed,
            timestamp: timestamp
        )
    }

    private static func fileModificationDate(_ url: URL) -> Date? {
        try? FileManager.default.attributesOfItem(atPath: url.path)[.modificationDate] as? Date
    }

    func clearImage() {
        queue.removeAll()
        selectedQueueIndex = 0
        applyDefaultQueueItemEditState()
    }

    var printableQueueCountFromSelection: Int {
        guard !queue.isEmpty else { return 0 }
        let startIndex = queue.indices.contains(selectedQueueIndex) ? selectedQueueIndex : 0
        return min(queue.count - startIndex, filmRemaining)
    }

    func resetCropAdjustments() {
        cropOffset = .zero
        cropZoom = Self.minCropZoom
    }

    var canQuickZoomIn: Bool {
        selectedImage != nil && cropZoom < Self.maxCropZoom - 0.001
    }

    var canQuickZoomOut: Bool {
        selectedImage != nil && fitMode == "crop" && cropZoom > Self.minCropZoom + 0.001
    }

    var canResetCropAdjustments: Bool {
        selectedImage != nil &&
        fitMode == "crop" &&
        (cropOffset != .zero || abs(cropZoom - Self.minCropZoom) > 0.001)
    }

    func quickZoomIn() {
        guard selectedImage != nil else { return }
        if fitMode != "crop" {
            fitMode = "crop"
        }
        setCropZoom(cropZoom + Self.quickCropZoomStep)
    }

    func quickZoomOut() {
        guard selectedImage != nil, fitMode == "crop" else { return }
        setCropZoom(cropZoom - Self.quickCropZoomStep)
    }

    func setCropZoom(_ zoom: CGFloat) {
        guard let image = selectedImage else { return }
        let newZoom = min(max(zoom, Self.minCropZoom), Self.maxCropZoom)
        cropZoom = newZoom
        cropOffset = clampedCropOffset(
            raw: cropOffset,
            imageSize: image.size,
            frameSize: cropFrameSize,
            zoom: newZoom
        )
    }

    func clampedCropOffset(raw: CGSize, imageSize: CGSize, frameSize: CGSize, zoom: CGFloat) -> CGSize {
        let maxOff = maxCropOffset(imageSize: imageSize, frameSize: frameSize, zoom: zoom)
        return CGSize(
            width: min(max(raw.width, -maxOff.width), maxOff.width),
            height: min(max(raw.height, -maxOff.height), maxOff.height)
        )
    }

    private func maxCropOffset(imageSize: CGSize, frameSize: CGSize, zoom: CGFloat) -> CGSize {
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

    func saveCurrentSettingsAsNewPhotoDefaults() {
        newPhotoDefaults = NewPhotoDefaults(
            fitMode: fitMode,
            overlays: overlays,
            filmOrientation: filmOrientation
        )
    }

    func resetNewPhotoDefaults() {
        newPhotoDefaults = NewPhotoDefaults()
    }

    func applyAppearancePreference() {
        NSApp.appearance = appearancePreference.nsAppearance
    }

    private func makeCurrentQueueItemEditState() -> QueueItemEditState {
        QueueItemEditState(
            fitMode: fitMode,
            cropOffset: cropOffset,
            cropZoom: cropZoom,
            rotationAngle: rotationAngle,
            isHorizontallyFlipped: isHorizontallyFlipped,
            overlays: overlays,
            filmOrientation: filmOrientation
        )
    }

    private func makeQueueItemEditStateFromDefaults() -> QueueItemEditState {
        QueueItemEditState(
            fitMode: newPhotoDefaults.fitMode,
            overlays: newPhotoDefaults.overlays,
            filmOrientation: newPhotoDefaults.filmOrientation
        )
    }

    private func makeCapturedQueueItemEditState() -> QueueItemEditState {
        var editState = makeQueueItemEditStateFromDefaults()
        editState.filmOrientation = filmOrientation
        editState.isHorizontallyFlipped = isHorizontallyFlipped
        return editState
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
        isHorizontallyFlipped = editState.isHorizontallyFlipped
        overlays = editState.overlays
        selectedOverlayID = overlays.last?.id
        filmOrientation = editState.filmOrientation
        isApplyingQueueItemEditState = false
    }

    private func persistSelectedQueueItemEditState() {
        guard !isApplyingQueueItemEditState,
              queue.indices.contains(selectedQueueIndex) else { return }
        queue[selectedQueueIndex].editState = makeCurrentQueueItemEditState()
    }

    func selectOverlay(_ id: UUID?) {
        selectedOverlayID = id
    }

    func addOverlay(kind: OverlayKind) {
        let overlay = OverlayItem(
            content: defaultOverlayContent(for: kind),
            placement: defaultOverlayPlacement(for: kind),
            opacity: 1.0,
            zIndex: (overlays.map(\.zIndex).max() ?? -1) + 1
        )
        overlays.append(overlay)
        selectedOverlayID = overlay.id
    }

    func deleteOverlay(id: UUID) {
        guard let index = overlays.firstIndex(where: { $0.id == id }) else { return }
        overlays.remove(at: index)
        if selectedOverlayID == id {
            selectedOverlayID = overlays.indices.contains(index) ? overlays[index].id : overlays.last?.id
        }
    }

    func deleteSelectedOverlay() {
        guard let selectedOverlayID else { return }
        deleteOverlay(id: selectedOverlayID)
    }

    func duplicateSelectedOverlay() {
        guard let selectedOverlay else { return }
        var duplicate = selectedOverlay
        duplicate.id = UUID()
        duplicate.createdAt = Date()
        duplicate.placement.normalizedCenterX = min(0.92, duplicate.placement.normalizedCenterX + 0.04)
        duplicate.placement.normalizedCenterY = min(0.92, duplicate.placement.normalizedCenterY + 0.04)
        duplicate.zIndex = (overlays.map(\.zIndex).max() ?? -1) + 1
        overlays.append(duplicate)
        selectedOverlayID = duplicate.id
    }

    func moveSelectedOverlayForward() {
        guard let index = selectedOverlayIndex, index < overlays.count - 1 else { return }
        let currentZ = overlays[index].zIndex
        let nextZ = overlays[index + 1].zIndex
        overlays[index].zIndex = nextZ
        overlays[index + 1].zIndex = currentZ
        overlays.sort { $0.zIndex < $1.zIndex }
    }

    func moveSelectedOverlayBackward() {
        guard let index = selectedOverlayIndex, index > 0 else { return }
        let currentZ = overlays[index].zIndex
        let previousZ = overlays[index - 1].zIndex
        overlays[index].zIndex = previousZ
        overlays[index - 1].zIndex = currentZ
        overlays.sort { $0.zIndex < $1.zIndex }
    }

    func updateSelectedOverlay(_ mutate: (inout OverlayItem) -> Void) {
        guard let index = selectedOverlayIndex else { return }
        var updated = overlays[index]
        mutate(&updated)
        updated.placement = updated.placement.clamped
        overlays[index] = updated
    }

    func updateOverlay(id: UUID, _ mutate: (inout OverlayItem) -> Void) {
        guard let index = overlays.firstIndex(where: { $0.id == id }) else { return }
        var updated = overlays[index]
        mutate(&updated)
        updated.placement = updated.placement.clamped
        overlays[index] = updated
    }

    func updateSelectedTextOverlay(_ mutate: (inout TextOverlayData) -> Void) {
        updateSelectedOverlay { overlay in
            guard case .text(var data) = overlay.content else { return }
            mutate(&data)
            overlay.content = .text(data)
        }
    }

    func updateSelectedQRCodeOverlay(_ mutate: (inout QROverlayData) -> Void) {
        updateSelectedOverlay { overlay in
            guard case .qrCode(var data) = overlay.content else { return }
            mutate(&data)
            overlay.content = .qrCode(data)
        }
    }

    func updateSelectedTimestampOverlay(_ mutate: (inout TimestampOverlayData) -> Void) {
        updateSelectedOverlay { overlay in
            guard case .timestamp(var data) = overlay.content else { return }
            mutate(&data)
            overlay.content = .timestamp(data)
        }
    }

    func updateSelectedImageOverlay(_ mutate: (inout ImageOverlayData) -> Void) {
        updateSelectedOverlay { overlay in
            guard case .image(var data) = overlay.content else { return }
            mutate(&data)
            overlay.content = .image(data)
        }
    }

    func updateSelectedLocationOverlay(_ mutate: (inout LocationOverlayData) -> Void) {
        updateSelectedOverlay { overlay in
            guard case .location(var data) = overlay.content else { return }
            mutate(&data)
            overlay.content = .location(data)
        }
    }

    func replaceSelectedImageOverlayAsset() {
        guard let selectedOverlay, case .image = selectedOverlay.content else { return }
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.image]
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        panel.message = L("Select an image to print")
        guard panel.runModal() == .OK,
              let url = panel.url,
              let image = NSImage(contentsOf: url),
              let tiff = image.tiffRepresentation else { return }
        updateSelectedImageOverlay { data in
            data.asset = OverlayImageAsset(fileName: url.lastPathComponent, imageData: tiff)
        }
    }

    var defaultTimestampOverlay: OverlayItem? {
        newPhotoDefaults.overlays.first { overlay in
            if case .timestamp = overlay.content {
                return true
            }
            return false
        }
    }

    func setDefaultTimestampOverlayEnabled(_ isEnabled: Bool) {
        if isEnabled {
            guard defaultTimestampOverlay == nil else { return }
            var overlay = OverlayItem(
                content: .timestamp(TimestampOverlayData()),
                placement: defaultOverlayPlacement(for: .timestamp),
                opacity: 1.0,
                zIndex: 0
            )
            overlay.isLocked = false
            newPhotoDefaults.overlays.append(overlay)
        } else {
            newPhotoDefaults.overlays.removeAll { overlay in
                if case .timestamp = overlay.content {
                    return true
                }
                return false
            }
        }
    }

    func updateDefaultTimestampOverlay(_ mutate: (inout TimestampOverlayData) -> Void) {
        guard let index = newPhotoDefaults.overlays.firstIndex(where: { overlay in
            if case .timestamp = overlay.content {
                return true
            }
            return false
        }) else { return }

        var overlay = newPhotoDefaults.overlays[index]
        guard case .timestamp(var data) = overlay.content else { return }
        mutate(&data)
        overlay.content = .timestamp(data)
        newPhotoDefaults.overlays[index] = overlay
    }

    func overlayTitle(for overlay: OverlayItem) -> String {
        switch overlay.content {
        case .text(let data):
            let trimmed = data.text.trimmingCharacters(in: .whitespacesAndNewlines)
            return trimmed.isEmpty ? L("Text") : trimmed
        case .qrCode:
            return L("QR Code")
        case .timestamp:
            return L("Timestamp")
        case .image(let data):
            return data.asset.fileName ?? L("Image")
        case .location:
            return L("Location")
        }
    }

    private func defaultOverlayContent(for kind: OverlayKind) -> OverlayContent {
        switch kind {
        case .text:
            return .text(TextOverlayData())
        case .qrCode:
            return .qrCode(QROverlayData())
        case .timestamp:
            return .timestamp(TimestampOverlayData())
        case .image:
            let emptyImage = NSImage(size: CGSize(width: 128, height: 128))
            let imageData = emptyImage.tiffRepresentation ?? Data()
            return .image(ImageOverlayData(asset: OverlayImageAsset(fileName: nil, imageData: imageData)))
        case .location:
            return .location(LocationOverlayData(coordinate: imageLocation?.coordinate))
        }
    }

    private func defaultOverlayPlacement(for kind: OverlayKind) -> OverlayPlacement {
        switch kind {
        case .qrCode:
            return OverlayPlacement(normalizedCenterX: 0.78, normalizedCenterY: 0.78, normalizedWidth: 0.22, normalizedHeight: 0.22, anchor: .center)
        case .image:
            return OverlayPlacement(normalizedCenterX: 0.78, normalizedCenterY: 0.24, normalizedWidth: 0.24, normalizedHeight: 0.24, anchor: .center)
        case .timestamp:
            return OverlayPlacement(normalizedCenterX: 0.78, normalizedCenterY: 0.9, normalizedWidth: 0.34, normalizedHeight: 0.1, anchor: .center)
        case .location:
            return OverlayPlacement(normalizedCenterX: 0.24, normalizedCenterY: 0.9, normalizedWidth: 0.34, normalizedHeight: 0.12, anchor: .center)
        case .text:
            return OverlayPlacement(normalizedCenterX: 0.5, normalizedCenterY: 0.16, normalizedWidth: 0.42, normalizedHeight: 0.14, anchor: .center)
        }
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
            showStatus(L("failed_to_save_captured_image", error.localizedDescription))
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
            imageLocation: nil,
            editState: makeCapturedQueueItemEditState()
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
    func toggleHorizontalFlip() { isHorizontallyFlipped.toggle() }

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

    // MARK: - Overlay Text Formatting

    private func nsColor(from color: OverlayColor) -> NSColor {
        NSColor(
            srgbRed: CGFloat(color.red),
            green: CGFloat(color.green),
            blue: CGFloat(color.blue),
            alpha: CGFloat(color.alpha)
        )
    }

    func timestampText(from date: Date, format: TimestampFormat, separator: String) -> String {
        let cal = Calendar.current
        let y = cal.component(.year, from: date) % 100
        let m = cal.component(.month, from: date)
        let d = cal.component(.day, from: date)
        let (yy, mm, dd) = (String(format: "%02d", y), String(format: "%02d", m), String(format: "%02d", d))
        switch format {
        case .mdy: return "\(mm)\(separator)\(dd)\(separator)\(yy)"
        case .dmy: return "\(dd)\(separator)\(mm)\(separator)\(yy)"
        case .ymd: return "\(yy)\(separator)\(mm)\(separator)\(dd)"
        }
    }

    func timeStampText(from date: Date) -> String {
        let cal = Calendar.current
        return String(format: "%02d:%02d", cal.component(.hour, from: date), cal.component(.minute, from: date))
    }

    func resolvedTimestampDate(for data: TimestampOverlayData) -> Date {
        switch data.source {
        case .photoDate:
            return imageDate ?? Date()
        case .now, .custom:
            return Date()
        }
    }

    func resolvedLocationText(for data: LocationOverlayData) -> String? {
        let coordinate: GeoCoordinate?
        switch data.source {
        case .photoMetadata:
            coordinate = imageLocation?.coordinate
        case .manualCoordinates:
            coordinate = data.coordinate
        case .manualText:
            coordinate = nil
        }

        let precision = max(0, min(data.precision, 6))
        let coordinateText: String?
        if let coordinate {
            coordinateText = String(
                format: "%.\(precision)f, %.\(precision)f",
                coordinate.latitude,
                coordinate.longitude
            )
        } else {
            coordinateText = nil
        }

        let trimmedName = data.locationName.trimmingCharacters(in: .whitespacesAndNewlines)
        let body: String?
        switch data.displayStyle {
        case .coordinates:
            body = coordinateText
        case .name:
            body = trimmedName.isEmpty ? coordinateText : trimmedName
        case .nameAndCoordinates:
            if !trimmedName.isEmpty, let coordinateText {
                body = "\(trimmedName)\n\(coordinateText)"
            } else {
                body = !trimmedName.isEmpty ? trimmedName : coordinateText
            }
        }

        guard let body, !body.isEmpty else { return nil }
        return "\(data.prefix)\(body)\(data.suffix)"
    }

    // MARK: - Overlay Rendering

    private func overlayRect(for item: OverlayItem, canvasSize: CGSize) -> CGRect {
        let rect = item.placement.rect(in: canvasSize)
        return CGRect(
            x: rect.minX,
            y: canvasSize.height - rect.maxY,
            width: rect.width,
            height: rect.height
        )
    }

    private func overlayShadow(for style: OverlayShadowStyle, color: NSColor = .black) -> NSShadow? {
        guard style != .none else { return nil }
        let shadow = NSShadow()
        shadow.shadowColor = color.withAlphaComponent(style == .strong ? 0.85 : 0.45)
        shadow.shadowBlurRadius = style == .strong ? 10 : 4
        shadow.shadowOffset = CGSize(width: 0, height: -1)
        return shadow
    }

    func qrCodeImage(for data: QROverlayData) -> NSImage? {
        guard let payload = data.payload.data(using: .utf8),
              let qrFilter = CIFilter(name: "CIQRCodeGenerator") else { return nil }
        qrFilter.setValue(payload, forKey: "inputMessage")
        qrFilter.setValue(data.correctionLevel.coreImageValue, forKey: "inputCorrectionLevel")
        guard let output = qrFilter.outputImage else { return nil }

        let falseColor = CIFilter(name: "CIFalseColor")
        falseColor?.setValue(output, forKey: kCIInputImageKey)
        falseColor?.setValue(CIColor(cgColor: nsColor(from: data.foregroundColor).cgColor), forKey: "inputColor0")
        falseColor?.setValue(CIColor(cgColor: nsColor(from: data.backgroundColor).cgColor), forKey: "inputColor1")
        let colored = falseColor?.outputImage ?? output
        let scaled = colored.transformed(by: CGAffineTransform(scaleX: 16, y: 16))
        let context = CIContext(options: nil)
        guard let cgImage = context.createCGImage(scaled, from: scaled.extent) else { return nil }
        return NSImage(cgImage: cgImage, size: NSSize(width: cgImage.width, height: cgImage.height))
    }

    private func imageFromOverlayAsset(_ asset: OverlayImageAsset) -> NSImage? {
        NSImage(data: asset.imageData)
    }

    private func drawTextOverlay(_ data: TextOverlayData, in rect: CGRect) {
        guard !data.text.isEmpty else { return }
        let fontSize = max(14, rect.height * CGFloat(max(data.fontScale, 0.05)) * 1.8)
        let font = NSFont(name: data.fontName, size: fontSize) ?? NSFont.systemFont(ofSize: fontSize, weight: .semibold)
        let paragraph = NSMutableParagraphStyle()
        switch data.textAlignment {
        case .leading:
            paragraph.alignment = .left
        case .center:
            paragraph.alignment = .center
        case .trailing:
            paragraph.alignment = .right
        }
        paragraph.lineBreakMode = data.allowsMultipleLines ? .byWordWrapping : .byTruncatingTail

        var attributes: [NSAttributedString.Key: Any] = [
            .font: font,
            .foregroundColor: nsColor(from: data.foregroundColor),
            .paragraphStyle: paragraph,
        ]
        if let shadow = overlayShadow(for: data.shadowStyle) {
            attributes[.shadow] = shadow
        }

        if data.backgroundColor.alpha > 0.01 {
            nsColor(from: data.backgroundColor).setFill()
            NSBezierPath(roundedRect: rect.insetBy(dx: -6, dy: -4), xRadius: 12, yRadius: 12).fill()
        }

        NSAttributedString(string: data.text, attributes: attributes).draw(
            with: rect,
            options: [.usesLineFragmentOrigin, .usesFontLeading],
            context: nil
        )
    }

    private func drawTimestampOverlay(_ data: TimestampOverlayData, in rect: CGRect) {
        let preset = Self.dateStampPresets[data.presetKey] ?? Self.dateStampPresets["classic"]!
        let date = resolvedTimestampDate(for: data)
        let body = data.showsTime
            ? "\(timestampText(from: date, format: data.format, separator: preset.separator))\n\(timeStampText(from: date))"
            : timestampText(from: date, format: data.format, separator: preset.separator)
        let fontSize = max(12, rect.height * (data.showsTime ? 0.34 : 0.58))
        let font = NSFont(name: preset.fontFamily, size: fontSize) ?? NSFont.monospacedDigitSystemFont(ofSize: fontSize, weight: .medium)
        let paragraph = NSMutableParagraphStyle()
        paragraph.alignment = .center

        var attributes: [NSAttributedString.Key: Any] = [
            .font: font,
            .foregroundColor: NSColor(srgbRed: preset.color.0, green: preset.color.1, blue: preset.color.2, alpha: 1),
            .paragraphStyle: paragraph,
            .kern: fontSize * preset.tracking,
        ]
        if data.lightBleedEnabled && preset.glowRadius > 0 {
            let glow = NSShadow()
            glow.shadowColor = NSColor(srgbRed: preset.glowColor.0, green: preset.glowColor.1, blue: preset.glowColor.2, alpha: 0.65)
            glow.shadowBlurRadius = fontSize * preset.glowRadius
            glow.shadowOffset = .zero
            attributes[.shadow] = glow
        }

        NSAttributedString(string: body, attributes: attributes).draw(
            with: rect,
            options: [.usesLineFragmentOrigin, .usesFontLeading],
            context: nil
        )
    }

    private func drawLocationOverlay(_ data: LocationOverlayData, in rect: CGRect) {
        guard let text = resolvedLocationText(for: data) else { return }
        let font = NSFont.monospacedSystemFont(ofSize: max(10, rect.height * 0.28), weight: .medium)
        let paragraph = NSMutableParagraphStyle()
        paragraph.alignment = .center
        var attributes: [NSAttributedString.Key: Any] = [
            .font: font,
            .foregroundColor: NSColor.white,
            .paragraphStyle: paragraph,
        ]
        if let shadow = overlayShadow(for: .soft) {
            attributes[.shadow] = shadow
        }
        NSAttributedString(string: text, attributes: attributes).draw(
            with: rect,
            options: [.usesLineFragmentOrigin, .usesFontLeading],
            context: nil
        )
    }

    private func drawImageOverlay(_ data: ImageOverlayData, in rect: CGRect) {
        guard let image = imageFromOverlayAsset(data.asset) else { return }
        if data.showsBacking {
            nsColor(from: data.backingColor).setFill()
            NSBezierPath(
                roundedRect: rect,
                xRadius: CGFloat(data.cornerRadius),
                yRadius: CGFloat(data.cornerRadius)
            ).fill()
        }

        NSGraphicsContext.current?.saveGraphicsState()
        NSBezierPath(
            roundedRect: rect,
            xRadius: CGFloat(data.cornerRadius),
            yRadius: CGFloat(data.cornerRadius)
        ).addClip()

        let imageRect: CGRect
        switch data.contentMode {
        case .fit:
            imageRect = AVMakeRect(aspectRatio: image.size, insideRect: rect)
        case .fill:
            let fitRect = AVMakeRect(aspectRatio: image.size, insideRect: rect)
            let scale = max(rect.width / max(fitRect.width, 1), rect.height / max(fitRect.height, 1))
            let scaledSize = CGSize(width: fitRect.width * scale, height: fitRect.height * scale)
            imageRect = CGRect(
                x: rect.midX - scaledSize.width / 2,
                y: rect.midY - scaledSize.height / 2,
                width: scaledSize.width,
                height: scaledSize.height
            )
        }
        image.draw(in: imageRect)
        NSGraphicsContext.current?.restoreGraphicsState()
    }

    private func drawQRCodeOverlay(_ data: QROverlayData, in rect: CGRect) {
        guard let image = qrCodeImage(for: data) else { return }
        let codeRect: CGRect
        if data.showsCaption {
            codeRect = CGRect(x: rect.minX, y: rect.minY + rect.height * 0.16, width: rect.width, height: rect.height * 0.84)
        } else {
            codeRect = rect
        }
        let drawRect = data.includesQuietZone
            ? codeRect.insetBy(dx: codeRect.width * 0.08, dy: codeRect.height * 0.08)
            : codeRect
        image.draw(in: drawRect)

        if data.showsCaption, !data.caption.isEmpty {
            let font = NSFont.systemFont(ofSize: max(10, rect.height * 0.11), weight: .medium)
            let paragraph = NSMutableParagraphStyle()
            paragraph.alignment = .center
            let attributes: [NSAttributedString.Key: Any] = [
                .font: font,
                .foregroundColor: nsColor(from: data.foregroundColor),
                .paragraphStyle: paragraph,
            ]
            NSAttributedString(string: data.caption, attributes: attributes).draw(
                with: CGRect(x: rect.minX, y: rect.minY, width: rect.width, height: rect.height * 0.16),
                options: [.usesLineFragmentOrigin, .usesFontLeading],
                context: nil
            )
        }
    }

    func composeOverlays(on cgImage: CGImage) -> CGImage? {
        let visibleOverlays = overlays
            .filter { !$0.isHidden }
            .sorted { $0.zIndex < $1.zIndex }
        guard !visibleOverlays.isEmpty else { return nil }

        let rep = NSBitmapImageRep(
            bitmapDataPlanes: nil,
            pixelsWide: cgImage.width,
            pixelsHigh: cgImage.height,
            bitsPerSample: 8,
            samplesPerPixel: 4,
            hasAlpha: true,
            isPlanar: false,
            colorSpaceName: .deviceRGB,
            bitmapFormat: [],
            bytesPerRow: 0,
            bitsPerPixel: 0
        )
        guard let rep,
              let graphicsContext = NSGraphicsContext(bitmapImageRep: rep) else { return nil }

        let baseImage = NSImage(cgImage: cgImage, size: NSSize(width: cgImage.width, height: cgImage.height))
        let canvasSize = CGSize(width: cgImage.width, height: cgImage.height)

        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = graphicsContext
        graphicsContext.imageInterpolation = .high

        baseImage.draw(in: CGRect(origin: .zero, size: canvasSize))
        for overlay in visibleOverlays {
            let rect = overlayRect(for: overlay, canvasSize: canvasSize)
            NSGraphicsContext.current?.cgContext.saveGState()
            NSGraphicsContext.current?.cgContext.setAlpha(CGFloat(overlay.opacity))
            switch overlay.content {
            case .text(let data):
                drawTextOverlay(data, in: rect)
            case .qrCode(let data):
                drawQRCodeOverlay(data, in: rect)
            case .timestamp(let data):
                drawTimestampOverlay(data, in: rect)
            case .image(let data):
                drawImageOverlay(data, in: rect)
            case .location(let data):
                drawLocationOverlay(data, in: rect)
            }
            NSGraphicsContext.current?.cgContext.restoreGState()
        }

        NSGraphicsContext.restoreGraphicsState()
        return rep.cgImage
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

    private func flipCGImageHorizontally(_ cgImage: CGImage) -> CGImage? {
        let w = cgImage.width
        let h = cgImage.height
        let colorSpace = CGColorSpaceCreateDeviceRGB()

        guard let context = CGContext(
            data: nil, width: w, height: h,
            bitsPerComponent: 8, bytesPerRow: 0, space: colorSpace,
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        ) else { return nil }

        context.translateBy(x: CGFloat(w), y: 0)
        context.scaleBy(x: -1, y: 1)
        context.draw(cgImage, in: CGRect(x: 0, y: 0, width: w, height: h))
        return context.makeImage()
    }

    // MARK: - Print Preparation

    @MainActor
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

        if isHorizontallyFlipped, let flipped = flipCGImageHorizontally(currentCG) {
            currentCG = flipped
            processed = true
        }

        if rotationAngle != 0, let rotated = rotateCGImage(currentCG, degrees: rotationAngle) {
            currentCG = rotated
            processed = true
        }

        if let composited = composeOverlays(on: currentCG) {
            currentCG = composited
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
        guard let prepared = await prepareImageForPrint() else { return }
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

    func printQueue(startingAt startIndex: Int? = nil) async {
        let firstIndex = startIndex ?? selectedQueueIndex
        guard queue.indices.contains(firstIndex) else { return }

        let count = min(queue.count - firstIndex, filmRemaining)
        guard count > 0 else { return }

        await MainActor.run {
            isPrinting = true
            printProgress = nil
            batchPrintIndex = 0
            batchPrintTotal = count
        }

        for offset in 0..<count {
            let queueIndex = firstIndex + offset
            await MainActor.run {
                batchPrintIndex = offset + 1
                selectQueueItem(at: queueIndex)
            }

            guard let prepared = await prepareImageForPrint() else {
                await MainActor.run {
                    isPrinting = false
                    batchPrintTotal = 0
                    showStatus(L("print_failed_at", offset + 1, count))
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
                    showStatus(L("print_failed_at", offset + 1, count))
                }
                return
            }

            await refreshStatus()

            let remaining = await MainActor.run { filmRemaining }
            if remaining <= 0 && offset < count - 1 {
                await MainActor.run {
                    isPrinting = false
                    batchPrintTotal = 0
                    showStatus(L("film_ran_out", offset + 1, count))
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

    private var showsSimulatedFilmFrame: Bool {
        viewModel.printerModelTag != nil &&
        ((viewModel.cameraState == .viewfinder && viewModel.captureSession != nil) ||
         (viewModel.cameraState == .preview && viewModel.capturedImage != nil))
    }

    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 8)
                .fill(Color(nsColor: .controlBackgroundColor))
            RoundedRectangle(cornerRadius: 8)
                .strokeBorder(style: StrokeStyle(lineWidth: 2), antialiased: true)
                .foregroundColor(showsSimulatedFilmFrame ? .clear : .secondary.opacity(0.5))

            if viewModel.cameraState == .viewfinder {
                if let session = viewModel.captureSession {
                    let isFront = viewModel.selectedCamera?.position == .front
                    FilmFrameView(filmModel: viewModel.printerModelTag,
                                  isRotated: viewModel.filmOrientation == "rotated") {
                        if let ar = viewModel.orientedAspectRatio {
                            CameraPreviewView(session: session, isMirrored: isFront)
                                .scaleEffect(x: viewModel.isHorizontallyFlipped ? -1 : 1, y: 1)
                                .aspectRatio(ar, contentMode: .fill)
                                .overlay {
                                    OverlayCanvasView()
                                }
                                .clipped()
                        } else {
                            CameraPreviewView(session: session, isMirrored: isFront)
                                .scaleEffect(x: viewModel.isHorizontallyFlipped ? -1 : 1, y: 1)
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
                            .scaleEffect(x: viewModel.isHorizontallyFlipped ? -1 : 1, y: 1)
                            .frame(maxWidth: .infinity, maxHeight: .infinity)
                            .aspectRatio(ar, contentMode: .fit)
                            .overlay {
                                OverlayCanvasView()
                            }
                            .clipped()
                    } else {
                        Image(nsImage: image)
                            .resizable()
                            .scaleEffect(x: viewModel.isHorizontallyFlipped ? -1 : 1, y: 1)
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
                        HStack(spacing: 8) {
                            Button {
                                viewModel.filmOrientation = viewModel.filmOrientation == "default" ? "rotated" : "default"
                            } label: {
                                HStack(spacing: 4) {
                                    Image(systemName: viewModel.filmOrientation == "default"
                                        ? "rectangle.portrait" : "rectangle")
                                    Image(systemName: "arrow.triangle.2.circlepath")
                                        .font(.system(size: 8, weight: .semibold))
                                }
                                .font(.callout)
                            }
                            .help(L("Film Orientation"))

                            Button {
                                viewModel.toggleHorizontalFlip()
                            } label: {
                                HStack(spacing: 4) {
                                    Image(systemName: "arrow.left.and.right")
                                    Text(L("Flip"))
                                }
                                .font(.callout)
                                .foregroundColor(viewModel.isHorizontallyFlipped ? .accentColor : .primary)
                            }
                            .buttonStyle(.bordered)
                            .help(L("Flip"))
                        }
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
    @State private var isQueueStripVisible = false
    @State private var lastQueueCount = 0

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
                            withAnimation(.easeInOut(duration: 0.2)) {
                                isQueueStripVisible = false
                            }
                        } else {
                            viewModel.cancelTimer()
                            viewModel.stopCameraSession()
                            viewModel.cameraState = .viewfinder
                            viewModel.capturedImage = nil
                            syncQueueStripVisibility(for: viewModel.queue.count, force: true)
                        }
                    }

                    if viewModel.captureMode == .file && !viewModel.queue.isEmpty {
                        Button {
                            withAnimation(.easeInOut(duration: 0.2)) {
                                isQueueStripVisible.toggle()
                            }
                        } label: {
                            HStack(spacing: 5) {
                                Image(systemName: isQueueStripVisible ? "square.stack.3d.up.fill" : "square.stack.3d.up")
                                    .font(.caption)
                                Text("\(viewModel.queue.count)")
                                    .font(.caption2)
                                    .fontWeight(.semibold)
                                    .monospacedDigit()
                            }
                            .padding(.horizontal, 8)
                            .padding(.vertical, 4)
                            .background(
                                Capsule()
                                    .fill(isQueueStripVisible ? Color.accentColor.opacity(0.18) : Color.secondary.opacity(0.12))
                            )
                            .foregroundColor(isQueueStripVisible ? .accentColor : .secondary)
                        }
                        .buttonStyle(.plain)
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

                if viewModel.captureMode == .file && isQueueStripVisible {
                    QueueStripView()
                        .padding(.horizontal, 14)
                        .padding(.top, 8)
                        .transition(.move(edge: .bottom).combined(with: .opacity))
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
            lastQueueCount = viewModel.queue.count
            syncQueueStripVisibility(for: viewModel.queue.count, force: true)
            // Auto-start pairing on launch
            if !viewModel.isConnected && !viewModel.isPairing {
                viewModel.startPairing()
            }
            // Silent update check on launch
            Task { await viewModel.checkForUpdates() }
        }
        .onChange(of: viewModel.queue.count) { newCount in
            syncQueueStripVisibility(for: newCount)
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

    private func syncQueueStripVisibility(for newCount: Int, force: Bool = false) {
        defer { lastQueueCount = newCount }

        guard viewModel.captureMode == .file else {
            if force {
                isQueueStripVisible = false
            }
            return
        }

        if newCount == 0 {
            withAnimation(.easeInOut(duration: 0.2)) {
                isQueueStripVisible = false
            }
            return
        }

        if force {
            isQueueStripVisible = newCount > 1
            return
        }

        if lastQueueCount <= 1 && newCount > 1 {
            withAnimation(.easeInOut(duration: 0.2)) {
                isQueueStripVisible = true
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

// MARK: - Overlay Preview

struct OverlayCanvasView: View {
    @EnvironmentObject var viewModel: ViewModel
    var editable: Bool = false

    var body: some View {
        GeometryReader { geo in
            ZStack {
                ForEach(viewModel.overlays.filter { !$0.isHidden }.sorted(by: { $0.zIndex < $1.zIndex })) { item in
                    OverlayPreviewItemView(
                        item: item,
                        canvasSize: geo.size,
                        editable: editable,
                        isSelected: editable && viewModel.selectedOverlayID == item.id
                    )
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .allowsHitTesting(editable)
    }
}

struct OverlayPreviewItemView: View {
    @EnvironmentObject var viewModel: ViewModel
    let item: OverlayItem
    let canvasSize: CGSize
    let editable: Bool
    let isSelected: Bool

    @State private var dragOrigin: OverlayPlacement?

    private var frame: CGRect {
        item.placement.rect(in: canvasSize)
    }

    var body: some View {
        previewContent
            .frame(width: frame.width, height: frame.height)
            .background(
                RoundedRectangle(cornerRadius: 10)
                    .fill(isSelected ? Color.accentColor.opacity(0.12) : Color.clear)
            )
            .overlay(
                RoundedRectangle(cornerRadius: 10)
                    .stroke(isSelected ? Color.accentColor : Color.clear, style: StrokeStyle(lineWidth: 1.5, dash: [5, 4]))
            )
            .opacity(item.opacity)
            .position(x: frame.midX, y: frame.midY)
            .onTapGesture {
                guard editable else { return }
                viewModel.selectOverlay(item.id)
            }
            .gesture(
                DragGesture()
                    .onChanged { value in
                        guard editable, !item.isLocked else { return }
                        if dragOrigin == nil {
                            dragOrigin = item.placement
                        }
                        guard let dragOrigin else { return }
                        viewModel.selectOverlay(item.id)
                        viewModel.updateOverlay(id: item.id) { overlay in
                            overlay.placement.normalizedCenterX = dragOrigin.normalizedCenterX + Double(value.translation.width / max(canvasSize.width, 1))
                            overlay.placement.normalizedCenterY = dragOrigin.normalizedCenterY + Double(value.translation.height / max(canvasSize.height, 1))
                        }
                    }
                    .onEnded { _ in
                        dragOrigin = nil
                    }
            )
    }

    @ViewBuilder
    private var previewContent: some View {
        switch item.content {
        case .text(let data):
            OverlayTextPreviewView(data: data, size: frame.size)
        case .qrCode(let data):
            OverlayQRCodePreviewView(data: data)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        case .timestamp(let data):
            TimestampPreviewView(data: data, size: frame.size)
        case .image(let data):
            OverlayImagePreviewView(data: data)
        case .location(let data):
            OverlayLocationPreviewView(data: data, size: frame.size)
        }
    }
}

struct OverlayTextPreviewView: View {
    let data: TextOverlayData
    let size: CGSize

    var body: some View {
        Text(data.text)
            .font(.system(size: max(12, size.height * CGFloat(max(data.fontScale, 0.05)) * 1.6), weight: .semibold, design: .rounded))
            .foregroundColor(data.foregroundColor.color)
            .multilineTextAlignment(textAlignment)
            .lineLimit(data.allowsMultipleLines ? 3 : 1)
            .minimumScaleFactor(0.4)
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(
                RoundedRectangle(cornerRadius: 10)
                    .fill(data.backgroundColor.color)
            )
            .shadow(color: shadowColor, radius: shadowRadius)
    }

    private var textAlignment: TextAlignment {
        switch data.textAlignment {
        case .leading: return .leading
        case .center: return .center
        case .trailing: return .trailing
        }
    }

    private var shadowColor: Color {
        switch data.shadowStyle {
        case .none: return .clear
        case .soft: return .black.opacity(0.35)
        case .strong: return .black.opacity(0.65)
        }
    }

    private var shadowRadius: CGFloat {
        switch data.shadowStyle {
        case .none: return 0
        case .soft: return 4
        case .strong: return 8
        }
    }
}

struct OverlayQRCodePreviewView: View {
    @EnvironmentObject var viewModel: ViewModel
    let data: QROverlayData

    var body: some View {
        VStack(spacing: 4) {
            if let image = viewModel.qrCodeImage(for: data) {
                Image(nsImage: image)
                    .resizable()
                    .interpolation(.none)
                    .aspectRatio(1, contentMode: .fit)
            } else {
                RoundedRectangle(cornerRadius: 8)
                    .fill(Color.secondary.opacity(0.15))
                    .overlay(Image(systemName: "qrcode").foregroundColor(.secondary))
            }
            if data.showsCaption, !data.caption.isEmpty {
                Text(data.caption)
                    .font(.caption2)
                    .foregroundColor(data.foregroundColor.color)
                    .multilineTextAlignment(.center)
                    .lineLimit(2)
                    .minimumScaleFactor(0.5)
            }
        }
        .padding(6)
        .background(
            RoundedRectangle(cornerRadius: 10)
                .fill(data.backgroundColor.color)
        )
    }
}

struct TimestampPreviewView: View {
    @EnvironmentObject var viewModel: ViewModel
    let data: TimestampOverlayData
    let size: CGSize

    var body: some View {
        let date = viewModel.resolvedTimestampDate(for: data)
        let preset = ViewModel.dateStampPresets[data.presetKey] ?? ViewModel.dateStampPresets["classic"]!
        let stampColor = Color(red: preset.color.0, green: preset.color.1, blue: preset.color.2)
        let fontSize = max(10, size.height * (data.showsTime ? 0.32 : 0.52))

        VStack(spacing: fontSize * 0.12) {
            Text(viewModel.timestampText(from: date, format: data.format, separator: preset.separator))
                .font(.custom(preset.fontFamily, size: fontSize))
                .tracking(fontSize * preset.tracking)
                .foregroundColor(stampColor)
            if data.showsTime {
                Text(viewModel.timeStampText(from: date))
                    .font(.custom(preset.fontFamily, size: fontSize))
                    .tracking(fontSize * preset.tracking)
                    .foregroundColor(stampColor)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .shadow(
            color: data.lightBleedEnabled && preset.glowRadius > 0 ? stampColor.opacity(0.8) : .clear,
            radius: data.lightBleedEnabled ? fontSize * preset.glowRadius * 0.5 : 0
        )
    }
}

struct OverlayImagePreviewView: View {
    let data: ImageOverlayData

    var body: some View {
        let image = NSImage(data: data.asset.imageData)
        ZStack {
            if data.showsBacking {
                RoundedRectangle(cornerRadius: data.cornerRadius)
                    .fill(data.backingColor.color)
            }
            if let image {
                Image(nsImage: image)
                    .resizable()
                    .aspectRatio(contentMode: data.contentMode == .fit ? .fit : .fill)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .clipShape(RoundedRectangle(cornerRadius: data.cornerRadius))
            } else {
                RoundedRectangle(cornerRadius: data.cornerRadius)
                    .fill(Color.secondary.opacity(0.12))
                    .overlay(Image(systemName: "photo").foregroundColor(.secondary))
            }
        }
        .clipShape(RoundedRectangle(cornerRadius: data.cornerRadius))
    }
}

struct OverlayLocationPreviewView: View {
    @EnvironmentObject var viewModel: ViewModel
    let data: LocationOverlayData
    let size: CGSize

    var body: some View {
        Text(viewModel.resolvedLocationText(for: data) ?? L("No location metadata"))
            .font(.system(size: max(10, size.height * 0.22), weight: .medium, design: .monospaced))
            .foregroundColor(.white)
            .multilineTextAlignment(.center)
            .lineLimit(3)
            .minimumScaleFactor(0.5)
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(Color.black.opacity(0.28))
            .clipShape(RoundedRectangle(cornerRadius: 10))
            .shadow(color: .black.opacity(0.35), radius: 4)
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

// MARK: - Main Preview View (read-only, in main window)

struct MainPreviewView: View {
    @EnvironmentObject var viewModel: ViewModel
    @State private var isTargeted = false
    @GestureState private var dragDelta: CGSize = .zero
    @GestureState private var magnifyDelta: CGFloat = 1.0
    @State private var localFrameSize: CGSize = .zero
    var openEditor: () -> Void

    private var showsSimulatedFilmFrame: Bool {
        viewModel.selectedImage != nil && viewModel.printerModelTag != nil
    }

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
                .foregroundColor(
                    viewModel.selectedImage == nil
                        ? (isTargeted ? .accentColor : .secondary.opacity(0.5))
                        : (showsSimulatedFilmFrame ? .clear : .secondary.opacity(0.5))
                )

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
                                        .scaleEffect(
                                            x: viewModel.isHorizontallyFlipped ? -effectiveZoom : effectiveZoom,
                                            y: effectiveZoom
                                        )
                                        .offset(effectiveOffset(imageSize: image.size))
                                        .rotationEffect(.degrees(Double(viewModel.rotationAngle)))
                                )
                                .overlay {
                                    OverlayCanvasView()
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
                                            viewModel.cropOffset = viewModel.clampedCropOffset(
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
                                            viewModel.setCropZoom(viewModel.cropZoom * value)
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
                                        .scaleEffect(x: viewModel.isHorizontallyFlipped ? -1 : 1, y: 1)
                                        .rotationEffect(.degrees(Double(viewModel.rotationAngle)))
                                )
                                .overlay {
                                    OverlayCanvasView()
                                }
                                .clipped()
                                .onTapGesture(count: 2) { openEditor() }
                        } else {
                            Image(nsImage: image)
                                .resizable()
                                .aspectRatio(contentMode: .fit)
                                .scaleEffect(x: viewModel.isHorizontallyFlipped ? -1 : 1, y: 1)
                                .rotationEffect(.degrees(Double(viewModel.rotationAngle)))
                                .overlay {
                                    OverlayCanvasView()
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
        min(max(viewModel.cropZoom * magnifyDelta, ViewModel.minCropZoom), ViewModel.maxCropZoom)
    }

    private func effectiveOffset(imageSize: CGSize) -> CGSize {
        let raw = CGSize(
            width: viewModel.cropOffset.width + dragDelta.width,
            height: viewModel.cropOffset.height + dragDelta.height
        )
        return viewModel.clampedCropOffset(
            raw: raw,
            imageSize: imageSize,
            frameSize: localFrameSize,
            zoom: effectiveZoom
        )
    }
}

// MARK: - Queue Strip View

struct QueueStripView: View {
    @EnvironmentObject var viewModel: ViewModel
    @State private var draggingItemID: UUID?

    private let thumbnailHeight: CGFloat = 44

    private var addButtonWidth: CGFloat {
        let aspectRatio = viewModel.orientedAspectRatio ?? (36.0 / thumbnailHeight)
        return max(36, thumbnailHeight * aspectRatio)
    }

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    ForEach(Array(viewModel.queue.enumerated()), id: \.element.id) { index, item in
                        QueueThumbnailView(
                            item: item,
                            isSelected: index == viewModel.selectedQueueIndex,
                            isDragging: draggingItemID == item.id,
                            onSelect: { viewModel.selectQueueItem(at: index) },
                            onRemove: { withAnimation { viewModel.removeQueueItem(at: index) } }
                        )
                        .id(item.id)
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
                            .frame(width: addButtonWidth, height: thumbnailHeight)
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
    @EnvironmentObject var viewModel: ViewModel
    let item: QueueItem
    let isSelected: Bool
    var isDragging: Bool = false
    let onSelect: () -> Void
    let onRemove: () -> Void

    private let thumbnailHeight: CGFloat = 44

    private var thumbnailAspectRatio: CGFloat {
        viewModel.orientedAspectRatio(for: item.editState.filmOrientation) ?? (36.0 / thumbnailHeight)
    }

    private var thumbnailWidth: CGFloat {
        max(36, thumbnailHeight * thumbnailAspectRatio)
    }

    var body: some View {
        ZStack(alignment: .topTrailing) {
            Button(action: onSelect) {
                Image(nsImage: item.image)
                    .resizable()
                    .aspectRatio(contentMode: .fill)
                    .scaleEffect(x: item.editState.isHorizontallyFlipped ? -1 : 1, y: 1)
                    .rotationEffect(.degrees(Double(item.editState.rotationAngle)))
                    .frame(width: thumbnailWidth, height: thumbnailHeight)
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
            .buttonStyle(.plain)

            Button(action: onRemove) {
                Image(systemName: "xmark.circle.fill")
                    .font(.system(size: 12, weight: .semibold))
                    .symbolRenderingMode(.hierarchical)
                    .foregroundColor(.white.opacity(0.95))
                    .background(
                        Circle()
                            .fill(Color.black.opacity(0.55))
                    )
            }
            .buttonStyle(.plain)
            .padding(3)
            .help(L("Remove"))
            .accessibilityLabel(Text(L("Remove")))
        }
        .frame(width: thumbnailWidth, height: thumbnailHeight)
    }
}

// MARK: - Main Actions View (Edit + Print buttons, in main window)

struct MainActionsView: View {
    @EnvironmentObject var viewModel: ViewModel
    var openEditor: () -> Void

    private var singlePrintLabel: String {
        if viewModel.isPrinting {
            if viewModel.batchPrintTotal > 1 {
                return L("printing_n_of_m", viewModel.batchPrintIndex, viewModel.batchPrintTotal)
            }
            return viewModel.printProgress.map { L("transfer_progress", $0.sent, $0.total) } ?? L("Preparing...")
        }
        return L("Print")
    }

    private var nextPrintLabel: String {
        L("print_next_n", viewModel.printableQueueCountFromSelection)
    }

    var body: some View {
        VStack(spacing: 10) {
            HStack(spacing: 10) {
                QuickPrintAdjustmentsView()
                    .frame(maxWidth: .infinity)

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
                .frame(maxWidth: .infinity)
            }

            if viewModel.queue.count > 1 {
                HStack(spacing: 10) {
                    Button {
                        Task { await viewModel.printSelectedImage() }
                    } label: {
                        HStack {
                            Image(systemName: "printer")
                            Text(L("Print Current"))
                        }
                        .frame(maxWidth: .infinity)
                    }
                    .controlSize(.large)
                    .disabled(viewModel.selectedImage == nil || !viewModel.isConnected || viewModel.isPrinting)

                    Button {
                        Task { await viewModel.printQueue(startingAt: viewModel.selectedQueueIndex) }
                    } label: {
                        HStack {
                            Image(systemName: "printer.fill")
                            Text(nextPrintLabel)
                        }
                        .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
                    .disabled(
                        viewModel.selectedImage == nil ||
                        !viewModel.isConnected ||
                        viewModel.isPrinting ||
                        viewModel.printableQueueCountFromSelection == 0
                    )
                }
            } else {
                Button {
                    Task { await viewModel.printSelectedImage() }
                } label: {
                    HStack {
                        if viewModel.isPrinting {
                            ProgressView()
                                .controlSize(.small)
                                .padding(.trailing, 2)
                        } else {
                            Image(systemName: "printer.fill")
                        }
                        Text(singlePrintLabel)
                    }
                    .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
                .disabled(viewModel.selectedImage == nil || !viewModel.isConnected || viewModel.isPrinting)
            }
        }
    }
}

struct QuickPrintAdjustmentsView: View {
    @EnvironmentObject var viewModel: ViewModel

    var body: some View {
        HStack(spacing: 8) {
            QuickZoomControlsView(showsChrome: false)
                .layoutPriority(1)

            Button {
                viewModel.rotateClockwise()
            } label: {
                Image(systemName: "rotate.right")
            }
            .buttonStyle(.bordered)
            .controlSize(.small)
            .disabled(viewModel.selectedImage == nil)
            .help(L("Rotate Right"))
            .accessibilityLabel(Text(L("Rotate Right")))

            if viewModel.printerAspectRatio != nil {
                Button {
                    viewModel.filmOrientation = viewModel.filmOrientation == "default" ? "rotated" : "default"
                } label: {
                    HStack(spacing: 4) {
                        Image(systemName: viewModel.filmOrientation == "default"
                            ? "rectangle.portrait" : "rectangle")
                        Image(systemName: "arrow.triangle.2.circlepath")
                            .font(.system(size: 8, weight: .semibold))
                    }
                    .font(.callout)
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
                .tint(viewModel.filmOrientation == "rotated" ? .accentColor : .secondary)
                .disabled(viewModel.selectedImage == nil)
                .help(L("Film Orientation"))
                .accessibilityLabel(Text(L("Film Orientation")))
            }
        }
    }
}

struct QuickZoomControlsView: View {
    @EnvironmentObject var viewModel: ViewModel
    var showsChrome: Bool = true

    var body: some View {
        ControlGroup {
            Button {
                viewModel.quickZoomOut()
            } label: {
                Image(systemName: "minus")
            }
            .disabled(!viewModel.canQuickZoomOut)
            .help(L("Zoom Out"))
            .accessibilityLabel(Text(L("Zoom Out")))

            Button(L("Reset")) {
                viewModel.resetCropAdjustments()
            }
            .disabled(!viewModel.canResetCropAdjustments)

            Button {
                viewModel.quickZoomIn()
            } label: {
                Image(systemName: "plus")
            }
            .disabled(!viewModel.canQuickZoomIn)
            .help(L("Zoom In"))
            .accessibilityLabel(Text(L("Zoom In")))
        }
        .controlSize(.small)
        .padding(.horizontal, 12)
        .frame(maxWidth: .infinity, minHeight: 36)
        .background {
            if showsChrome {
                RoundedRectangle(cornerRadius: 8)
                    .fill(Color(nsColor: .controlBackgroundColor))
            }
        }
        .overlay {
            if showsChrome {
                RoundedRectangle(cornerRadius: 8)
                    .strokeBorder(Color.secondary.opacity(0.18))
            }
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

    private var showsSimulatedFilmFrame: Bool {
        viewModel.selectedImage != nil && viewModel.printerModelTag != nil
    }

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
                .foregroundColor(
                    viewModel.selectedImage == nil
                        ? (isTargeted ? .accentColor : .secondary.opacity(0.5))
                        : (showsSimulatedFilmFrame ? .clear : .secondary.opacity(0.5))
                )

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
                                    .scaleEffect(
                                        x: viewModel.isHorizontallyFlipped ? -effectiveZoom : effectiveZoom,
                                        y: effectiveZoom
                                    )
                                    .offset(effectiveOffset(imageSize: image.size))
                                    .rotationEffect(.degrees(Double(viewModel.rotationAngle)))
                            )
                            .overlay {
                                OverlayCanvasView(editable: true)
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
                                        viewModel.cropOffset = viewModel.clampedCropOffset(
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
                                        viewModel.setCropZoom(viewModel.cropZoom * value)
                                    }
                            )
                    } else if viewModel.fitMode == "contain", let ar = viewModel.orientedAspectRatio {
                        Color.white
                            .aspectRatio(ar, contentMode: .fit)
                            .overlay(
                                Image(nsImage: image)
                                    .resizable()
                                    .aspectRatio(contentMode: .fit)
                                    .scaleEffect(x: viewModel.isHorizontallyFlipped ? -1 : 1, y: 1)
                                    .rotationEffect(.degrees(Double(viewModel.rotationAngle)))
                            )
                            .overlay {
                                OverlayCanvasView(editable: true)
                            }
                            .clipped()
                    } else {
                        Image(nsImage: image)
                            .resizable()
                            .aspectRatio(contentMode: .fit)
                            .scaleEffect(x: viewModel.isHorizontallyFlipped ? -1 : 1, y: 1)
                            .rotationEffect(.degrees(Double(viewModel.rotationAngle)))
                            .overlay {
                                OverlayCanvasView(editable: true)
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
        min(max(viewModel.cropZoom * magnifyDelta, ViewModel.minCropZoom), ViewModel.maxCropZoom)
    }

    private func effectiveOffset(imageSize: CGSize) -> CGSize {
        let raw = CGSize(
            width: viewModel.cropOffset.width + dragDelta.width,
            height: viewModel.cropOffset.height + dragDelta.height
        )
        return viewModel.clampedCropOffset(
            raw: raw,
            imageSize: imageSize,
            frameSize: localFrameSize,
            zoom: effectiveZoom
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

                    QuickZoomControlsView()
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

                        Button {
                            viewModel.toggleHorizontalFlip()
                        } label: {
                            Label(L("Flip"), systemImage: "arrow.left.and.right")
                        }
                        .controlSize(.small)
                        .buttonStyle(.bordered)
                        .tint(viewModel.isHorizontallyFlipped ? .accentColor : .secondary)

                        Spacer()
                    }
                }

                Divider()

                // Overlays
                AccordionSection(L("Overlays"), icon: "sparkles", expanded: true) {
                    Menu {
                        Button(L("Text")) { viewModel.addOverlay(kind: .text) }
                        Button(L("QR Code")) { viewModel.addOverlay(kind: .qrCode) }
                        Button(L("Timestamp")) { viewModel.addOverlay(kind: .timestamp) }
                        Button(L("Image")) { viewModel.addOverlay(kind: .image) }
                        Button(L("Location")) { viewModel.addOverlay(kind: .location) }
                    } label: {
                        Label(L("Add Overlay"), systemImage: "plus")
                            .frame(maxWidth: .infinity)
                    }
                    .controlSize(.small)

                    if viewModel.overlays.isEmpty {
                        Text(L("No overlays yet"))
                            .font(.caption)
                            .foregroundColor(.secondary)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(.vertical, 4)
                    } else {
                        VStack(spacing: 6) {
                            ForEach(viewModel.overlays.sorted(by: { $0.zIndex < $1.zIndex })) { overlay in
                                OverlayListRowView(overlay: overlay)
                            }
                        }
                    }

                    if viewModel.selectedOverlay != nil {
                        Divider().padding(.vertical, 4)
                        SelectedOverlayInspectorView()
                    } else {
                        Text(L("Select an overlay to edit"))
                            .font(.caption)
                            .foregroundColor(.secondary)
                            .frame(maxWidth: .infinity, alignment: .leading)
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

            DefaultTimestampOverlayEditor()

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

struct OverlayListRowView: View {
    @EnvironmentObject var viewModel: ViewModel
    let overlay: OverlayItem

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: symbolName)
                .frame(width: 16)
                .foregroundColor(isSelected ? .accentColor : .secondary)

            Text(viewModel.overlayTitle(for: overlay))
                .font(.callout)
                .lineLimit(1)
                .foregroundColor(.primary)

            Spacer()

            Button {
                viewModel.updateOverlay(id: overlay.id) { item in
                    item.isHidden.toggle()
                }
            } label: {
                Image(systemName: overlay.isHidden ? "eye.slash" : "eye")
                    .foregroundColor(.secondary)
            }
            .buttonStyle(.plain)

            Button {
                viewModel.deleteOverlay(id: overlay.id)
            } label: {
                Image(systemName: "trash")
                    .foregroundColor(.secondary)
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 6)
        .background(
            RoundedRectangle(cornerRadius: 8)
                .fill(isSelected ? Color.accentColor.opacity(0.12) : Color.secondary.opacity(0.06))
        )
        .contentShape(RoundedRectangle(cornerRadius: 8))
        .onTapGesture {
            viewModel.selectOverlay(overlay.id)
        }
    }

    private var isSelected: Bool {
        viewModel.selectedOverlayID == overlay.id
    }

    private var symbolName: String {
        switch overlay.kind {
        case .text: return "textformat"
        case .qrCode: return "qrcode"
        case .timestamp: return "calendar"
        case .image: return "photo"
        case .location: return "mappin.and.ellipse"
        }
    }
}

struct SelectedOverlayInspectorView: View {
    @EnvironmentObject var viewModel: ViewModel

    var body: some View {
        guard let overlay = viewModel.selectedOverlay else {
            return AnyView(EmptyView())
        }

        return AnyView(
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    Text(viewModel.overlayTitle(for: overlay))
                        .font(.callout)
                        .fontWeight(.semibold)
                    Spacer()
                    Button(L("Send Backward")) { viewModel.moveSelectedOverlayBackward() }
                        .controlSize(.small)
                    Button(L("Bring Forward")) { viewModel.moveSelectedOverlayForward() }
                        .controlSize(.small)
                }

                HStack {
                    Toggle(L("Lock"), isOn: lockBinding)
                    Toggle(L("Hidden"), isOn: hiddenBinding)
                }
                .font(.caption)

                HStack {
                    Button(L("Duplicate")) { viewModel.duplicateSelectedOverlay() }
                    Button(L("Delete")) { viewModel.deleteSelectedOverlay() }
                }
                .controlSize(.small)

                Group {
                    labeledSlider(L("Opacity"), value: opacityBinding, range: 0.1...1.0)
                    labeledSlider("X", value: positionXBinding, range: 0.05...0.95)
                    labeledSlider("Y", value: positionYBinding, range: 0.05...0.95)
                    labeledSlider(L("Width"), value: widthBinding, range: 0.08...0.95)
                    labeledSlider(L("Height"), value: heightBinding, range: 0.06...0.95)
                }

                switch overlay.content {
                case .text:
                    textControls
                case .qrCode:
                    qrControls
                case .timestamp:
                    timestampControls
                case .image:
                    imageControls
                case .location:
                    locationControls
                }
            }
        )
    }

    private func labeledSlider(_ title: String, value: Binding<Double>, range: ClosedRange<Double>) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.caption)
                .foregroundColor(.secondary)
            Slider(value: value, in: range)
        }
    }

    private var opacityBinding: Binding<Double> {
        Binding(
            get: { viewModel.selectedOverlay?.opacity ?? 1.0 },
            set: { newValue in viewModel.updateSelectedOverlay { $0.opacity = newValue } }
        )
    }

    private var positionXBinding: Binding<Double> {
        Binding(
            get: { viewModel.selectedOverlay?.placement.normalizedCenterX ?? 0.5 },
            set: { newValue in viewModel.updateSelectedOverlay { $0.placement.normalizedCenterX = newValue } }
        )
    }

    private var positionYBinding: Binding<Double> {
        Binding(
            get: { viewModel.selectedOverlay?.placement.normalizedCenterY ?? 0.5 },
            set: { newValue in viewModel.updateSelectedOverlay { $0.placement.normalizedCenterY = newValue } }
        )
    }

    private var widthBinding: Binding<Double> {
        Binding(
            get: { viewModel.selectedOverlay?.placement.normalizedWidth ?? 0.25 },
            set: { newValue in viewModel.updateSelectedOverlay { $0.placement.normalizedWidth = newValue } }
        )
    }

    private var heightBinding: Binding<Double> {
        Binding(
            get: { viewModel.selectedOverlay?.placement.normalizedHeight ?? 0.15 },
            set: { newValue in viewModel.updateSelectedOverlay { $0.placement.normalizedHeight = newValue } }
        )
    }

    private var hiddenBinding: Binding<Bool> {
        Binding(
            get: { viewModel.selectedOverlay?.isHidden ?? false },
            set: { newValue in viewModel.updateSelectedOverlay { $0.isHidden = newValue } }
        )
    }

    private var lockBinding: Binding<Bool> {
        Binding(
            get: { viewModel.selectedOverlay?.isLocked ?? false },
            set: { newValue in viewModel.updateSelectedOverlay { $0.isLocked = newValue } }
        )
    }

    private var textControls: some View {
        VStack(alignment: .leading, spacing: 8) {
            TextField(L("Text"), text: Binding(
                get: {
                    guard let overlay = viewModel.selectedOverlay,
                          case .text(let data) = overlay.content else { return "" }
                    return data.text
                },
                set: { newValue in
                    viewModel.updateSelectedTextOverlay { $0.text = newValue }
                }
            ))

            labeledSlider(L("Size"), value: Binding(
                get: {
                    guard let overlay = viewModel.selectedOverlay,
                          case .text(let data) = overlay.content else { return 0.1 }
                    return data.fontScale
                },
                set: { newValue in
                    viewModel.updateSelectedTextOverlay { $0.fontScale = newValue }
                }
            ), range: 0.05...0.24)

            Picker(L("Alignment"), selection: Binding(
                get: {
                    guard let overlay = viewModel.selectedOverlay,
                          case .text(let data) = overlay.content else { return OverlayTextAlignment.center }
                    return data.textAlignment
                },
                set: { newValue in
                    viewModel.updateSelectedTextOverlay { $0.textAlignment = newValue }
                }
            )) {
                Text(L("Leading")).tag(OverlayTextAlignment.leading)
                Text(L("Center")).tag(OverlayTextAlignment.center)
                Text(L("Trailing")).tag(OverlayTextAlignment.trailing)
            }
            .pickerStyle(.segmented)

            Picker(L("Shadow"), selection: Binding(
                get: {
                    guard let overlay = viewModel.selectedOverlay,
                          case .text(let data) = overlay.content else { return OverlayShadowStyle.soft }
                    return data.shadowStyle
                },
                set: { newValue in
                    viewModel.updateSelectedTextOverlay { $0.shadowStyle = newValue }
                }
            )) {
                Text(L("None")).tag(OverlayShadowStyle.none)
                Text(L("Soft")).tag(OverlayShadowStyle.soft)
                Text(L("Strong")).tag(OverlayShadowStyle.strong)
            }
            .pickerStyle(.segmented)
        }
    }

    private var qrControls: some View {
        VStack(alignment: .leading, spacing: 8) {
            TextField(L("Content"), text: Binding(
                get: {
                    guard let overlay = viewModel.selectedOverlay,
                          case .qrCode(let data) = overlay.content else { return "" }
                    return data.payload
                },
                set: { newValue in
                    viewModel.updateSelectedQRCodeOverlay { $0.payload = newValue }
                }
            ))

            Toggle(L("Show Caption"), isOn: Binding(
                get: {
                    guard let overlay = viewModel.selectedOverlay,
                          case .qrCode(let data) = overlay.content else { return false }
                    return data.showsCaption
                },
                set: { newValue in
                    viewModel.updateSelectedQRCodeOverlay { $0.showsCaption = newValue }
                }
            ))

            if let overlay = viewModel.selectedOverlay,
               case .qrCode(let data) = overlay.content,
               data.showsCaption {
                TextField(L("Caption"), text: Binding(
                    get: { data.caption },
                    set: { newValue in
                        viewModel.updateSelectedQRCodeOverlay { $0.caption = newValue }
                    }
                ))
            }

            Toggle(L("Quiet Zone"), isOn: Binding(
                get: {
                    guard let overlay = viewModel.selectedOverlay,
                          case .qrCode(let data) = overlay.content else { return true }
                    return data.includesQuietZone
                },
                set: { newValue in
                    viewModel.updateSelectedQRCodeOverlay { $0.includesQuietZone = newValue }
                }
            ))

            Picker(L("Error Correction"), selection: Binding(
                get: {
                    guard let overlay = viewModel.selectedOverlay,
                          case .qrCode(let data) = overlay.content else { return QRErrorCorrectionLevel.medium }
                    return data.correctionLevel
                },
                set: { newValue in
                    viewModel.updateSelectedQRCodeOverlay { $0.correctionLevel = newValue }
                }
            )) {
                Text("L").tag(QRErrorCorrectionLevel.low)
                Text("M").tag(QRErrorCorrectionLevel.medium)
                Text("Q").tag(QRErrorCorrectionLevel.quartile)
                Text("H").tag(QRErrorCorrectionLevel.high)
            }
            .pickerStyle(.segmented)
        }
    }

    private var timestampControls: some View {
        VStack(alignment: .leading, spacing: 8) {
            RoundedRectangle(cornerRadius: 6)
                .fill(Color(white: 0.15))
                .frame(height: 48)
                .overlay {
                    if let overlay = viewModel.selectedOverlay,
                       case .timestamp(let data) = overlay.content {
                        TimestampPreviewView(data: data, size: CGSize(width: 200, height: 48))
                    }
                }

            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    ForEach(ViewModel.presetOrder, id: \.self) { key in
                        PresetCard(
                            preset: ViewModel.dateStampPresets[key]!,
                            isSelected: {
                                guard let overlay = viewModel.selectedOverlay,
                                      case .timestamp(let data) = overlay.content else { return false }
                                return data.presetKey == key
                            }()
                        )
                        .onTapGesture {
                            viewModel.updateSelectedTimestampOverlay {
                                $0.presetKey = key
                                $0.lightBleedEnabled = ViewModel.dateStampPresets[key]!.defaultLightBleed
                            }
                        }
                    }
                }
            }

            Picker(L("Format"), selection: Binding(
                get: {
                    guard let overlay = viewModel.selectedOverlay,
                          case .timestamp(let data) = overlay.content else { return TimestampFormat.ymd }
                    return data.format
                },
                set: { newValue in
                    viewModel.updateSelectedTimestampOverlay { $0.format = newValue }
                }
            )) {
                Text("YY.MM.DD").tag(TimestampFormat.ymd)
                Text("MM.DD.YY").tag(TimestampFormat.mdy)
                Text("DD.MM.YY").tag(TimestampFormat.dmy)
            }
            .pickerStyle(.segmented)

            HStack {
                Toggle(L("Time"), isOn: Binding(
                    get: {
                        guard let overlay = viewModel.selectedOverlay,
                              case .timestamp(let data) = overlay.content else { return true }
                        return data.showsTime
                    },
                    set: { newValue in
                        viewModel.updateSelectedTimestampOverlay { $0.showsTime = newValue }
                    }
                ))
                Toggle(L("Glow"), isOn: Binding(
                    get: {
                        guard let overlay = viewModel.selectedOverlay,
                              case .timestamp(let data) = overlay.content else { return false }
                        return data.lightBleedEnabled
                    },
                    set: { newValue in
                        viewModel.updateSelectedTimestampOverlay { $0.lightBleedEnabled = newValue }
                    }
                ))
            }
            .font(.caption)
        }
    }

    private var imageControls: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button(L("Replace Image")) {
                viewModel.replaceSelectedImageOverlayAsset()
            }
            .controlSize(.small)

            Picker(L("Fit Mode"), selection: Binding(
                get: {
                    guard let overlay = viewModel.selectedOverlay,
                          case .image(let data) = overlay.content else { return OverlayImageContentMode.fit }
                    return data.contentMode
                },
                set: { newValue in
                    viewModel.updateSelectedImageOverlay { $0.contentMode = newValue }
                }
            )) {
                Text(L("Contain")).tag(OverlayImageContentMode.fit)
                Text(L("Crop")).tag(OverlayImageContentMode.fill)
            }
            .pickerStyle(.segmented)

            Toggle(L("Background"), isOn: Binding(
                get: {
                    guard let overlay = viewModel.selectedOverlay,
                          case .image(let data) = overlay.content else { return false }
                    return data.showsBacking
                },
                set: { newValue in
                    viewModel.updateSelectedImageOverlay { $0.showsBacking = newValue }
                }
            ))

            labeledSlider(L("Corner Radius"), value: Binding(
                get: {
                    guard let overlay = viewModel.selectedOverlay,
                          case .image(let data) = overlay.content else { return 0 }
                    return data.cornerRadius
                },
                set: { newValue in
                    viewModel.updateSelectedImageOverlay { $0.cornerRadius = newValue }
                }
            ), range: 0...32)
        }
    }

    private var locationControls: some View {
        VStack(alignment: .leading, spacing: 8) {
            Picker(L("Source"), selection: Binding(
                get: {
                    guard let overlay = viewModel.selectedOverlay,
                          case .location(let data) = overlay.content else { return LocationOverlaySource.photoMetadata }
                    return data.source
                },
                set: { newValue in
                    viewModel.updateSelectedLocationOverlay { $0.source = newValue }
                }
            )) {
                Text(L("Photo Metadata")).tag(LocationOverlaySource.photoMetadata)
                Text(L("Manual Coordinates")).tag(LocationOverlaySource.manualCoordinates)
                Text(L("Manual Text")).tag(LocationOverlaySource.manualText)
            }
            .pickerStyle(.menu)

            Picker(L("Display"), selection: Binding(
                get: {
                    guard let overlay = viewModel.selectedOverlay,
                          case .location(let data) = overlay.content else { return LocationOverlayDisplayStyle.coordinates }
                    return data.displayStyle
                },
                set: { newValue in
                    viewModel.updateSelectedLocationOverlay { $0.displayStyle = newValue }
                }
            )) {
                Text(L("Coordinates")).tag(LocationOverlayDisplayStyle.coordinates)
                Text(L("Name")).tag(LocationOverlayDisplayStyle.name)
                Text(L("Name + Coordinates")).tag(LocationOverlayDisplayStyle.nameAndCoordinates)
            }
            .pickerStyle(.menu)

            TextField(L("Name"), text: Binding(
                get: {
                    guard let overlay = viewModel.selectedOverlay,
                          case .location(let data) = overlay.content else { return "" }
                    return data.locationName
                },
                set: { newValue in
                    viewModel.updateSelectedLocationOverlay { $0.locationName = newValue }
                }
            ))

            HStack {
                TextField(L("Latitude"), text: Binding(
                    get: {
                        guard let overlay = viewModel.selectedOverlay,
                              case .location(let data) = overlay.content else { return "" }
                        guard let value = data.coordinate?.latitude else { return "" }
                        return String(value)
                    },
                    set: { newValue in
                        viewModel.updateSelectedLocationOverlay { data in
                            let latitude = Double(newValue) ?? data.coordinate?.latitude ?? 0
                            let longitude = data.coordinate?.longitude ?? 0
                            data.coordinate = GeoCoordinate(latitude: latitude, longitude: longitude)
                        }
                    }
                ))
                TextField(L("Longitude"), text: Binding(
                    get: {
                        guard let overlay = viewModel.selectedOverlay,
                              case .location(let data) = overlay.content else { return "" }
                        guard let value = data.coordinate?.longitude else { return "" }
                        return String(value)
                    },
                    set: { newValue in
                        viewModel.updateSelectedLocationOverlay { data in
                            let latitude = data.coordinate?.latitude ?? 0
                            let longitude = Double(newValue) ?? data.coordinate?.longitude ?? 0
                            data.coordinate = GeoCoordinate(latitude: latitude, longitude: longitude)
                        }
                    }
                ))
            }

            labeledSlider(L("Precision"), value: Binding(
                get: {
                    guard let overlay = viewModel.selectedOverlay,
                          case .location(let data) = overlay.content else { return 4 }
                    return Double(data.precision)
                },
                set: { newValue in
                    viewModel.updateSelectedLocationOverlay { $0.precision = Int(newValue.rounded()) }
                }
            ), range: 0...6)
        }
    }
}

struct DefaultTimestampOverlayEditor: View {
    @EnvironmentObject var viewModel: ViewModel

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(L("Timestamp"))
                .font(.caption)
                .foregroundColor(.secondary)

            Toggle(L("Enabled"), isOn: Binding(
                get: { viewModel.defaultTimestampOverlay != nil },
                set: { viewModel.setDefaultTimestampOverlayEnabled($0) }
            ))
            .font(.callout)

            if let overlay = viewModel.defaultTimestampOverlay,
               case .timestamp(let data) = overlay.content {
                RoundedRectangle(cornerRadius: 6)
                    .fill(Color(white: 0.15))
                    .frame(height: 48)
                    .overlay {
                        TimestampPreviewView(data: data, size: CGSize(width: 200, height: 48))
                            .environmentObject(viewModel)
                    }

                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 6) {
                        ForEach(ViewModel.presetOrder, id: \.self) { key in
                            PresetCard(
                                preset: ViewModel.dateStampPresets[key]!,
                                isSelected: data.presetKey == key
                            )
                            .onTapGesture {
                                viewModel.updateDefaultTimestampOverlay {
                                    $0.presetKey = key
                                    $0.lightBleedEnabled = ViewModel.dateStampPresets[key]!.defaultLightBleed
                                }
                            }
                        }
                    }
                }

                Picker(L("Format"), selection: Binding(
                    get: {
                        guard let overlay = viewModel.defaultTimestampOverlay,
                              case .timestamp(let data) = overlay.content else { return TimestampFormat.ymd }
                        return data.format
                    },
                    set: { newValue in
                        viewModel.updateDefaultTimestampOverlay { $0.format = newValue }
                    }
                )) {
                    Text("YY.MM.DD").tag(TimestampFormat.ymd)
                    Text("MM.DD.YY").tag(TimestampFormat.mdy)
                    Text("DD.MM.YY").tag(TimestampFormat.dmy)
                }
                .pickerStyle(.segmented)

                HStack {
                    Toggle(L("Time"), isOn: Binding(
                        get: {
                            guard let overlay = viewModel.defaultTimestampOverlay,
                                  case .timestamp(let data) = overlay.content else { return true }
                            return data.showsTime
                        },
                        set: { newValue in
                            viewModel.updateDefaultTimestampOverlay { $0.showsTime = newValue }
                        }
                    ))
                    Toggle(L("Glow"), isOn: Binding(
                        get: {
                            guard let overlay = viewModel.defaultTimestampOverlay,
                                  case .timestamp(let data) = overlay.content else { return false }
                            return data.lightBleedEnabled
                        },
                        set: { newValue in
                            viewModel.updateDefaultTimestampOverlay { $0.lightBleedEnabled = newValue }
                        }
                    ))
                }
                .font(.caption)
            }
        }
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
                    AppearanceSection()
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
                Text("\u{00A9} 2026 Hongjun Wu")
                    .font(.caption2)
                    .foregroundColor(.secondary)
                    .lineLimit(1)
                    .minimumScaleFactor(0.8)
                Spacer(minLength: 8)
                HStack(spacing: 8) {
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

                    Button {
                        if let url = URL(string: "https://me.hongjunwu.com/contact/") {
                            NSWorkspace.shared.open(url)
                        }
                    } label: {
                        HStack(spacing: 4) {
                            Image(systemName: "exclamationmark.bubble")
                            Text(L("Report an Issue"))
                        }
                        .font(.caption2)
                    }
                    .buttonStyle(.link)
                }
            }
        }
        .frame(maxWidth: .infinity)
    }
}

// MARK: - Appearance Section

struct AppearanceSection: View {
    @EnvironmentObject var viewModel: ViewModel

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(L("Appearance"))
                .font(.headline)

            Picker("", selection: $viewModel.appearancePreference) {
                Text(L("System Default")).tag(AppAppearance.system)
                Text(L("Light")).tag(AppAppearance.light)
                Text(L("Dark")).tag(AppAppearance.dark)
            }
            .labelsHidden()
            .pickerStyle(.menu)
        }
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
