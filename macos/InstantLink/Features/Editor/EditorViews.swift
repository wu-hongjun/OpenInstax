import SwiftUI
import UniformTypeIdentifiers

struct ImageEditorView: View {
    @EnvironmentObject var viewModel: ViewModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text(L("Edit Image"))
                    .font(.headline)
                Spacer()
                Button(L("Done")) {
                    dismiss()
                }
                .keyboardShortcut(.return, modifiers: [])
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 10)

            Divider()

            if viewModel.selectedImage != nil {
                HSplitView {
                    EditorPreviewView()
                        .padding(12)
                        .frame(minWidth: 620, idealWidth: 820)
                        .layoutPriority(1)

                    EditorSidebarView()
                }
            } else {
                VStack(spacing: 12) {
                    Spacer()
                    Image(systemName: "photo.on.rectangle.angled")
                        .font(.system(size: 40))
                        .foregroundColor(.secondary)
                    Text(L("No image selected"))
                        .font(.headline)
                        .foregroundColor(.secondary)
                    Button(L("Open File")) { viewModel.selectImage() }
                        .buttonStyle(.bordered)
                        .controlSize(.small)
                        .frame(minHeight: 36)
                    Spacer()
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .frame(minWidth: 1080, idealWidth: 1280, minHeight: 720, idealHeight: 820)
    }
}

struct EditorPreviewView: View {
    @EnvironmentObject var viewModel: ViewModel
    @State private var isTargeted = false
    @GestureState private var dragDelta: CGSize = .zero
    @GestureState private var magnifyDelta: CGFloat = 1.0
    @State private var localFrameSize: CGSize = .zero

    private var showsSimulatedFilmFrame: Bool {
        viewModel.selectedImage != nil && viewModel.printerModelTag != nil
    }

    private var panelChromeColor: Color {
        if viewModel.selectedImage == nil {
            return isTargeted ? .accentColor.opacity(0.55) : .secondary.opacity(0.22)
        }
        return showsSimulatedFilmFrame ? .clear : .secondary.opacity(0.18)
    }

    var body: some View {
        ZStack {
            AppPanelBackground(chromeColor: panelChromeColor, dashed: viewModel.selectedImage == nil)

            if let image = viewModel.selectedImage {
                FilmFrameView(filmModel: viewModel.printerModelTag, isRotated: viewModel.filmOrientation == "rotated") {
                    if viewModel.fitMode == "crop", let ar = viewModel.orientedAspectRatio {
                        Color.clear
                            .aspectRatio(ar, contentMode: .fit)
                            .background(
                                GeometryReader { geo in
                                    Color.clear.preference(key: CropFrameSizeKey.self, value: geo.size)
                                }
                            )
                            .onPreferenceChange(CropFrameSizeKey.self) { size in
                                localFrameSize = size
                                viewModel.cropFrameSize = size
                            }
                            .overlay(
                                Image(nsImage: image)
                                    .resizable()
                                    .aspectRatio(contentMode: .fill)
                                    .scaleEffect(
                                        x: viewModel.isHorizontallyFlipped ? -effectiveZoom : effectiveZoom,
                                        y: effectiveZoom
                                    )
                                    .offset(effectiveOffset(imageSize: image.size))
                                    .rotationEffect(.degrees(Double(viewModel.rotationAngle)))
                            )
                            .overlay {
                                OverlayCanvasView(editable: true)
                            }
                            .clipped()
                            .contentShape(Rectangle())
                            .gesture(
                                DragGesture()
                                    .updating($dragDelta) { value, state, _ in
                                        state = value.translation
                                    }
                                    .onEnded { value in
                                        let raw = CGSize(
                                            width: viewModel.cropOffset.width + value.translation.width,
                                            height: viewModel.cropOffset.height + value.translation.height
                                        )
                                        viewModel.cropOffset = viewModel.clampedCropOffset(
                                            raw: raw,
                                            imageSize: image.size,
                                            frameSize: localFrameSize,
                                            zoom: viewModel.cropZoom
                                        )
                                    }
                            )
                            .simultaneousGesture(
                                MagnificationGesture()
                                    .updating($magnifyDelta) { value, state, _ in
                                        state = value
                                    }
                                    .onEnded { value in
                                        viewModel.setCropZoom(viewModel.cropZoom * value)
                                    }
                            )
                    } else if viewModel.fitMode == "contain", let ar = viewModel.orientedAspectRatio {
                        Color.white
                            .aspectRatio(ar, contentMode: .fit)
                            .overlay(
                                Image(nsImage: image)
                                    .resizable()
                                    .aspectRatio(contentMode: .fit)
                                    .scaleEffect(x: viewModel.isHorizontallyFlipped ? -1 : 1, y: 1)
                                    .rotationEffect(.degrees(Double(viewModel.rotationAngle)))
                            )
                            .overlay {
                                OverlayCanvasView(editable: true)
                            }
                            .clipped()
                    } else {
                        Image(nsImage: image)
                            .resizable()
                            .aspectRatio(contentMode: .fit)
                            .scaleEffect(x: viewModel.isHorizontallyFlipped ? -1 : 1, y: 1)
                            .rotationEffect(.degrees(Double(viewModel.rotationAngle)))
                            .overlay {
                                OverlayCanvasView(editable: true)
                            }
                    }
                }
                .padding(4)
            }
        }
        .frame(minHeight: 250, idealHeight: 350)
        .animation(.easeInOut(duration: 0.22), value: viewModel.selectedImage != nil)
        .onDrop(of: [.fileURL], isTargeted: $isTargeted) { providers in
            guard !providers.isEmpty else { return false }
            for provider in providers {
                _ = provider.loadObject(ofClass: URL.self) { url, _ in
                    guard let url = url else { return }
                    DispatchQueue.main.async { viewModel.addImages(from: [url]) }
                }
            }
            return true
        }
    }

    private var effectiveZoom: CGFloat {
        min(max(viewModel.cropZoom * magnifyDelta, ViewModel.minCropZoom), ViewModel.maxCropZoom)
    }

    private func effectiveOffset(imageSize: CGSize) -> CGSize {
        let raw = CGSize(
            width: viewModel.cropOffset.width + dragDelta.width,
            height: viewModel.cropOffset.height + dragDelta.height
        )
        return viewModel.clampedCropOffset(
            raw: raw,
            imageSize: imageSize,
            frameSize: localFrameSize,
            zoom: effectiveZoom
        )
    }
}

struct AccordionSection<Content: View>: View {
    let title: String
    let icon: String
    @State private var isExpanded: Bool
    @ViewBuilder let content: () -> Content

    init(_ title: String, icon: String, expanded: Bool = true, @ViewBuilder content: @escaping () -> Content) {
        self.title = title
        self.icon = icon
        self._isExpanded = State(initialValue: expanded)
        self.content = content
    }

    var body: some View {
        VStack(spacing: 0) {
            Button {
                withAnimation(.easeInOut(duration: 0.2)) { isExpanded.toggle() }
            } label: {
                HStack {
                    Image(systemName: icon)
                        .frame(width: 16)
                    Text(title)
                        .font(.callout)
                        .fontWeight(.medium)
                    Spacer()
                    Image(systemName: "chevron.right")
                        .rotationEffect(.degrees(isExpanded ? 90 : 0))
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                .padding(.vertical, 8)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            if isExpanded {
                VStack(spacing: 8) {
                    content()
                }
                .padding(.bottom, 8)
            }
        }
    }
}

struct EditorSidebarView: View {
    @EnvironmentObject var viewModel: ViewModel
    @State private var showDefaultsPopover = false

    var body: some View {
        ScrollView {
            VStack(spacing: 0) {
                AccordionSection(L("Fit Mode"), icon: "crop") {
                    Picker("", selection: $viewModel.fitMode) {
                        Text(L("Crop")).tag("crop")
                        Text(L("Contain")).tag("contain")
                        Text(L("Stretch")).tag("stretch")
                    }
                    .pickerStyle(.segmented)
                    .labelsHidden()

                    QuickZoomControlsView()
                }

                Divider()

                AccordionSection(L("Rotate"), icon: "rotate.right") {
                    HStack(spacing: 12) {
                        Button {
                            viewModel.rotateCounterClockwise()
                        } label: {
                            Label(L("Rotate Left"), systemImage: "rotate.left")
                        }
                        .controlSize(.small)

                        Button {
                            viewModel.rotateClockwise()
                        } label: {
                            Label(L("Rotate Right"), systemImage: "rotate.right")
                        }
                        .controlSize(.small)

                        Button {
                            viewModel.toggleHorizontalFlip()
                        } label: {
                            Label(L("Flip"), systemImage: "arrow.left.and.right")
                        }
                        .controlSize(.small)
                        .buttonStyle(.bordered)
                        .tint(viewModel.isHorizontallyFlipped ? .accentColor : .secondary)

                        Spacer()
                    }
                }

                Divider()

                AccordionSection(L("Overlays"), icon: "sparkles", expanded: true) {
                    Menu {
                        Button(L("Text")) { viewModel.addOverlay(kind: .text) }
                        Button(L("QR Code")) { viewModel.addOverlay(kind: .qrCode) }
                        Button(L("Timestamp")) { viewModel.addOverlay(kind: .timestamp) }
                        Button(L("Image")) { viewModel.addOverlay(kind: .image) }
                        Button(L("Location")) { viewModel.addOverlay(kind: .location) }
                    } label: {
                        Label(L("Add Overlay"), systemImage: "plus")
                            .frame(maxWidth: .infinity)
                    }
                    .controlSize(.small)

                    if viewModel.overlays.isEmpty {
                        Text(L("No overlays yet"))
                            .font(.caption)
                            .foregroundColor(.secondary)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(.vertical, 4)
                    } else {
                        VStack(spacing: 6) {
                            ForEach(viewModel.overlays.sorted(by: { $0.zIndex < $1.zIndex })) { overlay in
                                OverlayListRowView(overlay: overlay)
                            }
                        }
                    }

                    if viewModel.selectedOverlay != nil {
                        Divider().padding(.vertical, 4)
                        SelectedOverlayInspectorView()
                    } else {
                        Text(L("Select an overlay to edit"))
                            .font(.caption)
                            .foregroundColor(.secondary)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }

                Divider()

                Button {
                    showDefaultsPopover = true
                } label: {
                    HStack(alignment: .top, spacing: 10) {
                        Image(systemName: "slider.horizontal.3")
                            .font(.callout)
                            .foregroundColor(.accentColor)
                            .frame(width: 18, height: 18)

                        VStack(alignment: .leading, spacing: 4) {
                            Text(L("Defaults For New Photos"))
                                .font(.callout)
                                .fontWeight(.medium)
                                .foregroundColor(.primary)
                            Text(L("Applies to photos added after this change. Existing queue items stay unchanged."))
                                .font(.caption)
                                .foregroundColor(.secondary)
                                .multilineTextAlignment(.leading)
                        }

                        Spacer(minLength: 8)

                        Image(systemName: "chevron.right")
                            .font(.caption)
                            .foregroundColor(.secondary)
                            .padding(.top, 2)
                    }
                    .padding(.vertical, 10)
                    .padding(.horizontal, 10)
                    .background(
                        RoundedRectangle(cornerRadius: 8)
                            .fill(Color.secondary.opacity(0.08))
                    )
                }
                .buttonStyle(.plain)
                .padding(.top, 12)
                .popover(isPresented: $showDefaultsPopover, arrowEdge: .leading) {
                    NewPhotoDefaultsPopover()
                        .environmentObject(viewModel)
                }
            }
            .padding(12)
        }
        .frame(minWidth: 360, idealWidth: 400, maxWidth: 460)
    }
}

struct NewPhotoDefaultsPopover: View {
    @EnvironmentObject var viewModel: ViewModel

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(L("Defaults For New Photos"))
                .font(.headline)

            Text(L("Applies to photos added after this change. Existing queue items stay unchanged."))
                .font(.caption)
                .foregroundColor(.secondary)

            VStack(alignment: .leading, spacing: 8) {
                Text(L("Fit Mode"))
                    .font(.caption)
                    .foregroundColor(.secondary)

                Picker("", selection: $viewModel.newPhotoDefaults.fitMode) {
                    Text(L("Crop")).tag("crop")
                    Text(L("Contain")).tag("contain")
                    Text(L("Stretch")).tag("stretch")
                }
                .pickerStyle(.segmented)
                .labelsHidden()
            }

            if let aspectRatio = viewModel.printerAspectRatio, aspectRatio != 1.0 {
                VStack(alignment: .leading, spacing: 8) {
                    Text(L("Film Orientation"))
                        .font(.caption)
                        .foregroundColor(.secondary)

                    Picker("", selection: $viewModel.newPhotoDefaults.filmOrientation) {
                        Text(L("Standard")).tag("default")
                        Text(L("Rotated")).tag("rotated")
                    }
                    .pickerStyle(.segmented)
                    .labelsHidden()
                }
            }

            DefaultTimestampOverlayEditor()

            Divider()

            HStack {
                Button(L("Use Current Timestamp as Default")) {
                    viewModel.saveCurrentSettingsAsNewPhotoDefaults()
                }
                .disabled(viewModel.selectedImage == nil)

                Spacer()

                Button(L("Reset Defaults")) {
                    viewModel.resetNewPhotoDefaults()
                }
                .disabled(viewModel.newPhotoDefaults == NewPhotoDefaults())
            }
        }
        .padding(16)
        .frame(width: 320)
    }
}

struct OverlayListRowView: View {
    @EnvironmentObject var viewModel: ViewModel
    @State private var isHovered = false
    let overlay: OverlayItem

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: symbolName)
                .frame(width: 16)
                .foregroundColor(isSelected ? .accentColor : .secondary)

            Text(viewModel.overlayTitle(for: overlay))
                .font(.callout)
                .lineLimit(1)
                .foregroundColor(.primary)

            Spacer()

            Button {
                viewModel.updateOverlay(id: overlay.id) { item in
                    item.isHidden.toggle()
                }
            } label: {
                Image(systemName: overlay.isHidden ? "eye.slash" : "eye")
                    .foregroundColor(.secondary)
            }
            .buttonStyle(.plain)
            .opacity(actionOpacity)

            Button {
                viewModel.deleteOverlay(id: overlay.id)
            } label: {
                Image(systemName: "trash")
                    .foregroundColor(.secondary)
            }
            .buttonStyle(.plain)
            .opacity(actionOpacity)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 6)
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(
                    isSelected
                        ? Color.accentColor.opacity(0.14)
                        : (isHovered ? Color.white.opacity(0.08) : Color.secondary.opacity(0.05))
                )
        )
        .overlay(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .stroke(
                    isSelected ? Color.accentColor.opacity(0.38) : Color.white.opacity(isHovered ? 0.14 : 0),
                    lineWidth: 1
                )
        )
        .shadow(color: isSelected ? Color.accentColor.opacity(0.12) : .clear, radius: 8, y: 4)
        .contentShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        .onTapGesture {
            viewModel.selectOverlay(overlay.id)
        }
        .onHover { hovered in
            withAnimation(.easeOut(duration: 0.16)) {
                isHovered = hovered
            }
        }
    }

    private var isSelected: Bool {
        viewModel.selectedOverlayID == overlay.id
    }

    private var actionOpacity: Double {
        (isHovered || isSelected) ? 1 : 0.55
    }

    private var symbolName: String {
        switch overlay.kind {
        case .text: return "textformat"
        case .qrCode: return "qrcode"
        case .timestamp: return "calendar"
        case .image: return "photo"
        case .location: return "mappin.and.ellipse"
        }
    }
}

struct InspectorSectionCard<Content: View>: View {
    let title: String
    @ViewBuilder let content: () -> Content

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title)
                .font(.caption)
                .foregroundColor(.secondary)

            content()
        }
        .padding(10)
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(.ultraThinMaterial)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .stroke(Color.white.opacity(0.18), lineWidth: 1)
        )
    }
}

struct SelectedOverlayInspectorView: View {
    @EnvironmentObject var viewModel: ViewModel

    var body: some View {
        guard let overlay = viewModel.selectedOverlay else {
            return AnyView(EmptyView())
        }

        let isLocked = overlay.isLocked

        return AnyView(
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    Text(viewModel.overlayTitle(for: overlay))
                        .font(.callout)
                        .fontWeight(.semibold)
                    Spacer()
                    Button(L("Send Backward")) { viewModel.moveSelectedOverlayBackward() }
                        .controlSize(.small)
                        .disabled(isLocked)
                    Button(L("Bring Forward")) { viewModel.moveSelectedOverlayForward() }
                        .controlSize(.small)
                        .disabled(isLocked)
                }

                HStack {
                    Toggle(L("Lock"), isOn: lockBinding)
                    Toggle(L("Hidden"), isOn: hiddenBinding)
                }
                .font(.caption)

                HStack {
                    Button(L("Duplicate")) { viewModel.duplicateSelectedOverlay() }
                    Button(L("Delete")) { viewModel.deleteSelectedOverlay() }
                }
                .controlSize(.small)

                InspectorSectionCard(title: L("Position")) {
                    labeledSlider("X", value: positionXBinding, range: 0.05...0.95)
                    labeledSlider("Y", value: positionYBinding, range: 0.05...0.95)
                    labeledSlider(L("Width"), value: widthBinding, range: 0.08...0.95)
                    labeledSlider(L("Height"), value: heightBinding, range: 0.06...0.95)
                }
                .disabled(isLocked)

                InspectorSectionCard(title: L("Appearance")) {
                    labeledSlider(L("Opacity"), value: opacityBinding, range: 0.1...1.0)
                }
                .disabled(isLocked)

                InspectorSectionCard(title: L("Content")) {
                    switch overlay.content {
                    case .text:
                        textControls
                    case .qrCode:
                        qrControls
                    case .timestamp:
                        timestampControls
                    case .image:
                        imageControls
                    case .location:
                        locationControls
                    }
                }
                .disabled(isLocked)
            }
        )
    }

    private func labeledSlider(_ title: String, value: Binding<Double>, range: ClosedRange<Double>) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.caption)
                .foregroundColor(.secondary)
            Slider(value: value, in: range)
        }
    }

    private var opacityBinding: Binding<Double> {
        Binding(
            get: { viewModel.selectedOverlay?.opacity ?? 1.0 },
            set: { newValue in viewModel.updateSelectedOverlay { $0.opacity = newValue } }
        )
    }

    private var positionXBinding: Binding<Double> {
        Binding(
            get: { viewModel.selectedOverlay?.placement.normalizedCenterX ?? 0.5 },
            set: { newValue in viewModel.updateSelectedOverlay { $0.placement.normalizedCenterX = newValue } }
        )
    }

    private var positionYBinding: Binding<Double> {
        Binding(
            get: { viewModel.selectedOverlay?.placement.normalizedCenterY ?? 0.5 },
            set: { newValue in viewModel.updateSelectedOverlay { $0.placement.normalizedCenterY = newValue } }
        )
    }

    private var widthBinding: Binding<Double> {
        Binding(
            get: { viewModel.selectedOverlay?.placement.normalizedWidth ?? 0.25 },
            set: { newValue in viewModel.updateSelectedOverlay { $0.placement.normalizedWidth = newValue } }
        )
    }

    private var heightBinding: Binding<Double> {
        Binding(
            get: { viewModel.selectedOverlay?.placement.normalizedHeight ?? 0.15 },
            set: { newValue in viewModel.updateSelectedOverlay { $0.placement.normalizedHeight = newValue } }
        )
    }

    private var hiddenBinding: Binding<Bool> {
        Binding(
            get: { viewModel.selectedOverlay?.isHidden ?? false },
            set: { newValue in viewModel.updateSelectedOverlay { $0.isHidden = newValue } }
        )
    }

    private var lockBinding: Binding<Bool> {
        Binding(
            get: { viewModel.selectedOverlay?.isLocked ?? false },
            set: { newValue in viewModel.updateSelectedOverlay { $0.isLocked = newValue } }
        )
    }

    private var textControls: some View {
        VStack(alignment: .leading, spacing: 8) {
            TextField(L("Text"), text: Binding(
                get: {
                    guard let overlay = viewModel.selectedOverlay,
                          case .text(let data) = overlay.content else { return "" }
                    return data.text
                },
                set: { newValue in
                    viewModel.updateSelectedTextOverlay { $0.text = newValue }
                }
            ))

            labeledSlider(L("Size"), value: Binding(
                get: {
                    guard let overlay = viewModel.selectedOverlay,
                          case .text(let data) = overlay.content else { return 0.1 }
                    return data.fontScale
                },
                set: { newValue in
                    viewModel.updateSelectedTextOverlay { $0.fontScale = newValue }
                }
            ), range: 0.05...0.24)

            Picker(L("Alignment"), selection: Binding(
                get: {
                    guard let overlay = viewModel.selectedOverlay,
                          case .text(let data) = overlay.content else { return OverlayTextAlignment.center }
                    return data.textAlignment
                },
                set: { newValue in
                    viewModel.updateSelectedTextOverlay { $0.textAlignment = newValue }
                }
            )) {
                Text(L("Leading")).tag(OverlayTextAlignment.leading)
                Text(L("Center")).tag(OverlayTextAlignment.center)
                Text(L("Trailing")).tag(OverlayTextAlignment.trailing)
            }
            .pickerStyle(.segmented)

            Picker(L("Shadow"), selection: Binding(
                get: {
                    guard let overlay = viewModel.selectedOverlay,
                          case .text(let data) = overlay.content else { return OverlayShadowStyle.soft }
                    return data.shadowStyle
                },
                set: { newValue in
                    viewModel.updateSelectedTextOverlay { $0.shadowStyle = newValue }
                }
            )) {
                Text(L("None")).tag(OverlayShadowStyle.none)
                Text(L("Soft")).tag(OverlayShadowStyle.soft)
                Text(L("Strong")).tag(OverlayShadowStyle.strong)
            }
            .pickerStyle(.segmented)
        }
    }

    private var qrControls: some View {
        VStack(alignment: .leading, spacing: 8) {
            TextField(L("Content"), text: Binding(
                get: {
                    guard let overlay = viewModel.selectedOverlay,
                          case .qrCode(let data) = overlay.content else { return "" }
                    return data.payload
                },
                set: { newValue in
                    viewModel.updateSelectedQRCodeOverlay { $0.payload = newValue }
                }
            ))

            Toggle(L("Show Caption"), isOn: Binding(
                get: {
                    guard let overlay = viewModel.selectedOverlay,
                          case .qrCode(let data) = overlay.content else { return false }
                    return data.showsCaption
                },
                set: { newValue in
                    viewModel.updateSelectedQRCodeOverlay { $0.showsCaption = newValue }
                }
            ))

            if let overlay = viewModel.selectedOverlay,
               case .qrCode(let data) = overlay.content,
               data.showsCaption {
                TextField(L("Caption"), text: Binding(
                    get: { data.caption },
                    set: { newValue in
                        viewModel.updateSelectedQRCodeOverlay { $0.caption = newValue }
                    }
                ))
            }

            Toggle(L("Quiet Zone"), isOn: Binding(
                get: {
                    guard let overlay = viewModel.selectedOverlay,
                          case .qrCode(let data) = overlay.content else { return true }
                    return data.includesQuietZone
                },
                set: { newValue in
                    viewModel.updateSelectedQRCodeOverlay { $0.includesQuietZone = newValue }
                }
            ))

            Picker(L("Error Correction"), selection: Binding(
                get: {
                    guard let overlay = viewModel.selectedOverlay,
                          case .qrCode(let data) = overlay.content else { return QRErrorCorrectionLevel.medium }
                    return data.correctionLevel
                },
                set: { newValue in
                    viewModel.updateSelectedQRCodeOverlay { $0.correctionLevel = newValue }
                }
            )) {
                Text("L").tag(QRErrorCorrectionLevel.low)
                Text("M").tag(QRErrorCorrectionLevel.medium)
                Text("Q").tag(QRErrorCorrectionLevel.quartile)
                Text("H").tag(QRErrorCorrectionLevel.high)
            }
            .pickerStyle(.segmented)
        }
    }

    private var timestampControls: some View {
        VStack(alignment: .leading, spacing: 8) {
            RoundedRectangle(cornerRadius: 6)
                .fill(Color(white: 0.15))
                .frame(height: 48)
                .overlay {
                    if let overlay = viewModel.selectedOverlay,
                       case .timestamp(let data) = overlay.content {
                        TimestampPreviewView(data: data, size: CGSize(width: 200, height: 48))
                    }
                }

            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    ForEach(TimestampPresetCatalog.presetOrder, id: \.self) { key in
                        PresetCard(
                            preset: TimestampPresetCatalog.presets[key]!,
                            isSelected: {
                                guard let overlay = viewModel.selectedOverlay,
                                      case .timestamp(let data) = overlay.content else { return false }
                                return data.presetKey == key
                            }()
                        )
                        .onTapGesture {
                            viewModel.updateSelectedTimestampOverlay {
                                $0.presetKey = key
                                $0.lightBleedEnabled = TimestampPresetCatalog.presets[key]!.defaultLightBleed
                            }
                        }
                    }
                }
            }

            Picker(L("Format"), selection: Binding(
                get: {
                    guard let overlay = viewModel.selectedOverlay,
                          case .timestamp(let data) = overlay.content else { return TimestampFormat.ymd }
                    return data.format
                },
                set: { newValue in
                    viewModel.updateSelectedTimestampOverlay { $0.format = newValue }
                }
            )) {
                Text("YY.MM.DD").tag(TimestampFormat.ymd)
                Text("MM.DD.YY").tag(TimestampFormat.mdy)
                Text("DD.MM.YY").tag(TimestampFormat.dmy)
            }
            .pickerStyle(.segmented)

            HStack {
                Toggle(L("Time"), isOn: Binding(
                    get: {
                        guard let overlay = viewModel.selectedOverlay,
                              case .timestamp(let data) = overlay.content else { return true }
                        return data.showsTime
                    },
                    set: { newValue in
                        viewModel.updateSelectedTimestampOverlay { $0.showsTime = newValue }
                    }
                ))
                Toggle(L("Glow"), isOn: Binding(
                    get: {
                        guard let overlay = viewModel.selectedOverlay,
                              case .timestamp(let data) = overlay.content else { return false }
                        return data.lightBleedEnabled
                    },
                    set: { newValue in
                        viewModel.updateSelectedTimestampOverlay { $0.lightBleedEnabled = newValue }
                    }
                ))
            }
            .font(.caption)
        }
    }

    private var imageControls: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button(L("Replace Image")) {
                viewModel.replaceSelectedImageOverlayAsset()
            }
            .controlSize(.small)

            Picker(L("Fit Mode"), selection: Binding(
                get: {
                    guard let overlay = viewModel.selectedOverlay,
                          case .image(let data) = overlay.content else { return OverlayImageContentMode.fit }
                    return data.contentMode
                },
                set: { newValue in
                    viewModel.updateSelectedImageOverlay { $0.contentMode = newValue }
                }
            )) {
                Text(L("Contain")).tag(OverlayImageContentMode.fit)
                Text(L("Crop")).tag(OverlayImageContentMode.fill)
            }
            .pickerStyle(.segmented)

            Toggle(L("Background"), isOn: Binding(
                get: {
                    guard let overlay = viewModel.selectedOverlay,
                          case .image(let data) = overlay.content else { return false }
                    return data.showsBacking
                },
                set: { newValue in
                    viewModel.updateSelectedImageOverlay { $0.showsBacking = newValue }
                }
            ))

            labeledSlider(L("Corner Radius"), value: Binding(
                get: {
                    guard let overlay = viewModel.selectedOverlay,
                          case .image(let data) = overlay.content else { return 0 }
                    return data.cornerRadius
                },
                set: { newValue in
                    viewModel.updateSelectedImageOverlay { $0.cornerRadius = newValue }
                }
            ), range: 0...32)
        }
    }

    private var locationControls: some View {
        VStack(alignment: .leading, spacing: 8) {
            Picker(L("Source"), selection: Binding(
                get: {
                    guard let overlay = viewModel.selectedOverlay,
                          case .location(let data) = overlay.content else { return LocationOverlaySource.photoMetadata }
                    return data.source
                },
                set: { newValue in
                    viewModel.updateSelectedLocationOverlay {
                        $0.source = newValue
                        if newValue == .manualText {
                            $0.displayStyle = .name
                        }
                    }
                }
            )) {
                Text(L("Photo Metadata")).tag(LocationOverlaySource.photoMetadata)
                Text(L("Manual Coordinates")).tag(LocationOverlaySource.manualCoordinates)
                Text(L("Manual Text")).tag(LocationOverlaySource.manualText)
            }
            .pickerStyle(.menu)

            if selectedLocationSource != .manualText {
                Picker(L("Display"), selection: Binding(
                    get: {
                        guard let overlay = viewModel.selectedOverlay,
                              case .location(let data) = overlay.content else { return LocationOverlayDisplayStyle.coordinates }
                        return data.displayStyle
                    },
                    set: { newValue in
                        viewModel.updateSelectedLocationOverlay { $0.displayStyle = newValue }
                    }
                )) {
                    Text(L("Coordinates")).tag(LocationOverlayDisplayStyle.coordinates)
                    Text(L("Name")).tag(LocationOverlayDisplayStyle.name)
                    Text(L("Name + Coordinates")).tag(LocationOverlayDisplayStyle.nameAndCoordinates)
                }
                .pickerStyle(.menu)
            }

            switch selectedLocationSource {
            case .photoMetadata:
                if viewModel.imageLocation == nil {
                    Text(L("No location metadata"))
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                if selectedLocationDisplayStyle != .coordinates {
                    nameField(title: L("Name"))
                }
                if selectedLocationDisplayStyle != .name {
                    precisionSlider
                }

            case .manualCoordinates:
                if selectedLocationDisplayStyle != .coordinates {
                    nameField(title: L("Name"))
                }

                HStack {
                    coordinateField(title: L("Latitude"), axis: .latitude)
                    coordinateField(title: L("Longitude"), axis: .longitude)
                }

                if selectedLocationDisplayStyle != .name {
                    precisionSlider
                }

            case .manualText:
                nameField(title: L("Content"))
            }
        }
    }

    private var selectedLocationSource: LocationOverlaySource {
        guard let overlay = viewModel.selectedOverlay,
              case .location(let data) = overlay.content else { return .photoMetadata }
        return data.source
    }

    private var selectedLocationDisplayStyle: LocationOverlayDisplayStyle {
        guard let overlay = viewModel.selectedOverlay,
              case .location(let data) = overlay.content else { return .coordinates }
        return data.displayStyle
    }

    private func nameField(title: String) -> some View {
        TextField(title, text: Binding(
            get: {
                guard let overlay = viewModel.selectedOverlay,
                      case .location(let data) = overlay.content else { return "" }
                return data.locationName
            },
            set: { newValue in
                viewModel.updateSelectedLocationOverlay { $0.locationName = newValue }
            }
        ))
    }

    private enum CoordinateAxis {
        case latitude
        case longitude
    }

    private func coordinateField(title: String, axis: CoordinateAxis) -> some View {
        TextField(title, text: Binding(
            get: {
                guard let overlay = viewModel.selectedOverlay,
                      case .location(let data) = overlay.content else { return "" }
                switch axis {
                case .latitude:
                    guard let value = data.coordinate?.latitude else { return "" }
                    return String(value)
                case .longitude:
                    guard let value = data.coordinate?.longitude else { return "" }
                    return String(value)
                }
            },
            set: { newValue in
                viewModel.updateSelectedLocationOverlay { data in
                    let latitude = axis == .latitude ? (Double(newValue) ?? data.coordinate?.latitude ?? 0) : (data.coordinate?.latitude ?? 0)
                    let longitude = axis == .longitude ? (Double(newValue) ?? data.coordinate?.longitude ?? 0) : (data.coordinate?.longitude ?? 0)
                    data.coordinate = GeoCoordinate(latitude: latitude, longitude: longitude)
                }
            }
        ))
    }

    private var precisionSlider: some View {
        labeledSlider(L("Precision"), value: Binding(
            get: {
                guard let overlay = viewModel.selectedOverlay,
                      case .location(let data) = overlay.content else { return 4 }
                return Double(data.precision)
            },
            set: { newValue in
                viewModel.updateSelectedLocationOverlay { $0.precision = Int(newValue.rounded()) }
            }
        ), range: 0...6)
    }
}

struct DefaultTimestampOverlayEditor: View {
    @EnvironmentObject var viewModel: ViewModel

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(L("Timestamp"))
                .font(.caption)
                .foregroundColor(.secondary)

            Toggle(L("Enabled"), isOn: Binding(
                get: { viewModel.defaultTimestampOverlay != nil },
                set: { viewModel.setDefaultTimestampOverlayEnabled($0) }
            ))
            .font(.callout)

            if let overlay = viewModel.defaultTimestampOverlay,
               case .timestamp(let data) = overlay.content {
                RoundedRectangle(cornerRadius: 6)
                    .fill(Color(white: 0.15))
                    .frame(height: 48)
                    .overlay {
                        TimestampPreviewView(data: data, size: CGSize(width: 200, height: 48))
                            .environmentObject(viewModel)
                    }

                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 6) {
                        ForEach(TimestampPresetCatalog.presetOrder, id: \.self) { key in
                            PresetCard(
                                preset: TimestampPresetCatalog.presets[key]!,
                                isSelected: data.presetKey == key
                            )
                            .onTapGesture {
                                viewModel.updateDefaultTimestampOverlay {
                                    $0.presetKey = key
                                    $0.lightBleedEnabled = TimestampPresetCatalog.presets[key]!.defaultLightBleed
                                }
                            }
                        }
                    }
                }

                Picker(L("Format"), selection: Binding(
                    get: {
                        guard let overlay = viewModel.defaultTimestampOverlay,
                              case .timestamp(let data) = overlay.content else { return TimestampFormat.ymd }
                        return data.format
                    },
                    set: { newValue in
                        viewModel.updateDefaultTimestampOverlay { $0.format = newValue }
                    }
                )) {
                    Text("YY.MM.DD").tag(TimestampFormat.ymd)
                    Text("MM.DD.YY").tag(TimestampFormat.mdy)
                    Text("DD.MM.YY").tag(TimestampFormat.dmy)
                }
                .pickerStyle(.segmented)

                HStack {
                    Toggle(L("Time"), isOn: Binding(
                        get: {
                            guard let overlay = viewModel.defaultTimestampOverlay,
                                  case .timestamp(let data) = overlay.content else { return true }
                            return data.showsTime
                        },
                        set: { newValue in
                            viewModel.updateDefaultTimestampOverlay { $0.showsTime = newValue }
                        }
                    ))
                    Toggle(L("Glow"), isOn: Binding(
                        get: {
                            guard let overlay = viewModel.defaultTimestampOverlay,
                                  case .timestamp(let data) = overlay.content else { return false }
                            return data.lightBleedEnabled
                        },
                        set: { newValue in
                            viewModel.updateDefaultTimestampOverlay { $0.lightBleedEnabled = newValue }
                        }
                    ))
                }
                .font(.caption)
            }
        }
    }
}
