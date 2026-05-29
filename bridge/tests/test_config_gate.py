"""Tests for the [ui] config section and headless surface gate."""

from __future__ import annotations

from pathlib import Path

import pytest

from instantlink_bridge.config import (
    BridgeConfig,
    UiConfig,
    UiSurface,
    load_config,
    render_config,
    write_config,
)
from instantlink_bridge.ui.display import NullDisplay, create_display
from instantlink_bridge.ui.input import NullInput, create_input

# ---------------------------------------------------------------------------
# Config schema: defaults
# ---------------------------------------------------------------------------


def test_ui_surface_defaults_to_lcd() -> None:
    config = BridgeConfig()
    assert config.ui.surface is UiSurface.LCD


def test_ui_config_defaults_to_lcd() -> None:
    ui = UiConfig()
    assert ui.surface is UiSurface.LCD


# ---------------------------------------------------------------------------
# Config load: [ui] section absent → default LCD
# ---------------------------------------------------------------------------


def test_load_config_missing_ui_section_defaults_to_lcd(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("[ftp]\n", encoding="utf-8")

    config = load_config(config_path)

    assert config.ui.surface is UiSurface.LCD


def test_load_config_no_file_defaults_to_lcd(tmp_path: Path) -> None:
    config = load_config(tmp_path / "nonexistent.toml")
    assert config.ui.surface is UiSurface.LCD


# ---------------------------------------------------------------------------
# Config load: explicit surface values
# ---------------------------------------------------------------------------


def test_load_config_ui_surface_headless(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('[ui]\nsurface = "headless"\n', encoding="utf-8")

    config = load_config(config_path)

    assert config.ui.surface is UiSurface.HEADLESS


def test_load_config_ui_surface_lcd(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('[ui]\nsurface = "lcd"\n', encoding="utf-8")

    config = load_config(config_path)

    assert config.ui.surface is UiSurface.LCD


def test_load_config_ui_surface_unknown_raises(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('[ui]\nsurface = "oled"\n', encoding="utf-8")

    with pytest.raises(ValueError, match=r"\[ui\]\.surface"):
        load_config(config_path)


def test_load_config_ui_not_a_table_raises(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("ui = 42\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"\[ui\]"):
        load_config(config_path)


# ---------------------------------------------------------------------------
# Config round-trip: render → load
# ---------------------------------------------------------------------------


def test_render_config_includes_ui_section_lcd() -> None:
    config = BridgeConfig()
    text = render_config(config)
    assert "[ui]" in text
    assert 'surface = "lcd"' in text


def test_render_config_includes_ui_section_headless() -> None:
    from dataclasses import replace

    config = replace(BridgeConfig(), ui=UiConfig(surface=UiSurface.HEADLESS))
    text = render_config(config)
    assert "[ui]" in text
    assert 'surface = "headless"' in text


def test_write_config_round_trip_headless(tmp_path: Path) -> None:
    from dataclasses import replace

    config_path = tmp_path / "config.toml"
    original = replace(BridgeConfig(), ui=UiConfig(surface=UiSurface.HEADLESS))
    write_config(original, config_path)

    loaded = load_config(config_path)

    assert loaded.ui.surface is UiSurface.HEADLESS


def test_write_config_round_trip_lcd(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    original = BridgeConfig()
    write_config(original, config_path)

    loaded = load_config(config_path)

    assert loaded.ui.surface is UiSurface.LCD


# ---------------------------------------------------------------------------
# Display factory gate
# ---------------------------------------------------------------------------


def test_create_display_headless_returns_null_display() -> None:
    display = create_display(UiSurface.HEADLESS)
    assert isinstance(display, NullDisplay)


def test_create_display_headless_does_not_probe_framebuffer() -> None:
    # Performance contract: the entire point of the headless gate is to skip
    # the ~0.5 s framebuffer probe. A future refactor must not re-introduce
    # the probe on headless or the cold-boot saving silently regresses.
    from unittest.mock import patch

    with patch(
        "instantlink_bridge.ui.display._st7789_framebuffer"
    ) as mock_probe:
        display = create_display(UiSurface.HEADLESS)
    assert isinstance(display, NullDisplay)
    mock_probe.assert_not_called()


def test_create_display_none_falls_through_to_null_on_non_pi() -> None:
    # On a non-Pi dev machine there is no ST7789 framebuffer and luma.lcd
    # is unavailable, so the factory falls back to NullDisplay.
    display = create_display(None)
    assert isinstance(display, NullDisplay)


def test_create_display_lcd_falls_through_to_null_on_non_pi() -> None:
    display = create_display(UiSurface.LCD)
    assert isinstance(display, NullDisplay)


# ---------------------------------------------------------------------------
# Input factory gate
# ---------------------------------------------------------------------------


def test_create_input_headless_returns_null_input() -> None:
    input_device = create_input(UiSurface.HEADLESS)
    assert isinstance(input_device, NullInput)


def test_create_input_none_falls_through_to_gpio_or_null() -> None:
    # When surface is None the probe chain runs: on a dev machine this may
    # succeed (GpioUiInput) or fail (NullInput). Either way it must NOT return
    # NullInput solely because of the headless gate.
    from instantlink_bridge.ui.input import GpioUiInput

    input_device = create_input(None)
    assert isinstance(input_device, GpioUiInput | NullInput)


def test_create_input_lcd_falls_through_to_gpio_or_null() -> None:
    from instantlink_bridge.ui.input import GpioUiInput

    input_device = create_input(UiSurface.LCD)
    assert isinstance(input_device, GpioUiInput | NullInput)
