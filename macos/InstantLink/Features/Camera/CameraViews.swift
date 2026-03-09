import SwiftUI

struct CameraView: View {
    @EnvironmentObject var viewModel: ViewModel
    @State private var showFlash = false

    private var showsSimulatedFilmFrame: Bool {
        viewModel.printerModelTag != nil &&
        ((viewModel.cameraState == .viewfinder && viewModel.captureSession != nil) ||
         (viewModel.cameraState == .preview && viewModel.capturedImage != nil))
    }

    private var panelChromeColor: Color {
        showsSimulatedFilmFrame ? .clear : .secondary.opacity(0.18)
    }

    var body: some View {
        ZStack {
            AppPanelBackground(chromeColor: panelChromeColor)

            if viewModel.cameraState == .viewfinder {
                if let session = viewModel.captureSession {
                    let isFront = viewModel.selectedCamera?.position == .front
                    FilmFrameView(filmModel: viewModel.printerModelTag, isRotated: viewModel.filmOrientation == "rotated") {
                        if let ar = viewModel.orientedAspectRatio {
                            CameraPreviewView(session: session, isMirrored: isFront)
                                .scaleEffect(x: viewModel.isHorizontallyFlipped ? -1 : 1, y: 1)
                                .aspectRatio(ar, contentMode: .fill)
                                .overlay {
                                    OverlayCanvasView()
                                }
                                .clipped()
                        } else {
                            CameraPreviewView(session: session, isMirrored: isFront)
                                .scaleEffect(x: viewModel.isHorizontallyFlipped ? -1 : 1, y: 1)
                        }
                    }
                    .padding(4)

                    if let count = viewModel.timerCountdown, count > 0 {
                        Text("\(count)")
                            .font(.system(size: 72, weight: .bold, design: .rounded))
                            .foregroundColor(.white)
                            .shadow(color: .black.opacity(0.5), radius: 8)
                            .transition(.scale.combined(with: .opacity))
                            .animation(.easeInOut(duration: 0.3), value: count)
                    }
                } else {
                    VStack(spacing: 8) {
                        Image(systemName: "camera.badge.ellipsis")
                            .font(.largeTitle)
                            .foregroundColor(.secondary)
                        Text(L("No camera available"))
                            .font(.callout)
                            .foregroundColor(.secondary)
                    }
                }
            } else if let image = viewModel.capturedImage {
                FilmFrameView(filmModel: viewModel.printerModelTag, isRotated: viewModel.filmOrientation == "rotated") {
                    if let ar = viewModel.orientedAspectRatio {
                        Image(nsImage: image)
                            .resizable()
                            .aspectRatio(contentMode: .fill)
                            .scaleEffect(x: viewModel.isHorizontallyFlipped ? -1 : 1, y: 1)
                            .frame(maxWidth: .infinity, maxHeight: .infinity)
                            .aspectRatio(ar, contentMode: .fit)
                            .overlay {
                                OverlayCanvasView()
                            }
                            .clipped()
                    } else {
                        Image(nsImage: image)
                            .resizable()
                            .scaleEffect(x: viewModel.isHorizontallyFlipped ? -1 : 1, y: 1)
                            .aspectRatio(contentMode: .fit)
                    }
                }
                .padding(4)
            }
        }
        .overlay(showFlash ? Color.white.opacity(0.8) : Color.clear)
        .frame(minHeight: 120, maxHeight: .infinity)
        .animation(.easeInOut(duration: 0.22), value: viewModel.cameraState)
        .onChange(of: viewModel.cameraState) { newState in
            if newState == .preview {
                showFlash = true
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) {
                    withAnimation(.easeOut(duration: 0.2)) { showFlash = false }
                }
            }
        }
    }
}

struct CameraActionsView: View {
    @EnvironmentObject var viewModel: ViewModel

    var body: some View {
        VStack(spacing: 10) {
            if viewModel.cameraState == .viewfinder {
                HStack(spacing: 8) {
                    if viewModel.availableCameras.count > 1 {
                        Picker(L("Camera"), selection: Binding(
                            get: { viewModel.selectedCamera?.uniqueID ?? "" },
                            set: { id in
                                if let device = viewModel.availableCameras.first(where: { $0.uniqueID == id }) {
                                    viewModel.switchCamera(to: device)
                                }
                            }
                        )) {
                            ForEach(viewModel.availableCameras, id: \.uniqueID) { device in
                                Text(device.localizedName).tag(device.uniqueID)
                            }
                        }
                        .labelsHidden()
                    }

                    Picker(L("Timer"), selection: $viewModel.timerMode) {
                        Text(L("Off")).tag(0)
                        Text("2s").tag(2)
                        Text("10s").tag(10)
                    }
                    .pickerStyle(.segmented)
                    .labelsHidden()
                    .frame(maxWidth: 140)

                    if viewModel.printerAspectRatio != nil {
                        HStack(spacing: 8) {
                            Button {
                                viewModel.filmOrientation = viewModel.filmOrientation == "default" ? "rotated" : "default"
                            } label: {
                                HStack(spacing: 4) {
                                    Image(systemName: viewModel.filmOrientation == "default" ? "rectangle.portrait" : "rectangle")
                                    Image(systemName: "arrow.triangle.2.circlepath")
                                        .font(.system(size: 8, weight: .semibold))
                                }
                                .font(.callout)
                            }
                            .help(L("Film Orientation"))

                            Button {
                                viewModel.toggleHorizontalFlip()
                            } label: {
                                HStack(spacing: 4) {
                                    Image(systemName: "arrow.left.and.right")
                                    Text(L("Flip"))
                                }
                                .font(.callout)
                                .foregroundColor(viewModel.isHorizontallyFlipped ? .accentColor : .primary)
                            }
                            .buttonStyle(.bordered)
                            .help(L("Flip"))
                        }
                    }
                }

                if viewModel.timerCountdown != nil {
                    Button {
                        viewModel.cancelTimer()
                    } label: {
                        HStack {
                            Image(systemName: "xmark")
                            Text(L("Cancel"))
                        }
                        .frame(maxWidth: .infinity)
                    }
                    .controlSize(.large)
                } else {
                    HStack(spacing: 10) {
                        Button {
                            viewModel.autoPrintAfterCapture = false
                            viewModel.captureWithTimer()
                        } label: {
                            HStack {
                                Image(systemName: "camera.shutter.button")
                                Text(L("Capture"))
                            }
                            .frame(maxWidth: .infinity)
                        }
                        .controlSize(.large)
                        .disabled(viewModel.captureSession == nil)

                        Button {
                            viewModel.autoPrintAfterCapture = true
                            viewModel.captureWithTimer()
                        } label: {
                            HStack {
                                Image(systemName: "printer.fill")
                                Text(L("Capture & Print"))
                            }
                            .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.borderedProminent)
                        .controlSize(.large)
                        .disabled(viewModel.captureSession == nil || viewModel.printerName == nil)
                    }
                }
            } else {
                HStack(spacing: 10) {
                    Button {
                        viewModel.retakePhoto()
                    } label: {
                        HStack {
                            Image(systemName: "arrow.counterclockwise")
                            Text(L("Retake"))
                        }
                        .frame(maxWidth: .infinity)
                    }
                    .controlSize(.large)

                    Button {
                        if viewModel.commitCapture() {
                            viewModel.showImageEditor = true
                        }
                    } label: {
                        HStack {
                            Image(systemName: "slider.horizontal.3")
                            Text(L("Edit Image"))
                        }
                        .frame(maxWidth: .infinity)
                    }
                    .controlSize(.large)

                    Button {
                        if viewModel.commitCapture() {
                            Task { await viewModel.printSelectedImage() }
                        }
                    } label: {
                        HStack {
                            Image(systemName: "printer.fill")
                            Text(L("Print"))
                        }
                        .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
                }
            }
        }
    }
}
