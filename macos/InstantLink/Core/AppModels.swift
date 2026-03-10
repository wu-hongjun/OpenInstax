import AppKit
import Foundation

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
    var filmFormatTag: String? { PrinterModelCatalog.filmFormatTag(for: effectiveModel) }

    static let availableModels = [
        "Instax Mini Link", "Instax Mini Link 2", "Instax Mini Link 3",
        "Instax Square Link", "Instax Wide Link",
    ]

    static let availableColors = [
        "White", "Pink", "Blue", "Green", "Gray", "Black", "Beige",
    ]

    static func parseSerialNumber(from bleIdentifier: String) -> String? {
        var value = bleIdentifier
        if value.hasPrefix("INSTAX-") {
            value = String(value.dropFirst(7))
        }
        let digits = String(value.prefix(while: { $0.isNumber }))
        return digits.isEmpty ? nil : digits
    }

    private static let defaultsKey = "printerProfiles"

    static func loadAll() -> [String: PrinterProfile] {
        guard let data = UserDefaults.standard.data(forKey: defaultsKey),
              let profiles = try? JSONDecoder().decode([String: PrinterProfile].self, from: data)
        else {
            return [:]
        }
        return profiles
    }

    static func save(_ profiles: [String: PrinterProfile]) {
        if let data = try? JSONEncoder().encode(profiles) {
            UserDefaults.standard.set(data, forKey: defaultsKey)
        }
    }
}

enum PrinterModelCatalog {
    static func aspectRatio(for model: String?) -> CGFloat? {
        switch model {
        case "Instax Square Link":
            return 1.0
        case "Instax Mini Link",
             "Instax Mini Link 2",
             "Instax Mini Link 3":
            return 600.0 / 800.0
        case "Instax Wide Link":
            return 1260.0 / 840.0
        default:
            return nil
        }
    }

    static func filmFormatTag(for model: String?) -> String? {
        switch model {
        case "Instax Square Link":
            return "Sqre"
        case "Instax Mini Link",
             "Instax Mini Link 2",
             "Instax Mini Link 3":
            return "Mini"
        case "Instax Wide Link":
            return "Wide"
        default:
            return nil
        }
    }
}

// MARK: - App Defaults

struct NewPhotoDefaults: Codable, Equatable {
    static let storageKey = "newPhotoDefaults"

    var fitMode: String = "crop"
    var rotationAngle: Int = 0
    var isHorizontallyFlipped: Bool = false
    var overlays: [OverlayItem] = []
    var filmOrientation: String = "default"

    static func load() -> Self {
        if let data = UserDefaults.standard.data(forKey: storageKey),
           let decoded = try? JSONDecoder().decode(Self.self, from: data) {
            return decoded.sanitized
        }
        return Self()
    }

    func save() {
        if let data = try? JSONEncoder().encode(sanitized) {
            UserDefaults.standard.set(data, forKey: Self.storageKey)
        }
    }

    var sanitized: Self {
        var sanitized = self
        var didKeepTimestamp = false
        sanitized.overlays = overlays
            .filter { overlay in
                guard case .timestamp = overlay.content else {
                    return false
                }
                guard !didKeepTimestamp else {
                    return false
                }
                didKeepTimestamp = true
                return true
            }
        return sanitized
    }
}

let initialNewPhotoDefaults = NewPhotoDefaults.load()

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

enum TimestampPresetCatalog {
    static let presetOrder: [String] = ["classic", "modern", "dotMatrix", "labPrint", "machinePrint"]

    static let presets: [String: DateStampPreset] = [
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
}

// MARK: - Queue

struct QueueItemEditState: Equatable {
    var fitMode: String
    var cropOffsetNormalized: CGSize = .zero
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
