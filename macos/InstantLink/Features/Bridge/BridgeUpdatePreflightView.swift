import SwiftUI

/// Renders a preflight result as a checklist. Each check row shows a
/// pass / warning / fail icon, a human-readable label derived from the
/// check `name`, and (when populated) an inline message explaining the
/// finding. Footer reports "Ready to install" or "Resolve N issues
/// before installing" depending on the overall `allowed` flag.
struct BridgeUpdatePreflightView: View {
    let preflight: BridgeUpdatePreflight

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(Array(preflight.checks.enumerated()), id: \.offset) { _, check in
                row(for: check)
            }
            Divider().padding(.vertical, 4)
            footer
        }
    }

    private func row(for check: BridgeUpdatePreflightCheck) -> some View {
        HStack(alignment: .top, spacing: 10) {
            statusIcon(for: check.status)
                .frame(width: 18)
                .padding(.top, 2)
            VStack(alignment: .leading, spacing: 2) {
                Text(humanLabel(for: check.name))
                    .font(.callout)
                if let message = check.message, !message.isEmpty {
                    Text(message)
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer()
        }
    }

    @ViewBuilder
    private func statusIcon(for status: BridgeUpdatePreflightCheckStatus) -> some View {
        switch status {
        case .pass:
            Image(systemName: "checkmark.circle.fill")
                .foregroundColor(.green)
        case .warning:
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundColor(.yellow)
        case .fail:
            Image(systemName: "xmark.octagon.fill")
                .foregroundColor(.red)
        }
    }

    private var footer: some View {
        HStack(spacing: 8) {
            if preflight.allowed {
                Image(systemName: "checkmark.circle")
                    .foregroundColor(.green)
                Text(L("Ready to install"))
                    .font(.callout.weight(.semibold))
                    .foregroundColor(.green)
            } else {
                Image(systemName: "exclamationmark.octagon")
                    .foregroundColor(.red)
                Text(blockedSummary)
                    .font(.callout.weight(.semibold))
                    .foregroundColor(.red)
            }
            Spacer()
        }
    }

    private var blockedSummary: String {
        let failing = preflight.checks.filter { $0.status == .fail }.count
        if failing == 0 {
            return L("Resolve outstanding issues before installing")
        }
        let template = L("Resolve %d issues before installing")
        return String(format: template, failing)
    }

    /// Map raw check names (e.g. `backup_available`) to human-readable
    /// labels (e.g. "Backup available"). Falls back to the raw name when
    /// the registry does not contain an entry.
    private func humanLabel(for name: String) -> String {
        switch name {
        case "service_health": return L("Bridge service is healthy")
        case "backup_available": return L("Recovery backup is available")
        case "package": return L("Update package is valid")
        case "battery": return L("Bridge battery is sufficient")
        case "disk_space": return L("Disk space is sufficient")
        case "network": return L("Network is stable")
        case "ble_state": return L("BLE state is ready")
        case "queue_empty": return L("Print queue is empty")
        default:
            return name
                .replacingOccurrences(of: "_", with: " ")
                .capitalized
        }
    }
}
