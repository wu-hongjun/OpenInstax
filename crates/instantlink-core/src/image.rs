//! Image loading, resizing, JPEG encoding, and chunking for Instax printers.

use std::io::Cursor;
use std::path::Path;

use image::imageops::FilterType;
use image::{DynamicImage, GenericImageView};

use crate::error::{PrinterError, Result};
use crate::models::PrinterModel;

/// How to fit the image to the printer's aspect ratio.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FitMode {
    /// Crop to fill (default) — may cut edges.
    Crop,
    /// Contain within bounds — may add white bars.
    Contain,
    /// Stretch to exact dimensions — may distort.
    Stretch,
}

impl FitMode {
    /// Parse from string, case-insensitive.
    pub fn from_str_lossy(s: &str) -> Self {
        match s.to_lowercase().as_str() {
            "contain" => FitMode::Contain,
            "stretch" => FitMode::Stretch,
            _ => FitMode::Crop,
        }
    }
}

/// Load an image from a file path.
pub fn load_image(path: &Path) -> Result<DynamicImage> {
    image::open(path).map_err(|e| PrinterError::Image(format!("failed to load image: {e}")))
}

/// Resize and fit the image to the printer's dimensions.
pub fn resize_image(img: &DynamicImage, model: PrinterModel, fit: FitMode) -> DynamicImage {
    let spec = model.spec();
    let target_w = spec.width;
    let target_h = spec.height;

    match fit {
        FitMode::Crop => img.resize_to_fill(target_w, target_h, FilterType::Lanczos3),
        FitMode::Contain => {
            let resized = img.resize(target_w, target_h, FilterType::Lanczos3);
            let (rw, rh) = resized.dimensions();
            let mut canvas = DynamicImage::new_rgb8(target_w, target_h);
            // Fill with white
            if let Some(rgb) = canvas.as_mut_rgb8() {
                for pixel in rgb.pixels_mut() {
                    *pixel = image::Rgb([255, 255, 255]);
                }
            }
            image::imageops::overlay(
                &mut canvas,
                &resized,
                ((target_w - rw) / 2) as i64,
                ((target_h - rh) / 2) as i64,
            );
            canvas
        }
        FitMode::Stretch => img.resize_exact(target_w, target_h, FilterType::Lanczos3),
    }
}

/// Encode an image as JPEG, finding the highest quality that fits within `max_size`.
///
/// Uses binary search to maximize quality while staying under the limit.
pub fn encode_jpeg(img: &DynamicImage, initial_quality: u8, max_size: usize) -> Result<Vec<u8>> {
    let rgb = img.to_rgb8();

    let encode_at = |q: u8| -> std::result::Result<Vec<u8>, PrinterError> {
        let mut buf = Cursor::new(Vec::new());
        let encoder = image::codecs::jpeg::JpegEncoder::new_with_quality(&mut buf, q);
        rgb.write_with_encoder(encoder)
            .map_err(|e| PrinterError::Image(format!("JPEG encode failed: {e}")))?;
        Ok(buf.into_inner())
    };

    // Try the requested quality first — often fits for smaller images.
    let capped = initial_quality.min(100);
    let data = encode_at(capped)?;
    if data.len() <= max_size {
        log::debug!("JPEG encoded: {} bytes at quality {}", data.len(), capped);
        return Ok(data);
    }

    // Binary search for the highest quality that fits.
    let mut low: u8 = 1;
    let mut high: u8 = capped.saturating_sub(1);
    let mut best_data: Option<Vec<u8>> = None;
    let mut best_quality: u8 = 0;
    let mut min_quality_size: Option<usize> = None;

    while low <= high {
        let mid = low + (high - low) / 2;
        let attempt = encode_at(mid)?;
        if min_quality_size.is_none() || mid <= low {
            min_quality_size = Some(attempt.len());
        }
        if attempt.len() <= max_size {
            best_quality = mid;
            best_data = Some(attempt);
            low = mid + 1;
        } else {
            if mid == 0 {
                break;
            }
            high = mid - 1;
        }
    }

    match best_data {
        Some(data) => {
            log::debug!(
                "JPEG encoded: {} bytes at quality {} (reduced from {})",
                data.len(),
                best_quality,
                capped
            );
            Ok(data)
        }
        None => {
            let size = min_quality_size.unwrap_or_else(|| encode_at(1).map_or(0, |d| d.len()));
            Err(PrinterError::ImageTooLarge {
                size,
                max: max_size,
            })
        }
    }
}

/// Split JPEG data into chunks appropriate for the printer model.
///
/// The last chunk is zero-padded to the full chunk size, matching the
/// reference implementation's behavior.
pub fn chunk_image_data(data: &[u8], model: PrinterModel) -> Vec<Vec<u8>> {
    let chunk_size = model.spec().chunk_size;
    let mut chunks: Vec<Vec<u8>> = data.chunks(chunk_size).map(|c| c.to_vec()).collect();
    // Pad last chunk to full chunk_size with zeros
    if let Some(last) = chunks.last_mut()
        && last.len() < chunk_size
    {
        last.resize(chunk_size, 0);
    }
    chunks
}

/// Shared image preparation pipeline: resize → flip (if needed) → encode → chunk.
fn prepare_image_inner(
    img: DynamicImage,
    model: PrinterModel,
    fit: FitMode,
    quality: u8,
) -> Result<(Vec<u8>, Vec<Vec<u8>>)> {
    let spec = model.spec();
    let mut resized = resize_image(&img, model, fit);
    if spec.flip_vertical {
        resized = resized.flipv();
    }
    let jpeg_data = encode_jpeg(&resized, quality, spec.max_image_size)?;
    let chunks = chunk_image_data(&jpeg_data, model);
    Ok((jpeg_data, chunks))
}

/// Complete image preparation pipeline: load → resize → flip (if needed) → encode → chunk.
pub fn prepare_image(
    path: &Path,
    model: PrinterModel,
    fit: FitMode,
    quality: u8,
) -> Result<(Vec<u8>, Vec<Vec<u8>>)> {
    prepare_image_inner(load_image(path)?, model, fit, quality)
}

/// Load an image from raw bytes (for use from FFI or other non-file sources).
pub fn load_image_from_bytes(data: &[u8]) -> Result<DynamicImage> {
    image::load_from_memory(data)
        .map_err(|e| PrinterError::Image(format!("failed to load image from bytes: {e}")))
}

/// Prepare an image from raw bytes (skip file loading).
pub fn prepare_image_from_bytes(
    data: &[u8],
    model: PrinterModel,
    fit: FitMode,
    quality: u8,
) -> Result<(Vec<u8>, Vec<Vec<u8>>)> {
    prepare_image_inner(load_image_from_bytes(data)?, model, fit, quality)
}

#[cfg(test)]
mod tests {
    use super::*;
    use image::ImageFormat;
    use image::RgbImage;

    fn create_test_image(w: u32, h: u32) -> DynamicImage {
        DynamicImage::new_rgb8(w, h)
    }

    #[test]
    fn fit_mode_from_str() {
        assert_eq!(FitMode::from_str_lossy("crop"), FitMode::Crop);
        assert_eq!(FitMode::from_str_lossy("Contain"), FitMode::Contain);
        assert_eq!(FitMode::from_str_lossy("STRETCH"), FitMode::Stretch);
        assert_eq!(FitMode::from_str_lossy("unknown"), FitMode::Crop);
    }

    #[test]
    fn resize_crop_dimensions() {
        let img = create_test_image(1000, 1000);
        let resized = resize_image(&img, PrinterModel::Mini, FitMode::Crop);
        let (w, h) = resized.dimensions();
        assert_eq!(w, 600);
        assert_eq!(h, 800);
    }

    #[test]
    fn resize_contain_dimensions() {
        let img = create_test_image(1000, 1000);
        let resized = resize_image(&img, PrinterModel::Mini, FitMode::Contain);
        let (w, h) = resized.dimensions();
        assert_eq!(w, 600);
        assert_eq!(h, 800);
    }

    #[test]
    fn resize_stretch_dimensions() {
        let img = create_test_image(1000, 500);
        let resized = resize_image(&img, PrinterModel::Square, FitMode::Stretch);
        let (w, h) = resized.dimensions();
        assert_eq!(w, 800);
        assert_eq!(h, 800);
    }

    #[test]
    fn resize_wide_model() {
        let img = create_test_image(2000, 1000);
        let resized = resize_image(&img, PrinterModel::Wide, FitMode::Crop);
        let (w, h) = resized.dimensions();
        assert_eq!(w, 1260);
        assert_eq!(h, 840);
    }

    #[test]
    fn encode_jpeg_produces_data() {
        let img = create_test_image(600, 800);
        let max_size = PrinterModel::Mini.spec().max_image_size;
        let data = encode_jpeg(&img, 97, max_size).unwrap();
        assert!(!data.is_empty());
        // JPEG magic bytes
        assert_eq!(data[0], 0xFF);
        assert_eq!(data[1], 0xD8);
    }

    #[test]
    fn encode_jpeg_fits_within_max() {
        let img = create_test_image(600, 800);
        let max_size = PrinterModel::Mini.spec().max_image_size;
        let data = encode_jpeg(&img, 97, max_size).unwrap();
        assert!(data.len() <= max_size);
    }

    #[test]
    fn encode_jpeg_link3_size_limit() {
        let img = create_test_image(600, 800);
        let max_size = PrinterModel::MiniLink3.spec().max_image_size;
        let data = encode_jpeg(&img, 97, max_size).unwrap();
        assert!(data.len() <= max_size);
    }

    #[test]
    fn chunk_image_data_mini() {
        let data = vec![0u8; 5000];
        let chunks = chunk_image_data(&data, PrinterModel::Mini);
        // 5000 / 900 = 5.55 → 6 chunks
        assert_eq!(chunks.len(), 6);
        assert_eq!(chunks[0].len(), 900);
        // Last chunk is padded to full chunk size
        assert_eq!(chunks[5].len(), 900);
    }

    #[test]
    fn chunk_image_data_square() {
        let data = vec![0u8; 5000];
        let chunks = chunk_image_data(&data, PrinterModel::Square);
        // 5000 / 1808 = 2.76 → 3 chunks
        assert_eq!(chunks.len(), 3);
        assert_eq!(chunks[0].len(), 1808);
        // Last chunk is padded to full chunk size
        assert_eq!(chunks[2].len(), 1808);
    }

    #[test]
    fn chunk_image_data_empty() {
        let data: Vec<u8> = vec![];
        let chunks = chunk_image_data(&data, PrinterModel::Mini);
        assert!(chunks.is_empty());
    }

    #[test]
    fn load_from_bytes_png() {
        // Create a minimal valid PNG in memory
        let img = create_test_image(10, 10);
        let mut buf = Cursor::new(Vec::new());
        img.write_to(&mut buf, ImageFormat::Png).unwrap();
        let loaded = load_image_from_bytes(&buf.into_inner()).unwrap();
        let (w, h) = loaded.dimensions();
        assert_eq!(w, 10);
        assert_eq!(h, 10);
    }

    #[test]
    fn load_from_bytes_invalid() {
        let result = load_image_from_bytes(&[0, 1, 2, 3]);
        assert!(result.is_err());
    }

    #[test]
    fn model_specs_correct() {
        // Verify all 4 model specs
        let mini = PrinterModel::Mini.spec();
        assert_eq!(mini.max_image_size, 105_000);
        assert_eq!(mini.packet_delay_ms, 0);
        assert_eq!(mini.success_code, 0);
        assert!(!mini.flip_vertical);

        let link3 = PrinterModel::MiniLink3.spec();
        assert_eq!(link3.max_image_size, 55_000);
        assert_eq!(link3.packet_delay_ms, 75);
        assert_eq!(link3.success_code, 16);
        assert!(link3.flip_vertical);

        let square = PrinterModel::Square.spec();
        assert_eq!(square.max_image_size, 105_000);
        assert_eq!(square.success_code, 12);

        let wide = PrinterModel::Wide.spec();
        assert_eq!(wide.max_image_size, 225_000);
        assert_eq!(wide.success_code, 15);
    }

    #[test]
    fn all_models_includes_link3() {
        let all = PrinterModel::all();
        assert_eq!(all.len(), 4);
        assert!(all.contains(&PrinterModel::MiniLink3));
    }

    #[test]
    fn encode_jpeg_returns_image_too_large_when_quality_one_still_exceeds_limit() {
        let img = create_test_image(600, 800);
        let err = encode_jpeg(&img, 1, 1).unwrap_err();
        match err {
            PrinterError::ImageTooLarge { size, max } => {
                assert!(size > max);
                assert_eq!(max, 1);
            }
            other => panic!("expected ImageTooLarge, got {other:?}"),
        }
    }

    #[test]
    fn prepare_image_from_bytes_flips_vertically_for_link3() {
        let width = PrinterModel::MiniLink3.spec().width;
        let height = PrinterModel::MiniLink3.spec().height;
        let mut rgb = RgbImage::new(width, height);
        for y in 0..height {
            for x in 0..width {
                let pixel = if y < height / 2 {
                    image::Rgb([255, 0, 0])
                } else {
                    image::Rgb([0, 0, 255])
                };
                rgb.put_pixel(x, y, pixel);
            }
        }

        let mut encoded = Cursor::new(Vec::new());
        DynamicImage::ImageRgb8(rgb)
            .write_to(&mut encoded, ImageFormat::Png)
            .unwrap();

        let (jpeg, _) = prepare_image_from_bytes(
            &encoded.into_inner(),
            PrinterModel::MiniLink3,
            FitMode::Crop,
            97,
        )
        .unwrap();
        let processed = load_image_from_bytes(&jpeg).unwrap().to_rgb8();
        let top = processed.get_pixel(width / 2, height / 4);
        let bottom = processed.get_pixel(width / 2, (height * 3) / 4);

        assert!(
            top[2] > top[0],
            "expected blue-dominant top pixel, got {top:?}"
        );
        assert!(
            bottom[0] > bottom[2],
            "expected red-dominant bottom pixel, got {bottom:?}"
        );
    }
}
