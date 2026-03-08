import SwiftUI

@main
struct OpenInstaxApp: App {
    @StateObject private var viewModel = ViewModel()

    var body: some Scene {
        // Menu bar extra for quick access
        MenuBarExtra("OpenInstax", systemImage: "printer.fill") {
            MenuBarView(viewModel: viewModel)
        }
        .menuBarExtraStyle(.window)

        // Full window for detailed controls
        WindowGroup {
            MainView(viewModel: viewModel)
        }
        .windowStyle(.hiddenTitleBar)
    }
}

// MARK: - View Model

class ViewModel: ObservableObject {
    let cli = OpenInstaxCLI()

    @Published var isConnected = false
    @Published var printerName: String?
    @Published var printerModel: String?
    @Published var battery: Int = 0
    @Published var filmRemaining: Int = 0
    @Published var printCount: Int = 0

    @Published var isPrinting = false
    @Published var isScanning = false

    func refreshStatus() async {
        let status = await cli.status()
        await MainActor.run {
            isConnected = status?.connected ?? false
            printerName = status?.name
            printerModel = status?.model
            battery = status?.battery ?? 0
            filmRemaining = status?.filmRemaining ?? 0
            printCount = status?.printCount ?? 0
        }
    }

    func printImage(path: String, quality: Int = 97, fit: String = "crop") async {
        await MainActor.run { isPrinting = true }
        let _ = await cli.printImage(path: path, quality: quality, fit: fit)
        await refreshStatus()
        await MainActor.run { isPrinting = false }
    }
}

// MARK: - Menu Bar View

struct MenuBarView: View {
    @ObservedObject var viewModel: ViewModel

    var body: some View {
        VStack(spacing: 12) {
            // Status header
            HStack {
                Image(systemName: viewModel.isConnected ? "printer.fill" : "printer")
                    .foregroundColor(viewModel.isConnected ? .green : .secondary)
                Text(viewModel.printerName ?? "No printer")
                    .font(.headline)
                Spacer()
            }

            if viewModel.isConnected {
                // Battery and film info
                HStack {
                    Label("\(viewModel.battery)%", systemImage: "battery.100")
                    Spacer()
                    Label("\(viewModel.filmRemaining) shots", systemImage: "film")
                }
                .font(.caption)

                Divider()

                // Drag and drop print zone
                DropZoneView(viewModel: viewModel)
            }

            Divider()

            Button("Refresh") {
                Task { await viewModel.refreshStatus() }
            }

            Button("Quit") {
                NSApplication.shared.terminate(nil)
            }
        }
        .padding()
        .frame(width: 280)
        .task {
            await viewModel.refreshStatus()
        }
    }
}

// MARK: - Drop Zone View

struct DropZoneView: View {
    @ObservedObject var viewModel: ViewModel
    @State private var isTargeted = false

    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 12)
                .strokeBorder(style: StrokeStyle(lineWidth: 2, dash: [8]))
                .foregroundColor(isTargeted ? .accentColor : .secondary)
                .frame(height: 80)

            if viewModel.isPrinting {
                ProgressView("Printing...")
            } else {
                VStack {
                    Image(systemName: "photo.on.rectangle.angled")
                        .font(.title2)
                    Text("Drop image to print")
                        .font(.caption)
                }
                .foregroundColor(.secondary)
            }
        }
        .onDrop(of: [.fileURL], isTargeted: $isTargeted) { providers in
            guard let provider = providers.first else { return false }
            _ = provider.loadObject(ofClass: URL.self) { url, _ in
                guard let url = url else { return }
                Task {
                    await viewModel.printImage(path: url.path)
                }
            }
            return true
        }
    }
}

// MARK: - Main Window View

struct MainView: View {
    @ObservedObject var viewModel: ViewModel

    var body: some View {
        VStack(spacing: 20) {
            // Header
            HStack {
                Text("OpenInstax")
                    .font(.largeTitle)
                    .bold()
                Spacer()
                if viewModel.isConnected {
                    HStack(spacing: 4) {
                        Circle()
                            .fill(.green)
                            .frame(width: 8, height: 8)
                        Text(viewModel.printerName ?? "Connected")
                            .font(.caption)
                    }
                }
            }

            if viewModel.isConnected {
                // Printer status cards
                HStack(spacing: 16) {
                    StatusCard(
                        icon: "battery.100",
                        title: "Battery",
                        value: "\(viewModel.battery)%"
                    )
                    StatusCard(
                        icon: "film",
                        title: "Film",
                        value: "\(viewModel.filmRemaining) left"
                    )
                    StatusCard(
                        icon: "printer",
                        title: "Prints",
                        value: "\(viewModel.printCount)"
                    )
                }

                Divider()

                // Print zone
                DropZoneView(viewModel: viewModel)
                    .frame(height: 200)
            } else {
                // Not connected
                VStack(spacing: 12) {
                    Image(systemName: "printer.dotmatrix")
                        .font(.system(size: 48))
                        .foregroundColor(.secondary)
                    Text("No printer connected")
                        .font(.title2)
                    Text("Turn on your Instax Link printer and click Refresh")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                .frame(maxHeight: .infinity)
            }

            Spacer()

            // Footer
            HStack {
                Button("Refresh") {
                    Task { await viewModel.refreshStatus() }
                }
                Spacer()
                Text("OpenInstax v0.1.0")
                    .font(.caption2)
                    .foregroundColor(.secondary)
            }
        }
        .padding(24)
        .frame(minWidth: 400, minHeight: 300)
        .task {
            await viewModel.refreshStatus()
        }
    }
}

// MARK: - Status Card

struct StatusCard: View {
    let icon: String
    let title: String
    let value: String

    var body: some View {
        VStack(spacing: 4) {
            Image(systemName: icon)
                .font(.title2)
            Text(value)
                .font(.headline)
            Text(title)
                .font(.caption)
                .foregroundColor(.secondary)
        }
        .frame(maxWidth: .infinity)
        .padding()
        .background(RoundedRectangle(cornerRadius: 8).fill(.quaternary))
    }
}
