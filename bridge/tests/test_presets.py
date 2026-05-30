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
    """'Black & white' preset must have saturation=0.0 (greyscale)."""
    assert PRESETS["Black & white"].saturation == pytest.approx(0.0), (
        f"Expected 'Black & white' saturation=0.0, got {PRESETS['Black & white'].saturation}"
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


def test_resolve_preset_always_uses_per_axis_values() -> None:
    """resolve_preset always builds the profile from per-axis config values (plan 036 phase 5).

    The preset field is a display label only.  Config with preset='Vivid' and
    saturation=0 resolves to saturation factor 1.0 (from the config value),
    NOT 1.5 (Vivid's built-in value).
    """
    cfg = AdjustmentsConfig(preset="Vivid", saturation=0)
    profile = resolve_preset(cfg)
    # saturation=0 → factor 1.0 + 0/100 = 1.0 (identity), regardless of preset name.
    assert profile.saturation == pytest.approx(1.0)


def test_resolve_preset_uses_per_axis_values() -> None:
    """Config with preset='Vivid' and saturation=50 resolves to factor 1.5 from config."""
    cfg = AdjustmentsConfig(preset="Vivid", saturation=50)
    profile = resolve_preset(cfg)
    # from_config: factor = 1.0 + 50/100 = 1.5
    assert profile.saturation == pytest.approx(1.5)


def test_resolve_preset_never_raises_on_unknown_name() -> None:
    """resolve_preset must not raise even for a completely unknown preset name."""
    cfg = AdjustmentsConfig(preset="Default")
    object.__setattr__(cfg, "preset", "NonExistentPreset999")
    # Should not raise; returns from_config(cfg) which uses per-axis values.
    profile = resolve_preset(cfg)
    assert profile is not None


def test_resolve_preset_unknown_name_uses_per_axis_config() -> None:
    """Unknown/stale preset name: resolve_preset still uses per-axis config values."""
    cfg = AdjustmentsConfig(preset="Default", saturation=25)
    object.__setattr__(cfg, "preset", "DeletedCustom5")

    profile = resolve_preset(cfg)
    # saturation=25 → factor 1.0 + 25/100 = 1.25
    assert profile.saturation == pytest.approx(1.25)


def test_resolve_preset_carries_overlays_from_config() -> None:
    """Overlays (datestamp, watermark) always come from config, not preset definition."""
    cfg = AdjustmentsConfig(preset="Vivid", datestamp=True, saturation=50)
    profile = resolve_preset(cfg)
    # datestamp from config.
    assert profile.datestamp is True
    # saturation from config, not Vivid's built-in 1.5.
    assert profile.saturation == pytest.approx(1.5)


def test_resolve_preset_watermark_text_from_config() -> None:
    """watermark_text comes from config.watermark_text when watermark=True."""
    cfg = AdjustmentsConfig(preset="Default", watermark=True, watermark_text="Test")
    profile = resolve_preset(cfg)
    assert profile.watermark is True
    assert profile.watermark_text == "Test"


def test_resolve_preset_overlays_off() -> None:
    """Overlays are off when config says so, regardless of preset name."""
    cfg = AdjustmentsConfig(
        preset="Black & white", watermark=False, datestamp=False, saturation=-100
    )
    profile = resolve_preset(cfg)
    assert profile.watermark is False
    assert profile.datestamp is False
    # saturation=-100 → factor 0.0
    assert profile.saturation == pytest.approx(0.0)


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
    """Config with preset='Instax Film' resolves from per-axis config values (plan 036 phase 5).

    After stamping the Instax Film preset into config, the per-axis values
    are saturation=-10, sharpness=-10, vignette=50.  resolve_preset uses
    those per-axis values directly.
    """
    from instantlink_bridge.imaging.presets import BUILTIN_PRESET_VALUES

    axes = BUILTIN_PRESET_VALUES["Instax Film"]
    cfg = AdjustmentsConfig(
        preset="Instax Film",
        saturation=axes["saturation"],
        sharpness=axes["sharpness"],
        vignette=axes["vignette"],
    )
    profile = resolve_preset(cfg)
    # saturation=-10 → factor 1.0 + (-10/100) = 0.9
    assert profile.saturation == pytest.approx(0.9, abs=0.01)
    # sharpness=-10 → factor 0.9
    assert profile.sharpness == pytest.approx(0.9, abs=0.01)
    # vignette=50 passes through directly
    assert profile.vignette == 50


def test_vignette_any_value_in_range_is_valid() -> None:
    """AdjustmentsConfig accepts any vignette integer in [0, 100] (continuous range)."""
    for v in (0, 1, 33, 50, 99, 100):
        cfg = AdjustmentsConfig(vignette=v)
        assert cfg.vignette == v


# ---------------------------------------------------------------------------
# Plan 036 Phase 5 — new tests
# ---------------------------------------------------------------------------


def test_slot_cap_is_6() -> None:
    """User preset slot cap must be 6 (plan 036 phase 5)."""
    from instantlink_bridge.imaging.presets import _MAX_USER_PRESETS, _USER_PRESET_SLOTS

    assert _MAX_USER_PRESETS == 6
    assert len(_USER_PRESET_SLOTS) == 6
    assert "Custom5" in _USER_PRESET_SLOTS
    assert "Custom6" in _USER_PRESET_SLOTS


def test_bw_renamed_to_black_and_white() -> None:
    """'Black & white' preset replaces 'B&W' (critic P2, plan 036 phase 5)."""
    assert "Black & white" in PRESETS
    assert "B&W" not in PRESETS


def test_black_and_white_preset_has_zero_saturation() -> None:
    """'Black & white' preset must have saturation=0.0 (greyscale)."""
    assert PRESETS["Black & white"].saturation == pytest.approx(0.0)


def test_valid_preset_names_includes_black_and_white() -> None:
    """VALID_PRESET_NAMES must include 'Black & white' and not 'B&W' or 'Custom'."""
    from instantlink_bridge.imaging.presets import VALID_PRESET_NAMES

    assert "Black & white" in VALID_PRESET_NAMES
    assert "B&W" not in VALID_PRESET_NAMES
    assert "Custom" not in VALID_PRESET_NAMES


def test_bw_preset_migrates_to_black_and_white(tmp_path: Path) -> None:
    """Loading a config with preset='B&W' silently migrates to 'Black & white'."""
    from instantlink_bridge.config import load_config

    config_path = tmp_path / "config.toml"
    config_path.write_text('[adjustments]\npreset = "B&W"\n', encoding="utf-8")
    cfg = load_config(config_path)
    assert cfg.adjustments.preset == "Black & white"


def test_custom_sentinel_migrates_to_default(tmp_path: Path) -> None:
    """Loading a config with preset='Custom' silently migrates to 'Default'."""
    from instantlink_bridge.config import load_config

    config_path = tmp_path / "config.toml"
    config_path.write_text('[adjustments]\npreset = "Custom"\nsaturation = 25\n', encoding="utf-8")
    cfg = load_config(config_path)
    # preset label migrates; per-axis values are preserved.
    assert cfg.adjustments.preset == "Default"
    assert cfg.adjustments.saturation == 25


def test_save_user_presets_fsync_invoked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """save_user_presets must call os.fsync before os.replace (durability gate)."""
    import instantlink_bridge.imaging.presets as _pm

    fsync_calls: list[int] = []
    replace_calls: list[int] = []
    call_order: list[str] = []

    orig_fsync = os.fsync
    orig_replace = os.replace

    def tracking_fsync(fd: int) -> None:
        fsync_calls.append(fd)
        call_order.append("fsync")
        orig_fsync(fd)

    def tracking_replace(src: object, dst: object) -> None:
        replace_calls.append(1)
        call_order.append("replace")
        orig_replace(src, dst)  # type: ignore[arg-type]

    monkeypatch.setattr(_pm.os, "fsync", tracking_fsync)
    monkeypatch.setattr(_pm.os, "replace", tracking_replace)

    path = tmp_path / "presets.toml"
    save_user_presets(path, {"Custom1": AdjustmentProfile(saturation=1.25)})

    assert len(fsync_calls) == 1, "os.fsync must be called exactly once"
    assert len(replace_calls) == 1, "os.replace must be called exactly once"
    assert call_order == ["fsync", "replace"], (
        f"fsync must precede replace; got {call_order}"
    )


def test_stamp_preset_into_config_builtin() -> None:
    """stamp_preset_into_config returns correct per-axis ints for built-in presets."""
    from instantlink_bridge.imaging.presets import stamp_preset_into_config

    axes = stamp_preset_into_config("Vivid")
    assert axes["saturation"] == 50
    assert axes["sharpness"] == 25
    assert axes["exposure"] == 0
    assert axes["hue"] == 0
    assert axes["vignette"] == 0


def test_stamp_preset_into_config_black_and_white() -> None:
    """stamp_preset_into_config for 'Black & white' returns saturation=-100."""
    from instantlink_bridge.imaging.presets import stamp_preset_into_config

    axes = stamp_preset_into_config("Black & white")
    assert axes["saturation"] == -100


def test_stamp_preset_into_config_instax_film() -> None:
    """stamp_preset_into_config for 'Instax Film' returns saturation=-10, vignette=50."""
    from instantlink_bridge.imaging.presets import stamp_preset_into_config

    axes = stamp_preset_into_config("Instax Film")
    assert axes["saturation"] == -10
    assert axes["sharpness"] == -10
    assert axes["vignette"] == 50


def test_stamp_preset_into_config_user_preset(tmp_path: Path) -> None:
    """stamp_preset_into_config for a user custom preset reverses the float factors."""
    from instantlink_bridge.imaging.presets import stamp_preset_into_config

    user = {"Custom1": AdjustmentProfile(saturation=1.5, exposure=1.0, sharpness=1.0, hue=0)}
    axes = stamp_preset_into_config("Custom1", user)
    assert axes["saturation"] == 50
    assert axes["exposure"] == 0
    assert axes["sharpness"] == 0


def test_custom5_and_custom6_valid_slots(tmp_path: Path) -> None:
    """Custom5 and Custom6 can be saved and loaded (slot cap is now 6)."""
    path = tmp_path / "presets.toml"
    presets: dict[str, AdjustmentProfile] = {
        "Custom5": AdjustmentProfile(saturation=1.1),
        "Custom6": AdjustmentProfile(saturation=0.8),
    }
    save_user_presets(path, presets)
    loaded = load_user_presets(path)
    assert "Custom5" in loaded
    assert "Custom6" in loaded
    assert loaded["Custom5"].saturation == pytest.approx(1.1, abs=0.02)
    assert loaded["Custom6"].saturation == pytest.approx(0.8, abs=0.02)


# ---------------------------------------------------------------------------
# Plan 036 P1 fix 2 — is_preset_modified tests
# ---------------------------------------------------------------------------


def test_is_preset_modified_false_when_axes_match_builtin() -> None:
    """is_preset_modified returns False when all axes match the named built-in preset."""
    from instantlink_bridge.imaging.presets import is_preset_modified

    # Vivid: saturation=50, exposure=0, sharpness=25, hue=0, vignette=0
    cfg = AdjustmentsConfig(
        preset="Vivid", saturation=50, exposure=0, sharpness=25, hue=0, vignette=0
    )
    assert is_preset_modified(cfg) is False


def test_is_preset_modified_true_when_axis_differs() -> None:
    """is_preset_modified returns True when any colour axis differs from the preset."""
    from instantlink_bridge.imaging.presets import is_preset_modified

    # Vivid canonical saturation is 50; using 37 triggers the marker.
    cfg = AdjustmentsConfig(
        preset="Vivid", saturation=37, exposure=0, sharpness=25, hue=0, vignette=0
    )
    assert is_preset_modified(cfg) is True


def test_is_preset_modified_ignores_datestamp_and_watermark() -> None:
    """Overlay settings (datestamp, watermark) do NOT trigger the modified marker."""
    from instantlink_bridge.imaging.presets import is_preset_modified

    # Default: all axes = 0.
    cfg = AdjustmentsConfig(
        preset="Default",
        saturation=0, exposure=0, sharpness=0, hue=0, vignette=0,
        datestamp=True, watermark=True,
    )
    assert is_preset_modified(cfg) is False


def test_is_preset_modified_false_for_unknown_preset() -> None:
    """is_preset_modified returns False for an unknown preset name (no baseline)."""
    from instantlink_bridge.imaging.presets import is_preset_modified

    # Use object.__setattr__ to bypass validation — same pattern as
    # test_resolve_preset_never_raises_on_unknown_name above.
    cfg = AdjustmentsConfig(preset="Default", saturation=99)
    object.__setattr__(cfg, "preset", "NonExistent999")
    assert is_preset_modified(cfg) is False


def test_is_preset_modified_true_for_user_custom_when_axis_differs(tmp_path: Path) -> None:
    """is_preset_modified returns True for a custom slot when axes differ from saved values."""
    from instantlink_bridge.imaging.presets import is_preset_modified, save_user_presets

    presets_path = tmp_path / "presets.toml"
    # Custom1 saved with saturation factor 1.5 (UI=50).
    save_user_presets(presets_path, {"Custom1": AdjustmentProfile(saturation=1.5)})

    # Config has saturation=30 (differs from saved 50).
    cfg = AdjustmentsConfig(preset="Custom1", saturation=30)
    assert is_preset_modified(cfg, presets_path) is True


def test_is_preset_modified_false_for_user_custom_when_axes_match(tmp_path: Path) -> None:
    """is_preset_modified returns False for a custom slot when axes match saved values."""
    from instantlink_bridge.imaging.presets import is_preset_modified, save_user_presets

    presets_path = tmp_path / "presets.toml"
    save_user_presets(presets_path, {"Custom1": AdjustmentProfile(saturation=1.5)})

    # Config with saturation=50 matches the saved factor 1.5.
    cfg = AdjustmentsConfig(preset="Custom1", saturation=50)
    assert is_preset_modified(cfg, presets_path) is False
