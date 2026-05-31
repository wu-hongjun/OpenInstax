import SwiftUI

/// Labeled "Bridge" button shown in the top-right of the main Print view when
/// the Bridge is discovered. Replaces an earlier bare rotating-icon indicator
/// that users said was too subtle — pairing the sync glyph with a "Bridge"
/// label matches the Mac/iOS system-status idiom (think iOS Settings "AirDrop"
/// or the Wi-Fi pill in Control Center) and makes the affordance obviously
/// tappable.
///
/// States:
/// - **paired + connected**: bordered button with rotating sync icon at
///   6 s/rev + "Bridge" text, accent color. Tap opens Bridge Control.
/// - **discovered but unpaired**: same button, icon static (not rotating),
///   secondary color, "Set up Bridge" tooltip. Tap opens Bridge Control so
///   the user can complete pairing.
/// - **searching / lost / never found**: hidden. Matches the discovery
///   banner's silent-when-paired behavior so the top-right cluster stays
///   uncluttered until there's something useful to show.
struct BridgeConnectionIndicator: View {
    let snapshot: BridgeControlSnapshot
    let onTap: () -> Void

    @State private var rotation: Double = 0

    /// True iff the Bridge is both paired and currently discovered. Drives
    /// the rotating-icon "alive" animation.
    private var isConnected: Bool {
        guard case .paired = snapshot.pairing else { return false }
        guard case .found = snapshot.discovery else { return false }
        return true
    }

    /// True when the Bridge has been discovered but the user has not yet
    /// completed pairing. The button stays visible so the user has a clear
    /// affordance to finish setup; the icon doesn't rotate because nothing
    /// is "live" yet.
    private var isDiscoveredButUnpaired: Bool {
        guard case .found = snapshot.discovery else { return false }
        if case .paired = snapshot.pairing { return false }
        return true
    }

    var body: some View {
        if isConnected {
            connectedButton
        } else if isDiscoveredButUnpaired {
            unpairedButton
        }
    }

    private var connectedButton: some View {
        Button(action: onTap) {
            HStack(spacing: 4) {
                Image(systemName: "arrow.triangle.2.circlepath")
                    .font(.system(size: 11, weight: .medium))
                    .rotationEffect(.degrees(rotation))
                Text(L("Bridge"))
                    .font(.callout)
            }
            .foregroundColor(.accentColor)
            .accessibilityLabel(Text(L("Bridge connected")))
        }
        .buttonStyle(.bordered)
        .controlSize(.small)
        .help(L("Bridge connected — open Bridge Control"))
        .onAppear { startRotation() }
        .transition(.opacity)
    }

    private var unpairedButton: some View {
        Button(action: onTap) {
            HStack(spacing: 4) {
                Image(systemName: "arrow.triangle.2.circlepath")
                    .font(.system(size: 11, weight: .medium))
                Text(L("Bridge"))
                    .font(.callout)
            }
            .foregroundColor(.secondary)
            .accessibilityLabel(Text(L("Set up Bridge")))
        }
        .buttonStyle(.bordered)
        .controlSize(.small)
        .help(L("Set up Bridge"))
        .transition(.opacity)
    }

    private func startRotation() {
        // 6 s per revolution: slow enough to read as "alive", not "frantic".
        // Matches the iOS sync icon cadence.
        rotation = 0
        withAnimation(.linear(duration: 6).repeatForever(autoreverses: false)) {
            rotation = 360
        }
    }
}
