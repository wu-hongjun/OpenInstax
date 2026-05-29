"""LCD theming — iOS 26 / Liquid Glass-inspired palette and primitives.

The 240×240 ST7789 has no GPU, so the "Liquid Glass" effect is faked with
cheap PIL primitives that read as glassy without costing real-time blur:

* Rounded cards with subtle vertical gradients instead of dynamic blur
* Pill capsules for status / hint chips (long rounded rectangles)
* High-contrast accent for the selected row (iOS picker style)
* Hairline 1 px separators between rows in a card

The :class:`Theme` dataclass owns the colour tokens; light and dark
instances are defined as module constants. ``theme_for(appearance)`` is
the lookup the renderer uses — it returns the right :class:`Theme` for
the user's :class:`Appearance` choice. SYSTEM defaults to LIGHT because
the bridge has no ambient sensor; revisit if a future hardware revision
adds one.

Colours follow Apple's system colour names (semantic) rather than
literal hex names so swapping the palette in the future doesn't touch
renderer code.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "Appearance",
    "DARK_THEME",
    "LIGHT_THEME",
    "Theme",
    "theme_for",
]


class Appearance(StrEnum):
    """User-selectable LCD appearance.

    SYSTEM tracks an ambient signal when one is available; on the
    current hardware (no ambient sensor) it falls through to LIGHT.
    """

    LIGHT = "light"
    DARK = "dark"
    SYSTEM = "system"


# ---------------------------------------------------------------------------
# Colour tokens
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Theme:
    """Resolved colour palette for one appearance.

    Every renderer reads tokens via this struct so adding a new theme is
    a single instance, not a sweep across files. Names mirror Apple's
    semantic system colours where possible.
    """

    # Surface layers (bottom → top)
    bg: str  # screen background
    surface: str  # card / row container
    surface_elevated: str  # elevated card (e.g. selected row in light)
    separator: str  # hairline divider between rows

    # Text
    label_primary: str  # title and main text
    label_secondary: str  # subtitle / muted
    label_inverse: str  # text on a vibrant accent (always high-contrast)

    # Accents — match iOS system colours
    accent_blue: str  # primary actions, picker selection
    accent_green: str  # ready / success
    accent_yellow: str  # warning / searching
    accent_red: str  # error / destructive
    accent_orange: str  # secondary warnings

    # Status pill tint (slightly translucent feel — full alpha but desaturated)
    pill_bg_green: str
    pill_bg_yellow: str
    pill_bg_red: str

    # Hint bar
    hint_bg: str  # bar background
    hint_fg: str  # hint text


# iOS 26-inspired *Light* theme. Backgrounds use the system gray-6 family;
# foregrounds use label primary/secondary. Accents are the canonical iOS
# system colours that Apple ships in UIKit.
LIGHT_THEME = Theme(
    bg="#F2F2F7",  # systemGray6 — iOS default background
    surface="#FFFFFF",  # systemBackground
    surface_elevated="#E5E5EA",  # systemGray5
    separator="#C6C6C8",  # iOS separator
    label_primary="#000000",  # label
    label_secondary="#6B6B70",  # secondaryLabel
    label_inverse="#FFFFFF",
    accent_blue="#007AFF",  # systemBlue
    accent_green="#34C759",  # systemGreen
    accent_yellow="#FFCC00",  # systemYellow
    accent_red="#FF3B30",  # systemRed
    accent_orange="#FF9500",  # systemOrange
    # Frosted pill fills: 70 % accent blended with systemBackground (#FFFFFF) at 30 %
    # so the capsule reads as a translucent glass chip rather than a solid badge.
    # Formula: round(accent * 0.70 + 255 * 0.30)
    pill_bg_green="#65D87C",   # #34C759 * 0.70 + #FFF * 0.30 → #65D87C
    pill_bg_yellow="#FFD833",  # #FFCC00 * 0.70 + #FFF * 0.30 → #FFD833
    pill_bg_red="#FF6D65",     # #FF3B30 * 0.70 + #FFF * 0.30 → #FF6D65
    hint_bg="#E5E5EA",
    hint_fg="#6B6B70",
)


# Dark theme — backgrounds drop to black/near-black to make the small LCD
# look like an iOS dark screen. Accents brighten slightly per Apple's
# dark-mode adjustments (e.g. systemBlue → #0A84FF).
DARK_THEME = Theme(
    bg="#000000",  # systemBackground (dark)
    surface="#1C1C1E",  # systemGray6 (dark)
    surface_elevated="#2C2C2E",  # systemGray5 (dark)
    separator="#38383A",
    label_primary="#FFFFFF",
    label_secondary="#A1A1A6",
    label_inverse="#000000",
    accent_blue="#0A84FF",  # systemBlue (dark)
    accent_green="#30D158",  # systemGreen (dark)
    accent_yellow="#FFD60A",  # systemYellow (dark)
    accent_red="#FF453A",  # systemRed (dark)
    accent_orange="#FF9F0A",
    # Frosted pill fills: 82 % dark accent + 18 % dark surface (#1C1C1E = 28,28,30)
    # Dark mode needs less muting than light (already on dark bg) — 82/18 keeps
    # the accent vibrant but adds the frosted-glass tonal shift.
    # Formula: round(accent * 0.82 + surface * 0.18)
    pill_bg_green="#2DB44E",   # #30D158 * 0.82 + #1C1C1E * 0.18 → #2DB44E
    pill_bg_yellow="#D4B20D",  # #FFD60A * 0.82 + #1C1C1E * 0.18 → #D4B20D
    pill_bg_red="#D43A30",     # #FF453A * 0.82 + #1C1C1E * 0.18 → #D43A30
    hint_bg="#1C1C1E",
    hint_fg="#A1A1A6",
)


def theme_for(appearance: Appearance | str) -> Theme:
    """Return the :class:`Theme` for the given appearance.

    Accepts a bare BCP-47-style string too so callers don't have to
    convert the snapshot's string-typed field. Unknown values fall
    through to LIGHT.
    """

    if isinstance(appearance, str):
        try:
            appearance = Appearance(appearance)
        except ValueError:
            return LIGHT_THEME
    if appearance is Appearance.DARK:
        return DARK_THEME
    return LIGHT_THEME
