import SwiftUI

/// Live progress feed driven by `BridgeUpdateSnapshot.Operation`.
///
/// Top: large phase label ("Uploading update…", "Verifying signature…").
/// Middle: progress bar — determinate during upload (`uploadProgress`),
///         indeterminate during install phases the bridge controls.
/// Bottom: collapsible chronological event log in a monospaced font.
struct BridgeUpdateProgressView: View {
    let operation: BridgeUpdateSnapshot.Operation
    @State private var isLogExpanded: Bool = false

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            phaseHeader
            progressBar
            messageRow
            log
        }
    }

    // MARK: - Header

    private var phaseHeader: some View {
        HStack(spacing: 10) {
            phaseIcon
                .frame(width: 26, height: 26)
            Text(humanPhase(operation.phase))
                .font(.title3.weight(.semibold))
            Spacer()
        }
    }

    @ViewBuilder
    private var phaseIcon: some View {
        switch operation.phase {
        case .done:
            Image(systemName: "checkmark.circle.fill")
                .resizable()
                .foregroundColor(.green)
        case .failed, .needsRecovery:
            Image(systemName: "xmark.octagon.fill")
                .resizable()
                .foregroundColor(.red)
        case .rolledBack:
            Image(systemName: "arrow.uturn.backward.circle.fill")
                .resizable()
                .foregroundColor(.orange)
        default:
            ProgressView()
                .controlSize(.small)
        }
    }

    // MARK: - Progress bar

    @ViewBuilder
    private var progressBar: some View {
        if operation.phase == .uploadingUpdate, let pct = operation.uploadProgress {
            VStack(alignment: .leading, spacing: 4) {
                ProgressView(value: max(0, min(1, pct)))
                Text(percentLabel(pct))
                    .font(.caption.monospacedDigit())
                    .foregroundColor(.secondary)
            }
        } else if let progress = operation.events.last?.progress {
            VStack(alignment: .leading, spacing: 4) {
                ProgressView(value: max(0, min(1, progress)))
                Text(percentLabel(progress))
                    .font(.caption.monospacedDigit())
                    .foregroundColor(.secondary)
            }
        } else {
            ProgressView()
                .progressViewStyle(.linear)
        }
    }

    private func percentLabel(_ pct: Double) -> String {
        let rounded = Int((max(0, min(1, pct)) * 100).rounded())
        return "\(rounded)%"
    }

    // MARK: - Message

    @ViewBuilder
    private var messageRow: some View {
        if let message = operation.lastMessage, !message.isEmpty {
            Text(message)
                .font(.callout)
                .foregroundColor(.secondary)
        }
    }

    // MARK: - Log

    @ViewBuilder
    private var log: some View {
        if !operation.events.isEmpty {
            DisclosureGroup(isExpanded: $isLogExpanded) {
                ScrollView {
                    VStack(alignment: .leading, spacing: 2) {
                        ForEach(operation.events) { event in
                            HStack(alignment: .firstTextBaseline, spacing: 6) {
                                Text(event.phase.rawValue)
                                    .font(.caption.monospaced())
                                    .foregroundColor(.secondary)
                                if let message = event.message, !message.isEmpty {
                                    Text(message)
                                        .font(.caption.monospaced())
                                }
                                Spacer()
                            }
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .frame(maxHeight: 160)
                .padding(.top, 4)
            } label: {
                Text(L("Show details"))
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
    }

    // MARK: - Phase label

    private func humanPhase(_ phase: BridgeUpdatePhase) -> String {
        switch phase {
        case .idle: return L("Preparing…")
        case .checkingBridge: return L("Checking Bridge…")
        case .backingUpSettings: return L("Backing up settings…")
        case .verifyingUpdate: return L("Verifying update…")
        case .uploadingUpdate: return L("Uploading update…")
        case .installingUpdate: return L("Installing update…")
        case .restartingBridge: return L("Restarting Bridge…")
        case .reconnecting: return L("Reconnecting to Bridge…")
        case .verifyingBridge: return L("Verifying Bridge…")
        case .pendingVerification: return L("Pending verification…")
        case .done: return L("Update complete")
        case .failed: return L("Update failed")
        case .rolledBack: return L("Bridge rolled back")
        case .needsRecovery: return L("Bridge needs recovery")
        }
    }
}
