"""Settings menu state and option helpers."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from instantlink_bridge.ble.models import PrinterModel
from instantlink_bridge.config import BridgeConfig, FontSize, FtpReceiveMode
from instantlink_bridge.imaging.pipeline import FitMode


class SettingKey(StrEnum):
    """Settings rows in LCD order."""

    OPEN_PRINTER = "open_printer"
    OPEN_CAMERA = "open_camera"
    OPEN_NETWORK = "open_network"
    OPEN_PRINT = "open_print"
    OPEN_SYSTEM = "open_system"
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
    REFRESH_STATUS = "refresh_status"


class SettingsPage(StrEnum):
    """Settings menu pages."""

    MAIN = "main"
    PRINTER = "printer"
    CAMERA = "camera"
    NETWORK = "network"
    PRINT = "print"
    SYSTEM = "system"


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
        SettingKey.OPEN_PRINTER,
        SettingKey.OPEN_CAMERA,
        SettingKey.OPEN_NETWORK,
        SettingKey.OPEN_PRINT,
        SettingKey.OPEN_SYSTEM,
    ),
    SettingsPage.PRINTER: (
        SettingKey.PAIR_PRINTER,
        SettingKey.RESET_PRINTER_LINK,
        SettingKey.FORGET_PRINTER,
        SettingKey.PRINTER_MODEL,
        SettingKey.KEEPALIVE,
        SettingKey.SEARCH_INTERVAL,
    ),
    SettingsPage.CAMERA: (
        SettingKey.NETWORK_HOTSPOT_SSID_INFO,
        SettingKey.NETWORK_HOTSPOT_PASSWORD_INFO,
        SettingKey.FTP_HOST_INFO,
        SettingKey.FTP_USERNAME_INFO,
        SettingKey.FTP_PASSWORD_INFO,
        SettingKey.FTP_RECEIVE_MODE,
        SettingKey.CAMERA_SETUP_INFO,
    ),
    SettingsPage.NETWORK: (
        SettingKey.NETWORK_HOTSPOT_INFO,
        # Hotspot SSID + password live on the Upload FTP page where the user is
        # actively setting up the camera; deliberately not duplicated here.
        SettingKey.NETWORK_BLUETOOTH_INFO,
        SettingKey.NETWORK_WIFI_INFO,
        SettingKey.NETWORK_ETHERNET_INFO,
    ),
    SettingsPage.PRINT: (
        SettingKey.AUTO_PRINT_DELAY,
        SettingKey.IMAGE_FIT,
        SettingKey.JPEG_QUALITY,
        SettingKey.ALLOW_PRINT_WITHOUT_FILM,
    ),
    SettingsPage.SYSTEM: (
        SettingKey.SYSTEM_DEVICE_ID,
        SettingKey.SYSTEM_APP_VERSION,
        SettingKey.SYSTEM_PYTHON_VERSION,
        SettingKey.SYSTEM_BLUEZ_VERSION,
        SettingKey.SYSTEM_OS_VERSION,
        SettingKey.SYSTEM_POWER_INFO,
        SettingKey.SYSTEM_BATTERY_INFO,
        SettingKey.SYSTEM_IDLE_INFO,
        SettingKey.SYSTEM_IDLE_POWEROFF,
        SettingKey.FONT_SIZE,
        SettingKey.REFRESH_STATUS,
    ),
}

PAGE_TITLES: dict[SettingsPage, str] = {
    SettingsPage.MAIN: "Settings",
    SettingsPage.PRINTER: "Printer",
    SettingsPage.CAMERA: "Upload FTP",
    SettingsPage.NETWORK: "Network",
    SettingsPage.PRINT: "Print",
    SettingsPage.SYSTEM: "System",
}

PAGE_FOR_OPEN_KEY: dict[SettingKey, SettingsPage] = {
    SettingKey.OPEN_PRINTER: SettingsPage.PRINTER,
    SettingKey.OPEN_CAMERA: SettingsPage.CAMERA,
    SettingKey.OPEN_NETWORK: SettingsPage.NETWORK,
    SettingKey.OPEN_PRINT: SettingsPage.PRINT,
    SettingKey.OPEN_SYSTEM: SettingsPage.SYSTEM,
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
        SettingKey.REFRESH_STATUS,
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
    }
)

HANDLED_SETTING_KEYS: frozenset[SettingKey] = (
    frozenset(PAGE_FOR_OPEN_KEY) | INFO_SETTING_KEYS | ACTION_SETTING_KEYS | ADJUSTABLE_SETTING_KEYS
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
# Total scan period options. The minimum (5s) equals the active-scan window, so it scans
# continuously (0 gap); larger values insert an idle gap between scans to save power.
SEARCH_INTERVAL_OPTIONS: tuple[float, ...] = (5.0, 15.0, 30.0, 60.0)


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
    SettingKey.OPEN_PRINTER: "Printer pairing and status",
    SettingKey.OPEN_CAMERA: "Camera-side FTP credentials",
    SettingKey.OPEN_NETWORK: "Wi-Fi, Bluetooth, USB-C info",
    SettingKey.OPEN_PRINT: "Photo size and print options",
    SettingKey.OPEN_SYSTEM: "Device info and power",
    SettingKey.FTP_RECEIVE_MODE: "How camera reaches bridge",
    SettingKey.PAIR_PRINTER: "Scan and remember one Instax printer",
    SettingKey.RESET_PRINTER_LINK: "Reconnect to the saved printer",
    SettingKey.FORGET_PRINTER: "Remove the saved printer",
    SettingKey.FTP_MODE_INFO: "Path the camera actually used",
    SettingKey.FTP_HOST_INFO: "Enter as FTP server in camera",
    SettingKey.FTP_USERNAME_INFO: "Enter as FTP user in camera",
    SettingKey.FTP_PASSWORD_INFO: "Enter as FTP password in camera",
    SettingKey.CAMERA_SETUP_INFO: "Any FTP client works (camera, app, scp)",
    SettingKey.NETWORK_ETHERNET_INFO: "USB-C network to Mac (setup, updates)",
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
    SettingKey.SYSTEM_DEVICE_ID: "Unique ID; used by the Mac app",
    SettingKey.SYSTEM_APP_VERSION: "Bridge software version",
    SettingKey.SYSTEM_PYTHON_VERSION: "Python runtime version",
    SettingKey.SYSTEM_BLUEZ_VERSION: "Bluetooth stack version",
    SettingKey.SYSTEM_OS_VERSION: "Linux distribution version",
    SettingKey.SYSTEM_POWER_INFO: "Bridge battery/UPS hardware",
    SettingKey.SYSTEM_BATTERY_INFO: "Battery charge if telemetry available",
    SettingKey.SYSTEM_IDLE_INFO: "Dim and screen-off timing",
    SettingKey.SYSTEM_IDLE_POWEROFF: "Shuts down after 10 min idle",
    SettingKey.FONT_SIZE: "LCD text size",
    SettingKey.REFRESH_STATUS: "Re-check printer and FTP now",
}


def setting_help_text(key: SettingKey) -> str:
    """Return one-line KEY3 help for a settings row."""

    return SETTING_HELP_TEXT[key]


def setting_options(key: SettingKey) -> tuple[SettingOption, ...]:
    """Return explicit selectable options for an adjustable setting."""

    if key is SettingKey.FTP_RECEIVE_MODE:
        return (
            SettingOption("Bridge Wi-Fi", FtpReceiveMode.HOTSPOT),
            SettingOption("Same Wi-Fi adv", FtpReceiveMode.PEER),
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
    return ()


def selected_option_index(config: BridgeConfig, key: SettingKey) -> int:
    """Return the current option index for an adjustable setting."""

    options = setting_options(key)
    current = _setting_value(config, key)
    for index, option in enumerate(options):
        if option.value == current:
            return index
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
        FtpReceiveMode.HOTSPOT: "Bridge Wi-Fi",
        FtpReceiveMode.PEER: "Same Wi-Fi adv",
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
    return None
