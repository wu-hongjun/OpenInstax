import AppKit
import AVFoundation
import SwiftUI

struct MainView: View {
    @EnvironmentObject var viewModel: ViewModel
    @State private var isQueueStripVisible = false
    @State private var lastQueueCount = 0

    var body: some View {
        VStack(spacing: 0) {
            if let error = viewModel.updateError {
                HStack(spacing: 6) {
                    Image(systemName: "exclamationmark.triangle")
                        .font(.caption)
                    Text(L("update_failed", error))
                        .font(.caption)
                        .lineLimit(1)
                    Spacer()
                    Button(L("Dismiss")) {
                        viewModel.updateError = nil
                    }
                    .font(.caption)
                    .buttonStyle(.plain)
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 6)
                .background(Color.red.opacity(0.15))
                .transition(.move(edge: .top).combined(with: .opacity))
            } else if viewModel.isUpdating {
                HStack(spacing: 6) {
                    if viewModel.updateProgress >= 1.0 {
                        ProgressView()
                            .controlSize(.small)
                        Text(L("Installing update..."))
                            .font(.caption)
                    } else {
                        Image(systemName: "arrow.down.circle")
                            .font(.caption)
                        Text(L("Downloading update..."))
                            .font(.caption)
                        Spacer()
                        ProgressView(value: viewModel.updateProgress)
                            .frame(width: 60)
                        Text("\(Int(viewModel.updateProgress * 100))%")
                            .font(.caption)
                            .monospacedDigit()
                    }
                    Spacer()
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 6)
                .background(Color.blue.opacity(0.1))
                .transition(.move(edge: .top).combined(with: .opacity))
            } else if let version = viewModel.updateAvailable {
                HStack(spacing: 6) {
                    Image(systemName: "arrow.up.circle.fill")
                        .font(.caption)
                        .foregroundColor(.blue)
                    Text(L("update_available_version", version))
                        .font(.caption)
                    Spacer()
                    Button(L("Update Now")) {
                        viewModel.performUpdate()
                    }
                    .font(.caption)
                    .buttonStyle(.borderedProminent)
                    .controlSize(.small)
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 6)
                .background(Color.blue.opacity(0.1))
                .transition(.move(edge: .top).combined(with: .opacity))
            }

            if let message = viewModel.statusMessage {
                HStack(spacing: 8) {
                    Image(systemName: statusBannerIcon)
                        .font(.caption)
                    Text(message)
                        .font(.caption)
                        .lineLimit(2)
                    Spacer()
                    if viewModel.isStatusMessagePersistent {
                        Button(L("Dismiss")) {
                            viewModel.dismissStatusMessage()
                        }
                        .font(.caption)
                        .buttonStyle(.plain)
                    }
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 6)
                .background(statusBannerBackground)
                .transition(.move(edge: .top).combined(with: .opacity))
            }

            if viewModel.isConnected {
                ViewThatFits(in: .horizontal) {
                    groupedConnectedHeader
                    compactConnectedHeader
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 10)

                Divider()

                if viewModel.captureMode == .file {
                    MainPreviewView(openEditor: { viewModel.showImageEditor = true })
                        .padding(.horizontal, 14)
                        .padding(.top, 16)
                        .layoutPriority(-1)
                        .transition(.asymmetric(insertion: .opacity.combined(with: .scale(scale: 0.98)), removal: .opacity))
                } else {
                    CameraView()
                        .padding(.horizontal, 14)
                        .padding(.top, 16)
                        .layoutPriority(-1)
                        .transition(.asymmetric(insertion: .opacity.combined(with: .scale(scale: 0.98)), removal: .opacity))
                }

                if viewModel.captureMode == .file && isQueueStripVisible {
                    QueueStripView()
                        .padding(.horizontal, 14)
                        .padding(.top, 8)
                        .transition(.move(edge: .bottom).combined(with: .opacity))
                }

                Spacer(minLength: 0)

                if viewModel.captureMode == .file {
                    MainActionsView(
                        openEditor: { viewModel.showImageEditor = true },
                        isQueueStripVisible: $isQueueStripVisible
                    )
                        .padding(.horizontal, 14)
                        .padding(.vertical, 12)
                        .transition(.move(edge: .bottom).combined(with: .opacity))
                } else {
                    CameraActionsView()
                        .padding(.horizontal, 14)
                        .padding(.vertical, 12)
                        .transition(.move(edge: .bottom).combined(with: .opacity))
                }
            } else {
                ZStack(alignment: .topTrailing) {
                    Button {
                        viewModel.showSettings = true
                    } label: {
                        Image(systemName: "gearshape")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                    .buttonStyle(.plain)
                    .padding(10)

                    VStack(spacing: 16) {
                        Spacer()

                        if viewModel.isPairing {
                            PairingChecklistCard(
                                title: viewModel.pairingStatus,
                                steps: pairingChecklistSteps
                            )
                            if !viewModel.isConnectingSpecificPrinter {
                                VStack(alignment: .leading, spacing: 4) {
                                    Label(L("Make sure your printer is turned on"), systemImage: "1.circle")
                                    Label(L("Press the button to enable Bluetooth"), systemImage: "2.circle")
                                    Label(L("Keep the printer nearby"), systemImage: "3.circle")
                                }
                                .font(.caption)
                                .foregroundColor(.secondary)
                                .padding(.vertical, 4)
                            }
                            Button(L("Cancel")) {
                                viewModel.stopPairing()
                            }
                            .controlSize(.large)
                        } else if viewModel.hasSearchedOnce {
                            Image(systemName: "printer.dotmatrix")
                                .font(.system(size: 40))
                                .foregroundColor(.secondary)
                            Text(L("No printer found"))
                                .font(.headline)
                            VStack(alignment: .leading, spacing: 4) {
                                Label(L("Make sure your printer is turned on"), systemImage: "1.circle")
                                Label(L("Press the button to enable Bluetooth"), systemImage: "2.circle")
                                Label(L("Keep the printer nearby"), systemImage: "3.circle")
                            }
                            .font(.caption)
                            .foregroundColor(.secondary)
                            .padding(.vertical, 4)
                            Button(L("Try Again")) {
                                viewModel.startPairing()
                            }
                            .controlSize(.large)
                        } else {
                            Image(systemName: "printer.dotmatrix")
                                .font(.system(size: 40))
                                .foregroundColor(.secondary)
                            Text(L("Connect to your printer"))
                                .font(.headline)
                            VStack(alignment: .leading, spacing: 4) {
                                Label(L("Turn on your Instax printer"), systemImage: "1.circle")
                                Label(L("Press the button to enable Bluetooth"), systemImage: "2.circle")
                            }
                            .font(.caption)
                            .foregroundColor(.secondary)
                            .padding(.vertical, 4)
                            Button(L("Find Printer")) {
                                viewModel.startPairing()
                            }
                            .buttonStyle(.borderedProminent)
                            .controlSize(.large)
                        }

                        Spacer()
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.horizontal, 14)
                }
            }
        }
        .frame(minWidth: 240, idealWidth: 260, minHeight: 380)
        .navigationTitle("InstantLink v\(Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "0.0.0")")
        .sheet(isPresented: $viewModel.showPrinterPicker) {
            PrinterPickerSheet()
                .environmentObject(viewModel)
        }
        .sheet(isPresented: $viewModel.showProfileSheet) {
            PrinterProfileSheet(isPostPairing: true)
                .environmentObject(viewModel)
        }
        .sheet(isPresented: $viewModel.showProfileEditor) {
            PrinterProfileSheet(isPostPairing: false)
                .environmentObject(viewModel)
        }
        .sheet(isPresented: $viewModel.showSettings) {
            SettingsView()
                .environmentObject(viewModel)
        }
        .sheet(isPresented: $viewModel.showImageEditor) {
            ImageEditorView()
                .environmentObject(viewModel)
        }
        .confirmationDialog(
            L("Discard captured photo?"),
            isPresented: $viewModel.showCameraDiscardConfirmation,
            titleVisibility: .visible
        ) {
            Button(L("Remove"), role: .destructive) {
                viewModel.confirmPendingCaptureModeChange()
            }
            Button(L("Cancel"), role: .cancel) {
                viewModel.cancelPendingCaptureModeChange()
            }
        }
        .onAppear {
            lastQueueCount = viewModel.queue.count
            syncQueueStripVisibility(for: viewModel.queue.count, force: true)
            if !viewModel.isConnected && !viewModel.isPairing {
                viewModel.startPairing()
            }
            Task { await viewModel.checkForUpdates() }
        }
        .onChange(of: viewModel.queue.count) { _, newCount in
            syncQueueStripVisibility(for: newCount)
        }
        .onReceive(NotificationCenter.default.publisher(for: .findPrinter)) { _ in
            NSApplication.shared.activate(ignoringOtherApps: true)
            for window in NSApplication.shared.windows where window.canBecomeMain {
                window.makeKeyAndOrderFront(nil)
            }
            viewModel.showPrinterPicker = true
            viewModel.scanNearby()
        }
        .onReceive(NotificationCenter.default.publisher(for: .refreshStatus)) { _ in
            Task { await viewModel.refreshStatus() }
        }
        .onReceive(NotificationCenter.default.publisher(for: .openSettings)) { _ in
            viewModel.showSettings = true
        }
        .onReceive(NotificationCenter.default.publisher(for: .checkForUpdates)) { _ in
            Task { await viewModel.checkForUpdates() }
        }
        .onDisappear {
            if viewModel.captureMode == .camera {
                viewModel.stopCameraSession()
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didResignActiveNotification)) { _ in
            if let session = viewModel.captureSession {
                DispatchQueue.global(qos: .userInitiated).async {
                    session.stopRunning()
                }
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in
            if viewModel.captureMode == .camera, let session = viewModel.captureSession, !session.isRunning {
                DispatchQueue.global(qos: .userInitiated).async {
                    session.startRunning()
                }
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: AVCaptureDevice.wasConnectedNotification)) { _ in
            viewModel.discoverCameras(ensureSession: viewModel.captureMode == .camera)
        }
        .onReceive(NotificationCenter.default.publisher(for: AVCaptureDevice.wasDisconnectedNotification)) { _ in
            viewModel.discoverCameras(ensureSession: viewModel.captureMode == .camera)
        }
    }

    private func syncQueueStripVisibility(for newCount: Int, force: Bool = false) {
        defer { lastQueueCount = newCount }

        guard viewModel.captureMode == .file else {
            if force {
                isQueueStripVisible = false
            }
            return
        }

        if newCount <= 1 {
            withAnimation(.easeInOut(duration: 0.2)) {
                isQueueStripVisible = false
            }
            return
        }

        if force {
            isQueueStripVisible = newCount > 1
            return
        }

        if lastQueueCount <= 1 && newCount > 1 {
            withAnimation(.easeInOut(duration: 0.2)) {
                isQueueStripVisible = true
            }
        }
    }

    private var groupedConnectedHeader: some View {
        HStack(spacing: 10) {
            printerIdentityControl
                .layoutPriority(2)

            Spacer(minLength: 0)

            captureModeControl

            HStack(spacing: 8) {
                batteryStatusControl
                HeaderDivider()
                filmStatusControl
            }

            settingsControl
        }
    }

    private var compactConnectedHeader: some View {
        HStack(spacing: 12) {
            printerIdentityControl
                .layoutPriority(2)

            Spacer()

            captureModeControl

            batteryStatusControl
            filmStatusControl
            settingsControl
        }
    }

    private var printerIdentityControl: some View {
        Button {
            viewModel.handlePrinterIdentityAction()
        } label: {
            HStack(spacing: 4) {
                Image(systemName: printerStatusSymbolName)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(printerStatusColor)
                Text(viewModel.currentPrinterDisplayName ?? L("Connected"))
                    .font(.callout)
                    .fontWeight(.medium)
                    .lineLimit(1)
                if let tag = viewModel.printerModelTag {
                    Text(tag)
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundColor(.white)
                        .padding(.horizontal, 5)
                        .padding(.vertical, 1)
                        .background(Capsule().fill(.secondary))
                }
            }
        }
        .buttonStyle(.plain)
    }

    private var captureModeControl: some View {
        Picker("", selection: Binding(
            get: { viewModel.captureMode },
            set: { viewModel.requestCaptureModeChange(to: $0) }
        )) {
            Label {
                Text(L("Printer"))
                    .font(.callout.weight(.medium))
            } icon: {
                Image(systemName: "printer")
            }
            .tag(CaptureMode.file)

            Label {
                Text(L("Camera"))
                    .font(.callout.weight(.medium))
            } icon: {
                Image(systemName: "camera")
            }
            .tag(CaptureMode.camera)
        }
        .pickerStyle(.segmented)
        .controlSize(.regular)
        .labelsHidden()
        .frame(width: 180)
        .onChange(of: viewModel.captureMode) { _, newMode in
            if newMode == .camera {
                viewModel.requestCameraAccessAndStart()
                withAnimation(.easeInOut(duration: 0.2)) {
                    isQueueStripVisible = false
                }
            } else {
                viewModel.cancelTimer()
                viewModel.stopCameraSession()
                viewModel.cameraState = .viewfinder
                viewModel.capturedImage = nil
                syncQueueStripVisibility(for: viewModel.queue.count, force: true)
            }
        }
    }

    private var batteryStatusControl: some View {
        Button {
            Task { await viewModel.refreshStatus() }
        } label: {
            StatusItem(
                icon: viewModel.isCharging ? "battery.100.bolt" : "battery.100",
                value: viewModel.isCharging ? L("Charging") : L("battery_percent", viewModel.battery)
            )
        }
        .buttonStyle(.plain)
        .disabled(viewModel.isRefreshing)
    }

    private var filmStatusControl: some View {
        Button {
            Task { await viewModel.refreshStatus() }
        } label: {
            StatusItem(icon: "film", value: L("film_remaining", viewModel.filmRemaining))
        }
        .buttonStyle(.plain)
        .disabled(viewModel.isRefreshing)
    }

    private var settingsControl: some View {
        Button {
            viewModel.showSettings = true
        } label: {
            Image(systemName: "gearshape")
                .font(.callout)
                .foregroundColor(.secondary)
        }
        .buttonStyle(.plain)
    }

    private var statusBannerIcon: String {
        switch viewModel.statusMessageTone {
        case .info: return "info.circle"
        case .success: return "checkmark.circle.fill"
        case .warning: return "exclamationmark.triangle.fill"
        case .error: return "xmark.octagon.fill"
        }
    }

    private var statusBannerBackground: Color {
        switch viewModel.statusMessageTone {
        case .info: return Color.blue.opacity(0.1)
        case .success: return Color.green.opacity(0.12)
        case .warning: return Color.orange.opacity(0.14)
        case .error: return Color.red.opacity(0.14)
        }
    }

    private var printerStatusColor: Color {
        switch viewModel.printerStatusIndicatorState {
        case .disconnected:
            return .secondary
        case .connecting:
            return .orange
        case .refreshing:
            return .blue
        case .ready:
            return .green
        case .busy:
            return .blue
        case .warning:
            return .orange
        case .error:
            return .red
        }
    }

    private var printerStatusSymbolName: String {
        switch viewModel.printerStatusIndicatorState {
        case .disconnected:
            return "circle.slash.fill"
        case .connecting:
            return "dot.radiowaves.left.and.right"
        case .refreshing:
            return "arrow.clockwise.circle.fill"
        case .ready:
            return "circle.fill"
        case .busy:
            return "clock.fill"
        case .warning:
            return "exclamationmark.circle.fill"
        case .error:
            return "xmark.octagon.fill"
        }
    }

    private var pairingChecklistSteps: [PairingChecklistStep] {
        let currentStage = viewModel.pairingConnectionStage
        let currentIndex = pairingChecklistIndex(for: currentStage)

        return [
            PairingChecklistStep(
                title: L("pairing_stage_scanning"),
                detail: nil,
                state: checklistState(for: 0, currentIndex: currentIndex, stage: currentStage)
            ),
            PairingChecklistStep(
                title: L("pairing_stage_found", viewModel.pairingConnectionStageDetail ?? viewModel.selectedPrinter ?? L("Printer")),
                detail: nil,
                state: checklistState(for: 1, currentIndex: currentIndex, stage: currentStage)
            ),
            PairingChecklistStep(
                title: L("pairing_stage_opening_bluetooth"),
                detail: nil,
                state: checklistState(for: 2, currentIndex: currentIndex, stage: currentStage)
            ),
            PairingChecklistStep(
                title: L("pairing_stage_setting_up_services"),
                detail: nil,
                state: checklistState(for: 3, currentIndex: currentIndex, stage: currentStage)
            ),
            PairingChecklistStep(
                title: L("pairing_stage_reading_info"),
                detail: nil,
                state: checklistState(for: 4, currentIndex: currentIndex, stage: currentStage)
            ),
        ]
    }

    private func pairingChecklistIndex(for stage: ConnectionStage?) -> Int {
        switch stage {
        case .scanStarted, .scanFinished, nil:
            return viewModel.pairingPhase == .connecting ? 2 : 0
        case .deviceMatched:
            return 1
        case .bleConnecting:
            return 2
        case .servicesDiscovering, .characteristicsResolving, .notificationsSubscribing:
            return 3
        case .modelDetecting, .statusFetching:
            return 4
        case .connected:
            return 4
        case .failed, .unknown:
            return max(0, viewModel.isConnectingSpecificPrinter ? 2 : 0)
        }
    }

    private func checklistState(
        for index: Int,
        currentIndex: Int,
        stage: ConnectionStage?
    ) -> PairingChecklistStep.State {
        if case .failed = stage, index == currentIndex {
            return .failed
        }
        if index < currentIndex {
            return .completed
        }
        if index == currentIndex && viewModel.isPairing {
            return .active
        }
        return .pending
    }
}

private struct PairingChecklistStep: Identifiable {
    enum State {
        case pending
        case active
        case completed
        case failed
    }

    let id = UUID()
    let title: String
    let detail: String?
    let state: State
}

private struct PairingChecklistCard: View {
    let title: String
    let steps: [PairingChecklistStep]

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 10) {
                ZStack {
                    Circle()
                        .fill(Color.accentColor.opacity(0.10))
                        .frame(width: 34, height: 34)
                    Image(systemName: "printer.dotmatrix")
                        .font(.headline)
                        .foregroundStyle(Color.accentColor)
                }

                VStack(alignment: .leading, spacing: 2) {
                    Text(title)
                        .font(.headline)
                    Text("InstantLink")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            VStack(alignment: .leading, spacing: 8) {
                ForEach(steps) { step in
                    PairingChecklistRow(step: step)
                }
            }
        }
        .frame(maxWidth: 360, alignment: .leading)
        .padding(.horizontal, 16)
        .padding(.vertical, 14)
        .background(
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .fill(.regularMaterial.opacity(0.72))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .stroke(Color.white.opacity(0.10), lineWidth: 1)
        )
    }
}

private struct PairingChecklistRow: View {
    let step: PairingChecklistStep

    var body: some View {
        HStack(spacing: 10) {
            statusIcon
                .frame(width: 18, height: 18)

            VStack(alignment: .leading, spacing: 1) {
                Text(step.title)
                    .font(.callout)
                    .foregroundStyle(step.state == .pending ? .secondary : .primary)
                if let detail = step.detail {
                    Text(detail)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            Spacer(minLength: 0)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .background(rowBackground)
        .overlay(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .stroke(rowBorderColor, lineWidth: 1)
        )
        .animation(.easeInOut(duration: 0.18), value: step.state)
    }

    @ViewBuilder
    private var statusIcon: some View {
        switch step.state {
        case .pending:
            Image(systemName: "circle")
                .foregroundStyle(.tertiary)
        case .active:
            ProgressView()
                .controlSize(.small)
        case .completed:
            Image(systemName: "checkmark.circle.fill")
                .foregroundStyle(.green)
        case .failed:
            Image(systemName: "xmark.circle.fill")
                .foregroundStyle(.red)
        }
    }

    private var rowBackground: some ShapeStyle {
        switch step.state {
        case .pending:
            return AnyShapeStyle(Color.clear)
        case .active:
            return AnyShapeStyle(Color.accentColor.opacity(0.08))
        case .completed:
            return AnyShapeStyle(Color.green.opacity(0.08))
        case .failed:
            return AnyShapeStyle(Color.red.opacity(0.10))
        }
    }

    private var rowBorderColor: Color {
        switch step.state {
        case .pending:
            return Color.white.opacity(0.06)
        case .active:
            return Color.accentColor.opacity(0.20)
        case .completed:
            return Color.green.opacity(0.18)
        case .failed:
            return Color.red.opacity(0.20)
        }
    }
}
