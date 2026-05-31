import SwiftUI

/// Bridge Settings tab.
///
/// Owns its own ``BridgeSettingsDraft`` and load lifecycle. On appear
/// it fetches `GET /v1/config` through the coordinator; the user edits
/// typed controls bound to the draft; the action bar at the bottom runs
/// `validate()` + `applyConfig(diff:)` and refreshes the canonical state
/// on success.
struct BridgeSettingsView: View {
    @ObservedObject var coordinator: BridgeControlCoordinator
    @StateObject private var draft = BridgeSettingsDraft()
    @State private var loadState: LoadState = .idle

    private enum LoadState: Equatable {
        case idle
        case loading
        case loaded
        case failed(message: String)
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                switch loadState {
                case .idle, .loading:
                    loadingCard
                case .failed(let message):
                    failureCard(message: message)
                case .loaded:
                    if draft.draft != nil {
                        printerCard
                        networkCard
                        autoPrintCard
                        powerCard
                        uiCard
                        adjustmentsCard
                        diffPreview
                    }
                }
                Spacer(minLength: 6)
            }
            .padding(16)
        }
        .safeAreaInset(edge: .bottom) {
            if case .loaded = loadState {
                actionBar
                    .padding(.horizontal, 16)
                    .padding(.vertical, 10)
                    .background(.thinMaterial)
            }
        }
        .task {
            await loadIfNeeded()
        }
        // Re-fetch when the user becomes paired (e.g. wizard completes
        // while the Settings tab is already showing).
        .onChange(of: coordinator.snapshot.pairing) {
            Task { await loadIfNeeded(force: true) }
        }
    }

    // MARK: - Load lifecycle

    private func loadIfNeeded(force: Bool = false) async {
        if !force, case .loaded = loadState { return }
        guard case .paired = coordinator.snapshot.pairing else {
            loadState = .failed(message: L("Pair this Mac with the Bridge to edit settings."))
            return
        }
        loadState = .loading
        do {
            let config = try await coordinator.fetchConfig()
            draft.load(config)
            loadState = .loaded
        } catch let error as BridgeAPIError {
            loadState = .failed(message: error.payload.message)
        } catch {
            loadState = .failed(message: L("Management service unavailable"))
        }
    }

    // MARK: - Status / error cards

    private var loadingCard: some View {
        BridgeSettingsSection(title: L("Settings")) {
            HStack(spacing: 10) {
                ProgressView().controlSize(.small)
                Text(L("Loading settings from Bridge…"))
                    .font(.callout)
                    .foregroundColor(.secondary)
            }
        }
    }

    private func failureCard(message: String) -> some View {
        BridgeSettingsSection(title: L("Settings unavailable")) {
            VStack(alignment: .leading, spacing: 8) {
                Text(message)
                    .font(.callout)
                    .foregroundColor(.secondary)
                Button(L("Retry")) {
                    Task { await loadIfNeeded(force: true) }
                }
                .buttonStyle(.borderedProminent)
            }
        }
    }

    // MARK: - Cards (only built when draft is loaded)

    private var printerCard: some View {
        BridgeSettingsSection(title: L("Printer")) {
            VStack(alignment: .leading, spacing: 10) {
                pickerRow(
                    label: L("Model"),
                    selection: binding(\.printer.model, default: "auto"),
                    options: [
                        ("auto", L("Auto-detect")),
                        ("mini", L("Mini")),
                        ("mini_link3", L("Mini Link 3")),
                        ("square", L("Square")),
                        ("wide", L("Wide")),
                    ]
                )
                pickerRow(
                    label: L("Fit mode"),
                    selection: binding(\.printer.fit, default: "auto"),
                    options: [
                        ("auto", L("Auto")),
                        ("crop", L("Crop")),
                        ("contain", L("Contain")),
                        ("stretch", L("Stretch")),
                    ]
                )
                stepperRow(
                    label: L("JPEG quality"),
                    value: bindingInt(\.printer.quality, default: 100),
                    range: 1...100,
                    step: 5,
                    suffix: "%"
                )
                pickerRow(
                    label: L("Keepalive interval"),
                    selection: bindingDouble(\.printer.keepaliveIntervalSeconds, default: 10),
                    options: [
                        (5, "5 s"),
                        (10, "10 s"),
                        (30, "30 s"),
                        (60, "60 s"),
                    ]
                )
                pickerRow(
                    label: L("Search interval"),
                    selection: bindingDouble(\.printer.searchIntervalSeconds, default: 5),
                    options: [
                        (5, "5 s"),
                        (10, "10 s"),
                        (30, "30 s"),
                        (60, "60 s"),
                    ]
                )
            }
        } footer: {
            errorFooter(for: [
                .printerJPEGQuality,
                .printerKeepaliveInterval,
                .printerSearchInterval,
            ])
        }
    }

    private var networkCard: some View {
        BridgeSettingsSection(title: L("Network")) {
            VStack(alignment: .leading, spacing: 10) {
                pickerRow(
                    label: L("FTP receive mode"),
                    selection: bindingEnum(
                        \.ftp.mode,
                        default: .hotspot
                    ),
                    options: BridgeFTPReceiveMode.allCases.map { ($0, modeLabel($0)) }
                )
                textFieldRow(
                    label: L("FTP username"),
                    text: binding(\.ftp.username, default: "ib")
                )
                passwordRow
            }
        } footer: {
            errorFooter(for: [.ftpUsername, .ftpPassword])
        }
    }

    private var passwordRow: some View {
        HStack(spacing: 10) {
            Text(L("FTP password"))
                .font(.callout)
                .frame(width: 160, alignment: .leading)
            SecureField(
                draft.loaded?.ftp.passwordSet == true ? L("Set — type to replace") : L("Not set"),
                text: Binding(
                    get: { draft.pendingPassword ?? "" },
                    set: { draft.pendingPassword = $0 }
                )
            )
            .textFieldStyle(.roundedBorder)
            Spacer()
        }
    }

    private var autoPrintCard: some View {
        BridgeSettingsSection(title: L("Auto print")) {
            VStack(alignment: .leading, spacing: 10) {
                pickerRow(
                    label: L("Auto-print delay"),
                    selection: Binding<Int>(
                        get: {
                            guard let value = draft.draft?.workflow.autoPrintDelaySeconds else { return -1 }
                            return Int(value)
                        },
                        set: { newValue in
                            updateDraft { config in
                                if newValue < 0 {
                                    config.workflow.autoPrintDelaySeconds = nil
                                } else {
                                    config.workflow.autoPrintDelaySeconds = Double(newValue)
                                }
                            }
                        }
                    ),
                    options: [
                        (0, L("Print immediately")),
                        (5, L("5-second preview")),
                        (-1, L("Off (wait for confirm)")),
                    ]
                )
                Toggle(
                    L("Allow print without film"),
                    isOn: Binding(
                        get: { draft.draft?.workflow.allowPrintWithoutFilm ?? false },
                        set: { newValue in
                            updateDraft { config in
                                config.workflow.allowPrintWithoutFilm = newValue
                            }
                        }
                    )
                )
            }
        } footer: {
            errorFooter(for: [.workflowAutoPrintDelay])
        }
    }

    private var powerCard: some View {
        BridgeSettingsSection(title: L("Power")) {
            VStack(alignment: .leading, spacing: 10) {
                Toggle(
                    L("Idle poweroff"),
                    isOn: Binding(
                        get: { draft.draft?.power.idlePoweroffEnabled ?? false },
                        set: { newValue in
                            updateDraft { config in
                                config.power.idlePoweroffEnabled = newValue
                            }
                        }
                    )
                )
                if draft.draft?.power.idlePoweroffEnabled == true {
                    stepperRow(
                        label: L("Power off after"),
                        value: bindingDouble(\.power.idlePoweroffAfterSeconds, default: 7200),
                        range: 3600...86400,
                        step: 1800,
                        suffix: L("seconds")
                    )
                }
            }
        } footer: {
            errorFooter(for: [.powerIdlePoweroffAfter])
        }
    }

    private var uiCard: some View {
        BridgeSettingsSection(title: L("Bridge LCD")) {
            VStack(alignment: .leading, spacing: 10) {
                pickerRow(
                    label: L("Appearance"),
                    selection: bindingEnum(\.ui.appearance, default: .light),
                    options: [
                        (.light, L("Light")),
                        (.dark, L("Dark")),
                        (.auto, L("Auto")),
                    ]
                )
                pickerRow(
                    label: L("Font size"),
                    selection: bindingEnum(\.ui.fontSize, default: .medium),
                    options: [
                        (.small, L("Small")),
                        (.medium, L("Medium")),
                        (.large, L("Large")),
                    ]
                )
                pickerRow(
                    label: L("Language"),
                    selection: bindingEnum(\.ui.language, default: .english),
                    options: [
                        (.english, L("English")),
                        (.chineseSimplified, L("Chinese (Simplified)")),
                    ]
                )
            }
        } footer: {
            errorFooter(for: [.uiAppearance, .uiFontSize, .uiLanguage])
        }
    }

    private var adjustmentsCard: some View {
        BridgeSettingsSection(title: L("Image adjustments")) {
            VStack(alignment: .leading, spacing: 10) {
                pickerRow(
                    label: L("Preset"),
                    selection: binding(\.adjustments.preset, default: "Default"),
                    options: BridgeAdjustmentsConfig.allPresetNames.map { ($0, presetLabel($0)) }
                )
                sliderRow(
                    label: L("Saturation"),
                    value: bindingInt(\.adjustments.saturation, default: 0),
                    in: -100...100,
                    style: .signed
                )
                sliderRow(
                    label: L("Exposure"),
                    value: bindingInt(\.adjustments.exposure, default: 0),
                    in: -100...100,
                    style: .signed
                )
                sliderRow(
                    label: L("Sharpness"),
                    value: bindingInt(\.adjustments.sharpness, default: 0),
                    in: -100...100,
                    style: .signed
                )
                sliderRow(
                    label: L("Hue"),
                    value: bindingInt(\.adjustments.hue, default: 0),
                    in: -100...100,
                    style: .signed
                )
                sliderRow(
                    label: L("Vignette"),
                    value: bindingInt(\.adjustments.vignette, default: 0),
                    in: 0...100,
                    style: .unsigned
                )
                Toggle(
                    L("Datestamp"),
                    isOn: Binding(
                        get: { draft.draft?.adjustments.datestamp ?? false },
                        set: { newValue in
                            updateDraft { config in
                                config.adjustments.datestamp = newValue
                            }
                        }
                    )
                )
                pickerRow(
                    label: L("Datestamp format"),
                    selection: bindingEnum(\.adjustments.datestampFormat, default: .quartzDate),
                    options: [
                        (.quartzDate, L("Quartz Date")),
                        (.olympus, L("Olympus")),
                        (.contax, L("Contax")),
                        (.modern, L("Modern")),
                        (.labPrint, L("Lab Print")),
                    ]
                )
                .disabled(!(draft.draft?.adjustments.datestamp ?? false))
                Toggle(
                    L("Watermark"),
                    isOn: Binding(
                        get: { draft.draft?.adjustments.watermark ?? false },
                        set: { newValue in
                            updateDraft { config in
                                config.adjustments.watermark = newValue
                            }
                        }
                    )
                )
                textFieldRow(
                    label: L("Watermark text"),
                    text: binding(\.adjustments.watermarkText, default: "")
                )
                .disabled(!(draft.draft?.adjustments.watermark ?? false))
            }
        } footer: {
            errorFooter(for: [
                .adjustmentsPreset,
                .adjustmentsSaturation,
                .adjustmentsExposure,
                .adjustmentsSharpness,
                .adjustmentsHue,
                .adjustmentsVignette,
                .adjustmentsDatestamp,
                .adjustmentsDatestampFormat,
                .adjustmentsWatermark,
                .adjustmentsWatermarkText,
            ])
        }
    }

    /// Render label for a preset name. Built-ins pass through; ``CustomN``
    /// slot names are localised as ``"Custom N"``.
    private func presetLabel(_ name: String) -> String {
        if name.hasPrefix("Custom"), let n = Int(name.dropFirst("Custom".count)) {
            return "\(L("Custom")) \(n)"
        }
        return L(name)
    }

    private enum SliderValueStyle {
        case signed
        case unsigned
    }

    private func sliderRow(
        label: String,
        value: Binding<Int>,
        in range: ClosedRange<Int>,
        style: SliderValueStyle
    ) -> some View {
        let doubleBinding = Binding<Double>(
            get: { Double(value.wrappedValue) },
            set: { value.wrappedValue = Int($0.rounded()) }
        )
        let badge: String
        switch style {
        case .signed:
            if value.wrappedValue > 0 {
                badge = "+\(value.wrappedValue)"
            } else {
                badge = "\(value.wrappedValue)"
            }
        case .unsigned:
            badge = "\(value.wrappedValue)"
        }
        return HStack(spacing: 10) {
            Text(label)
                .font(.callout)
                .frame(width: 160, alignment: .leading)
            Slider(
                value: doubleBinding,
                in: Double(range.lowerBound)...Double(range.upperBound),
                step: 1
            )
            Text(badge)
                .font(.callout.monospacedDigit())
                .foregroundColor(.secondary)
                .frame(width: 44, alignment: .trailing)
        }
    }

    @ViewBuilder
    private var diffPreview: some View {
        if draft.isDirtyIncludingPassword {
            BridgeSettingsSection(title: L("Pending changes")) {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(diffRows, id: \.field) { row in
                        HStack(alignment: .firstTextBaseline) {
                            Text(row.field)
                                .font(.caption)
                                .foregroundColor(.secondary)
                                .frame(width: 220, alignment: .leading)
                            Text(row.before)
                                .font(.caption)
                                .foregroundColor(.secondary)
                            Image(systemName: "arrow.right")
                                .font(.caption2)
                            Text(row.after)
                                .font(.caption.weight(.semibold))
                        }
                    }
                }
            }
        }
    }

    private var actionBar: some View {
        HStack(spacing: 12) {
            statusLabel
            Spacer()
            Button(L("Revert")) {
                draft.revert()
            }
            .disabled(!draft.isDirtyIncludingPassword || draft.applyState.isApplying)
            Button(L("Apply to Bridge")) {
                Task { await applyChanges() }
            }
            .keyboardShortcut(.defaultAction)
            .buttonStyle(.borderedProminent)
            .disabled(!draft.isDirtyIncludingPassword || draft.applyState.isApplying)
        }
    }

    private var statusLabel: some View {
        Group {
            switch draft.applyState {
            case .idle:
                EmptyView()
            case .applying:
                HStack(spacing: 6) {
                    ProgressView().controlSize(.small)
                    Text(L("Applying…")).font(.caption).foregroundColor(.secondary)
                }
            case .succeeded:
                Text(L("Bridge settings updated"))
                    .font(.caption)
                    .foregroundColor(.green)
            case .failed(let message):
                Text(message)
                    .font(.caption)
                    .foregroundColor(.red)
                    .lineLimit(2)
            }
        }
    }

    // MARK: - Apply

    private func applyChanges() async {
        guard draft.validate() else { return }
        var diff = draft.diff()
        if let password = draft.pendingPassword, !password.isEmpty {
            var ftp = (diff["ftp"] as? [String: Any]) ?? [:]
            ftp["password"] = password
            diff["ftp"] = ftp
        }
        if diff.isEmpty { return }

        draft.beginApplying()
        do {
            let fresh = try await coordinator.applyConfig(diff: diff)
            draft.recordApplySuccess(fresh)
            draft.pendingPassword = nil
        } catch let error as BridgeConfigValidationError {
            draft.recordApplyFailure(
                message: error.message,
                fieldErrors: error.fieldErrors
            )
        } catch let error as BridgeAPIError {
            draft.recordApplyFailure(message: error.payload.message)
        } catch {
            draft.recordApplyFailure(message: L("Management service unavailable"))
        }
    }

    // MARK: - Bindings + helpers

    private func updateDraft(_ mutate: (inout BridgeConfig) -> Void) {
        guard var current = draft.draft else { return }
        mutate(&current)
        draft.draft = current
    }

    private func binding<T: Equatable>(
        _ keyPath: WritableKeyPath<BridgeConfig, T>,
        default fallback: T
    ) -> Binding<T> {
        Binding(
            get: { draft.draft?[keyPath: keyPath] ?? fallback },
            set: { newValue in
                updateDraft { config in
                    config[keyPath: keyPath] = newValue
                }
            }
        )
    }

    private func bindingInt(
        _ keyPath: WritableKeyPath<BridgeConfig, Int>,
        default fallback: Int
    ) -> Binding<Int> {
        binding(keyPath, default: fallback)
    }

    private func bindingDouble(
        _ keyPath: WritableKeyPath<BridgeConfig, Double>,
        default fallback: Double
    ) -> Binding<Double> {
        binding(keyPath, default: fallback)
    }

    private func bindingEnum<T: Equatable>(
        _ keyPath: WritableKeyPath<BridgeConfig, T>,
        default fallback: T
    ) -> Binding<T> {
        binding(keyPath, default: fallback)
    }

    private func pickerRow<Value: Hashable>(
        label: String,
        selection: Binding<Value>,
        options: [(Value, String)]
    ) -> some View {
        HStack(spacing: 10) {
            Text(label)
                .font(.callout)
                .frame(width: 160, alignment: .leading)
            Picker("", selection: selection) {
                ForEach(options, id: \.0) { value, name in
                    Text(name).tag(value)
                }
            }
            .labelsHidden()
            .pickerStyle(.menu)
            Spacer()
        }
    }

    private func stepperRow<Value: Strideable & Comparable>(
        label: String,
        value: Binding<Value>,
        range: ClosedRange<Value>,
        step: Value.Stride,
        suffix: String
    ) -> some View {
        HStack(spacing: 10) {
            Text(label)
                .font(.callout)
                .frame(width: 160, alignment: .leading)
            Stepper(value: value, in: range, step: step) {
                Text("\(value.wrappedValue) \(suffix)")
                    .font(.callout)
            }
            Spacer()
        }
    }

    private func textFieldRow(
        label: String,
        text: Binding<String>
    ) -> some View {
        HStack(spacing: 10) {
            Text(label)
                .font(.callout)
                .frame(width: 160, alignment: .leading)
            TextField("", text: text)
                .textFieldStyle(.roundedBorder)
            Spacer()
        }
    }

    @ViewBuilder
    private func errorFooter(for fields: [BridgeConfigField]) -> some View {
        let messages = fields.compactMap { draft.fieldErrors[$0] }
        if !messages.isEmpty {
            VStack(alignment: .leading, spacing: 2) {
                ForEach(messages, id: \.self) { message in
                    BridgeSettingsHint(message: message, isError: true)
                }
            }
        } else {
            EmptyView()
        }
    }

    private func modeLabel(_ mode: BridgeFTPReceiveMode) -> String {
        switch mode {
        case .auto: return L("Auto")
        case .hotspot: return L("Bridge Wi-Fi (hotspot)")
        case .peer: return L("Same Wi-Fi (peer)")
        case .wired: return L("USB IP (wired)")
        }
    }

    private func appearanceLabel(_ value: BridgeUIAppearance) -> String {
        switch value {
        case .light: return L("Light")
        case .dark: return L("Dark")
        case .auto: return L("Auto")
        }
    }

    private func fontSizeLabel(_ value: BridgeFontSize) -> String {
        switch value {
        case .small: return L("Small")
        case .medium: return L("Medium")
        case .large: return L("Large")
        }
    }

    private func languageLabel(_ value: BridgeUILanguage) -> String {
        switch value {
        case .english: return L("English")
        case .chineseSimplified: return L("Chinese (Simplified)")
        }
    }

    // MARK: - Diff preview rows

    private struct DiffRow {
        let field: String
        let before: String
        let after: String
    }

    private var diffRows: [DiffRow] {
        guard let loaded = draft.loaded, let draft = draft.draft else { return [] }
        var rows: [DiffRow] = []
        if loaded.printer.model != draft.printer.model {
            rows.append(.init(field: L("Printer model"), before: loaded.printer.model, after: draft.printer.model))
        }
        if loaded.printer.fit != draft.printer.fit {
            rows.append(.init(field: L("Printer fit"), before: loaded.printer.fit, after: draft.printer.fit))
        }
        if loaded.printer.quality != draft.printer.quality {
            rows.append(.init(field: L("JPEG quality"), before: "\(loaded.printer.quality)", after: "\(draft.printer.quality)"))
        }
        if loaded.printer.keepaliveIntervalSeconds != draft.printer.keepaliveIntervalSeconds {
            rows.append(.init(
                field: L("Keepalive interval"),
                before: "\(Int(loaded.printer.keepaliveIntervalSeconds)) s",
                after: "\(Int(draft.printer.keepaliveIntervalSeconds)) s"
            ))
        }
        if loaded.printer.searchIntervalSeconds != draft.printer.searchIntervalSeconds {
            rows.append(.init(
                field: L("Search interval"),
                before: "\(Int(loaded.printer.searchIntervalSeconds)) s",
                after: "\(Int(draft.printer.searchIntervalSeconds)) s"
            ))
        }
        if loaded.ftp.mode != draft.ftp.mode {
            rows.append(.init(
                field: L("FTP receive mode"),
                before: modeLabel(loaded.ftp.mode),
                after: modeLabel(draft.ftp.mode)
            ))
        }
        if loaded.ftp.username != draft.ftp.username {
            rows.append(.init(field: L("FTP username"), before: loaded.ftp.username, after: draft.ftp.username))
        }
        if let pending = self.draft.pendingPassword, !pending.isEmpty {
            rows.append(.init(field: L("FTP password"), before: L("(hidden)"), after: L("(new value)")))
        }
        if loaded.workflow.autoPrintDelaySeconds != draft.workflow.autoPrintDelaySeconds {
            rows.append(.init(
                field: L("Auto-print delay"),
                before: formatDelay(loaded.workflow.autoPrintDelaySeconds),
                after: formatDelay(draft.workflow.autoPrintDelaySeconds)
            ))
        }
        if loaded.workflow.allowPrintWithoutFilm != draft.workflow.allowPrintWithoutFilm {
            rows.append(.init(
                field: L("Allow print without film"),
                before: loaded.workflow.allowPrintWithoutFilm ? L("On") : L("Off"),
                after: draft.workflow.allowPrintWithoutFilm ? L("On") : L("Off")
            ))
        }
        if loaded.power.idlePoweroffEnabled != draft.power.idlePoweroffEnabled {
            rows.append(.init(
                field: L("Idle poweroff"),
                before: loaded.power.idlePoweroffEnabled ? L("On") : L("Off"),
                after: draft.power.idlePoweroffEnabled ? L("On") : L("Off")
            ))
        }
        if loaded.power.idlePoweroffAfterSeconds != draft.power.idlePoweroffAfterSeconds {
            rows.append(.init(
                field: L("Idle poweroff after"),
                before: "\(Int(loaded.power.idlePoweroffAfterSeconds)) s",
                after: "\(Int(draft.power.idlePoweroffAfterSeconds)) s"
            ))
        }
        if loaded.ui.appearance != draft.ui.appearance {
            rows.append(.init(
                field: L("LCD appearance"),
                before: appearanceLabel(loaded.ui.appearance),
                after: appearanceLabel(draft.ui.appearance)
            ))
        }
        if loaded.ui.fontSize != draft.ui.fontSize {
            rows.append(.init(
                field: L("LCD font size"),
                before: fontSizeLabel(loaded.ui.fontSize),
                after: fontSizeLabel(draft.ui.fontSize)
            ))
        }
        if loaded.ui.language != draft.ui.language {
            rows.append(.init(
                field: L("LCD language"),
                before: languageLabel(loaded.ui.language),
                after: languageLabel(draft.ui.language)
            ))
        }
        if loaded.adjustments.preset != draft.adjustments.preset {
            rows.append(.init(
                field: L("Preset"),
                before: presetLabel(loaded.adjustments.preset),
                after: presetLabel(draft.adjustments.preset)
            ))
        }
        if loaded.adjustments.saturation != draft.adjustments.saturation {
            rows.append(.init(
                field: L("Saturation"),
                before: signedBadge(loaded.adjustments.saturation),
                after: signedBadge(draft.adjustments.saturation)
            ))
        }
        if loaded.adjustments.exposure != draft.adjustments.exposure {
            rows.append(.init(
                field: L("Exposure"),
                before: signedBadge(loaded.adjustments.exposure),
                after: signedBadge(draft.adjustments.exposure)
            ))
        }
        if loaded.adjustments.sharpness != draft.adjustments.sharpness {
            rows.append(.init(
                field: L("Sharpness"),
                before: signedBadge(loaded.adjustments.sharpness),
                after: signedBadge(draft.adjustments.sharpness)
            ))
        }
        if loaded.adjustments.hue != draft.adjustments.hue {
            rows.append(.init(
                field: L("Hue"),
                before: signedBadge(loaded.adjustments.hue),
                after: signedBadge(draft.adjustments.hue)
            ))
        }
        if loaded.adjustments.vignette != draft.adjustments.vignette {
            rows.append(.init(
                field: L("Vignette"),
                before: "\(loaded.adjustments.vignette)",
                after: "\(draft.adjustments.vignette)"
            ))
        }
        if loaded.adjustments.datestamp != draft.adjustments.datestamp {
            rows.append(.init(
                field: L("Datestamp"),
                before: loaded.adjustments.datestamp ? L("On") : L("Off"),
                after: draft.adjustments.datestamp ? L("On") : L("Off")
            ))
        }
        if loaded.adjustments.datestampFormat != draft.adjustments.datestampFormat {
            rows.append(.init(
                field: L("Datestamp format"),
                before: loaded.adjustments.datestampFormat.rawValue,
                after: draft.adjustments.datestampFormat.rawValue
            ))
        }
        if loaded.adjustments.watermark != draft.adjustments.watermark {
            rows.append(.init(
                field: L("Watermark"),
                before: loaded.adjustments.watermark ? L("On") : L("Off"),
                after: draft.adjustments.watermark ? L("On") : L("Off")
            ))
        }
        if loaded.adjustments.watermarkText != draft.adjustments.watermarkText {
            rows.append(.init(
                field: L("Watermark text"),
                before: loaded.adjustments.watermarkText.isEmpty ? L("(empty)") : loaded.adjustments.watermarkText,
                after: draft.adjustments.watermarkText.isEmpty ? L("(empty)") : draft.adjustments.watermarkText
            ))
        }
        return rows
    }

    private func signedBadge(_ value: Int) -> String {
        value > 0 ? "+\(value)" : "\(value)"
    }

    private func formatDelay(_ value: Double?) -> String {
        guard let value else { return L("Off") }
        return "\(Int(value)) s"
    }
}
