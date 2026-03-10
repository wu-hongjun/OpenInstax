import SwiftUI
import UniformTypeIdentifiers

struct MainPreviewView: View {
    @EnvironmentObject var viewModel: ViewModel
    @State private var isTargeted = false
    @GestureState private var dragDelta: CGSize = .zero
    @GestureState private var magnifyDelta: CGFloat = 1.0
    @State private var localFrameSize: CGSize = .zero
    var openEditor: () -> Void

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
            AppPanelBackground(
                chromeColor: panelChromeColor,
                dashed: viewModel.selectedImage == nil,
                showsChrome: !showsSimulatedFilmFrame,
                showsBaseChrome: viewModel.selectedImage != nil
            )

            if viewModel.isPrinting {
                VStack(spacing: 8) {
                    if viewModel.batchPrintTotal > 1 {
                        Text(L("printing_n_of_m", viewModel.batchPrintIndex, viewModel.batchPrintTotal))
                            .font(.caption)
                            .fontWeight(.medium)
                            .foregroundColor(.secondary)
                    }
                    if let p = viewModel.printProgress {
                        ProgressView(value: Double(p.sent), total: Double(p.total))
                            .progressViewStyle(.linear)
                            .frame(width: 120)
                        Text(L("transfer_progress", p.sent, p.total))
                            .font(.caption)
                            .foregroundColor(.secondary)
                    } else {
                        ProgressView().controlSize(.regular)
                        Text(L("Preparing..."))
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                }
                .transition(.opacity.combined(with: .scale(scale: 0.98)))
            } else if let image = viewModel.selectedImage {
                ZStack(alignment: .topTrailing) {
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
                            }
                            .overlay(
                                ExposureAdjustedImageView(image: image, exposureEV: viewModel.exposureEV) { previewImage in
                                    previewImage
                                        .resizable()
                                        .aspectRatio(contentMode: .fill)
                                        .scaleEffect(
                                            x: viewModel.isHorizontallyFlipped ? -effectiveZoom : effectiveZoom,
                                            y: effectiveZoom
                                        )
                                        .offset(effectiveOffset(imageSize: image.size))
                                        .rotationEffect(.degrees(Double(viewModel.rotationAngle)))
                                }
                                .overlay {
                                    OverlayCanvasView()
                                }
                                .clipped()
                                .contentShape(Rectangle())
                                .gesture(
                                    DragGesture()
                                        .updating($dragDelta) { value, state, _ in
                                            state = value.translation
                                        }
                                        .onEnded { value in
                                            let currentOffset = viewModel.cropOffsetInPoints(
                                                imageSize: image.size,
                                                frameSize: localFrameSize,
                                                zoom: viewModel.cropZoom
                                            )
                                            let raw = CGSize(
                                                width: currentOffset.width + value.translation.width,
                                                height: currentOffset.height + value.translation.height
                                            )
                                            let clamped = viewModel.clampedCropOffsetPoints(
                                                raw: raw,
                                                imageSize: image.size,
                                                frameSize: localFrameSize,
                                                zoom: viewModel.cropZoom
                                            )
                                            viewModel.cropOffsetNormalized = viewModel.normalizedCropOffset(
                                                from: clamped,
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
                                .onTapGesture(count: 2) {
                                    guard !viewModel.isPrinting else { return }
                                    openEditor()
                                }
                            )
                        } else if viewModel.fitMode == "contain", let ar = viewModel.orientedAspectRatio {
                            Color.white
                                .aspectRatio(ar, contentMode: .fit)
                                .overlay(
                                    ExposureAdjustedImageView(image: image, exposureEV: viewModel.exposureEV) { previewImage in
                                        previewImage
                                            .resizable()
                                            .aspectRatio(contentMode: .fit)
                                            .scaleEffect(x: viewModel.isHorizontallyFlipped ? -1 : 1, y: 1)
                                            .rotationEffect(.degrees(Double(viewModel.rotationAngle)))
                                    }
                                )
                                .overlay {
                                    OverlayCanvasView()
                                }
                                .clipped()
                                .onTapGesture(count: 2) {
                                    guard !viewModel.isPrinting else { return }
                                    openEditor()
                                }
                        } else {
                            ExposureAdjustedImageView(image: image, exposureEV: viewModel.exposureEV) { previewImage in
                                previewImage
                                    .resizable()
                                    .aspectRatio(contentMode: .fit)
                                    .scaleEffect(x: viewModel.isHorizontallyFlipped ? -1 : 1, y: 1)
                                    .rotationEffect(.degrees(Double(viewModel.rotationAngle)))
                            }
                                .overlay {
                                    OverlayCanvasView()
                                }
                                .onTapGesture(count: 2) {
                                    guard !viewModel.isPrinting else { return }
                                    openEditor()
                                }
                        }
                    }
                    .padding(4)

                    Button { viewModel.removeSelectedQueueItem() } label: {
                        Image(systemName: "xmark.circle.fill")
                            .font(.title3)
                            .symbolRenderingMode(.hierarchical)
                            .foregroundColor(.secondary.opacity(0.92))
                            .padding(4)
                            .background(.ultraThinMaterial, in: Circle())
                    }
                    .buttonStyle(.plain)
                    .disabled(viewModel.isPrinting)
                    .help(L("Remove"))
                    .accessibilityLabel(Text(L("Remove")))
                    .padding(8)
                }
                .transition(.opacity.combined(with: .scale(scale: 0.985)))
            } else {
                VStack(spacing: 8) {
                    Image(systemName: "photo.on.rectangle.angled")
                        .font(.largeTitle)
                        .foregroundColor(.secondary)
                    Text(L("Drop images or click Open File"))
                        .font(.callout)
                        .foregroundColor(.secondary)
                    Button(L("Open")) { viewModel.selectImage() }
                        .buttonStyle(.bordered)
                        .buttonBorderShape(.roundedRectangle)
                        .controlSize(.small)
                }
                .transition(.opacity.combined(with: .scale(scale: 0.98)))
            }
        }
        .frame(minHeight: 120, maxHeight: .infinity)
        .animation(.easeInOut(duration: 0.22), value: viewModel.isPrinting)
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
        let currentOffset = viewModel.cropOffsetInPoints(
            imageSize: imageSize,
            frameSize: localFrameSize,
            zoom: effectiveZoom
        )
        let raw = CGSize(
            width: currentOffset.width + dragDelta.width,
            height: currentOffset.height + dragDelta.height
        )
        let clamped = viewModel.clampedCropOffsetPoints(
            raw: raw,
            imageSize: imageSize,
            frameSize: localFrameSize,
            zoom: effectiveZoom
        )
        return CGSize(width: -clamped.width, height: -clamped.height)
    }
}

struct QueueStripView: View {
    @EnvironmentObject var viewModel: ViewModel
    @State private var draggingItemID: UUID?

    private let thumbnailHeight: CGFloat = 44

    private var addButtonWidth: CGFloat {
        let aspectRatio = viewModel.orientedAspectRatio ?? (36.0 / thumbnailHeight)
        return max(36, thumbnailHeight * aspectRatio)
    }

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                        ForEach(Array(viewModel.queue.enumerated()), id: \.element.id) { index, item in
                            QueueThumbnailView(
                                item: item,
                                isSelected: index == viewModel.selectedQueueIndex,
                                isDragging: draggingItemID == item.id,
                                onSelect: { viewModel.selectQueueItem(at: index) },
                                onRemove: { withAnimation { viewModel.removeQueueItem(at: index) } }
                            )
                            .id(item.id)
                            .contextMenu {
                                if index > 0 {
                                    Button(L("Move Left")) {
                                        withAnimation { viewModel.moveQueueItem(from: index, to: index - 1) }
                                    }
                                }
                                if index < viewModel.queue.count - 1 {
                                    Button(L("Move Right")) {
                                        withAnimation { viewModel.moveQueueItem(from: index, to: index + 1) }
                                    }
                                }
                                Divider()
                                Button(L("Remove")) {
                                    withAnimation { viewModel.removeQueueItem(at: index) }
                                }
                            }
                            .onDrag {
                                draggingItemID = item.id
                                return NSItemProvider(object: item.id.uuidString as NSString)
                            }
                            .onDrop(of: [.text], delegate: QueueDropDelegate(
                                targetIndex: index,
                                viewModel: viewModel,
                                draggingItemID: $draggingItemID
                            ))
                        }

                        Button { viewModel.selectImage() } label: {
                            Image(systemName: "plus")
                                .font(.system(size: 14, weight: .medium))
                                .foregroundColor(.secondary)
                                .frame(width: addButtonWidth, height: thumbnailHeight)
                                .background(
                                    RoundedRectangle(cornerRadius: 7, style: .continuous)
                                        .fill(Color.white.opacity(0.04))
                                )
                                .overlay(
                                    RoundedRectangle(cornerRadius: 7, style: .continuous)
                                        .strokeBorder(style: StrokeStyle(lineWidth: 1, dash: [3]))
                                        .foregroundColor(.secondary.opacity(0.28))
                                )
                        }
                        .buttonStyle(.plain)
                        .frame(width: addButtonWidth + 6, height: thumbnailHeight + 6)
                }
                .padding(.horizontal, 2)
                .padding(.vertical, 1)
            }
            .opacity(viewModel.isPrinting ? 0.72 : 1.0)
            .allowsHitTesting(!viewModel.isPrinting)
            .onChange(of: viewModel.selectedQueueIndex) { _, _ in
                if viewModel.queue.indices.contains(viewModel.selectedQueueIndex) {
                    withAnimation(.easeInOut(duration: 0.2)) {
                        proxy.scrollTo(viewModel.queue[viewModel.selectedQueueIndex].id, anchor: .center)
                    }
                }
            }
        }
    }
}

struct QueueDropDelegate: DropDelegate {
    let targetIndex: Int
    let viewModel: ViewModel
    @Binding var draggingItemID: UUID?

    func performDrop(info: DropInfo) -> Bool {
        draggingItemID = nil
        return true
    }

    func dropEntered(info: DropInfo) {
        guard let dragID = draggingItemID,
              let sourceIndex = viewModel.queue.firstIndex(where: { $0.id == dragID }),
              sourceIndex != targetIndex else { return }
        withAnimation(.easeInOut(duration: 0.2)) {
            viewModel.moveQueueItem(from: sourceIndex, to: targetIndex)
        }
    }

    func dropUpdated(info: DropInfo) -> DropProposal? {
        DropProposal(operation: .move)
    }
}

struct QueueThumbnailView: View {
    @EnvironmentObject var viewModel: ViewModel
    @State private var isHovered = false
    let item: QueueItem
    let isSelected: Bool
    var isDragging: Bool = false
    let onSelect: () -> Void
    let onRemove: () -> Void

    private let thumbnailHeight: CGFloat = 44

    private var thumbnailAspectRatio: CGFloat {
        viewModel.orientedAspectRatio(for: item.editState.filmOrientation) ?? (36.0 / thumbnailHeight)
    }

    private var thumbnailWidth: CGFloat {
        max(36, thumbnailHeight * thumbnailAspectRatio)
    }

    var body: some View {
        ZStack(alignment: .topTrailing) {
            Button(action: onSelect) {
                ExposureAdjustedImageView(image: item.image, exposureEV: item.editState.exposureEV) { previewImage in
                    previewImage
                        .resizable()
                        .aspectRatio(contentMode: .fill)
                        .scaleEffect(x: item.editState.isHorizontallyFlipped ? -1 : 1, y: 1)
                        .rotationEffect(.degrees(Double(item.editState.rotationAngle)))
                        .frame(width: thumbnailWidth, height: thumbnailHeight)
                        .clipShape(RoundedRectangle(cornerRadius: 4))
                        .overlay(
                            RoundedRectangle(cornerRadius: 4)
                                .stroke(isSelected ? Color.accentColor.opacity(0.7) : Color.clear, lineWidth: 1.5)
                        )
                        .opacity(isDragging ? 0.5 : 1.0)
                }
            }
            .buttonStyle(.plain)

            Button(action: onRemove) {
                Image(systemName: "xmark.circle.fill")
                    .font(.system(size: 12, weight: .semibold))
                    .symbolRenderingMode(.hierarchical)
                    .foregroundColor(.secondary)
                    .background(.ultraThinMaterial, in: Circle())
                    .overlay(
                        Circle().stroke(Color.white.opacity(0.18), lineWidth: 1)
                    )
            }
            .buttonStyle(.plain)
            .padding(3)
            .opacity(isHovered || isSelected ? 1 : 0)
            .allowsHitTesting(isHovered || isSelected)
            .help(L("Remove"))
            .accessibilityLabel(Text(L("Remove")))
        }
        .padding(4)
        .background(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .fill(isSelected ? Color.accentColor.opacity(0.10) : (isHovered ? Color.white.opacity(0.05) : Color.clear))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .stroke(isSelected ? Color.accentColor.opacity(0.32) : Color.white.opacity(isHovered ? 0.10 : 0), lineWidth: 1)
        )
        .frame(width: thumbnailWidth + 6, height: thumbnailHeight + 6)
        .onHover { hovered in
            withAnimation(.easeOut(duration: 0.16)) {
                isHovered = hovered
            }
        }
        .animation(.easeInOut(duration: 0.16), value: isSelected)
    }
}

struct MainActionsView: View {
    @EnvironmentObject var viewModel: ViewModel
    var openEditor: () -> Void
    @Binding var isQueueStripVisible: Bool

    private var canPrintCurrent: Bool {
        viewModel.selectedImage != nil && viewModel.isConnected && !viewModel.isPrinting
    }

    private var canPrintNextBatch: Bool {
        viewModel.selectedImage != nil &&
        viewModel.isConnected &&
        !viewModel.isPrinting &&
        viewModel.printableQueueCountFromSelection > 0
    }

    private var singlePrintLabel: String {
        if viewModel.isPrinting {
            if viewModel.batchPrintTotal > 1 {
                return L("printing_n_of_m", viewModel.batchPrintIndex, viewModel.batchPrintTotal)
            }
            return viewModel.printProgress.map { L("transfer_progress", $0.sent, $0.total) } ?? L("Preparing...")
        }
        return L("Print")
    }

    private var printNextHint: String? {
        guard viewModel.queue.count > 1, !viewModel.isPrinting else { return nil }
        if !viewModel.isConnected {
            return L("Connect to your printer")
        }
        if viewModel.filmRemaining <= 0 {
            return L("No Film")
        }
        return nil
    }

    @ViewBuilder
    private func styledPrintButton<Label: View>(
        isProminent: Bool,
        action: @escaping () -> Void,
        @ViewBuilder label: @escaping () -> Label
    ) -> some View {
        if isProminent {
            Button(action: action, label: label)
                .buttonStyle(.borderedProminent)
        } else {
            Button(action: action, label: label)
                .buttonStyle(.bordered)
        }
    }

    var body: some View {
        VStack(spacing: 10) {
            QuickPrintToolbarView(
                openEditor: openEditor,
                isQueueStripVisible: $isQueueStripVisible
            )

            if viewModel.queue.count > 1 {
                VStack(alignment: .leading, spacing: 6) {
                    HStack(spacing: 10) {
                        styledPrintButton(isProminent: canPrintCurrent) {
                            Task { await viewModel.printSelectedImage() }
                        } label: {
                            HStack {
                                Image(systemName: "printer")
                                Text(L("Print Current"))
                            }
                            .frame(maxWidth: .infinity)
                        }
                        .controlSize(.large)
                        .disabled(!canPrintCurrent)

                        styledPrintButton(isProminent: canPrintNextBatch) {
                            Task { await viewModel.printQueue(startingAt: viewModel.selectedQueueIndex) }
                        } label: {
                            HStack {
                                Image(systemName: "printer.fill")
                                Text(viewModel.printNextActionLabel)
                            }
                            .frame(maxWidth: .infinity)
                        }
                        .controlSize(.large)
                        .disabled(!canPrintNextBatch)
                    }

                    if let printNextHint {
                        Text(printNextHint)
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                }
            } else {
                styledPrintButton(isProminent: canPrintCurrent) {
                    Task { await viewModel.printSelectedImage() }
                } label: {
                    HStack {
                        if viewModel.isPrinting {
                            ProgressView()
                                .controlSize(.small)
                                .padding(.trailing, 2)
                        } else {
                            Image(systemName: "printer.fill")
                        }
                        Text(singlePrintLabel)
                    }
                    .frame(maxWidth: .infinity)
                }
                .controlSize(.large)
                .disabled(!canPrintCurrent)
            }
        }
    }
}

struct QuickPrintToolbarView: View {
    @EnvironmentObject var viewModel: ViewModel
    var openEditor: () -> Void
    @Binding var isQueueStripVisible: Bool

    private var canEditSelectedImage: Bool {
        viewModel.selectedImage != nil && !viewModel.isPrinting
    }

    private var shouldPromoteOpenButton: Bool {
        viewModel.selectedImage == nil && !viewModel.isPrinting
    }

    private var isHorizontalOrientation: Bool {
        (viewModel.orientedAspectRatio ?? 1.0) > 1.0
    }

    private var orientationTitle: String {
        isHorizontalOrientation ? L("Horizontal") : L("Vertical")
    }

    private var orientationSymbolName: String {
        isHorizontalOrientation ? "rectangle" : "rectangle.portrait"
    }

    var body: some View {
        HStack(spacing: 8) {
            QuickZoomControlsView(resetTitle: L("Reset Zoom"), showsChrome: false)
            QuickExposureControlsView(showsChrome: false)

            quickToolbarButton(
                title: L("Rotate"),
                systemImage: "rotate.right",
                action: { viewModel.rotateClockwise() }
            )
            .disabled(viewModel.selectedImage == nil)
            .help(L("Rotate Right"))
            .accessibilityLabel(Text(L("Rotate Right")))

            if let aspectRatio = viewModel.printerAspectRatio, aspectRatio != 1.0 {
                quickToolbarButton(
                    title: orientationTitle,
                    systemImage: orientationSymbolName,
                    action: {
                        viewModel.filmOrientation = viewModel.filmOrientation == "default" ? "rotated" : "default"
                    },
                    isActive: viewModel.filmOrientation == "rotated"
                )
                .disabled(viewModel.selectedImage == nil)
                .help(L("Film Orientation"))
                .accessibilityLabel(Text(L("Film Orientation")))
            }

            if !viewModel.queue.isEmpty {
                quickToolbarButton(
                    title: "\(viewModel.queue.count)",
                    systemImage: isQueueStripVisible ? "square.stack.3d.up.fill" : "square.stack.3d.up",
                    action: {
                        withAnimation(.easeInOut(duration: 0.2)) {
                            isQueueStripVisible.toggle()
                        }
                    },
                    isActive: isQueueStripVisible
                )
                .help(L("Queue"))
                .accessibilityLabel(Text(L("Queue")))
            }

            quickToolbarButton(
                title: L("Open"),
                systemImage: "plus",
                action: { viewModel.selectImage() },
                prominent: shouldPromoteOpenButton
            )

            quickToolbarButton(
                title: L("Edit"),
                systemImage: "slider.horizontal.3",
                action: openEditor,
                prominent: canEditSelectedImage
            )
            .disabled(!canEditSelectedImage)
        }
        .frame(maxWidth: .infinity, alignment: .center)
        .disabled(viewModel.isPrinting)
    }

    private func quickToolbarButton(
        title: String,
        systemImage: String,
        action: @escaping () -> Void,
        isActive: Bool = false,
        prominent: Bool = false
    ) -> some View {
        Group {
            if prominent {
                Button(action: action) {
                    Label(title, systemImage: systemImage)
                }
                .buttonStyle(.borderedProminent)
                .buttonBorderShape(.roundedRectangle)
                .controlSize(.small)
                .tint(.orange)
            } else {
                Button(action: action) {
                    Label(title, systemImage: systemImage)
                }
                .buttonStyle(.bordered)
                .buttonBorderShape(.roundedRectangle)
                .controlSize(.small)
                .tint(isActive ? .accentColor : nil)
            }
        }
    }
}

struct QuickZoomControlsView: View {
    @EnvironmentObject var viewModel: ViewModel
    let resetTitle: String
    var showsChrome: Bool = true

    var body: some View {
        ControlGroup {
            Button {
                viewModel.quickZoomOut()
            } label: {
                Image(systemName: "minus")
            }
            .disabled(!viewModel.canQuickZoomOut)
            .help(L("Zoom Out"))
            .accessibilityLabel(Text(L("Zoom Out")))

            Button(resetTitle) {
                viewModel.resetCropAdjustments()
            }
            .disabled(!viewModel.canResetCropAdjustments)

            Button {
                viewModel.quickZoomIn()
            } label: {
                Image(systemName: "plus")
            }
            .disabled(!viewModel.canQuickZoomIn)
            .help(L("Zoom In"))
            .accessibilityLabel(Text(L("Zoom In")))
        }
        .controlSize(.small)
        .frame(minHeight: 30)
        .background {
            if showsChrome {
                CompactGlassSurface(cornerRadius: 9)
            }
        }
    }
}

struct QuickExposureControlsView: View {
    @EnvironmentObject var viewModel: ViewModel
    var showsChrome: Bool = true

    var body: some View {
        ControlGroup {
            Button {
                viewModel.decreaseExposure()
            } label: {
                Image(systemName: "minus")
            }
            .disabled(!viewModel.canDecreaseExposure)
            .help(L("Exposure"))
            .accessibilityLabel(Text(L("Exposure")))

            Button(viewModel.exposureDisplayValue) {
                viewModel.resetExposure()
            }
            .disabled(!viewModel.canResetExposure)

            Button {
                viewModel.increaseExposure()
            } label: {
                Image(systemName: "plus")
            }
            .disabled(!viewModel.canIncreaseExposure)
            .help(L("Exposure"))
            .accessibilityLabel(Text(L("Exposure")))
        }
        .controlSize(.small)
        .frame(minHeight: 30)
        .background {
            if showsChrome {
                CompactGlassSurface(cornerRadius: 9)
            }
        }
    }
}
