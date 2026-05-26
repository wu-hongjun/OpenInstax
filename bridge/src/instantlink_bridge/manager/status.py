"""Read-only status collection for the management scaffold."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from instantlink_bridge.config import (
    DEFAULT_CONFIG_PATH,
    BridgeConfig,
    load_config,
)
from instantlink_bridge.manager.contract import API_VERSION, SERVICE_NAME, JsonObject, JsonValue
from instantlink_bridge.system_info import SystemInfo, read_system_info

DISPLAY_NAME = "InstantLink Bridge"


@dataclass(frozen=True, slots=True)
class ConfigSnapshot:
    """Config plus load metadata safe to expose through read-only status."""

    config: BridgeConfig
    source: str
    error_code: str | None = None
    message: str | None = None


def collect_hello_payload(config_path: Path = DEFAULT_CONFIG_PATH) -> JsonObject:
    """Return unauthenticated discovery metadata with no credentials or printer identifiers."""

    info = read_system_info()
    snapshot = read_config_snapshot(config_path)
    return {
        "api_version": API_VERSION,
        "device": device_payload(info),
        "management": {
            "service": SERVICE_NAME,
            "auth_implemented": False,
            "admin_routes": "auth_required",
            "pairing_open": False,
            "public_key_fingerprint": None,
        },
        "network_labels": network_labels_payload(snapshot.config),
    }


def collect_pairing_status_payload() -> JsonObject:
    """Return Phase 1 pairing status before local authorization is implemented."""

    return {
        "pairing": {
            "open": False,
            "auth_implemented": False,
            "confirmation_code_required": True,
            "expires_at": None,
        }
    }


def collect_status_payload(config_path: Path = DEFAULT_CONFIG_PATH) -> JsonObject:
    """Return local read-only status for CLI use without probing hardware."""

    info = read_system_info()
    snapshot = read_config_snapshot(config_path)
    config = snapshot.config
    return {
        "api_version": API_VERSION,
        "device": device_payload(info),
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


def device_payload(info: SystemInfo) -> JsonObject:
    """Return stable device identity fields shared by hello and status."""

    return {
        "device_id": info.device_id,
        "display_name": DISPLAY_NAME,
        "software_version": info.app_version,
        "api_version": API_VERSION,
        "management_public_key_fingerprint": None,
        "pairing_open": False,
        "network_labels": ["Bridge Wi-Fi", "USB debug", "Same-Wi-Fi"],
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
            "label": "USB debug",
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
