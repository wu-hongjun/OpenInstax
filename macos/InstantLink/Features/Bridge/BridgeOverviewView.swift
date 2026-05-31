import SwiftUI

struct BridgeOverviewView: View {
    @ObservedObject var coordinator: BridgeControlCoordinator
    @State private var showPairingSheet = false
    @State private var showRollbackConfirmation = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                switch coordinator.snapshot.discovery {
                case .searching:
                    searchingCard
                case .lost(let device, _):
                    disconnectedCard(device: device)
                case .found(let device, _):
                    deviceCard(device: device)
                }

                if isUnpaired {
                    pairCTACard
                }

                if let status = coordinator.snapshot.status {
                    networkCard(status: status)
                    systemCard(stats: status.systemStats)
                    printerCard(printer: status.printer)
                    uploadsCard(status: status)
                    if status.update?.previousVersion != nil {
                        rollbackCard(previousVersion: status.update?.previousVersion ?? "")
                    }
                }

                if let error = coordinator.snapshot.lastError {
                    errorCard(error: error)
                }

                Spacer(minLength: 8)
            }
            .padding(16)
        }
        .sheet(isPresented: $showPairingSheet) {
            BridgePairingView(coordinator: coordinator, isPresented: $showPairingSheet)
        }
        .confirmationDialog(
            L("Roll back the Bridge to the previous version?"),
            isPresented: $showRollbackConfirmation,
            titleVisibility: .visible
        ) {
            Button(L("Roll back"), role: .destructive) {
                Task { await runRollback() }
            }
            Button(L("Cancel"), role: .cancel) {}
        } message: {
            Text(L("The Bridge will restart. Active uploads may be interrupted."))
        }
    }

    private func rollbackCard(previousVersion: String) -> some View {
        BridgeCard(title: L("Rollback")) {
            VStack(alignment: .leading, spacing: 8) {
                Text(String(format: L("Restore the previously installed version (v%@)."), previousVersion))
                    .font(.callout)
                    .foregroundColor(.secondary)
                Button(L("Roll back to previous version")) {
                    showRollbackConfirmation = true
                }
            }
        }
    }

    private func runRollback() async {
        guard let device = currentDevice() else { return }
        await coordinator.updateCoordinator.rollback(device: device, reason: "user_initiated")
    }

    private func currentDevice() -> BridgeDevice? {
        switch coordinator.snapshot.discovery {
        case .found(let device, _): return device
        case .lost(let device, _): return device
        case .searching: return nil
        }
    }

    // MARK: - State helpers

    private var isUnpaired: Bool {
        switch coordinator.snapshot.pairing {
        case .paired: return false
        default: return true
        }
    }

    // MARK: - Cards

    private var searchingCard: some View {
        BridgeCard(title: L("Looking for Bridge")) {
            HStack(spacing: 10) {
                ProgressView().controlSize(.small)
                Text(L("Plug your InstantLink Bridge into this Mac via USB to begin."))
                    .font(.callout)
                    .foregroundColor(.secondary)
            }
        }
    }

    private func disconnectedCard(device: BridgeDevice?) -> some View {
        BridgeCard(title: L("Bridge disconnected")) {
            VStack(alignment: .leading, spacing: 6) {
                if let device {
                    Text(device.deviceID)
                        .font(.callout.weight(.semibold))
                }
                Text(L("Reconnect the Bridge to continue."))
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
    }

    private func deviceCard(device: BridgeDevice) -> some View {
        BridgeCard(title: L("Device")) {
            VStack(alignment: .leading, spacing: 6) {
                infoRow(label: L("Device ID"), value: device.deviceID)
                infoRow(label: L("Name"), value: device.displayName)
                infoRow(label: L("Software version"), value: "v\(device.softwareVersion)")
                infoRow(label: L("API"), value: device.apiVersion)
                if let endpoint = device.endpointURL {
                    infoRow(label: L("Address"), value: endpoint.absoluteString)
                }
            }
        }
    }

    private var pairCTACard: some View {
        BridgeCard(title: L("Pair this Bridge")) {
            VStack(alignment: .leading, spacing: 8) {
                Text(L("Pair this Mac to read live status and change settings."))
                    .font(.callout)
                    .foregroundColor(.secondary)
                Button(L("Pair…")) {
                    showPairingSheet = true
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.regular)
            }
        }
    }

    private func networkCard(status: BridgeStatus) -> some View {
        BridgeCard(title: L("Network")) {
            VStack(alignment: .leading, spacing: 6) {
                if let network = status.network {
                    infoRow(label: L("Mode"), value: network.label)
                    if let address = network.address {
                        infoRow(label: L("Address"), value: address)
                    }
                    infoRow(
                        label: L("Status"),
                        value: network.connected ? L("Connected") : L("Disconnected")
                    )
                } else {
                    Text(L("No network info available."))
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                infoRow(
                    label: L("Upload path"),
                    value: uploadModeLabel(status.activeUploadMode)
                )
            }
        }
    }

    private func systemCard(stats: BridgeSystemStats?) -> some View {
        BridgeCard(title: L("System")) {
            if let stats {
                VStack(alignment: .leading, spacing: 6) {
                    infoRow(label: L("CPU"), value: stats.formattedCPU)
                    infoRow(label: L("Memory"), value: stats.formattedMemory)
                    infoRow(label: L("Storage"), value: stats.formattedStorage)
                    infoRow(label: L("SoC temp"), value: stats.formattedTemperature)
                }
            } else {
                // Older bridges that don't ship the system_stats block decode
                // as nil. Render a single explanatory row so the empty card
                // looks intentional rather than broken — matching the
                // discovery banner's "silent-when-paired" pattern.
                Text(L("System stats not available on this Bridge"))
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
    }

    private func printerCard(printer: BridgePrinterStatus?) -> some View {
        BridgeCard(title: L("Printer")) {
            if let printer {
                VStack(alignment: .leading, spacing: 6) {
                    infoRow(
                        label: L("Name"),
                        value: printer.displayName ?? L("Unknown printer")
                    )
                    if let model = printer.model {
                        infoRow(label: L("Model"), value: model)
                    }
                    if let film = printer.filmRemaining {
                        infoRow(label: L("Film"), value: "\(film)")
                    }
                    if let battery = printer.batteryPercent {
                        infoRow(label: L("Battery"), value: "\(battery)%")
                    }
                    infoRow(
                        label: L("Status"),
                        value: printer.connected ? L("Connected") : L("Disconnected")
                    )
                }
            } else {
                Text(L("No printer paired."))
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
    }

    private func uploadsCard(status: BridgeStatus) -> some View {
        BridgeCard(title: L("Recent uploads")) {
            if let last = status.lastUpload {
                VStack(alignment: .leading, spacing: 4) {
                    Text(last.filename ?? L("Untitled"))
                        .font(.callout)
                    Text(last.status)
                        .font(.caption)
                        .foregroundColor(.secondary)
                    if let received = last.receivedAt {
                        Text("\(L("Received")) \(received)")
                            .font(.caption2)
                            .foregroundColor(.secondary)
                    }
                }
            } else {
                Text(L("No recent uploads."))
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
    }

    private func errorCard(error: BridgeErrorPayload) -> some View {
        BridgeCard(title: L("Bridge error")) {
            Text(error.message)
                .font(.caption)
                .foregroundColor(.red)
        }
    }

    private func infoRow(label: String, value: String) -> some View {
        HStack(alignment: .firstTextBaseline) {
            Text(label)
                .font(.caption)
                .foregroundColor(.secondary)
                .frame(width: 110, alignment: .leading)
            Text(value)
                .font(.callout)
                .textSelection(.enabled)
                .lineLimit(2)
            Spacer()
        }
    }

    private func uploadModeLabel(_ mode: BridgeUploadMode) -> String {
        switch mode {
        case .bridgeWiFi: return L("Bridge Wi-Fi")
        case .sameWiFi: return L("Same Wi-Fi")
        case .usbDebug: return L("USB debug")
        case .disabled: return L("Disabled")
        case .unknown: return L("Unknown")
        }
    }
}

/// Small styled card used by the overview tab.
struct BridgeCard<Content: View>: View {
    let title: String
    let content: () -> Content

    init(title: String, @ViewBuilder content: @escaping () -> Content) {
        self.title = title
        self.content = content
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title)
                .font(.headline)
            content()
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .fill(Color.secondary.opacity(0.08))
        )
    }
}
