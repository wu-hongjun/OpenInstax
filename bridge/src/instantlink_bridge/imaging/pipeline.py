"""Model-aware image preparation pipeline for Instax Link printers."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Protocol, cast

from PIL import Image, ImageOps, UnidentifiedImageError

from instantlink_bridge.ble.models import PrinterModel, spec_for
from instantlink_bridge.imaging.postprocess import AdjustmentProfile, apply_adjustments


class ImagePipelineError(RuntimeError):
    """Base image pipeline error."""


class UnsupportedImageError(ImagePipelineError):
    """Raised when the source is not a supported camera still image."""


class ImageTooLargeError(ImagePipelineError):
    """Raised when an image exceeds an encoded or decoded safety limit."""

    def __init__(self, size: int, maximum: int, unit: str = "bytes") -> None:
        super().__init__(f"image too large: {size} {unit} (max {maximum} {unit})")
        self.size = size
        self.maximum = maximum
        self.unit = unit

    def __reduce__(self) -> tuple[type[ImageTooLargeError], tuple[int, int, str]]:
        return (self.__class__, (self.size, self.maximum, self.unit))


IMAGE_DECODE_ERRORS = (
    UnidentifiedImageError,
    Image.DecompressionBombError,
    OSError,
    ValueError,
)


class FitMode(StrEnum):
    """How to fit source pixels to the printer aspect ratio."""

    AUTO = "auto"
    CROP = "crop"
    CONTAIN = "contain"
    STRETCH = "stretch"


@dataclass(frozen=True, slots=True)
class PrintEdit:
    """Per-photo preview edits chosen on the LCD before printing."""

    rotate_degrees: int = 0
    zoom: float = 1.0
    offset_x: float = 0.0
    offset_y: float = 0.0


@dataclass(frozen=True, slots=True)
class PreparedImage:
    """Prepared JPEG for a specific Instax printer model."""

    data: bytes
    model: PrinterModel
    width: int
    height: int
    quality: int
    fit: FitMode


class _RawThumbnail(Protocol):
    format: object
    data: object


class _RawImage(Protocol):
    def extract_thumb(self) -> _RawThumbnail:
        """Return the camera-embedded RAW preview."""

    def postprocess(self, **kwargs: object) -> object:
        """Return an RGB array from RAW sensor data."""


class _RawThumbFormat(Protocol):
    JPEG: object
    BITMAP: object


class _RawPyModule(Protocol):
    ThumbFormat: _RawThumbFormat


def parse_fit_mode(value: str) -> FitMode:
    """Parse a user/config fit mode value."""

    normalized = value.strip().lower()
    try:
        return FitMode(normalized)
    except ValueError as error:
        supported = ", ".join(mode.value for mode in FitMode)
        raise ValueError(f"unsupported fit mode {value!r}; expected one of {supported}") from error


def prepare_for_instax(
    source_path: Path,
    model: PrinterModel,
    *,
    fit: FitMode = FitMode.AUTO,
    quality: int = 100,
    edit: PrintEdit | None = None,
    adjustments: AdjustmentProfile | None = None,
) -> PreparedImage:
    """Convert a camera still image into a model-specific Instax JPEG."""

    return _prepare_for_model(
        source_path,
        model,
        fit=fit,
        quality=quality,
        edit=edit,
        adjustments=adjustments,
        apply_model_flip=True,
    )


def prepare_for_instantlink_backend(
    source_path: Path,
    model: PrinterModel,
    *,
    fit: FitMode = FitMode.AUTO,
    quality: int = 100,
    edit: PrintEdit | None = None,
    adjustments: AdjustmentProfile | None = None,
) -> PreparedImage:
    """Prepare an edited, model-sized JPEG for InstantLink to send.

    InstantLink applies model-specific transport transforms itself. In particular,
    Mini Link 3 output is vertically flipped inside InstantLink before upload, so
    this helper deliberately leaves that flip unapplied and the caller must pass
    the result to InstantLink with fit mode ``stretch``.
    """

    return _prepare_for_model(
        source_path,
        model,
        fit=fit,
        quality=quality,
        edit=edit,
        adjustments=adjustments,
        apply_model_flip=False,
    )


def _prepare_for_model(
    source_path: Path,
    model: PrinterModel,
    *,
    fit: FitMode,
    quality: int,
    edit: PrintEdit | None,
    adjustments: AdjustmentProfile | None,
    apply_model_flip: bool,
) -> PreparedImage:
    """Prepare a source image for the given printer model.

    Pipeline stage order (must not be reordered):

    1. decode via ``_open_source_image`` (JPEG / HEIF / RAW)
    2. ``Image.draft`` hint for JPEG sources
    3. ``ImageOps.exif_transpose`` — correct camera orientation
    4. ``convert("RGB")`` — normalise colour space
    5. ``apply_adjustments`` — colour/overlay adjustments at full source
       resolution (identity profile in phase 2; wired to user settings
       in phase 3)
    6. ``_apply_print_edit`` — per-photo interactive rotate / zoom / offset
    7. ``_fit_image`` — model-aware crop / contain / stretch to print size
    8. ``_encode_jpeg_with_size_limit`` — final JPEG at model chunk budget
    """
    spec = spec_for(model)
    working_size = _working_size_for_model(spec.width, spec.height)
    minimum_source_edge = max(spec.width, spec.height)
    image: Image.Image | None = None
    try:
        image = _open_source_image(source_path, working_size, minimum_source_edge)
        if image.format == "JPEG":
            image.draft("RGB", working_size)
        if not _is_supported_source_format(source_path, image):
            raise UnsupportedImageError(
                "unsupported image format "
                f"{image.format or source_path.suffix.lower() or 'unknown'}"
            )
        transposed = ImageOps.exif_transpose(image)
        if transposed is None:
            transposed = image.copy()
        prepared = transposed.convert("RGB")
        profile = adjustments if adjustments is not None else AdjustmentProfile()
        prepared = apply_adjustments(prepared, profile)
        prepared = _apply_print_edit(prepared, edit)
        fitted = _fit_image(prepared, spec.width, spec.height, fit)
    except IMAGE_DECODE_ERRORS as error:
        raise UnsupportedImageError("unsupported or corrupt image file") from error
    finally:
        if image is not None:
            image.close()

    if apply_model_flip and spec.flip_vertical:
        fitted = ImageOps.flip(fitted)

    data, final_quality = _encode_jpeg_with_size_limit(fitted, quality, spec.max_image_size)
    return PreparedImage(
        data=data,
        model=model,
        width=spec.width,
        height=spec.height,
        quality=final_quality,
        fit=fit,
    )


def create_preview_image(
    source_path: Path,
    model: PrinterModel,
    *,
    fit: FitMode = FitMode.AUTO,
    edit: PrintEdit | None = None,
    max_size: tuple[int, int] = (172, 112),
) -> Image.Image:
    """Create a small RGB preview of the final print framing for the LCD."""

    spec = spec_for(model)
    working_size = _working_size_for_model(spec.width, spec.height)
    minimum_source_edge = min(spec.width, spec.height)
    image: Image.Image | None = None
    try:
        image = _open_source_image(source_path, working_size, minimum_source_edge)
        if image.format == "JPEG":
            image.draft("RGB", working_size)
        transposed = ImageOps.exif_transpose(image)
        if transposed is None:
            transposed = image.copy()
        prepared = transposed.convert("RGB")
        prepared = _apply_print_edit(prepared, edit)
        fitted = _fit_image(prepared, spec.width, spec.height, fit)
        if spec.flip_vertical:
            fitted = ImageOps.flip(fitted)
        return _film_preview_for_model(fitted, model, max_size)
    except IMAGE_DECODE_ERRORS as error:
        raise UnsupportedImageError("unsupported or corrupt image file") from error
    finally:
        if image is not None:
            image.close()


def create_preview_from_prepared(
    prepared: PreparedImage,
    *,
    max_size: tuple[int, int] = (172, 112),
) -> Image.Image:
    """Create an LCD film preview from an already prepared printer JPEG."""

    with Image.open(BytesIO(prepared.data)) as image:
        return _film_preview_for_model(image.convert("RGB"), prepared.model, max_size)


def chunk_image_data(data: bytes, model: PrinterModel) -> list[bytes]:
    """Split JPEG data into model-specific chunks, padding the final chunk."""

    chunk_size = spec_for(model).chunk_size
    chunks = [data[index : index + chunk_size] for index in range(0, len(data), chunk_size)]
    if chunks and len(chunks[-1]) < chunk_size:
        chunks[-1] = chunks[-1] + bytes(chunk_size - len(chunks[-1]))
    return chunks


def _fit_image(image: Image.Image, width: int, height: int, fit: FitMode) -> Image.Image:
    if fit in {FitMode.AUTO, FitMode.CROP}:
        if fit == FitMode.AUTO:
            image = _auto_orient_for_target(image, width, height)
        return ImageOps.fit(
            image,
            (width, height),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )
    if fit == FitMode.STRETCH:
        return image.resize((width, height), Image.Resampling.LANCZOS)

    contained = ImageOps.contain(image, (width, height), method=Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (width, height), (255, 255, 255))
    left = (width - contained.width) // 2
    top = (height - contained.height) // 2
    canvas.paste(contained, (left, top))
    return canvas


def _apply_print_edit(image: Image.Image, edit: PrintEdit | None) -> Image.Image:
    if edit is None:
        return image
    rotated = _rotate_image(image, edit.rotate_degrees)
    return _zoom_and_offset(rotated, edit.zoom, edit.offset_x, edit.offset_y)


def _rotate_image(image: Image.Image, rotate_degrees: int) -> Image.Image:
    normalized = rotate_degrees % 360
    if normalized == 0:
        return image
    return image.rotate(-normalized, expand=True)


def _zoom_and_offset(
    image: Image.Image, zoom: float, offset_x: float, offset_y: float
) -> Image.Image:
    bounded_zoom = max(1.0, min(3.0, zoom))
    if bounded_zoom <= 1.0001:
        return image

    crop_width = max(1, int(image.width / bounded_zoom))
    crop_height = max(1, int(image.height / bounded_zoom))
    max_left = image.width - crop_width
    max_top = image.height - crop_height
    bounded_x = max(-1.0, min(1.0, offset_x))
    bounded_y = max(-1.0, min(1.0, offset_y))
    left = int((max_left / 2) * (bounded_x + 1.0))
    top = int((max_top / 2) * (bounded_y + 1.0))
    return image.crop((left, top, left + crop_width, top + crop_height))


def _film_preview_for_model(
    image: Image.Image,
    model: PrinterModel,
    max_size: tuple[int, int],
) -> Image.Image:
    film_width, film_height, image_width, image_height = _film_preview_dimensions(model)
    scale = min(max_size[0] / film_width, max_size[1] / film_height)
    preview_width = max(1, int(film_width * scale))
    preview_height = max(1, int(film_height * scale))
    print_width = max(1, int(image_width * scale))
    print_height = max(1, int(image_height * scale))
    side = max(0, (preview_width - print_width) // 2)
    top = max(0, int(5 * scale))

    film = Image.new("RGB", (preview_width, preview_height), (252, 252, 247))
    print_area = image.resize((print_width, print_height), Image.Resampling.LANCZOS)
    film.paste(print_area, (side, top))
    return film


def _film_preview_dimensions(model: PrinterModel) -> tuple[int, int, int, int]:
    if model in {PrinterModel.MINI, PrinterModel.MINI_LINK3}:
        return (54, 86, 46, 62)
    if model is PrinterModel.SQUARE:
        return (72, 86, 62, 62)
    return (108, 86, 99, 62)


def _auto_orient_for_target(image: Image.Image, width: int, height: int) -> Image.Image:
    """Rotate portrait/landscape mismatch for non-square Instax frames."""

    if width == height:
        return image
    image_landscape = image.width > image.height
    target_landscape = width > height
    if image_landscape == target_landscape:
        return image
    return image.transpose(Image.Transpose.ROTATE_90)


def _working_size_for_model(width: int, height: int) -> tuple[int, int]:
    if (width, height) == (600, 800):
        return (MINI_WORKING_EDGE, MINI_WORKING_EDGE)
    return (max(width, DEFAULT_WORKING_EDGE), max(height, DEFAULT_WORKING_EDGE))


def _encode_jpeg_with_size_limit(
    image: Image.Image,
    initial_quality: int,
    max_size: int,
) -> tuple[bytes, int]:
    quality = max(1, min(100, initial_quality))
    first = _encode_jpeg(image, quality)
    if len(first) <= max_size:
        return first, quality

    low = 1
    high = quality - 1
    best: tuple[bytes, int] | None = None
    smallest = first
    while low <= high:
        mid = low + (high - low) // 2
        attempt = _encode_jpeg(image, mid)
        if len(attempt) < len(smallest):
            smallest = attempt
        if len(attempt) <= max_size:
            best = (attempt, mid)
            low = mid + 1
        else:
            high = mid - 1

    if best is not None:
        return best
    raise ImageTooLargeError(len(smallest), max_size)


def _encode_jpeg(image: Image.Image, quality: int) -> bytes:
    from io import BytesIO

    output = BytesIO()
    image.save(output, format="JPEG", quality=quality, subsampling=2, optimize=True)
    return output.getvalue()


HEIF_SUFFIXES = {".hif", ".heif", ".heic"}
RAW_SUFFIXES = {".arw", ".raw", ".dng"}
MINI_WORKING_EDGE = 1200
DEFAULT_WORKING_EDGE = 1600
MAX_FALLBACK_DECODE_PIXELS = 24_000_000
MAX_FALLBACK_DECODE_EDGE = 8_000

_HEIF_OPENER_REGISTERED = False


def _open_source_image(
    source_path: Path,
    working_size: tuple[int, int],
    minimum_source_edge: int,
) -> Image.Image:
    suffix = source_path.suffix.lower()
    if suffix in HEIF_SUFFIXES:
        return _open_heif_image(source_path, _heif_thumbnail_edge(working_size))
    if suffix in RAW_SUFFIXES:
        return _open_raw_image(source_path, working_size, minimum_source_edge)
    return Image.open(source_path)


def _is_supported_source_format(source_path: Path, image: Image.Image) -> bool:
    suffix = source_path.suffix.lower()
    if suffix in RAW_SUFFIXES:
        return True
    if suffix in HEIF_SUFFIXES:
        return True
    return image.format == "JPEG"


def _register_heif_opener() -> None:
    global _HEIF_OPENER_REGISTERED

    if _HEIF_OPENER_REGISTERED:
        return
    try:
        from pillow_heif import register_heif_opener
    except ImportError as error:
        raise UnsupportedImageError("HEIF/HIF support is not installed") from error
    register_heif_opener()
    _HEIF_OPENER_REGISTERED = True


def _heif_thumbnail_edge(working_size: tuple[int, int]) -> int:
    if working_size == (MINI_WORKING_EDGE, MINI_WORKING_EDGE):
        return MINI_WORKING_EDGE
    return max(DEFAULT_WORKING_EDGE, *working_size)


def _open_heif_image(source_path: Path, thumbnail_edge: int) -> Image.Image:
    thumbnailer = shutil.which("heif-thumbnailer")
    if thumbnailer is not None:
        return _open_heif_thumbnail(source_path, thumbnailer, thumbnail_edge)
    _register_heif_opener()
    image = Image.open(source_path)
    _ensure_fallback_decode_size(image.size)
    return image


def _open_heif_thumbnail(source_path: Path, thumbnailer: str, size: int) -> Image.Image:
    with TemporaryDirectory(prefix="instantlink-bridge-heif-") as temp_dir:
        output_path = Path(temp_dir) / "thumbnail.png"
        try:
            subprocess.run(
                [thumbnailer, "-s", str(size), str(source_path), str(output_path)],
                check=True,
                capture_output=True,
                timeout=30,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            raise UnsupportedImageError("could not decode HIF image") from error
        with Image.open(output_path) as thumbnail:
            # Pillow 10 inline stubs return `Image | Any` from `.convert`; the
            # cast was redundant on the Pillow stub revision active when
            # b0eefa2 landed, but the current stubs need it again to avoid
            # an implicit-`Any` return under `mypy --strict`.
            return cast(Image.Image, thumbnail.convert("RGB"))


def _open_raw_image(
    source_path: Path,
    working_size: tuple[int, int],
    minimum_source_edge: int,
) -> Image.Image:
    try:
        import rawpy
    except ImportError as error:
        raise UnsupportedImageError("RAW support is not installed") from error

    rgb: object
    try:
        with rawpy.imread(str(source_path)) as raw:
            preview = _open_raw_preview(raw, rawpy, working_size, minimum_source_edge)
            if preview is not None:
                return preview
            _ensure_raw_fallback_decode_size(raw)
            rgb = raw.postprocess(
                use_camera_wb=True,
                no_auto_bright=True,
                output_bps=8,
                half_size=True,
            )
    except ImagePipelineError:
        raise
    except Exception as error:
        raise UnsupportedImageError("could not decode RAW image") from error
    image = Image.fromarray(cast(Any, rgb)).convert("RGB")
    _ensure_fallback_decode_size(image.size)
    return _downsample_loaded_image(image, working_size)


def _open_raw_preview(
    raw: object,
    rawpy_module: object,
    working_size: tuple[int, int],
    minimum_source_edge: int,
) -> Image.Image | None:
    raw_image = cast(_RawImage, raw)
    thumb_format = cast(_RawPyModule, rawpy_module).ThumbFormat
    try:
        thumbnail = raw_image.extract_thumb()
    except Exception:
        return None

    image: Image.Image | None = None
    try:
        if thumbnail.format == thumb_format.JPEG:
            preview_data = cast(bytes, thumbnail.data)
            with Image.open(BytesIO(preview_data)) as preview:
                preview.draft("RGB", working_size)
                image = preview.copy()
        elif thumbnail.format == thumb_format.BITMAP:
            image = Image.fromarray(cast(Any, thumbnail.data)).convert("RGB")
        else:
            return None
        if image is None:
            return None
        if max(image.size) < minimum_source_edge:
            image.close()
            return None
        return _downsample_loaded_image(image, working_size)
    except Exception:
        if image is not None:
            image.close()
        return None


def _downsample_loaded_image(image: Image.Image, working_size: tuple[int, int]) -> Image.Image:
    if image.mode != "RGB":
        image = image.convert("RGB")
    if image.size[0] > working_size[0] or image.size[1] > working_size[1]:
        image.thumbnail(working_size, Image.Resampling.LANCZOS)
    return image


def _ensure_fallback_decode_size(size: tuple[int, int]) -> None:
    width, height = size
    pixels = width * height
    if width > MAX_FALLBACK_DECODE_EDGE or height > MAX_FALLBACK_DECODE_EDGE:
        raise ImageTooLargeError(max(width, height), MAX_FALLBACK_DECODE_EDGE, "pixels per edge")
    if pixels > MAX_FALLBACK_DECODE_PIXELS:
        raise ImageTooLargeError(pixels, MAX_FALLBACK_DECODE_PIXELS, "pixels")


def _ensure_raw_fallback_decode_size(raw: object) -> None:
    size = _raw_source_size(raw)
    if size is None:
        raise UnsupportedImageError("RAW fallback requires bounded preview or source dimensions")
    _ensure_fallback_decode_size(size)


def _raw_source_size(raw: object) -> tuple[int, int] | None:
    sizes = getattr(raw, "sizes", None)
    for width_name, height_name in (
        ("width", "height"),
        ("iwidth", "iheight"),
        ("raw_width", "raw_height"),
    ):
        width = _positive_int_attr(sizes, width_name)
        height = _positive_int_attr(sizes, height_name)
        if width is not None and height is not None:
            return (width, height)
    return None


def _positive_int_attr(instance: object, name: str) -> int | None:
    value = getattr(instance, name, None)
    if isinstance(value, int) and value > 0:
        return value
    return None
