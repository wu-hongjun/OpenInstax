"""Configuration loading for InstantLink Bridge."""

from __future__ import annotations

import json
import os
import tempfile
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from ipaddress import IPv4Address, IPv4Network, ip_address
from itertools import pairwise
from math import isfinite
from pathlib import Path

from instantlink_bridge.ble.models import PrinterModel, parse_printer_model
from instantlink_bridge.imaging.pipeline import FitMode, parse_fit_mode

DEFAULT_CONFIG_PATH = Path("/etc/InstantLinkBridge/config.toml")
FTP_SUBNET_PREFIX_LEN = 24


class FtpReceiveMode(StrEnum):
    """Configured FTP receive policy.

    ``AUTO`` and ``WIRED`` are retained for legacy config parsing only. USB gadget clients are
    admin/diagnostics traffic and are not accepted for camera print uploads in v1.
    """

    AUTO = "auto"
    WIRED = "wired"
    HOTSPOT = "hotspot"
    PEER = "peer"


class FtpSourceKind(StrEnum):
    """Classified FTP client source network."""

    INVALID = "invalid"
    LINK_LOCAL = "link_local"
    USB = "usb"
    HOTSPOT = "hotspot"
    PEER = "peer"


class PowerBackend(StrEnum):
    """Bridge power hardware backend."""

    X306 = "x306"
    PISUGAR = "pisugar"
    NONE = "none"


class UiSurface(StrEnum):
    """Bridge UI display surface."""

    LCD = "lcd"
    HEADLESS = "headless"


class FontSize(StrEnum):
    """Global LCD font size."""

    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"


class UiAppearance(StrEnum):
    """User-selectable LCD appearance (light / dark / auto).

    AUTO switches between LIGHT and DARK based on local clock time: the
    bridge runs headless on a Raspberry Pi with no ambient sensor and no
    user-visible host OS to inherit a theme from, so a wall-clock schedule
    is the only practical "automatic" we can offer. The previous SYSTEM
    value was effectively a no-op (it fell through to LIGHT) and is
    retained here as a parse-time alias for backwards compatibility with
    existing configs on deployed bridges.
    """

    LIGHT = "light"
    DARK = "dark"
    AUTO = "auto"


class UiLanguage(StrEnum):
    """User-selectable LCD languages (BCP 47 tags).

    Kept separate from the runtime i18n module's Language enum so config
    doesn't import the i18n table; the two are kept value-equivalent and
    converted at the boundary.
    """

    EN = "en"
    ZH_HANS = "zh-Hans"


class StatusSinkKind(StrEnum):
    """Where the unified status signal is published.

    ``lcd`` is the default for the LCD-SKU and means the status indicator is
    drawn into the top bar; no separate sink is wired. ``gpio`` is reserved
    for the future headless-SKU (Plan 033 Phase 5); today it logs transitions.
    ``null`` disables the side-channel entirely (LCD bar still renders).
    """

    LCD = "lcd"
    GPIO = "gpio"
    NULL = "null"


@dataclass(frozen=True, slots=True)
class FtpSourceDecision:
    """Result of applying FTP receive-mode source policy."""

    allowed: bool
    source: FtpSourceKind
    reason: str


@dataclass(frozen=True, slots=True)
class FtpConfig:
    """FTP receive configuration."""

    mode: FtpReceiveMode = FtpReceiveMode.HOTSPOT
    bind_host: str = "0.0.0.0"
    host: str = "192.168.7.1"
    hotspot_host: str = "192.168.8.1"
    port: int = 21
    username: str = "ib"
    password: str = "change-me"
    incoming_dir: Path = Path("/var/lib/InstantLinkBridge/incoming")
    preferred_wifi_host: str | None = None


def ftp_config_source_decision(
    config: FtpConfig,
    remote_ip: str,
    *,
    active_peer_hosts: Iterable[str] | None = None,
) -> FtpSourceDecision:
    """Return whether a remote FTP source is allowed by a full FTP config."""

    if active_peer_hosts is None:
        peer_hosts: Iterable[str] = (
            (config.preferred_wifi_host,) if config.preferred_wifi_host is not None else ()
        )
    else:
        peer_hosts = active_peer_hosts
    return ftp_source_decision(
        config.mode,
        remote_ip,
        usb_host=config.host,
        hotspot_host=config.hotspot_host,
        active_peer_hosts=peer_hosts,
    )


def ftp_source_decision(
    mode: FtpReceiveMode,
    remote_ip: str,
    *,
    usb_host: str,
    hotspot_host: str,
    active_peer_hosts: Iterable[str] = (),
) -> FtpSourceDecision:
    """Return whether a remote FTP source is allowed for the selected mode."""

    try:
        source_ip = IPv4Address(remote_ip)
    except ValueError:
        return FtpSourceDecision(False, FtpSourceKind.INVALID, "remote_ip_not_ipv4")

    if source_ip.is_link_local:
        return FtpSourceDecision(False, FtpSourceKind.LINK_LOCAL, "link_local_rejected")
    if source_ip.is_loopback or source_ip.is_multicast or source_ip.is_unspecified:
        return FtpSourceDecision(False, FtpSourceKind.INVALID, "non_client_ipv4_rejected")

    try:
        source_kind = _classify_ftp_source(source_ip, usb_host, hotspot_host)
    except ValueError:
        return FtpSourceDecision(False, FtpSourceKind.INVALID, "configured_subnet_invalid")

    if source_kind is FtpSourceKind.PEER:
        try:
            peer_networks = active_peer_ipv4_networks(
                active_peer_hosts,
                usb_host=usb_host,
                hotspot_host=hotspot_host,
            )
        except ValueError:
            return FtpSourceDecision(False, FtpSourceKind.INVALID, "configured_subnet_invalid")
        if not _ipv4_in_any_network(source_ip, peer_networks):
            return FtpSourceDecision(
                False,
                source_kind,
                "peer_source_outside_active_networks",
            )

    if source_kind is FtpSourceKind.USB:
        return FtpSourceDecision(False, source_kind, "usb_source_admin_only")
    if mode is FtpReceiveMode.AUTO:
        return FtpSourceDecision(True, source_kind, "allowed")
    if mode is FtpReceiveMode.WIRED:
        return FtpSourceDecision(False, source_kind, "wired_mode_disabled_for_v1")
    if mode is FtpReceiveMode.HOTSPOT and source_kind is FtpSourceKind.HOTSPOT:
        return FtpSourceDecision(True, source_kind, "allowed")
    if mode is FtpReceiveMode.PEER and source_kind is FtpSourceKind.PEER:
        return FtpSourceDecision(True, source_kind, "allowed")
    return FtpSourceDecision(
        False,
        source_kind,
        f"{mode.value}_mode_rejects_{source_kind.value}_source",
    )


def _classify_ftp_source(
    source_ip: IPv4Address,
    usb_host: str,
    hotspot_host: str,
) -> FtpSourceKind:
    if source_ip in ipv4_24_network(usb_host):
        return FtpSourceKind.USB
    if source_ip in ipv4_24_network(hotspot_host):
        return FtpSourceKind.HOTSPOT
    return FtpSourceKind.PEER


def active_peer_ipv4_networks(
    active_peer_hosts: Iterable[str],
    *,
    usb_host: str,
    hotspot_host: str,
) -> tuple[IPv4Network, ...]:
    """Return active peer /24 networks, excluding reserved USB and hotspot subnets."""

    usb_network = ipv4_24_network(usb_host)
    hotspot_network = ipv4_24_network(hotspot_host)
    networks: list[IPv4Network] = []
    for host in active_peer_hosts:
        host_ip = IPv4Address(host)
        if _is_non_peer_host(host_ip):
            continue
        network = ipv4_24_network(str(host_ip))
        if network in {usb_network, hotspot_network} or network in networks:
            continue
        networks.append(network)
    return tuple(networks)


def _ipv4_in_any_network(address: IPv4Address, networks: Iterable[IPv4Network]) -> bool:
    return any(address in network for network in networks)


def _is_non_peer_host(address: IPv4Address) -> bool:
    return (
        address.is_link_local
        or address.is_loopback
        or address.is_multicast
        or address.is_unspecified
    )


@dataclass(frozen=True, slots=True)
class PrinterConfig:
    """Printer selection and image-prep configuration."""

    model: PrinterModel | None = None
    fit: FitMode = FitMode.AUTO
    quality: int = 100
    print_option: int = 0
    device_name: str | None = None
    keepalive_interval_s: float = 10.0
    # Total scan period, in seconds, while searching for the offline selected printer: the active
    # scan window plus any idle gap. The minimum (5s) equals the scan window, so the bridge scans
    # continuously; larger values add an idle gap to save power. User-selectable in Settings; no
    # exponential backoff, so reconnection stays prompt when the printer powers on.
    search_interval_s: float = 5.0


@dataclass(frozen=True, slots=True)
class WorkflowConfig:
    """Image receive and print workflow configuration."""

    auto_print_delay_s: float | None = 5.0
    allow_print_without_film: bool = False


@dataclass(frozen=True, slots=True)
class PowerConfig:
    """Bridge battery and idle power configuration."""

    backend: PowerBackend = PowerBackend.X306
    battery_poll_interval_s: float = 30.0
    battery_warning_threshold_percent: float = 20.0
    battery_safe_shutdown_threshold_percent: float = 10.0
    idle_dim_after_s: float = 300.0
    idle_screen_off_after_s: float = 1800.0
    idle_deep_after_s: float = 3600.0
    idle_poweroff_after_s: float = 7200.0
    idle_poweroff_enabled: bool = False

    def __post_init__(self) -> None:
        idle_thresholds = (
            self.idle_dim_after_s,
            self.idle_screen_off_after_s,
            self.idle_deep_after_s,
            self.idle_poweroff_after_s,
        )
        if any(not isfinite(value) or value <= 0 for value in idle_thresholds):
            raise ValueError("[power] idle thresholds must be finite positive values")
        if any(previous >= current for previous, current in pairwise(idle_thresholds)):
            raise ValueError("[power] idle thresholds must be strictly increasing")
        if self.battery_safe_shutdown_threshold_percent > self.battery_warning_threshold_percent:
            raise ValueError(
                "[power].battery_safe_shutdown_threshold_percent must be <= "
                "[power].battery_warning_threshold_percent"
            )


@dataclass(frozen=True, slots=True)
class UiConfig:
    """Bridge UI surface configuration."""

    surface: UiSurface = UiSurface.LCD
    font_size: FontSize = FontSize.MEDIUM
    status_sink: StatusSinkKind = StatusSinkKind.LCD
    language: UiLanguage = UiLanguage.EN
    appearance: UiAppearance = UiAppearance.LIGHT


@dataclass(frozen=True, slots=True)
class FirmwareTrustedPublicKeyConfig:
    """Configured firmware release signing public key."""

    key_id: str
    public_key: str


@dataclass(frozen=True, slots=True)
class FirmwareUpdateConfig:
    """Firmware update trust configuration."""

    trusted_public_keys: tuple[FirmwareTrustedPublicKeyConfig, ...] = ()


_ADJUSTMENT_VALID_VALUES: frozenset[int] = frozenset({-100, -50, 0, 50, 100})
_VIGNETTE_VALID_VALUES: frozenset[int] = frozenset({0, 25, 50, 75, 100})

# Valid preset names: built-ins + user custom slots + the "Custom" sentinel.
# Keep in sync with VALID_PRESET_NAMES in imaging/presets.py (imported lazily
# to avoid a circular dependency at module load time).
_ADJUSTMENT_VALID_PRESET_NAMES: frozenset[str] = frozenset(
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


@dataclass(frozen=True, slots=True)
class AdjustmentsConfig:
    """Colour-adjustment settings.

    All values must be one of {-100, -50, 0, 50, 100}. Zero is the
    identity for every axis — no adjustments applied.
    """

    preset: str = "Default"
    """Active preset name.  Must be in the known preset name set."""

    saturation: int = 0
    """Colour intensity. -100 = greyscale, 0 = unchanged, +100 = double."""

    exposure: int = 0
    """Brightness in EV stops. -100 ≈ -1 EV (0.5×), +100 ≈ +1 EV (2×)."""

    sharpness: int = 0
    """Edge contrast. -100 = blurred, 0 = unchanged, +100 = double."""

    hue: int = 0
    """Hue rotation. -100 = -180 deg, 0 = unchanged, +100 = +180 deg."""

    datestamp: bool = False
    """Render EXIF DateTimeOriginal in the bottom-right corner when True."""

    watermark: bool = False
    """Render watermark_text in the top-right corner when True."""

    watermark_text: str = "InstantLink"
    """Text to stamp as a watermark. Empty string disables rendering."""

    vignette: int = 0
    """Corner-darkening strength. One of {0, 25, 50, 75, 100}. 0 = off (identity)."""

    def __post_init__(self) -> None:
        if self.preset not in _ADJUSTMENT_VALID_PRESET_NAMES:
            raise ValueError(
                f"[adjustments].preset must be one of "
                f"{sorted(_ADJUSTMENT_VALID_PRESET_NAMES)}; got {self.preset!r}"
            )
        for field_name in ("saturation", "exposure", "sharpness", "hue"):
            value = getattr(self, field_name)
            if value not in _ADJUSTMENT_VALID_VALUES:
                raise ValueError(
                    f"[adjustments].{field_name} must be one of "
                    f"{sorted(_ADJUSTMENT_VALID_VALUES)}; got {value!r}"
                )
        if self.vignette not in _VIGNETTE_VALID_VALUES:
            raise ValueError(
                f"[adjustments].vignette must be one of "
                f"{sorted(_VIGNETTE_VALID_VALUES)}; got {self.vignette!r}"
            )


@dataclass(frozen=True, slots=True)
class BridgeConfig:
    """Top-level bridge configuration."""

    ftp: FtpConfig = FtpConfig()
    printer: PrinterConfig = PrinterConfig()
    workflow: WorkflowConfig = WorkflowConfig()
    power: PowerConfig = PowerConfig()
    firmware: FirmwareUpdateConfig = FirmwareUpdateConfig()
    ui: UiConfig = UiConfig()
    adjustments: AdjustmentsConfig = AdjustmentsConfig()


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> BridgeConfig:
    """Load config from TOML, returning defaults if the file is missing."""

    if not path.exists():
        return BridgeConfig()
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return BridgeConfig(
        ftp=_load_ftp_config(data.get("ftp", {})),
        printer=_load_printer_config(data.get("printer", {})),
        workflow=_load_workflow_config(data.get("workflow", {})),
        power=_load_power_config(data.get("power", {})),
        firmware=_load_firmware_config(data.get("firmware", {})),
        ui=_load_ui_config(data.get("ui", {})),
        adjustments=_load_adjustments_config(data.get("adjustments", {})),
    )


def write_config(config: BridgeConfig, path: Path = DEFAULT_CONFIG_PATH) -> None:
    """Persist config to TOML using an atomic replace."""

    path.parent.mkdir(parents=True, exist_ok=True)
    text = render_config(config)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(text)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        if path.exists():
            tmp_path.chmod(path.stat().st_mode & 0o777)
        else:
            tmp_path.chmod(0o660)
        os.replace(tmp_path, path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def render_config(config: BridgeConfig) -> str:
    """Render a complete TOML config file."""

    preferred_wifi = (
        f"preferred_wifi_host = {_toml_string(config.ftp.preferred_wifi_host)}"
        if config.ftp.preferred_wifi_host is not None
        else '# preferred_wifi_host = "192.168.5.7"'
    )
    model = config.printer.model.value if config.printer.model is not None else "auto"
    device_name = config.printer.device_name or ""
    lines = [
        "[ftp]",
        f"mode = {_toml_string(config.ftp.mode.value)}",
        f"bind_host = {_toml_string(config.ftp.bind_host)}",
        f"host = {_toml_string(config.ftp.host)}",
        f"hotspot_host = {_toml_string(config.ftp.hotspot_host)}",
        f"port = {config.ftp.port}",
        f"username = {_toml_string(config.ftp.username)}",
        f"password = {_toml_string(config.ftp.password)}",
        f"incoming_dir = {_toml_string(str(config.ftp.incoming_dir))}",
        preferred_wifi,
        "",
        "[printer]",
        f"model = {_toml_string(model)}",
        f"fit = {_toml_string(config.printer.fit.value)}",
        f"quality = {config.printer.quality}",
        f"print_option = {config.printer.print_option}",
        f"device_name = {_toml_string(device_name)}",
        f"keepalive_interval_s = {_format_float(config.printer.keepalive_interval_s)}",
        f"search_interval_s = {_format_float(config.printer.search_interval_s)}",
        "",
        "[workflow]",
        f"auto_print_delay_s = {_format_auto_print_delay(config.workflow.auto_print_delay_s)}",
        f"allow_print_without_film = {_toml_bool(config.workflow.allow_print_without_film)}",
        "",
        "[power]",
        f"backend = {_toml_string(config.power.backend.value)}",
        f"battery_poll_interval_s = {_format_float(config.power.battery_poll_interval_s)}",
        "battery_warning_threshold_percent = "
        f"{_format_float(config.power.battery_warning_threshold_percent)}",
        "battery_safe_shutdown_threshold_percent = "
        f"{_format_float(config.power.battery_safe_shutdown_threshold_percent)}",
        f"idle_dim_after_s = {_format_float(config.power.idle_dim_after_s)}",
        f"idle_screen_off_after_s = {_format_float(config.power.idle_screen_off_after_s)}",
        f"idle_deep_after_s = {_format_float(config.power.idle_deep_after_s)}",
        f"idle_poweroff_after_s = {_format_float(config.power.idle_poweroff_after_s)}",
        f"idle_poweroff_enabled = {_toml_bool(config.power.idle_poweroff_enabled)}",
        "",
        "[firmware]",
    ]
    if config.firmware.trusted_public_keys:
        lines.append("trusted_public_keys = [")
        for record in config.firmware.trusted_public_keys:
            lines.append(
                "  { key_id = "
                f"{_toml_string(record.key_id)}, public_key = {_toml_string(record.public_key)}"
                " },"
            )
        lines.append("]")
    else:
        lines.append("trusted_public_keys = []")
    lines.extend(
        [
            "",
            "[ui]",
            f"surface = {_toml_string(config.ui.surface.value)}",
            f"font_size = {_toml_string(config.ui.font_size.value)}",
            f"status_sink = {_toml_string(config.ui.status_sink.value)}",
            f"language = {_toml_string(config.ui.language.value)}",
            f"appearance = {_toml_string(config.ui.appearance.value)}",
            "",
            "[adjustments]",
            f"preset = {_toml_string(config.adjustments.preset)}",
            f"saturation = {config.adjustments.saturation}",
            f"exposure = {config.adjustments.exposure}",
            f"sharpness = {config.adjustments.sharpness}",
            f"hue = {config.adjustments.hue}",
            f"datestamp = {_toml_bool(config.adjustments.datestamp)}",
            f"watermark = {_toml_bool(config.adjustments.watermark)}",
            f"watermark_text = {_toml_string(config.adjustments.watermark_text)}",
            f"vignette = {config.adjustments.vignette}",
            "",
        ]
    )
    return "\n".join(lines)


def _load_ftp_config(data: object) -> FtpConfig:
    if not isinstance(data, dict):
        raise ValueError("[ftp] must be a TOML table")
    host = _ipv4_str(data.get("host", "192.168.7.1"), "[ftp].host")
    hotspot_host = _ipv4_str(data.get("hotspot_host", "192.168.8.1"), "[ftp].hotspot_host")
    preferred_wifi_host = _optional_ipv4_str(
        data.get("preferred_wifi_host"),
        "[ftp].preferred_wifi_host",
    )
    _validate_not_usb_subnet(hotspot_host, "[ftp].hotspot_host", host)
    if preferred_wifi_host is not None:
        _validate_not_usb_subnet(preferred_wifi_host, "[ftp].preferred_wifi_host", host)
        _validate_not_hotspot_subnet(
            preferred_wifi_host,
            "[ftp].preferred_wifi_host",
            hotspot_host,
        )
    return FtpConfig(
        mode=parse_ftp_receive_mode(data.get("mode", "hotspot")),
        bind_host=str(data.get("bind_host", "0.0.0.0")),
        host=host,
        hotspot_host=hotspot_host,
        port=int(data.get("port", 21)),
        username=str(data.get("username", "ib")),
        password=str(data.get("password", "change-me")),
        incoming_dir=Path(str(data.get("incoming_dir", "/var/lib/InstantLinkBridge/incoming"))),
        preferred_wifi_host=preferred_wifi_host,
    )


def _load_printer_config(data: object) -> PrinterConfig:
    if not isinstance(data, dict):
        raise ValueError("[printer] must be a TOML table")
    raw_model = data.get("model")
    model = None if raw_model in {None, "auto"} else parse_printer_model(str(raw_model))
    quality = int(data.get("quality", 100))
    if not 1 <= quality <= 100:
        raise ValueError("[printer].quality must be between 1 and 100")
    keepalive_interval_s = float(data.get("keepalive_interval_s", 10.0))
    if not isfinite(keepalive_interval_s) or keepalive_interval_s <= 0:
        raise ValueError("[printer].keepalive_interval_s must be a finite value greater than 0")
    search_interval_s = float(data.get("search_interval_s", 5.0))
    if not isfinite(search_interval_s) or search_interval_s <= 0:
        raise ValueError("[printer].search_interval_s must be a finite value greater than 0")
    return PrinterConfig(
        model=model,
        fit=parse_fit_mode(str(data.get("fit", "auto"))),
        quality=quality,
        print_option=int(data.get("print_option", 0)),
        device_name=_optional_str(data.get("device_name")),
        keepalive_interval_s=keepalive_interval_s,
        search_interval_s=search_interval_s,
    )


def _load_workflow_config(data: object) -> WorkflowConfig:
    if not isinstance(data, dict):
        raise ValueError("[workflow] must be a TOML table")
    auto_print_delay_s = _parse_auto_print_delay(data.get("auto_print_delay_s", 5.0))
    allow_print_without_film = _parse_bool(
        data.get("allow_print_without_film", False),
        "[workflow].allow_print_without_film",
    )
    return WorkflowConfig(
        auto_print_delay_s=auto_print_delay_s,
        allow_print_without_film=allow_print_without_film,
    )


def _load_power_config(data: object) -> PowerConfig:
    if not isinstance(data, dict):
        raise ValueError("[power] must be a TOML table")
    return PowerConfig(
        backend=parse_power_backend(data.get("backend", PowerBackend.X306.value)),
        battery_poll_interval_s=_positive_float(
            data.get("battery_poll_interval_s", 30.0),
            "[power].battery_poll_interval_s",
        ),
        battery_warning_threshold_percent=_percent_float(
            data.get("battery_warning_threshold_percent", 20.0),
            "[power].battery_warning_threshold_percent",
        ),
        battery_safe_shutdown_threshold_percent=_percent_float(
            data.get("battery_safe_shutdown_threshold_percent", 10.0),
            "[power].battery_safe_shutdown_threshold_percent",
        ),
        idle_dim_after_s=_positive_float(
            data.get("idle_dim_after_s", 300.0),
            "[power].idle_dim_after_s",
        ),
        idle_screen_off_after_s=_positive_float(
            data.get("idle_screen_off_after_s", 1800.0),
            "[power].idle_screen_off_after_s",
        ),
        idle_deep_after_s=_positive_float(
            data.get("idle_deep_after_s", 3600.0),
            "[power].idle_deep_after_s",
        ),
        idle_poweroff_after_s=_positive_float(
            data.get("idle_poweroff_after_s", 7200.0),
            "[power].idle_poweroff_after_s",
        ),
        idle_poweroff_enabled=_parse_bool(
            data.get("idle_poweroff_enabled", False),
            "[power].idle_poweroff_enabled",
        ),
    )


def _load_firmware_config(data: object) -> FirmwareUpdateConfig:
    if not isinstance(data, dict):
        raise ValueError("[firmware] must be a TOML table")

    raw_keys = data.get("trusted_public_keys", ())
    if raw_keys is None:
        raw_keys = ()
    if not isinstance(raw_keys, list | tuple):
        raise ValueError("[firmware].trusted_public_keys must be a list")

    records: list[FirmwareTrustedPublicKeyConfig] = []
    seen_key_ids: set[str] = set()
    for index, raw_record in enumerate(raw_keys):
        field = f"[firmware].trusted_public_keys[{index}]"
        if not isinstance(raw_record, dict):
            raise ValueError(f"{field} must be a TOML table")
        key_id = _required_non_empty_str(raw_record.get("key_id"), f"{field}.key_id")
        public_key = _required_non_empty_str(raw_record.get("public_key"), f"{field}.public_key")
        if key_id in seen_key_ids:
            raise ValueError(f"{field}.key_id must be unique")
        seen_key_ids.add(key_id)
        records.append(FirmwareTrustedPublicKeyConfig(key_id=key_id, public_key=public_key))

    return FirmwareUpdateConfig(trusted_public_keys=tuple(records))


def _load_adjustments_config(data: object) -> AdjustmentsConfig:
    if not isinstance(data, dict):
        raise ValueError("[adjustments] must be a TOML table")
    raw_preset = str(data.get("preset", "Default"))
    if raw_preset not in _ADJUSTMENT_VALID_PRESET_NAMES:
        raise ValueError(
            f"[adjustments].preset must be one of "
            f"{sorted(_ADJUSTMENT_VALID_PRESET_NAMES)}; got {raw_preset!r}"
        )
    return AdjustmentsConfig(
        preset=raw_preset,
        saturation=_adjustment_int(data.get("saturation", 0), "[adjustments].saturation"),
        exposure=_adjustment_int(data.get("exposure", 0), "[adjustments].exposure"),
        sharpness=_adjustment_int(data.get("sharpness", 0), "[adjustments].sharpness"),
        hue=_adjustment_int(data.get("hue", 0), "[adjustments].hue"),
        datestamp=_parse_bool(data.get("datestamp", False), "[adjustments].datestamp"),
        watermark=_parse_bool(data.get("watermark", False), "[adjustments].watermark"),
        watermark_text=str(data.get("watermark_text", "InstantLink")),
        vignette=_vignette_int(data.get("vignette", 0), "[adjustments].vignette"),
    )


def _adjustment_int(value: object, field_name: str) -> int:
    if not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    if value not in _ADJUSTMENT_VALID_VALUES:
        raise ValueError(
            f"{field_name} must be one of {sorted(_ADJUSTMENT_VALID_VALUES)}; got {value!r}"
        )
    return value


def _vignette_int(value: object, field_name: str) -> int:
    if not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    if value not in _VIGNETTE_VALID_VALUES:
        raise ValueError(
            f"{field_name} must be one of {sorted(_VIGNETTE_VALID_VALUES)}; got {value!r}"
        )
    return value


def _load_ui_config(data: object) -> UiConfig:
    if not isinstance(data, dict):
        raise ValueError("[ui] must be a TOML table")
    return UiConfig(
        surface=parse_ui_surface(data.get("surface", UiSurface.LCD.value)),
        font_size=parse_font_size(data.get("font_size", FontSize.MEDIUM.value)),
        status_sink=parse_status_sink(data.get("status_sink", StatusSinkKind.LCD.value)),
        language=parse_ui_language(data.get("language", UiLanguage.EN.value)),
        appearance=parse_ui_appearance(data.get("appearance", UiAppearance.LIGHT.value)),
    )


def parse_ui_surface(value: object) -> UiSurface:
    """Parse a configured UI surface."""

    text = str(value).strip().lower()
    try:
        return UiSurface(text)
    except ValueError as exc:
        allowed = ", ".join(s.value for s in UiSurface)
        raise ValueError(f"[ui].surface must be one of: {allowed}") from exc


def parse_font_size(value: object) -> FontSize:
    """Parse a configured UI font size."""

    text = str(value).strip().lower()
    try:
        return FontSize(text)
    except ValueError as exc:
        allowed = ", ".join(s.value for s in FontSize)
        raise ValueError(f"[ui].font_size must be one of: {allowed}") from exc


def parse_status_sink(value: object) -> StatusSinkKind:
    """Parse a configured status indicator sink kind."""

    text = str(value).strip().lower()
    try:
        return StatusSinkKind(text)
    except ValueError as exc:
        allowed = ", ".join(s.value for s in StatusSinkKind)
        raise ValueError(f"[ui].status_sink must be one of: {allowed}") from exc


def parse_ui_appearance(value: object) -> UiAppearance:
    """Parse a configured LCD appearance (light / dark / auto).

    Legacy "system" configs roll forward to AUTO without erroring so a
    bridge already in the field keeps booting after this release.
    """

    text = str(value).strip().lower()
    if text == "system":
        # SYSTEM was a no-op on hardware that has no host OS theme and no
        # ambient sensor; AUTO replaces it with a clock-time schedule.
        return UiAppearance.AUTO
    try:
        return UiAppearance(text)
    except ValueError as exc:
        allowed = ", ".join(a.value for a in UiAppearance)
        raise ValueError(f"[ui].appearance must be one of: {allowed}") from exc


def parse_ui_language(value: object) -> UiLanguage:
    """Parse a configured LCD language (BCP 47 tag)."""

    # Case-insensitive for the tag prefix ("en"), but the "Hans" subtag is
    # mixed-case in BCP 47 — accept any case spelling and normalise on the
    # enum value.
    text = str(value).strip()
    for lang in UiLanguage:
        if text.lower() == lang.value.lower():
            return lang
    allowed = ", ".join(lang.value for lang in UiLanguage)
    raise ValueError(f"[ui].language must be one of: {allowed}")


def parse_ftp_receive_mode(value: object) -> FtpReceiveMode:
    """Parse a configured FTP receive mode."""

    text = str(value).strip().lower()
    try:
        return FtpReceiveMode(text)
    except ValueError as exc:
        allowed = ", ".join(mode.value for mode in FtpReceiveMode)
        raise ValueError(f"[ftp].mode must be one of: {allowed}") from exc


def parse_power_backend(value: object) -> PowerBackend:
    """Parse a configured bridge power backend."""

    text = str(value).strip().lower()
    try:
        return PowerBackend(text)
    except ValueError as exc:
        allowed = ", ".join(backend.value for backend in PowerBackend)
        raise ValueError(f"[power].backend must be one of: {allowed}") from exc


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _required_non_empty_str(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _optional_ipv4_str(value: object, field_name: str) -> str | None:
    text = _optional_str(value)
    if text is None:
        return None
    return _ipv4_str(text, field_name)


def _ipv4_str(value: object, field_name: str) -> str:
    text = str(value).strip()
    try:
        parsed = ip_address(text)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an IPv4 address") from exc
    if parsed.version != 4:
        raise ValueError(f"{field_name} must be an IPv4 address")
    return text


def _validate_not_usb_subnet(value: str, field_name: str, usb_host: str) -> None:
    usb_network = ipv4_24_network(usb_host)
    if IPv4Address(value) in usb_network:
        raise ValueError(f"{field_name} must not be inside USB subnet {usb_network}")


def _validate_not_hotspot_subnet(value: str, field_name: str, hotspot_host: str) -> None:
    hotspot_network = ipv4_24_network(hotspot_host)
    if IPv4Address(value) in hotspot_network:
        raise ValueError(f"{field_name} must not be inside hotspot subnet {hotspot_network}")


def ipv4_24_network(host: str) -> IPv4Network:
    """Return the InstantLink Bridge /24 network containing an IPv4 host address."""

    return IPv4Network(f"{host}/{FTP_SUBNET_PREFIX_LEN}", strict=False)


def ipv4_in_24_subnet(address: str, host: str) -> bool:
    """Return whether an IPv4 address is in the /24 network containing host."""

    return IPv4Address(address) in ipv4_24_network(host)


def is_link_local_ipv4(address: str) -> bool:
    """Return whether an IPv4 address is link-local."""

    try:
        return IPv4Address(address).is_link_local
    except ValueError:
        return False


def _toml_string(value: str) -> str:
    return json.dumps(value)


def _toml_bool(value: bool) -> str:
    return "true" if value else "false"


def _format_float(value: float) -> str:
    return f"{value:g}"


def _format_auto_print_delay(value: float | None) -> str:
    if value is None:
        return _toml_string("off")
    return _format_float(value)


def _parse_auto_print_delay(value: object) -> float | None:
    if isinstance(value, str) and value.strip().lower() in {"off", "false", "none"}:
        return None
    if not isinstance(value, str | int | float):
        raise ValueError("[workflow].auto_print_delay_s must be off, 0, or 5")
    auto_print_delay_s = float(value)
    if not isfinite(auto_print_delay_s) or auto_print_delay_s < 0:
        raise ValueError("[workflow].auto_print_delay_s must be off, 0, or 5")
    if auto_print_delay_s == 0 or auto_print_delay_s == 5:
        return auto_print_delay_s
    return 5.0


def _positive_float(value: object, field_name: str) -> float:
    if not isinstance(value, str | int | float):
        raise ValueError(f"{field_name} must be a finite value greater than 0")
    parsed = float(value)
    if not isfinite(parsed) or parsed <= 0:
        raise ValueError(f"{field_name} must be a finite value greater than 0")
    return parsed


def _percent_float(value: object, field_name: str) -> float:
    if not isinstance(value, str | int | float):
        raise ValueError(f"{field_name} must be between 0 and 100")
    parsed = float(value)
    if not isfinite(parsed) or not 0 <= parsed <= 100:
        raise ValueError(f"{field_name} must be between 0 and 100")
    return parsed


def _parse_bool(value: object, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "on", "1"}:
            return True
        if normalized in {"false", "no", "off", "0"}:
            return False
    raise ValueError(f"{field_name} must be a boolean")
