import SwiftUI

/// Passive banner shown in the Print main view when a Bridge is connected.
/// Stays silent when the Bridge is working (paired + present) and surfaces a
/// quiet CTA only when the user must act (unpaired) or briefly hints when the
/// Bridge has just dropped off.
///
/// Plan 038 phase A.1 adds a transient "Bridge connected and authorized" toast
/// for USB-physical auto-trust events. The toast fades after 5 s, after which
/// the banner returns to its normal silent-when-paired state.
struct BridgeDiscoveryBanner: View {
    let snapshot: BridgeControlSnapshot
    let onOpen: () -> Void

    private static let autoTrustToastDuration: TimeInterval = 5.0

    /// Drives the auto-trust toast countdown. We hold `now` in `@State` so the
    /// view can re-render itself once the 5 s window has elapsed.
    @State private var now: Date = Date()

    var body: some View {
        Group {
            if let event = snapshot.lastAutoTrustEvent,
               now.timeIntervalSince(event) < Self.autoTrustToastDuration {
                autoTrustStrip
                    .task(id: event) {
                        // Wake up once the toast window has elapsed so the view
                        // can transition back to its silent state.
                        let remaining = Self.autoTrustToastDuration - now.timeIntervalSince(event)
                        guard remaining > 0 else { return }
                        let nanos = UInt64(remaining * 1_000_000_000)
                        try? await Task.sleep(nanoseconds: nanos)
                        now = Date()
                    }
            } else {
                switch snapshot.discovery {
                case .searching:
                    EmptyView()
                case .found(_, let medium):
                    if case .paired = snapshot.pairing {
                        // Bridge is working. Nothing for the user to do — stay quiet.
                        EmptyView()
                    } else if medium == .usb {
                        // USB auto-trust will land momentarily (sub-second). Stay
                        // quiet during the brief request; the success toast above
                        // owns the user feedback.
                        EmptyView()
                    } else {
                        setupStrip
                    }
                case .lost:
                    disconnectedStrip
                }
            }
        }
    }

    // MARK: - Strips

    private var setupStrip: some View {
        HStack(spacing: 8) {
            Image(systemName: "link.badge.plus")
                .font(.caption)
                .foregroundColor(.accentColor)
            Text(L("InstantLink Bridge ready to set up"))
                .font(.caption)
                .lineLimit(1)
            Spacer(minLength: 4)
            Button(L("Set up")) {
                onOpen()
            }
            .font(.caption)
            .buttonStyle(.borderless)
            .controlSize(.small)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .background(Color.accentColor.opacity(0.10))
        .transition(.move(edge: .top).combined(with: .opacity))
    }

    private var disconnectedStrip: some View {
        HStack(spacing: 8) {
            Image(systemName: "antenna.radiowaves.left.and.right.slash")
                .font(.caption)
                .foregroundColor(.secondary)
            Text(L("Bridge disconnected"))
                .font(.caption)
                .foregroundColor(.secondary)
                .lineLimit(1)
            Spacer(minLength: 4)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .background(Color.secondary.opacity(0.08))
        .transition(.move(edge: .top).combined(with: .opacity))
    }

    private var autoTrustStrip: some View {
        HStack(spacing: 8) {
            Image(systemName: "checkmark.shield")
                .font(.caption)
                .foregroundColor(.green)
            Text(L("Bridge connected and authorized"))
                .font(.caption)
                .lineLimit(1)
            Spacer(minLength: 4)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .background(Color.green.opacity(0.10))
        .transition(.move(edge: .top).combined(with: .opacity))
    }
}
