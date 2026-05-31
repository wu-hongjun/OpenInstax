"""Tests for the bridge UI i18n translation table."""

from __future__ import annotations

import pytest

from instantlink_bridge.ui.i18n import Language, t, translatable_strings


def test_english_passthrough_returns_source_unchanged() -> None:
    """English is the source language — no translation lookup."""

    assert t("Ready", Language.EN) == "Ready"
    assert t("Anything goes here", Language.EN) == "Anything goes here"


def test_chinese_translates_registered_strings() -> None:
    assert t("Ready", Language.ZH_HANS) == "就绪"
    assert t("Connected", Language.ZH_HANS) == "已连接"
    assert t("Searching", Language.ZH_HANS) == "搜索中"


def test_missing_translation_falls_back_to_english_source() -> None:
    """A key not present in the target language returns the source string
    so missing translations degrade gracefully (text stays readable)."""

    assert t("not-yet-translated", Language.ZH_HANS) == "not-yet-translated"


def test_string_language_tag_is_parsed() -> None:
    """The runtime carries language as a snapshot str (BCP 47); t() accepts
    both the enum and the bare tag so callers don't have to convert."""

    assert t("Ready", "zh-Hans") == "就绪"
    assert t("Ready", "en") == "Ready"
    # Unknown tag → fall back to source.
    assert t("Ready", "xx-YY") == "Ready"


def test_translatable_strings_exposes_full_target_map() -> None:
    table = translatable_strings(Language.ZH_HANS)

    # Spot-check a representative slice — the full table is owned by the
    # i18n module and shouldn't be coupled to a hard count here.
    assert table["Ready"] == "就绪"
    assert table["Settings"] == "设置"
    assert "Connected" in table
    assert "KEY1 Setting" in table


# ---------------------------------------------------------------------------
# Plan 040: iOS-style confirmation dialog strings translate to zh-Hans
# ---------------------------------------------------------------------------


# zh-Hans intentionally uses the full-width question mark — that is the
# Apple iOS convention this i18n module mirrors. Each parametrised entry
# carries an RUF001 inline suppression so ruff's ambiguous-glyph check
# doesn't trip on the deliberate localisation choice.
@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("Forget printer?", "忘记打印机？"),  # noqa: RUF001
        ("Reset credentials?", "还原凭据？"),  # noqa: RUF001
        ("Reset connection?", "还原连接？"),  # noqa: RUF001
        ("Re-pair printer?", "重新配对打印机？"),  # noqa: RUF001
        ("Save preset?", "存储预设？"),  # noqa: RUF001
        ("Cancel", "取消"),
        ("Reset", "还原"),
        ("Save", "存储"),
        ("Delete", "删除"),
        ("Overwrite", "覆盖"),
    ],
)
def test_zh_hans_confirmation_dialog_strings(source: str, expected: str) -> None:
    """The 7 dialog flows (plan 040) need both title + verb labels translated."""

    assert t(source, Language.ZH_HANS) == expected


# ---------------------------------------------------------------------------
# Plan 037 polish #15: datestamp format preset names — translate vs keep
# ---------------------------------------------------------------------------


def test_zh_hans_datestamp_format_modern_quartz_labprint_translated() -> None:
    """Plan 037 polish #15: descriptive English datestamp names get
    translated to zh-Hans; brand names (Olympus, Contax) stay Latin."""

    assert t("Modern", Language.ZH_HANS) == "现代"
    assert t("Quartz Date", Language.ZH_HANS) == "石英日期"
    assert t("Lab Print", Language.ZH_HANS) == "冲印店"


def test_zh_hans_olympus_contax_stay_latin() -> None:
    """Plan 037 polish #15 (regression guard): Olympus and Contax are
    real product brands and intentionally fall through untranslated, in
    line with the i18n doctrine of leaving brand identifiers in Latin.
    """

    assert t("Olympus", Language.ZH_HANS) == "Olympus"
    assert t("Contax", Language.ZH_HANS) == "Contax"


# ---------------------------------------------------------------------------
# Plan 037 polish #6: preset "edited" badge translation
# ---------------------------------------------------------------------------


def test_zh_hans_preset_edited_marker_translates() -> None:
    """Plan 037 polish #6: the "edited" badge that replaces the cryptic
    "*" marker on the Preset row translates to zh-Hans."""

    assert t("edited", Language.ZH_HANS) == "已编辑"


# ---------------------------------------------------------------------------
# Plan 037 polish #8: "Camera link" row label translation
# ---------------------------------------------------------------------------


def test_zh_hans_camera_link_label_translates() -> None:
    """Plan 037 polish #8: the renamed FTP_RECEIVE_MODE row label
    ("Camera link", was "Wi-Fi Mode") has a zh-Hans translation so the
    label reads naturally in the localised settings list."""

    assert t("Camera link", Language.ZH_HANS) == "相机链路"


# ---------------------------------------------------------------------------
# Plan 037 polish #14: Hue help string trailing-period cleanup
# ---------------------------------------------------------------------------


def test_hue_help_zh_hans_has_no_trailing_full_stop() -> None:
    """Plan 037 polish #14: the source Hue help text dropped its trailing
    period to match sibling help strings; the zh-Hans translation drops
    its corresponding full-width period."""

    translated = t("Tint. Left toward orange, right toward blue", Language.ZH_HANS)
    assert translated == "色调。左偏橙色，右偏蓝色"  # noqa: RUF001
    assert not translated.endswith("。")
