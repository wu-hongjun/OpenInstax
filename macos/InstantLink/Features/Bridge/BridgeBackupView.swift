import AppKit
import SwiftUI
import UniformTypeIdentifiers

/// Backup tab content. Owns a child `BridgeBackupCoordinator` that walks the
/// bridge through createBackup → download → write file (on backup) or
/// read file → upload → restoreBackup (on restore). Reads paired-device from
/// the parent `BridgeControlCoordinator` so it stays in lock-step with
/// discovery and pairing.
struct BridgeBackupView: View {
    @ObservedObject var coordinator: BridgeControlCoordinator
    @ObservedObject var backupCoordinator: BridgeBackupCoordinator

    @State private var showBackupPassphraseSheet: Bool = false
    @State private var backupPassphrase: String = ""
    @State private var backupPassphraseConfirm: String = ""
    @State private var backupPassphraseError: String?

    @State private var showRestorePassphraseSheet: Bool = false
    @State private var restorePassphrase: String = ""
    @State private var restorePassphraseError: String?
    @State private var pendingRestoreFile: BridgeBackupFile?
    @State private var pendingRestoreURL: URL?
    @State private var showCrossBridgeConfirmation: Bool = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                if isUnpaired {
                    pairingRequiredCard
                } else {
                    backupCard
                    restoreCard
                    if let result = backupCoordinator.snapshot.lastResult {
                        resultCard(result: result)
                    }
                    if backupCoordinator.snapshot.operation != nil {
                        operationCard
                    }
                }
                Spacer(minLength: 8)
            }
            .padding(16)
        }
        .sheet(isPresented: $showBackupPassphraseSheet) {
            backupPassphraseSheet
        }
        .sheet(isPresented: $showRestorePassphraseSheet) {
            restorePassphraseSheet
        }
        .confirmationDialog(
            L("This will overwrite the current bridge identity. Continue?"),
            isPresented: $showCrossBridgeConfirmation,
            titleVisibility: .visible
        ) {
            Button(L("Restore anyway"), role: .destructive) {
                proceedWithRestore()
            }
            Button(L("Cancel"), role: .cancel) {
                cancelPendingRestore()
            }
        } message: {
            if let pending = pendingRestoreFile, let current = currentDevice() {
                Text(String(
                    format: L("Backup is from %@ but this Bridge is %@."),
                    pending.sourceDeviceID,
                    current.deviceID
                ))
            } else {
                Text(L("The backup is from a different Bridge."))
            }
        }
    }

    // MARK: - Pairing gate

    private var isUnpaired: Bool {
        if case .paired = coordinator.snapshot.pairing { return false }
        return true
    }

    private var pairingRequiredCard: some View {
        BridgeCard(title: L("Backup")) {
            Text(L("Pair this Mac with the Bridge to back up or restore."))
                .font(.callout)
                .foregroundColor(.secondary)
        }
    }

    // MARK: - Back up card

    private var backupCard: some View {
        BridgeCard(title: L("Back up Bridge")) {
            VStack(alignment: .leading, spacing: 10) {
                Text(L("Save a Bridge backup file you can restore later (or to another Bridge)."))
                    .font(.callout)
                    .foregroundColor(.secondary)
                Button {
                    presentBackupPassphraseSheet()
                } label: {
                    Text(L("Back up Bridge…"))
                }
                .buttonStyle(.borderedProminent)
                .disabled(isOperationActive)
                HStack(alignment: .top, spacing: 6) {
                    Image(systemName: "exclamationmark.shield.fill")
                        .foregroundColor(.orange)
                    Text(L("The backup contains your Bridge's signing identity and Wi-Fi credentials. Treat it like a password."))
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    // MARK: - Restore card

    private var restoreCard: some View {
        BridgeCard(title: L("Restore from file")) {
            VStack(alignment: .leading, spacing: 10) {
                Text(L("Restore a previously-saved Bridge backup."))
                    .font(.callout)
                    .foregroundColor(.secondary)
                Button {
                    presentRestoreFilePicker()
                } label: {
                    Text(L("Restore from file…"))
                }
                .disabled(isOperationActive)
            }
        }
    }

    // MARK: - Operation card

    private var operationCard: some View {
        BridgeCard(title: L("In progress")) {
            HStack(spacing: 10) {
                ProgressView().controlSize(.small)
                Text(operationLabel)
                    .font(.callout)
                Spacer()
            }
        }
    }

    private var operationLabel: String {
        switch backupCoordinator.snapshot.operation {
        case .creatingBackup:
            return L("Creating backup…")
        case .downloadingBackup:
            return L("Downloading backup…")
        case .restoringBackup(let phase):
            switch phase {
            case .uploading: return L("Uploading backup…")
            case .applying: return L("Applying backup…")
            case .restarting: return L("Restarting Bridge…")
            case .verifying: return L("Verifying Bridge…")
            }
        case .none:
            return L("Working…")
        }
    }

    // MARK: - Result card

    @ViewBuilder
    private func resultCard(result: BridgeBackupSnapshot.Result) -> some View {
        BridgeCard(title: L("Last result")) {
            VStack(alignment: .leading, spacing: 10) {
                switch result {
                case .backupCreated(let path, _, _):
                    HStack(spacing: 10) {
                        Image(systemName: "checkmark.circle.fill")
                            .foregroundColor(.green)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(L("Backup saved."))
                                .font(.callout.weight(.semibold))
                            Text(path.lastPathComponent)
                                .font(.caption)
                                .foregroundColor(.secondary)
                                .textSelection(.enabled)
                        }
                        Spacer()
                    }
                    HStack(spacing: 8) {
                        Button(L("Show in Finder")) {
                            NSWorkspace.shared.activateFileViewerSelecting([path])
                        }
                        Button(L("Dismiss")) {
                            backupCoordinator.clearLastResult()
                        }
                    }
                case .backupRestored:
                    HStack(spacing: 10) {
                        Image(systemName: "checkmark.circle.fill")
                            .foregroundColor(.green)
                        Text(L("Bridge restored. Reconnecting…"))
                            .font(.callout)
                        Spacer()
                    }
                    Button(L("Dismiss")) {
                        backupCoordinator.clearLastResult()
                    }
                case .failed(let reason, _):
                    HStack(alignment: .top, spacing: 10) {
                        Image(systemName: "exclamationmark.octagon.fill")
                            .foregroundColor(.red)
                        VStack(alignment: .leading, spacing: 4) {
                            Text(L("Backup failed"))
                                .font(.callout.weight(.semibold))
                                .foregroundColor(.red)
                            Text(reason)
                                .font(.caption)
                                .foregroundColor(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        Spacer()
                    }
                    Button(L("Dismiss")) {
                        backupCoordinator.clearLastResult()
                    }
                }
            }
        }
    }

    // MARK: - Sheets

    private var backupPassphraseSheet: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(L("Set a backup passphrase"))
                .font(.headline)
            Text(L("Pick a passphrase you'll remember; you'll need it to restore."))
                .font(.callout)
                .foregroundColor(.secondary)
            SecureField(L("Passphrase"), text: $backupPassphrase)
                .textFieldStyle(.roundedBorder)
            SecureField(L("Confirm passphrase"), text: $backupPassphraseConfirm)
                .textFieldStyle(.roundedBorder)
            if let error = backupPassphraseError {
                Text(error)
                    .font(.caption)
                    .foregroundColor(.red)
            }
            HStack {
                Spacer()
                Button(L("Cancel")) {
                    dismissBackupPassphraseSheet()
                }
                Button(L("Save backup")) {
                    handleBackupPassphraseSubmit()
                }
                .buttonStyle(.borderedProminent)
                .keyboardShortcut(.defaultAction)
            }
        }
        .padding(20)
        .frame(minWidth: 360)
    }

    private var restorePassphraseSheet: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(L("Enter backup passphrase"))
                .font(.headline)
            Text(L("Enter the passphrase you used when this backup was created."))
                .font(.callout)
                .foregroundColor(.secondary)
            SecureField(L("Passphrase"), text: $restorePassphrase)
                .textFieldStyle(.roundedBorder)
            if let error = restorePassphraseError {
                Text(error)
                    .font(.caption)
                    .foregroundColor(.red)
            }
            HStack {
                Spacer()
                Button(L("Cancel")) {
                    dismissRestorePassphraseSheet()
                }
                Button(L("Restore")) {
                    handleRestorePassphraseSubmit()
                }
                .buttonStyle(.borderedProminent)
                .keyboardShortcut(.defaultAction)
            }
        }
        .padding(20)
        .frame(minWidth: 360)
    }

    // MARK: - Backup flow

    private func presentBackupPassphraseSheet() {
        backupPassphrase = ""
        backupPassphraseConfirm = ""
        backupPassphraseError = nil
        showBackupPassphraseSheet = true
    }

    private func dismissBackupPassphraseSheet() {
        showBackupPassphraseSheet = false
        backupPassphrase = ""
        backupPassphraseConfirm = ""
        backupPassphraseError = nil
    }

    private func handleBackupPassphraseSubmit() {
        let passphrase = backupPassphrase
        let confirm = backupPassphraseConfirm
        if passphrase.count < BridgeBackupCoordinator.minimumPassphraseLength {
            backupPassphraseError = L("Passphrase must be at least 8 characters.")
            return
        }
        if passphrase != confirm {
            backupPassphraseError = L("Passphrases do not match.")
            return
        }
        guard let device = currentDevice() else {
            backupPassphraseError = L("No Bridge is currently connected.")
            return
        }

        let savePanel = NSSavePanel()
        savePanel.title = L("Save Bridge backup")
        savePanel.nameFieldStringValue = defaultBackupFilename(device: device)
        savePanel.allowedContentTypes = [bridgeBackupContentType]
        savePanel.canCreateDirectories = true

        let response = savePanel.runModal()
        guard response == .OK, let destinationURL = savePanel.url else { return }

        showBackupPassphraseSheet = false
        Task {
            await backupCoordinator.createBackup(
                device: device,
                passphrase: passphrase,
                destinationURL: destinationURL
            )
        }
    }

    // MARK: - Restore flow

    private func presentRestoreFilePicker() {
        let openPanel = NSOpenPanel()
        openPanel.title = L("Open Bridge backup")
        openPanel.allowsMultipleSelection = false
        openPanel.canChooseDirectories = false
        openPanel.allowedContentTypes = [bridgeBackupContentType]

        let response = openPanel.runModal()
        guard response == .OK, let fileURL = openPanel.url else { return }

        do {
            let file = try backupCoordinator.inspectBackupFile(at: fileURL)
            pendingRestoreFile = file
            pendingRestoreURL = fileURL
            restorePassphrase = ""
            restorePassphraseError = nil
            showRestorePassphraseSheet = true
        } catch {
            // Inline error: surface via the coordinator's result strip.
            Task { @MainActor in
                backupCoordinator.clearLastResult()
                // Force a failed result so the user sees the parse error in the
                // standard result location instead of a transient alert.
                await backupCoordinator.restoreBackup(
                    device: currentDevice() ?? makePlaceholderDevice(),
                    fileURL: fileURL,
                    passphrase: String(repeating: "*", count: BridgeBackupCoordinator.minimumPassphraseLength)
                )
            }
        }
    }

    private func dismissRestorePassphraseSheet() {
        showRestorePassphraseSheet = false
        restorePassphrase = ""
        restorePassphraseError = nil
        pendingRestoreFile = nil
        pendingRestoreURL = nil
    }

    private func handleRestorePassphraseSubmit() {
        let passphrase = restorePassphrase
        if passphrase.count < BridgeBackupCoordinator.minimumPassphraseLength {
            restorePassphraseError = L("Passphrase must be at least 8 characters.")
            return
        }
        guard currentDevice() != nil else {
            restorePassphraseError = L("No Bridge is currently connected.")
            return
        }

        if let pending = pendingRestoreFile,
           let current = currentDevice(),
           pending.sourceDeviceID != current.deviceID {
            showRestorePassphraseSheet = false
            showCrossBridgeConfirmation = true
        } else {
            showRestorePassphraseSheet = false
            proceedWithRestore()
        }
    }

    private func proceedWithRestore() {
        guard let device = currentDevice(),
              let url = pendingRestoreURL else {
            cancelPendingRestore()
            return
        }
        let passphrase = restorePassphrase
        Task {
            await backupCoordinator.restoreBackup(
                device: device,
                fileURL: url,
                passphrase: passphrase
            )
            await MainActor.run {
                cancelPendingRestore()
            }
        }
    }

    private func cancelPendingRestore() {
        pendingRestoreFile = nil
        pendingRestoreURL = nil
        restorePassphrase = ""
        restorePassphraseError = nil
    }

    // MARK: - Helpers

    private var isOperationActive: Bool {
        backupCoordinator.snapshot.operation != nil
    }

    private func currentDevice() -> BridgeDevice? {
        switch coordinator.snapshot.discovery {
        case .found(let device, _): return device
        case .lost(let device, _): return device
        case .searching: return nil
        }
    }

    private func makePlaceholderDevice() -> BridgeDevice {
        BridgeDevice(
            deviceID: "unknown",
            displayName: "unknown",
            softwareVersion: "0",
            apiVersion: "0"
        )
    }

    private func defaultBackupFilename(device: BridgeDevice) -> String {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd"
        formatter.locale = Locale(identifier: "en_US_POSIX")
        let datestamp = formatter.string(from: Date())
        return "bridge-\(device.deviceID)-\(datestamp).\(BridgeBackupFile.fileExtension)"
    }

    private var bridgeBackupContentType: UTType {
        UTType(filenameExtension: BridgeBackupFile.fileExtension) ?? .data
    }
}
