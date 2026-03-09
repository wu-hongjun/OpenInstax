import Foundation
import SwiftUI

struct OverlayItem: Identifiable, Codable, Equatable {
    var id: UUID = UUID()
    var content: OverlayContent
    var placement: OverlayPlacement = .defaultPlacement
    var opacity: Double = 1.0
    var zIndex: Int = 0
    var isHidden: Bool = false
    var isLocked: Bool = false
    var createdAt: Date = Date()

    var kind: OverlayKind {
        content.kind
    }
}

enum OverlayKind: String, Codable, CaseIterable, Identifiable {
    case text
    case qrCode
    case timestamp
    case image
    case location

    var id: String { rawValue }
}

enum OverlayContent: Codable, Equatable {
    case text(TextOverlayData)
    case qrCode(QROverlayData)
    case timestamp(TimestampOverlayData)
    case image(ImageOverlayData)
    case location(LocationOverlayData)

    var kind: OverlayKind {
        switch self {
        case .text: return .text
        case .qrCode: return .qrCode
        case .timestamp: return .timestamp
        case .image: return .image
        case .location: return .location
        }
    }

    private enum CodingKeys: String, CodingKey {
        case kind
        case text
        case qrCode
        case timestamp
        case image
        case location
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let kind = try container.decode(OverlayKind.self, forKey: .kind)
        switch kind {
        case .text:
            self = .text(try container.decode(TextOverlayData.self, forKey: .text))
        case .qrCode:
            self = .qrCode(try container.decode(QROverlayData.self, forKey: .qrCode))
        case .timestamp:
            self = .timestamp(try container.decode(TimestampOverlayData.self, forKey: .timestamp))
        case .image:
            self = .image(try container.decode(ImageOverlayData.self, forKey: .image))
        case .location:
            self = .location(try container.decode(LocationOverlayData.self, forKey: .location))
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(kind, forKey: .kind)
        switch self {
        case .text(let value):
            try container.encode(value, forKey: .text)
        case .qrCode(let value):
            try container.encode(value, forKey: .qrCode)
        case .timestamp(let value):
            try container.encode(value, forKey: .timestamp)
        case .image(let value):
            try container.encode(value, forKey: .image)
        case .location(let value):
            try container.encode(value, forKey: .location)
        }
    }
}

struct OverlayPlacement: Codable, Equatable {
    var normalizedCenterX: Double
    var normalizedCenterY: Double
    var normalizedWidth: Double
    var normalizedHeight: Double

    static let defaultPlacement = OverlayPlacement(
        normalizedCenterX: 0.5,
        normalizedCenterY: 0.5,
        normalizedWidth: 0.28,
        normalizedHeight: 0.14
    )

    var clamped: OverlayPlacement {
        OverlayPlacement(
            normalizedCenterX: Self.clamp(normalizedCenterX),
            normalizedCenterY: Self.clamp(normalizedCenterY),
            normalizedWidth: max(0.05, min(normalizedWidth, 1.0)),
            normalizedHeight: max(0.05, min(normalizedHeight, 1.0))
        )
    }

    func rect(in size: CGSize) -> CGRect {
        let clamped = self.clamped
        let width = size.width * clamped.normalizedWidth
        let height = size.height * clamped.normalizedHeight
        let center = CGPoint(x: size.width * clamped.normalizedCenterX, y: size.height * clamped.normalizedCenterY)
        return CGRect(x: center.x - width / 2.0, y: center.y - height / 2.0, width: width, height: height)
    }

    private static func clamp(_ value: Double) -> Double {
        max(0.0, min(value, 1.0))
    }
}

struct OverlayColor: Codable, Equatable, Hashable {
    var red: Double
    var green: Double
    var blue: Double
    var alpha: Double

    init(red: Double, green: Double, blue: Double, alpha: Double = 1.0) {
        self.red = Self.clamp(red)
        self.green = Self.clamp(green)
        self.blue = Self.clamp(blue)
        self.alpha = Self.clamp(alpha)
    }

    init(hex: Int, alpha: Double = 1.0) {
        self.init(
            red: Double((hex >> 16) & 0xFF) / 255.0,
            green: Double((hex >> 8) & 0xFF) / 255.0,
            blue: Double(hex & 0xFF) / 255.0,
            alpha: alpha
        )
    }

    var color: Color {
        Color(.sRGB, red: red, green: green, blue: blue, opacity: alpha)
    }

    static let white = OverlayColor(hex: 0xFFFFFF)
    static let black = OverlayColor(hex: 0x000000)
    static let orange = OverlayColor(hex: 0xF58A1F)
    static let green = OverlayColor(hex: 0x26DE6D)
    static let cream = OverlayColor(hex: 0xF3EDE3)
    static let blue = OverlayColor(hex: 0x1F6FEB)
    static let pink = OverlayColor(hex: 0xFF5A8A)
    static let transparent = OverlayColor(red: 0, green: 0, blue: 0, alpha: 0)

    private static func clamp(_ value: Double) -> Double {
        max(0.0, min(value, 1.0))
    }
}

struct TextOverlayData: Codable, Equatable {
    var text: String = "New Text"
    var fontScale: Double = 0.1
    var foregroundColor: OverlayColor = .white
    var backgroundColor: OverlayColor = .transparent
    var textAlignment: OverlayTextAlignment = .center
    var shadowStyle: OverlayShadowStyle = .soft
    var allowsMultipleLines: Bool = true
}

enum OverlayTextAlignment: String, Codable, CaseIterable, Identifiable {
    case leading
    case center
    case trailing

    var id: String { rawValue }
}

enum OverlayShadowStyle: String, Codable, CaseIterable, Identifiable {
    case none
    case soft
    case strong

    var id: String { rawValue }
}

struct QROverlayData: Codable, Equatable {
    var payload: String = "https://github.com/wu-hongjun/InstantLink"
    var correctionLevel: QRErrorCorrectionLevel = .medium
    var foregroundColor: OverlayColor = .black
    var backgroundColor: OverlayColor = .white
    var includesQuietZone: Bool = true
    var showsCaption: Bool = false
    var caption: String = ""
}

enum QRErrorCorrectionLevel: String, Codable, CaseIterable, Identifiable {
    case low
    case medium
    case quartile
    case high

    var id: String { rawValue }

    var coreImageValue: String {
        switch self {
        case .low: return "L"
        case .medium: return "M"
        case .quartile: return "Q"
        case .high: return "H"
        }
    }
}

struct TimestampOverlayData: Codable, Equatable {
    var presetKey: String = "classic"
    var format: TimestampFormat = .ymd
    var showsTime: Bool = true
    var lightBleedEnabled: Bool = false
}

enum TimestampFormat: String, Codable, CaseIterable, Identifiable {
    case ymd
    case mdy
    case dmy

    var id: String { rawValue }
}

struct ImageOverlayData: Codable, Equatable {
    var asset: OverlayImageAsset
    var contentMode: OverlayImageContentMode = .fit
    var cornerRadius: Double = 0.0
    var showsBacking: Bool = false
    var backingColor: OverlayColor = .white
}

enum OverlayImageContentMode: String, Codable, CaseIterable, Identifiable {
    case fit
    case fill

    var id: String { rawValue }
}

struct OverlayImageAsset: Identifiable, Codable, Equatable {
    var id: UUID = UUID()
    var fileName: String?
    var imageData: Data
}

struct LocationOverlayData: Codable, Equatable {
    var source: LocationOverlaySource = .photoMetadata
    var displayStyle: LocationOverlayDisplayStyle = .coordinates
    var coordinate: GeoCoordinate?
    var locationName: String = ""
    var precision: Int = 4
}

enum LocationOverlaySource: String, Codable, CaseIterable, Identifiable {
    case photoMetadata
    case manualCoordinates
    case manualText

    var id: String { rawValue }
}

enum LocationOverlayDisplayStyle: String, Codable, CaseIterable, Identifiable {
    case coordinates
    case name
    case nameAndCoordinates

    var id: String { rawValue }
}

struct GeoCoordinate: Codable, Equatable, Hashable {
    var latitude: Double
    var longitude: Double

    var isValid: Bool {
        (-90.0...90.0).contains(latitude) && (-180.0...180.0).contains(longitude)
    }
}

struct ImageLocationMetadata: Codable, Equatable, Hashable {
    var coordinate: GeoCoordinate
    var altitude: Double?
    var speed: Double?
    var timestamp: Date?
}
