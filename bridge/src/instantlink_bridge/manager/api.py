"""aiohttp management API app factory."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import cast

from aiohttp import web

from instantlink_bridge.config import DEFAULT_CONFIG_PATH
from instantlink_bridge.manager.auth import ManagementAuthError, SignedRequestVerifier
from instantlink_bridge.manager.contract import (
    ADMIN_ROUTES,
    JsonObject,
    ManagementRoute,
    error_response,
    new_request_id,
    success_response,
)
from instantlink_bridge.manager.status import (
    collect_hello_payload,
    collect_pairing_status_payload,
)

LOGGER = logging.getLogger(__name__)
REQUEST_ID_HEADER = "X-Request-Id"

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]
RequestIdFactory = Callable[[], str]
CONFIG_PATH_KEY = web.AppKey("instantlink_bridge.manager.config_path", Path)
REQUEST_ID_FACTORY_KEY = web.AppKey("instantlink_bridge.manager.request_id_factory", object)
AUTH_VERIFIER_KEY = web.AppKey("instantlink_bridge.manager.auth_verifier", SignedRequestVerifier)


def create_app(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    request_id_factory: RequestIdFactory = new_request_id,
    auth_verifier: SignedRequestVerifier | None = None,
) -> web.Application:
    """Create the Phase 1 Bridge management API application."""

    app = web.Application(middlewares=[json_error_middleware])
    app[CONFIG_PATH_KEY] = config_path
    app[REQUEST_ID_FACTORY_KEY] = request_id_factory
    if auth_verifier is not None:
        app[AUTH_VERIFIER_KEY] = auth_verifier
    app.router.add_get("/v1/hello", handle_hello)
    app.router.add_get("/v1/pairing/status", handle_pairing_status)
    app.router.add_post("/v1/pairing/complete", handle_pairing_complete)
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

    return json_success(request, collect_hello_payload(config_path_for(request)))


async def handle_pairing_status(request: web.Request) -> web.Response:
    """Return unauthenticated Phase 1 pairing state."""

    return json_success(request, collect_pairing_status_payload())


async def handle_pairing_complete(request: web.Request) -> web.Response:
    """Return a stable placeholder until LCD-approved pairing is implemented."""

    return json_failure(
        request,
        status=423,
        error_code="pairing_not_open",
        message="Bridge access is not open for this Mac.",
        recommended_action="Open Bridge access on the Bridge LCD, then retry pairing.",
    )


def auth_required_handler(route: ManagementRoute) -> Handler:
    """Return a placeholder handler for routes that will require signed auth."""

    async def handler(request: web.Request) -> web.Response:
        verifier = request.app.get(AUTH_VERIFIER_KEY)
        if verifier is not None:
            try:
                await verify_signed_request(request, verifier)
            except ManagementAuthError as exc:
                return json_failure(
                    request,
                    status=401,
                    error_code=exc.error_code,
                    message=str(exc),
                    recommended_action="Pair this Mac with the Bridge before retrying.",
                )
            return json_failure(
                request,
                status=501,
                error_code="not_implemented",
                message="This management endpoint is not implemented yet.",
                recommended_action="Install a Bridge firmware that supports this operation.",
            )

        body = error_response(
            "auth_required",
            "Authentication is required for this management endpoint.",
            request_id=request_id_for(request),
            recommended_action="Pair this Mac with the Bridge before retrying.",
        )
        body["auth_required"] = True
        body["operation_id"] = route.operation_id
        return web.json_response(body, status=401)

    return handler


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
