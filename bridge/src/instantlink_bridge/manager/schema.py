"""Schema descriptors for the Mac Bridge Control UI (plan 039).

The Mac renders settings sections generically from these schemas. Each
field returns its display type (slider / picker / toggle / text), its
constraints (range, options), its labels (English source strings — the
Mac uses them as L() lookup keys), and its dependency on other fields.

Single source of truth: when a new field is added to
:class:`instantlink_bridge.config.AdjustmentsConfig`, the schema endpoint
must reflect it. Tests guard this via a coverage assertion that compares
schema field keys to dataclass field names.
"""

from __future__ import annotations

from instantlink_bridge.config import DatestampFormat
from instantlink_bridge.manager.contract import JsonObject, JsonValue
from instantlink_bridge.ui.settings import (
    BUILTIN_PRESET_NAMES,
    SETTING_HELP_TEXT,
    USER_PRESET_SLOT_NAMES,
    SettingKey,
)

# Datestamp format presets borrow macOS-vocabulary labels per plan 037 phase 4
# (the Pi LCD picker uses the same labels in DATESTAMP_FORMAT_OPTIONS).
_DATESTAMP_FORMAT_LABELS: dict[DatestampFormat, str] = {
    DatestampFormat.QUARTZ_DATE: "Quartz Date",
    DatestampFormat.OLYMPUS: "Olympus",
    DatestampFormat.CONTAX: "Contax",
    DatestampFormat.MODERN: "Modern",
    DatestampFormat.LAB_PRINT: "Lab Print",
}


def _preset_options() -> list[JsonValue]:
    """Return picker options for the preset row.

    Includes all five built-in presets followed by the six user
    Custom slots. Labels mirror the values verbatim (the Mac applies
    L() to localise them).
    """

    return [
        {"value": name, "label": name}
        for name in (*BUILTIN_PRESET_NAMES, *USER_PRESET_SLOT_NAMES)
    ]


def _datestamp_format_options() -> list[JsonValue]:
    """Return picker options for the datestamp_format row.

    Enumerated programmatically by iterating ``DatestampFormat`` members
    so adding a new preset on the bridge auto-extends the Mac picker.
    """

    return [
        {"value": fmt.value, "label": _DATESTAMP_FORMAT_LABELS[fmt]}
        for fmt in DatestampFormat
    ]


def build_adjustments_schema() -> JsonObject:
    """Return the JSON-serialisable schema for the Adjustments section."""

    saturation_help = SETTING_HELP_TEXT[SettingKey.ADJUST_SATURATION]
    exposure_help = SETTING_HELP_TEXT[SettingKey.ADJUST_EXPOSURE]
    sharpness_help = SETTING_HELP_TEXT[SettingKey.ADJUST_SHARPNESS]
    hue_help = SETTING_HELP_TEXT[SettingKey.ADJUST_HUE]
    vignette_help = SETTING_HELP_TEXT[SettingKey.ADJUST_VIGNETTE]
    datestamp_help = SETTING_HELP_TEXT[SettingKey.ADJUST_DATESTAMP]

    fields: list[JsonValue] = [
        {
            "key": "preset",
            "type": "picker",
            "label": "Preset",
            "help": "Choose a preset or Custom slot",
            "options": _preset_options(),
        },
        {
            "key": "saturation",
            "type": "slider",
            "label": "Saturation",
            "help": saturation_help,
            "range": {"min": -100, "max": 100, "step": 1},
            "display": "signed_percent",
        },
        {
            "key": "exposure",
            "type": "slider",
            "label": "Exposure",
            "help": exposure_help,
            "range": {"min": -100, "max": 100, "step": 1},
            "display": "signed_percent",
        },
        {
            "key": "sharpness",
            "type": "slider",
            "label": "Sharpness",
            "help": sharpness_help,
            "range": {"min": -100, "max": 100, "step": 1},
            "display": "signed_percent",
        },
        {
            "key": "hue",
            "type": "slider",
            "label": "Hue",
            "help": hue_help,
            "range": {"min": -100, "max": 100, "step": 1},
            "display": "signed_percent",
        },
        {
            "key": "vignette",
            "type": "slider",
            "label": "Vignette",
            "help": vignette_help,
            "range": {"min": 0, "max": 100, "step": 1},
            "display": "unsigned_percent",
        },
        {
            "key": "datestamp",
            "type": "toggle",
            "label": "Datestamp",
            "help": datestamp_help,
        },
        {
            "key": "datestamp_format",
            "type": "picker",
            "label": "Datestamp format",
            "depends_on": {"field": "datestamp", "value": True},
            "options": _datestamp_format_options(),
        },
        {
            "key": "watermark",
            "type": "toggle",
            "label": "Watermark",
            # The previous help copy ("Render watermark_text …") leaked
            # the JSON key into user-facing text; rephrased to reference
            # the sibling field by its display label.
            "help": "Render the watermark text in the bottom-left corner",
        },
        {
            "key": "watermark_text",
            "type": "text",
            "label": "Watermark text",
            "depends_on": {"field": "watermark", "value": True},
        },
    ]

    return {
        "schema_version": 1,
        "section": "adjustments",
        "title": "Image adjustments",
        "fields": fields,
    }
