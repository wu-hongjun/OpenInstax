"""Stable JSON contract helpers for the Bridge management surface."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TypeAlias

SCHEMA_VERSION = 1
API_VERSION = "v1"
SERVICE_NAME = "instantlink-bridge-manager"
DEFAULT_PORT = 8742
DEFAULT_BIND_HOSTS = ("192.168.7.1", "192.168.8.1")

JsonValue: TypeAlias = (
    None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
)
JsonObject: TypeAlias = dict[str, JsonValue]


@dataclass(frozen=True, slots=True)
class ManagementRoute:
    """One public management route advertised by the CLI contract."""

    method: str
    path: str
    auth_required: bool
    operation_id: str
    summary: str


DISCOVERY_ROUTES: tuple[ManagementRoute, ...] = (
    ManagementRoute(
        method="GET",
        path="/v1/hello",
        auth_required=False,
        operation_id="hello",
        summary="Discovery metadata safe for unpaired clients.",
    ),
    ManagementRoute(
        method="GET",
        path="/v1/pairing/status",
        auth_required=False,
        operation_id="pairing_status",
        summary="Pairing-window state without exposing secrets.",
    ),
    ManagementRoute(
        method="POST",
        path="/v1/pairing/complete",
        auth_required=False,
        operation_id="pairing_complete",
        summary="Complete physical-code-gated local authorization.",
    ),
    ManagementRoute(
        method="POST",
        path="/v1/pairing/usb_auto_trust",
        auth_required=False,
        operation_id="pairing_usb_auto_trust",
        summary="USB-physical auto-trust: register a new client over the USB-tether interface.",
    ),
)

ADMIN_ROUTES: tuple[ManagementRoute, ...] = (
    ManagementRoute("GET", "/v1/status", True, "status", "Read-only bridge status."),
    ManagementRoute("GET", "/v1/config", True, "config_get", "Read sanitized bridge config."),
    ManagementRoute("PUT", "/v1/config", True, "config_put", "Update bridge config."),
    ManagementRoute(
        "POST",
        "/v1/config/validate",
        True,
        "config_validate",
        "Validate bridge config without saving.",
    ),
    ManagementRoute(
        "GET",
        "/v1/network/status",
        True,
        "network_status",
        "Read network mode and health.",
    ),
    ManagementRoute("POST", "/v1/network/mode", True, "network_mode", "Change network mode."),
    ManagementRoute(
        "GET",
        "/v1/printer/status",
        True,
        "printer_status",
        "Read selected printer status.",
    ),
    ManagementRoute("POST", "/v1/printer/scan", True, "printer_scan", "Scan for printers."),
    ManagementRoute("POST", "/v1/printer/select", True, "printer_select", "Select printer."),
    ManagementRoute("POST", "/v1/printer/forget", True, "printer_forget", "Forget printer."),
    ManagementRoute("POST", "/v1/backup/create", True, "backup_create", "Create backup."),
    ManagementRoute("POST", "/v1/backup/restore", True, "backup_restore", "Restore backup."),
    ManagementRoute(
        "POST",
        "/v1/support-bundle/create",
        True,
        "support_bundle_create",
        "Create redacted support bundle.",
    ),
    ManagementRoute(
        "POST",
        "/v1/update/preflight",
        True,
        "update_preflight",
        "Validate an update before install.",
    ),
    ManagementRoute("POST", "/v1/update/upload", True, "update_upload", "Upload update package."),
    ManagementRoute(
        "POST",
        "/v1/update/install",
        True,
        "update_install",
        "Install verified update package.",
    ),
    ManagementRoute("GET", "/v1/update/status", True, "update_status", "Read update state."),
    ManagementRoute(
        "POST",
        "/v1/update/mark-good",
        True,
        "update_mark_good",
        "Mark pending release as good.",
    ),
    ManagementRoute("POST", "/v1/update/rollback", True, "update_rollback", "Rollback release."),
    ManagementRoute("GET", "/v1/events", True, "events", "Stream management events."),
)

MANAGEMENT_ROUTES = DISCOVERY_ROUTES + ADMIN_ROUTES


def new_request_id() -> str:
    """Return an opaque request id for a CLI invocation or HTTP request."""

    return uuid.uuid4().hex


def success_response(
    payload: Mapping[str, JsonValue] | None = None,
    *,
    request_id: str | None = None,
) -> JsonObject:
    """Wrap a successful payload in the management response envelope."""

    response: JsonObject = {
        "schema_version": SCHEMA_VERSION,
        "request_id": request_id if request_id is not None else new_request_id(),
        "ok": True,
    }
    if payload is not None:
        response.update(payload)
    return response


def error_response(
    error_code: str,
    message: str,
    *,
    request_id: str | None = None,
    recommended_action: str | None = None,
    details: Mapping[str, JsonValue] | None = None,
    retry_after_seconds: int | None = None,
) -> JsonObject:
    """Wrap an error in the management response envelope."""

    error_payload: JsonObject = {"message": message}
    if details is not None:
        error_payload["details"] = dict(details)
    if recommended_action is not None:
        error_payload["recommended_action"] = recommended_action
    if retry_after_seconds is not None:
        error_payload["retry_after_seconds"] = retry_after_seconds

    response: JsonObject = {
        "schema_version": SCHEMA_VERSION,
        "request_id": request_id if request_id is not None else new_request_id(),
        "ok": False,
        "error_code": error_code,
        "message": message,
        "error": error_payload,
    }
    if recommended_action is not None:
        response["recommended_action"] = recommended_action
    return response


def routes_payload() -> JsonObject:
    """Return the public route catalog as JSON-compatible data."""

    return {
        "api_version": API_VERSION,
        "routes": [route_to_json(route) for route in MANAGEMENT_ROUTES],
    }


def route_to_json(route: ManagementRoute) -> JsonObject:
    """Return a JSON-compatible route description."""

    return {
        "method": route.method,
        "path": route.path,
        "auth_required": route.auth_required,
        "operation_id": route.operation_id,
        "summary": route.summary,
    }
