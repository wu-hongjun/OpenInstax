import SwiftUI
import UniformTypeIdentifiers
import ImageIO

@main
struct InstantLinkApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var appDelegate
    @StateObject private var viewModel = ViewModel()

    var body: some Scene {
        WindowGroup {
            MainView()
                .environmentObject(viewModel)
        }

        Window("Image Editor", id: "image-editor") {
            ImageEditorView()
                .environmentObject(viewModel)
        }
        .defaultSize(width: 600, height: 550)
        .applySuppressedLaunch()
    }
}

extension Scene {
    func applySuppressedLaunch() -> some Scene {
        if #available(macOS 15.0, *) {
            return self.defaultLaunchBehavior(.suppressed)
        } else {
            return self
        }
    }
}

// MARK: - App Delegate (menu bar icon + window management)

class AppDelegate: NSObject, NSApplicationDelegate {
    var statusItem: NSStatusItem?

    func applicationDidFinishLaunching(_ notification: Notification) {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        if let button = statusItem?.button {
            button.image = NSImage(systemSymbolName: "printer.fill", accessibilityDescription: "InstantLink")
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
    let ffi = InstantLinkFFI()!

    // Printer state
    @Published var isConnected = false
    @Published var printerName: String?
    @Published var printerModel: String?
    @Published var battery: Int = 0
    @Published var isCharging: Bool = false
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

    // Rotation
    @Published var rotationAngle: Int = 0  // 0, 90, 180, 270

    // Date stamp
    @Published var dateStampEnabled: Bool = false
    @Published var showTimeRow: Bool = true
    @Published var dateStampPosition: String = "bottomRight"
    @Published var dateStampStyle: String = "classic"
    @Published var dateStampFormat: String = "ymd"
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
    @Published var isRefreshing = false
    @Published var statusMessage: String?
    @Published var hasSearchedOnce = false

    // Pairing mode
    @Published var isPairing = false
    @Published var pairingAttempt = 0
    @Published var pairingStatus: String = "Scanning..."
    private var pairingTask: Task<Void, Never>?

    private var autoRefreshTimer: Timer?

    init() {
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
        pairingStatus = "Scanning..."
        statusMessage = nil

        pairingTask = Task { [weak self] in
            guard let self = self else { return }

            while !Task.isCancelled {
                await MainActor.run {
                    self.pairingAttempt += 1
                    self.pairingStatus = "Scanning..."
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
                        self.pairingStatus = "Connecting to \(target)..."
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

    // MARK: - Refresh (quiet — no "searching" spinner, just update numbers)

    func refreshStatus() async {
        await MainActor.run { isRefreshing = true }

        // With persistent FFI connection, status queries are instant (no scan/reconnect)
        guard ffi.isConnected() else {
            await MainActor.run {
                isRefreshing = false
                isConnected = false
            }
            return
        }

        // Single FFI call fetches battery + film + charging + print count
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
        // Disconnect existing connection before connecting to new printer
        Task {
            if ffi.isConnected() {
                await ffi.disconnect()
            }
            await MainActor.run { isConnected = false }
            startPairing()
        }
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
        rotationAngle = 0
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
        rotationAngle = 0
        resetCropAdjustments()
    }

    func resetCropAdjustments() {
        cropOffset = .zero
        cropZoom = 1.0
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
        let color: (CGFloat, CGFloat, CGFloat)  // RGB 0-1
        let glowColor: (CGFloat, CGFloat, CGFloat)
    }

    static let dateStampPresets: [String: DateStampPreset] = [
        "classic": DateStampPreset(
            color: (0.961, 0.541, 0.122),      // #F58A1F amber-orange
            glowColor: (0.961, 0.541, 0.122)
        ),
        "dotMatrix": DateStampPreset(
            color: (1.0, 0.435, 0.165),         // #FF6F2A redder orange
            glowColor: (1.0, 0.435, 0.165)
        ),
        "bw": DateStampPreset(
            color: (0.953, 0.933, 0.890),       // #F3EEE3 warm off-white
            glowColor: (0.953, 0.933, 0.890)
        ),
    ]

    // MARK: - 7-Segment Digit Renderer

    // Segment map: which segments (a-g) are on for each digit 0-9
    static let segmentMap: [[Bool]] = [
        // a     b     c     d     e     f     g
        [true,  true,  true,  true,  true,  true,  false], // 0
        [false, true,  true,  false, false, false, false], // 1
        [true,  true,  false, true,  true,  false, true],  // 2
        [true,  true,  true,  true,  false, false, true],  // 3
        [false, true,  true,  false, false, true,  true],  // 4
        [true,  false, true,  true,  false, true,  true],  // 5
        [true,  false, true,  true,  true,  true,  true],  // 6
        [true,  true,  true,  false, false, false, false], // 7
        [true,  true,  true,  true,  true,  true,  true],  // 8
        [true,  true,  true,  true,  false, true,  true],  // 9
    ]

    /// Draw a single 7-segment digit into a CGContext at the given origin.
    /// `cellH` = total digit cell height. Segments are computed relative to this.
    static func drawSevenSegmentDigit(
        _ digit: Int, in ctx: CGContext,
        x: CGFloat, y: CGFloat, cellH: CGFloat
    ) {
        let segW = cellH * 0.15          // segment thickness
        let gap = segW * 0.08            // gap between segments
        let cellW = cellH * 0.55         // digit cell width
        let halfH = (cellH - segW) / 2.0

        // Segment geometry: each segment is a filled trapezoid
        // Origin (x,y) is bottom-left of digit cell in CG coords
        let segments = segmentMap[digit]

        // Helper: draw a horizontal segment
        func hSeg(_ sx: CGFloat, _ sy: CGFloat, _ length: CGFloat) {
            let t = segW * 0.3  // taper
            let path = CGMutablePath()
            path.move(to: CGPoint(x: sx + t, y: sy + segW))
            path.addLine(to: CGPoint(x: sx + length - t, y: sy + segW))
            path.addLine(to: CGPoint(x: sx + length, y: sy + segW / 2))
            path.addLine(to: CGPoint(x: sx + length - t, y: sy))
            path.addLine(to: CGPoint(x: sx + t, y: sy))
            path.addLine(to: CGPoint(x: sx, y: sy + segW / 2))
            path.closeSubpath()
            ctx.addPath(path)
            ctx.fillPath()
        }

        // Helper: draw a vertical segment
        func vSeg(_ sx: CGFloat, _ sy: CGFloat, _ length: CGFloat) {
            let t = segW * 0.3
            let path = CGMutablePath()
            path.move(to: CGPoint(x: sx, y: sy + t))
            path.addLine(to: CGPoint(x: sx, y: sy + length - t))
            path.addLine(to: CGPoint(x: sx + segW / 2, y: sy + length))
            path.addLine(to: CGPoint(x: sx + segW, y: sy + length - t))
            path.addLine(to: CGPoint(x: sx + segW, y: sy + t))
            path.addLine(to: CGPoint(x: sx + segW / 2, y: sy))
            path.closeSubpath()
            ctx.addPath(path)
            ctx.fillPath()
        }

        // a: top horizontal
        if segments[0] {
            hSeg(x + gap, y + cellH - segW, cellW - gap * 2)
        }
        // b: top-right vertical
        if segments[1] {
            vSeg(x + cellW - segW, y + halfH + gap, halfH - gap)
        }
        // c: bottom-right vertical
        if segments[2] {
            vSeg(x + cellW - segW, y + segW + gap, halfH - gap)
        }
        // d: bottom horizontal
        if segments[3] {
            hSeg(x + gap, y, cellW - gap * 2)
        }
        // e: bottom-left vertical
        if segments[4] {
            vSeg(x, y + segW + gap, halfH - gap)
        }
        // f: top-left vertical
        if segments[5] {
            vSeg(x, y + halfH + gap, halfH - gap)
        }
        // g: middle horizontal
        if segments[6] {
            hSeg(x + gap, y + halfH, cellW - gap * 2)
        }
    }

    /// Format the date into digit groups based on format setting.
    /// Returns an array of 2-digit strings, e.g. ["26", "03", "08"]
    func dateDigitGroups(from date: Date) -> [String] {
        let cal = Calendar.current
        let y = cal.component(.year, from: date) % 100
        let m = cal.component(.month, from: date)
        let d = cal.component(.day, from: date)
        let yy = String(format: "%02d", y)
        let mm = String(format: "%02d", m)
        let dd = String(format: "%02d", d)

        switch dateStampFormat {
        case "mdy": return [mm, dd, yy]
        case "dmy": return [dd, mm, yy]
        default:    return [yy, mm, dd]  // ymd
        }
    }

    func timeDigitGroups(from date: Date) -> [String] {
        let cal = Calendar.current
        let h = cal.component(.hour, from: date)
        let m = cal.component(.minute, from: date)
        return [String(format: "%02d", h), String(format: "%02d", m)]
    }

    /// Draw a row of digit groups into a CGContext.
    /// Returns the total width of the rendered row.
    @discardableResult
    static func drawDigitRow(
        groups: [String], in ctx: CGContext,
        x: CGFloat, y: CGFloat, cellH: CGFloat
    ) -> CGFloat {
        let cellW = cellH * 0.55
        let interDigit = cellW * 0.60
        let groupGap = cellW * 1.20

        var curX = x
        for (gi, group) in groups.enumerated() {
            for (di, ch) in group.enumerated() {
                if let digit = ch.wholeNumberValue {
                    drawSevenSegmentDigit(digit, in: ctx, x: curX, y: y, cellH: cellH)
                }
                curX += cellW
                if di < group.count - 1 {
                    curX += interDigit
                }
            }
            if gi < groups.count - 1 {
                curX += groupGap
            }
        }
        return curX - x
    }

    /// Measure the total width of a digit row without drawing.
    static func measureDigitRow(groups: [String], cellH: CGFloat) -> CGFloat {
        let cellW = cellH * 0.55
        let interDigit = cellW * 0.60
        let groupGap = cellW * 1.20

        var w: CGFloat = 0
        for (gi, group) in groups.enumerated() {
            for (di, _) in group.enumerated() {
                w += cellW
                if di < group.count - 1 {
                    w += interDigit
                }
            }
            if gi < groups.count - 1 {
                w += groupGap
            }
        }
        return w
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
        let cellH = CGFloat(height) * 0.026
        let padding = cellH * 0.8
        let rowGap = cellH * 0.3

        let preset = Self.dateStampPresets[dateStampStyle]
            ?? Self.dateStampPresets["classic"]!
        let (r, g, b) = preset.color
        let (gr, gg, gb) = preset.glowColor

        let dateGroups = dateDigitGroups(from: date)
        let timeGroups = timeDigitGroups(from: date)

        let dateWidth = Self.measureDigitRow(groups: dateGroups, cellH: cellH)
        let timeWidth = showTimeRow
            ? Self.measureDigitRow(groups: timeGroups, cellH: cellH) : 0
        let maxWidth = max(dateWidth, timeWidth)
        let totalHeight = cellH + (showTimeRow ? cellH + rowGap : 0)

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
        func drawDigits() {
            // Date row (top row)
            let dateY = blockY + (showTimeRow ? cellH + rowGap : 0)
            Self.drawDigitRow(groups: dateGroups, in: context,
                              x: blockX, y: dateY, cellH: cellH)
            // Time row (below date)
            if showTimeRow {
                Self.drawDigitRow(groups: timeGroups, in: context,
                                  x: blockX, y: blockY, cellH: cellH)
            }
        }

        if lightBleedEnabled {
            // Glow pass: draw with shadow
            context.saveGState()
            context.setShadow(
                offset: .zero,
                blur: cellH * 0.15,
                color: CGColor(
                    srgbRed: gr, green: gg, blue: gb, alpha: 0.6
                )
            )
            context.setFillColor(CGColor(
                srgbRed: r, green: g, blue: b, alpha: 1.0
            ))
            drawDigits()
            context.restoreGState()

            // Sharp overdraw
            context.setFillColor(CGColor(
                srgbRed: r, green: g, blue: b, alpha: 1.0
            ))
            drawDigits()
        } else {
            context.setFillColor(CGColor(
                srgbRed: r, green: g, blue: b, alpha: 1.0
            ))
            drawDigits()
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
        await MainActor.run { isPrinting = true }

        let success = await ffi.printImage(
            path: prepared.path,
            quality: 100,
            fit: prepared.fit
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
    @EnvironmentObject var viewModel: ViewModel
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        VStack(spacing: 0) {
            if viewModel.isConnected {
                // Status bar
                HStack(spacing: 12) {
                    Circle()
                        .fill(.green)
                        .frame(width: 8, height: 8)
                    Text(viewModel.currentPrinterDisplayName ?? "Connected")
                        .font(.caption)
                        .fontWeight(.medium)
                    Button {
                        if let bleId = viewModel.printerName,
                           let profile = viewModel.printerProfiles[bleId] {
                            viewModel.editingProfile = profile
                            viewModel.showProfileEditor = true
                        }
                    } label: {
                        Image(systemName: "pencil")
                    }
                    .buttonStyle(.plain)
                    .foregroundColor(.secondary)

                    Spacer()

                    StatusItem(
                        icon: viewModel.isCharging ? "battery.100.bolt" : "battery.100",
                        value: viewModel.isCharging ? "Charging" : "\(viewModel.battery)%"
                    )
                    StatusItem(icon: "film", value: "\(viewModel.filmRemaining) left")

                    Button {
                        Task { await viewModel.refreshStatus() }
                    } label: {
                        Image(systemName: "arrow.triangle.2.circlepath")
                            .font(.caption)
                            .rotationEffect(.degrees(viewModel.isRefreshing ? 360 : 0))
                            .animation(
                                viewModel.isRefreshing
                                    ? .linear(duration: 1).repeatForever(autoreverses: false)
                                    : .default,
                                value: viewModel.isRefreshing
                            )
                    }
                    .buttonStyle(.plain)
                    .foregroundColor(.secondary)
                    .disabled(viewModel.isRefreshing)
                    .help("Refresh status")

                    if let msg = viewModel.statusMessage {
                        Text(msg)
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 10)

                Divider()

                // Read-only preview
                MainPreviewView(openEditor: { openWindow(id: "image-editor") })
                    .padding(.horizontal, 14)
                    .padding(.top, 16)

                // Actions (Edit + Print)
                MainActionsView(openEditor: { openWindow(id: "image-editor") })
                    .padding(.horizontal, 14)
                    .padding(.top, 12)

            } else {
                // Disconnected — pairing mode
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
                .padding(.horizontal, 14)
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

                Spacer()

                Text("v0.1.0")
                    .font(.caption2)
                    .foregroundColor(.secondary)
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
        }
        .frame(minWidth: 240, idealWidth: 260, minHeight: 380)
        .sheet(isPresented: $viewModel.showProfileSheet) {
            PrinterProfileSheet(isPostPairing: true)
        }
        .sheet(isPresented: $viewModel.showProfileEditor) {
            PrinterProfileSheet(isPostPairing: false)
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
            SevenSegmentStampView(viewModel: viewModel, digitHeight: digitHeight)
                .padding(4)
        }
    }
}

/// SwiftUI Canvas-based 7-segment stamp renderer for previews.
struct SevenSegmentStampView: View {
    @ObservedObject var viewModel: ViewModel
    var digitHeight: CGFloat = 11

    var body: some View {
        let date = viewModel.imageDate ?? Date()
        let dateGroups = viewModel.dateDigitGroups(from: date)
        let timeGroups = viewModel.timeDigitGroups(from: date)
        let cellH = digitHeight
        let rowGap = cellH * 0.3

        let dateWidth = ViewModel.measureDigitRow(groups: dateGroups, cellH: cellH)
        let timeWidth = viewModel.showTimeRow
            ? ViewModel.measureDigitRow(groups: timeGroups, cellH: cellH) : 0
        let maxWidth = max(dateWidth, timeWidth)
        let totalHeight = cellH + (viewModel.showTimeRow ? cellH + rowGap : 0)

        let preset = ViewModel.dateStampPresets[viewModel.dateStampStyle]
            ?? ViewModel.dateStampPresets["classic"]!
        let (r, g, b) = preset.color
        let stampColor = Color(red: r, green: g, blue: b)

        Canvas { ctx, _ in
            // Draw using CGContext via resolved image
            // Use GraphicsContext path drawing instead
            func drawDigitRow(groups: [String], x: CGFloat, y: CGFloat) {
                let cellW = cellH * 0.55
                let interDigit = cellW * 0.60
                let groupGap = cellW * 1.20
                let segW = cellH * 0.15
                let gap = segW * 0.08
                let halfH = (cellH - segW) / 2.0

                var curX = x
                for (gi, group) in groups.enumerated() {
                    for (di, ch) in group.enumerated() {
                        if let digit = ch.wholeNumberValue {
                            let segments = ViewModel.segmentMap[digit]
                            let ox = curX
                            // Note: SwiftUI Canvas has origin at top-left, flip y
                            let oy = y

                            func hSeg(_ sx: CGFloat, _ sy: CGFloat, _ length: CGFloat) {
                                let t = segW * 0.3
                                var path = Path()
                                path.move(to: CGPoint(x: sx + t, y: sy))
                                path.addLine(to: CGPoint(x: sx + length - t, y: sy))
                                path.addLine(to: CGPoint(x: sx + length, y: sy + segW / 2))
                                path.addLine(to: CGPoint(x: sx + length - t, y: sy + segW))
                                path.addLine(to: CGPoint(x: sx + t, y: sy + segW))
                                path.addLine(to: CGPoint(x: sx, y: sy + segW / 2))
                                path.closeSubpath()
                                ctx.fill(path, with: .color(stampColor))
                            }

                            func vSeg(_ sx: CGFloat, _ sy: CGFloat, _ length: CGFloat) {
                                let t = segW * 0.3
                                var path = Path()
                                path.move(to: CGPoint(x: sx, y: sy + t))
                                path.addLine(to: CGPoint(x: sx + segW / 2, y: sy))
                                path.addLine(to: CGPoint(x: sx + segW, y: sy + t))
                                path.addLine(to: CGPoint(x: sx + segW, y: sy + length - t))
                                path.addLine(to: CGPoint(x: sx + segW / 2, y: sy + length))
                                path.addLine(to: CGPoint(x: sx, y: sy + length - t))
                                path.closeSubpath()
                                ctx.fill(path, with: .color(stampColor))
                            }

                            // a: top horizontal
                            if segments[0] { hSeg(ox + gap, oy, cellW - gap * 2) }
                            // b: top-right vertical
                            if segments[1] { vSeg(ox + cellW - segW, oy + segW + gap, halfH - gap) }
                            // c: bottom-right vertical
                            if segments[2] { vSeg(ox + cellW - segW, oy + halfH + gap, halfH - gap) }
                            // d: bottom horizontal
                            if segments[3] { hSeg(ox + gap, oy + cellH - segW, cellW - gap * 2) }
                            // e: bottom-left vertical
                            if segments[4] { vSeg(ox, oy + halfH + gap, halfH - gap) }
                            // f: top-left vertical
                            if segments[5] { vSeg(ox, oy + segW + gap, halfH - gap) }
                            // g: middle horizontal
                            if segments[6] { hSeg(ox + gap, oy + halfH, cellW - gap * 2) }
                        }
                        curX += cellW
                        if di < group.count - 1 {
                            curX += interDigit
                        }
                    }
                    if gi < groups.count - 1 {
                        curX += groupGap
                    }
                }
            }

            drawDigitRow(groups: dateGroups, x: 0, y: 0)
            if viewModel.showTimeRow {
                drawDigitRow(groups: timeGroups, x: 0, y: cellH + rowGap)
            }
        }
        .frame(width: maxWidth, height: totalHeight)
        .shadow(
            color: viewModel.lightBleedEnabled ? stampColor.opacity(0.8) : .clear,
            radius: viewModel.lightBleedEnabled ? 2 : 0
        )
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
                    ProgressView().controlSize(.regular)
                    Text("Printing...")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
            } else if let image = viewModel.selectedImage {
                ZStack(alignment: .topTrailing) {
                    if viewModel.fitMode == "crop", let ar = viewModel.printerAspectRatio {
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
                            .onTapGesture(count: 2) { openEditor() }
                            .padding(4)
                    } else if viewModel.fitMode == "contain", let ar = viewModel.printerAspectRatio {
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
                            .cornerRadius(6)
                            .onTapGesture(count: 2) { openEditor() }
                            .padding(4)
                    } else {
                        Image(nsImage: image)
                            .resizable()
                            .aspectRatio(contentMode: .fit)
                            .rotationEffect(.degrees(Double(viewModel.rotationAngle)))
                            .overlay(alignment: stampAlignmentFor(viewModel)) {
                                DateStampOverlayView()
                            }
                            .cornerRadius(6)
                            .onTapGesture(count: 2) { openEditor() }
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

// MARK: - Main Actions View (Edit + Print buttons, in main window)

struct MainActionsView: View {
    @EnvironmentObject var viewModel: ViewModel
    var openEditor: () -> Void

    var body: some View {
        VStack(spacing: 10) {
            Button {
                openEditor()
            } label: {
                HStack {
                    Image(systemName: "slider.horizontal.3")
                    Text("Edit Image")
                }
                .frame(maxWidth: .infinity)
            }
            .controlSize(.large)
            .disabled(viewModel.selectedImage == nil)

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
}

// MARK: - Image Editor View (editor window root)

struct ImageEditorView: View {
    @EnvironmentObject var viewModel: ViewModel

    var body: some View {
        Group {
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
                    Text("No image selected")
                        .font(.headline)
                        .foregroundColor(.secondary)
                    Button("Open File") { viewModel.selectImage() }
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
                ZStack(alignment: .topTrailing) {
                    if viewModel.fitMode == "crop", let ar = viewModel.printerAspectRatio {
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
                            .cornerRadius(6)
                            .padding(4)
                    } else {
                        Image(nsImage: image)
                            .resizable()
                            .aspectRatio(contentMode: .fit)
                            .rotationEffect(.degrees(Double(viewModel.rotationAngle)))
                            .overlay(alignment: stampAlignmentFor(viewModel)) {
                                DateStampOverlayView()
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
            }
        }
        .frame(minHeight: 250, idealHeight: 350)
        .onDrop(of: [.fileURL], isTargeted: $isTargeted) { providers in
            guard let provider = providers.first else { return false }
            _ = provider.loadObject(ofClass: URL.self) { url, _ in
                guard let url = url else { return }
                DispatchQueue.main.async { viewModel.loadImage(from: url) }
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

    var body: some View {
        ScrollView {
            VStack(spacing: 0) {
                // Fit Mode
                AccordionSection("Fit Mode", icon: "crop") {
                    Picker("", selection: $viewModel.fitMode) {
                        Text("Crop").tag("crop")
                        Text("Contain").tag("contain")
                        Text("Stretch").tag("stretch")
                    }
                    .pickerStyle(.segmented)
                    .labelsHidden()

                    if viewModel.fitMode == "crop" {
                        Button("Reset Crop") {
                            viewModel.resetCropAdjustments()
                        }
                        .controlSize(.small)
                        .disabled(viewModel.cropOffset == .zero && viewModel.cropZoom == 1.0)
                        .frame(maxWidth: .infinity, alignment: .trailing)
                    }
                }

                Divider()

                // Rotate
                AccordionSection("Rotate", icon: "rotate.right") {
                    HStack(spacing: 12) {
                        Button {
                            viewModel.rotateCounterClockwise()
                        } label: {
                            Label("Rotate Left", systemImage: "rotate.left")
                        }
                        .controlSize(.small)

                        Button {
                            viewModel.rotateClockwise()
                        } label: {
                            Label("Rotate Right", systemImage: "rotate.right")
                        }
                        .controlSize(.small)

                        Spacer()
                    }
                }

                Divider()

                // Date Stamp
                AccordionSection("Date Stamp", icon: "calendar", expanded: false) {
                    Toggle("Enabled", isOn: $viewModel.dateStampEnabled)
                        .font(.callout)

                    if viewModel.dateStampEnabled {
                        // Stamp preview (7-segment)
                        RoundedRectangle(cornerRadius: 6)
                            .fill(Color(white: 0.15))
                            .frame(height: 44)
                            .overlay(
                                SevenSegmentStampView(
                                    viewModel: viewModel,
                                    digitHeight: 13
                                )
                            )

                        Toggle("Show time", isOn: $viewModel.showTimeRow)
                            .font(.callout)

                        Toggle("Light bleed", isOn: $viewModel.lightBleedEnabled)
                            .font(.callout)

                        HStack {
                            Text("Style:")
                                .font(.callout)
                                .frame(width: 50, alignment: .leading)
                            Picker("", selection: $viewModel.dateStampStyle) {
                                Text("Classic").tag("classic")
                                Text("Dot Matrix").tag("dotMatrix")
                                Text("B&W").tag("bw")
                            }
                            .pickerStyle(.segmented)
                            .labelsHidden()
                        }

                        HStack {
                            Text("Format:")
                                .font(.callout)
                                .frame(width: 50, alignment: .leading)
                            Picker("", selection: $viewModel.dateStampFormat) {
                                Text("YY MM DD").tag("ymd")
                                Text("MM DD YY").tag("mdy")
                                Text("DD MM YY").tag("dmy")
                            }
                            .pickerStyle(.segmented)
                            .labelsHidden()
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
            }
            .padding(12)
        }
        .frame(minWidth: 200, idealWidth: 220, maxWidth: 260)
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

            Button {
                Task { await viewModel.scanAllPrinters() }
            } label: {
                HStack {
                    Image(systemName: "antenna.radiowaves.left.and.right")
                    Text("Scan for Printers")
                }
            }
            .disabled(viewModel.isSearching)

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
