"""Built-in and user-defined ``AdjustmentProfile`` presets.

A preset is a named profile; the active preset name lives in
``AdjustmentsConfig.preset``.  Built-ins are defined here; user customs
persist in ``/etc/InstantLinkBridge/presets.toml``.

Phase 5 (plan 036) semantics
-----------------------------
Presets are now *starting templates*, not gates.  The ``"Custom"`` sentinel
has been removed.  Selecting a preset stamps its values into the live config;
the ``preset`` field on ``AdjustmentsConfig`` is a display label — it records
which template was last loaded, not an immutable lock.  Every slider is always
editable regardless of the active preset name.

``resolve_preset`` always reads the per-axis integer values from ``config``
(saturation / exposure / sharpness / hue / vignette) and builds an
``AdjustmentProfile`` from them, with overlay settings (datestamp, watermark)
applied on top.  The ``preset`` field is purely informational.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from instantlink_bridge.imaging.postprocess import AdjustmentProfile

if TYPE_CHECKING:
    from instantlink_bridge.config import AdjustmentsConfig  # pragma: no cover

__all__ = [
    "BUILTIN_PRESET_VALUES",
    "PRESETS",
    "PRESET_ORDER",
    "USER_PRESETS_PATH",
    "VALID_PRESET_NAMES",
    "is_preset_modified",
    "load_user_presets",
    "resolve_preset",
    "save_user_presets",
    "stamp_preset_into_config",
]

LOGGER = logging.getLogger(__name__)

USER_PRESETS_PATH = Path("/etc/InstantLinkBridge/presets.toml")

# ---------------------------------------------------------------------------
# Built-in presets
# ---------------------------------------------------------------------------
# Values here are the *internal* AdjustmentProfile float factors, NOT the
# -100…+100 UI integers.  A preset can encode any float; the pipeline accepts
# any float but the UI editor constrains per-axis edits to [-100, +100] ints.
#
# NOTE: "Instax Film" values:
#   saturation=0.9  (-10 on ±100 UI scale → factor 1.0 + (-10)/100 = 0.9)
#   sharpness=0.9   (-10 on ±100 UI scale → factor 1.0 + (-10)/100 = 0.9)
#   vignette=50     (50 on 0…100 one-sided scale)

PRESETS: dict[str, AdjustmentProfile] = {
    "Default": AdjustmentProfile(
        saturation=1.0,
        exposure=1.0,
        sharpness=1.0,
        hue=0,
    ),
    "Vivid": AdjustmentProfile(
        saturation=1.5,   # +50 on the -100…+100 UI scale → factor 1.0 + 50/100 = 1.5
        exposure=1.0,
        sharpness=1.25,   # +25 on the UI scale → factor 1.25
        hue=0,
    ),
    "Soft": AdjustmentProfile(
        saturation=0.75,  # -25 on UI scale → factor 0.75
        exposure=1.0,
        sharpness=0.75,   # -25 on UI scale → factor 0.75
        hue=0,
    ),
    "Black & white": AdjustmentProfile(
        saturation=0.0,   # -100 → greyscale
        exposure=1.0,
        sharpness=1.0,
        hue=0,
    ),
    "Instax Film": AdjustmentProfile(
        saturation=0.9,   # -10 on ±100 UI scale: slight desat for vintage feel
        exposure=1.0,
        sharpness=0.9,    # -10 on ±100 UI scale: Instax prints aren't tack-sharp
        hue=0,
        vignette=50,      # visible but not heavy corner darkening
    ),
}

# Stable picker order: built-ins first, then user custom slots appended
# dynamically by callers after loading user presets.
PRESET_ORDER: tuple[str, ...] = (
    "Default",
    "Vivid",
    "Soft",
    "Black & white",
    "Instax Film",
    # User custom slots are not listed here — callers insert them after
    # "Instax Film" after calling load_user_presets().
)

# The full set of valid preset names across built-ins and user custom slots.
# Used for config validation.  "Custom" is retained in the legacy set only
# for migration (see _load_adjustments_config in config.py).
VALID_PRESET_NAMES: frozenset[str] = frozenset(
    {
        "Default",
        "Vivid",
        "Soft",
        "Black & white",
        "Instax Film",
        "Custom1",
        "Custom2",
        "Custom3",
        "Custom4",
        "Custom5",
        "Custom6",
    }
)

# Per-axis UI integer values (saturation, exposure, sharpness, hue, vignette)
# corresponding to each built-in preset.  Used by stamp_preset_into_config to
# write back into AdjustmentsConfig when the user selects a preset.
BUILTIN_PRESET_VALUES: dict[str, dict[str, int]] = {
    "Default": {"saturation": 0, "exposure": 0, "sharpness": 0, "hue": 0, "vignette": 0},
    "Vivid": {"saturation": 50, "exposure": 0, "sharpness": 25, "hue": 0, "vignette": 0},
    "Soft": {"saturation": -25, "exposure": 0, "sharpness": -25, "hue": 0, "vignette": 0},
    "Black & white": {"saturation": -100, "exposure": 0, "sharpness": 0, "hue": 0, "vignette": 0},
    "Instax Film": {"saturation": -10, "exposure": 0, "sharpness": -10, "hue": 0, "vignette": 50},
}

_MAX_USER_PRESETS = 6
_USER_PRESET_SLOTS = ("Custom1", "Custom2", "Custom3", "Custom4", "Custom5", "Custom6")


# ---------------------------------------------------------------------------
# Preset modification detection (plan 036 P1 fix 2)
# ---------------------------------------------------------------------------


def is_preset_modified(
    config: AdjustmentsConfig,
    user_presets_path: Path = USER_PRESETS_PATH,
) -> bool:
    """Return True if the active preset's axes have been changed since loading.

    Compares the five colour/tone axes (saturation, exposure, sharpness, hue,
    vignette) against the canonical values for ``config.preset``.  Overlay
    settings (datestamp, watermark) are intentionally excluded — they are
    orthogonal features and should not trigger the modified marker.

    For built-in presets the canonical values come from ``BUILTIN_PRESET_VALUES``.
    For user custom slots the canonical values are reverse-converted from the
    ``AdjustmentProfile`` stored in the presets file.  If the slot is not found
    in the file (e.g. just allocated but not yet written) returns False.
    If ``config.preset`` is neither a built-in nor a known user slot, returns
    False (can't compare against an unknown baseline).
    """

    preset_name = config.preset

    if preset_name in BUILTIN_PRESET_VALUES:
        canonical = BUILTIN_PRESET_VALUES[preset_name]
    else:
        # User custom slot — load from disk and reverse-convert.
        try:
            user_presets = load_user_presets(user_presets_path)
        except Exception:
            return False
        profile = user_presets.get(preset_name)
        if profile is None:
            return False
        sat_ui = _factor_to_ui_int(profile.saturation - 1.0)
        exp_ui = _factor_to_ui_int_exposure(profile.exposure)
        shr_ui = _factor_to_ui_int(profile.sharpness - 1.0)
        hue_ui = round(profile.hue / 1.8) if profile.hue != 0 else 0
        canonical = {
            "saturation": max(-100, min(100, sat_ui)),
            "exposure": max(-100, min(100, exp_ui)),
            "sharpness": max(-100, min(100, shr_ui)),
            "hue": max(-100, min(100, hue_ui)),
            "vignette": max(0, min(100, profile.vignette)),
        }

    return (
        config.saturation != canonical["saturation"]
        or config.exposure != canonical["exposure"]
        or config.sharpness != canonical["sharpness"]
        or config.hue != canonical["hue"]
        or config.vignette != canonical["vignette"]
    )


# ---------------------------------------------------------------------------
# User preset persistence
# ---------------------------------------------------------------------------


def load_user_presets(path: Path = USER_PRESETS_PATH) -> dict[str, AdjustmentProfile]:
    """Load user-defined presets from ``path``.

    Returns an empty dict when the file is absent or unreadable.  Invalid
    entries are skipped with a warning so a corrupt file does not prevent
    the bridge from starting.

    File format::

        [presets.Custom1]
        saturation = 50
        exposure = -50
        sharpness = 0
        hue = 0
    """

    if not path.exists():
        return {}

    try:
        import tomllib

        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        LOGGER.warning("presets.load_failed path=%s error=%s", path, exc)
        return {}

    raw_presets = data.get("presets", {})
    if not isinstance(raw_presets, dict):
        LOGGER.warning("presets.invalid_format path=%s: [presets] is not a table", path)
        return {}

    result: dict[str, AdjustmentProfile] = {}
    for name, fields in raw_presets.items():
        if name not in _USER_PRESET_SLOTS:
            LOGGER.warning("presets.unknown_slot name=%r (skipping)", name)
            continue
        if not isinstance(fields, dict):
            LOGGER.warning("presets.invalid_entry name=%r (skipping)", name)
            continue
        try:
            result[name] = _profile_from_dict(name, fields)
        except (ValueError, TypeError) as exc:
            LOGGER.warning("presets.invalid_values name=%r error=%s (skipping)", name, exc)
    return result


def stamp_preset_into_config(
    name: str,
    user_presets: dict[str, AdjustmentProfile] | None = None,
) -> dict[str, int]:
    """Return per-axis UI integer values for the named preset.

    Used when the user selects a preset from the picker: the controller
    stamps these values into ``AdjustmentsConfig`` and persists, making the
    preset a one-shot starting template rather than a permanent override.

    For built-in presets, values come from ``BUILTIN_PRESET_VALUES``.
    For user custom slots, values are reverse-converted from the stored
    ``AdjustmentProfile`` floats.  Unknown names return all-zero values
    (same as ``Default``).

    Parameters
    ----------
    name:
        Preset name to look up (e.g. ``"Vivid"``, ``"Custom1"``).
    user_presets:
        Loaded user custom presets (from ``load_user_presets``).

    Returns
    -------
    dict with keys ``saturation``, ``exposure``, ``sharpness``, ``hue``,
    ``vignette`` — all UI integers suitable for ``AdjustmentsConfig``.
    """
    import math

    if user_presets is None:
        user_presets = {}

    if name in BUILTIN_PRESET_VALUES:
        return dict(BUILTIN_PRESET_VALUES[name])

    profile = user_presets.get(name)
    if profile is None:
        LOGGER.warning("presets.stamp_unknown name=%r — using zero values", name)
        return {"saturation": 0, "exposure": 0, "sharpness": 0, "hue": 0, "vignette": 0}

    sat_ui = _factor_to_ui_int(profile.saturation - 1.0)
    exp_ui = _factor_to_ui_int_exposure(profile.exposure)
    shr_ui = _factor_to_ui_int(profile.sharpness - 1.0)
    hue_ui = round(profile.hue / 1.8) if profile.hue != 0 else 0
    _ = math  # used via _factor_to_ui_int_exposure
    return {
        "saturation": max(-100, min(100, sat_ui)),
        "exposure": max(-100, min(100, exp_ui)),
        "sharpness": max(-100, min(100, shr_ui)),
        "hue": max(-100, min(100, hue_ui)),
        "vignette": max(0, min(100, profile.vignette)),
    }


def save_user_presets(
    path: Path,
    presets: dict[str, AdjustmentProfile],
) -> None:
    """Persist ``presets`` to ``path`` atomically.

    Uses tempfile + ``fp.flush() + os.fsync() + os.replace`` — the same
    pattern as ``write_config`` in ``config.py`` — so a write failure
    mid-stream never corrupts the existing file, and the data is durable
    before the rename commits it.  Mode 0o600: the file may contain
    user-chosen labels so it is kept readable only by the service user.

    Parameters
    ----------
    path:
        Destination file (normally ``USER_PRESETS_PATH``).
    presets:
        Mapping of slot name (``"Custom1"``…``"Custom6"``) to profile.
        Only valid slot names are written; others are silently dropped.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# InstantLink Bridge — user presets\n"]
    for slot in _USER_PRESET_SLOTS:
        profile = presets.get(slot)
        if profile is None:
            continue
        sat_ui = _factor_to_ui_int(profile.saturation - 1.0)  # round-trip via UI-int
        exp_ui = _factor_to_ui_int_exposure(profile.exposure)
        shr_ui = _factor_to_ui_int(profile.sharpness - 1.0)
        hue_ui = int(profile.hue / 1.8) if profile.hue != 0 else 0
        lines.append(f"\n[presets.{slot}]\n")
        lines.append(f"saturation = {sat_ui}\n")
        lines.append(f"exposure = {exp_ui}\n")
        lines.append(f"sharpness = {shr_ui}\n")
        lines.append(f"hue = {hue_ui}\n")

    text = "".join(lines)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            fp.write(text)
            # Flush Python buffers then fsync to storage — same durability
            # pattern as write_config() in config.py.  Without fsync the OS
            # may reorder the rename ahead of the write on a power failure.
            fp.flush()
            os.fsync(fp.fileno())
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, path)
    except Exception:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Preset resolution
# ---------------------------------------------------------------------------


def resolve_preset(
    config: AdjustmentsConfig,
    user_presets: dict[str, AdjustmentProfile] | None = None,
) -> AdjustmentProfile:
    """Return the ``AdjustmentProfile`` for the current adjustments in ``config``.

    Phase 5 (plan 036) semantics: presets are now *templates*, not gates.
    This function ALWAYS builds the profile from the per-axis integer values
    stored in ``config`` (saturation / exposure / sharpness / hue / vignette)
    plus overlay settings (datestamp / watermark / watermark_text).

    The ``config.preset`` field is a display label only — it records which
    template was last loaded but has no effect on the returned profile.  Any
    slider value the user edits is immediately live.

    This function never raises.  Callers can always expect a valid profile.

    Note: ``datestamp_text`` is NOT set here — the caller (controller /
    app.py) reads EXIF and formats the date, then patches ``datestamp_text``
    on the returned profile via ``dataclasses.replace``.
    """

    if user_presets is None:
        user_presets = {}  # kept for API compatibility; no longer used

    # Always derive from the live per-axis config values.
    return AdjustmentProfile.from_config(config)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _profile_from_dict(name: str, fields: dict[str, object]) -> AdjustmentProfile:
    """Build an AdjustmentProfile from a TOML preset table."""

    def _int_field(key: str) -> int:
        val = fields.get(key, 0)
        if isinstance(val, int):
            return val
        # TOML may give float for integer-valued numbers in some parsers.
        if isinstance(val, float):
            return int(val)
        return 0

    sat_ui = _int_field("saturation")
    exp_ui = _int_field("exposure")
    shr_ui = _int_field("sharpness")
    hue_ui = _int_field("hue")
    return AdjustmentProfile(
        saturation=1.0 + sat_ui / 100.0,
        exposure=2.0 ** (exp_ui / 100.0),
        sharpness=1.0 + shr_ui / 100.0,
        hue=round(hue_ui * 1.8),
    )


def _factor_to_ui_int(delta: float) -> int:
    """Convert an internal factor delta back to a UI -100…+100 int (rounded)."""
    return round(delta * 100)


def _factor_to_ui_int_exposure(factor: float) -> int:
    """Convert an exposure factor back to a UI -100…+100 int (rounded)."""
    import math

    if factor <= 0:
        return -100
    return round(math.log2(factor) * 100)
