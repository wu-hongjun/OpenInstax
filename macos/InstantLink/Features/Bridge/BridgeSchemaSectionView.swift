import SwiftUI

/// Generic schema-driven settings renderer (plan 039 phase 1).
///
/// Takes a ``BridgeConfigSchema`` and the shared ``BridgeSettingsDraft``
/// and renders each field type to a SwiftUI primitive. The view never
/// references the typed ``BridgeAdjustmentsConfig`` directly — it speaks
/// only in the bridge's snake_case keys, going through the draft's
/// key adapter (``adjustmentsValue(forKey:)`` /
/// ``setAdjustmentsValue(_:forKey:)``).
///
/// Forward-compatibility: a field whose ``type`` the Mac doesn't know
/// (e.g. a future ``"color_picker"``) renders as a dimmed
/// "update InstantLink" placeholder rather than crashing.
struct BridgeSchemaSectionView: View {
    @ObservedObject var draft: BridgeSettingsDraft
    let schema: BridgeConfigSchema

    /// Width of the labels column. All four control types
    /// (picker / slider / toggle / text) anchor their control to the same
    /// x-coordinate so the card reads as a single aligned grid rather than
    /// per-row-improvised layouts.
    private static let labelColumnWidth: CGFloat = 160
    /// Standing horizontal pad between label column and the start of the
    /// control. Kept in sync with the label width so the inline help
    /// caption aligns under the control rather than under the label.
    private static let labelToControlSpacing: CGFloat = 10
    /// Opacity applied to a row when its ``depends_on`` parent disables
    /// it. SwiftUI's ``.disabled(true)`` greys the picker chevron but
    /// leaves the slider track, toggle switch chrome, and text field
    /// background at full color, so we dim the whole row at the view
    /// layer for a uniform "this is inactive" signal.
    private static let disabledOpacity: Double = 0.55

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            ForEach(Array(schema.fields.enumerated()), id: \.offset) { _, field in
                fieldView(field)
            }
        }
    }

    @ViewBuilder
    private func fieldView(_ field: BridgeConfigSchemaField) -> some View {
        let disabled = !isFieldEnabled(field)
        switch field {
        case .picker(let picker):
            pickerRow(picker)
                .disabled(disabled)
                .opacity(disabled ? Self.disabledOpacity : 1.0)
        case .slider(let slider):
            sliderRow(slider)
                .disabled(disabled)
                .opacity(disabled ? Self.disabledOpacity : 1.0)
        case .toggle(let toggle):
            toggleRow(toggle)
                .disabled(disabled)
                .opacity(disabled ? Self.disabledOpacity : 1.0)
        case .text(let text):
            textRow(text)
                .disabled(disabled)
                .opacity(disabled ? Self.disabledOpacity : 1.0)
        case .unknown(let key, let type):
            unknownRow(key: key, type: type)
        }
    }

    // MARK: - Row chrome

    /// Shared row scaffold: label column on the left, caller-supplied
    /// control filling the remaining width, optional help caption indented
    /// under the control. Every adjustments row is built from this so the
    /// grid alignment, the row spacing, and the help styling stay
    /// in lockstep across control types.
    @ViewBuilder
    private func labeledRow<Control: View>(
        label: String,
        help: String?,
        @ViewBuilder control: () -> Control
    ) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: Self.labelToControlSpacing) {
                Text(L(label))
                    .font(.callout)
                    // ``lineLimit(2) + fixedSize`` lets long localized
                    // labels (e.g. zh-Hans + future translations) wrap
                    // gracefully instead of truncating with "…".
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(width: Self.labelColumnWidth, alignment: .leading)
                control()
            }
            if let help, !help.isEmpty {
                Text(L(help))
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .lineLimit(2)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.leading, Self.labelColumnWidth + Self.labelToControlSpacing)
            }
        }
    }

    // MARK: - Picker

    private func pickerRow(_ field: BridgePickerField) -> some View {
        let binding = Binding<String>(
            get: {
                if let current = draft.adjustmentsValue(forKey: field.key) as? String {
                    return current
                }
                return field.options.first?.value ?? ""
            },
            set: { newValue in
                draft.setAdjustmentsValue(newValue, forKey: field.key)
            }
        )
        return labeledRow(label: field.label, help: field.help) {
            Picker("", selection: binding) {
                ForEach(field.options, id: \.value) { option in
                    Text(L(option.label)).tag(option.value)
                }
            }
            .labelsHidden()
            .pickerStyle(.menu)
            Spacer(minLength: 0)
        }
    }

    // MARK: - Slider

    private func sliderRow(_ field: BridgeSliderField) -> some View {
        let intBinding = Binding<Int>(
            get: {
                if let current = draft.adjustmentsValue(forKey: field.key) as? Int {
                    return current
                }
                return Int(field.range.min)
            },
            set: { newValue in
                draft.setAdjustmentsValue(newValue, forKey: field.key)
            }
        )
        let doubleBinding = Binding<Double>(
            get: { Double(intBinding.wrappedValue) },
            set: { intBinding.wrappedValue = Int($0.rounded()) }
        )
        return labeledRow(label: field.label, help: field.help) {
            Slider(
                value: doubleBinding,
                in: field.range.min...field.range.max,
                step: field.range.step
            )
            .controlSize(.small)
            Text(formatSliderBadge(value: intBinding.wrappedValue, display: field.display))
                .font(.callout.monospacedDigit())
                .foregroundColor(.secondary)
                // Widened from 44 → 56 pt to fit the percent suffix
                // ("+100 %" = 6 chars) at .callout monospaced-digit.
                .frame(width: 56, alignment: .trailing)
        }
    }

    private func formatSliderBadge(value: Int, display: BridgeSliderDisplay) -> String {
        switch display {
        case .signedPercent:
            // Polish #2: percent badges advertise the unit. Stepper
            // rows in sibling cards (e.g. JPEG quality) already do this;
            // matching the convention here keeps the card legible.
            if value > 0 { return "+\(value) %" }
            return "\(value) %"
        case .unsignedPercent:
            return "\(value) %"
        case .integer:
            return "\(value)"
        }
    }

    // MARK: - Toggle

    private func toggleRow(_ field: BridgeToggleField) -> some View {
        let binding = Binding<Bool>(
            get: { (draft.adjustmentsValue(forKey: field.key) as? Bool) ?? false },
            set: { newValue in draft.setAdjustmentsValue(newValue, forKey: field.key) }
        )
        // Polish #1: toggles route through the same labeled-row scaffold
        // as pickers / sliders / text so the on/off switch sits at the
        // same x-coordinate as every other control in the card. Without
        // this the toggle label ran the full row width and the switch
        // anchored to the trailing edge, breaking the grid.
        return labeledRow(label: field.label, help: field.help) {
            Toggle("", isOn: binding)
                .labelsHidden()
                .toggleStyle(.switch)
            Spacer(minLength: 0)
        }
    }

    // MARK: - Text

    private func textRow(_ field: BridgeTextField) -> some View {
        let binding = Binding<String>(
            get: { (draft.adjustmentsValue(forKey: field.key) as? String) ?? "" },
            set: { newValue in draft.setAdjustmentsValue(newValue, forKey: field.key) }
        )
        return labeledRow(label: field.label, help: field.help) {
            TextField("", text: binding)
                .textFieldStyle(.roundedBorder)
            Spacer(minLength: 0)
        }
    }

    // MARK: - Unknown field placeholder

    private func unknownRow(key: String, type: String) -> some View {
        HStack(spacing: 10) {
            Image(systemName: "exclamationmark.triangle")
                .foregroundColor(.secondary)
            Text(L("Setting requires a newer InstantLink. Update the app to manage \(key)."))
                .font(.callout)
                .foregroundColor(.secondary)
            Spacer()
        }
        .accessibilityIdentifier("bridge.schema.unknownField.\(type)")
    }

    // MARK: - Dependency gating

    /// Resolve a field's dependency against the current draft. A field with
    /// no ``depends_on`` is always enabled. When the parent value doesn't
    /// equal the declared dependency value, the field renders disabled.
    private func isFieldEnabled(_ field: BridgeConfigSchemaField) -> Bool {
        guard let dependency = field.dependsOn else { return true }
        let parentValue = draft.adjustmentsValue(forKey: dependency.field)
        return matches(parentValue: parentValue, dependency: dependency.value)
    }

    private func matches(parentValue: Any?, dependency: BridgeJSONValue) -> Bool {
        switch dependency {
        case .bool(let expected):
            return (parentValue as? Bool) == expected
        case .string(let expected):
            return (parentValue as? String) == expected
        case .number(let expected):
            if let intValue = parentValue as? Int {
                return Double(intValue) == expected
            }
            if let doubleValue = parentValue as? Double {
                return doubleValue == expected
            }
            return false
        case .null:
            return parentValue == nil
        case .array, .object:
            return false
        }
    }
}
