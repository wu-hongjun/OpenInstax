import SwiftUI
import UniformTypeIdentifiers
import ImageIO

@main
struct OpenInstaxApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var appDelegate
    @StateObject private var viewModel = ViewModel()

    var body: some Scene {
        WindowGroup {
            MainView(viewModel: viewModel)
        }
    }
}

// MARK: - App Delegate (menu bar icon + window management)

class AppDelegate: NSObject, NSApplicationDelegate {
    var statusItem: NSStatusItem?

    func applicationDidFinishLaunching(_ notification: Notification) {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        if let button = statusItem?.button {
            button.image = NSImage(systemSymbolName: "printer.fill", accessibilityDescription: "OpenInstax")
        }

        let menu = NSMenu()
        menu.addItem(NSMenuItem(title: "Show Window", action: #selector(showWindow), keyEquivalent: ""))
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "Find Printer", action: #selector(findPrinter), keyEquivalent: ""))
        menu.addItem(NSMenuItem(title: "Refresh Status", action: #selector(refreshStatus), keyEquivalent: ""))
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "Quit", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
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

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if !flag { showWindow() }
        return true
    }
}

extension Notification.Name {
    static let findPrinter = Notification.Name("findPrinter")
    static let refreshStatus = Notification.Name("refreshStatus")
}

// MARK: - Printer Profile

struct PrinterProfile: Codable, Equatable {
    let bleIdentifier: String
    let serialNumber: String?
    var detectedModel: String
    var overriddenModel: String?
    var deviceColor: String?
    var customName: String?

    var displayName: String { customName?.isEmpty == false ? customName! : bleIdentifier }
    var effectiveModel: String { overriddenModel ?? detectedModel }

    static let availableModels = [
        "Instax Mini Link", "Instax Mini Link 2",
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

// MARK: - View Model

class ViewModel: ObservableObject {
    let cli = OpenInstaxCLI()

    // Printer state
    @Published var isConnected = false
    @Published var printerName: String?
    @Published var printerModel: String?
    @Published var battery: Int = 0
    @Published var filmRemaining: Int = 0
    @Published var printCount: Int = 0

    // Image selection
    @Published var selectedImage: NSImage?
    @Published var selectedImagePath: String?

    // Print options
    @Published var fitMode: String = "crop" {
        didSet { resetCropAdjustments() }
    }

    // Crop interaction (pan & zoom)
    @Published var cropOffset: CGSize = .zero
    @Published var cropZoom: CGFloat = 1.0
    var cropFrameSize: CGSize = .zero

    // Date stamp
    @Published var dateStampEnabled: Bool = false
    @Published var showTimeRow: Bool = true
    @Published var dateStampPosition: String = "bottomRight"
    @Published var dateStampFont: String = "Courier-Bold"
    @Published var dateStampColor: Color = Color(red: 1.0, green: 0.4, blue: 0.0)
    @Published var lightBleedEnabled: Bool = false
    var imageDate: Date?

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
             "Instax Mini Link 2":  return 600.0/800.0  // 600×800
        case "Instax Wide Link":    return 1260.0/840.0  // 1260×840
        default: return nil
        }
    }

    // UI state
    @Published var isPrinting = false
    @Published var isSearching = false
    @Published var statusMessage: String?
    @Published var hasSearchedOnce = false

    // Pairing mode
    @Published var isPairing = false
    @Published var pairingAttempt = 0
    private var pairingTask: Task<Void, Never>?

    private var autoRefreshTimer: Timer?

    init() {
        autoRefreshTimer = Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { [weak self] _ in
            guard let self = self, self.isConnected else { return }
            Task { await self.refreshStatus() }
        }
    }

    deinit { autoRefreshTimer?.invalidate() }

    // MARK: - Pairing Mode (continuous scan loop)

    func startPairing() {
        // Cancel any existing pairing task
        pairingTask?.cancel()

        isPairing = true
        pairingAttempt = 0
        statusMessage = nil

        pairingTask = Task { [weak self] in
            guard let self = self else { return }

            while !Task.isCancelled {
                await MainActor.run { self.pairingAttempt += 1 }

                // Phase 1: lightweight scan to discover printers
                let printers = await self.cli.scan(duration: 3)

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
                    // Phase 2: connect and query info (only when we know it's there)
                    let info = await self.cli.info(device: target)

                    if Task.isCancelled { break }

                    if let info = info {
                        await MainActor.run {
                            self.isConnected = true
                            self.isPairing = false
                            self.printerName = info.name
                            self.printerModel = info.model
                            self.battery = info.battery
                            self.filmRemaining = info.filmRemaining
                            self.printCount = info.printCount
                            self.selectedPrinter = info.name
                            self.hasSearchedOnce = true
                            if !self.availablePrinters.contains(info.name) {
                                self.availablePrinters.append(info.name)
                            }

                            // Profile management
                            if var existing = self.printerProfiles[info.name] {
                                existing.detectedModel = info.model
                                self.saveProfile(existing)
                            } else {
                                let profile = PrinterProfile(
                                    bleIdentifier: info.name,
                                    serialNumber: PrinterProfile.parseSerialNumber(from: info.name),
                                    detectedModel: info.model
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

    // MARK: - Refresh (quiet — no "searching" spinner, just update numbers)

    func refreshStatus() async {
        let device = await MainActor.run { selectedPrinter }
        let info = await cli.info(device: device)

        await MainActor.run {
            if let info = info {
                isConnected = true
                printerName = info.name
                printerModel = info.model
                battery = info.battery
                filmRemaining = info.filmRemaining
                printCount = info.printCount
            } else {
                isConnected = false
            }
        }
    }

    // MARK: - Scan (discover all printers for the picker, then connect)

    func scanAllPrinters() async {
        await MainActor.run { isSearching = true }
        let printers = await cli.scan()
        await MainActor.run {
            availablePrinters = printers
            isSearching = false
            hasSearchedOnce = true

            if printers.isEmpty {
                showStatus("No printers found")
                return
            }
            showStatus("Found \(printers.count) printer\(printers.count == 1 ? "" : "s")")
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
        startPairing()
    }

    // MARK: - Image Selection

    func selectImage() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.png, .jpeg, .heic, .tiff, .webP]
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = false
        panel.message = "Select an image to print"
        if panel.runModal() == .OK, let url = panel.url {
            loadImage(from: url)
        }
    }

    func loadImage(from url: URL) {
        guard let image = NSImage(contentsOf: url) else { return }
        selectedImage = image
        selectedImagePath = url.path
        resetCropAdjustments()
        imageDate = Self.extractImageDate(from: url)
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
        selectedImage = nil
        selectedImagePath = nil
        imageDate = nil
        resetCropAdjustments()
    }

    func resetCropAdjustments() {
        cropOffset = .zero
        cropZoom = 1.0
    }

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
        let cal = Calendar.current
        let line1 = String(format: "%d  %02d  %02d",
            cal.component(.year, from: date),
            cal.component(.month, from: date),
            cal.component(.day, from: date))
        let line2 = String(format: "%02d:%02d:%02d",
            cal.component(.hour, from: date),
            cal.component(.minute, from: date),
            cal.component(.second, from: date))

        let fontSize = CGFloat(height) * 0.035
        let padding = fontSize * 0.5
        let nsColor = NSColor(dateStampColor)
        let font = NSFont(name: dateStampFont, size: fontSize)
            ?? NSFont.monospacedSystemFont(ofSize: fontSize, weight: .bold)

        let attributes: [NSAttributedString.Key: Any] = [
            .font: font,
            .foregroundColor: nsColor
        ]

        let attrLine1 = NSAttributedString(string: line1, attributes: attributes)
        let line1Size = attrLine1.size()

        var totalHeight = line1Size.height
        var attrLine2: NSAttributedString?
        var line2Size: CGSize = .zero
        if showTimeRow {
            let a2 = NSAttributedString(string: line2, attributes: attributes)
            line2Size = a2.size()
            totalHeight += line2Size.height + fontSize * 0.1
            attrLine2 = a2
        }

        let maxWidth = max(line1Size.width, line2Size.width)

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

        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(cgContext: context, flipped: false)

        func drawText() {
            let line1Y = blockY + (showTimeRow ? line2Size.height + fontSize * 0.1 : 0)
            attrLine1.draw(at: NSPoint(x: blockX, y: line1Y))
            if let attrLine2 = attrLine2 {
                attrLine2.draw(at: NSPoint(x: blockX, y: blockY))
            }
        }

        if lightBleedEnabled {
            let shadow = NSShadow()
            shadow.shadowColor = nsColor.withAlphaComponent(0.8)
            shadow.shadowBlurRadius = max(3, fontSize * 0.08)
            shadow.shadowOffset = .zero
            shadow.set()
            drawText()
            NSShadow().set()
            drawText()
        } else {
            drawText()
        }

        NSGraphicsContext.restoreGraphicsState()
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

        if dateStampEnabled, let stamped = stampImage(currentCG) {
            currentCG = stamped
            processed = true
        }

        if processed {
            let tempURL = FileManager.default.temporaryDirectory
                .appendingPathComponent("openinstax_print_\(UUID().uuidString).jpg")
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
        await MainActor.run { isPrinting = true }

        let success = await cli.printImage(
            path: prepared.path,
            quality: 100,
            fit: prepared.fit,
            device: selectedPrinter
        )

        if let temp = prepared.tempFile {
            try? FileManager.default.removeItem(atPath: temp)
        }

        await refreshStatus()
        await MainActor.run {
            isPrinting = false
            showStatus(success ? "Printed!" : "Print failed")
        }
    }

    // MARK: - Status Message

    func showStatus(_ message: String) {
        statusMessage = message
        DispatchQueue.main.asyncAfter(deadline: .now() + 4) { [weak self] in
            if self?.statusMessage == message { self?.statusMessage = nil }
        }
    }
}

// MARK: - Main Window View

struct MainView: View {
    @ObservedObject var viewModel: ViewModel

    var body: some View {
        VStack(spacing: 0) {
            // Header
            HStack {
                Text("OpenInstax")
                    .font(.title2)
                    .fontWeight(.semibold)

                if let msg = viewModel.statusMessage {
                    Text(msg)
                        .font(.caption)
                        .foregroundColor(.secondary)
                }

                Spacer()

                // Connection indicator
                HStack(spacing: 6) {
                    if viewModel.isPairing {
                        ProgressView().controlSize(.small)
                        Text("Pairing...")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    } else {
                        Circle()
                            .fill(viewModel.isConnected ? .green : .secondary)
                            .frame(width: 8, height: 8)
                        Text(viewModel.isConnected
                             ? (viewModel.currentPrinterDisplayName ?? "Connected")
                             : "Disconnected")
                            .font(.caption)
                            .foregroundColor(viewModel.isConnected ? .primary : .secondary)
                        if viewModel.isConnected {
                            Button {
                                if let bleId = viewModel.printerName,
                                   let profile = viewModel.printerProfiles[bleId] {
                                    viewModel.editingProfile = profile
                                    viewModel.showProfileEditor = true
                                }
                            } label: {
                                Image(systemName: "pencil.circle")
                            }
                            .buttonStyle(.plain)
                            .foregroundColor(.secondary)
                        }
                    }
                }
            }
            .padding(.horizontal, 20)
            .padding(.top, 16)
            .padding(.bottom, 12)

            Divider()

            if viewModel.isConnected {
                // Status bar
                HStack(spacing: 24) {
                    StatusItem(icon: "battery.100", value: "\(viewModel.battery)%")
                    StatusItem(icon: "film", value: "\(viewModel.filmRemaining) left")
                }
                .padding(.horizontal, 20)
                .padding(.vertical, 10)

                Divider()

                // Print area
                PrintAreaView(viewModel: viewModel)
                    .padding(.horizontal, 20)
                    .padding(.top, 16)

                // Print options
                PrintOptionsView(viewModel: viewModel)
                    .padding(.horizontal, 20)
                    .padding(.top, 12)

            } else {
                // Disconnected — pairing mode
                VStack(spacing: 16) {
                    Spacer()

                    if viewModel.isPairing {
                        // Active pairing
                        ProgressView()
                            .controlSize(.regular)
                        Text("Looking for your printer...")
                            .font(.callout)
                            .foregroundColor(.secondary)
                        Text("Attempt \(viewModel.pairingAttempt)")
                            .font(.caption)
                            .foregroundColor(.secondary)
                            .monospacedDigit()
                        VStack(alignment: .leading, spacing: 4) {
                            Label("Make sure your printer is turned on", systemImage: "1.circle")
                            Label("Press the button to enable Bluetooth", systemImage: "2.circle")
                            Label("Keep the printer nearby", systemImage: "3.circle")
                        }
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .padding(.vertical, 4)
                        Button("Cancel") {
                            viewModel.stopPairing()
                        }
                        .controlSize(.large)
                    } else if viewModel.hasSearchedOnce {
                        // Pairing cancelled / failed
                        Image(systemName: "printer.dotmatrix")
                            .font(.system(size: 40))
                            .foregroundColor(.secondary)
                        Text("No printer found")
                            .font(.headline)
                        VStack(alignment: .leading, spacing: 4) {
                            Label("Make sure your printer is turned on", systemImage: "1.circle")
                            Label("Press the button to enable Bluetooth", systemImage: "2.circle")
                            Label("Keep the printer nearby", systemImage: "3.circle")
                        }
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .padding(.vertical, 4)
                        Button("Try Again") {
                            viewModel.startPairing()
                        }
                        .controlSize(.large)
                    } else {
                        // First launch
                        Image(systemName: "printer.dotmatrix")
                            .font(.system(size: 40))
                            .foregroundColor(.secondary)
                        Text("Connect to your printer")
                            .font(.headline)
                        VStack(alignment: .leading, spacing: 4) {
                            Label("Turn on your Instax printer", systemImage: "1.circle")
                            Label("Press the button to enable Bluetooth", systemImage: "2.circle")
                        }
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .padding(.vertical, 4)
                        Button("Find Printer") {
                            viewModel.startPairing()
                        }
                        .buttonStyle(.borderedProminent)
                        .controlSize(.large)
                    }

                    Spacer()
                }
                .frame(maxWidth: .infinity)
                .padding(.horizontal, 20)
            }

            Spacer(minLength: 0)
            Divider()

            // Footer
            HStack(spacing: 8) {
                if viewModel.availablePrinters.count > 1 {
                    Picker("", selection: Binding(
                        get: { viewModel.selectedPrinter ?? "" },
                        set: { viewModel.switchPrinter(to: $0) }
                    )) {
                        ForEach(viewModel.availablePrinters, id: \.self) { p in
                            Text(viewModel.printerProfiles[p]?.displayName ?? p).tag(p)
                        }
                    }
                    .frame(maxWidth: 160)
                    .labelsHidden()
                }

                if viewModel.isConnected {
                    Button {
                        Task { await viewModel.scanAllPrinters() }
                    } label: {
                        Image(systemName: "arrow.clockwise")
                    }
                    .controlSize(.small)
                    .disabled(viewModel.isSearching)
                    .help("Scan for other printers")

                    Button {
                        Task { await viewModel.refreshStatus() }
                    } label: {
                        Image(systemName: "arrow.triangle.2.circlepath")
                    }
                    .controlSize(.small)
                    .help("Refresh printer status")
                }

                Spacer()

                Text("v0.1.0")
                    .font(.caption2)
                    .foregroundColor(.secondary)
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 10)
        }
        .frame(minWidth: 420, idealWidth: 440, minHeight: 380)
        .sheet(isPresented: $viewModel.showProfileSheet) {
            PrinterProfileSheet(viewModel: viewModel, isPostPairing: true)
        }
        .sheet(isPresented: $viewModel.showProfileEditor) {
            PrinterProfileSheet(viewModel: viewModel, isPostPairing: false)
        }
        .onAppear {
            // Auto-start pairing on launch
            if !viewModel.isConnected && !viewModel.isPairing {
                viewModel.startPairing()
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: .findPrinter)) { _ in
            if viewModel.isPairing {
                viewModel.stopPairing()
            } else {
                viewModel.startPairing()
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: .refreshStatus)) { _ in
            Task { await viewModel.refreshStatus() }
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

// MARK: - Print Area View

private struct CropFrameSizeKey: PreferenceKey {
    static var defaultValue: CGSize = .zero
    static func reduce(value: inout CGSize, nextValue: () -> CGSize) {
        value = nextValue()
    }
}

struct PrintAreaView: View {
    @ObservedObject var viewModel: ViewModel
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

            if viewModel.isPrinting {
                VStack(spacing: 8) {
                    ProgressView().controlSize(.regular)
                    Text("Printing...")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
            } else if let image = viewModel.selectedImage {
                ZStack(alignment: .topTrailing) {
                    if viewModel.fitMode == "crop", let ar = viewModel.printerAspectRatio {
                        // Interactive crop preview: drag to pan, pinch to zoom
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
                            )
                            .overlay(alignment: stampAlignment) {
                                dateStampOverlay()
                            }
                            .clipped()
                            .clipShape(RoundedRectangle(cornerRadius: 6))
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
                            .padding(4)
                    } else if viewModel.fitMode == "contain", let ar = viewModel.printerAspectRatio {
                        // Contain preview: image fitted inside printer-ratio frame
                        Color.white
                            .aspectRatio(ar, contentMode: .fit)
                            .overlay(
                                Image(nsImage: image)
                                    .resizable()
                                    .aspectRatio(contentMode: .fit)
                            )
                            .overlay(alignment: stampAlignment) {
                                dateStampOverlay()
                            }
                            .clipped()
                            .cornerRadius(6)
                            .padding(4)
                    } else {
                        // Stretch or unknown model: show as-is
                        Image(nsImage: image)
                            .resizable()
                            .aspectRatio(contentMode: .fit)
                            .overlay(alignment: stampAlignment) {
                                dateStampOverlay()
                            }
                            .cornerRadius(6)
                            .padding(4)
                    }

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
                    Text("Drop image or click Open File")
                        .font(.callout)
                        .foregroundColor(.secondary)
                    Button("Open File") { viewModel.selectImage() }
                        .controlSize(.small)
                }
            }
        }
        .frame(height: 200)
        .onDrop(of: [.fileURL], isTargeted: $isTargeted) { providers in
            guard let provider = providers.first else { return false }
            _ = provider.loadObject(ofClass: URL.self) { url, _ in
                guard let url = url else { return }
                DispatchQueue.main.async { viewModel.loadImage(from: url) }
            }
            return true
        }
    }

    // MARK: - Date stamp overlay

    private var stampAlignment: Alignment {
        switch viewModel.dateStampPosition {
        case "topLeft": return .topLeading
        case "topRight": return .topTrailing
        case "bottomLeft": return .bottomLeading
        default: return .bottomTrailing
        }
    }

    @ViewBuilder
    private func dateStampOverlay() -> some View {
        if viewModel.dateStampEnabled {
            let date = viewModel.imageDate ?? Date()
            let cal = Calendar.current
            let line1 = String(format: "%d  %02d  %02d",
                cal.component(.year, from: date),
                cal.component(.month, from: date),
                cal.component(.day, from: date))
            let line2 = String(format: "%02d:%02d:%02d",
                cal.component(.hour, from: date),
                cal.component(.minute, from: date),
                cal.component(.second, from: date))

            VStack(alignment: .trailing, spacing: 0) {
                Text(line1)
                if viewModel.showTimeRow {
                    Text(line2)
                }
            }
            .font(.custom(viewModel.dateStampFont, size: 11))
            .foregroundColor(viewModel.dateStampColor)
            .shadow(
                color: viewModel.lightBleedEnabled ? viewModel.dateStampColor.opacity(0.8) : .clear,
                radius: viewModel.lightBleedEnabled ? 2 : 0
            )
            .padding(4)
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

// MARK: - Print Options View

struct PrintOptionsView: View {
    @ObservedObject var viewModel: ViewModel

    var body: some View {
        VStack(spacing: 10) {
            HStack {
                Text("Fit:")
                    .font(.callout)
                    .frame(width: 55, alignment: .leading)
                Picker("", selection: $viewModel.fitMode) {
                    Text("Crop").tag("crop")
                    Text("Contain").tag("contain")
                    Text("Stretch").tag("stretch")
                }
                .pickerStyle(.segmented)
                .labelsHidden()
            }

            // Date stamp section
            VStack(spacing: 8) {
                Toggle("Date stamp", isOn: $viewModel.dateStampEnabled)
                    .font(.callout)

                if viewModel.dateStampEnabled {
                    // Stamp preview
                    RoundedRectangle(cornerRadius: 6)
                        .fill(Color(white: 0.15))
                        .frame(height: 40)
                        .overlay(
                            VStack(alignment: .trailing, spacing: 0) {
                                Text(stampPreviewLine1)
                                if viewModel.showTimeRow {
                                    Text(stampPreviewLine2)
                                }
                            }
                            .font(.custom(viewModel.dateStampFont, size: 13))
                            .foregroundColor(viewModel.dateStampColor)
                            .shadow(
                                color: viewModel.lightBleedEnabled
                                    ? viewModel.dateStampColor.opacity(0.8) : .clear,
                                radius: viewModel.lightBleedEnabled ? 2 : 0
                            )
                        )

                    Toggle("Show time", isOn: $viewModel.showTimeRow)
                        .font(.callout)

                    Toggle("Light bleed", isOn: $viewModel.lightBleedEnabled)
                        .font(.callout)

                    HStack {
                        Text("Font:")
                            .font(.callout)
                            .frame(width: 55, alignment: .leading)
                        Picker("", selection: $viewModel.dateStampFont) {
                            Text("Courier").tag("Courier-Bold")
                            Text("Menlo").tag("Menlo-Bold")
                            Text("Monaco").tag("Monaco")
                        }
                        .pickerStyle(.segmented)
                        .labelsHidden()
                    }

                    HStack {
                        Text("Color:")
                            .font(.callout)
                            .frame(width: 55, alignment: .leading)
                        ColorPicker("", selection: $viewModel.dateStampColor)
                            .labelsHidden()
                        Spacer()
                    }

                    HStack {
                        Text("Position:")
                            .font(.callout)
                            .frame(width: 55, alignment: .leading)
                        Picker("", selection: $viewModel.dateStampPosition) {
                            Text("\u{2198}").tag("bottomRight")
                            Text("\u{2199}").tag("bottomLeft")
                            Text("\u{2197}").tag("topRight")
                            Text("\u{2196}").tag("topLeft")
                        }
                        .pickerStyle(.segmented)
                        .labelsHidden()
                    }
                }
            }

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
                    Text(viewModel.isPrinting ? "Printing..." : "Print")
                }
                .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .disabled(viewModel.selectedImage == nil || !viewModel.isConnected || viewModel.isPrinting)
        }
    }

    private var stampPreviewLine1: String {
        let date = viewModel.imageDate ?? Date()
        let cal = Calendar.current
        return String(format: "%d  %02d  %02d",
            cal.component(.year, from: date),
            cal.component(.month, from: date),
            cal.component(.day, from: date))
    }

    private var stampPreviewLine2: String {
        let date = viewModel.imageDate ?? Date()
        let cal = Calendar.current
        return String(format: "%02d:%02d:%02d",
            cal.component(.hour, from: date),
            cal.component(.minute, from: date),
            cal.component(.second, from: date))
    }
}

// MARK: - Printer Profile Sheet

struct PrinterProfileSheet: View {
    @ObservedObject var viewModel: ViewModel
    let isPostPairing: Bool

    @State private var customName: String = ""
    @State private var selectedModel: String = ""
    @State private var selectedColor: String = ""

    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(isPostPairing ? "Printer Connected" : "Edit Printer")
                .font(.headline)

            if let profile = viewModel.editingProfile {
                VStack(alignment: .leading, spacing: 8) {
                    if let serial = profile.serialNumber {
                        VStack(alignment: .leading, spacing: 2) {
                            HStack {
                                Text("Serial Number:")
                                    .foregroundColor(.secondary)
                                Text(serial)
                                    .fontWeight(.medium)
                                    .textSelection(.enabled)
                            }
                            Text("Verify this matches the serial number on the bottom of your device")
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                    }

                    HStack {
                        Text("BLE Name:")
                            .foregroundColor(.secondary)
                        Text(profile.bleIdentifier)
                            .font(.caption)
                            .textSelection(.enabled)
                    }
                }

                Divider()

                VStack(alignment: .leading, spacing: 10) {
                    HStack {
                        Text("Model:")
                            .frame(width: 50, alignment: .leading)
                        Picker("", selection: $selectedModel) {
                            ForEach(PrinterProfile.availableModels, id: \.self) { model in
                                Text(model).tag(model)
                            }
                        }
                        .labelsHidden()
                    }

                    HStack {
                        Text("Color:")
                            .frame(width: 50, alignment: .leading)
                        Picker("", selection: $selectedColor) {
                            Text("None").tag("")
                            ForEach(PrinterProfile.availableColors, id: \.self) { color in
                                Text(color).tag(color)
                            }
                        }
                        .labelsHidden()
                    }

                    HStack {
                        Text("Name:")
                            .frame(width: 50, alignment: .leading)
                        TextField("Custom display name", text: $customName)
                            .textFieldStyle(.roundedBorder)
                    }
                }
            }

            Divider()

            HStack {
                Spacer()
                Button("Cancel") { dismiss() }
                    .keyboardShortcut(.cancelAction)
                Button("Save") { saveAndDismiss() }
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
