import SwiftUI

private struct PrinterProfileDraft {
    var customName: String
    var selectedModel: String
    var selectedColor: String

    init(profile: PrinterProfile) {
        customName = profile.customName ?? ""
        selectedModel = profile.effectiveModel
        selectedColor = profile.deviceColor ?? ""
    }

    func apply(to profile: PrinterProfile) -> PrinterProfile {
        var updated = profile
        updated.customName = customName.isEmpty ? nil : customName
        updated.overriddenModel = selectedModel == profile.detectedModel ? nil : selectedModel
        updated.deviceColor = selectedColor.isEmpty ? nil : selectedColor
        return updated
    }
}

struct PrinterProfileEditorView: View {
    @EnvironmentObject var viewModel: ViewModel
    @Environment(\.dismiss) private var dismiss

    let profile: PrinterProfile
    let title: String
    let showsSerialVerificationNote: Bool

    @State private var draft: PrinterProfileDraft

    init(
        profile: PrinterProfile,
        title: String,
        showsSerialVerificationNote: Bool = false
    ) {
        self.profile = profile
        self.title = title
        self.showsSerialVerificationNote = showsSerialVerificationNote
        _draft = State(initialValue: PrinterProfileDraft(profile: profile))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text(title)
                .font(.headline)

            VStack(alignment: .leading, spacing: 8) {
                if let serial = profile.serialNumber {
                    VStack(alignment: .leading, spacing: 2) {
                        HStack {
                            Text(L("Serial Number:"))
                                .foregroundColor(.secondary)
                            Text(serial)
                                .fontWeight(.medium)
                                .textSelection(.enabled)
                        }

                        if showsSerialVerificationNote {
                            Text(L("Verify this matches the serial number on the bottom of your device"))
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                    }
                }

                HStack {
                    Text(L("BLE Name:"))
                        .foregroundColor(.secondary)
                    Text(profile.bleIdentifier)
                        .font(.caption)
                        .textSelection(.enabled)
                }
            }

            Divider()

            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    Text(L("Model:"))
                        .frame(width: 50, alignment: .leading)
                    Picker("", selection: $draft.selectedModel) {
                        ForEach(PrinterProfile.availableModels, id: \.self) { model in
                            Text(model).tag(model)
                        }
                    }
                    .labelsHidden()
                }

                HStack {
                    Text(L("Color:"))
                        .frame(width: 50, alignment: .leading)
                    Picker("", selection: $draft.selectedColor) {
                        Text(L("None")).tag("")
                        ForEach(PrinterProfile.availableColors, id: \.self) { color in
                            Text(L(color)).tag(color)
                        }
                    }
                    .labelsHidden()
                }

                HStack {
                    Text(L("Name:"))
                        .frame(width: 50, alignment: .leading)
                    TextField(L("Custom display name"), text: $draft.customName)
                        .textFieldStyle(.roundedBorder)
                }
            }

            Divider()

            HStack {
                Spacer()
                Button(L("Cancel")) { dismiss() }
                    .keyboardShortcut(.cancelAction)
                Button(L("Save")) { saveAndDismiss() }
                    .keyboardShortcut(.defaultAction)
                    .buttonStyle(.borderedProminent)
            }
        }
        .padding(24)
        .frame(width: 380)
    }

    private func saveAndDismiss() {
        viewModel.saveProfile(draft.apply(to: profile))
        dismiss()
    }
}

struct PrinterProfileSheet: View {
    @EnvironmentObject var viewModel: ViewModel
    let isPostPairing: Bool

    var body: some View {
        Group {
            if let profile = viewModel.editingProfile {
                PrinterProfileEditorView(
                    profile: profile,
                    title: isPostPairing ? L("Printer Connected") : L("Edit Printer"),
                    showsSerialVerificationNote: isPostPairing
                )
            } else {
                EmptyView()
            }
        }
    }
}
