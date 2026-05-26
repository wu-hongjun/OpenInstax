from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest
from aiohttp.test_utils import TestClient, TestServer

from instantlink_bridge.manager import status as manager_status
from instantlink_bridge.manager.api import REQUEST_ID_HEADER, create_app
from instantlink_bridge.manager.auth import (
    CLIENT_ID_HEADER,
    NONCE_HEADER,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    AuthorizedClient,
    ClientStore,
    SignedRequestVerifier,
    canonical_request_payload,
    encode_base64url,
    public_key_text,
)
from instantlink_bridge.manager.cli import main as manager_main
from instantlink_bridge.manager.cli import validate_bind_hosts
from instantlink_bridge.manager.contract import ADMIN_ROUTES, SCHEMA_VERSION
from instantlink_bridge.system_info import SystemInfo

ed25519 = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")


def test_manager_cli_hello_json_is_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(manager_status, "read_system_info", fake_system_info)

    manager_main(
        ["--config", str(tmp_path / "missing.toml"), "hello", "--json"],
        request_id_factory=lambda: "req-cli",
    )

    output = capsys.readouterr().out
    data = json.loads(output)
    assert_success_envelope(data, request_id="req-cli")
    assert data["device"] == {
        "device_id": "IB-1234ABCD",
        "display_name": "InstantLink Bridge",
        "software_version": "9.8.7",
        "api_version": "v1",
        "management_public_key_fingerprint": None,
        "pairing_open": False,
        "network_labels": ["Bridge Wi-Fi", "USB debug", "Same-Wi-Fi"],
        "endpoint_url": None,
        "is_paired": False,
    }
    assert data["management"]["auth_implemented"] is False
    assert data["management"]["admin_routes"] == "auth_required"
    assert "change-me" not in output
    assert "password" not in output


def test_manager_cli_status_json_omits_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(manager_status, "read_system_info", fake_system_info)
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            (
                "[ftp]",
                'password = "super-secret"',
                'host = "192.168.7.1"',
                'hotspot_host = "192.168.8.1"',
                "",
                "[printer]",
                'device_name = "INSTAX-1N034655"',
                "",
            )
        ),
        encoding="utf-8",
    )

    manager_main(
        ["--config", str(config_path), "status", "--json"],
        request_id_factory=lambda: "req-status",
    )

    output = capsys.readouterr().out
    data = json.loads(output)
    assert_success_envelope(data, request_id="req-status")
    assert data["config"]["source"] == "file"
    assert data["network"]["ftp_receive"]["mode"] == "hotspot"
    assert data["printer"]["device_name"] == "INSTAX-1N034655"
    assert "super-secret" not in output
    assert "password" not in output


def test_manager_cli_api_routes_describes_auth_boundaries(
    capsys: pytest.CaptureFixture[str],
) -> None:
    manager_main(["api-routes", "--json"], request_id_factory=lambda: "req-routes")

    data = json.loads(capsys.readouterr().out)
    assert_success_envelope(data, request_id="req-routes")
    routes = {(route["method"], route["path"]): route for route in data["routes"]}
    assert routes[("GET", "/v1/hello")]["auth_required"] is False
    assert routes[("GET", "/v1/pairing/status")]["auth_required"] is False
    assert routes[("POST", "/v1/pairing/complete")]["auth_required"] is False
    assert routes[("GET", "/v1/status")]["auth_required"] is True
    assert routes[("POST", "/v1/update/install")]["auth_required"] is True


@pytest.mark.asyncio
async def test_manager_http_discovery_routes_are_unauthenticated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(manager_status, "read_system_info", fake_system_info)
    app = create_app(config_path=tmp_path / "missing.toml", request_id_factory=lambda: "req-http")
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        hello = await client.get("/v1/hello", headers={REQUEST_ID_HEADER: "req-hello"})
        hello_data = cast(dict[str, Any], await hello.json())
        assert hello.status == 200
        assert_success_envelope(hello_data, request_id="req-hello")
        assert hello_data["device"]["device_id"] == "IB-1234ABCD"

        pairing = await client.get("/v1/pairing/status")
        pairing_data = cast(dict[str, Any], await pairing.json())
        assert pairing.status == 200
        assert_success_envelope(pairing_data, request_id="req-http")
        assert pairing_data["pairing"]["open"] is False
        assert pairing_data["pairing"]["auth_implemented"] is False

        complete = await client.post("/v1/pairing/complete")
        complete_data = cast(dict[str, Any], await complete.json())
        assert complete.status == 423
        assert_error_envelope(complete_data, error_code="pairing_not_open")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_manager_http_admin_routes_are_auth_required(
    tmp_path: Path,
) -> None:
    app = create_app(config_path=tmp_path / "missing.toml", request_id_factory=request_ids())
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        for route in ADMIN_ROUTES:
            response = await client.request(route.method, route.path)
            data = cast(dict[str, Any], await response.json())
            assert response.status == 401
            assert_error_envelope(data, error_code="auth_required")
            assert data["auth_required"] is True
            assert data["operation_id"] == route.operation_id
            assert "recommended_action" in data
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_manager_http_admin_route_accepts_signed_request_then_reports_unimplemented(
    tmp_path: Path,
) -> None:
    private_key = ed25519.Ed25519PrivateKey.generate()
    store = ClientStore(tmp_path / "clients")
    store.save_client(
        AuthorizedClient(
            client_id="macbook",
            client_name="Test Mac",
            public_key=public_key_text(private_key.public_key()),
            created_at="2026-05-26T15:30:00Z",
        )
    )
    verifier = SignedRequestVerifier(store, now_seconds=lambda: 1000)
    app = create_app(
        config_path=tmp_path / "missing.toml",
        request_id_factory=lambda: "req-signed",
        auth_verifier=verifier,
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        path = "/v1/status"
        nonce = "nonce-0001"
        timestamp = 1000
        body_sha256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        signature = private_key.sign(
            canonical_request_payload(
                method="GET",
                path=path,
                body_sha256=body_sha256,
                timestamp=timestamp,
                nonce=nonce,
            )
        )
        response = await client.get(
            path,
            headers={
                CLIENT_ID_HEADER: "macbook",
                TIMESTAMP_HEADER: str(timestamp),
                NONCE_HEADER: nonce,
                SIGNATURE_HEADER: encode_base64url(signature),
            },
        )
        data = cast(dict[str, Any], await response.json())
        assert response.status == 501
        assert_error_envelope(data, error_code="not_implemented")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_manager_http_unknown_route_returns_json_error(tmp_path: Path) -> None:
    app = create_app(config_path=tmp_path / "missing.toml", request_id_factory=lambda: "req-404")
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.get("/v1/missing")
        data = cast(dict[str, Any], await response.json())
        assert response.status == 404
        assert_error_envelope(data, error_code="not_found", request_id="req-404")
    finally:
        await client.close()


def test_manager_cli_rejects_wildcard_bind_by_default() -> None:
    with pytest.raises(SystemExit, match="unsafe management bind"):
        validate_bind_hosts(("0.0.0.0",))

    validate_bind_hosts(("0.0.0.0",), allow_unsafe=True)


def assert_success_envelope(data: dict[str, Any], *, request_id: str) -> None:
    assert data["schema_version"] == SCHEMA_VERSION
    assert data["request_id"] == request_id
    assert data["ok"] is True


def assert_error_envelope(
    data: dict[str, Any],
    *,
    error_code: str,
    request_id: str | None = None,
) -> None:
    assert data["schema_version"] == SCHEMA_VERSION
    if request_id is not None:
        assert data["request_id"] == request_id
    assert data["ok"] is False
    assert data["error_code"] == error_code
    assert isinstance(data["message"], str)
    assert data["error"]["message"] == data["message"]


def fake_system_info() -> SystemInfo:
    return SystemInfo(
        device_id="IB-1234ABCD",
        app_version="9.8.7",
        python_version="3.11.9",
        bluez_version="5.82",
        os_version="Debian GNU/Linux 13 (trixie)",
    )


def request_ids() -> Callable[[], str]:
    count = 0

    def next_request_id() -> str:
        nonlocal count
        count += 1
        return f"req-admin-{count}"

    return next_request_id
