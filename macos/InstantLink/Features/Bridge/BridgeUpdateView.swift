import SwiftUI

/// Updates tab content. Owns a child `BridgeUpdateCoordinator` that walks
/// the bridge through preflight → upload → install → reconnect → mark-good
/// (or rollback). Reads paired-device + status from the parent
/// `BridgeControlCoordinator` so it stays in lock-step with discovery and
/// pairing.
struct BridgeUpdateView: View {
    @ObservedObject var coordinator: BridgeControlCoordinator
    @ObservedObject var updateCoordinator: BridgeUpdateCoordinator

    @State private var showRollbackConfirmation: Bool = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                if isUnpaired {
                    pairingRequiredCard
                } else {
                    statusCard
                    if let preflight = updateCoordinator.snapshot.preflight {
                        preflightCard(preflight: preflight)
                    }
                    if updateCoordinator.snapshot.operation != nil
                        || updateCoordinator.snapshot.lastResult != nil {
                        installCard
                    }
                    if isRollbackVisible {
                        rollbackCard
                    }
                }
                Spacer(minLength: 8)
            }
            .padding(16)
        }
        .onAppear {
            updateCoordinator.loadBundle(status: coordinator.snapshot.status)
        }
        .onChange(of: coordinator.snapshot.status) {
            updateCoordinator.loadBundle(status: coordinator.snapshot.status)
        }
    }

    // MARK: - Pairing gate

    private var isUnpaired: Bool {
        if case .paired = coordinator.snapshot.pairing { return false }
        return true
    }

    private var pairingRequiredCard: some View {
        BridgeCard(title: L("Updates")) {
            Text(L("Pair this Mac with the Bridge to manage updates."))
                .font(.callout)
                .foregroundColor(.secondary)
        }
    }

    // MARK: - Status card

    @ViewBuilder
    private var statusCard: some View {
        BridgeCard(title: L("Bridge software")) {
            switch updateCoordinator.snapshot.availability {
            case .upToDate(let version):
                upToDateStatus(version: version)
            case .updateAvailable(let current, let bundled):
                updateAvailableStatus(current: current, bundled: bundled)
            case .noBundle:
                noBundleStatus
            case .unknown:
                unknownStatus
            }
        }
    }

    private func upToDateStatus(version: String) -> some View {
        HStack(spacing: 10) {
            Image(systemName: "checkmark.seal.fill")
                .foregroundColor(.green)
                .imageScale(.large)
            VStack(alignment: .leading, spacing: 2) {
                Text(L("Up to date"))
                    .font(.callout.weight(.semibold))
                Text(String(format: L("Bridge is running v%@."), version))
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
            Spacer()
        }
    }

    private func updateAvailableStatus(current: String, bundled: String) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 10) {
                Image(systemName: "arrow.up.circle.fill")
                    .foregroundColor(.accentColor)
                    .imageScale(.large)
                VStack(alignment: .leading, spacing: 2) {
                    Text(L("Update available"))
                        .font(.callout.weight(.semibold))
                    Text(String(format: L("Bundled with this app: v%@ → v%@"), current, bundled))
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                Spacer()
            }
            HStack(spacing: 8) {
                Button {
                    Task { await runPreflight() }
                } label: {
                    if updateCoordinator.snapshot.isPreflightInFlight {
                        ProgressView().controlSize(.small)
                    } else {
                        Text(L("Run preflight"))
                    }
                }
                .buttonStyle(.borderedProminent)
                .disabled(isOperationActive || updateCoordinator.snapshot.isPreflightInFlight)
                if updateCoordinator.snapshot.preflight != nil {
                    Button(L("Refresh preflight")) {
                        Task { await runPreflight() }
                    }
                    .disabled(isOperationActive || updateCoordinator.snapshot.isPreflightInFlight)
                }
            }
        }
    }

    private var noBundleStatus: some View {
        HStack(spacing: 10) {
            Image(systemName: "shippingbox")
                .foregroundColor(.secondary)
                .imageScale(.large)
            Text(L("No update bundled with this version of InstantLink."))
                .font(.callout)
                .foregroundColor(.secondary)
            Spacer()
        }
    }

    private var unknownStatus: some View {
        HStack(spacing: 10) {
            ProgressView().controlSize(.small)
            Text(L("Checking for bundled update…"))
                .font(.callout)
                .foregroundColor(.secondary)
            Spacer()
        }
    }

    // MARK: - Preflight card

    private func preflightCard(preflight: BridgeUpdatePreflight) -> some View {
        BridgeCard(title: L("Preflight")) {
            VStack(alignment: .leading, spacing: 12) {
                BridgeUpdatePreflightView(preflight: preflight)
                Button {
                    Task { await runInstall() }
                } label: {
                    Text(L("Install update"))
                }
                .buttonStyle(.borderedProminent)
                .disabled(!preflight.allowed || isOperationActive)
            }
        }
    }

    // MARK: - Install card

    private var installCard: some View {
        BridgeCard(title: L("Install")) {
            VStack(alignment: .leading, spacing: 12) {
                if let operation = updateCoordinator.snapshot.operation {
                    BridgeUpdateProgressView(operation: operation)
                }
                if let result = updateCoordinator.snapshot.lastResult {
                    resultRow(for: result)
                }
            }
        }
    }

    @ViewBuilder
    private func resultRow(for result: BridgeUpdateSnapshot.Result) -> some View {
        switch result {
        case .succeeded(_, let newVersion):
            HStack(spacing: 10) {
                Image(systemName: "checkmark.circle.fill")
                    .foregroundColor(.green)
                Text(String(format: L("Update succeeded. Bridge is now running v%@."), newVersion))
                    .font(.callout)
                Spacer()
            }
        case .failed(let reason, _):
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: "exclamationmark.octagon.fill")
                    .foregroundColor(.red)
                VStack(alignment: .leading, spacing: 4) {
                    Text(L("Update failed"))
                        .font(.callout.weight(.semibold))
                        .foregroundColor(.red)
                    Text(reason)
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer()
            }
        case .rolledBack:
            HStack(spacing: 10) {
                Image(systemName: "arrow.uturn.backward.circle.fill")
                    .foregroundColor(.orange)
                Text(L("Bridge rolled back to the previous version."))
                    .font(.callout)
                Spacer()
            }
        }
    }

    // MARK: - Rollback card

    private var isRollbackVisible: Bool {
        coordinator.snapshot.status?.update?.previousVersion != nil
    }

    private var rollbackCard: some View {
        BridgeCard(title: L("Rollback")) {
            VStack(alignment: .leading, spacing: 8) {
                if let previous = coordinator.snapshot.status?.update?.previousVersion {
                    Text(String(format: L("Restore the previously installed version (v%@)."), previous))
                        .font(.callout)
                        .foregroundColor(.secondary)
                }
                Button {
                    showRollbackConfirmation = true
                } label: {
                    if updateCoordinator.snapshot.isRollbackInFlight {
                        ProgressView().controlSize(.small)
                    } else {
                        Text(L("Roll back to previous version"))
                    }
                }
                .disabled(isOperationActive || updateCoordinator.snapshot.isRollbackInFlight)
            }
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

    // MARK: - Actions

    private var isOperationActive: Bool {
        updateCoordinator.snapshot.operation != nil
    }

    private func currentDevice() -> BridgeDevice? {
        switch coordinator.snapshot.discovery {
        case .found(let device, _): return device
        case .lost(let device, _): return device
        case .searching: return nil
        }
    }

    private func runPreflight() async {
        guard let device = currentDevice() else { return }
        await updateCoordinator.refreshPreflight(device: device)
    }

    private func runInstall() async {
        guard let device = currentDevice() else { return }
        await updateCoordinator.runUpdate(device: device)
    }

    private func runRollback() async {
        guard let device = currentDevice() else { return }
        await updateCoordinator.rollback(device: device, reason: "user_initiated")
    }
}
