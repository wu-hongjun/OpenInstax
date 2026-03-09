import AVFoundation
import CoreImage
import CoreText
import SwiftUI
import UniformTypeIdentifiers
import ImageIO

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

enum StatusMessageTone {
    case info
    case success
    case warning
    case error
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

    private var savedFileModeEditState: QueueItemEditState?

    // Camera mode
    @Published var captureMode: CaptureMode = .file {
        didSet {
            guard captureMode != oldValue else { return }
            if captureMode == .camera {
                savedFileModeEditState = queue.indices.contains(selectedQueueIndex)
                    ? makeCurrentQueueItemEditState()
                    : nil
                applyQueueItemEditState(makeCameraDraftEditState())
            } else if oldValue == .camera {
                restoreFileModeEditStateAfterCamera()
            }
        }
    }
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
    @Published var statusMessageTone: StatusMessageTone = .info
    @Published var isStatusMessagePersistent = false
    @Published var showCameraDiscardConfirmation = false
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
    private var statusMessageDismissWorkItem: DispatchWorkItem?
    private var pendingCaptureMode: CaptureMode?

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
        dismissStatusMessage()

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
        let previousSelectedQueueIndex = selectedQueueIndex
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
        if initialCount == 0, !queue.isEmpty {
            selectedQueueIndex = queue.count - 1
            applyQueueItemEditState(queue[selectedQueueIndex].editState)
        } else if hadSelectedQueueItem,
                  queue.indices.contains(previousSelectedQueueIndex) {
            selectedQueueIndex = previousSelectedQueueIndex
            applyQueueItemEditState(queue[selectedQueueIndex].editState)
        } else if !queue.isEmpty {
            selectedQueueIndex = min(selectedQueueIndex, queue.count - 1)
            applyQueueItemEditState(queue[selectedQueueIndex].editState)
        }
        let addedCount = queue.count - initialCount
        if addedCount == 0 && initialCount >= Self.maxQueueItems {
            showStatus("Queue limit reached (\(Self.maxQueueItems) images max)", tone: .warning, autoDismiss: false)
        } else if addedCount < urls.count {
            showStatus("Added \(addedCount) of \(urls.count) images (\(Self.maxQueueItems) max in queue)", tone: .warning, autoDismiss: false)
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
        removeSelectedQueueItem()
    }

    func removeSelectedQueueItem() {
        guard queue.indices.contains(selectedQueueIndex) else { return }
        removeQueueItem(at: selectedQueueIndex)
    }

    var printableQueueCountFromSelection: Int {
        guard !queue.isEmpty else { return 0 }
        let startIndex = queue.indices.contains(selectedQueueIndex) ? selectedQueueIndex : 0
        return min(queue.count - startIndex, filmRemaining)
    }

    var printNextActionLabel: String {
        if filmRemaining <= 0 {
            return L("No Film")
        }
        return L("print_next_n", printableQueueCountFromSelection)
    }

    var hasUncommittedCameraCapture: Bool {
        captureMode == .camera && cameraState == .preview && capturedImage != nil
    }

    func requestCaptureModeChange(to newMode: CaptureMode) {
        guard newMode != captureMode else { return }
        if captureMode == .camera, newMode == .file, hasUncommittedCameraCapture {
            pendingCaptureMode = newMode
            showCameraDiscardConfirmation = true
            return
        }
        pendingCaptureMode = nil
        captureMode = newMode
    }

    func confirmPendingCaptureModeChange() {
        let nextMode = pendingCaptureMode ?? .file
        showCameraDiscardConfirmation = false
        pendingCaptureMode = nil
        captureMode = nextMode
    }

    func cancelPendingCaptureModeChange() {
        showCameraDiscardConfirmation = false
        pendingCaptureMode = nil
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
            overlays: defaultEligibleOverlays(from: overlays),
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

    private func makeCameraDraftEditState() -> QueueItemEditState {
        makeQueueItemEditStateFromDefaults()
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
              captureMode == .file,
              queue.indices.contains(selectedQueueIndex) else { return }
        queue[selectedQueueIndex].editState = makeCurrentQueueItemEditState()
    }

    private func restoreFileModeEditStateAfterCamera() {
        if queue.indices.contains(selectedQueueIndex) {
            applyQueueItemEditState(queue[selectedQueueIndex].editState)
        } else if let savedFileModeEditState {
            applyQueueItemEditState(savedFileModeEditState)
        } else {
            applyDefaultQueueItemEditState()
        }
        self.savedFileModeEditState = nil
    }

    private func defaultEligibleOverlays(from overlays: [OverlayItem]) -> [OverlayItem] {
        overlays
            .filter { overlay in
                if case .timestamp = overlay.content {
                    return true
                }
                return false
            }
            .enumerated()
            .map { index, overlay in
                var overlay = overlay
                overlay.zIndex = index
                return overlay
            }
    }

    func selectOverlay(_ id: UUID?) {
        selectedOverlayID = id
    }

    func addOverlay(kind: OverlayKind) {
        let content: OverlayContent
        switch kind {
        case .image:
            guard let asset = selectOverlayImageAsset() else { return }
            content = .image(ImageOverlayData(asset: asset))
        default:
            content = defaultOverlayContent(for: kind)
        }

        let overlay = OverlayItem(
            content: content,
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
        guard let asset = selectOverlayImageAsset() else { return }
        updateSelectedImageOverlay { data in
            data.asset = asset
        }
    }

    private func selectOverlayImageAsset() -> OverlayImageAsset? {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.image]
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        panel.message = L("Select an image to print")
        guard panel.runModal() == .OK,
              let url = panel.url,
              let image = NSImage(contentsOf: url),
              let tiff = image.tiffRepresentation else { return nil }
        return OverlayImageAsset(fileName: url.lastPathComponent, imageData: tiff)
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
            return OverlayPlacement(normalizedCenterX: 0.78, normalizedCenterY: 0.78, normalizedWidth: 0.22, normalizedHeight: 0.22)
        case .image:
            return OverlayPlacement(normalizedCenterX: 0.78, normalizedCenterY: 0.24, normalizedWidth: 0.24, normalizedHeight: 0.24)
        case .timestamp:
            return OverlayPlacement(normalizedCenterX: 0.78, normalizedCenterY: 0.9, normalizedWidth: 0.34, normalizedHeight: 0.1)
        case .location:
            return OverlayPlacement(normalizedCenterX: 0.24, normalizedCenterY: 0.9, normalizedWidth: 0.34, normalizedHeight: 0.12)
        case .text:
            return OverlayPlacement(normalizedCenterX: 0.5, normalizedCenterY: 0.16, normalizedWidth: 0.42, normalizedHeight: 0.14)
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
                        self.showError(L("Camera access denied"))
                    }
                }
            }
        default:
            captureMode = .file
            showError(L("Camera access denied"))
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
            showStatus("Queue limit reached (\(Self.maxQueueItems) images max)", tone: .warning, autoDismiss: false)
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
            showError(L("failed_to_save_captured_image", error.localizedDescription))
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
        imageDate ?? Date()
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
        return body
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
        let font = NSFont.systemFont(ofSize: fontSize, weight: .semibold)
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

    private func renderImageForCurrentPrintCanvas(_ cgImage: CGImage) -> CGImage? {
        guard let targetAspectRatio = orientedAspectRatio else { return nil }

        let sourceSize = CGSize(width: cgImage.width, height: cgImage.height)
        let sourceAspectRatio = sourceSize.width / max(sourceSize.height, 1)
        let canvasSize: CGSize
        if fitMode == "crop" || abs(sourceAspectRatio - targetAspectRatio) < 0.0001 {
            canvasSize = sourceSize
        } else if sourceAspectRatio > targetAspectRatio {
            canvasSize = CGSize(width: sourceSize.width, height: sourceSize.width / targetAspectRatio)
        } else {
            canvasSize = CGSize(width: sourceSize.height * targetAspectRatio, height: sourceSize.height)
        }

        let rep = NSBitmapImageRep(
            bitmapDataPlanes: nil,
            pixelsWide: max(Int(canvasSize.width.rounded()), 1),
            pixelsHigh: max(Int(canvasSize.height.rounded()), 1),
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

        let canvasRect = CGRect(origin: .zero, size: canvasSize)
        let drawRect: CGRect
        switch fitMode {
        case "contain":
            drawRect = AVMakeRect(aspectRatio: sourceSize, insideRect: canvasRect)
        case "stretch", "crop":
            drawRect = canvasRect
        default:
            drawRect = canvasRect
        }

        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = graphicsContext
        NSColor.white.setFill()
        canvasRect.fill()
        NSImage(cgImage: cgImage, size: NSSize(width: sourceSize.width, height: sourceSize.height)).draw(in: drawRect)
        NSGraphicsContext.restoreGraphicsState()

        return rep.cgImage
    }

    // MARK: - Print Preparation

    @MainActor
    func prepareImageForPrint() -> (path: String, fit: String, tempFile: String?)? {
        guard let path = selectedImagePath,
              let image = selectedImage,
              let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil) else { return nil }

        var currentCG = cgImage
        var processed = false
        var renderedToFinalCanvas = false
        let hasVisibleOverlays = overlays.contains { !$0.isHidden }

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

        if (processed || hasVisibleOverlays), let canvasImage = renderImageForCurrentPrintCanvas(currentCG) {
            currentCG = canvasImage
            processed = true
            renderedToFinalCanvas = true
        }

        if hasVisibleOverlays, let composited = composeOverlays(on: currentCG) {
            currentCG = composited
            processed = true
        }

        // If film orientation is rotated, rotate 90° to fit native pixel layout
        if filmOrientation == "rotated", let ar = printerAspectRatio, ar != 1.0 {
            if let rotated = rotateCGImage(currentCG, degrees: 90) {
                currentCG = rotated
                processed = true
                renderedToFinalCanvas = true
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
                return (path: tempURL.path, fit: renderedToFinalCanvas ? "stretch" : fitMode, tempFile: tempURL.path)
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
            if success {
                showStatus(L("Printed!"), tone: .success)
            } else {
                showError(L("Print failed"))
            }
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
                    showError(L("print_failed_at", offset + 1, count))
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
                    showError(L("print_failed_at", offset + 1, count))
                }
                return
            }

            await refreshStatus()

            let remaining = await MainActor.run { filmRemaining }
            if remaining <= 0 && offset < count - 1 {
                await MainActor.run {
                    isPrinting = false
                    batchPrintTotal = 0
                    showError(L("film_ran_out", offset + 1, count))
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

    func dismissStatusMessage() {
        statusMessageDismissWorkItem?.cancel()
        statusMessageDismissWorkItem = nil
        statusMessage = nil
        statusMessageTone = .info
        isStatusMessagePersistent = false
    }

    func showStatus(
        _ message: String,
        tone: StatusMessageTone = .info,
        autoDismiss: Bool = true,
        duration: TimeInterval = 4
    ) {
        statusMessageDismissWorkItem?.cancel()
        statusMessage = message
        statusMessageTone = tone
        isStatusMessagePersistent = !autoDismiss

        guard autoDismiss else { return }

        let workItem = DispatchWorkItem { [weak self] in
            guard self?.statusMessage == message else { return }
            self?.dismissStatusMessage()
        }
        statusMessageDismissWorkItem = workItem
        DispatchQueue.main.asyncAfter(deadline: .now() + duration, execute: workItem)
    }

    func showError(_ message: String) {
        showStatus(message, tone: .error, autoDismiss: false)
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
