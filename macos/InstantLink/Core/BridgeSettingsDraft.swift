import Foundation

/// Observable draft model for the Bridge Settings tab.
///
/// Owns:
///   * The last-fetched canonical ``BridgeConfig`` (``loaded``).
///   * An editable copy the user mutates (``draft``).
///   * A field-error map populated by client-side validation and / or by
///     the bridge's `config_validation_failed` response.
///   * The Apply lifecycle state (idle / applying / succeeded / failed).
///
/// The view binds typed controls (Picker, Stepper, Toggle, TextField) to
/// nested fields under `draft`. When the user clicks "Apply", the view
/// calls `validate()`, builds a diff, and submits it through the
/// coordinator; on success it calls `load(_:)` again with the bridge's
/// fresh canonical state.
@MainActor
final class BridgeSettingsDraft: ObservableObject {
    /// Apply-button lifecycle. Used by the action bar to swap between
    /// idle / spinner / green-toast / red-error chrome without forcing
    /// the view to track its own boolean flags.
    enum ApplyState: Equatable {
        case idle
        case applying
        case succeeded(at: Date)
        case failed(message: String)

        var isApplying: Bool {
            if case .applying = self { return true }
            return false
        }
    }

    @Published private(set) var loaded: BridgeConfig?
    @Published var draft: BridgeConfig?
    @Published private(set) var fieldErrors: [BridgeConfigField: String] = [:]
    @Published private(set) var applyState: ApplyState = .idle

    init(loaded: BridgeConfig? = nil) {
        self.loaded = loaded
        self.draft = loaded
    }

    // MARK: - Lifecycle

    /// Replace both ``loaded`` and ``draft`` with a freshly-fetched config
    /// and clear errors / apply state.
    func load(_ config: BridgeConfig) {
        loaded = config
        draft = config
        fieldErrors = [:]
        applyState = .idle
    }

    /// Revert in-memory edits back to the last loaded canonical state.
    func revert() {
        draft = loaded
        fieldErrors = [:]
        if case .failed = applyState {
            applyState = .idle
        }
    }

    /// True when the draft diverges from the last loaded canonical state.
    var isDirty: Bool {
        guard let loaded, let draft else { return false }
        return loaded != draft
    }

    // MARK: - Apply state transitions

    func beginApplying() {
        applyState = .applying
    }

    func recordApplySuccess(_ config: BridgeConfig, at date: Date = Date()) {
        load(config)
        applyState = .succeeded(at: date)
    }

    func recordApplyFailure(message: String, fieldErrors: [String: String] = [:]) {
        let mapped = Self.mapFieldErrors(fieldErrors)
        self.fieldErrors = mapped
        applyState = .failed(message: message)
    }

    // MARK: - Validation + diff

    /// Run client-side validation against ``draft``. Populates
    /// ``fieldErrors`` and returns ``true`` when no errors were raised.
    @discardableResult
    func validate() -> Bool {
        guard let draft else {
            fieldErrors = [:]
            return true
        }
        var errors: [BridgeConfigField: String] = [:]
        if draft.printer.quality < 1 || draft.printer.quality > 100 {
            errors[.printerJPEGQuality] = "JPEG quality must be between 1 and 100."
        }
        if !draft.printer.keepaliveIntervalSeconds.isFinite || draft.printer.keepaliveIntervalSeconds <= 0 {
            errors[.printerKeepaliveInterval] = "Keepalive interval must be greater than 0."
        }
        if !draft.printer.searchIntervalSeconds.isFinite || draft.printer.searchIntervalSeconds <= 0 {
            errors[.printerSearchInterval] = "Search interval must be greater than 0."
        }
        if let delay = draft.workflow.autoPrintDelaySeconds, delay != 0, delay != 5 {
            errors[.workflowAutoPrintDelay] = "Auto-print delay must be 0 s, 5 s, or Off."
        }
        if draft.power.idlePoweroffEnabled {
            if !draft.power.idlePoweroffAfterSeconds.isFinite || draft.power.idlePoweroffAfterSeconds <= 0 {
                errors[.powerIdlePoweroffAfter] = "Idle poweroff timer must be greater than 0."
            }
        }
        if draft.ftp.username.trimmingCharacters(in: .whitespaces).isEmpty {
            errors[.ftpUsername] = "FTP username is required."
        }
        if !BridgeAdjustmentsConfig.allPresetNames.contains(draft.adjustments.preset) {
            errors[.adjustmentsPreset] = "Unknown preset."
        }
        let signedAxes: [(BridgeConfigField, Int)] = [
            (.adjustmentsSaturation, draft.adjustments.saturation),
            (.adjustmentsExposure, draft.adjustments.exposure),
            (.adjustmentsSharpness, draft.adjustments.sharpness),
            (.adjustmentsHue, draft.adjustments.hue),
        ]
        for (field, value) in signedAxes {
            if value < -100 || value > 100 {
                errors[field] = "Must be between -100 and +100"
            }
        }
        if draft.adjustments.vignette < 0 || draft.adjustments.vignette > 100 {
            errors[.adjustmentsVignette] = "Must be between 0 and 100"
        }
        fieldErrors = errors
        return errors.isEmpty
    }

    /// Build the JSON-encodable diff payload to send to `PUT /v1/config`.
    ///
    /// Returns a dictionary shaped ``[section: [field: value]]``. Sections
    /// whose fields are all unchanged are omitted; unchanged fields within
    /// a changed section are also omitted. The result is intentionally a
    /// plain JSON-compatible value tree so the transport can encode it
    /// with ``JSONSerialization``.
    func diff() -> [String: Any] {
        guard let loaded, let draft else { return [:] }
        var payload: [String: Any] = [:]
        var ftp: [String: Any] = [:]
        if loaded.ftp.mode != draft.ftp.mode {
            ftp["mode"] = draft.ftp.mode.rawValue
        }
        if loaded.ftp.username != draft.ftp.username {
            ftp["username"] = draft.ftp.username
        }
        if let pending = pendingPassword, !pending.isEmpty {
            ftp["password"] = pending
        }
        if !ftp.isEmpty {
            payload["ftp"] = ftp
        }

        var printer: [String: Any] = [:]
        if loaded.printer.model != draft.printer.model {
            printer["model"] = draft.printer.model
        }
        if loaded.printer.fit != draft.printer.fit {
            printer["fit"] = draft.printer.fit
        }
        if loaded.printer.quality != draft.printer.quality {
            printer["quality"] = draft.printer.quality
        }
        if loaded.printer.keepaliveIntervalSeconds != draft.printer.keepaliveIntervalSeconds {
            printer["keepalive_interval_s"] = draft.printer.keepaliveIntervalSeconds
        }
        if loaded.printer.searchIntervalSeconds != draft.printer.searchIntervalSeconds {
            printer["search_interval_s"] = draft.printer.searchIntervalSeconds
        }
        if !printer.isEmpty {
            payload["printer"] = printer
        }

        var workflow: [String: Any] = [:]
        if loaded.workflow.autoPrintDelaySeconds != draft.workflow.autoPrintDelaySeconds {
            if let value = draft.workflow.autoPrintDelaySeconds {
                workflow["auto_print_delay_s"] = value
            } else {
                workflow["auto_print_delay_s"] = "off"
            }
        }
        if loaded.workflow.allowPrintWithoutFilm != draft.workflow.allowPrintWithoutFilm {
            workflow["allow_print_without_film"] = draft.workflow.allowPrintWithoutFilm
        }
        if !workflow.isEmpty {
            payload["workflow"] = workflow
        }

        var power: [String: Any] = [:]
        if loaded.power.idlePoweroffEnabled != draft.power.idlePoweroffEnabled {
            power["idle_poweroff_enabled"] = draft.power.idlePoweroffEnabled
        }
        if loaded.power.idlePoweroffAfterSeconds != draft.power.idlePoweroffAfterSeconds {
            power["idle_poweroff_after_s"] = draft.power.idlePoweroffAfterSeconds
        }
        if !power.isEmpty {
            payload["power"] = power
        }

        var ui: [String: Any] = [:]
        if loaded.ui.appearance != draft.ui.appearance {
            ui["appearance"] = draft.ui.appearance.rawValue
        }
        if loaded.ui.fontSize != draft.ui.fontSize {
            ui["font_size"] = draft.ui.fontSize.rawValue
        }
        if loaded.ui.language != draft.ui.language {
            ui["language"] = draft.ui.language.rawValue
        }
        if !ui.isEmpty {
            payload["ui"] = ui
        }

        var adjustments: [String: Any] = [:]
        if loaded.adjustments.preset != draft.adjustments.preset {
            adjustments["preset"] = draft.adjustments.preset
        }
        if loaded.adjustments.saturation != draft.adjustments.saturation {
            adjustments["saturation"] = draft.adjustments.saturation
        }
        if loaded.adjustments.exposure != draft.adjustments.exposure {
            adjustments["exposure"] = draft.adjustments.exposure
        }
        if loaded.adjustments.sharpness != draft.adjustments.sharpness {
            adjustments["sharpness"] = draft.adjustments.sharpness
        }
        if loaded.adjustments.hue != draft.adjustments.hue {
            adjustments["hue"] = draft.adjustments.hue
        }
        if loaded.adjustments.vignette != draft.adjustments.vignette {
            adjustments["vignette"] = draft.adjustments.vignette
        }
        if loaded.adjustments.datestamp != draft.adjustments.datestamp {
            adjustments["datestamp"] = draft.adjustments.datestamp
        }
        if loaded.adjustments.datestampFormat != draft.adjustments.datestampFormat {
            adjustments["datestamp_format"] = draft.adjustments.datestampFormat.rawValue
        }
        if loaded.adjustments.watermark != draft.adjustments.watermark {
            adjustments["watermark"] = draft.adjustments.watermark
        }
        if loaded.adjustments.watermarkText != draft.adjustments.watermarkText {
            adjustments["watermark_text"] = draft.adjustments.watermarkText
        }
        if !adjustments.isEmpty {
            payload["adjustments"] = adjustments
        }

        return payload
    }

    // MARK: - Cleartext-only fields (not held inside `draft`)

    /// Pending FTP password. The cleartext value lives outside ``draft``
    /// because the bridge never returns it; we only ship it on Apply when
    /// the user explicitly typed a new value.
    @Published var pendingPassword: String?

    var isDirtyIncludingPassword: Bool {
        if let pending = pendingPassword, !pending.isEmpty { return true }
        return isDirty
    }

    // MARK: - Helpers

    private static func mapFieldErrors(_ raw: [String: String]) -> [BridgeConfigField: String] {
        var mapped: [BridgeConfigField: String] = [:]
        for (key, value) in raw {
            if let field = BridgeConfigField(rawValue: key) {
                mapped[field] = value
            }
        }
        return mapped
    }
}
