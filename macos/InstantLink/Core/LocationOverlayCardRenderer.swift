import AppKit
import Foundation

enum LocationOverlayCardRenderer {
    struct ResolvedContent {
        let title: String
        let subtitle: String?
        let coordinate: GeoCoordinate?
        let isPlaceholder: Bool
    }

    static func resolvedContent(
        for data: LocationOverlayData,
        imageLocation: ImageLocationMetadata?,
        allowsPlaceholder: Bool
    ) -> ResolvedContent? {
        let trimmedName = data.locationName.trimmingCharacters(in: .whitespacesAndNewlines)
        let coordinate: GeoCoordinate?
        switch data.source {
        case .photoMetadata:
            coordinate = imageLocation?.coordinate
        case .manualCoordinates:
            coordinate = data.coordinate
        case .manualText:
            coordinate = nil
        }

        let coordinateText: String?
        if let coordinate, coordinate.isValid {
            let precision = max(0, min(data.precision, 6))
            coordinateText = String(
                format: "%.\(precision)f, %.\(precision)f",
                coordinate.latitude,
                coordinate.longitude
            )
        } else {
            coordinateText = nil
        }

        switch data.source {
        case .manualText:
            if !trimmedName.isEmpty {
                return ResolvedContent(
                    title: trimmedName,
                    subtitle: nil,
                    coordinate: nil,
                    isPlaceholder: false
                )
            }
        case .photoMetadata, .manualCoordinates:
            switch data.displayStyle {
            case .coordinates:
                if let coordinateText {
                    return ResolvedContent(
                        title: coordinateText,
                        subtitle: nil,
                        coordinate: coordinate,
                        isPlaceholder: false
                    )
                }
            case .name:
                if !trimmedName.isEmpty {
                    return ResolvedContent(
                        title: trimmedName,
                        subtitle: nil,
                        coordinate: coordinate,
                        isPlaceholder: false
                    )
                }
                if let coordinateText {
                    return ResolvedContent(
                        title: coordinateText,
                        subtitle: nil,
                        coordinate: coordinate,
                        isPlaceholder: false
                    )
                }
            case .nameAndCoordinates:
                if !trimmedName.isEmpty {
                    return ResolvedContent(
                        title: trimmedName,
                        subtitle: coordinateText,
                        coordinate: coordinate,
                        isPlaceholder: false
                    )
                }
                if let coordinateText {
                    return ResolvedContent(
                        title: coordinateText,
                        subtitle: nil,
                        coordinate: coordinate,
                        isPlaceholder: false
                    )
                }
            }
        }

        guard allowsPlaceholder else { return nil }
        return ResolvedContent(
            title: L("No location metadata"),
            subtitle: nil,
            coordinate: nil,
            isPlaceholder: true
        )
    }

    static func renderedImage(
        for data: LocationOverlayData,
        imageLocation: ImageLocationMetadata?,
        size: CGSize,
        scale: CGFloat = 2.0,
        allowsPlaceholder: Bool
    ) -> NSImage? {
        guard size.width > 4, size.height > 4 else { return nil }
        guard let content = resolvedContent(
            for: data,
            imageLocation: imageLocation,
            allowsPlaceholder: allowsPlaceholder
        ) else {
            return nil
        }

        let image = NSImage(size: size)
        image.lockFocus()
        guard let context = NSGraphicsContext.current?.cgContext else {
            image.unlockFocus()
            return nil
        }
        context.saveGState()
        context.scaleBy(x: 1 / max(scale, 1), y: 1 / max(scale, 1))
        draw(
            content: content,
            in: CGRect(origin: .zero, size: CGSize(width: size.width * max(scale, 1), height: size.height * max(scale, 1))),
            scale: max(scale, 1)
        )
        context.restoreGState()
        image.unlockFocus()
        return image
    }

    private static func draw(
        content: ResolvedContent,
        in rect: CGRect,
        scale: CGFloat
    ) {
        let cardRadius = max(10, min(rect.width, rect.height) * 0.12)
        let cardPath = NSBezierPath(
            roundedRect: rect.insetBy(dx: 1.0 * scale, dy: 1.0 * scale),
            xRadius: cardRadius,
            yRadius: cardRadius
        )

        NSGraphicsContext.current?.saveGraphicsState()
        let shadow = NSShadow()
        shadow.shadowColor = NSColor.black.withAlphaComponent(content.isPlaceholder ? 0.12 : 0.16)
        shadow.shadowBlurRadius = 10 * scale
        shadow.shadowOffset = CGSize(width: 0, height: -3 * scale)
        shadow.set()
        NSColor.black.withAlphaComponent(0.001).setFill()
        cardPath.fill()
        NSGraphicsContext.current?.restoreGraphicsState()

        cardPath.addClip()

        let cardBackground = NSGradient(
            colors: content.isPlaceholder
                ? [
                    NSColor(calibratedRed: 0.22, green: 0.24, blue: 0.28, alpha: 0.92),
                    NSColor(calibratedRed: 0.15, green: 0.16, blue: 0.19, alpha: 0.92),
                ]
                : [
                    NSColor(calibratedRed: 0.96, green: 0.95, blue: 0.91, alpha: 0.96),
                    NSColor(calibratedRed: 0.93, green: 0.95, blue: 0.89, alpha: 0.96),
                ]
        )
        cardBackground?.draw(in: cardPath, angle: -90)

        let padding = 8.0 * scale
        let mapHeight = rect.height * 0.58
        let mapRect = CGRect(
            x: rect.minX + padding,
            y: rect.maxY - mapHeight - padding,
            width: rect.width - (padding * 2),
            height: mapHeight
        )
        let infoRect = CGRect(
            x: rect.minX + padding,
            y: rect.minY + padding,
            width: rect.width - (padding * 2),
            height: rect.height - mapRect.height - (padding * 3)
        )

        drawMapThumbnail(in: mapRect, content: content, scale: scale)
        drawInfoPanel(in: infoRect, content: content, scale: scale)

        NSColor.white.withAlphaComponent(content.isPlaceholder ? 0.14 : 0.7).setStroke()
        cardPath.lineWidth = 1 * scale
        cardPath.stroke()
    }

    private static func drawMapThumbnail(
        in rect: CGRect,
        content: ResolvedContent,
        scale: CGFloat
    ) {
        let radius = max(8, min(rect.width, rect.height) * 0.16)
        let path = NSBezierPath(roundedRect: rect, xRadius: radius, yRadius: radius)
        let gradient = NSGradient(
            colors: content.isPlaceholder
                ? [
                    NSColor(calibratedRed: 0.28, green: 0.30, blue: 0.35, alpha: 1.0),
                    NSColor(calibratedRed: 0.18, green: 0.19, blue: 0.23, alpha: 1.0),
                ]
                : [
                    NSColor(calibratedRed: 0.86, green: 0.93, blue: 0.83, alpha: 1.0),
                    NSColor(calibratedRed: 0.80, green: 0.89, blue: 0.96, alpha: 1.0),
                ]
        )
        gradient?.draw(in: path, angle: -65)

        NSGraphicsContext.current?.saveGraphicsState()
        path.addClip()
        drawMapTexture(in: rect, content: content, scale: scale)
        drawMapPin(in: rect, content: content, scale: scale)
        NSGraphicsContext.current?.restoreGraphicsState()

        NSColor.white.withAlphaComponent(content.isPlaceholder ? 0.10 : 0.35).setStroke()
        path.lineWidth = 1 * scale
        path.stroke()
    }

    private static func drawMapTexture(
        in rect: CGRect,
        content: ResolvedContent,
        scale: CGFloat
    ) {
        let seed = content.coordinate.map { coordinateSeed(for: $0) } ?? UInt64(abs(content.title.hashValue))
        let roadColor = (content.isPlaceholder ? NSColor.white.withAlphaComponent(0.10) : NSColor.white.withAlphaComponent(0.45))
        let roadAccent = (content.isPlaceholder ? NSColor.white.withAlphaComponent(0.08) : NSColor(calibratedRed: 0.98, green: 0.80, blue: 0.57, alpha: 0.45))
        let gridColor = (content.isPlaceholder ? NSColor.white.withAlphaComponent(0.06) : NSColor.black.withAlphaComponent(0.06))

        for index in 0..<3 {
            let path = NSBezierPath()
            let startY = rect.minY + rect.height * pseudoFraction(seed, salt: UInt64(index + 1), min: 0.12, max: 0.88)
            path.move(to: CGPoint(x: rect.minX - 10 * scale, y: startY))
            path.curve(
                to: CGPoint(x: rect.maxX + 10 * scale, y: rect.minY + rect.height * pseudoFraction(seed, salt: UInt64(index + 11), min: 0.12, max: 0.88)),
                controlPoint1: CGPoint(
                    x: rect.minX + rect.width * pseudoFraction(seed, salt: UInt64(index + 21), min: 0.18, max: 0.42),
                    y: rect.minY + rect.height * pseudoFraction(seed, salt: UInt64(index + 31), min: 0.05, max: 0.95)
                ),
                controlPoint2: CGPoint(
                    x: rect.minX + rect.width * pseudoFraction(seed, salt: UInt64(index + 41), min: 0.58, max: 0.82),
                    y: rect.minY + rect.height * pseudoFraction(seed, salt: UInt64(index + 51), min: 0.05, max: 0.95)
                )
            )
            path.lineWidth = (index == 0 ? 4.5 : 2.5) * scale
            (index == 0 ? roadAccent : roadColor).setStroke()
            path.stroke()
        }

        let riverPath = NSBezierPath()
        riverPath.move(to: CGPoint(x: rect.minX + rect.width * 0.14, y: rect.maxY + 6 * scale))
        riverPath.curve(
            to: CGPoint(x: rect.maxX - rect.width * 0.10, y: rect.minY - 6 * scale),
            controlPoint1: CGPoint(x: rect.minX + rect.width * 0.34, y: rect.maxY - rect.height * 0.15),
            controlPoint2: CGPoint(x: rect.minX + rect.width * 0.60, y: rect.minY + rect.height * 0.18)
        )
        riverPath.lineWidth = 7 * scale
        (content.isPlaceholder
            ? NSColor.white.withAlphaComponent(0.05)
            : NSColor(calibratedRed: 0.36, green: 0.63, blue: 0.92, alpha: 0.22)
        ).setStroke()
        riverPath.stroke()

        let gridStep = max(14 * scale, rect.width / 5)
        var x = rect.minX + gridStep * 0.4
        while x < rect.maxX {
            let path = NSBezierPath()
            path.move(to: CGPoint(x: x, y: rect.minY))
            path.line(to: CGPoint(x: x, y: rect.maxY))
            path.lineWidth = 1 * scale
            gridColor.setStroke()
            path.stroke()
            x += gridStep
        }

        var y = rect.minY + gridStep * 0.3
        while y < rect.maxY {
            let path = NSBezierPath()
            path.move(to: CGPoint(x: rect.minX, y: y))
            path.line(to: CGPoint(x: rect.maxX, y: y))
            path.lineWidth = 1 * scale
            gridColor.setStroke()
            path.stroke()
            y += gridStep
        }
    }

    private static func drawMapPin(
        in rect: CGRect,
        content: ResolvedContent,
        scale: CGFloat
    ) {
        let location = pinLocation(in: rect, coordinate: content.coordinate)
        let pinRadius = max(8, rect.height * 0.12)
        let ringRect = CGRect(
            x: location.x - pinRadius * 0.95,
            y: location.y - pinRadius * 0.95,
            width: pinRadius * 1.9,
            height: pinRadius * 1.9
        )
        NSColor.white.withAlphaComponent(content.isPlaceholder ? 0.10 : 0.25).setFill()
        NSBezierPath(ovalIn: ringRect).fill()

        let stemPath = NSBezierPath()
        stemPath.move(to: CGPoint(x: location.x, y: location.y - pinRadius * 1.2))
        stemPath.line(to: CGPoint(x: location.x, y: location.y - pinRadius * 0.15))
        stemPath.lineWidth = 3 * scale
        (content.isPlaceholder ? NSColor.white.withAlphaComponent(0.38) : NSColor(calibratedRed: 0.90, green: 0.34, blue: 0.29, alpha: 1.0)).setStroke()
        stemPath.stroke()

        let pinRect = CGRect(
            x: location.x - pinRadius,
            y: location.y - pinRadius * 0.35,
            width: pinRadius * 2,
            height: pinRadius * 2
        )
        let pinPath = NSBezierPath(ovalIn: pinRect)
        (content.isPlaceholder ? NSColor.white.withAlphaComponent(0.28) : NSColor(calibratedRed: 0.90, green: 0.34, blue: 0.29, alpha: 1.0)).setFill()
        pinPath.fill()
        NSColor.white.withAlphaComponent(0.92).setStroke()
        pinPath.lineWidth = 2 * scale
        pinPath.stroke()

        let innerRect = pinRect.insetBy(dx: pinRadius * 0.42, dy: pinRadius * 0.42)
        (content.isPlaceholder ? NSColor.white.withAlphaComponent(0.95) : NSColor.white).setFill()
        NSBezierPath(ovalIn: innerRect).fill()
    }

    private static func drawInfoPanel(
        in rect: CGRect,
        content: ResolvedContent,
        scale: CGFloat
    ) {
        let titleFont = NSFont.systemFont(ofSize: max(10, rect.height * 0.34), weight: .semibold)
        let subtitleFont = NSFont.monospacedDigitSystemFont(ofSize: max(8, rect.height * 0.22), weight: .medium)
        let paragraph = NSMutableParagraphStyle()
        paragraph.alignment = .left
        paragraph.lineBreakMode = .byTruncatingTail

        let titleColor = content.isPlaceholder
            ? NSColor.white.withAlphaComponent(0.92)
            : NSColor(calibratedRed: 0.18, green: 0.20, blue: 0.22, alpha: 1.0)
        let subtitleColor = content.isPlaceholder
            ? NSColor.white.withAlphaComponent(0.68)
            : NSColor(calibratedRed: 0.27, green: 0.31, blue: 0.34, alpha: 0.92)

        let markerRect = CGRect(
            x: rect.minX,
            y: rect.maxY - max(10, rect.height * 0.34),
            width: max(10, rect.height * 0.26),
            height: max(10, rect.height * 0.26)
        )
        let markerColor = content.isPlaceholder
            ? NSColor.white.withAlphaComponent(0.9)
            : NSColor(calibratedRed: 0.90, green: 0.34, blue: 0.29, alpha: 1.0)
        let markerPath = NSBezierPath(ovalIn: markerRect)
        markerColor.setFill()
        markerPath.fill()
        NSColor.white.withAlphaComponent(0.9).setStroke()
        markerPath.lineWidth = 1.2 * scale
        markerPath.stroke()

        let markerDotRect = markerRect.insetBy(dx: markerRect.width * 0.34, dy: markerRect.height * 0.34)
        NSColor.white.setFill()
        NSBezierPath(ovalIn: markerDotRect).fill()

        let textX = rect.minX + max(14, rect.height * 0.30)
        let titleRect = CGRect(
            x: textX,
            y: rect.maxY - max(12, rect.height * 0.42),
            width: rect.width - (textX - rect.minX),
            height: max(12, rect.height * 0.42)
        )
        let titleString = NSAttributedString(
            string: content.title,
            attributes: [
                .font: titleFont,
                .foregroundColor: titleColor,
                .paragraphStyle: paragraph,
            ]
        )
        titleString.draw(in: titleRect)

        if let subtitle = content.subtitle {
            let subtitleRect = CGRect(
                x: textX,
                y: rect.minY + 1 * scale,
                width: rect.width - (textX - rect.minX),
                height: max(10, rect.height * 0.34)
            )
            let subtitleString = NSAttributedString(
                string: subtitle,
                attributes: [
                    .font: subtitleFont,
                    .foregroundColor: subtitleColor,
                    .paragraphStyle: paragraph,
                ]
            )
            subtitleString.draw(in: subtitleRect)
        }
    }

    private static func pinLocation(in rect: CGRect, coordinate: GeoCoordinate?) -> CGPoint {
        guard let coordinate, coordinate.isValid else {
            return CGPoint(x: rect.midX, y: rect.midY)
        }

        let normalizedX = CGFloat((coordinate.longitude + 180.0) / 360.0)
        let normalizedY = CGFloat(1.0 - ((coordinate.latitude + 90.0) / 180.0))
        let clampedX = min(max(normalizedX, 0.18), 0.82)
        let clampedY = min(max(normalizedY, 0.18), 0.82)
        return CGPoint(
            x: rect.minX + rect.width * clampedX,
            y: rect.minY + rect.height * clampedY
        )
    }

    private static func coordinateSeed(for coordinate: GeoCoordinate) -> UInt64 {
        let lat = UInt64(abs((coordinate.latitude * 10_000).rounded()))
        let lon = UInt64(abs((coordinate.longitude * 10_000).rounded()))
        return lat << 16 ^ lon
    }

    private static func pseudoFraction(
        _ seed: UInt64,
        salt: UInt64,
        min: CGFloat,
        max: CGFloat
    ) -> CGFloat {
        var value = seed ^ (salt &* 0x9E3779B97F4A7C15)
        value ^= value >> 30
        value &*= 0xBF58476D1CE4E5B9
        value ^= value >> 27
        value &*= 0x94D049BB133111EB
        value ^= value >> 31
        let normalized = CGFloat(Double(value % 10_000) / 10_000.0)
        return min + ((max - min) * normalized)
    }
}
