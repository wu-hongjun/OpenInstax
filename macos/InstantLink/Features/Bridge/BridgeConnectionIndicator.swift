import SwiftUI

/// Small sync-style indicator shown in the top-right of the main Print view
/// when the Bridge is paired and connected to this Mac. Mirrors the iOS
/// "computer linked" affordance — a subtle, continuously-rotating sync glyph
/// that confirms the link is live without competing for attention.
///
/// States:
/// - paired + connected → show, rotate slowly
/// - any other state    → hidden
///
/// Tap → opens the Bridge Control window so the user can manage settings.
struct BridgeConnectionIndicator: View {
    let snapshot: BridgeControlSnapshot
    let onTap: () -> Void

    @State private var rotation: Double = 0

    private var isConnected: Bool {
        guard case .paired = snapshot.pairing else { return false }
        guard case .found = snapshot.discovery else { return false }
        return true
    }

    var body: some View {
        if isConnected {
            Button(action: onTap) {
                Image(systemName: "arrow.triangle.2.circlepath")
                    .font(.system(size: 12, weight: .medium))
                    .foregroundColor(.accentColor)
                    .rotationEffect(.degrees(rotation))
                    .accessibilityLabel(Text(L("Bridge connected")))
            }
            .buttonStyle(.plain)
            .help(L("Bridge connected — open Bridge Control"))
            .onAppear { startRotation() }
            .transition(.opacity)
        }
    }

    private func startRotation() {
        // 6 s per revolution: slow enough to read as "alive", not "frantic".
        // matches the iOS sync icon cadence.
        rotation = 0
        withAnimation(.linear(duration: 6).repeatForever(autoreverses: false)) {
            rotation = 360
        }
    }
}
