"""Read-only status collection for the management scaffold."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from instantlink_bridge.config import (
    DEFAULT_CONFIG_PATH,
    BridgeConfig,
    FtpReceiveMode,
    load_config,
)
from instantlink_bridge.manager.auth import PairingWindowError, PairingWindowStore
from instantlink_bridge.manager.contract import API_VERSION, SERVICE_NAME, JsonObject, JsonValue
from instantlink_bridge.system_info import SystemInfo, read_system_info
from instantlink_bridge.system_stats import (
    CPUSampler,
    SystemStatsSnapshot,
    read_system_stats,
)

DISPLAY_NAME = "InstantLink Bridge"

# Module-level sampler so CPU% can be computed across status calls. The first
# call has no baseline; ``read_system_stats`` handles the brief warm-up by
# resampling after a short sleep. Subsequent calls return the percent measured
# over the interval since the previous /v1/status request, which is the same
# pattern the LCD About page uses.
_status_cpu_sampler = CPUSampler()


@dataclass(frozen=True, slots=True)
class ConfigSnapshot:
    """Config plus load metadata safe to expose through read-only status."""

    config: BridgeConfig
    source: str
    error_code: str | None = None
    message: str | None = None


@dataclass(frozen=True, slots=True)
class PairingStatusSnapshot:
    """Pairing-window metadata safe to expose over unauthenticated discovery."""

    open: bool
    expires_at: int | None
    expires_in_seconds: int | None
    error_code: str | None = None
    message: str | None = None


def collect_hello_payload(
    config_path: Path = DEFAULT_CONFIG_PATH,
    *,
    pairing_store: PairingWindowStore | None = None,
) -> JsonObject:
    """Return unauthenticated discovery metadata with no credentials or printer identifiers."""

    info = read_system_info()
    snapshot = read_config_snapshot(config_path)
    pairing = read_pairing_status(pairing_store)
    return {
        "api_version": API_VERSION,
        "device": device_payload(info, pairing_open=pairing.open),
        "management": {
            "service": SERVICE_NAME,
            "auth_implemented": True,
            "admin_routes": "signed_request_required",
            "pairing_open": pairing.open,
            "public_key_fingerprint": None,
        },
        "network_labels": network_labels_payload(snapshot.config),
    }


def collect_pairing_status_payload(
    pairing_store: PairingWindowStore | None = None,
) -> JsonObject:
    """Return pairing status without exposing the physical confirmation code."""

    return {
        "pairing": pairing_status_payload(read_pairing_status(pairing_store)),
    }


def pairing_status_payload(pairing: PairingStatusSnapshot) -> JsonObject:
    """Return a JSON payload for a pairing status snapshot."""

    payload: JsonObject = {
        "open": pairing.open,
        "auth_implemented": True,
        "confirmation_code_required": True,
        "expires_at": pairing.expires_at,
        "expires_in_seconds": pairing.expires_in_seconds,
    }
    if pairing.error_code is not None:
        payload["error_code"] = pairing.error_code
        payload["message"] = pairing.message or "Pairing status could not be read."
    return payload


def collect_status_payload(
    config_path: Path = DEFAULT_CONFIG_PATH,
    *,
    pairing_store: PairingWindowStore | None = None,
) -> JsonObject:
    """Return local read-only status for CLI use without probing hardware."""

    info = read_system_info()
    snapshot = read_config_snapshot(config_path)
    config = snapshot.config
    pairing = read_pairing_status(pairing_store)
    return {
        "api_version": API_VERSION,
        "device": device_payload(info, pairing_open=pairing.open),
        "runtime": {
            "python_version": info.python_version,
            "bluez_version": info.bluez_version,
            "os_version": info.os_version,
        },
        "services": {
            "runtime": {
                "name": "instantlink-bridge.service",
                "status": "unknown",
            },
            "manager": {
                "name": "instantlink-bridge-manager.service",
                "status": "local_cli",
            },
        },
        "config": config_payload(snapshot, config_path),
        "network": network_payload(config),
        "printer": printer_payload(config),
        "pairing": pairing_status_payload(pairing),
    }


def collect_http_status_payload(
    config_path: Path = DEFAULT_CONFIG_PATH,
) -> JsonObject:
    """Return the BridgeStatus-shaped payload used by the macOS management API."""

    info = read_system_info()
    snapshot = read_config_snapshot(config_path)
    config = snapshot.config
    upload_mode = bridge_upload_mode(config.ftp.mode)
    stats = read_system_stats(_status_cpu_sampler)
    return {
        "status": {
            "device_id": info.device_id,
            "display_name": DISPLAY_NAME,
            "bridge_version": info.app_version,
            "api_version": API_VERSION,
            "readiness": "needs_attention" if snapshot.error_code is not None else "ready",
            "active_upload_mode": upload_mode,
            "uptime_seconds": None,
            "network": {
                "mode": upload_mode,
                "label": bridge_upload_label(config.ftp.mode),
                "address": bridge_upload_address(config),
                "connected": snapshot.error_code is None,
            },
            "printer": {
                "display_name": config.printer.device_name,
                "model": config.printer.model.value if config.printer.model is not None else None,
                "film_remaining": None,
                "battery_percent": None,
                # Charge state, print/connection status, and the smoothed battery-life estimate.
                # The management API surfaces them from live status when available; defaults are
                # None/False so older clients and the no-live-status path stay backward-compatible.
                "charging": None,
                "battery_minutes_remaining": None,
                "print_status": None,
                "connected": False,
                "busy": False,
                "last_error": None,
            },
            "update": {
                "current_version": info.app_version,
                "available_version": None,
                "can_update": False,
                "operation_id": None,
                "phase": "idle",
            },
            "system_stats": system_stats_payload(stats),
            "last_upload": None,
            "last_error": (
                {
                    "message": snapshot.message or "Config could not be loaded.",
                    "recommended_action": "Fix the Bridge config file and restart the manager.",
                    "details": {"error_code": snapshot.error_code},
                }
                if snapshot.error_code is not None
                else None
            ),
        }
    }


def system_stats_payload(stats: SystemStatsSnapshot) -> JsonObject:
    """Serialize a ``SystemStatsSnapshot`` into the /v1/status JSON shape.

    Every field is always present; readers that fail (missing thermal zone,
    storage stat error, no CPU baseline yet) serialize as ``null`` so the
    macOS client can render an em-dash without a key-missing branch.
    """

    return {
        "cpu_percent": stats.cpu_percent,
        "ram_used_mb": stats.ram_used_mb,
        "ram_total_mb": stats.ram_total_mb,
        "storage_used_gb": stats.storage_used_gb,
        "storage_total_gb": stats.storage_total_gb,
        "soc_temperature_c": stats.soc_temperature_c,
    }


def read_config_snapshot(config_path: Path) -> ConfigSnapshot:
    """Load config or return defaults with sanitized error metadata."""

    try:
        exists = config_path.exists()
        config = load_config(config_path)
    except (OSError, ValueError) as exc:
        return ConfigSnapshot(
            config=BridgeConfig(),
            source="error",
            error_code="config_unavailable",
            message=str(exc),
        )
    return ConfigSnapshot(config=config, source="file" if exists else "defaults")


def bridge_upload_mode(mode: FtpReceiveMode) -> str:
    """Map Bridge FTP receive config to the management API upload-mode vocabulary."""

    if mode is FtpReceiveMode.HOTSPOT:
        return "bridge_wifi"
    if mode is FtpReceiveMode.PEER:
        return "same_wifi"
    if mode is FtpReceiveMode.WIRED:
        return "usb_debug"
    return "unknown"


def bridge_upload_label(mode: FtpReceiveMode) -> str:
    """Return the user-facing label for a configured upload mode."""

    if mode is FtpReceiveMode.HOTSPOT:
        return "Bridge Wi-Fi"
    if mode is FtpReceiveMode.PEER:
        return "Same-Wi-Fi"
    if mode is FtpReceiveMode.WIRED:
        return "USB IP"
    return "Auto"


def bridge_upload_address(config: BridgeConfig) -> str | None:
    """Return the primary configured address for the active upload mode."""

    if config.ftp.mode is FtpReceiveMode.HOTSPOT:
        return config.ftp.hotspot_host
    if config.ftp.mode is FtpReceiveMode.PEER:
        return config.ftp.preferred_wifi_host
    if config.ftp.mode is FtpReceiveMode.WIRED:
        return config.ftp.host
    return None


def read_pairing_status(
    pairing_store: PairingWindowStore | None = None,
) -> PairingStatusSnapshot:
    """Read the pairing window without exposing the confirmation code."""

    store = pairing_store or PairingWindowStore()
    try:
        window = store.read_window()
    except PairingWindowError as exc:
        return PairingStatusSnapshot(
            open=False,
            expires_at=None,
            expires_in_seconds=None,
            error_code=exc.error_code,
            message=str(exc),
        )
    if window is None:
        return PairingStatusSnapshot(open=False, expires_at=None, expires_in_seconds=None)
    now = store.now_seconds()
    expires_in_seconds = max(0, window.expires_at - now)
    return PairingStatusSnapshot(
        open=not window.is_expired(now=now),
        expires_at=window.expires_at,
        expires_in_seconds=expires_in_seconds,
    )


def current_device_id() -> str:
    """Return the Bridge device id used to guard pairing completion."""

    return read_system_info().device_id


def device_payload(info: SystemInfo, *, pairing_open: bool = False) -> JsonObject:
    """Return stable device identity fields shared by hello and status."""

    return {
        "device_id": info.device_id,
        "display_name": DISPLAY_NAME,
        "software_version": info.app_version,
        "api_version": API_VERSION,
        "management_public_key_fingerprint": None,
        "pairing_open": pairing_open,
        "network_labels": ["Bridge Wi-Fi", "USB IP", "Same-Wi-Fi"],
        "endpoint_url": None,
        "is_paired": False,
    }


def config_payload(snapshot: ConfigSnapshot, config_path: Path) -> JsonObject:
    """Return a sanitized config summary without FTP credentials."""

    payload: JsonObject = {
        "path": str(config_path),
        "source": snapshot.source,
        "loaded": snapshot.error_code is None,
    }
    if snapshot.error_code is not None:
        payload["error_code"] = snapshot.error_code
        payload["message"] = snapshot.message or "Config could not be loaded."
    return payload


def network_payload(config: BridgeConfig) -> JsonObject:
    """Return management-safe network and FTP receive-mode labels."""

    return {
        "management_interfaces": network_labels_payload(config),
        "ftp_receive": {
            "mode": config.ftp.mode.value,
            "bind_host": config.ftp.bind_host,
            "port": config.ftp.port,
            "incoming_dir": str(config.ftp.incoming_dir),
        },
        "same_wifi_management_enabled": False,
    }


def network_labels_payload(config: BridgeConfig) -> list[JsonValue]:
    """Return safe labels for networks where management may eventually bind."""

    labels: list[JsonValue] = [
        {
            "key": "usb_debug",
            "label": "USB IP",
            "address": config.ftp.host,
            "enabled": True,
        },
        {
            "key": "bridge_wifi",
            "label": "Bridge Wi-Fi",
            "address": config.ftp.hotspot_host,
            "enabled": True,
        },
        {
            "key": "same_wifi",
            "label": "Same-Wi-Fi",
            "address": config.ftp.preferred_wifi_host,
            "enabled": False,
        },
    ]
    return labels


def printer_payload(config: BridgeConfig) -> JsonObject:
    """Return selected-printer metadata without exposing Bluetooth addresses."""

    return {
        "configured": config.printer.device_name is not None,
        "device_name": config.printer.device_name,
        "model": config.printer.model.value if config.printer.model is not None else None,
        "status": "unknown",
    }
