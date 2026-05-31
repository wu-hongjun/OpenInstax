import SwiftUI

enum BridgeControlTab: String, CaseIterable, Identifiable {
    case overview
    case settings
    case updates
    case backup
    case diagnostics

    var id: String { rawValue }

    var label: String {
        switch self {
        case .overview: return L("Overview")
        case .settings: return L("Settings")
        case .updates: return L("Updates")
        case .backup: return L("Backup")
        case .diagnostics: return L("Diagnostics")
        }
    }

    var systemImage: String {
        switch self {
        case .overview: return "gauge.with.dots.needle.bottom.50percent"
        case .settings: return "slider.horizontal.3"
        case .updates: return "arrow.triangle.2.circlepath"
        case .backup: return "externaldrive"
        case .diagnostics: return "stethoscope"
        }
    }
}

/// Top-level window for the Bridge Control surface. Hosts the Overview,
/// Settings, Updates, Backup, and Diagnostics tabs plus the recovery banner
/// that surfaces when the bridge management service is unreachable.
struct BridgeControlWindow: View {
    @ObservedObject var coordinator: BridgeControlCoordinator
    @State private var selectedTab: BridgeControlTab = .overview

    var body: some View {
        NavigationSplitView {
            List(BridgeControlTab.allCases, selection: $selectedTab) { tab in
                NavigationLink(value: tab) {
                    Label(tab.label, systemImage: tab.systemImage)
                }
            }
            .navigationTitle(L("Bridge Control"))
            .frame(minWidth: 180)
        } detail: {
            VStack(spacing: 0) {
                toolbar
                Divider()
                BridgeRecoveryView(
                    coordinator: coordinator,
                    diagnosticsCoordinator: coordinator.diagnosticsCoordinator
                )
                content
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                Divider()
                statusBar
            }
        }
        .frame(minWidth: 720, minHeight: 520)
        .onAppear {
            coordinator.onWindowVisibilityChanged(true)
        }
        .onDisappear {
            coordinator.onWindowVisibilityChanged(false)
        }
    }

    // MARK: - Sections

    private var toolbar: some View {
        HStack(spacing: 10) {
            chip
            Spacer()
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
    }

    private var chip: some View {
        let snapshot = coordinator.snapshot
        let title: String
        let icon: String
        let tint: Color
        switch snapshot.discovery {
        case .found(let device, _):
            title = "\(device.deviceID) — v\(device.softwareVersion)"
            icon = "antenna.radiowaves.left.and.right"
            tint = .accentColor
        case .lost(let device, _):
            title = device.map { "\($0.deviceID) — \(L("Disconnected"))" } ?? L("Bridge disconnected")
            icon = "antenna.radiowaves.left.and.right.slash"
            tint = .secondary
        case .searching:
            title = L("Looking for Bridge")
            icon = "magnifyingglass"
            tint = .secondary
        }
        return HStack(spacing: 6) {
            Image(systemName: icon)
            Text(title)
                .font(.callout)
        }
        .foregroundColor(tint)
        .padding(.horizontal, 10)
        .padding(.vertical, 5)
        .background(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .fill(tint.opacity(0.10))
        )
    }

    @ViewBuilder
    private var content: some View {
        switch selectedTab {
        case .overview:
            BridgeOverviewView(coordinator: coordinator)
        case .settings:
            BridgeSettingsView(coordinator: coordinator)
        case .updates:
            BridgeUpdateView(
                coordinator: coordinator,
                updateCoordinator: coordinator.updateCoordinator
            )
        case .backup:
            BridgeBackupView(
                coordinator: coordinator,
                backupCoordinator: coordinator.backupCoordinator
            )
        case .diagnostics:
            BridgeDiagnosticsView(
                coordinator: coordinator,
                diagnosticsCoordinator: coordinator.diagnosticsCoordinator
            )
        }
    }

    private var statusBar: some View {
        HStack(spacing: 8) {
            if let updated = coordinator.snapshot.lastUpdated {
                Text(lastUpdatedLabel(date: updated))
                    .font(.caption2)
                    .foregroundColor(.secondary)
            } else {
                Text(L("Not updated yet"))
                    .font(.caption2)
                    .foregroundColor(.secondary)
            }
            Spacer()
            Button {
                Task { await coordinator.refreshNow() }
            } label: {
                Image(systemName: "arrow.clockwise")
            }
            .buttonStyle(.borderless)
            .help(L("Refresh"))
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 6)
    }

    private func lastUpdatedLabel(date: Date) -> String {
        let formatter = RelativeDateTimeFormatter()
        formatter.unitsStyle = .short
        return L("Updated") + " " + formatter.localizedString(for: date, relativeTo: Date())
    }
}
