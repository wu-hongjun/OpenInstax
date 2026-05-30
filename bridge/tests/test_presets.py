"""Tests for instantlink_bridge.imaging.presets (plan 035 phase 5)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from instantlink_bridge.config import AdjustmentsConfig
from instantlink_bridge.imaging.postprocess import AdjustmentProfile
from instantlink_bridge.imaging.presets import (
    PRESETS,
    load_user_presets,
    resolve_preset,
    save_user_presets,
)

# ---------------------------------------------------------------------------
# Built-in preset values
# ---------------------------------------------------------------------------


def test_default_preset_is_identity() -> None:
    """Default preset must be the AdjustmentProfile identity (all pass-through)."""
    identity = AdjustmentProfile()
    default = PRESETS["Default"]
    assert default.saturation == identity.saturation
    assert default.exposure == identity.exposure
    assert default.sharpness == identity.sharpness
    assert default.hue == identity.hue


def test_vivid_preset_has_higher_saturation() -> None:
    """Vivid preset must have a saturation factor greater than 1.0."""
    assert PRESETS["Vivid"].saturation > 1.0, (
        f"Expected Vivid saturation > 1.0, got {PRESETS['Vivid'].saturation}"
    )


def test_vivid_preset_has_higher_sharpness() -> None:
    """Vivid preset must have a sharpness factor greater than 1.0."""
    assert PRESETS["Vivid"].sharpness > 1.0, (
        f"Expected Vivid sharpness > 1.0, got {PRESETS['Vivid'].sharpness}"
    )


def test_bw_preset_has_zero_saturation() -> None:
    """B&W preset must have saturation=0.0 (greyscale)."""
    assert PRESETS["B&W"].saturation == pytest.approx(0.0), (
        f"Expected B&W saturation=0.0, got {PRESETS['B&W'].saturation}"
    )


def test_soft_preset_has_lower_saturation_than_default() -> None:
    """Soft preset must have lower saturation than Default (< 1.0)."""
    assert PRESETS["Soft"].saturation < 1.0


def test_instax_film_preset_has_vignette_50() -> None:
    """Phase 6: Instax Film preset has vignette=50, saturation=0.9, sharpness=0.9."""
    instax = PRESETS["Instax Film"]
    assert instax.vignette == 50, f"Expected vignette=50, got {instax.vignette}"
    assert instax.saturation == pytest.approx(0.9), (
        f"Expected saturation=0.9, got {instax.saturation}"
    )
    assert instax.sharpness == pytest.approx(0.9), (
        f"Expected sharpness=0.9, got {instax.sharpness}"
    )
    assert instax.exposure == pytest.approx(1.0)
    assert instax.hue == 0


# ---------------------------------------------------------------------------
# resolve_preset
# ---------------------------------------------------------------------------


def test_resolve_preset_uses_built_in_when_named() -> None:
    """Config with preset='Vivid' resolves to Vivid's saturation regardless of per-axis values."""
    cfg = AdjustmentsConfig(preset="Vivid", saturation=0)  # per-axis says 0, preset overrides
    profile = resolve_preset(cfg)
    assert profile.saturation == pytest.approx(PRESETS["Vivid"].saturation)


def test_resolve_preset_custom_uses_per_axis_values() -> None:
    """Config with preset='Custom' and saturation=50 resolves to factor 1.5."""
    cfg = AdjustmentsConfig(preset="Custom", saturation=50)
    profile = resolve_preset(cfg)
    # from_config: factor = 1.0 + 50/100 = 1.5
    assert profile.saturation == pytest.approx(1.5)


def test_resolve_preset_unknown_falls_back_to_default() -> None:
    """Unknown preset name (stale custom) falls back to Default profile."""
    # We need to bypass the config validator — use a raw AdjustmentsConfig workaround.
    # The validator blocks unknown names, so simulate by patching post-construction.

    cfg = AdjustmentsConfig(preset="Default")
    # Force an unknown preset name via object.__setattr__ on the frozen dataclass.
    object.__setattr__(cfg, "preset", "DeletedCustom5")

    profile = resolve_preset(cfg)
    default = PRESETS["Default"]
    assert profile.saturation == pytest.approx(default.saturation)
    assert profile.exposure == pytest.approx(default.exposure)
    assert profile.sharpness == pytest.approx(default.sharpness)


def test_resolve_preset_carries_overlays_across_presets() -> None:
    """preset='Vivid' with datestamp=True carries Vivid saturation AND datestamp=True."""
    cfg = AdjustmentsConfig(preset="Vivid", datestamp=True)
    profile = resolve_preset(cfg)
    assert profile.saturation == pytest.approx(PRESETS["Vivid"].saturation)
    assert profile.datestamp is True


def test_resolve_preset_custom_carries_watermark_text() -> None:
    """preset='Custom' with watermark=True carries watermark_text from config."""
    cfg = AdjustmentsConfig(preset="Custom", watermark=True, watermark_text="Test")
    profile = resolve_preset(cfg)
    assert profile.watermark is True
    assert profile.watermark_text == "Test"


def test_resolve_preset_non_custom_applies_user_overlay_not_preset_overlay() -> None:
    """For a named preset, overlays come from config (not preset definition)."""
    cfg = AdjustmentsConfig(preset="B&W", watermark=False, datestamp=False)
    profile = resolve_preset(cfg)
    assert profile.watermark is False
    assert profile.datestamp is False
    assert profile.saturation == pytest.approx(0.0)  # still B&W greyscale


def test_resolve_preset_never_raises_on_unknown_name() -> None:
    """resolve_preset must not raise even for a completely unknown preset name."""
    cfg = AdjustmentsConfig(preset="Default")
    object.__setattr__(cfg, "preset", "NonExistentPreset999")
    # Should not raise; should return Default.
    profile = resolve_preset(cfg)
    assert profile is not None


# ---------------------------------------------------------------------------
# User preset persistence
# ---------------------------------------------------------------------------


def test_load_user_presets_missing_file_returns_empty(tmp_path: Path) -> None:
    """Missing presets file returns an empty dict without error."""
    result = load_user_presets(tmp_path / "nonexistent.toml")
    assert result == {}


def test_load_user_presets_roundtrip(tmp_path: Path) -> None:
    """Save two custom presets and load them back — values must be identical."""
    path = tmp_path / "presets.toml"
    presets: dict[str, AdjustmentProfile] = {
        "Custom1": AdjustmentProfile(saturation=1.5, exposure=1.0, sharpness=1.0, hue=0),
        "Custom2": AdjustmentProfile(saturation=0.5, exposure=0.5, sharpness=1.0, hue=0),
    }
    save_user_presets(path, presets)
    loaded = load_user_presets(path)

    assert "Custom1" in loaded
    assert "Custom2" in loaded
    assert loaded["Custom1"].saturation == pytest.approx(presets["Custom1"].saturation, abs=0.02)
    assert loaded["Custom2"].saturation == pytest.approx(presets["Custom2"].saturation, abs=0.02)
    assert loaded["Custom2"].exposure == pytest.approx(presets["Custom2"].exposure, abs=0.02)


def test_save_user_presets_creates_parent_dirs(tmp_path: Path) -> None:
    """save_user_presets creates parent directories if they don't exist."""
    path = tmp_path / "nested" / "dir" / "presets.toml"
    save_user_presets(path, {"Custom1": AdjustmentProfile(saturation=1.25)})
    assert path.exists()


def test_save_user_presets_file_mode(tmp_path: Path) -> None:
    """Saved presets file must have mode 0o600."""
    path = tmp_path / "presets.toml"
    save_user_presets(path, {"Custom1": AdjustmentProfile()})
    mode = oct(path.stat().st_mode & 0o777)
    assert mode == oct(0o600), f"Expected 0600 but got {mode}"


def test_save_user_presets_atomic_on_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If os.replace fails, the original file must be unchanged."""
    path = tmp_path / "presets.toml"
    original_content = "# original\n"
    path.write_text(original_content)

    def failing_replace(src: object, dst: object) -> None:
        # Clean up the temp file to simulate a mid-write failure, then raise.
        try:
            Path(str(src)).unlink(missing_ok=True)
        except OSError:
            pass
        raise OSError("simulated failure")

    monkeypatch.setattr(os, "replace", failing_replace)
    with pytest.raises(OSError, match="simulated failure"):
        save_user_presets(path, {"Custom1": AdjustmentProfile(saturation=1.5)})

    # Original file must be intact.
    assert path.read_text() == original_content


def test_load_user_presets_skips_unknown_slots(tmp_path: Path) -> None:
    """Unknown slot names in the TOML are silently ignored on load."""
    path = tmp_path / "presets.toml"
    path.write_text(
        "[presets.UnknownSlot]\nsaturation = 50\nexposure = 0\nsharpness = 0\nhue = 0\n"
    )
    result = load_user_presets(path)
    assert "UnknownSlot" not in result


def test_load_user_presets_returns_empty_on_corrupt_file(tmp_path: Path) -> None:
    """A corrupt (non-TOML) file returns empty dict without raising."""
    path = tmp_path / "presets.toml"
    path.write_text("NOT VALID TOML !!!! @@@@")
    result = load_user_presets(path)
    assert result == {}


# ---------------------------------------------------------------------------
# Phase 6: Instax Film preset resolution + vignette config validation
# ---------------------------------------------------------------------------


def test_instax_film_preset_resolves_for_pipeline() -> None:
    """Config with preset='Instax Film' resolves to Instax Film profile via resolve_preset.

    The vignette, saturation, and sharpness values must match the built-in
    preset definition, regardless of the per-axis values stored in config.
    """
    cfg = AdjustmentsConfig(preset="Instax Film", saturation=0, sharpness=0)
    profile = resolve_preset(cfg)
    instax = PRESETS["Instax Film"]
    assert profile.vignette == instax.vignette
    assert profile.saturation == pytest.approx(instax.saturation)
    assert profile.sharpness == pytest.approx(instax.sharpness)


def test_vignette_invalid_value_raises() -> None:
    """AdjustmentsConfig with vignette not in {0, 25, 50, 75, 100} raises ValueError."""
    with pytest.raises(ValueError, match="vignette"):
        AdjustmentsConfig(vignette=33)
