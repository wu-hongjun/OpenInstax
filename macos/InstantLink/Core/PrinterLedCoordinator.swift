import Foundation

@MainActor
final class PrinterLedCoordinator {
    private enum Pattern: UInt8 {
        case solid = 0
        case blink = 1
        case breathe = 2
    }

    private enum State: Equatable {
        case off
        case sending
        case success
        case failure
    }

    private var state: State = .off
    private var completionTask: Task<Void, Never>?

    func beginSending(using ffi: InstantLinkFFI) async {
        completionTask?.cancel()
        completionTask = nil
        guard state != .sending else { return }
        state = .sending
        _ = await ffi.setLed(r: 31, g: 111, b: 235, pattern: Pattern.breathe.rawValue)
    }

    func signalSuccess(using ffi: InstantLinkFFI) async {
        await complete(using: ffi, state: .success, r: 38, g: 222, b: 109)
    }

    func signalFailure(using ffi: InstantLinkFFI) async {
        await complete(using: ffi, state: .failure, r: 230, g: 57, b: 70)
    }

    func turnOff(using ffi: InstantLinkFFI) async {
        completionTask?.cancel()
        completionTask = nil
        guard state != .off else { return }
        state = .off
        _ = await ffi.ledOff()
    }

    func turnOffSync(using ffi: InstantLinkFFI) {
        completionTask?.cancel()
        completionTask = nil
        state = .off
        ffi.ledOffSync()
    }

    private func complete(using ffi: InstantLinkFFI, state: State, r: UInt8, g: UInt8, b: UInt8) async {
        completionTask?.cancel()
        self.state = state
        _ = await ffi.setLed(r: r, g: g, b: b, pattern: Pattern.blink.rawValue)
        completionTask = Task {
            try? await Task.sleep(nanoseconds: 1_100_000_000)
            guard !Task.isCancelled else { return }
            await self.turnOff(using: ffi)
        }
    }
}
