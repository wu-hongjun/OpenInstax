"""Local system identity and version helpers."""

from __future__ import annotations

import platform
import shutil
import subprocess
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from instantlink_bridge import __version__


@dataclass(frozen=True, slots=True)
class SystemInfo:
    """Information shown on the LCD System settings page."""

    device_id: str
    app_version: str
    python_version: str
    bluez_version: str
    os_version: str


def read_system_info() -> SystemInfo:
    """Read local software versions and the stable device identifier."""

    return SystemInfo(
        device_id=read_device_id(),
        app_version=read_app_version(),
        python_version=platform.python_version(),
        bluez_version=read_bluez_version(),
        os_version=read_os_version(),
    )


def read_device_id() -> str:
    """Return a short stable identifier derived from the machine ID."""

    suffix = read_device_suffix()
    if suffix is not None:
        return f"IB-{suffix}"
    return "IB-UNKNOWN"


def default_hotspot_ssid() -> str:
    """Return the default per-device bridge Wi-Fi SSID.

    Format: ``InstantLink-XXXX`` where XXXX is the last 4 hex chars of
    the machine identifier — matches the product name the user sees
    elsewhere in the UI.
    """

    suffix = read_device_suffix()
    if suffix is not None:
        return f"InstantLink-{suffix[-4:]}"
    return "InstantLink-XXXX"


def read_device_suffix() -> str | None:
    """Return the stable eight-character device suffix, if available."""

    for path in (Path("/etc/machine-id"), Path("/var/lib/dbus/machine-id")):
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            return value[:8].upper()
    return None


def read_app_version() -> str:
    """Return the installed InstantLink Bridge package version."""

    try:
        return version("instantlink-bridge")
    except PackageNotFoundError:
        return __version__


def format_version_summary(info: SystemInfo | None = None) -> str:
    """Return a concise one-line version summary for CLI output."""

    snapshot = info if info is not None else read_system_info()
    return (
        f"InstantLink Bridge {snapshot.app_version} "
        f"(Python {snapshot.python_version}; BlueZ {snapshot.bluez_version}; "
        f"OS {snapshot.os_version})"
    )


def format_status_report(info: SystemInfo | None = None) -> str:
    """Return a read-only system status report for CLI output."""

    snapshot = info if info is not None else read_system_info()
    return "\n".join(
        (
            "InstantLink Bridge status",
            f"device: {snapshot.device_id}",
            f"app: {snapshot.app_version}",
            f"python: {snapshot.python_version}",
            f"bluez: {snapshot.bluez_version}",
            f"os: {snapshot.os_version}",
        )
    )


def read_bluez_version() -> str:
    """Return the installed BlueZ version, if bluetoothctl is available."""

    if shutil.which("bluetoothctl") is None:
        return "unknown"
    try:
        result = subprocess.run(
            ("bluetoothctl", "-v"),
            check=False,
            capture_output=True,
            text=True,
            timeout=0.5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    value = (result.stdout or result.stderr).strip().splitlines()
    if not value:
        return "unknown"
    first_line = value[0].strip()
    if ":" in first_line:
        first_line = first_line.rsplit(":", 1)[1].strip()
    return first_line or "unknown"


def read_os_version() -> str:
    """Return a compact operating-system version label."""

    data = _read_os_release()
    return data.get("PRETTY_NAME") or data.get("VERSION_CODENAME") or platform.system()


def _read_os_release() -> dict[str, str]:
    for path in (Path("/etc/os-release"), Path("/usr/lib/os-release")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        values: dict[str, str] = {}
        for line in lines:
            if "=" not in line or line.startswith("#"):
                continue
            key, raw_value = line.split("=", 1)
            values[key] = raw_value.strip().strip('"')
        return values
    return {}
