import AppKit
import AVFoundation
import CoreImage
import Foundation

enum PrintRenderService {
    struct Request {
        let sourcePath: String
        let sourceImage: NSImage
        let fitMode: String
        let cropOffsetNormalized: CGSize
        let cropZoom: CGFloat
        let rotationAngle: Int
        let isHorizontallyFlipped: Bool
        let overlays: [OverlayItem]
        let filmOrientation: String
        let printerAspectRatio: CGFloat?
        let imageDate: Date?
        let imageLocation: ImageLocationMetadata?
    }

    struct PreparedPrint {
        let path: String
        let fitModeForPrinter: String
        let temporaryFilePath: String?
    }

    private struct OverlayRenderContext {
        let imageDate: Date?
        let imageLocation: ImageLocationMetadata?
    }

    static func preparePrint(_ request: Request) -> PreparedPrint? {
        guard let cgImage = request.sourceImage.cgImage(
            forProposedRect: nil,
            context: nil,
            hints: nil
        ) else {
            return nil
        }

        var currentCG = cgImage
        var processed = false
        var renderedToFinalCanvas = false
        let hasVisibleOverlays = request.overlays.contains { !$0.isHidden }

        if request.fitMode == "crop",
           let cropped = cropCGImage(
               currentCG,
               targetAspectRatio: orientedAspectRatio(
                   printerAspectRatio: request.printerAspectRatio,
                   filmOrientation: request.filmOrientation
               ),
               cropOffsetNormalized: request.cropOffsetNormalized,
               cropZoom: request.cropZoom
           ) {
            currentCG = cropped
            processed = true
        }

        if request.isHorizontallyFlipped,
           let flipped = flipCGImageHorizontally(currentCG) {
            currentCG = flipped
            processed = true
        }

        let normalizedRotation = normalizedRightAngle(request.rotationAngle)
        if normalizedRotation != 0,
           let rotated = rotateCGImage(currentCG, degrees: normalizedRotation) {
            currentCG = rotated
            processed = true
        }

        if (processed || hasVisibleOverlays),
           let targetAspectRatio = orientedAspectRatio(
               printerAspectRatio: request.printerAspectRatio,
               filmOrientation: request.filmOrientation
           ),
           let canvasImage = renderImageForPrintCanvas(
               currentCG,
               fitMode: request.fitMode,
               targetAspectRatio: targetAspectRatio
           ) {
            currentCG = canvasImage
            processed = true
            renderedToFinalCanvas = true
        }

        if hasVisibleOverlays,
           let composited = composeOverlays(
               on: currentCG,
               overlays: request.overlays,
               context: OverlayRenderContext(
                   imageDate: request.imageDate,
                   imageLocation: request.imageLocation
               )
           ) {
            currentCG = composited
            processed = true
        }

        if request.filmOrientation == "rotated",
           let ar = request.printerAspectRatio,
           ar != 1.0,
           let rotated = rotateCGImage(currentCG, degrees: 90) {
            currentCG = rotated
            processed = true
            renderedToFinalCanvas = true
        }

        if !processed {
            return PreparedPrint(
                path: request.sourcePath,
                fitModeForPrinter: request.fitMode,
                temporaryFilePath: nil
            )
        }

        let tempURL = FileManager.default.temporaryDirectory
            .appendingPathComponent("instantlink_print_\(UUID().uuidString).jpg")
        let bitmapRep = NSBitmapImageRep(cgImage: currentCG)
        guard let jpegData = bitmapRep.representation(
            using: .jpeg,
            properties: [.compressionFactor: 0.95]
        ) else {
            return nil
        }

        do {
            try jpegData.write(to: tempURL)
            return PreparedPrint(
                path: tempURL.path,
                fitModeForPrinter: renderedToFinalCanvas ? "stretch" : request.fitMode,
                temporaryFilePath: tempURL.path
            )
        } catch {
            return nil
        }
    }

    static func timestampText(from date: Date, format: TimestampFormat, separator: String) -> String {
        let cal = Calendar.current
        let y = cal.component(.year, from: date) % 100
        let m = cal.component(.month, from: date)
        let d = cal.component(.day, from: date)
        let yy = String(format: "%02d", y)
        let mm = String(format: "%02d", m)
        let dd = String(format: "%02d", d)
        switch format {
        case .mdy:
            return "\(mm)\(separator)\(dd)\(separator)\(yy)"
        case .dmy:
            return "\(dd)\(separator)\(mm)\(separator)\(yy)"
        case .ymd:
            return "\(yy)\(separator)\(mm)\(separator)\(dd)"
        }
    }

    static func timeStampText(from date: Date) -> String {
        let cal = Calendar.current
        return String(
            format: "%02d:%02d",
            cal.component(.hour, from: date),
            cal.component(.minute, from: date)
        )
    }

    static func resolvedTimestampDate(
        for _: TimestampOverlayData,
        imageDate: Date?
    ) -> Date {
        imageDate ?? Date()
    }

    static func resolvedLocationText(
        for data: LocationOverlayData,
        imageLocation: ImageLocationMetadata?
    ) -> String? {
        let coordinate: GeoCoordinate?
        switch data.source {
        case .photoMetadata:
            coordinate = imageLocation?.coordinate
        case .manualCoordinates:
            coordinate = data.coordinate
        case .manualText:
            coordinate = nil
        }

        let precision = max(0, min(data.precision, 6))
        let coordinateText: String?
        if let coordinate {
            coordinateText = String(
                format: "%.\(precision)f, %.\(precision)f",
                coordinate.latitude,
                coordinate.longitude
            )
        } else {
            coordinateText = nil
        }

        let trimmedName = data.locationName.trimmingCharacters(in: .whitespacesAndNewlines)
        let body: String?
        switch data.displayStyle {
        case .coordinates:
            body = coordinateText
        case .name:
            body = trimmedName.isEmpty ? coordinateText : trimmedName
        case .nameAndCoordinates:
            if !trimmedName.isEmpty, let coordinateText {
                body = "\(trimmedName)\n\(coordinateText)"
            } else {
                body = !trimmedName.isEmpty ? trimmedName : coordinateText
            }
        }

        guard let body, !body.isEmpty else { return nil }
        return body
    }

    static func qrCodeImage(for data: QROverlayData) -> NSImage? {
        guard let payload = data.payload.data(using: .utf8),
              let qrFilter = CIFilter(name: "CIQRCodeGenerator") else {
            return nil
        }
        qrFilter.setValue(payload, forKey: "inputMessage")
        qrFilter.setValue(data.correctionLevel.coreImageValue, forKey: "inputCorrectionLevel")
        guard let output = qrFilter.outputImage else {
            return nil
        }

        let falseColor = CIFilter(name: "CIFalseColor")
        falseColor?.setValue(output, forKey: kCIInputImageKey)
        falseColor?.setValue(CIColor(cgColor: nsColor(from: data.foregroundColor).cgColor), forKey: "inputColor0")
        falseColor?.setValue(CIColor(cgColor: nsColor(from: data.backgroundColor).cgColor), forKey: "inputColor1")
        let colored = falseColor?.outputImage ?? output
        let scaled = colored.transformed(by: CGAffineTransform(scaleX: 16, y: 16))
        let context = CIContext(options: nil)
        guard let cgImage = context.createCGImage(scaled, from: scaled.extent) else {
            return nil
        }
        return NSImage(cgImage: cgImage, size: NSSize(width: cgImage.width, height: cgImage.height))
    }

    private static func nsColor(from color: OverlayColor) -> NSColor {
        NSColor(
            srgbRed: CGFloat(color.red),
            green: CGFloat(color.green),
            blue: CGFloat(color.blue),
            alpha: CGFloat(color.alpha)
        )
    }

    private static func normalizedRightAngle(_ degrees: Int) -> Int {
        let normalized = ((degrees % 360) + 360) % 360
        switch normalized {
        case 90, 180, 270:
            return normalized
        default:
            return 0
        }
    }

    private static func orientedAspectRatio(
        printerAspectRatio: CGFloat?,
        filmOrientation: String
    ) -> CGFloat? {
        guard let ar = printerAspectRatio else { return nil }
        if filmOrientation == "rotated" && ar != 1.0 {
            return 1.0 / ar
        }
        return ar
    }

    private static func cropCGImage(
        _ cgImage: CGImage,
        targetAspectRatio: CGFloat?,
        cropOffsetNormalized: CGSize,
        cropZoom: CGFloat
    ) -> CGImage? {
        guard let targetAspectRatio,
              cropZoom > 0 else {
            return nil
        }

        let pixelW = CGFloat(cgImage.width)
        let pixelH = CGFloat(cgImage.height)
        let imageAR = pixelW / pixelH
        let hasManualCropAdjustment = cropOffsetNormalized != .zero || abs(cropZoom - 1.0) > 0.001
        if !hasManualCropAdjustment, abs(imageAR - targetAspectRatio) < 0.0001 {
            return nil
        }
        let cropRectSize: CGSize
        if imageAR > targetAspectRatio {
            cropRectSize = CGSize(
                width: pixelH * targetAspectRatio / cropZoom,
                height: pixelH / cropZoom
            )
        } else {
            cropRectSize = CGSize(
                width: pixelW / cropZoom,
                height: pixelW / targetAspectRatio / cropZoom
            )
        }
        let maxOffset = CGSize(
            width: max(0, (pixelW - cropRectSize.width) / 2),
            height: max(0, (pixelH - cropRectSize.height) / 2)
        )
        let normalizedOffset = CGSize(
            width: min(max(cropOffsetNormalized.width, -1), 1),
            height: min(max(cropOffsetNormalized.height, -1), 1)
        )
        let origin = CGPoint(
            x: maxOffset.width + normalizedOffset.width * maxOffset.width,
            y: maxOffset.height + normalizedOffset.height * maxOffset.height
        )

        let cropRect = CGRect(
            x: origin.x,
            y: origin.y,
            width: cropRectSize.width,
            height: cropRectSize.height
        )

        let bounds = CGRect(x: 0, y: 0, width: pixelW, height: pixelH)
        let clampedRect = cropRect.intersection(bounds)
        guard !clampedRect.isEmpty else { return nil }

        return cgImage.cropping(to: clampedRect)
    }

    private static func overlayRect(for item: OverlayItem, canvasSize: CGSize) -> CGRect {
        let rect = item.placement.rect(in: canvasSize)
        return CGRect(
            x: rect.minX,
            y: canvasSize.height - rect.maxY,
            width: rect.width,
            height: rect.height
        )
    }

    private static func overlayShadow(
        for style: OverlayShadowStyle,
        color: NSColor = .black
    ) -> NSShadow? {
        guard style != .none else { return nil }
        let shadow = NSShadow()
        shadow.shadowColor = color.withAlphaComponent(style == .strong ? 0.85 : 0.45)
        shadow.shadowBlurRadius = style == .strong ? 10 : 4
        shadow.shadowOffset = CGSize(width: 0, height: -1)
        return shadow
    }

    private static func imageFromOverlayAsset(_ asset: OverlayImageAsset) -> NSImage? {
        NSImage(data: asset.imageData)
    }

    private static func drawTextOverlay(_ data: TextOverlayData, in rect: CGRect) {
        guard !data.text.isEmpty else { return }
        let fontSize = max(14, rect.height * CGFloat(max(data.fontScale, 0.05)) * 1.8)
        let font = NSFont.systemFont(ofSize: fontSize, weight: .semibold)
        let paragraph = NSMutableParagraphStyle()
        switch data.textAlignment {
        case .leading:
            paragraph.alignment = .left
        case .center:
            paragraph.alignment = .center
        case .trailing:
            paragraph.alignment = .right
        }
        paragraph.lineBreakMode = data.allowsMultipleLines ? .byWordWrapping : .byTruncatingTail

        var attributes: [NSAttributedString.Key: Any] = [
            .font: font,
            .foregroundColor: nsColor(from: data.foregroundColor),
            .paragraphStyle: paragraph,
        ]
        if let shadow = overlayShadow(for: data.shadowStyle) {
            attributes[.shadow] = shadow
        }

        if data.backgroundColor.alpha > 0.01 {
            nsColor(from: data.backgroundColor).setFill()
            NSBezierPath(
                roundedRect: rect.insetBy(dx: -6, dy: -4),
                xRadius: 12,
                yRadius: 12
            ).fill()
        }

        NSAttributedString(string: data.text, attributes: attributes).draw(
            with: rect,
            options: [.usesLineFragmentOrigin, .usesFontLeading],
            context: nil
        )
    }

    private static func drawTimestampOverlay(
        _ data: TimestampOverlayData,
        in rect: CGRect,
        context: OverlayRenderContext
    ) {
        let preset = TimestampPresetCatalog.presets[data.presetKey]
            ?? TimestampPresetCatalog.presets["classic"]!
        let date = resolvedTimestampDate(for: data, imageDate: context.imageDate)
        let body = data.showsTime
            ? "\(timestampText(from: date, format: data.format, separator: preset.separator))\n\(timeStampText(from: date))"
            : timestampText(from: date, format: data.format, separator: preset.separator)
        let fontSize = timestampFontSize(for: data, preset: preset, rectHeight: rect.height)
        let font = NSFont(name: preset.fontFamily, size: fontSize)
            ?? NSFont.monospacedDigitSystemFont(ofSize: fontSize, weight: .medium)
        let paragraph = NSMutableParagraphStyle()
        paragraph.alignment = .center

        var attributes: [NSAttributedString.Key: Any] = [
            .font: font,
            .foregroundColor: NSColor(
                srgbRed: preset.color.0,
                green: preset.color.1,
                blue: preset.color.2,
                alpha: 1
            ),
            .paragraphStyle: paragraph,
            .kern: fontSize * preset.tracking,
        ]
        if data.lightBleedEnabled && preset.glowRadius > 0 {
            let glow = NSShadow()
            glow.shadowColor = NSColor(
                srgbRed: preset.glowColor.0,
                green: preset.glowColor.1,
                blue: preset.glowColor.2,
                alpha: 0.65
            )
            glow.shadowBlurRadius = fontSize * preset.glowRadius
            glow.shadowOffset = .zero
            attributes[.shadow] = glow
        }

        NSAttributedString(string: body, attributes: attributes).draw(
            with: rect,
            options: [.usesLineFragmentOrigin, .usesFontLeading],
            context: nil
        )
    }

    static func timestampFontSize(
        for data: TimestampOverlayData,
        preset: DateStampPreset,
        rectHeight: CGFloat
    ) -> CGFloat {
        let classicSize = TimestampPresetCatalog.presets["classic"]?.sizePercent ?? preset.sizePercent
        let relativeScale = CGFloat(preset.sizePercent / max(classicSize, 0.0001))
        let baseMultiplier: CGFloat = data.showsTime ? 0.34 : 0.58
        return max(10, rectHeight * baseMultiplier * relativeScale)
    }

    private static func drawLocationOverlay(
        _ data: LocationOverlayData,
        in rect: CGRect,
        context: OverlayRenderContext
    ) {
        guard let text = resolvedLocationText(for: data, imageLocation: context.imageLocation) else {
            return
        }
        let font = NSFont.monospacedSystemFont(
            ofSize: max(10, rect.height * 0.28),
            weight: .medium
        )
        let paragraph = NSMutableParagraphStyle()
        paragraph.alignment = .center
        var attributes: [NSAttributedString.Key: Any] = [
            .font: font,
            .foregroundColor: NSColor.white,
            .paragraphStyle: paragraph,
        ]
        if let shadow = overlayShadow(for: .soft) {
            attributes[.shadow] = shadow
        }
        NSAttributedString(string: text, attributes: attributes).draw(
            with: rect,
            options: [.usesLineFragmentOrigin, .usesFontLeading],
            context: nil
        )
    }

    private static func drawImageOverlay(_ data: ImageOverlayData, in rect: CGRect) {
        guard let image = imageFromOverlayAsset(data.asset) else { return }
        if data.showsBacking {
            nsColor(from: data.backingColor).setFill()
            NSBezierPath(
                roundedRect: rect,
                xRadius: CGFloat(data.cornerRadius),
                yRadius: CGFloat(data.cornerRadius)
            ).fill()
        }

        NSGraphicsContext.current?.saveGraphicsState()
        NSBezierPath(
            roundedRect: rect,
            xRadius: CGFloat(data.cornerRadius),
            yRadius: CGFloat(data.cornerRadius)
        ).addClip()

        let imageRect: CGRect
        switch data.contentMode {
        case .fit:
            imageRect = AVMakeRect(aspectRatio: image.size, insideRect: rect)
        case .fill:
            let fitRect = AVMakeRect(aspectRatio: image.size, insideRect: rect)
            let scale = max(
                rect.width / max(fitRect.width, 1),
                rect.height / max(fitRect.height, 1)
            )
            let scaledSize = CGSize(
                width: fitRect.width * scale,
                height: fitRect.height * scale
            )
            imageRect = CGRect(
                x: rect.midX - scaledSize.width / 2,
                y: rect.midY - scaledSize.height / 2,
                width: scaledSize.width,
                height: scaledSize.height
            )
        }
        image.draw(in: imageRect)
        NSGraphicsContext.current?.restoreGraphicsState()
    }

    private static func drawQRCodeOverlay(_ data: QROverlayData, in rect: CGRect) {
        guard let image = qrCodeImage(for: data) else { return }
        let codeRect: CGRect
        if data.showsCaption {
            codeRect = CGRect(
                x: rect.minX,
                y: rect.minY + rect.height * 0.16,
                width: rect.width,
                height: rect.height * 0.84
            )
        } else {
            codeRect = rect
        }

        let drawRect = data.includesQuietZone
            ? codeRect.insetBy(dx: codeRect.width * 0.08, dy: codeRect.height * 0.08)
            : codeRect
        image.draw(in: drawRect)

        if data.showsCaption, !data.caption.isEmpty {
            let font = NSFont.systemFont(
                ofSize: max(10, rect.height * 0.11),
                weight: .medium
            )
            let paragraph = NSMutableParagraphStyle()
            paragraph.alignment = .center
            let attributes: [NSAttributedString.Key: Any] = [
                .font: font,
                .foregroundColor: nsColor(from: data.foregroundColor),
                .paragraphStyle: paragraph,
            ]
            NSAttributedString(string: data.caption, attributes: attributes).draw(
                with: CGRect(
                    x: rect.minX,
                    y: rect.minY,
                    width: rect.width,
                    height: rect.height * 0.16
                ),
                options: [.usesLineFragmentOrigin, .usesFontLeading],
                context: nil
            )
        }
    }

    private static func composeOverlays(
        on cgImage: CGImage,
        overlays: [OverlayItem],
        context: OverlayRenderContext
    ) -> CGImage? {
        let visibleOverlays = overlays
            .filter { !$0.isHidden }
            .sorted { $0.zIndex < $1.zIndex }
        guard !visibleOverlays.isEmpty else {
            return nil
        }

        let rep = NSBitmapImageRep(
            bitmapDataPlanes: nil,
            pixelsWide: cgImage.width,
            pixelsHigh: cgImage.height,
            bitsPerSample: 8,
            samplesPerPixel: 4,
            hasAlpha: true,
            isPlanar: false,
            colorSpaceName: .deviceRGB,
            bitmapFormat: [],
            bytesPerRow: 0,
            bitsPerPixel: 0
        )
        guard let rep,
              let graphicsContext = NSGraphicsContext(bitmapImageRep: rep) else {
            return nil
        }

        let baseImage = NSImage(
            cgImage: cgImage,
            size: NSSize(width: cgImage.width, height: cgImage.height)
        )
        let canvasSize = CGSize(width: cgImage.width, height: cgImage.height)

        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = graphicsContext
        graphicsContext.imageInterpolation = .high

        baseImage.draw(in: CGRect(origin: .zero, size: canvasSize))
        for overlay in visibleOverlays {
            let rect = overlayRect(for: overlay, canvasSize: canvasSize)
            NSGraphicsContext.current?.cgContext.saveGState()
            NSGraphicsContext.current?.cgContext.setAlpha(CGFloat(overlay.opacity))

            switch overlay.content {
            case .text(let data):
                drawTextOverlay(data, in: rect)
            case .qrCode(let data):
                drawQRCodeOverlay(data, in: rect)
            case .timestamp(let data):
                drawTimestampOverlay(data, in: rect, context: context)
            case .image(let data):
                drawImageOverlay(data, in: rect)
            case .location(let data):
                drawLocationOverlay(data, in: rect, context: context)
            }

            NSGraphicsContext.current?.cgContext.restoreGState()
        }

        NSGraphicsContext.restoreGraphicsState()
        return rep.cgImage
    }

    private static func rotateCGImage(_ cgImage: CGImage, degrees: Int) -> CGImage? {
        let w = cgImage.width
        let h = cgImage.height
        let colorSpace = CGColorSpaceCreateDeviceRGB()

        let newW: Int
        let newH: Int
        if degrees == 90 || degrees == 270 {
            newW = h
            newH = w
        } else {
            newW = w
            newH = h
        }

        guard let context = CGContext(
            data: nil,
            width: newW,
            height: newH,
            bitsPerComponent: 8,
            bytesPerRow: 0,
            space: colorSpace,
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        ) else {
            return nil
        }

        switch degrees {
        case 90:
            context.translateBy(x: CGFloat(newW), y: 0)
            context.rotate(by: .pi / 2)
        case 180:
            context.translateBy(x: CGFloat(newW), y: CGFloat(newH))
            context.rotate(by: .pi)
        case 270:
            context.translateBy(x: 0, y: CGFloat(newH))
            context.rotate(by: -.pi / 2)
        default:
            break
        }

        context.draw(cgImage, in: CGRect(x: 0, y: 0, width: w, height: h))
        return context.makeImage()
    }

    private static func flipCGImageHorizontally(_ cgImage: CGImage) -> CGImage? {
        let w = cgImage.width
        let h = cgImage.height
        let colorSpace = CGColorSpaceCreateDeviceRGB()

        guard let context = CGContext(
            data: nil,
            width: w,
            height: h,
            bitsPerComponent: 8,
            bytesPerRow: 0,
            space: colorSpace,
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        ) else {
            return nil
        }

        context.translateBy(x: CGFloat(w), y: 0)
        context.scaleBy(x: -1, y: 1)
        context.draw(cgImage, in: CGRect(x: 0, y: 0, width: w, height: h))
        return context.makeImage()
    }

    private static func renderImageForPrintCanvas(
        _ cgImage: CGImage,
        fitMode: String,
        targetAspectRatio: CGFloat
    ) -> CGImage? {
        let sourceSize = CGSize(width: cgImage.width, height: cgImage.height)
        let sourceAspectRatio = sourceSize.width / max(sourceSize.height, 1)
        let canvasSize: CGSize
        if fitMode == "crop" || abs(sourceAspectRatio - targetAspectRatio) < 0.0001 {
            canvasSize = sourceSize
        } else if sourceAspectRatio > targetAspectRatio {
            canvasSize = CGSize(
                width: sourceSize.width,
                height: sourceSize.width / targetAspectRatio
            )
        } else {
            canvasSize = CGSize(
                width: sourceSize.height * targetAspectRatio,
                height: sourceSize.height
            )
        }

        let rep = NSBitmapImageRep(
            bitmapDataPlanes: nil,
            pixelsWide: max(Int(canvasSize.width.rounded()), 1),
            pixelsHigh: max(Int(canvasSize.height.rounded()), 1),
            bitsPerSample: 8,
            samplesPerPixel: 4,
            hasAlpha: true,
            isPlanar: false,
            colorSpaceName: .deviceRGB,
            bitmapFormat: [],
            bytesPerRow: 0,
            bitsPerPixel: 0
        )
        guard let rep,
              let graphicsContext = NSGraphicsContext(bitmapImageRep: rep) else {
            return nil
        }

        let canvasRect = CGRect(origin: .zero, size: canvasSize)
        let drawRect: CGRect
        switch fitMode {
        case "contain":
            drawRect = AVMakeRect(aspectRatio: sourceSize, insideRect: canvasRect)
        case "stretch", "crop":
            drawRect = canvasRect
        default:
            drawRect = canvasRect
        }

        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = graphicsContext
        NSColor.white.setFill()
        canvasRect.fill()
        NSImage(
            cgImage: cgImage,
            size: NSSize(width: sourceSize.width, height: sourceSize.height)
        ).draw(in: drawRect)
        NSGraphicsContext.restoreGraphicsState()

        return rep.cgImage
    }
}
