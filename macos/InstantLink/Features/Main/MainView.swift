import AppKit
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
                    MainActionsView(openEditor: { viewModel.showImageEditor = true })
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
                            ProgressView()
                                .controlSize(.regular)
                            Text(viewModel.pairingStatus)
                                .font(.callout)
                                .foregroundColor(.secondary)
                            VStack(alignment: .leading, spacing: 4) {
                                Label(L("Make sure your printer is turned on"), systemImage: "1.circle")
                                Label(L("Press the button to enable Bluetooth"), systemImage: "2.circle")
                                Label(L("Keep the printer nearby"), systemImage: "3.circle")
                            }
                            .font(.caption)
                            .foregroundColor(.secondary)
                            .padding(.vertical, 4)
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
        .onChange(of: viewModel.queue.count) { newCount in
            syncQueueStripVisibility(for: newCount)
        }
        .onReceive(NotificationCenter.default.publisher(for: .findPrinter)) { _ in
            NSApplication.shared.activate(ignoringOtherApps: true)
            for window in NSApplication.shared.windows where window.canBecomeMain {
                window.makeKeyAndOrderFront(nil)
            }
            viewModel.showPrinterPicker = true
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
    }

    private func syncQueueStripVisibility(for newCount: Int, force: Bool = false) {
        defer { lastQueueCount = newCount }

        guard viewModel.captureMode == .file else {
            if force {
                isQueueStripVisible = false
            }
            return
        }

        if newCount == 0 {
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
            HeaderCapsule {
                printerIdentityControl
                HeaderDivider()
                printerPickerControl
            }
            .layoutPriority(1)

            Spacer(minLength: 0)

            HeaderCapsule {
                captureModeControl
                if viewModel.captureMode == .file && !viewModel.queue.isEmpty {
                    HeaderDivider()
                    queueToggleControl
                }
            }

            HeaderCapsule {
                batteryStatusControl
                HeaderDivider()
                filmStatusControl
            }

            HeaderCapsule {
                settingsControl
            }
        }
    }

    private var compactConnectedHeader: some View {
        HStack(spacing: 12) {
            printerIdentityControl
            printerPickerControl

            Spacer()

            captureModeControl

            if viewModel.captureMode == .file && !viewModel.queue.isEmpty {
                queueToggleControl
            }

            batteryStatusControl
            filmStatusControl
            settingsControl
        }
    }

    private var printerIdentityControl: some View {
        Button {
            if let bleId = viewModel.printerName, let profile = viewModel.printerProfiles[bleId] {
                viewModel.editingProfile = profile
                viewModel.showProfileEditor = true
            }
        } label: {
            HStack(spacing: 4) {
                Circle()
                    .fill(.green)
                    .frame(width: 8, height: 8)
                Text(viewModel.currentPrinterDisplayName ?? L("Connected"))
                    .font(.caption)
                    .fontWeight(.medium)
                    .lineLimit(1)
                if let tag = viewModel.printerModelTag {
                    Text(tag)
                        .font(.system(size: 9, weight: .semibold))
                        .foregroundColor(.white)
                        .padding(.horizontal, 5)
                        .padding(.vertical, 1)
                        .background(Capsule().fill(.secondary))
                }
            }
        }
        .buttonStyle(.plain)
    }

    private var printerPickerControl: some View {
        Button {
            viewModel.showPrinterPicker = true
        } label: {
            Image(systemName: "chevron.down")
                .font(.system(size: 8, weight: .semibold))
                .foregroundColor(.secondary)
                .frame(width: 16, height: 16)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    private var captureModeControl: some View {
        Picker("", selection: Binding(
            get: { viewModel.captureMode },
            set: { viewModel.requestCaptureModeChange(to: $0) }
        )) {
            Image(systemName: "photo.on.rectangle").tag(CaptureMode.file)
            Image(systemName: "camera").tag(CaptureMode.camera)
        }
        .pickerStyle(.segmented)
        .labelsHidden()
        .frame(width: 60)
        .onChange(of: viewModel.captureMode) { newMode in
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

    private var queueToggleControl: some View {
        Button {
            withAnimation(.easeInOut(duration: 0.2)) {
                isQueueStripVisible.toggle()
            }
        } label: {
            HStack(spacing: 5) {
                Image(systemName: isQueueStripVisible ? "square.stack.3d.up.fill" : "square.stack.3d.up")
                    .font(.caption)
                Text("\(viewModel.queue.count)")
                    .font(.caption2)
                    .fontWeight(.semibold)
                    .monospacedDigit()
            }
            .foregroundColor(isQueueStripVisible ? .accentColor : .secondary)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
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
                .font(.caption)
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
}
