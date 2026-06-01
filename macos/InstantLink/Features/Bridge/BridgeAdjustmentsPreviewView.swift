import AppKit
import CoreImage
import CoreImage.CIFilterBuiltins
import SwiftUI

/// Live preview of the current Bridge Adjustments draft applied to a
/// shared reference photo.
///
/// The reference image ships at ``macos/Resources/AdjustmentsPreview.jpg``
/// and is the same procedural sky / foliage / skin / shadow swatch the
/// bridge's ``imaging/_example_photo.jpg`` uses for its own pipeline
/// tests — so what the user sees here is the closest the Mac can get to
/// the bridge's actual output without round-tripping a photo through
/// the printer. The Mac's Core Image pipeline approximates the bridge's
/// PIL/HSV pipeline; pixel-exact parity is *not* a goal and would
/// require duplicating the bridge image-processing code in Swift.
///
/// Rendering is debounced: every change to the draft starts a new
/// background render task that supersedes the previous one. Slider drag
/// stays smooth because we apply Core Image filters on a private
/// queue, then hop back to the main actor with the finished image.
struct BridgeAdjustmentsPreviewView: View {
    @ObservedObject var draft: BridgeSettingsDraft

    /// Output size for the preview surface. Callers can pick a smaller
    /// "thumbnail" footprint for the main Adjustments card and a larger
    /// one for the per-axis editor sheet. Defaults to the card size.
    var renderSize: CGSize = BridgeAdjustmentsPreviewView.defaultRenderSize

    /// Whether to render the "Live preview" caption above the image.
    /// Tracks how the surface is presented: the main card wants it on
    /// for context, the per-axis sheet has its own header and turns it
    /// off to avoid duplication.
    var showsCaption: Bool = true

    /// Cached CIImage of the bundled reference photo. Loaded once per
    /// view lifetime; nil when the bundle resource is missing (release
    /// builds without the asset, sandbox issues, …).
    @State private var baseImage: CIImage?
    /// The currently-rendered preview, updated on the main actor when
    /// a render task completes.
    @State private var renderedImage: NSImage?
    /// Outstanding render task; cancelled when a fresh edit arrives so
    /// in-flight renders don't fight with newer ones.
    @State private var renderTask: Task<Void, Never>?

    /// Core Image context reused across renders. ``CIContext`` is
    /// expensive to construct and safe to share read-only between
    /// invocations on a single view.
    private static let ciContext = CIContext()

    /// Default footprint for the main Adjustments card. The base image
    /// is 480 × 480; we letterbox into a 3:2 frame so the preview reads
    /// as a photo rather than the bridge's square print canvas.
    static let defaultRenderSize = CGSize(width: 360, height: 240)

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if showsCaption {
                HStack(spacing: 6) {
                    Image(systemName: "photo")
                        .font(.caption)
                        .foregroundColor(.secondary)
                    Text(L("Live preview"))
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
            }
            previewSurface
        }
        .onAppear(perform: loadBaseImageIfNeeded)
        .onChange(of: previewKey) { _, _ in scheduleRender() }
    }

    @ViewBuilder
    private var previewSurface: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .fill(Color.black.opacity(0.08))
            if let image = renderedImage {
                Image(nsImage: image)
                    .resizable()
                    .aspectRatio(contentMode: .fit)
                    .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
            } else if baseImage == nil {
                // Asset missing — render builds without the bundled JPEG
                // would land here. Surface clearly instead of an empty box.
                VStack(spacing: 4) {
                    Image(systemName: "exclamationmark.triangle")
                        .font(.body)
                        .foregroundColor(.secondary)
                    Text(L("Preview unavailable"))
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
            } else {
                // Brief first-paint window before the initial render
                // lands. Keeps the surface non-empty so the section
                // doesn't visibly grow/shrink as the image arrives.
                ProgressView()
                    .controlSize(.small)
            }
        }
        .frame(width: renderSize.width, height: renderSize.height)
        .frame(maxWidth: .infinity, alignment: .center)
    }

    // MARK: - Base image bootstrap

    private func loadBaseImageIfNeeded() {
        guard baseImage == nil else { return }
        guard let url = Bundle.main.url(forResource: "AdjustmentsPreview", withExtension: "jpg"),
              let raw = CIImage(contentsOf: url) else {
            baseImage = nil
            return
        }
        // The shared reference photo ships with heavy grain — intentional
        // for the bridge's 88 × 88 LCD pipeline tests, where the noise
        // reads as subtle film grain. At the Mac's 360 × 240+ display
        // size the same speckle gets magnified ~4× and dominates the
        // smooth color zones. Apply a single Gaussian once at load so
        // the user-controlled pipeline operates on a clean canvas;
        // cropping back to the source extent strips the blur's natural
        // border bleed. The radius is small enough that the silhouettes
        // (trees, color boundaries) still register, so sharpness still
        // has edges to bite into.
        let denoised = raw
            .applyingFilter(
                "CIGaussianBlur",
                parameters: [kCIInputRadiusKey: 1.2]
            )
            .cropped(to: raw.extent)
        baseImage = denoised
        scheduleRender()
    }

    // MARK: - Render scheduling

    /// Tuple of all values the preview depends on. SwiftUI's
    /// ``onChange`` re-fires whenever any element of this tuple
    /// differs, which is exactly the set of edits that should retrigger
    /// the Core Image pipeline.
    private var previewKey: PreviewSnapshot {
        let intValue: (String) -> Int = { key in
            (draft.adjustmentsValue(forKey: key) as? Int) ?? 0
        }
        let boolValue: (String) -> Bool = { key in
            (draft.adjustmentsValue(forKey: key) as? Bool) ?? false
        }
        let stringValue: (String) -> String = { key in
            (draft.adjustmentsValue(forKey: key) as? String) ?? ""
        }
        return PreviewSnapshot(
            saturation: intValue("saturation"),
            exposure: intValue("exposure"),
            sharpness: intValue("sharpness"),
            hue: intValue("hue"),
            vignette: intValue("vignette"),
            datestamp: boolValue("datestamp"),
            datestampFormat: stringValue("datestamp_format"),
            watermark: boolValue("watermark"),
            watermarkText: stringValue("watermark_text")
        )
    }

    private func scheduleRender() {
        guard let baseImage else { return }
        renderTask?.cancel()
        let snapshot = previewKey
        renderTask = Task(priority: .userInitiated) {
            // Small debounce so a slider drag does not enqueue dozens
            // of pending renders. 60 ms is below the human flicker
            // threshold yet long enough to coalesce a fast drag.
            try? await Task.sleep(nanoseconds: 60_000_000)
            if Task.isCancelled { return }
            let image = await renderImage(base: baseImage, snapshot: snapshot)
            if Task.isCancelled { return }
            await MainActor.run {
                renderedImage = image
            }
        }
    }

    // MARK: - Core Image pipeline

    private func renderImage(
        base: CIImage,
        snapshot: PreviewSnapshot
    ) async -> NSImage {
        var ciImage = base

        // Exposure: ±100 maps to ±1 EV. The bridge clamps to the same
        // window, so the slider edges agree at the boundary.
        if snapshot.exposure != 0 {
            ciImage = ciImage.applyingFilter(
                "CIExposureAdjust",
                parameters: [kCIInputEVKey: Double(snapshot.exposure) / 100.0]
            )
        }

        // Saturation: −100 → grayscale, 0 → identity, +100 → 2× saturation.
        // CIColorControls' saturation parameter is multiplicative; the
        // linear mapping below mirrors the bridge's HSV-S scaling at the
        // edges.
        if snapshot.saturation != 0 {
            ciImage = ciImage.applyingFilter(
                "CIColorControls",
                parameters: [
                    kCIInputSaturationKey: 1.0 + Double(snapshot.saturation) / 100.0
                ]
            )
        }

        // Hue: ±100 maps to ±90° of hue rotation, matching the bridge's
        // ``hue`` axis (roughly orange ↔ blue tint).
        if snapshot.hue != 0 {
            ciImage = ciImage.applyingFilter(
                "CIHueAdjust",
                parameters: [kCIInputAngleKey: Double(snapshot.hue) / 100.0 * .pi / 2.0]
            )
        }

        // Sharpness: positive uses unsharp mask; negative softens with
        // a small Gaussian. The bridge's pipeline ramps sharpness in a
        // similar but not identical curve — the preview is an
        // approximation.
        if snapshot.sharpness > 0 {
            ciImage = ciImage.applyingFilter(
                "CIUnsharpMask",
                parameters: [
                    kCIInputRadiusKey: 2.5,
                    kCIInputIntensityKey: Double(snapshot.sharpness) / 50.0
                ]
            )
        } else if snapshot.sharpness < 0 {
            ciImage = ciImage.applyingFilter(
                "CIGaussianBlur",
                parameters: [kCIInputRadiusKey: Double(-snapshot.sharpness) / 50.0]
            )
        }

        // Vignette: 0 → none, 100 → strong corner darkening.
        if snapshot.vignette > 0 {
            ciImage = ciImage.applyingFilter(
                "CIVignette",
                parameters: [
                    kCIInputRadiusKey: 2.0,
                    kCIInputIntensityKey: Double(snapshot.vignette) / 50.0
                ]
            )
        }

        // Crop back to the base extent in case any filter (vignette,
        // gaussian blur) extended the image bounds. Without this the
        // CGImage would include transparent margins.
        ciImage = ciImage.cropped(to: base.extent)

        guard let cgImage = Self.ciContext.createCGImage(ciImage, from: base.extent) else {
            return NSImage()
        }
        let baseNSImage = NSImage(
            cgImage: cgImage,
            size: NSSize(width: cgImage.width, height: cgImage.height)
        )
        return drawOverlays(on: baseNSImage, snapshot: snapshot)
    }

    /// Draw datestamp + watermark overlays on top of the Core Image
    /// output. We draw into a fresh ``NSImage`` so the underlying CG
    /// bitmap stays a clean copy of the pipeline output.
    private func drawOverlays(on baseImage: NSImage, snapshot: PreviewSnapshot) -> NSImage {
        guard snapshot.datestamp || (snapshot.watermark && !snapshot.watermarkText.isEmpty) else {
            return baseImage
        }
        let size = baseImage.size
        let result = NSImage(size: size)
        result.lockFocusFlipped(false)
        defer { result.unlockFocus() }
        baseImage.draw(in: NSRect(origin: .zero, size: size))

        let fontSize = max(size.height * 0.05, 14)
        let padding = size.height * 0.04
        let textColor = NSColor.white
        let shadow = NSShadow()
        shadow.shadowColor = NSColor.black.withAlphaComponent(0.6)
        shadow.shadowOffset = NSSize(width: 0, height: -1)
        shadow.shadowBlurRadius = 2
        let attrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.systemFont(ofSize: fontSize, weight: .semibold),
            .foregroundColor: textColor,
            .shadow: shadow,
        ]

        if snapshot.watermark && !snapshot.watermarkText.isEmpty {
            let text = NSAttributedString(string: snapshot.watermarkText, attributes: attrs)
            let measured = text.size()
            let point = NSPoint(x: padding, y: padding)
            _ = measured
            text.draw(at: point)
        }
        if snapshot.datestamp {
            let formatted = formatDate(formatKey: snapshot.datestampFormat)
            let text = NSAttributedString(string: formatted, attributes: attrs)
            let measured = text.size()
            let point = NSPoint(
                x: size.width - measured.width - padding,
                y: padding
            )
            text.draw(at: point)
        }
        return result
    }

    private func formatDate(formatKey: String) -> String {
        // The bridge owns the canonical datestamp formatting; the
        // Mac preview just approximates each preset so the row
        // toggles meaningfully. ``Quartz Date`` / ``Olympus`` /
        // ``Contax`` etc. all reduce to short calendar-date strings.
        let formatter = DateFormatter()
        switch formatKey {
        case "quartz_date":
            formatter.dateFormat = "yyyy MM dd"
        case "olympus":
            formatter.dateFormat = "'’'yy MM dd"
        case "contax":
            formatter.dateFormat = "MM dd 'YR'yy"
        case "lab_print":
            formatter.dateFormat = "yyyy.MM.dd"
        case "modern":
            formatter.dateFormat = "MMM d, yyyy"
        default:
            formatter.dateFormat = "MMM d, yyyy"
        }
        return formatter.string(from: Date())
    }
}

/// Snapshot of every adjustment that affects the preview. Conforming to
/// ``Equatable`` lets SwiftUI's ``onChange`` debounce noop edits and
/// only fire when the visible state actually changes.
private struct PreviewSnapshot: Equatable {
    var saturation: Int
    var exposure: Int
    var sharpness: Int
    var hue: Int
    var vignette: Int
    var datestamp: Bool
    var datestampFormat: String
    var watermark: Bool
    var watermarkText: String
}
