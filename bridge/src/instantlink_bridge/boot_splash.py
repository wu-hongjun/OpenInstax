"""Early LCD boot splash entry point.

Writes a solid brand colour to the framebuffer so the user gets immediate
feedback that the bridge has started booting. The heavier UI stack (PIL,
`instantlink_bridge.ui.display`, `instantlink_bridge.config` — which transitively
pulls Pillow via `imaging.pipeline.FitMode`) is intentionally NOT imported here:
on the Pi Zero 2 W it adds ~1.2 s of cumulative import time, which contends with
the bridge service for the SD card / CPU during cold boot (docs/plans/032 Q1
made the splash run in parallel with the bridge, so any time the splash spends
importing is time stolen from the bridge's own startup). Stdlib only.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

LOGGER = logging.getLogger(__name__)

# 240x240 ST7789 LCD over SPI via fbtft, RGB565 little-endian (verified on Pi
# Zero 2 W with `dtoverlay=fbtft,spi0-0,st7789v,...`).
FB_DEVICE = Path("/dev/fb1")
WIDTH = 240
HEIGHT = 240
# Brand teal-ish dark colour — enough visual feedback that the bridge is booting
# without needing a font/text renderer.
SPLASH_COLOR_RGB = (16, 80, 96)


def encode_rgb565(r: int, g: int, b: int) -> bytes:
    """Encode an 8-bit RGB triple to a 2-byte little-endian RGB565 pixel."""

    value = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
    return value.to_bytes(2, "little")


def main() -> None:
    """Fill the framebuffer with the brand colour and exit."""

    parser = argparse.ArgumentParser(description="Draw the InstantLink Bridge boot splash")
    # `--config` is preserved for backwards compatibility with the existing systemd unit
    # (`instantlink-bridge-splash --config /etc/InstantLinkBridge/config.toml --hold 0.25`)
    # but is intentionally ignored — no config load happens, see module docstring.
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="(ignored; preserved for systemd unit compat)",
    )
    parser.add_argument(
        "--hold",
        type=float,
        default=0.25,
        help="seconds to keep the process alive after drawing",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Python logging level",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    pixel = encode_rgb565(*SPLASH_COLOR_RGB)
    frame = pixel * (WIDTH * HEIGHT)
    try:
        FB_DEVICE.write_bytes(frame)
    except OSError as exc:
        sys.stderr.write(f"boot_splash: framebuffer write failed: {exc}\n")
    LOGGER.info("boot_splash.rendered")
    time.sleep(max(0.0, args.hold))


if __name__ == "__main__":
    main()
