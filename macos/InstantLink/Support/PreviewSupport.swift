import AppKit
import AVFoundation
import SwiftUI

struct FilmFrameView<Content: View>: View {
    let filmModel: String?
    let isRotated: Bool
    let content: () -> Content

    private var topBorder: CGFloat { 0.129 }
    private var bottomBorder: CGFloat { 0.258 }
    private var sideBorder: CGFloat { 0.087 }

    init(filmModel: String?, isRotated: Bool, @ViewBuilder content: @escaping () -> Content) {
        self.filmModel = filmModel
        self.isRotated = isRotated
        self.content = content
    }

    private var imageAR: CGFloat {
        switch filmModel {
        case "Mini": return 46.0 / 62.0
        case "Wide": return 99.0 / 62.0
        default: return 1.0
        }
    }

    private func layout(availW: CGFloat, availH: CGFloat) -> (cardW: CGFloat, cardH: CGFloat, imgW: CGFloat, imgH: CGFloat, offsetX: CGFloat, offsetY: CGFloat) {
        let tb = topBorder
        let bb = bottomBorder
        let sb = sideBorder
        let iar = imageAR

        let cardHRatio = tb + 1.0 + bb
        let cardWRatio = iar + 2.0 * sb
        let cardAR = cardWRatio / cardHRatio
        let effectiveCardAR = isRotated ? (1.0 / cardAR) : cardAR

        let fitW: CGFloat
        let fitH: CGFloat
        if availH > 0 && availW / availH > effectiveCardAR {
            fitH = availH
            fitW = availH * effectiveCardAR
        } else {
            fitW = availW
            fitH = availW > 0 && effectiveCardAR > 0 ? availW / effectiveCardAR : availH
        }

        let divisor = isRotated ? cardWRatio : cardHRatio
        let imageAreaH = divisor > 0 ? fitH / divisor : fitH
        let imageAreaW = imageAreaH * iar

        let imgW = isRotated ? imageAreaH : imageAreaW
        let imgH = isRotated ? imageAreaW : imageAreaH

        let borderDelta = (tb - bb) / cardHRatio / 2
        let offsetX = isRotated ? borderDelta * fitW : CGFloat(0)
        let offsetY = isRotated ? CGFloat(0) : borderDelta * fitH

        return (fitW, fitH, imgW, imgH, offsetX, offsetY)
    }

    var body: some View {
        if filmModel != nil {
            GeometryReader { geo in
                let layout = layout(availW: geo.size.width, availH: geo.size.height)
                ZStack {
                    RoundedRectangle(cornerRadius: 3)
                        .fill(Color.white)
                        .frame(width: layout.cardW, height: layout.cardH)
                        .shadow(color: .black.opacity(0.15), radius: 4, y: 2)

                    content()
                        .frame(width: layout.imgW, height: layout.imgH)
                        .clipped()
                        .offset(x: layout.offsetX, y: layout.offsetY)
                }
                .position(x: geo.size.width / 2, y: geo.size.height / 2)
            }
        } else {
            content()
        }
    }
}

class CameraPreviewNSView: NSView {
    let previewLayer = AVCaptureVideoPreviewLayer()

    override init(frame: CGRect) {
        super.init(frame: frame)
        wantsLayer = true
        layer?.addSublayer(previewLayer)
    }

    required init?(coder: NSCoder) { fatalError() }

    override func layout() {
        super.layout()
        previewLayer.frame = bounds
    }
}

struct CameraPreviewView: NSViewRepresentable {
    let session: AVCaptureSession
    var isMirrored: Bool = false

    func makeNSView(context: Context) -> CameraPreviewNSView {
        let view = CameraPreviewNSView()
        view.previewLayer.session = session
        view.previewLayer.videoGravity = .resizeAspectFill
        if let connection = view.previewLayer.connection {
            connection.automaticallyAdjustsVideoMirroring = false
            connection.isVideoMirrored = isMirrored
        }
        return view
    }

    func updateNSView(_ nsView: CameraPreviewNSView, context: Context) {
        nsView.previewLayer.session = session
        if let connection = nsView.previewLayer.connection {
            connection.automaticallyAdjustsVideoMirroring = false
            connection.isVideoMirrored = isMirrored
        }
    }
}

struct StatusItem: View {
    let icon: String
    let value: String

    var body: some View {
        HStack(spacing: 4) {
            Image(systemName: icon)
                .font(.callout)
                .foregroundColor(.secondary)
            Text(value)
                .font(.callout)
                .fontWeight(.medium)
        }
    }
}

struct HeaderDivider: View {
    var body: some View {
        Rectangle()
            .fill(Color.white.opacity(0.18))
            .frame(width: 1, height: 16)
    }
}

struct AppPanelBackground: View {
    let chromeColor: Color
    var dashed: Bool = false
    var showsChrome: Bool = true
    var showsBaseChrome: Bool = true

    var body: some View {
        Group {
            if showsChrome {
                ZStack {
                    if showsBaseChrome {
                        RoundedRectangle(cornerRadius: 16, style: .continuous)
                            .fill(.regularMaterial)
                            .overlay(
                                RoundedRectangle(cornerRadius: 16, style: .continuous)
                                    .stroke(Color.white.opacity(0.22), lineWidth: 1)
                            )
                            .shadow(color: .black.opacity(0.08), radius: 12, y: 6)
                    }

                    RoundedRectangle(cornerRadius: 16, style: .continuous)
                        .strokeBorder(
                            style: StrokeStyle(lineWidth: 1.5, dash: dashed ? [6] : [])
                        )
                        .foregroundColor(chromeColor)
                }
            } else {
                Color.clear
            }
        }
    }
}

struct CompactGlassSurface: View {
    var cornerRadius: CGFloat = 10

    var body: some View {
        RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
            .fill(.thinMaterial)
            .overlay(
                RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                    .stroke(Color.white.opacity(0.12), lineWidth: 1)
            )
            .shadow(color: .black.opacity(0.035), radius: 5, y: 2)
    }
}

struct ExposureAdjustedImageView<Content: View>: View {
    let image: NSImage
    let exposureEV: Double
    let content: (Image) -> Content

    @State private var renderedImage: NSImage

    init(
        image: NSImage,
        exposureEV: Double,
        @ViewBuilder content: @escaping (Image) -> Content
    ) {
        self.image = image
        self.exposureEV = exposureEV
        self.content = content
        _renderedImage = State(initialValue: ImageAdjustmentService.applyExposure(to: image, ev: exposureEV) ?? image)
    }

    var body: some View {
        content(Image(nsImage: renderedImage))
            .onAppear(perform: refresh)
            .onChange(of: ObjectIdentifier(image)) { _, _ in refresh() }
            .onChange(of: exposureEV) { _, _ in refresh() }
    }

    private func refresh() {
        renderedImage = ImageAdjustmentService.applyExposure(to: image, ev: exposureEV) ?? image
    }
}

struct CropFrameSizeKey: PreferenceKey {
    static var defaultValue: CGSize = .zero

    static func reduce(value: inout CGSize, nextValue: () -> CGSize) {
        value = nextValue()
    }
}

struct OverlayCanvasView: View {
    @EnvironmentObject var viewModel: ViewModel
    var editable: Bool = false

    var body: some View {
        GeometryReader { geo in
            ZStack {
                ForEach(viewModel.overlays.filter { !$0.isHidden }.sorted(by: { $0.zIndex < $1.zIndex })) { item in
                    OverlayPreviewItemView(
                        item: item,
                        canvasSize: geo.size,
                        editable: editable,
                        isSelected: editable && viewModel.selectedOverlayID == item.id
                    )
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .allowsHitTesting(editable)
    }
}

struct OverlayPreviewItemView: View {
    @EnvironmentObject var viewModel: ViewModel
    let item: OverlayItem
    let canvasSize: CGSize
    let editable: Bool
    let isSelected: Bool

    @State private var dragOrigin: OverlayPlacement?
    @State private var resizeOrigin: OverlayPlacement?

    private var frame: CGRect {
        item.placement.rect(in: canvasSize)
    }

    var body: some View {
        ZStack {
            previewContent
            if showsResizeHandles {
                resizeHandles
            }
        }
        .frame(width: frame.width, height: frame.height)
        .background(
            RoundedRectangle(cornerRadius: 10)
                .fill(isSelected ? Color.accentColor.opacity(0.12) : Color.clear)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 10)
                .stroke(isSelected ? Color.accentColor : Color.clear, style: StrokeStyle(lineWidth: 1.5, dash: [5, 4]))
        )
        .opacity(item.opacity)
        .position(x: frame.midX, y: frame.midY)
        .onTapGesture(count: 2) {
            guard editable else { return }
            if isTextOverlay {
                viewModel.requestTextOverlayEditing(item.id)
            } else {
                viewModel.selectOverlay(item.id)
            }
        }
        .onTapGesture {
            guard editable else { return }
            viewModel.selectOverlay(item.id)
        }
        .gesture(
            DragGesture()
                .onChanged { value in
                    guard editable, !item.isLocked else { return }
                    if dragOrigin == nil {
                        dragOrigin = item.placement
                    }
                    guard let dragOrigin else { return }
                    viewModel.selectOverlay(item.id)
                    viewModel.updateOverlay(id: item.id) { overlay in
                        overlay.placement.normalizedCenterX = dragOrigin.normalizedCenterX + Double(value.translation.width / max(canvasSize.width, 1))
                        overlay.placement.normalizedCenterY = dragOrigin.normalizedCenterY + Double(value.translation.height / max(canvasSize.height, 1))
                    }
                }
                .onEnded { _ in
                    dragOrigin = nil
                }
        )
    }

    private var showsResizeHandles: Bool {
        editable && isSelected && !item.isLocked
    }

    private var isTextOverlay: Bool {
        if case .text = item.content {
            return true
        }
        return false
    }

    private var resizeHandles: some View {
        GeometryReader { proxy in
            ZStack {
                resizeHandle(alignmentX: 0, alignmentY: 0, xSign: -1, ySign: -1, in: proxy.size)
                resizeHandle(alignmentX: 1, alignmentY: 0, xSign: 1, ySign: -1, in: proxy.size)
                resizeHandle(alignmentX: 0, alignmentY: 1, xSign: -1, ySign: 1, in: proxy.size)
                resizeHandle(alignmentX: 1, alignmentY: 1, xSign: 1, ySign: 1, in: proxy.size)
            }
        }
    }

    private func resizeHandle(
        alignmentX: CGFloat,
        alignmentY: CGFloat,
        xSign: Double,
        ySign: Double,
        in size: CGSize
    ) -> some View {
        Circle()
            .fill(Color.white)
            .frame(width: 10, height: 10)
            .overlay(
                Circle()
                    .stroke(Color.accentColor, lineWidth: 1.5)
            )
            .shadow(color: .black.opacity(0.18), radius: 2, y: 1)
            .position(x: alignmentX * size.width, y: alignmentY * size.height)
            .gesture(
                DragGesture()
                    .onChanged { value in
                        if resizeOrigin == nil {
                            resizeOrigin = item.placement
                        }
                        guard let resizeOrigin else { return }
                        viewModel.selectOverlay(item.id)
                        viewModel.updateOverlay(id: item.id) { overlay in
                            overlay.placement.normalizedWidth = resizeOrigin.normalizedWidth + (2 * Double(value.translation.width) / max(canvasSize.width, 1)) * xSign
                            overlay.placement.normalizedHeight = resizeOrigin.normalizedHeight + (2 * Double(value.translation.height) / max(canvasSize.height, 1)) * ySign
                        }
                    }
                    .onEnded { _ in
                        resizeOrigin = nil
                    }
            )
    }

    @ViewBuilder
    private var previewContent: some View {
        switch item.content {
        case .text(let data):
            OverlayTextPreviewView(data: data, size: frame.size)
        case .qrCode(let data):
            OverlayQRCodePreviewView(data: data)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        case .timestamp(let data):
            TimestampPreviewView(data: data, size: frame.size)
        case .image(let data):
            OverlayImagePreviewView(data: data)
        case .location(let data):
            OverlayLocationPreviewView(data: data, size: frame.size)
        }
    }
}

struct OverlayTextPreviewView: View {
    let data: TextOverlayData
    let size: CGSize

    var body: some View {
        Text(data.text)
            .font(.system(size: max(12, size.height * CGFloat(max(data.fontScale, 0.05)) * 1.6), weight: .semibold, design: .rounded))
            .foregroundColor(data.foregroundColor.color)
            .multilineTextAlignment(textAlignment)
            .lineLimit(data.allowsMultipleLines ? 3 : 1)
            .minimumScaleFactor(0.4)
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(
                RoundedRectangle(cornerRadius: 10)
                    .fill(data.backgroundColor.color)
            )
            .shadow(color: shadowColor, radius: shadowRadius)
    }

    private var textAlignment: TextAlignment {
        switch data.textAlignment {
        case .leading: return .leading
        case .center: return .center
        case .trailing: return .trailing
        }
    }

    private var shadowColor: Color {
        switch data.shadowStyle {
        case .none: return .clear
        case .soft: return .black.opacity(0.35)
        case .strong: return .black.opacity(0.65)
        }
    }

    private var shadowRadius: CGFloat {
        switch data.shadowStyle {
        case .none: return 0
        case .soft: return 4
        case .strong: return 8
        }
    }
}

struct OverlayQRCodePreviewView: View {
    @EnvironmentObject var viewModel: ViewModel
    let data: QROverlayData

    var body: some View {
        GeometryReader { proxy in
            let size = proxy.size
            let captionHeight = (data.showsCaption && !data.caption.isEmpty) ? size.height * 0.16 : 0
            let codeHeight = max(0, size.height - captionHeight)
            let quietZonePadding = data.includesQuietZone ? min(size.width, codeHeight) * 0.08 : 0

            VStack(spacing: 0) {
                Group {
                    if let image = PrintRenderService.qrCodeImage(for: data) {
                        Image(nsImage: image)
                            .resizable()
                            .interpolation(.none)
                            .aspectRatio(1, contentMode: .fit)
                            .padding(quietZonePadding)
                    } else {
                        RoundedRectangle(cornerRadius: 8)
                            .fill(Color.secondary.opacity(0.15))
                            .overlay(Image(systemName: "qrcode").foregroundColor(.secondary))
                            .padding(quietZonePadding)
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .frame(height: codeHeight)

                if captionHeight > 0 {
                    Text(data.caption)
                        .font(.caption2)
                        .foregroundColor(data.foregroundColor.color)
                        .multilineTextAlignment(.center)
                        .lineLimit(2)
                        .minimumScaleFactor(0.5)
                        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .center)
                        .frame(height: captionHeight)
                }
            }
            .frame(width: size.width, height: size.height)
            .background(
                RoundedRectangle(cornerRadius: 10)
                    .fill(data.backgroundColor.color)
            )
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

struct TimestampPreviewView: View {
    @EnvironmentObject var viewModel: ViewModel
    let data: TimestampOverlayData
    let size: CGSize

    var body: some View {
        let date = PrintRenderService.resolvedTimestampDate(for: data, imageDate: viewModel.imageDate)
        let preset = TimestampPresetCatalog.presets[data.presetKey] ?? TimestampPresetCatalog.presets["classic"]!
        let stampColor = Color(red: preset.color.0, green: preset.color.1, blue: preset.color.2)
        let fontSize = PrintRenderService.timestampFontSize(for: data, preset: preset, rectHeight: size.height)

        VStack(spacing: fontSize * 0.12) {
            Text(PrintRenderService.timestampText(from: date, data: data, preset: preset))
                .font(timestampFont(for: preset, size: fontSize))
                .tracking(fontSize * preset.tracking)
                .foregroundColor(stampColor)
            if data.showsTime {
                Text(PrintRenderService.timeStampText(from: date))
                    .font(timestampFont(for: preset, size: fontSize))
                    .tracking(fontSize * preset.tracking)
                    .foregroundColor(stampColor)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .shadow(
            color: data.lightBleedEnabled && preset.glowRadius > 0 ? stampColor.opacity(0.8) : .clear,
            radius: data.lightBleedEnabled ? fontSize * preset.glowRadius * 0.5 : 0
        )
    }

    private func timestampFont(for preset: DateStampPreset, size: CGFloat) -> Font {
        switch preset.fontStyle {
        case .custom(let name):
            return .custom(name, size: size)
        case .systemMonospaced:
            return .system(size: size, weight: .medium, design: .monospaced)
        }
    }
}

struct OverlayImagePreviewView: View {
    let data: ImageOverlayData

    var body: some View {
        let image = NSImage(data: data.asset.imageData)
        ZStack {
            if data.showsBacking {
                RoundedRectangle(cornerRadius: data.cornerRadius)
                    .fill(data.backingColor.color)
            }
            if let image {
                Image(nsImage: image)
                    .resizable()
                    .aspectRatio(contentMode: data.contentMode == .fit ? .fit : .fill)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .clipShape(RoundedRectangle(cornerRadius: data.cornerRadius))
            } else {
                RoundedRectangle(cornerRadius: data.cornerRadius)
                    .fill(Color.secondary.opacity(0.12))
                    .overlay(Image(systemName: "photo").foregroundColor(.secondary))
            }
        }
        .clipShape(RoundedRectangle(cornerRadius: data.cornerRadius))
    }
}

struct OverlayLocationPreviewView: View {
    @EnvironmentObject var viewModel: ViewModel
    let data: LocationOverlayData
    let size: CGSize

    var body: some View {
        Text(
            PrintRenderService.resolvedLocationText(
                for: data,
                imageLocation: viewModel.imageLocation
            ) ?? L("No location metadata")
        )
            .font(.system(size: max(10, size.height * 0.22), weight: .medium, design: .monospaced))
            .foregroundColor(.white)
            .multilineTextAlignment(.center)
            .lineLimit(3)
            .minimumScaleFactor(0.5)
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(Color.black.opacity(0.28))
            .clipShape(RoundedRectangle(cornerRadius: 10))
            .shadow(color: .black.opacity(0.35), radius: 4)
    }
}

struct PresetCard: View {
    let preset: DateStampPreset
    let isSelected: Bool

    var body: some View {
        Text(L(preset.displayName))
            .font(.system(size: 9, weight: .medium))
            .foregroundColor(isSelected ? Color(red: preset.color.0, green: preset.color.1, blue: preset.color.2) : .secondary)
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(
                RoundedRectangle(cornerRadius: 4).fill(
                    isSelected ? Color(red: preset.color.0, green: preset.color.1, blue: preset.color.2).opacity(0.15) : Color.clear
                )
            )
            .overlay(
                RoundedRectangle(cornerRadius: 4).stroke(
                    isSelected ? Color(red: preset.color.0, green: preset.color.1, blue: preset.color.2).opacity(0.5) : Color.gray.opacity(0.3),
                    lineWidth: 1
                )
            )
    }
}
