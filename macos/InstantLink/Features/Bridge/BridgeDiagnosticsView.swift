import AppKit
import SwiftUI
import UniformTypeIdentifiers

/// Diagnostics tab content. Owns a child `BridgeDiagnosticsCoordinator` that
/// streams `/v1/logs/stream` SSE events and brokers support-bundle creation.
/// Reads paired-device from the parent `BridgeControlCoordinator` so it stays
/// in lock-step with discovery and pairing.
struct BridgeDiagnosticsView: View {
    @ObservedObject var coordinator: BridgeControlCoordinator
    @ObservedObject var diagnosticsCoordinator: BridgeDiagnosticsCoordinator

    @State private var tailFollow: Bool = true

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                if isUnpaired {
                    pairingRequiredCard
                } else {
                    logsCard
                    supportBundleCard
                }
                Spacer(minLength: 8)
            }
            .padding(16)
        }
        .onAppear {
            guard let device = currentDevice() else { return }
            diagnosticsCoordinator.startStreaming(device: device)
        }
        .onDisappear {
            diagnosticsCoordinator.stopStreaming()
        }
    }

    // MARK: - Pairing gate

    private var isUnpaired: Bool {
        if case .paired = coordinator.snapshot.pairing { return false }
        return true
    }

    private var pairingRequiredCard: some View {
        BridgeCard(title: L("Diagnostics")) {
            Text(L("Pair this Mac with the Bridge to read live diagnostics."))
                .font(.callout)
                .foregroundColor(.secondary)
        }
    }

    // MARK: - Logs card

    private var logsCard: some View {
        BridgeCard(title: L("Live logs")) {
            VStack(alignment: .leading, spacing: 10) {
                logControls
                logList
            }
        }
    }

    private var logControls: some View {
        HStack(spacing: 10) {
            filterPills
            Spacer()
            streamStatePill
            Toggle(L("Follow"), isOn: $tailFollow)
                .toggleStyle(.switch)
                .controlSize(.small)
            Button {
                diagnosticsCoordinator.clearTail()
            } label: {
                Image(systemName: "trash")
            }
            .buttonStyle(.borderless)
            .help(L("Clear log buffer"))
            streamToggleButton
        }
    }

    private var filterPills: some View {
        HStack(spacing: 6) {
            ForEach(BridgeLogLevel.allCases, id: \.self) { level in
                filterPill(level: level)
            }
        }
    }

    private func filterPill(level: BridgeLogLevel) -> some View {
        let isSelected = diagnosticsCoordinator.snapshot.logLevelFilter == level
        return Button {
            diagnosticsCoordinator.setFilter(level)
        } label: {
            Text(L(level.displayLabel))
                .font(.caption.weight(isSelected ? .semibold : .regular))
                .padding(.horizontal, 8)
                .padding(.vertical, 3)
                .background(
                    RoundedRectangle(cornerRadius: 6)
                        .fill(isSelected ? Color.accentColor.opacity(0.20) : Color.secondary.opacity(0.10))
                )
                .foregroundColor(isSelected ? .accentColor : .secondary)
        }
        .buttonStyle(.plain)
    }

    private var streamStatePill: some View {
        let (text, color): (String, Color)
        switch diagnosticsCoordinator.snapshot.streamState {
        case .live: (text, color) = (L("Live"), .green)
        case .paused: (text, color) = (L("Paused"), .orange)
        case .connecting: (text, color) = (L("Connecting"), .secondary)
        case .disconnected: (text, color) = (L("Disconnected"), .red)
        case .idle: (text, color) = (L("Idle"), .secondary)
        }
        return HStack(spacing: 4) {
            Circle().fill(color).frame(width: 6, height: 6)
            Text(text)
                .font(.caption)
                .foregroundColor(.secondary)
        }
    }

    @ViewBuilder
    private var streamToggleButton: some View {
        switch diagnosticsCoordinator.snapshot.streamState {
        case .live, .connecting:
            Button {
                diagnosticsCoordinator.stopStreaming()
            } label: {
                Image(systemName: "pause.fill")
            }
            .buttonStyle(.borderless)
            .help(L("Pause"))
        default:
            Button {
                guard let device = currentDevice() else { return }
                diagnosticsCoordinator.startStreaming(device: device)
            } label: {
                Image(systemName: "play.fill")
            }
            .buttonStyle(.borderless)
            .help(L("Resume"))
        }
    }

    private var logList: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 4) {
                    if diagnosticsCoordinator.snapshot.logTail.isEmpty {
                        Text(L("No log events yet."))
                            .font(.caption)
                            .foregroundColor(.secondary)
                            .padding(.vertical, 8)
                    } else {
                        ForEach(diagnosticsCoordinator.snapshot.logTail) { event in
                            logRow(event: event)
                                .id(event.id)
                        }
                    }
                }
                .padding(.vertical, 4)
            }
            .frame(minHeight: 240, maxHeight: 380)
            .background(
                RoundedRectangle(cornerRadius: 6)
                    .fill(Color(NSColor.textBackgroundColor).opacity(0.6))
            )
            .onChange(of: diagnosticsCoordinator.snapshot.logTail.last?.id) {
                if tailFollow, let lastID = diagnosticsCoordinator.snapshot.logTail.last?.id {
                    proxy.scrollTo(lastID, anchor: .bottom)
                }
            }
        }
    }

    private func logRow(event: BridgeLogEvent) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(levelGlyph(event.level))
                .font(.system(.caption, design: .monospaced))
                .foregroundColor(levelColor(event.level))
                .frame(width: 14)
            Text(event.timestamp)
                .font(.system(.caption2, design: .monospaced))
                .foregroundColor(.secondary)
            Text(event.message)
                .font(.system(.caption, design: .monospaced))
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.horizontal, 6)
    }

    private func levelGlyph(_ level: BridgeLogLevel) -> String {
        switch level {
        case .info: return "·"
        case .warning: return "!"
        case .error: return "✕"
        }
    }

    private func levelColor(_ level: BridgeLogLevel) -> Color {
        switch level {
        case .info: return .secondary
        case .warning: return .orange
        case .error: return .red
        }
    }

    // MARK: - Support bundle card

    private var supportBundleCard: some View {
        BridgeCard(title: L("Support bundle")) {
            VStack(alignment: .leading, spacing: 10) {
                Text(L("Create a redacted support bundle you can share with support. Passwords and signing keys are stripped."))
                    .font(.callout)
                    .foregroundColor(.secondary)
                supportBundleContent
            }
        }
    }

    @ViewBuilder
    private var supportBundleContent: some View {
        switch diagnosticsCoordinator.snapshot.supportBundle {
        case .idle:
            Button {
                presentSupportBundlePanel()
            } label: {
                Text(L("Create support bundle…"))
            }
            .buttonStyle(.borderedProminent)
        case .creating:
            HStack(spacing: 10) {
                ProgressView().controlSize(.small)
                Text(L("Creating support bundle…"))
                    .font(.callout)
                    .foregroundColor(.secondary)
            }
        case .ready(let bundle, let savedTo, _):
            supportBundleReady(bundle: bundle, savedTo: savedTo)
        case .failed(let reason, _):
            VStack(alignment: .leading, spacing: 8) {
                HStack(alignment: .top, spacing: 10) {
                    Image(systemName: "exclamationmark.octagon.fill")
                        .foregroundColor(.red)
                    VStack(alignment: .leading, spacing: 4) {
                        Text(L("Support bundle failed"))
                            .font(.callout.weight(.semibold))
                            .foregroundColor(.red)
                        Text(reason)
                            .font(.caption)
                            .foregroundColor(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    Spacer()
                }
                HStack(spacing: 8) {
                    Button(L("Try again")) {
                        presentSupportBundlePanel()
                    }
                    Button(L("Dismiss")) {
                        diagnosticsCoordinator.clearSupportBundle()
                    }
                }
            }
        }
    }

    private func supportBundleReady(bundle: BridgeSupportBundleResult, savedTo: URL?) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: "checkmark.seal.fill")
                    .foregroundColor(.green)
                VStack(alignment: .leading, spacing: 2) {
                    Text(L("Support bundle ready."))
                        .font(.callout.weight(.semibold))
                    Text(bundle.archivePath)
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .textSelection(.enabled)
                        .lineLimit(2)
                }
                Spacer()
            }
            HStack(spacing: 8) {
                if let savedTo {
                    Button(L("Show in Finder")) {
                        NSWorkspace.shared.activateFileViewerSelecting([savedTo])
                    }
                }
                Button(L("Create another")) {
                    presentSupportBundlePanel()
                }
                Button(L("Dismiss")) {
                    diagnosticsCoordinator.clearSupportBundle()
                }
            }
        }
    }

    private func presentSupportBundlePanel() {
        guard let device = currentDevice() else { return }
        let savePanel = NSSavePanel()
        savePanel.title = L("Save support bundle sidecar")
        savePanel.nameFieldStringValue = defaultSupportBundleFilename(device: device)
        savePanel.allowedContentTypes = [supportBundleSidecarContentType]
        savePanel.canCreateDirectories = true
        let response = savePanel.runModal()
        let destination: URL? = response == .OK ? savePanel.url : nil
        Task {
            await diagnosticsCoordinator.createSupportBundle(
                device: device,
                destinationURL: destination
            )
        }
    }

    // MARK: - Helpers

    private func currentDevice() -> BridgeDevice? {
        switch coordinator.snapshot.discovery {
        case .found(let device, _): return device
        case .lost(let device, _): return device
        case .searching: return nil
        }
    }

    private func defaultSupportBundleFilename(device: BridgeDevice) -> String {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd"
        formatter.locale = Locale(identifier: "en_US_POSIX")
        let datestamp = formatter.string(from: Date())
        return "bridge-support-\(device.deviceID)-\(datestamp).json"
    }

    private var supportBundleSidecarContentType: UTType {
        UTType.json
    }
}
