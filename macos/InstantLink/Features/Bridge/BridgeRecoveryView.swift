import SwiftUI

/// Recovery banner that appears at the top of the Bridge Control window when
/// the bridge's management service is unreachable. Verbatim copy from plan
/// 029: "Bridge management service unavailable. Uploads may still work.
/// Restart the Bridge service or reconnect with USB debug."
struct BridgeRecoveryView: View {
    @ObservedObject var coordinator: BridgeControlCoordinator
    @ObservedObject var diagnosticsCoordinator: BridgeDiagnosticsCoordinator

    @State private var showLCDInstructions: Bool = false

    var body: some View {
        switch diagnosticsCoordinator.snapshot.recovery {
        case .ok:
            EmptyView()
        case .checking:
            checkingBanner
        case .managementUnavailable:
            unavailableBanner
        case .restartInFlight:
            restartInFlightBanner
        case .recovered(let when):
            recoveredBanner(at: when)
        case .unrecoverable(let reason, _):
            unrecoverableBanner(reason: reason)
        }
    }

    // MARK: - Banners

    private var checkingBanner: some View {
        recoveryBanner(
            icon: "stethoscope",
            tint: .secondary,
            title: L("Checking Bridge management service…"),
            body: nil,
            buttons: EmptyView()
        )
    }

    private var unavailableBanner: some View {
        recoveryBanner(
            icon: "exclamationmark.triangle.fill",
            tint: .orange,
            title: L("Bridge management service unavailable."),
            body: L("Uploads may still work. Restart the Bridge service or reconnect with USB debug."),
            buttons: HStack(spacing: 8) {
                Button(L("Restart management service")) {
                    Task { await runRestart() }
                }
                .buttonStyle(.borderedProminent)
                Button(L("Show LCD instructions")) {
                    showLCDInstructions = true
                }
            }
        )
        .sheet(isPresented: $showLCDInstructions) {
            lcdInstructionsSheet
        }
    }

    private var restartInFlightBanner: some View {
        recoveryBanner(
            icon: "arrow.triangle.2.circlepath",
            tint: .secondary,
            title: L("Restarting Bridge management service…"),
            body: L("This may take a few seconds. The banner will dismiss when the service is back."),
            buttons: EmptyView()
        )
    }

    private func recoveredBanner(at _: Date) -> some View {
        recoveryBanner(
            icon: "checkmark.circle.fill",
            tint: .green,
            title: L("Bridge management service is back."),
            body: nil,
            buttons: Button(L("Dismiss")) {
                diagnosticsCoordinator.dismissRecovery()
            }
        )
    }

    private func unrecoverableBanner(reason: String) -> some View {
        recoveryBanner(
            icon: "xmark.octagon.fill",
            tint: .red,
            title: L("Could not restart Bridge management service."),
            body: reason,
            buttons: HStack(spacing: 8) {
                Button(L("Show LCD instructions")) {
                    showLCDInstructions = true
                }
                Button(L("Dismiss")) {
                    diagnosticsCoordinator.dismissRecovery()
                }
            }
        )
        .sheet(isPresented: $showLCDInstructions) {
            lcdInstructionsSheet
        }
    }

    private var lcdInstructionsSheet: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(L("Power-cycle the Bridge"))
                .font(.headline)
            Text(L("If the Bridge's management service won't restart from the Mac, power-cycle the Bridge:"))
                .font(.callout)
                .foregroundColor(.secondary)
            VStack(alignment: .leading, spacing: 6) {
                instructionRow(number: 1, text: L("Long-press the X306 power button to turn the Bridge off."))
                instructionRow(number: 2, text: L("Wait 5 seconds."))
                instructionRow(number: 3, text: L("Short-press the power button to turn the Bridge back on."))
                instructionRow(number: 4, text: L("Watch the LCD for the Bridge Wi-Fi or USB IP labels — those mean the service is back."))
            }
            HStack {
                Spacer()
                Button(L("Done")) {
                    showLCDInstructions = false
                }
                .buttonStyle(.borderedProminent)
                .keyboardShortcut(.defaultAction)
            }
        }
        .padding(20)
        .frame(minWidth: 380)
    }

    private func instructionRow(number: Int, text: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text("\(number).")
                .font(.callout.weight(.semibold))
                .foregroundColor(.secondary)
                .frame(width: 16, alignment: .trailing)
            Text(text)
                .font(.callout)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private func recoveryBanner<Buttons: View>(
        icon: String,
        tint: Color,
        title: String,
        body: String?,
        buttons: Buttons
    ) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: icon)
                .foregroundColor(tint)
                .imageScale(.large)
            VStack(alignment: .leading, spacing: 6) {
                Text(title)
                    .font(.callout.weight(.semibold))
                if let body {
                    Text(body)
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                buttons
            }
            Spacer()
        }
        .padding(12)
        .background(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .fill(tint.opacity(0.08))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .stroke(tint.opacity(0.30), lineWidth: 1)
        )
        .padding(.horizontal, 14)
        .padding(.vertical, 6)
    }

    // MARK: - Helpers

    private func currentDevice() -> BridgeDevice? {
        switch coordinator.snapshot.discovery {
        case .found(let device, _): return device
        case .lost(let device, _): return device
        case .searching: return nil
        }
    }

    private func runRestart() async {
        guard let device = currentDevice() else { return }
        await diagnosticsCoordinator.attemptRecovery(device: device)
    }
}
