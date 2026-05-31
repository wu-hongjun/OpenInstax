import Foundation

// MARK: - Top-level config

/// Mac mirror of the bridge's ``BridgeConfig`` dataclass tree, sent over
/// `GET /v1/config` and round-tripped by `PUT /v1/config`. The shape is
/// owned by the bridge; the macOS app decodes what the bridge sanitizes
/// and produces a partial diff back when the user clicks Apply.
struct BridgeConfig: Codable, Equatable, Hashable, Sendable {
    var ftp: BridgeFTPConfig
    var printer: BridgePrinterConfig
    var workflow: BridgeWorkflowConfig
    var power: BridgePowerConfig
    var ui: BridgeUIConfig
    var adjustments: BridgeAdjustmentsConfig

    enum CodingKeys: String, CodingKey {
        case ftp
        case printer
        case workflow
        case power
        case ui
        case adjustments
    }

    static let defaults = BridgeConfig(
        ftp: .defaults,
        printer: .defaults,
        workflow: .defaults,
        power: .defaults,
        ui: .defaults,
        adjustments: .defaults
    )
}

// MARK: - Child structs

struct BridgeFTPConfig: Codable, Equatable, Hashable, Sendable {
    /// Configured FTP receive mode.
    var mode: BridgeFTPReceiveMode
    /// FTP username the bridge advertises to the camera.
    var username: String
    /// `true` when the bridge has a non-default password persisted. The
    /// cleartext password never leaves the bridge; the Mac uses this flag
    /// to render a "set" / "unset" pill.
    var passwordSet: Bool

    enum CodingKeys: String, CodingKey {
        case mode
        case username
        case passwordSet = "password_set"
    }

    static let defaults = BridgeFTPConfig(
        mode: .hotspot,
        username: "ib",
        passwordSet: false
    )
}

struct BridgePrinterConfig: Codable, Equatable, Hashable, Sendable {
    /// Selected printer model. ``"auto"`` means "use the first compatible
    /// printer discovered on BLE".
    var model: String
    /// Image fit mode the bridge applies before sending to the printer.
    var fit: String
    /// JPEG re-encode quality in [1, 100].
    var quality: Int
    /// BLE keepalive interval in seconds.
    var keepaliveIntervalSeconds: Double
    /// BLE search interval in seconds.
    var searchIntervalSeconds: Double

    enum CodingKeys: String, CodingKey {
        case model
        case fit
        case quality
        case keepaliveIntervalSeconds = "keepalive_interval_s"
        case searchIntervalSeconds = "search_interval_s"
    }

    static let defaults = BridgePrinterConfig(
        model: "auto",
        fit: "auto",
        quality: 100,
        keepaliveIntervalSeconds: 10,
        searchIntervalSeconds: 5
    )
}

struct BridgeWorkflowConfig: Codable, Equatable, Hashable, Sendable {
    /// Auto-print delay seconds. ``nil`` means "off" (decoded from the
    /// bridge's `"off"` sentinel). Valid values are `nil`, ``0`` and ``5``.
    var autoPrintDelaySeconds: Double?
    var allowPrintWithoutFilm: Bool

    enum CodingKeys: String, CodingKey {
        case autoPrintDelaySeconds = "auto_print_delay_s"
        case allowPrintWithoutFilm = "allow_print_without_film"
    }

    static let defaults = BridgeWorkflowConfig(
        autoPrintDelaySeconds: 5,
        allowPrintWithoutFilm: false
    )

    init(autoPrintDelaySeconds: Double?, allowPrintWithoutFilm: Bool) {
        self.autoPrintDelaySeconds = autoPrintDelaySeconds
        self.allowPrintWithoutFilm = allowPrintWithoutFilm
    }

    init(from decoder: any Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        if let stringValue = try? container.decode(String.self, forKey: .autoPrintDelaySeconds) {
            let normalized = stringValue.lowercased()
            if normalized == "off" || normalized == "none" || normalized == "false" {
                self.autoPrintDelaySeconds = nil
            } else if let parsed = Double(stringValue) {
                self.autoPrintDelaySeconds = parsed
            } else {
                throw DecodingError.dataCorruptedError(
                    forKey: .autoPrintDelaySeconds,
                    in: container,
                    debugDescription: "auto_print_delay_s must be a number or 'off'"
                )
            }
        } else if let doubleValue = try container.decodeIfPresent(Double.self, forKey: .autoPrintDelaySeconds) {
            self.autoPrintDelaySeconds = doubleValue
        } else {
            self.autoPrintDelaySeconds = nil
        }
        self.allowPrintWithoutFilm = try container.decodeIfPresent(
            Bool.self,
            forKey: .allowPrintWithoutFilm
        ) ?? false
    }

    func encode(to encoder: any Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        if let value = autoPrintDelaySeconds {
            try container.encode(value, forKey: .autoPrintDelaySeconds)
        } else {
            try container.encode("off", forKey: .autoPrintDelaySeconds)
        }
        try container.encode(allowPrintWithoutFilm, forKey: .allowPrintWithoutFilm)
    }
}

struct BridgePowerConfig: Codable, Equatable, Hashable, Sendable {
    /// Power hardware backend identifier (read-only from the Mac).
    var backend: BridgePowerBackend
    var idlePoweroffEnabled: Bool
    var idlePoweroffAfterSeconds: Double

    enum CodingKeys: String, CodingKey {
        case backend
        case idlePoweroffEnabled = "idle_poweroff_enabled"
        case idlePoweroffAfterSeconds = "idle_poweroff_after_s"
    }

    static let defaults = BridgePowerConfig(
        backend: .x306,
        idlePoweroffEnabled: false,
        idlePoweroffAfterSeconds: 7200
    )
}

struct BridgeUIConfig: Codable, Equatable, Hashable, Sendable {
    var appearance: BridgeUIAppearance
    var fontSize: BridgeFontSize
    var language: BridgeUILanguage

    enum CodingKeys: String, CodingKey {
        case appearance
        case fontSize = "font_size"
        case language
    }

    static let defaults = BridgeUIConfig(
        appearance: .light,
        fontSize: .medium,
        language: .english
    )
}

struct BridgeFirmwareUpdateConfig: Codable, Equatable, Hashable, Sendable {
    /// Configured firmware trusted-key list. Read-only from the macOS app
    /// — the management surface does not yet expose key management.
    var trustedPublicKeyCount: Int

    enum CodingKeys: String, CodingKey {
        case trustedPublicKeyCount = "trusted_public_key_count"
    }
}

struct BridgeAdjustmentsConfig: Codable, Equatable, Hashable, Sendable {
    /// Active preset name. Must be one of the built-in names or a
    /// ``CustomN`` slot. See ``BridgeAdjustmentsConfig/builtinPresetNames``
    /// and ``BridgeAdjustmentsConfig/customPresetNames``.
    var preset: String
    /// Colour intensity. Integer in `[-100, +100]`. `0` is identity.
    var saturation: Int
    /// Brightness. Integer in `[-100, +100]`. `0` is identity.
    var exposure: Int
    /// Edge contrast. Integer in `[-100, +100]`. `0` is identity.
    var sharpness: Int
    /// Hue rotation. Integer in `[-100, +100]`. `0` is identity.
    var hue: Int
    /// Corner-darkening strength. Integer in `[0, 100]`. `0` disables.
    var vignette: Int
    /// Render EXIF date overlay when `true`.
    var datestamp: Bool
    var datestampFormat: BridgeDatestampFormat
    /// Render the watermark overlay when `true`.
    var watermark: Bool
    var watermarkText: String

    enum CodingKeys: String, CodingKey {
        case preset
        case saturation
        case exposure
        case sharpness
        case hue
        case vignette
        case datestamp
        case datestampFormat = "datestamp_format"
        case watermark
        case watermarkText = "watermark_text"
    }

    static let defaults = BridgeAdjustmentsConfig(
        preset: "Default",
        saturation: 0,
        exposure: 0,
        sharpness: 0,
        hue: 0,
        vignette: 0,
        datestamp: false,
        datestampFormat: .quartzDate,
        watermark: false,
        watermarkText: ""
    )

    /// Built-in preset names exposed on the bridge LCD. Order matches the
    /// LCD picker.
    static let builtinPresetNames: [String] = [
        "Default",
        "Vivid",
        "Soft",
        "Black & white",
        "Instax Film",
    ]

    /// Six user-saveable preset slot names (``Custom1`` .. ``Custom6``).
    static let customPresetNames: [String] = (1...6).map { "Custom\($0)" }

    /// Full set of accepted preset names (built-ins + custom slots).
    static var allPresetNames: [String] {
        builtinPresetNames + customPresetNames
    }
}

// MARK: - Enums (raw values mirror the bridge's StrEnum values exactly)

enum BridgeFTPReceiveMode: String, Codable, CaseIterable, Sendable {
    case auto
    case hotspot
    case peer
    case wired
}

enum BridgePowerBackend: String, Codable, CaseIterable, Sendable {
    case x306
    case pisugar
    case none = "none"
}

enum BridgeFontSize: String, Codable, CaseIterable, Sendable {
    case small
    case medium
    case large
}

enum BridgeDatestampFormat: String, Codable, CaseIterable, Sendable {
    case quartzDate = "quartz_date"
    case olympus
    case contax
    case modern
    case labPrint = "lab_print"
}

enum BridgeUIAppearance: String, Codable, CaseIterable, Sendable {
    case light
    case dark
    case auto
}

enum BridgeUILanguage: String, Codable, CaseIterable, Sendable {
    case english = "en"
    case chineseSimplified = "zh-Hans"
}

// MARK: - Field identifiers + validation errors

/// Identifies one editable field in the Bridge settings tab. Used as the
/// key in `BridgeSettingsDraft.fieldErrors` and the focus identifier for
/// inline error rendering.
enum BridgeConfigField: String, CaseIterable, Hashable, Sendable {
    case printerModel = "printer.model"
    case printerFit = "printer.fit"
    case printerJPEGQuality = "printer.quality"
    case printerKeepaliveInterval = "printer.keepalive_interval_s"
    case printerSearchInterval = "printer.search_interval_s"

    case ftpMode = "ftp.mode"
    case ftpUsername = "ftp.username"
    case ftpPassword = "ftp.password"

    case workflowAutoPrintDelay = "workflow.auto_print_delay_s"
    case workflowAllowPrintWithoutFilm = "workflow.allow_print_without_film"

    case powerIdlePoweroffEnabled = "power.idle_poweroff_enabled"
    case powerIdlePoweroffAfter = "power.idle_poweroff_after_s"

    case uiAppearance = "ui.appearance"
    case uiFontSize = "ui.font_size"
    case uiLanguage = "ui.language"

    case adjustmentsPreset = "adjustments.preset"
    case adjustmentsSaturation = "adjustments.saturation"
    case adjustmentsExposure = "adjustments.exposure"
    case adjustmentsSharpness = "adjustments.sharpness"
    case adjustmentsHue = "adjustments.hue"
    case adjustmentsVignette = "adjustments.vignette"
    case adjustmentsDatestamp = "adjustments.datestamp"
    case adjustmentsDatestampFormat = "adjustments.datestamp_format"
    case adjustmentsWatermark = "adjustments.watermark"
    case adjustmentsWatermarkText = "adjustments.watermark_text"

    /// Section path used when building the PUT diff payload (e.g. "ftp").
    var section: String {
        rawValue.split(separator: ".", maxSplits: 1, omittingEmptySubsequences: false)
            .first.map(String.init) ?? rawValue
    }

    /// Field portion of the dotted path (e.g. "mode").
    var key: String {
        rawValue.split(separator: ".", maxSplits: 1, omittingEmptySubsequences: false)
            .dropFirst().first.map(String.init) ?? rawValue
    }
}

/// Server-side validation error surfaced by `PUT /v1/config` when one or
/// more fields are out of range. The bridge embeds a `field_errors` map
/// under `details`; this error mirrors that into a typed Swift value the
/// settings UI can index per-field.
struct BridgeConfigValidationError: Error, Equatable {
    /// Map keyed by ``"section.field"`` (matching ``BridgeConfigField.rawValue``)
    /// to a human-readable message safe to render inline.
    let fieldErrors: [String: String]
    /// Optional top-level message surfaced by the bridge envelope.
    let message: String

    func error(for field: BridgeConfigField) -> String? {
        fieldErrors[field.rawValue]
    }
}
