@preconcurrency import AVFoundation
import CoreImage
import ImageIO
import SwiftUI
import UniformTypeIdentifiers

struct OverlayTextFocusRequest: Equatable {
    let overlayID: UUID
    let token = UUID()
}

// MARK: - View Model

@MainActor
class ViewModel: ObservableObject {
    static let maxQueueItems = QueueEditCoordinator.maxQueueItems
    static let minCropZoom: CGFloat = 1.0
    static let maxCropZoom: CGFloat = 5.0
    static let quickCropZoomStep: CGFloat = 0.25
    static let minExposureEV = -6.0
    static let maxExposureEV = 6.0
    static let quickExposureStep = 0.5

    let ffi: InstantLinkFFI
    private var isApplyingQueueItemEditState = false
    private var isApplyingConnectionSnapshot = false
    private lazy var queueCoordinator = QueueEditCoordinator(
        queue: queue,
        selectedQueueIndex: selectedQueueIndex,
        newPhotoDefaults: newPhotoDefaults
    )
    private lazy var connectionCoordinator = PrinterConnectionCoordinator(
        ffi: ffi,
        initialSnapshot: PrinterConnectionSnapshot(
            isConnected: isConnected,
            printerName: printerName,
            printerModel: printerModel,
            battery: battery,
            isCharging: isCharging,
            filmRemaining: filmRemaining,
            printCount: printCount,
            availablePrinters: availablePrinters,
            selectedPrinter: selectedPrinter,
            nearbyPrinters: nearbyPrinters,
            isSearching: isSearching,
            isRefreshing: isRefreshing,
            isScanning: isScanning,
            isPairing: isPairing,
            pairingPhase: pairingPhase,
            pairingAttempt: pairingAttempt,
            pairingStatus: pairingStatus,
            hasSearchedOnce: hasSearchedOnce
        ),
        initialProfiles: printerProfiles,
        callbacks: PrinterConnectionCoordinatorCallbacks(
            onSnapshotChange: { [weak self] snapshot in
                self?.applyConnectionSnapshot(snapshot)
            },
            onProfilesChanged: { [weak self] profiles in
                self?.applyPrinterProfiles(profiles)
            },
            onProfileBootstrapRequested: { [weak self] profile in
                self?.editingProfile = profile
                self?.showProfileSheet = true
            },
            onStatusEvent: { [weak self] event in
                self?.handleConnectionStatusEvent(event)
            }
        )
    )

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
            let sanitized = newPhotoDefaults.sanitized
            if sanitized != newPhotoDefaults {
                newPhotoDefaults = sanitized
                return
            }
            newPhotoDefaults.save()
            queueCoordinator.newPhotoDefaults = newPhotoDefaults
            if queue.isEmpty {
                applyQueueEditingSnapshot(queueCoordinator.selectedOrDefaultEditingSnapshot)
            }
        }
    }
    @Published var appearancePreference: AppAppearance = AppAppearance.load() {
        didSet {
            appearancePreference.save()
            AppAppearanceService.apply(appearancePreference)
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
    @Published var cropOffsetNormalized: CGSize = .zero {
        didSet { persistSelectedQueueItemEditState() }
    }
    @Published var cropZoom: CGFloat = 1.0 {
        didSet { persistSelectedQueueItemEditState() }
    }
    @Published var exposureEV: Double = 0 {
        didSet {
            let clamped = Self.clampedExposureEV(exposureEV)
            if clamped != exposureEV {
                exposureEV = clamped
                return
            }
            persistSelectedQueueItemEditState()
        }
    }

    // Rotation
    @Published var rotationAngle: Int = initialNewPhotoDefaults.rotationAngle {  // 0, 90, 180, 270
        didSet { persistSelectedQueueItemEditState() }
    }
    @Published var isHorizontallyFlipped: Bool = initialNewPhotoDefaults.isHorizontallyFlipped {
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
    @Published var textOverlayFocusRequest: OverlayTextFocusRequest?

    var selectedOverlayIndex: Int? {
        guard let selectedOverlayID else { return nil }
        return overlays.firstIndex(where: { $0.id == selectedOverlayID })
    }

    var selectedOverlay: OverlayItem? {
        guard let selectedOverlayIndex else { return nil }
        return overlays[selectedOverlayIndex]
    }

    // Camera mode
    @Published var captureMode: CaptureMode = .file {
        didSet {
            guard captureMode != oldValue else { return }
            if captureMode == .camera {
                applyQueueEditingSnapshot(
                    queueCoordinator.beginCameraDraft(from: currentQueueEditingSnapshot())
                )
            } else if oldValue == .camera {
                applyQueueEditingSnapshot(queueCoordinator.restoreFileModeAfterCamera())
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
    @Published var selectedPrinter: String? {
        didSet {
            guard !isApplyingConnectionSnapshot else { return }
            connectionCoordinator.setSelectedPrinter(selectedPrinter)
        }
    }

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
        return PrinterModelCatalog.aspectRatio(for: model)
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
        return PrinterModelCatalog.filmFormatTag(for: model)
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
    @Published var pairingPhase: PrinterPairingPhase = .idle
    @Published var pairingAttempt = 0
    @Published var pairingStatus: String = L("Scanning...")
    private var statusMessageDismissWorkItem: DispatchWorkItem?
    private var pendingCaptureMode: CaptureMode?

    private var autoRefreshTimer: Timer?

    init() {
        guard let f = InstantLinkFFI() else {
            fatalError("Failed to load InstantLink native library. The app bundle may be corrupted.")
        }
        ffi = f
        _ = queueCoordinator
        _ = connectionCoordinator
        AppAppearanceService.apply(appearancePreference)
        loadCoreVersion()
        autoRefreshTimer = Timer.scheduledTimer(withTimeInterval: 10, repeats: true) { [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self = self, self.isConnected else { return }
                await self.refreshStatus()
            }
        }
    }

    deinit {
        autoRefreshTimer?.invalidate()
        ffi.disconnectSync()
    }

    // MARK: - Pairing Mode (continuous scan loop)

    func startPairing() {
        connectionCoordinator.startPairingLoop()
    }

    func stopPairing() {
        connectionCoordinator.stopPairingLoop()
    }

    // MARK: - Printer Profiles

    var currentPrinterDisplayName: String? {
        guard let bleId = printerName else { return nil }
        return printerProfiles[bleId]?.displayName ?? bleId
    }

    func saveProfile(_ profile: PrinterProfile) {
        printerProfiles[profile.bleIdentifier] = profile
        PrinterProfile.save(printerProfiles)
        connectionCoordinator.replaceProfiles(printerProfiles)
        editingProfile = nil
    }

    func deleteProfile(_ bleIdentifier: String) {
        connectionCoordinator.deleteProfile(bleIdentifier)
    }

    private func applyPrinterProfiles(_ profiles: [String: PrinterProfile]) {
        printerProfiles = profiles
        PrinterProfile.save(profiles)
        connectionCoordinator.replaceProfiles(profiles)
    }

    private func applyConnectionSnapshot(_ snapshot: PrinterConnectionSnapshot) {
        isApplyingConnectionSnapshot = true
        isConnected = snapshot.isConnected
        printerName = snapshot.printerName
        printerModel = snapshot.printerModel
        battery = snapshot.battery
        isCharging = snapshot.isCharging
        filmRemaining = snapshot.filmRemaining
        printCount = snapshot.printCount
        availablePrinters = snapshot.availablePrinters
        selectedPrinter = snapshot.selectedPrinter
        nearbyPrinters = snapshot.nearbyPrinters
        isSearching = snapshot.isSearching
        isRefreshing = snapshot.isRefreshing
        isScanning = snapshot.isScanning
        isPairing = snapshot.isPairing
        pairingPhase = snapshot.pairingPhase
        pairingAttempt = snapshot.pairingAttempt
        pairingStatus = snapshot.pairingStatus ?? L("Scanning...")
        hasSearchedOnce = snapshot.hasSearchedOnce
        isApplyingConnectionSnapshot = false
    }

    private func handleConnectionStatusEvent(_ event: PrinterConnectionStatusEvent) {
        switch event {
        case .dismiss:
            dismissStatusMessage()
        case .show(let message):
            showStatus(
                message.text,
                tone: message.tone,
                autoDismiss: message.autoDismiss,
                duration: message.duration
            )
        }
    }

    // MARK: - Refresh (quiet — no "searching" spinner, just update numbers)

    func refreshStatus() async {
        await connectionCoordinator.refresh()
    }

    // MARK: - Scan (discover all printers for the picker, then connect)

    func scanAllPrinters() async {
        await connectionCoordinator.scanAll()
    }

    // MARK: - Switch printer (from footer picker)

    func switchPrinter(to name: String) {
        Task {
            await connectionCoordinator.switchPrinter(to: name)
        }
    }

    // MARK: - Scan Nearby (one-shot for picker)

    func scanNearby() {
        Task {
            await connectionCoordinator.scanNearby()
        }
    }

    // MARK: - Queue Management

    func addImages(from urls: [URL]) {
        guard !urls.isEmpty else { return }
        let items = urls.compactMap { url -> QueueImportItem? in
            guard let image = NSImage(contentsOf: url) else { return nil }
            let metadata = Self.extractImageMetadata(from: url)
            return QueueImportItem(
                url: url,
                image: image,
                imageDate: metadata.date,
                imageLocation: metadata.location
            )
        }

        let result = queueCoordinator.addItems(
            items,
            currentEditing: currentQueueEditingSnapshot()
        )
        applyQueueSelectionUpdate(result.selection)

        if result.addedCount == 0 && queue.count >= Self.maxQueueItems {
            showStatus("Queue limit reached (\(Self.maxQueueItems) images max)", tone: .warning, autoDismiss: false)
        } else if result.addedCount < urls.count {
            showStatus(
                "Added \(result.addedCount) of \(urls.count) images (\(Self.maxQueueItems) max in queue)",
                tone: .warning,
                autoDismiss: false
            )
        }
    }

    func removeQueueItem(at index: Int) {
        if let selection = queueCoordinator.removeQueueItem(
            at: index,
            currentEditing: currentQueueEditingSnapshot()
        ) {
            applyQueueSelectionUpdate(selection)
        }
    }

    func selectQueueItem(at index: Int) {
        if let selection = queueCoordinator.selectQueueItem(
            at: index,
            currentEditing: currentQueueEditingSnapshot()
        ) {
            applyQueueSelectionUpdate(selection)
        }
    }

    func moveQueueItem(from source: Int, to destination: Int) {
        if let selection = queueCoordinator.moveQueueItem(
            from: source,
            to: destination,
            currentEditing: currentQueueEditingSnapshot()
        ) {
            applyQueueSelectionUpdate(selection)
        }
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
        if let selection = queueCoordinator.removeSelectedQueueItem(
            currentEditing: currentQueueEditingSnapshot()
        ) {
            applyQueueSelectionUpdate(selection)
        }
    }

    var printableQueueCountFromSelection: Int {
        min(queueCoordinator.printableQueueCountFromSelection, filmRemaining)
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
        cropOffsetNormalized = .zero
        cropZoom = Self.minCropZoom
    }

    static func clampedExposureEV(_ value: Double) -> Double {
        min(max(value, minExposureEV), maxExposureEV)
    }

    var canIncreaseExposure: Bool {
        selectedImage != nil && exposureEV < Self.maxExposureEV - 0.001
    }

    var canDecreaseExposure: Bool {
        selectedImage != nil && exposureEV > Self.minExposureEV + 0.001
    }

    var canResetExposure: Bool {
        selectedImage != nil && abs(exposureEV) > 0.001
    }

    var exposureDisplayValue: String {
        let normalized = abs(exposureEV) < 0.001 ? 0 : exposureEV
        let sign = normalized > 0 ? "+" : ""
        let valueText: String
        if abs(normalized.rounded() - normalized) < 0.001 {
            valueText = "\(Int(normalized.rounded()))"
        } else {
            valueText = String(format: "%.1f", normalized)
        }
        return "\(sign)\(valueText) EV"
    }

    func setExposureEV(_ value: Double) {
        exposureEV = Self.clampedExposureEV(value)
    }

    func increaseExposure() {
        guard selectedImage != nil else { return }
        setExposureEV(exposureEV + Self.quickExposureStep)
    }

    func decreaseExposure() {
        guard selectedImage != nil else { return }
        setExposureEV(exposureEV - Self.quickExposureStep)
    }

    func resetExposure() {
        exposureEV = 0
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
        (cropOffsetNormalized != .zero || abs(cropZoom - Self.minCropZoom) > 0.001)
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
        let newZoom = min(max(zoom, Self.minCropZoom), Self.maxCropZoom)
        cropZoom = newZoom
        if newZoom <= Self.minCropZoom + 0.001 {
            cropOffsetNormalized = .zero
        } else {
            cropOffsetNormalized = clampedNormalizedCropOffset(cropOffsetNormalized)
        }
    }

    func cropOffsetInPoints(imageSize: CGSize, frameSize: CGSize, zoom: CGFloat) -> CGSize {
        let maxOff = maxCropOffsetPoints(imageSize: imageSize, frameSize: frameSize, zoom: zoom)
        return CGSize(
            width: maxOff.width * cropOffsetNormalized.width,
            height: maxOff.height * cropOffsetNormalized.height
        )
    }

    func clampedCropOffsetPoints(raw: CGSize, imageSize: CGSize, frameSize: CGSize, zoom: CGFloat) -> CGSize {
        let maxOff = maxCropOffsetPoints(imageSize: imageSize, frameSize: frameSize, zoom: zoom)
        return CGSize(
            width: min(max(raw.width, -maxOff.width), maxOff.width),
            height: min(max(raw.height, -maxOff.height), maxOff.height)
        )
    }

    func normalizedCropOffset(from rawPoints: CGSize, imageSize: CGSize, frameSize: CGSize, zoom: CGFloat) -> CGSize {
        let maxOff = maxCropOffsetPoints(imageSize: imageSize, frameSize: frameSize, zoom: zoom)
        return clampedNormalizedCropOffset(
            CGSize(
                width: maxOff.width > 0 ? rawPoints.width / maxOff.width : 0,
                height: maxOff.height > 0 ? rawPoints.height / maxOff.height : 0
            )
        )
    }

    private func maxCropOffsetPoints(imageSize: CGSize, frameSize: CGSize, zoom: CGFloat) -> CGSize {
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

    private func clampedNormalizedCropOffset(_ offset: CGSize) -> CGSize {
        CGSize(
            width: min(max(offset.width, -1), 1),
            height: min(max(offset.height, -1), 1)
        )
    }

    func saveCurrentLayoutAsNewPhotoDefaults() {
        if let snapshot = queueCoordinator.saveCurrentLayoutAsNewPhotoDefaults(
            from: currentQueueEditingSnapshot()
                ?? QueueEditingSnapshot(editState: makeCurrentQueueItemEditState())
        ) {
            applyQueueEditingSnapshot(snapshot)
        }
        syncQueueCoordinatorState()
    }

    var shouldMirrorDefaultsToSelectedImage: Bool {
        captureMode == .file && selectedImage != nil
    }

    func setDefaultFitMode(_ value: String) {
        newPhotoDefaults.fitMode = value
        guard shouldMirrorDefaultsToSelectedImage else { return }
        fitMode = value
    }

    func setDefaultFilmOrientation(_ value: String) {
        newPhotoDefaults.filmOrientation = value
        guard shouldMirrorDefaultsToSelectedImage else { return }
        filmOrientation = value
    }

    func setDefaultRotationAngle(_ value: Int) {
        newPhotoDefaults.rotationAngle = value
        guard shouldMirrorDefaultsToSelectedImage else { return }
        rotationAngle = value
    }

    func setDefaultHorizontalFlip(_ value: Bool) {
        newPhotoDefaults.isHorizontallyFlipped = value
        guard shouldMirrorDefaultsToSelectedImage else { return }
        isHorizontallyFlipped = value
    }

    func saveSelectedTimestampOverlayAsNewPhotoDefaults() {
        guard let overlay = selectedTimestampOverlay else {
            return
        }
        if let snapshot = queueCoordinator.saveTimestampOverlayAsNewPhotoDefaults(overlay) {
            applyQueueEditingSnapshot(snapshot)
        }
        syncQueueCoordinatorState()
    }

    func resetNewPhotoDefaults() {
        if let snapshot = queueCoordinator.resetNewPhotoDefaults() {
            applyQueueEditingSnapshot(snapshot)
        }
        syncQueueCoordinatorState()
    }

    private func makeCurrentQueueItemEditState() -> QueueItemEditState {
        QueueItemEditState(
            fitMode: fitMode,
            cropOffsetNormalized: cropOffsetNormalized,
            cropZoom: cropZoom,
            exposureEV: exposureEV,
            rotationAngle: rotationAngle,
            isHorizontallyFlipped: isHorizontallyFlipped,
            overlays: overlays,
            filmOrientation: filmOrientation
        )
    }

    private func currentQueueEditingSnapshot() -> QueueEditingSnapshot? {
        guard captureMode == .file else { return nil }
        return QueueEditingSnapshot(editState: makeCurrentQueueItemEditState())
    }

    private func applyQueueEditingSnapshot(_ snapshot: QueueEditingSnapshot) {
        isApplyingQueueItemEditState = true
        fitMode = snapshot.fitMode
        cropOffsetNormalized = snapshot.cropOffsetNormalized
        cropZoom = snapshot.cropZoom
        exposureEV = snapshot.exposureEV
        rotationAngle = snapshot.rotationAngle
        isHorizontallyFlipped = snapshot.isHorizontallyFlipped
        overlays = snapshot.overlays
        selectedOverlayID = overlays.last?.id
        filmOrientation = snapshot.filmOrientation
        isApplyingQueueItemEditState = false
    }

    private func persistSelectedQueueItemEditState() {
        guard !isApplyingQueueItemEditState,
              let snapshot = currentQueueEditingSnapshot() else {
            return
        }
        queueCoordinator.persistSelectedQueueItemEditState(
            snapshot,
            isFileMode: captureMode == .file
        )
        queue = queueCoordinator.queue
    }

    private func applyQueueSelectionUpdate(_ update: QueueSelectionUpdate) {
        syncQueueCoordinatorState()
        if queue.indices.contains(update.selectedQueueIndex) || queue.isEmpty {
            selectedQueueIndex = update.selectedQueueIndex
        }
        applyQueueEditingSnapshot(update.editing)
    }

    private func syncQueueCoordinatorState() {
        queue = queueCoordinator.queue
        selectedQueueIndex = queueCoordinator.selectedQueueIndex
        if newPhotoDefaults != queueCoordinator.newPhotoDefaults {
            newPhotoDefaults = queueCoordinator.newPhotoDefaults
        }
    }

    func selectOverlay(_ id: UUID?) {
        selectedOverlayID = id
    }

    func requestTextOverlayEditing(_ id: UUID) {
        selectOverlay(id)
        guard let overlay = overlays.first(where: { $0.id == id }),
              case .text = overlay.content else { return }
        textOverlayFocusRequest = OverlayTextFocusRequest(overlayID: id)
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

    var selectedTimestampOverlay: OverlayItem? {
        guard let selectedOverlay,
              case .timestamp = selectedOverlay.content else { return nil }
        return selectedOverlay
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
        syncDefaultTimestampOverlayToSelectedImage()
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
        syncDefaultTimestampOverlayToSelectedImage()
    }

    private func syncDefaultTimestampOverlayToSelectedImage() {
        guard shouldMirrorDefaultsToSelectedImage else { return }

        let selectedTimestampIndex = overlays.firstIndex { overlay in
            if case .timestamp = overlay.content {
                return true
            }
            return false
        }

        guard let defaultTimestampOverlay else {
            if let selectedTimestampIndex {
                let removedID = overlays[selectedTimestampIndex].id
                overlays.remove(at: selectedTimestampIndex)
                if selectedOverlayID == removedID {
                    selectedOverlayID = overlays.last?.id
                }
            }
            return
        }

        if let selectedTimestampIndex {
            let existing = overlays[selectedTimestampIndex]
            var replacement = defaultTimestampOverlay
            replacement.id = existing.id
            replacement.createdAt = existing.createdAt
            replacement.zIndex = existing.zIndex
            overlays[selectedTimestampIndex] = replacement
        } else {
            var mirrored = defaultTimestampOverlay
            mirrored.id = UUID()
            mirrored.createdAt = Date()
            mirrored.zIndex = (overlays.map(\.zIndex).max() ?? -1) + 1
            overlays.append(mirrored)
        }
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
            discoverCameras(ensureSession: true)
        case .notDetermined:
            AVCaptureDevice.requestAccess(for: .video) { [weak self] granted in
                DispatchQueue.main.async {
                    guard let self = self else { return }
                    if granted {
                        self.discoverCameras(ensureSession: true)
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

    func discoverCameras(ensureSession: Bool = false) {
        let previousSelectedCameraID = selectedCamera?.uniqueID
        let discovery = AVCaptureDevice.DiscoverySession(
            deviceTypes: cameraDiscoveryDeviceTypes,
            mediaType: .video,
            position: .unspecified
        )
        let mergedDevices = discovery.devices.sorted(by: cameraSortOrder)

        let nextSelectedCamera = mergedDevices.first(where: { $0.uniqueID == previousSelectedCameraID })
            ?? preferredDefaultCamera(in: mergedDevices)
        let selectionChanged = previousSelectedCameraID != nextSelectedCamera?.uniqueID

        availableCameras = mergedDevices
        selectedCamera = nextSelectedCamera

        guard ensureSession, captureMode == .camera else { return }

        if mergedDevices.isEmpty {
            stopCameraSession()
            return
        }

        // Keep the live session stable while previewing a captured photo.
        guard cameraState == .viewfinder else { return }

        if captureSession == nil {
            startCameraSession()
        } else if selectionChanged, let nextSelectedCamera {
            switchCamera(to: nextSelectedCamera)
        }
    }

    private var cameraDiscoveryDeviceTypes: [AVCaptureDevice.DeviceType] {
        [
            .builtInWideAngleCamera,
            .external,
            .continuityCamera,
            .deskViewCamera
        ]
    }

    private func cameraSortOrder(_ lhs: AVCaptureDevice, _ rhs: AVCaptureDevice) -> Bool {
        let lhsRank = cameraSortRank(for: lhs)
        let rhsRank = cameraSortRank(for: rhs)
        if lhsRank != rhsRank {
            return lhsRank < rhsRank
        }
        return lhs.localizedName.localizedCaseInsensitiveCompare(rhs.localizedName) == .orderedAscending
    }

    private func cameraSortRank(for device: AVCaptureDevice) -> Int {
        defaultCameraRank(for: device)
    }

    private func preferredDefaultCamera(in devices: [AVCaptureDevice]) -> AVCaptureDevice? {
        devices.min { lhs, rhs in
            let lhsRank = defaultCameraRank(for: lhs)
            let rhsRank = defaultCameraRank(for: rhs)
            if lhsRank != rhsRank {
                return lhsRank < rhsRank
            }
            return lhs.localizedName.localizedCaseInsensitiveCompare(rhs.localizedName) == .orderedAscending
        }
    }

    private func defaultCameraRank(for device: AVCaptureDevice) -> Int {
        switch device.deviceType {
        case .builtInWideAngleCamera:
            switch device.position {
            case .front:
                return 0
            case .back:
                return 1
            default:
                return 2
            }
        case .external:
            return 3
        case .continuityCamera:
            return 4
        case .deskViewCamera:
            return 5
        default:
            return 6
        }
    }

    func startCameraSession() {
        guard let camera = selectedCamera ?? availableCameras.first else { return }
        if selectedCamera?.uniqueID != camera.uniqueID {
            selectedCamera = camera
        }
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
        discoverCameras(ensureSession: true)
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

        let finalImage = NSImage(data: jpegData) ?? image
        guard let selection = queueCoordinator.appendCapturedItem(
            QueueImportItem(
                url: tempURL,
                image: finalImage,
                imageDate: Date(),
                imageLocation: nil
            ),
            filmOrientation: filmOrientation,
            isHorizontallyFlipped: isHorizontallyFlipped,
            currentEditing: currentQueueEditingSnapshot()
        ) else {
            showStatus("Queue limit reached (\(Self.maxQueueItems) images max)", tone: .warning, autoDismiss: false)
            return false
        }
        applyQueueSelectionUpdate(selection)
        captureMode = .file
        stopCameraSession()
        capturedImage = nil
        cameraState = .viewfinder
        return true
    }

    func rotateClockwise() { rotationAngle = (rotationAngle + 90) % 360 }
    func rotateCounterClockwise() { rotationAngle = (rotationAngle + 270) % 360 }
    func toggleHorizontalFlip() { isHorizontallyFlipped.toggle() }

    // MARK: - Print Preparation

    func prepareImageForPrint() -> (path: String, fit: String, tempFile: String?)? {
        guard queue.indices.contains(selectedQueueIndex) else { return nil }
        return prepareImageForPrint(queue[selectedQueueIndex])
    }

    private func prepareImageForPrint(_ item: QueueItem) -> (path: String, fit: String, tempFile: String?)? {
        guard let prepared = PrintRenderService.preparePrint(
            PrintRenderService.Request(
                sourcePath: item.url.path,
                sourceImage: item.image,
                fitMode: item.editState.fitMode,
                cropOffsetNormalized: item.editState.cropOffsetNormalized,
                cropZoom: item.editState.cropZoom,
                exposureEV: item.editState.exposureEV,
                rotationAngle: item.editState.rotationAngle,
                isHorizontallyFlipped: item.editState.isHorizontallyFlipped,
                overlays: item.editState.overlays,
                filmOrientation: item.editState.filmOrientation,
                printerAspectRatio: printerAspectRatio,
                imageDate: item.imageDate,
                imageLocation: item.imageLocation
            )
        ) else {
            return nil
        }

        return (
            path: prepared.path,
            fit: prepared.fitModeForPrinter,
            tempFile: prepared.temporaryFilePath
        )
    }

    // MARK: - Printing

    func printSelectedImage() async {
        guard let prepared = prepareImageForPrint() else { return }
        await MainActor.run {
            isPrinting = true
            printProgress = nil
        }
        let progressRelay = PrintProgressRelay(viewModel: self)

        let success = await ffi.printImage(
            path: prepared.path,
            quality: 100,
            fit: prepared.fit
        ) { sent, total in
            progressRelay.update(sent: sent, total: total)
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
            let item = queue[queueIndex]
            await MainActor.run {
                batchPrintIndex = offset + 1
                selectQueueItem(at: queueIndex)
            }

            guard let prepared = prepareImageForPrint(item) else {
                await MainActor.run {
                    isPrinting = false
                    batchPrintTotal = 0
                    showError(L("print_failed_at", offset + 1, count))
                }
                return
            }
            let progressRelay = PrintProgressRelay(viewModel: self)

            let success = await ffi.printImage(
                path: prepared.path,
                quality: 100,
                fit: prepared.fit
            ) { sent, total in
                progressRelay.update(sent: sent, total: total)
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
        Task { @MainActor [weak self] in
            self?.coreVersion = await AppVersionService.loadBundledCoreVersion()
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

    func checkForUpdates() async {
        let currentCoreVersion = coreVersion.hasPrefix("v") ? String(coreVersion.dropFirst()) : coreVersion
        let update = await AppUpdateService.checkForUpdates(
            currentAppVersion: AppVersionService.currentAppVersion,
            currentCoreVersion: currentCoreVersion
        )

        await MainActor.run {
            self.updateAvailable = update?.version
            self.updateDownloadURL = update?.downloadURL
        }
    }

    func performUpdate() {
        guard let urlString = updateDownloadURL else { return }
        isUpdating = true
        updateProgress = 0
        updateError = nil

        AppUpdateService.installUpdate(
            from: urlString,
            onProgress: { [weak self] progress in
                self?.updateProgress = progress
            },
            onFailure: { [weak self] message in
                self?.isUpdating = false
                self?.updateError = message
            }
        )
    }
}

private final class PrintProgressRelay: @unchecked Sendable {
    weak var viewModel: ViewModel?

    init(viewModel: ViewModel) {
        self.viewModel = viewModel
    }

    func update(sent: UInt32, total: UInt32) {
        DispatchQueue.main.async { [weak self] in
            self?.viewModel?.printProgress = (sent: Int(sent), total: Int(total))
        }
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
