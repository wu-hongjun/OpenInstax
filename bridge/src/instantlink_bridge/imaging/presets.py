"""Built-in and user-defined ``AdjustmentProfile`` presets.

A preset is a named profile; the active preset name lives in
``AdjustmentsConfig.preset``.  Built-ins are defined here; user customs
persist in ``/etc/InstantLinkBridge/presets.toml``.
"""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from instantlink_bridge.imaging.postprocess import AdjustmentProfile

if TYPE_CHECKING:
    from instantlink_bridge.config import AdjustmentsConfig  # pragma: no cover

__all__ = [
    "PRESETS",
    "PRESET_ORDER",
    "USER_PRESETS_PATH",
    "VALID_PRESET_NAMES",
    "load_user_presets",
    "resolve_preset",
    "save_user_presets",
]

LOGGER = logging.getLogger(__name__)

USER_PRESETS_PATH = Path("/etc/InstantLinkBridge/presets.toml")

# ---------------------------------------------------------------------------
# Built-in presets
# ---------------------------------------------------------------------------
# Values here are the *internal* AdjustmentProfile float factors, NOT the
# -100…+100 UI integers.  A preset can encode any float; the UI picker only
# constrains user-driven per-axis edits in "Custom" mode.
#
# NOTE: "Instax Film" values filled in Phase 6:
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
    "B&W": AdjustmentProfile(
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

# Stable picker order: built-ins first, then user custom slots, then the
# always-present "Custom" sentinel.  The user-custom slots are appended by
# callers that merge PRESET_ORDER with loaded user presets.
PRESET_ORDER: tuple[str, ...] = (
    "Default",
    "Vivid",
    "Soft",
    "B&W",
    "Instax Film",
    # User custom slots are not listed here — callers insert them between
    # "Instax Film" and "Custom" after calling load_user_presets().
    "Custom",
)

# The full set of valid preset names across built-ins, user custom slots, and
# the "Custom" sentinel.  Used for config validation.
VALID_PRESET_NAMES: frozenset[str] = frozenset(
    {
        "Default",
        "Vivid",
        "Soft",
        "B&W",
        "Instax Film",
        "Custom1",
        "Custom2",
        "Custom3",
        "Custom4",
        "Custom",
    }
)

_MAX_USER_PRESETS = 4
_USER_PRESET_SLOTS = ("Custom1", "Custom2", "Custom3", "Custom4")


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


def save_user_presets(
    path: Path,
    presets: dict[str, AdjustmentProfile],
) -> None:
    """Persist ``presets`` to ``path`` atomically.

    Uses tempfile + ``os.replace`` — the same pattern as
    ``_atomic_write_credential_file`` in ``ui/controller.py`` — so a write
    failure mid-stream never corrupts the existing file.  Mode 0o600: the
    file may contain user-chosen labels so it is kept readable only by the
    service user.

    Parameters
    ----------
    path:
        Destination file (normally ``USER_PRESETS_PATH``).
    presets:
        Mapping of slot name (``"Custom1"``…``"Custom4"``) to profile.
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
    """Return the ``AdjustmentProfile`` for the active preset in ``config``.

    Resolution rules:

    * ``preset == "Custom"`` → build a profile from the per-axis int values
      stored in ``config`` (saturation, exposure, sharpness, hue) plus the
      overlay settings.  This is effectively ``AdjustmentProfile.from_config``
      minus the datestamp_text fill (the caller must set that from EXIF).
    * ``preset`` is a known built-in name → return the built-in profile, but
      overlay the user's ``datestamp`` / ``watermark`` / ``watermark_text``
      settings on top (overlays are orthogonal to colour presets).
    * ``preset`` is a user custom slot → look it up in ``user_presets`` and
      apply overlays as above.
    * Unknown name (stale custom that was deleted) → log a warning, fall
      back to the ``"Default"`` profile with overlays applied.

    This function never raises.  Callers can always expect a valid profile.

    Note: ``datestamp_text`` is NOT set here — the caller (controller /
    app.py) reads EXIF and formats the date, then patches ``datestamp_text``
    on the returned profile via ``dataclasses.replace``.
    """

    if user_presets is None:
        user_presets = {}

    name = config.preset

    if name == "Custom":
        return AdjustmentProfile.from_config(config)

    # Look up built-in or user custom.
    profile = PRESETS.get(name) or user_presets.get(name)

    if profile is None:
        LOGGER.warning(
            "presets.unknown_preset name=%r — falling back to Default",
            name,
        )
        profile = PRESETS["Default"]

    # Overlays (datestamp, watermark) are orthogonal to colour presets — the
    # user's overlay settings are always applied on top.
    profile = replace(
        profile,
        datestamp=config.datestamp,
        watermark=config.watermark,
        datestamp_text="",  # caller fills from EXIF
        watermark_text=config.watermark_text if config.watermark else "",
    )
    return profile


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
