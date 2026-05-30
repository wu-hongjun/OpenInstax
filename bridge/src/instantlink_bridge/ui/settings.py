"""Settings menu state and option helpers."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from instantlink_bridge.ble.models import PrinterModel
from instantlink_bridge.config import (
    BridgeConfig,
    FontSize,
    FtpReceiveMode,
    UiAppearance,
    UiLanguage,
)
from instantlink_bridge.imaging.pipeline import FitMode


class SettingKey(StrEnum):
    """Settings rows in LCD order."""

    OPEN_PRINT = "open_print"
    OPEN_NETWORK = "open_network"
    OPEN_SYSTEM = "open_system"
    # Print hub → sub-page openers (phase 1, plan 035).
    OPEN_PRINTER = "open_printer"
    OPEN_ADJUSTMENTS = "open_adjustments"
    OPEN_TRANSFORM = "open_transform"
    OPEN_AUTO_PRINT = "open_auto_print"
    # Adjustments sub-page placeholder (phase 1 only; kept for back-compat but no
    # longer surfaced in any page — replaced by the four real rows below).
    ADJUSTMENTS_COMING_SOON = "adjustments_coming_soon"
    # Adjustments sub-page rows (plan 035 phase 3).
    ADJUST_SATURATION = "adjust_saturation"
    ADJUST_EXPOSURE = "adjust_exposure"
    ADJUST_SHARPNESS = "adjust_sharpness"
    ADJUST_HUE = "adjust_hue"
    # Adjustments vignette picker (plan 035 phase 6).
    ADJUST_VIGNETTE = "adjust_vignette"
    # Adjustments sub-page overlay toggles (plan 035 phase 4).
    ADJUST_DATESTAMP = "adjust_datestamp"
    ADJUST_WATERMARK = "adjust_watermark"
    # Adjustments sub-page preset picker + save action (plan 035 phase 5).
    ADJUST_PRESET = "adjust_preset"
    ADJUST_SAVE_CUSTOM = "adjust_save_custom"
    FTP_RECEIVE_MODE = "ftp_receive_mode"
    PAIR_PRINTER = "pair_printer"
    FTP_MODE_INFO = "ftp_mode_info"
    FTP_HOST_INFO = "ftp_host_info"
    FTP_USERNAME_INFO = "ftp_username_info"
    FTP_PASSWORD_INFO = "ftp_password_info"
    CAMERA_SETUP_INFO = "camera_setup_info"
    NETWORK_ETHERNET_INFO = "network_ethernet_info"
    NETWORK_WIFI_INFO = "network_wifi_info"
    NETWORK_HOTSPOT_INFO = "network_hotspot_info"
    NETWORK_HOTSPOT_SSID_INFO = "network_hotspot_ssid_info"
    NETWORK_HOTSPOT_PASSWORD_INFO = "network_hotspot_password_info"
    NETWORK_BLUETOOTH_INFO = "network_bluetooth_info"
    PRINTER_MODEL = "printer_model"
    IMAGE_FIT = "image_fit"
    JPEG_QUALITY = "jpeg_quality"
    AUTO_PRINT_DELAY = "auto_print_delay"
    ALLOW_PRINT_WITHOUT_FILM = "allow_print_without_film"
    KEEPALIVE = "keepalive"
    SEARCH_INTERVAL = "search_interval"
    RESET_PRINTER_LINK = "reset_printer_link"
    FORGET_PRINTER = "forget_printer"
    PRINTER_SERIAL_INFO = "printer_serial_info"
    FORGET_AND_REPAIR = "forget_and_repair"
    NETWORK_DIAGNOSTICS_HEADER = "network_diagnostics_header"
    PRINT_ADVANCED_HEADER = "print_advanced_header"
    SYSTEM_PERSONALISATION_HEADER = "system_personalisation_header"
    OPEN_ABOUT = "open_about"
    SYSTEM_DEVICE_ID = "system_device_id"
    SYSTEM_APP_VERSION = "system_app_version"
    SYSTEM_PYTHON_VERSION = "system_python_version"
    SYSTEM_BLUEZ_VERSION = "system_bluez_version"
    SYSTEM_OS_VERSION = "system_os_version"
    SYSTEM_POWER_INFO = "system_power_info"
    SYSTEM_BATTERY_INFO = "system_battery_info"
    SYSTEM_IDLE_INFO = "system_idle_info"
    SYSTEM_IDLE_POWEROFF = "system_idle_poweroff"
    FONT_SIZE = "font_size"
    LANGUAGE = "language"
    APPEARANCE = "appearance"
    REFRESH_STATUS = "refresh_status"
    RESET_CREDENTIALS = "reset_credentials"


class SettingsPage(StrEnum):
    """Settings menu pages.

    PRINTER (pairing/print options) and CAMERA (FTP credentials) merged
    into PRINT and NETWORK respectively; the obsolete enum values were
    removed. MAIN now has four top-level entries: Print, Network, System,
    Accessibility.

    Phase 1 (plan 035): PRINT becomes a hub with four sub-pages —
    PRINTER, ADJUSTMENTS, TRANSFORM, AUTO_PRINT. BACK from any sub-page
    returns to PRINT (wired via SETTINGS_PARENT_PAGE), and BACK from
    PRINT returns to MAIN.
    """

    MAIN = "main"
    NETWORK = "network"
    PRINT = "print"
    SYSTEM = "system"
    ABOUT = "about"
    # Print sub-pages (plan 035 phase 1).
    PRINTER = "printer"
    ADJUSTMENTS = "adjustments"
    TRANSFORM = "transform"
    AUTO_PRINT = "auto_print"


class WifiMode(StrEnum):
    """User-selectable Wi-Fi operating modes."""

    HOME = "home"
    HOTSPOT = "hotspot"
    OFF = "off"


@dataclass(frozen=True, slots=True)
class SettingOption:
    """One explicit value choice for an adjustable setting."""

    label: str
    value: object


SETTINGS_BY_PAGE: dict[SettingsPage, tuple[SettingKey, ...]] = {
    SettingsPage.MAIN: (
        SettingKey.OPEN_PRINT,
        SettingKey.OPEN_NETWORK,
        SettingKey.OPEN_SYSTEM,
    ),
    # PRINT is now a 4-row hub (plan 035 phase 1). Each row opens a
    # dedicated sub-page; BACK from any sub-page returns here.
    SettingsPage.PRINT: (
        SettingKey.OPEN_PRINTER,
        SettingKey.OPEN_ADJUSTMENTS,
        SettingKey.OPEN_TRANSFORM,
        SettingKey.OPEN_AUTO_PRINT,
    ),
    # PRINTER: pairing actions and model selection.
    # PAIR_PRINTER is the single pair/re-pair surface: when no printer is
    # saved it shows "Pair" and starts a scan; when one is saved it shows
    # "Re-pair" and routes through the destructive Forget+scan confirm
    # (formerly the standalone FORGET_AND_REPAIR row). RESET_PRINTER_LINK
    # and FORGET_PRINTER are only useful when there's something to operate
    # on, so the controller filters them out when nothing is paired —
    # they stay listed here as the canonical paired-state row order.
    SettingsPage.PRINTER: (
        SettingKey.PRINTER_SERIAL_INFO,
        SettingKey.PAIR_PRINTER,
        SettingKey.RESET_PRINTER_LINK,
        SettingKey.FORGET_PRINTER,
        SettingKey.PRINTER_MODEL,
    ),
    # ADJUSTMENTS: preset picker (top) + four colour/tone knobs + two overlay
    # toggles + save action (bottom) (plan 035 phases 3, 4, and 5).
    # When preset != "Custom", the four colour rows are read-only displays;
    # the controller branches on config.adjustments.preset in
    # _settings_row_for_key and _activate_setting.
    SettingsPage.ADJUSTMENTS: (
        SettingKey.ADJUST_PRESET,
        SettingKey.ADJUST_SATURATION,
        SettingKey.ADJUST_EXPOSURE,
        SettingKey.ADJUST_SHARPNESS,
        SettingKey.ADJUST_HUE,
        SettingKey.ADJUST_VIGNETTE,
        SettingKey.ADJUST_DATESTAMP,
        SettingKey.ADJUST_WATERMARK,
        SettingKey.ADJUST_SAVE_CUSTOM,
    ),
    # TRANSFORM: image-fit mode and JPEG encode quality.
    SettingsPage.TRANSFORM: (
        SettingKey.IMAGE_FIT,
        SettingKey.JPEG_QUALITY,
    ),
    # AUTO_PRINT: workflow behaviour and BLE polling knobs.
    # PRINT_ADVANCED_HEADER separates the power-user polling knobs
    # (Keepalive, Search rate) from the common workflow options so they
    # read as developer knobs rather than everyday settings (plan 034
    # item 18, Option B; kept here under the Auto print sub-page).
    SettingsPage.AUTO_PRINT: (
        SettingKey.AUTO_PRINT_DELAY,
        SettingKey.ALLOW_PRINT_WITHOUT_FILM,
        SettingKey.PRINT_ADVANCED_HEADER,
        SettingKey.KEEPALIVE,
        SettingKey.SEARCH_INTERVAL,
    ),
    # NETWORK subsumes the old Connect (camera FTP) page. Camera-setup block
    # (Wi-Fi Mode → FTP PIN) comes first — these are the values the user
    # literally types into the Sony a7C II FTP settings. The
    # NETWORK_DIAGNOSTICS_HEADER row acts as a visual separator before the
    # read-only diagnostic rows (Bluetooth, Same Wi-Fi adv, USB IP) so they
    # read as status info, not credentials to enter (plan 034 item 9).
    # RESET_CREDENTIALS is last — destructive escape hatch.
    SettingsPage.NETWORK: (
        SettingKey.FTP_RECEIVE_MODE,
        SettingKey.NETWORK_HOTSPOT_SSID_INFO,
        SettingKey.NETWORK_HOTSPOT_PASSWORD_INFO,
        SettingKey.FTP_HOST_INFO,
        SettingKey.FTP_USERNAME_INFO,
        SettingKey.FTP_PASSWORD_INFO,
        SettingKey.NETWORK_DIAGNOSTICS_HEADER,
        SettingKey.NETWORK_BLUETOOTH_INFO,
        SettingKey.NETWORK_WIFI_INFO,
        SettingKey.NETWORK_ETHERNET_INFO,
        SettingKey.RESET_CREDENTIALS,
    ),
    # SYSTEM holds operational rows AND personalisation knobs after the
    # Accessibility page was folded in (3 rows on a standalone page was
    # thin justification for its own MAIN slot). Order: device-state
    # info → power toggles → manual refresh → personalisation →
    # versions/about behind a final chevron.
    SettingsPage.SYSTEM: (
        SettingKey.SYSTEM_BATTERY_INFO,
        SettingKey.SYSTEM_IDLE_INFO,
        SettingKey.SYSTEM_IDLE_POWEROFF,
        SettingKey.REFRESH_STATUS,
        SettingKey.SYSTEM_PERSONALISATION_HEADER,
        SettingKey.APPEARANCE,
        SettingKey.FONT_SIZE,
        SettingKey.LANGUAGE,
        SettingKey.OPEN_ABOUT,
    ),
    SettingsPage.ABOUT: (
        SettingKey.SYSTEM_DEVICE_ID,
        SettingKey.SYSTEM_APP_VERSION,
        SettingKey.SYSTEM_PYTHON_VERSION,
        SettingKey.SYSTEM_BLUEZ_VERSION,
        SettingKey.SYSTEM_OS_VERSION,
    ),
}

PAGE_TITLES: dict[SettingsPage, str] = {
    SettingsPage.MAIN: "Settings",
    SettingsPage.NETWORK: "Network",
    SettingsPage.PRINT: "Print",
    SettingsPage.SYSTEM: "System",
    SettingsPage.ABOUT: "About",
    # Print sub-page titles (plan 035 phase 1).
    SettingsPage.PRINTER: "Printer",
    SettingsPage.ADJUSTMENTS: "Adjustments",
    SettingsPage.TRANSFORM: "Transform",
    SettingsPage.AUTO_PRINT: "Auto print",
}

PAGE_FOR_OPEN_KEY: dict[SettingKey, SettingsPage] = {
    SettingKey.OPEN_NETWORK: SettingsPage.NETWORK,
    SettingKey.OPEN_PRINT: SettingsPage.PRINT,
    SettingKey.OPEN_SYSTEM: SettingsPage.SYSTEM,
    SettingKey.OPEN_ABOUT: SettingsPage.ABOUT,
    # Print hub → sub-page openers (plan 035 phase 1).
    SettingKey.OPEN_PRINTER: SettingsPage.PRINTER,
    SettingKey.OPEN_ADJUSTMENTS: SettingsPage.ADJUSTMENTS,
    SettingKey.OPEN_TRANSFORM: SettingsPage.TRANSFORM,
    SettingKey.OPEN_AUTO_PRINT: SettingsPage.AUTO_PRINT,
}

# Parent for each sub-page when the user presses BACK/LEFT. Only pages nested
# below a non-MAIN parent need an explicit entry; everything else defaults to
# MAIN in the controller back-nav branch.
SETTINGS_PARENT_PAGE: dict[SettingsPage, SettingsPage] = {
    SettingsPage.ABOUT: SettingsPage.SYSTEM,
    # Print sub-pages all return to the Print hub (plan 035 phase 1).
    SettingsPage.PRINTER: SettingsPage.PRINT,
    SettingsPage.ADJUSTMENTS: SettingsPage.PRINT,
    SettingsPage.TRANSFORM: SettingsPage.PRINT,
    SettingsPage.AUTO_PRINT: SettingsPage.PRINT,
}

INFO_SETTING_KEYS: frozenset[SettingKey] = frozenset(
    {
        SettingKey.FTP_MODE_INFO,
        SettingKey.FTP_HOST_INFO,
        SettingKey.FTP_USERNAME_INFO,
        SettingKey.FTP_PASSWORD_INFO,
        SettingKey.CAMERA_SETUP_INFO,
        SettingKey.NETWORK_ETHERNET_INFO,
        SettingKey.NETWORK_WIFI_INFO,
        SettingKey.NETWORK_HOTSPOT_INFO,
        SettingKey.NETWORK_HOTSPOT_SSID_INFO,
        SettingKey.NETWORK_HOTSPOT_PASSWORD_INFO,
        SettingKey.NETWORK_BLUETOOTH_INFO,
        SettingKey.NETWORK_DIAGNOSTICS_HEADER,
        SettingKey.PRINT_ADVANCED_HEADER,
        SettingKey.SYSTEM_PERSONALISATION_HEADER,
        SettingKey.PRINTER_SERIAL_INFO,
        SettingKey.SYSTEM_DEVICE_ID,
        SettingKey.SYSTEM_APP_VERSION,
        SettingKey.SYSTEM_PYTHON_VERSION,
        SettingKey.SYSTEM_BLUEZ_VERSION,
        SettingKey.SYSTEM_OS_VERSION,
        SettingKey.SYSTEM_POWER_INFO,
        SettingKey.SYSTEM_BATTERY_INFO,
        SettingKey.SYSTEM_IDLE_INFO,
    }
)

ACTION_SETTING_KEYS: frozenset[SettingKey] = frozenset(
    {
        SettingKey.PAIR_PRINTER,
        SettingKey.RESET_PRINTER_LINK,
        SettingKey.FORGET_PRINTER,
        SettingKey.FORGET_AND_REPAIR,
        SettingKey.REFRESH_STATUS,
        SettingKey.RESET_CREDENTIALS,
        # Preset save action (plan 035 phase 5).
        SettingKey.ADJUST_SAVE_CUSTOM,
    }
)

ADJUSTABLE_SETTING_KEYS: frozenset[SettingKey] = frozenset(
    {
        SettingKey.FTP_RECEIVE_MODE,
        SettingKey.PRINTER_MODEL,
        SettingKey.IMAGE_FIT,
        SettingKey.JPEG_QUALITY,
        SettingKey.AUTO_PRINT_DELAY,
        SettingKey.ALLOW_PRINT_WITHOUT_FILM,
        SettingKey.KEEPALIVE,
        SettingKey.SEARCH_INTERVAL,
        SettingKey.SYSTEM_IDLE_POWEROFF,
        SettingKey.FONT_SIZE,
        SettingKey.LANGUAGE,
        SettingKey.APPEARANCE,
        # Adjustments sub-page pickers (plan 035 phase 3).
        SettingKey.ADJUST_SATURATION,
        SettingKey.ADJUST_EXPOSURE,
        SettingKey.ADJUST_SHARPNESS,
        SettingKey.ADJUST_HUE,
        # Vignette picker (plan 035 phase 6).
        SettingKey.ADJUST_VIGNETTE,
        # Adjustments overlay toggles (plan 035 phase 4).
        SettingKey.ADJUST_DATESTAMP,
        SettingKey.ADJUST_WATERMARK,
        # Preset picker (plan 035 phase 5).
        SettingKey.ADJUST_PRESET,
    }
)

HANDLED_SETTING_KEYS: frozenset[SettingKey] = (
    frozenset(PAGE_FOR_OPEN_KEY) | INFO_SETTING_KEYS | ACTION_SETTING_KEYS | ADJUSTABLE_SETTING_KEYS
)

# Stable built-in preset names for the picker.  User custom slots are
# appended dynamically by the controller once user presets are loaded.
# Phase 5 (plan 036): "Custom" sentinel removed; "B&W" renamed to "Black & white".
BUILTIN_PRESET_NAMES: tuple[str, ...] = (
    "Default",
    "Vivid",
    "Soft",
    "Black & white",
    "Instax Film",
)
USER_PRESET_SLOT_NAMES: tuple[str, ...] = (
    "Custom1",
    "Custom2",
    "Custom3",
    "Custom4",
    "Custom5",
    "Custom6",
)

# Five-position discrete picker for all four colour adjustment axes.
# Labels use the Unicode minus sign (U+2212) for negative values and a
# leading "+" for positive values; zero has no sign.
ADJUSTMENT_OPTIONS: tuple[SettingOption, ...] = (
    SettingOption("−100", -100),
    SettingOption("−50", -50),
    SettingOption("0", 0),
    SettingOption("+50", 50),
    SettingOption("+100", 100),
)

# Five-position one-sided discrete picker for vignette.
# Values are always non-negative (0…100); no sign prefix on labels.
VIGNETTE_OPTIONS: tuple[SettingOption, ...] = (
    SettingOption("0", 0),
    SettingOption("25", 25),
    SettingOption("50", 50),
    SettingOption("75", 75),
    SettingOption("100", 100),
)

MODEL_OPTIONS: tuple[PrinterModel | None, ...] = (
    None,
    PrinterModel.MINI,
    PrinterModel.MINI_LINK3,
    PrinterModel.SQUARE,
    PrinterModel.WIDE,
)
FIT_OPTIONS: tuple[FitMode, ...] = (
    FitMode.AUTO,
    FitMode.CROP,
    FitMode.CONTAIN,
    FitMode.STRETCH,
)
QUALITY_OPTIONS: tuple[int, ...] = (70, 75, 80, 85, 90, 95, 100)
AUTO_PRINT_DELAY_OPTIONS: tuple[float | None, ...] = (None, 0.0, 5.0)
BOOL_OPTIONS: tuple[bool, ...] = (False, True)
KEEPALIVE_OPTIONS: tuple[float, ...] = (5.0, 10.0, 15.0, 30.0)
FONT_SIZE_OPTIONS: tuple[FontSize, ...] = (FontSize.SMALL, FontSize.MEDIUM, FontSize.LARGE)
LANGUAGE_OPTIONS: tuple[UiLanguage, ...] = (UiLanguage.EN, UiLanguage.ZH_HANS)
APPEARANCE_OPTIONS: tuple[UiAppearance, ...] = (
    UiAppearance.LIGHT,
    UiAppearance.DARK,
    UiAppearance.AUTO,
)
# Total scan period options. The minimum (5s) equals the active-scan window, so it scans
# continuously (0 gap); larger values insert an idle gap between scans to save power.
SEARCH_INTERVAL_OPTIONS: tuple[float, ...] = (5.0, 15.0, 30.0, 60.0)


def preset_options(user_preset_names: tuple[str, ...] = ()) -> tuple[SettingOption, ...]:
    """Return picker options for the preset row.

    Built-ins come first, then any loaded user custom names.  Phase 5
    (plan 036): the ``"Custom"`` sentinel has been removed — every preset
    is now a starting template and all sliders are always editable.
    """

    names = (*BUILTIN_PRESET_NAMES, *user_preset_names)
    return tuple(SettingOption(name, name) for name in names)


def setting_action_hint(key: SettingKey) -> str:
    """Return the short joystick hint for a settings row."""

    if key in PAGE_FOR_OPEN_KEY:
        return "Right/KEY1 open"
    if key in INFO_SETTING_KEYS:
        return "Right/KEY1 info"
    if key in ACTION_SETTING_KEYS:
        return "Right/KEY1 run"
    if key in ADJUSTABLE_SETTING_KEYS:
        return "Right/KEY1 choose"
    return "Not implemented"


SETTING_HELP_TEXT: dict[SettingKey, str] = {
    SettingKey.OPEN_NETWORK: "Wi-Fi, FTP credentials, Bluetooth, USB",
    SettingKey.OPEN_PRINT: "Pairing and photo/print options",
    SettingKey.OPEN_SYSTEM: "Bridge health, personalisation, updates",
    SettingKey.OPEN_ABOUT: "Versions and device identity",
    # Print hub → sub-page opener help strings (plan 035 phase 1).
    SettingKey.OPEN_PRINTER: "Pairing and printer model",
    SettingKey.OPEN_ADJUSTMENTS: "Colour and overlay adjustments",
    SettingKey.OPEN_TRANSFORM: "Fit-to-film and JPEG quality",
    SettingKey.OPEN_AUTO_PRINT: "Auto-print delay and connection knobs",
    # Adjustments placeholder help (phase 1 only — no longer surfaced in any page).
    SettingKey.ADJUSTMENTS_COMING_SOON: "Saturation, exposure, sharpness coming in v2",
    # Adjustments sub-page rows (plan 035 phase 3).
    SettingKey.ADJUST_SATURATION: "Colour intensity. Negative dulls, positive boosts",
    SettingKey.ADJUST_EXPOSURE: "Brightness in EV stops. ±100 = ±1 EV",
    SettingKey.ADJUST_SHARPNESS: "Edge contrast. Negative softens, positive crisps",
    SettingKey.ADJUST_HUE: "Tint. Left toward orange, right toward blue.",
    # Vignette picker (plan 035 phase 6).
    SettingKey.ADJUST_VIGNETTE: "Darken the corners to simulate Instax film",
    # Adjustments overlay toggles (plan 035 phase 4).
    SettingKey.ADJUST_DATESTAMP: "Stamp the photo's date in the bottom-right corner",
    SettingKey.ADJUST_WATERMARK: "Stamp a short label in the top-right corner",
    # Preset picker and save action (plan 035 phase 5; updated plan 036 phase 5).
    SettingKey.ADJUST_PRESET: "Choose a look, or tweak the sliders below",
    SettingKey.ADJUST_SAVE_CUSTOM: "Store current values as a custom preset",
    SettingKey.FTP_RECEIVE_MODE: "Hotspot: bridge AP. Client: join existing.",
    SettingKey.PAIR_PRINTER: "Pair an Instax printer, or re-pair to swap",
    SettingKey.RESET_PRINTER_LINK: "Reconnect to the saved printer",
    SettingKey.FORGET_PRINTER: "Forget the saved printer",
    SettingKey.PRINTER_SERIAL_INFO: "Serial of the saved Instax printer",
    # FORGET_AND_REPAIR's help text is retained here for callers that look
    # it up via SETTING_HELP_TEXT (kept for back-compat in the dynamic
    # _settings_row_help branches); the row itself is no longer surfaced.
    SettingKey.FORGET_AND_REPAIR: "Wipe pairing, then start a fresh scan",
    SettingKey.FTP_MODE_INFO: "Path the camera actually used",
    SettingKey.FTP_HOST_INFO: "Enter as FTP server in camera",
    SettingKey.FTP_USERNAME_INFO: "Enter as FTP user in camera",
    SettingKey.FTP_PASSWORD_INFO: "PIN: enter as FTP password in camera",
    SettingKey.CAMERA_SETUP_INFO: "Any FTP client works (camera, app, scp)",
    SettingKey.NETWORK_ETHERNET_INFO: "USB network to computer (setup, updates)",
    SettingKey.NETWORK_WIFI_INFO: "Advanced: bridge on existing Wi-Fi",
    SettingKey.NETWORK_HOTSPOT_INFO: "Camera connects here for upload",
    SettingKey.NETWORK_HOTSPOT_SSID_INFO: "Bridge Wi-Fi name to join from camera",
    SettingKey.NETWORK_HOTSPOT_PASSWORD_INFO: "Bridge Wi-Fi password (8 digits)",
    SettingKey.NETWORK_BLUETOOTH_INFO: "BLE link to Instax printer",
    SettingKey.PRINTER_MODEL: "Auto detects from printer",
    SettingKey.IMAGE_FIT: "How to fit photo to film aspect",
    SettingKey.JPEG_QUALITY: "Trade-off: higher = bigger, sharper",
    SettingKey.AUTO_PRINT_DELAY: "Editable preview, then prints",
    SettingKey.ALLOW_PRINT_WITHOUT_FILM: "Test mode: skip 0/10 film check",
    SettingKey.KEEPALIVE: "Polls printer while idle",
    SettingKey.SEARCH_INTERVAL: "Scans when printer offline",
    SettingKey.SYSTEM_DEVICE_ID: "Unique ID; used by the desktop app",
    SettingKey.SYSTEM_APP_VERSION: "Bridge software version",
    SettingKey.SYSTEM_PYTHON_VERSION: "Python: language running bridge code",
    SettingKey.SYSTEM_BLUEZ_VERSION: "Bluetooth stack used for pairing",
    SettingKey.SYSTEM_OS_VERSION: "Operating system release",
    SettingKey.SYSTEM_POWER_INFO: "Bridge battery/UPS hardware (legacy)",
    SettingKey.SYSTEM_BATTERY_INFO: "Battery charge if telemetry available",
    SettingKey.SYSTEM_IDLE_INFO: "Dim and screen-off timing",
    SettingKey.SYSTEM_IDLE_POWEROFF: "Shuts down after 10 min idle",
    SettingKey.FONT_SIZE: "Screen text size",
    SettingKey.LANGUAGE: "Screen language (中文 / English)",
    SettingKey.APPEARANCE: "Auto: light 07-19, dark overnight",
    SettingKey.REFRESH_STATUS: "Re-check printer and FTP now",
    SettingKey.RESET_CREDENTIALS: "Generate new Wi-Fi & FTP credentials",
    # Separator rows — info-only, no interactive action.
    SettingKey.NETWORK_DIAGNOSTICS_HEADER: "Read-only connection diagnostics",
    SettingKey.PRINT_ADVANCED_HEADER: "Power-user polling intervals",
    SettingKey.SYSTEM_PERSONALISATION_HEADER: "Theme, text size, and language",
}


def setting_help_text(key: SettingKey) -> str:
    """Return one-line KEY3 help for a settings row."""

    return SETTING_HELP_TEXT[key]


def setting_options(key: SettingKey) -> tuple[SettingOption, ...]:
    """Return explicit selectable options for an adjustable setting."""

    if key is SettingKey.FTP_RECEIVE_MODE:
        return (
            # "Hotspot" = bridge runs its own Wi-Fi the camera joins.
            # "Client" = bridge joins your existing Wi-Fi alongside the camera.
            SettingOption("Hotspot", FtpReceiveMode.HOTSPOT),
            SettingOption("Client", FtpReceiveMode.PEER),
        )
    if key is SettingKey.PRINTER_MODEL:
        return tuple(SettingOption(model_label(value), value) for value in MODEL_OPTIONS)
    if key is SettingKey.IMAGE_FIT:
        return tuple(SettingOption(fit_label(value), value) for value in FIT_OPTIONS)
    if key is SettingKey.JPEG_QUALITY:
        return tuple(SettingOption(str(value), value) for value in QUALITY_OPTIONS)
    if key is SettingKey.AUTO_PRINT_DELAY:
        return tuple(
            SettingOption(seconds_label(value), value) for value in AUTO_PRINT_DELAY_OPTIONS
        )
    if key is SettingKey.ALLOW_PRINT_WITHOUT_FILM:
        return tuple(SettingOption(bool_label(value), value) for value in BOOL_OPTIONS)
    if key is SettingKey.KEEPALIVE:
        return tuple(SettingOption(seconds_label(value), value) for value in KEEPALIVE_OPTIONS)
    if key is SettingKey.SEARCH_INTERVAL:
        return tuple(
            SettingOption(seconds_label(value), value) for value in SEARCH_INTERVAL_OPTIONS
        )
    if key is SettingKey.SYSTEM_IDLE_POWEROFF:
        return tuple(SettingOption(bool_label(value), value) for value in BOOL_OPTIONS)
    if key is SettingKey.FONT_SIZE:
        return tuple(SettingOption(value.value.capitalize(), value) for value in FONT_SIZE_OPTIONS)
    if key is SettingKey.LANGUAGE:
        return tuple(SettingOption(language_label(value), value) for value in LANGUAGE_OPTIONS)
    if key is SettingKey.APPEARANCE:
        return tuple(SettingOption(appearance_label(value), value) for value in APPEARANCE_OPTIONS)
    if key in {
        SettingKey.ADJUST_SATURATION,
        SettingKey.ADJUST_EXPOSURE,
        SettingKey.ADJUST_SHARPNESS,
        SettingKey.ADJUST_HUE,
    }:
        return ADJUSTMENT_OPTIONS
    if key is SettingKey.ADJUST_VIGNETTE:
        return VIGNETTE_OPTIONS
    if key is SettingKey.ADJUST_DATESTAMP:
        return tuple(SettingOption(bool_label(value), value) for value in BOOL_OPTIONS)
    if key is SettingKey.ADJUST_WATERMARK:
        return tuple(SettingOption(bool_label(value), value) for value in BOOL_OPTIONS)
    if key is SettingKey.ADJUST_PRESET:
        # Without live user-preset names the picker shows built-ins + Custom.
        # The controller passes user_preset_names when it calls setting_options
        # indirectly; direct callers see the minimal set.
        return preset_options()
    return ()


def selected_option_index(config: BridgeConfig, key: SettingKey) -> int:
    """Return the current option index for an adjustable setting.

    For integer-valued axes (saturation, exposure, sharpness, hue, vignette)
    an off-grid value (e.g. saturation=7, not in the 5-position picker) falls
    back to the nearest discrete option by absolute distance.  Ties are broken
    towards lower indices (i.e. the lower-valued option wins).
    """

    options = setting_options(key)
    current = _setting_value(config, key)
    for index, option in enumerate(options):
        if option.value == current:
            return index
    # Nearest-option fallback for integer axes with off-grid values.
    if isinstance(current, int) and options:
        best_index = 0
        best_dist = abs(current - options[0].value) if isinstance(options[0].value, int) else None
        for index, option in enumerate(options):
            if not isinstance(option.value, int):
                continue
            dist = abs(current - option.value)
            if best_dist is None or dist < best_dist:
                best_dist = dist
                best_index = index
        return best_index
    return 0


def config_with_setting_value(
    config: BridgeConfig,
    key: SettingKey,
    value: object,
) -> BridgeConfig:
    """Return config with one setting assigned to a specific option value."""

    if key is SettingKey.FTP_RECEIVE_MODE and isinstance(value, FtpReceiveMode):
        return replace(config, ftp=replace(config.ftp, mode=value))
    if key is SettingKey.PRINTER_MODEL and (isinstance(value, PrinterModel) or value is None):
        return replace(config, printer=replace(config.printer, model=value))
    if key is SettingKey.IMAGE_FIT and isinstance(value, FitMode):
        return replace(config, printer=replace(config.printer, fit=value))
    if key is SettingKey.JPEG_QUALITY and isinstance(value, int):
        return replace(config, printer=replace(config.printer, quality=value))
    if key is SettingKey.AUTO_PRINT_DELAY and (isinstance(value, float) or value is None):
        return replace(
            config,
            workflow=replace(config.workflow, auto_print_delay_s=value),
        )
    if key is SettingKey.ALLOW_PRINT_WITHOUT_FILM and isinstance(value, bool):
        return replace(
            config,
            workflow=replace(config.workflow, allow_print_without_film=value),
        )
    if key is SettingKey.KEEPALIVE and isinstance(value, float):
        return replace(config, printer=replace(config.printer, keepalive_interval_s=value))
    if key is SettingKey.SEARCH_INTERVAL and isinstance(value, float):
        return replace(config, printer=replace(config.printer, search_interval_s=value))
    if key is SettingKey.SYSTEM_IDLE_POWEROFF and isinstance(value, bool):
        return replace(config, power=replace(config.power, idle_poweroff_enabled=value))
    if key is SettingKey.FONT_SIZE and isinstance(value, FontSize):
        return replace(config, ui=replace(config.ui, font_size=value))
    if key is SettingKey.LANGUAGE and isinstance(value, UiLanguage):
        return replace(config, ui=replace(config.ui, language=value))
    if key is SettingKey.APPEARANCE and isinstance(value, UiAppearance):
        return replace(config, ui=replace(config.ui, appearance=value))
    if key is SettingKey.ADJUST_SATURATION and isinstance(value, int):
        return replace(config, adjustments=replace(config.adjustments, saturation=value))
    if key is SettingKey.ADJUST_EXPOSURE and isinstance(value, int):
        return replace(config, adjustments=replace(config.adjustments, exposure=value))
    if key is SettingKey.ADJUST_SHARPNESS and isinstance(value, int):
        return replace(config, adjustments=replace(config.adjustments, sharpness=value))
    if key is SettingKey.ADJUST_HUE and isinstance(value, int):
        return replace(config, adjustments=replace(config.adjustments, hue=value))
    if key is SettingKey.ADJUST_VIGNETTE and isinstance(value, int):
        return replace(config, adjustments=replace(config.adjustments, vignette=value))
    if key is SettingKey.ADJUST_DATESTAMP and isinstance(value, bool):
        return replace(config, adjustments=replace(config.adjustments, datestamp=value))
    if key is SettingKey.ADJUST_WATERMARK and isinstance(value, bool):
        return replace(config, adjustments=replace(config.adjustments, watermark=value))
    if key is SettingKey.ADJUST_PRESET and isinstance(value, str):
        return replace(config, adjustments=replace(config.adjustments, preset=value))
    return config


def model_label(model: PrinterModel | None) -> str:
    """Return compact LCD label for a configured printer model."""

    if model is None:
        return "Auto"
    labels = {
        PrinterModel.MINI: "Mini",
        PrinterModel.MINI_LINK3: "Mini 3",
        PrinterModel.SQUARE: "Square",
        PrinterModel.WIDE: "Wide",
    }
    return labels[model]


def fit_label(fit: FitMode) -> str:
    """Return compact LCD label for image fit mode."""

    labels = {
        FitMode.AUTO: "Auto",
        FitMode.CROP: "Crop",
        FitMode.CONTAIN: "Contain",
        FitMode.STRETCH: "Stretch",
    }
    return labels[fit]


def ftp_receive_mode_label(mode: FtpReceiveMode) -> str:
    """Return compact LCD label for the configured FTP receive mode."""

    labels = {
        FtpReceiveMode.AUTO: "Advanced",
        FtpReceiveMode.WIRED: "USB IP",
        FtpReceiveMode.HOTSPOT: "Hotspot",
        FtpReceiveMode.PEER: "Client",
    }
    return labels[mode]


def seconds_label(value: float | None) -> str:
    """Return compact LCD label for a second value."""

    if value is None:
        return "Off"
    return f"{value:g}s"


def bool_label(value: bool) -> str:
    """Return compact LCD label for a boolean setting."""

    return "On" if value else "Off"


def _setting_value(config: BridgeConfig, key: SettingKey) -> object:
    if key is SettingKey.FTP_RECEIVE_MODE:
        return config.ftp.mode
    if key is SettingKey.PRINTER_MODEL:
        return config.printer.model
    if key is SettingKey.IMAGE_FIT:
        return config.printer.fit
    if key is SettingKey.JPEG_QUALITY:
        return config.printer.quality
    if key is SettingKey.AUTO_PRINT_DELAY:
        return config.workflow.auto_print_delay_s
    if key is SettingKey.ALLOW_PRINT_WITHOUT_FILM:
        return config.workflow.allow_print_without_film
    if key is SettingKey.KEEPALIVE:
        return config.printer.keepalive_interval_s
    if key is SettingKey.SEARCH_INTERVAL:
        return config.printer.search_interval_s
    if key is SettingKey.SYSTEM_IDLE_POWEROFF:
        return config.power.idle_poweroff_enabled
    if key is SettingKey.FONT_SIZE:
        return config.ui.font_size
    if key is SettingKey.LANGUAGE:
        return config.ui.language
    if key is SettingKey.APPEARANCE:
        return config.ui.appearance
    if key is SettingKey.ADJUST_SATURATION:
        return config.adjustments.saturation
    if key is SettingKey.ADJUST_EXPOSURE:
        return config.adjustments.exposure
    if key is SettingKey.ADJUST_SHARPNESS:
        return config.adjustments.sharpness
    if key is SettingKey.ADJUST_HUE:
        return config.adjustments.hue
    if key is SettingKey.ADJUST_VIGNETTE:
        return config.adjustments.vignette
    if key is SettingKey.ADJUST_DATESTAMP:
        return config.adjustments.datestamp
    if key is SettingKey.ADJUST_WATERMARK:
        return config.adjustments.watermark
    if key is SettingKey.ADJUST_PRESET:
        return config.adjustments.preset
    return None


def format_int_with_sign(value: int) -> str:
    """Format an integer with an explicit sign for non-zero values.

    Zero is returned as ``"0"`` (no sign). Positive values are prefixed
    with ``"+"``; negative values use the Unicode minus sign (U+2212) to
    match the picker labels in ``ADJUSTMENT_OPTIONS``.
    """
    if value == 0:
        return "0"
    if value > 0:
        return f"+{value}"
    # Use the Unicode minus sign (U+2212) to match the picker labels.
    return f"−{abs(value)}"


def appearance_label(appearance: UiAppearance) -> str:
    """Return the picker label for an appearance.

    Labels mirror the iOS Settings naming so the choice feels familiar
    even on the LCD. Keep them short — the picker row is ~140 px wide.
    """

    labels = {
        UiAppearance.LIGHT: "Light",
        UiAppearance.DARK: "Dark",
        # "Auto" runs the wall-clock schedule defined in ui.theme — light
        # during the day, dark overnight. The user can't drive a "follow
        # the host OS" mode on a headless Pi, so this replaces the old
        # SYSTEM option that effectively did nothing.
        UiAppearance.AUTO: "Auto",
    }
    return labels[appearance]


def language_label(language: UiLanguage) -> str:
    """Return the picker label for a language.

    The label is written in the *target* language so the user sees their
    own native spelling — "English" vs "中文" — regardless of which UI
    language is currently active.
    """

    labels = {
        UiLanguage.EN: "English",
        UiLanguage.ZH_HANS: "中文",
    }
    return labels[language]
