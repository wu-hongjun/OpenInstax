"""aiohttp management API app factory."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from aiohttp import web

from instantlink_bridge.config import DEFAULT_CONFIG_PATH, write_config
from instantlink_bridge.manager import status as manager_status
from instantlink_bridge.manager.auth import (
    DEFAULT_CLIENTS_DIR,
    AuthorizedClient,
    ClientStore,
    ManagementAuthError,
    PairingWindowError,
    PairingWindowStore,
    SignedRequestVerifier,
    utc_timestamp,
)
from instantlink_bridge.manager.config_payload import (
    ConfigValidationError,
    apply_config_diff,
    serialize_config,
)
from instantlink_bridge.manager.contract import (
    ADMIN_ROUTES,
    JsonObject,
    ManagementRoute,
    error_response,
    new_request_id,
    success_response,
)
from instantlink_bridge.manager.diagnostics import (
    DEFAULT_LOG_LEVELS,
    SUPPORT_BUNDLE_SCHEMA_VERSION,
    LogStreamSource,
    SupportBundleSource,
    create_support_bundle,
    default_support_bundle_sources,
    format_sse_event,
    format_sse_heartbeat,
)
from instantlink_bridge.manager.update_flow import (
    ManagerEnvironment,
    UpdateFlowError,
    read_update_status,
    run_backup_create,
    run_backup_restore,
    run_install,
    run_mark_good,
    run_preflight,
    run_rollback,
    store_upload,
)

LOGGER = logging.getLogger(__name__)
REQUEST_ID_HEADER = "X-Request-Id"

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]
AdminHandler = Callable[[web.Request], Awaitable[web.StreamResponse]]
RequestIdFactory = Callable[[], str]
CONFIG_PATH_KEY = web.AppKey("instantlink_bridge.manager.config_path", Path)
REQUEST_ID_FACTORY_KEY = web.AppKey("instantlink_bridge.manager.request_id_factory", object)
AUTH_VERIFIER_KEY = web.AppKey("instantlink_bridge.manager.auth_verifier", SignedRequestVerifier)
CLIENT_STORE_KEY = web.AppKey("instantlink_bridge.manager.client_store", ClientStore)
PAIRING_STORE_KEY = web.AppKey("instantlink_bridge.manager.pairing_store", PairingWindowStore)
ENVIRONMENT_KEY = web.AppKey("instantlink_bridge.manager.environment", ManagerEnvironment)
LOG_STREAM_SOURCE_KEY = web.AppKey(
    "instantlink_bridge.manager.log_stream_source", LogStreamSource
)
SUPPORT_BUNDLE_DIR_KEY = web.AppKey(
    "instantlink_bridge.manager.support_bundle_dir", Path
)
SUPPORT_BUNDLE_SOURCES_KEY = web.AppKey(
    "instantlink_bridge.manager.support_bundle_sources", object
)
DEFAULT_SUPPORT_BUNDLE_DIR = Path("/var/lib/InstantLinkBridge/support-bundles")


@dataclass(frozen=True, slots=True)
class PairingCompleteBody:
    client_id: str
    client_name: str
    public_key: str
    public_key_algorithm: str
    confirmation_code: str
    expected_device_id: str | None


@dataclass(frozen=True, slots=True)
class UsbAutoTrustBody:
    client_id: str
    client_name: str
    public_key: str
    public_key_algorithm: str
    expected_device_id: str | None


class PairingRequestError(ValueError):
    """Raised when the pairing completion request body is invalid."""

    def __init__(self, message: str, *, error_code: str = "invalid_request") -> None:
        super().__init__(message)
        self.error_code = error_code


USB_AUTO_TRUST_LOCAL_PREFIX = "192.168.7."


def create_app(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    request_id_factory: RequestIdFactory = new_request_id,
    auth_verifier: SignedRequestVerifier | None = None,
    client_store: ClientStore | None = None,
    pairing_store: PairingWindowStore | None = None,
    environment: ManagerEnvironment | None = None,
    log_stream_source: LogStreamSource | None = None,
    support_bundle_dir: Path | None = None,
    support_bundle_sources: tuple[SupportBundleSource, ...] | None = None,
) -> web.Application:
    """Create the Phase 1 Bridge management API application."""

    app = web.Application(middlewares=[json_error_middleware])
    actual_client_store = client_store
    if actual_client_store is None and auth_verifier is not None:
        actual_client_store = auth_verifier.client_store
    if actual_client_store is None:
        actual_client_store = ClientStore(DEFAULT_CLIENTS_DIR)
    actual_auth_verifier = auth_verifier or SignedRequestVerifier(actual_client_store)

    app[CONFIG_PATH_KEY] = config_path
    app[REQUEST_ID_FACTORY_KEY] = request_id_factory
    app[CLIENT_STORE_KEY] = actual_client_store
    app[PAIRING_STORE_KEY] = pairing_store or PairingWindowStore()
    app[AUTH_VERIFIER_KEY] = actual_auth_verifier
    app[ENVIRONMENT_KEY] = environment or ManagerEnvironment.production()
    app[LOG_STREAM_SOURCE_KEY] = log_stream_source or LogStreamSource()
    app[SUPPORT_BUNDLE_DIR_KEY] = support_bundle_dir or DEFAULT_SUPPORT_BUNDLE_DIR
    app[SUPPORT_BUNDLE_SOURCES_KEY] = (
        support_bundle_sources
        if support_bundle_sources is not None
        else default_support_bundle_sources(Path("/"))
    )
    app.router.add_get("/v1/hello", handle_hello)
    app.router.add_get("/v1/pairing/status", handle_pairing_status)
    app.router.add_post("/v1/pairing/complete", handle_pairing_complete)
    app.router.add_post("/v1/pairing/usb_auto_trust", handle_pairing_usb_auto_trust)
    for route in ADMIN_ROUTES:
        app.router.add_route(route.method, route.path, auth_required_handler(route))
    return app


@web.middleware
async def json_error_middleware(
    request: web.Request,
    handler: Handler,
) -> web.StreamResponse:
    """Convert framework errors into the management JSON envelope."""

    request_id = request_id_for(request)
    try:
        response = await handler(request)
    except web.HTTPNotFound:
        response = json_failure(
            request,
            status=404,
            error_code="not_found",
            message="No management endpoint matches this request.",
            recommended_action="Use a supported /v1 management route.",
        )
    except web.HTTPMethodNotAllowed:
        response = json_failure(
            request,
            status=405,
            error_code="method_not_allowed",
            message="This management endpoint does not support the requested method.",
            recommended_action="Retry with one of the route's supported methods.",
        )
    except web.HTTPException as exc:
        response = json_failure(
            request,
            status=exc.status,
            error_code="http_error",
            message=exc.reason or "The management request failed.",
        )
    except Exception:
        LOGGER.exception("manager.request_failed path=%s", request.path)
        response = json_failure(
            request,
            status=500,
            error_code="internal_error",
            message="The management service could not complete this request.",
            recommended_action="Retry later or restart the Bridge management service.",
        )
    response.headers[REQUEST_ID_HEADER] = request_id
    return response


async def handle_hello(request: web.Request) -> web.Response:
    """Return unauthenticated discovery metadata."""

    return json_success(
        request,
        manager_status.collect_hello_payload(
            config_path_for(request),
            pairing_store=pairing_store_for(request),
        ),
    )


async def handle_pairing_status(request: web.Request) -> web.Response:
    """Return unauthenticated Phase 1 pairing state."""

    return json_success(
        request,
        manager_status.collect_pairing_status_payload(pairing_store_for(request)),
    )


async def handle_pairing_complete(request: web.Request) -> web.Response:
    """Complete physical-code-gated local authorization."""

    try:
        body = await read_pairing_complete_body(request)
    except PairingRequestError as exc:
        return json_failure(
            request,
            status=400,
            error_code=exc.error_code,
            message=str(exc),
            recommended_action="Retry with a valid pairing request body.",
        )
    if body.public_key_algorithm != "ed25519":
        return json_failure(
            request,
            status=400,
            error_code="unsupported_key_algorithm",
            message="Bridge management pairing currently requires Ed25519 client keys.",
            recommended_action="Retry pairing with an Ed25519 client key.",
        )

    if body.expected_device_id is not None:
        actual_device_id = manager_status.current_device_id()
        if body.expected_device_id != actual_device_id:
            return json_failure(
                request,
                status=409,
                error_code="device_id_mismatch",
                message="This pairing request targets a different Bridge device.",
                recommended_action="Refresh Bridge discovery and retry pairing with this device.",
            )

    try:
        client = AuthorizedClient(
            client_id=body.client_id,
            client_name=body.client_name,
            public_key=body.public_key,
            created_at=utc_timestamp(),
        )
    except ManagementAuthError as exc:
        return json_failure(
            request,
            status=400,
            error_code="invalid_request",
            message=str(exc),
            recommended_action="Retry with a valid client id, name, and Ed25519 public key.",
        )

    try:
        pairing_store_for(request).consume_window(body.confirmation_code)
    except PairingWindowError as exc:
        return json_failure(
            request,
            status=status_for_pairing_error(exc),
            error_code=exc.error_code,
            message=str(exc),
            recommended_action="Open Bridge access on the Bridge LCD, then retry pairing.",
        )

    client_store_for(request).save_client(client)
    return json_success(
        request,
        {
            "pairing_completion": {
                "paired": True,
                "client_id": client.client_id,
                "client_name": client.client_name,
                "public_key_algorithm": body.public_key_algorithm,
                "created_at": client.created_at,
            },
        },
    )


async def handle_pairing_usb_auto_trust(request: web.Request) -> web.Response:
    """USB-physical auto-trust: register a client over the USB-tether interface only.

    The gate is the local listening IP. A request that arrived on the bridge's own
    USB-bound listener has ``sockname[0] == "192.168.7.1"``; a Wi-Fi-bound request has
    ``sockname[0] == "192.168.8.1"``. This is non-spoofable because it is the bridge's
    own server socket address, not the peer's. Wi-Fi callers must use the LCD-code
    pairing window instead.
    """

    local_ip = local_listening_ip_for(request)
    peer = peer_address_for(request)
    if local_ip is None or not local_ip.startswith(USB_AUTO_TRUST_LOCAL_PREFIX):
        LOGGER.info(
            "ui.management.usb_auto_trust.rejected reason=not_usb_interface "
            "local_ip=%s peer=%s",
            local_ip,
            peer,
        )
        return json_failure(
            request,
            status=403,
            error_code="not_usb_interface",
            message="usb_auto_trust is only available on the USB-tether interface.",
            recommended_action="Connect the Bridge over USB or use LCD-code pairing instead.",
        )

    try:
        body = await read_usb_auto_trust_body(request)
    except PairingRequestError as exc:
        return json_failure(
            request,
            status=400,
            error_code=exc.error_code,
            message=str(exc),
            recommended_action="Retry with a valid usb_auto_trust request body.",
        )
    if body.public_key_algorithm != "ed25519":
        return json_failure(
            request,
            status=400,
            error_code="unsupported_key_algorithm",
            message="Bridge management pairing currently requires Ed25519 client keys.",
            recommended_action="Retry pairing with an Ed25519 client key.",
        )

    if body.expected_device_id is not None:
        actual_device_id = manager_status.current_device_id()
        if body.expected_device_id != actual_device_id:
            return json_failure(
                request,
                status=409,
                error_code="device_id_mismatch",
                message="This pairing request targets a different Bridge device.",
                recommended_action="Refresh Bridge discovery and retry pairing with this device.",
            )

    try:
        client = AuthorizedClient(
            client_id=body.client_id,
            client_name=body.client_name,
            public_key=body.public_key,
            created_at=utc_timestamp(),
        )
    except ManagementAuthError as exc:
        return json_failure(
            request,
            status=400,
            error_code="invalid_request",
            message=str(exc),
            recommended_action="Retry with a valid client id, name, and Ed25519 public key.",
        )

    client_store_for(request).save_client(client)
    LOGGER.info(
        "ui.management.usb_auto_trust client_id=%s display_name=%s peer=%s local_ip=%s",
        client.client_id,
        client.client_name,
        peer,
        local_ip,
    )
    return json_success(
        request,
        {
            "pairing_completion": {
                "paired": True,
                "client_id": client.client_id,
                "client_name": client.client_name,
                "public_key_algorithm": body.public_key_algorithm,
                "created_at": client.created_at,
            },
        },
    )


def local_listening_ip_for(request: web.Request) -> str | None:
    """Return the bridge's own listening IP for the request, or ``None`` if unavailable.

    ``request.transport.get_extra_info('sockname')`` returns the local server socket
    address, which is the bind host the manager was launched with (e.g.
    ``192.168.7.1`` for the USB gadget interface or ``192.168.8.1`` for Bridge Wi-Fi).
    """

    transport = request.transport
    if transport is None:
        return None
    sockname = transport.get_extra_info("sockname")
    if not sockname:
        return None
    candidate = sockname[0] if isinstance(sockname, tuple | list) else None
    if isinstance(candidate, str) and candidate:
        return candidate
    return None


def peer_address_for(request: web.Request) -> str | None:
    """Return the remote peer address for logs, or ``None`` if unavailable."""

    transport = request.transport
    if transport is None:
        return None
    peername = transport.get_extra_info("peername")
    if not peername:
        return None
    candidate = peername[0] if isinstance(peername, tuple | list) else None
    if isinstance(candidate, str) and candidate:
        return candidate
    return None


async def read_pairing_complete_body(request: web.Request) -> PairingCompleteBody:
    """Validate the JSON body for pairing completion."""

    try:
        value = await request.json()
    except ValueError as exc:
        raise PairingRequestError("Request body must be valid JSON.") from exc
    if not isinstance(value, dict):
        raise PairingRequestError("Request body must be a JSON object.")
    payload = cast(dict[str, Any], value)
    return PairingCompleteBody(
        client_id=required_body_str(payload, "client_id"),
        client_name=required_body_str(payload, "client_name"),
        public_key=required_body_str(payload, "public_key"),
        public_key_algorithm=optional_body_str(payload, "public_key_algorithm") or "ed25519",
        confirmation_code=required_body_str(payload, "confirmation_code"),
        expected_device_id=optional_body_str(payload, "expected_device_id"),
    )


async def read_usb_auto_trust_body(request: web.Request) -> UsbAutoTrustBody:
    """Validate the JSON body for USB-physical auto-trust pairing."""

    try:
        value = await request.json()
    except ValueError as exc:
        raise PairingRequestError("Request body must be valid JSON.") from exc
    if not isinstance(value, dict):
        raise PairingRequestError("Request body must be a JSON object.")
    payload = cast(dict[str, Any], value)
    return UsbAutoTrustBody(
        client_id=required_body_str(payload, "client_id"),
        client_name=required_body_str(payload, "client_name"),
        public_key=required_body_str(payload, "public_key"),
        public_key_algorithm=optional_body_str(payload, "public_key_algorithm") or "ed25519",
        expected_device_id=optional_body_str(payload, "expected_device_id"),
    )


def required_body_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PairingRequestError(f"{key} must be a non-empty string.")
    return value.strip()


def optional_body_str(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise PairingRequestError(f"{key} must be a non-empty string when provided.")
    return value.strip()


def status_for_pairing_error(exc: PairingWindowError) -> int:
    if exc.error_code == "pairing_not_open":
        return 423
    if exc.error_code == "pairing_expired":
        return 410
    if exc.error_code == "pairing_code_invalid":
        return 403
    if exc.error_code == "pairing_store_unavailable":
        return 503
    return 500


def auth_required_handler(route: ManagementRoute) -> Handler:
    """Return a signed admin handler for one management route."""

    async def handler(request: web.Request) -> web.StreamResponse:
        try:
            await verify_signed_request(request, request.app[AUTH_VERIFIER_KEY])
        except ManagementAuthError as exc:
            body = error_response(
                exc.error_code,
                str(exc),
                request_id=request_id_for(request),
                recommended_action="Pair this Mac with the Bridge before retrying.",
            )
            body["auth_required"] = True
            body["operation_id"] = route.operation_id
            return web.json_response(body, status=401)

        if route.operation_id == "status":
            return json_success(
                request,
                manager_status.collect_http_status_payload(config_path_for(request)),
            )

        admin_handler = ADMIN_OPERATION_HANDLERS.get(route.operation_id)
        if admin_handler is None:
            return json_failure(
                request,
                status=501,
                error_code="not_implemented",
                message="This management endpoint is not implemented yet.",
                recommended_action="Install a Bridge firmware that supports this operation.",
            )

        try:
            return await admin_handler(request)
        except UpdateFlowError as exc:
            return update_flow_failure(request, exc)

    return handler


async def handle_update_preflight(request: web.Request) -> web.Response:
    """Validate a firmware package without changing the install."""

    package = await read_package_body(request)
    payload = await asyncio.to_thread(run_preflight, environment_for(request), package)
    return json_success(request, payload)


async def handle_update_upload(request: web.Request) -> web.Response:
    """Store an uploaded firmware payload for a later install."""

    data = await request.read()
    filename = request.headers.get("X-Upload-Filename") or request.query.get("filename") or ""
    payload = await asyncio.to_thread(
        store_upload,
        environment_for(request),
        filename=filename,
        data=data,
    )
    return json_success(request, payload)


async def handle_update_install(request: web.Request) -> web.Response:
    """Back up and install a staged, verified firmware bundle."""

    package = await read_package_body(request)
    payload = await asyncio.to_thread(run_install, environment_for(request), package)
    return json_success(request, payload)


async def handle_update_status(request: web.Request) -> web.Response:
    """Return the persisted release-slot update state."""

    operation_id = request.query.get("operation_id")
    payload = await asyncio.to_thread(
        read_update_status,
        environment_for(request),
        operation_id,
    )
    return json_success(request, payload)


async def handle_update_mark_good(request: web.Request) -> web.Response:
    """Mark a pending release good after health gates pass."""

    payload = await asyncio.to_thread(run_mark_good, environment_for(request))
    return json_success(request, payload)


async def handle_update_rollback(request: web.Request) -> web.Response:
    """Roll back the current release to the previous one."""

    reason = await read_rollback_reason(request)
    payload = await asyncio.to_thread(run_rollback, environment_for(request), reason)
    return json_success(request, payload)


async def handle_backup_create(request: web.Request) -> web.Response:
    """Create and verify a configuration backup archive."""

    payload = await asyncio.to_thread(run_backup_create, environment_for(request))
    return json_success(request, payload)


async def handle_backup_restore(request: web.Request) -> web.Response:
    """Restore a previously created backup archive."""

    backup_id = await read_backup_id_body(request)
    payload = await asyncio.to_thread(
        run_backup_restore,
        environment_for(request),
        backup_id=backup_id,
    )
    return json_success(request, payload)


async def handle_logs_stream(request: web.Request) -> web.StreamResponse:
    """Stream redacted Bridge log entries as Server-Sent Events.

    The connection stays open until either the client disconnects or
    the underlying ``LogStreamSource`` exhausts. A short heartbeat
    comment is emitted whenever the source has no events available so
    intermediaries don't drop the connection.
    """

    response = web.StreamResponse(
        status=200,
        reason="OK",
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            REQUEST_ID_HEADER: request_id_for(request),
        },
    )
    await response.prepare(request)

    level = request.query.get("level")
    if level is not None and level.strip().lower() not in {"all", *DEFAULT_LOG_LEVELS}:
        await response.write(
            format_sse_event_error("invalid_level", "Unknown log level requested.")
        )
        await response.write_eof()
        return response

    source = request.app[LOG_STREAM_SOURCE_KEY]
    try:
        async for event in source.iter_events(level_filter=level):
            await response.write(format_sse_event(event))
    except ConnectionResetError:
        LOGGER.info("manager.logs_stream.client_disconnected")
    else:
        await response.write(format_sse_heartbeat())
    finally:
        try:
            await response.write_eof()
        except (ConnectionResetError, RuntimeError):
            # Client may already have closed; the stream is over either way.
            pass
    return response


def format_sse_event_error(error_code: str, message: str) -> bytes:
    """Encode an error as a single SSE record (used before stream end)."""

    payload = json.dumps(
        {"error_code": error_code, "message": message},
        separators=(",", ":"),
    )
    return f"event: error\ndata: {payload}\n\n".encode()


async def handle_support_bundle_create(request: web.Request) -> web.Response:
    """Stage a redacted support bundle and return its location metadata."""

    bundle_dir = request.app[SUPPORT_BUNDLE_DIR_KEY]
    sources = cast(
        "tuple[SupportBundleSource, ...]",
        request.app[SUPPORT_BUNDLE_SOURCES_KEY],
    )
    try:
        result = await asyncio.to_thread(
            create_support_bundle,
            bundles_dir=bundle_dir,
            sources=sources,
        )
    except FileNotFoundError as exc:
        LOGGER.exception("manager.support_bundle.required_source_missing")
        return json_failure(
            request,
            status=503,
            error_code="support_bundle_failed",
            message=str(exc),
            recommended_action=(
                "Retry once the Bridge has finished booting or fix the missing path."
            ),
        )
    except OSError as exc:
        LOGGER.exception("manager.support_bundle.write_failed")
        return json_failure(
            request,
            status=500,
            error_code="support_bundle_failed",
            message=str(exc),
            recommended_action="Check disk space and Bridge permissions, then retry.",
        )

    return json_success(
        request,
        {
            "support_bundle": {
                "schema_version": SUPPORT_BUNDLE_SCHEMA_VERSION,
                "bundle_id": result.bundle_id,
                "archive_path": str(result.archive_path),
                "size_bytes": result.size_bytes,
                "sha256": result.sha256,
                "contents": list(result.contents),
                "created_at": result.created_at,
            }
        },
    )


async def handle_config_get(request: web.Request) -> web.Response:
    """Return the current bridge config in the management JSON shape.

    Secrets (FTP password) are masked via ``serialize_config``; the Mac
    surfaces a "set"/"unset" indicator instead of the cleartext value.
    """

    snapshot = manager_status.read_config_snapshot(config_path_for(request))
    if snapshot.error_code is not None:
        return json_failure(
            request,
            status=503,
            error_code=snapshot.error_code,
            message=snapshot.message or "Config could not be loaded.",
            recommended_action="Fix the Bridge config file and restart the manager.",
        )
    return json_success(request, {"config": serialize_config(snapshot.config)})


async def handle_config_put(request: web.Request) -> web.Response:
    """Apply a partial config diff and persist it atomically.

    The diff payload is shaped ``{section: {field: value}}``; any section
    or field outside the editable surface listed in
    ``config_payload.ALLOWED_FIELDS`` is rejected with
    ``config_validation_failed``.
    """

    payload = await read_json_object_compatible(request)
    diff = payload.get("config")
    if diff is None:
        diff = {k: v for k, v in payload.items() if k != "schema_version"}
    if not isinstance(diff, dict):
        return json_failure(
            request,
            status=400,
            error_code="invalid_request",
            message="Request body must include a config diff object.",
            recommended_action="Send a JSON body with a 'config' object.",
        )

    snapshot = manager_status.read_config_snapshot(config_path_for(request))
    if snapshot.error_code is not None:
        return json_failure(
            request,
            status=503,
            error_code=snapshot.error_code,
            message=snapshot.message or "Config could not be loaded.",
            recommended_action="Fix the Bridge config file and restart the manager.",
        )

    try:
        new_config = apply_config_diff(snapshot.config, cast(dict[str, Any], diff))
    except ConfigValidationError as exc:
        return web.json_response(
            error_response(
                "config_validation_failed",
                "One or more configuration values are invalid.",
                request_id=request_id_for(request),
                recommended_action="Fix the highlighted fields and apply again.",
                details={"field_errors": cast("dict[str, Any]", exc.field_errors)},
            ),
            status=422,
        )
    except ValueError as exc:
        return json_failure(
            request,
            status=422,
            error_code="config_validation_failed",
            message=str(exc),
            recommended_action="Fix the configuration values and apply again.",
        )

    try:
        await asyncio.to_thread(write_config, new_config, config_path_for(request))
    except OSError as exc:
        LOGGER.exception("manager.config_put.write_failed")
        return json_failure(
            request,
            status=500,
            error_code="config_write_failed",
            message=str(exc),
            recommended_action="Check disk space and Bridge permissions, then retry.",
        )

    return json_success(request, {"config": serialize_config(new_config)})


async def read_json_object_compatible(request: web.Request) -> dict[str, Any]:
    """Parse a JSON object body, returning ``{}`` for an empty body.

    Unlike :func:`read_json_object`, this helper does not raise on an
    empty body — used by config PUT so an empty payload becomes a
    no-op rather than a 400.
    """

    body = await request.read()
    if not body:
        return {}
    try:
        value = await request.json()
    except ValueError as exc:
        raise UpdateFlowError(
            "invalid_request",
            "Request body must be valid JSON.",
            http_status=400,
            recommended_action="Send a valid JSON object body.",
        ) from exc
    if not isinstance(value, dict):
        raise UpdateFlowError(
            "invalid_request",
            "Request body must be a JSON object.",
            http_status=400,
            recommended_action="Send a JSON object body.",
        )
    return cast("dict[str, Any]", value)


ADMIN_OPERATION_HANDLERS: dict[str, AdminHandler] = {
    "config_get": handle_config_get,
    "config_put": handle_config_put,
    "update_preflight": handle_update_preflight,
    "update_upload": handle_update_upload,
    "update_install": handle_update_install,
    "update_status": handle_update_status,
    "update_mark_good": handle_update_mark_good,
    "update_rollback": handle_update_rollback,
    "backup_create": handle_backup_create,
    "backup_restore": handle_backup_restore,
    "logs_stream": handle_logs_stream,
    "support_bundle_create": handle_support_bundle_create,
}


async def read_package_body(request: web.Request) -> dict[str, Any]:
    """Read the firmware ``package`` object from a POST JSON body."""

    payload = await read_json_object(request)
    package = payload.get("package")
    if not isinstance(package, dict):
        raise UpdateFlowError(
            "invalid_request",
            "Request body must include a package object.",
            http_status=400,
            recommended_action="Send a JSON body with a package object.",
        )
    return cast("dict[str, Any]", package)


async def read_backup_id_body(request: web.Request) -> str:
    """Read the ``backup_id`` field from a POST JSON body."""

    payload = await read_json_object(request)
    backup_id = payload.get("backup_id")
    if not isinstance(backup_id, str) or not backup_id.strip():
        raise UpdateFlowError(
            "invalid_request",
            "Request body must include a backup_id.",
            http_status=400,
            recommended_action="Send a JSON body with a backup_id string.",
        )
    return backup_id


async def read_rollback_reason(request: web.Request) -> str:
    """Read the rollback ``reason`` from a POST JSON body, defaulting if absent."""

    body = await request.read()
    if not body:
        return "operator_requested"
    payload = await read_json_object(request)
    reason = payload.get("reason")
    if reason is None:
        return "operator_requested"
    if not isinstance(reason, str) or not reason.strip():
        raise UpdateFlowError(
            "invalid_request",
            "Rollback reason must be a non-empty string when provided.",
            http_status=400,
            recommended_action="Send a JSON body with a reason string or omit it.",
        )
    return reason


async def read_json_object(request: web.Request) -> dict[str, Any]:
    """Parse a JSON object body, raising a contract error on malformed input."""

    try:
        value = await request.json()
    except ValueError as exc:
        raise UpdateFlowError(
            "invalid_request",
            "Request body must be valid JSON.",
            http_status=400,
            recommended_action="Send a valid JSON object body.",
        ) from exc
    if not isinstance(value, dict):
        raise UpdateFlowError(
            "invalid_request",
            "Request body must be a JSON object.",
            http_status=400,
            recommended_action="Send a JSON object body.",
        )
    return cast("dict[str, Any]", value)


def update_flow_failure(request: web.Request, exc: UpdateFlowError) -> web.Response:
    """Translate an UpdateFlowError into the management error envelope."""

    return web.json_response(
        error_response(
            exc.error_code,
            exc.message,
            request_id=request_id_for(request),
            recommended_action=exc.recommended_action,
            details=exc.details,
            retry_after_seconds=exc.retry_after_seconds,
        ),
        status=exc.http_status,
    )


async def verify_signed_request(
    request: web.Request,
    verifier: SignedRequestVerifier,
) -> None:
    """Verify a signed request before an admin placeholder can be reached."""

    body = await request.read()
    verifier.verify(
        headers=request.headers,
        method=request.method,
        path=request.rel_url.path_qs,
        body=body,
    )


def json_success(request: web.Request, payload: JsonObject) -> web.Response:
    """Return a successful JSON response."""

    return web.json_response(success_response(payload, request_id=request_id_for(request)))


def json_failure(
    request: web.Request,
    *,
    status: int,
    error_code: str,
    message: str,
    recommended_action: str | None = None,
) -> web.Response:
    """Return a failed JSON response."""

    return web.json_response(
        error_response(
            error_code,
            message,
            request_id=request_id_for(request),
            recommended_action=recommended_action,
        ),
        status=status,
    )


def config_path_for(request: web.Request) -> Path:
    """Return the app's configured Bridge config path."""

    return request.app[CONFIG_PATH_KEY]


def client_store_for(request: web.Request) -> ClientStore:
    """Return the app's management client store."""

    return request.app[CLIENT_STORE_KEY]


def pairing_store_for(request: web.Request) -> PairingWindowStore:
    """Return the app's management pairing window store."""

    return request.app[PAIRING_STORE_KEY]


def environment_for(request: web.Request) -> ManagerEnvironment:
    """Return the app's update-orchestration environment."""

    return request.app[ENVIRONMENT_KEY]


def request_id_for(request: web.Request) -> str:
    """Return or create the request id for this HTTP request."""

    existing = request.get("request_id")
    if isinstance(existing, str):
        return existing
    header_value = request.headers.get(REQUEST_ID_HEADER)
    if header_value is not None and header_value.strip():
        request_id = header_value.strip()[:128]
    else:
        request_id_factory = cast(RequestIdFactory, request.app[REQUEST_ID_FACTORY_KEY])
        request_id = request_id_factory()
    request["request_id"] = request_id
    return request_id
