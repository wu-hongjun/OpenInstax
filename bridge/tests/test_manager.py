from __future__ import annotations

import hashlib
import json
import stat
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, cast

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
    PairingWindowStore,
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


class SigningPrivateKey(Protocol):
    def sign(self, data: bytes) -> bytes:
        ...


def test_manager_cli_hello_json_is_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(manager_status, "read_system_info", fake_system_info)

    manager_main(
        [
            "--config",
            str(tmp_path / "missing.toml"),
            "--pairing-window",
            str(tmp_path / "pairing.json"),
            "hello",
            "--json",
        ],
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
    assert data["management"]["auth_implemented"] is True
    assert data["management"]["admin_routes"] == "signed_request_required"
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
        [
            "--config",
            str(config_path),
            "--pairing-window",
            str(tmp_path / "pairing.json"),
            "status",
            "--json",
        ],
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


def test_http_status_payload_printer_carries_battery_charge_and_estimate_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The macOS management contract expects the printer object to expose battery percent, charge
    # state, the battery-life estimate, and a print status field. They default to None/False but
    # must always be present so the Swift BridgePrinterStatus decoder finds the keys.
    monkeypatch.setattr(manager_status, "read_system_info", fake_system_info)
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            (
                "[printer]",
                'device_name = "INSTAX-1N034655"',
                "",
            )
        ),
        encoding="utf-8",
    )

    payload = manager_status.collect_http_status_payload(config_path)
    printer = payload["status"]["printer"]

    assert printer["battery_percent"] is None
    assert printer["film_remaining"] is None
    assert printer["charging"] is None
    assert printer["battery_minutes_remaining"] is None
    assert printer["print_status"] is None
    assert printer["connected"] is False
    assert printer["busy"] is False


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
    pairing_store = PairingWindowStore(tmp_path / "pairing.json", now_seconds=lambda: 1000)
    app = create_app(
        config_path=tmp_path / "missing.toml",
        request_id_factory=lambda: "req-http",
        pairing_store=pairing_store,
    )
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
        assert pairing_data["pairing"]["auth_implemented"] is True

        private_key = ed25519.Ed25519PrivateKey.generate()
        complete = await client.post(
            "/v1/pairing/complete",
            json=pairing_body(
                public_key=public_key_text(private_key.public_key()),
                confirmation_code="123456",
            ),
        )
        complete_data = cast(dict[str, Any], await complete.json())
        assert complete.status == 423
        assert_error_envelope(complete_data, error_code="pairing_not_open")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_manager_http_pairing_complete_succeeds_and_stores_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(manager_status, "read_system_info", fake_system_info)
    private_key = ed25519.Ed25519PrivateKey.generate()
    client_store = ClientStore(tmp_path / "clients")
    pairing_store = PairingWindowStore(
        tmp_path / "pairing.json",
        now_seconds=lambda: 1000,
        confirmation_code_factory=lambda: "123456",
    )
    pairing_store.open_window()
    app = create_app(
        config_path=tmp_path / "missing.toml",
        request_id_factory=lambda: "req-pair",
        auth_verifier=SignedRequestVerifier(client_store, now_seconds=lambda: 1000),
        client_store=client_store,
        pairing_store=pairing_store,
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.post(
            "/v1/pairing/complete",
            json=pairing_body(
                public_key=public_key_text(private_key.public_key()),
                confirmation_code="123456",
                expected_device_id="IB-1234ABCD",
            ),
        )
        data = cast(dict[str, Any], await response.json())
        assert response.status == 200
        assert_success_envelope(data, request_id="req-pair")
        completion = data["pairing_completion"]
        assert completion == {
            "paired": True,
            "client_id": "macbook",
            "client_name": "Test Mac",
            "public_key_algorithm": "ed25519",
            "created_at": completion["created_at"],
        }
        response_text = json.dumps(data)
        assert "confirmation_code" not in response_text
        assert '"public_key"' not in response_text
        assert pairing_store.read_window() is None

        stored = client_store.read_client("macbook")
        assert stored.client_name == "Test Mac"
        assert stored.public_key == public_key_text(private_key.public_key())
        assert stat.S_IMODE(client_store.root.stat().st_mode) == 0o700
        assert stat.S_IMODE(client_store.client_path("macbook").stat().st_mode) == 0o600

        admin_response = await client.get("/v1/status", headers=signed_headers(private_key))
        admin_data = cast(dict[str, Any], await admin_response.json())
        assert admin_response.status == 200
        assert_success_envelope(admin_data, request_id="req-pair")
        assert admin_data["status"]["device_id"] == "IB-1234ABCD"
        assert admin_data["status"]["active_upload_mode"] == "bridge_wifi"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_manager_http_pairing_complete_rejects_unsupported_key_algorithm(
    tmp_path: Path,
) -> None:
    private_key = ed25519.Ed25519PrivateKey.generate()
    client_store = ClientStore(tmp_path / "clients")
    pairing_store = PairingWindowStore(
        tmp_path / "pairing.json",
        now_seconds=lambda: 1000,
        confirmation_code_factory=lambda: "123456",
    )
    pairing_store.open_window()
    app = create_app(
        config_path=tmp_path / "missing.toml",
        request_id_factory=lambda: "req-bad-alg",
        client_store=client_store,
        pairing_store=pairing_store,
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        body = pairing_body(
            public_key=public_key_text(private_key.public_key()),
            confirmation_code="123456",
        )
        body["public_key_algorithm"] = "p256_sha256"
        response = await client.post("/v1/pairing/complete", json=body)
        data = cast(dict[str, Any], await response.json())
        assert response.status == 400
        assert_error_envelope(data, error_code="unsupported_key_algorithm")
        assert pairing_store.read_window() is not None
        assert not client_store.client_path("macbook").exists()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_manager_http_pairing_complete_rejects_wrong_code(tmp_path: Path) -> None:
    private_key = ed25519.Ed25519PrivateKey.generate()
    client_store = ClientStore(tmp_path / "clients")
    pairing_store = PairingWindowStore(
        tmp_path / "pairing.json",
        now_seconds=lambda: 1000,
        confirmation_code_factory=lambda: "123456",
    )
    pairing_store.open_window()
    app = create_app(
        config_path=tmp_path / "missing.toml",
        request_id_factory=lambda: "req-wrong-code",
        client_store=client_store,
        pairing_store=pairing_store,
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.post(
            "/v1/pairing/complete",
            json=pairing_body(
                public_key=public_key_text(private_key.public_key()),
                confirmation_code="000000",
            ),
        )
        data = cast(dict[str, Any], await response.json())
        assert response.status == 403
        assert_error_envelope(data, error_code="pairing_code_invalid")
        assert pairing_store.read_window() is not None
        assert not client_store.client_path("macbook").exists()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_manager_http_pairing_complete_rejects_expired_window(tmp_path: Path) -> None:
    now = 1000

    def now_seconds() -> int:
        return now

    private_key = ed25519.Ed25519PrivateKey.generate()
    client_store = ClientStore(tmp_path / "clients")
    pairing_store = PairingWindowStore(
        tmp_path / "pairing.json",
        now_seconds=now_seconds,
        confirmation_code_factory=lambda: "123456",
    )
    pairing_store.open_window()
    now = 1090
    app = create_app(
        config_path=tmp_path / "missing.toml",
        request_id_factory=lambda: "req-expired",
        client_store=client_store,
        pairing_store=pairing_store,
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.post(
            "/v1/pairing/complete",
            json=pairing_body(
                public_key=public_key_text(private_key.public_key()),
                confirmation_code="123456",
            ),
        )
        data = cast(dict[str, Any], await response.json())
        assert response.status == 410
        assert_error_envelope(data, error_code="pairing_expired")
        assert pairing_store.read_window() is None
        assert not client_store.client_path("macbook").exists()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_manager_http_pairing_complete_rejects_expected_device_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(manager_status, "read_system_info", fake_system_info)
    private_key = ed25519.Ed25519PrivateKey.generate()
    client_store = ClientStore(tmp_path / "clients")
    pairing_store = PairingWindowStore(
        tmp_path / "pairing.json",
        now_seconds=lambda: 1000,
        confirmation_code_factory=lambda: "123456",
    )
    pairing_store.open_window()
    app = create_app(
        config_path=tmp_path / "missing.toml",
        request_id_factory=lambda: "req-device-mismatch",
        client_store=client_store,
        pairing_store=pairing_store,
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.post(
            "/v1/pairing/complete",
            json=pairing_body(
                public_key=public_key_text(private_key.public_key()),
                confirmation_code="123456",
                expected_device_id="IB-DIFFERENT",
            ),
        )
        data = cast(dict[str, Any], await response.json())
        assert response.status == 409
        assert_error_envelope(data, error_code="device_id_mismatch")
        assert pairing_store.read_window() is not None
        assert not client_store.client_path("macbook").exists()
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
async def test_manager_http_admin_status_route_accepts_signed_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(manager_status, "read_system_info", fake_system_info)
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
        response = await client.get(
            path,
            headers=signed_headers(private_key, path=path),
        )
        data = cast(dict[str, Any], await response.json())
        assert response.status == 200
        assert_success_envelope(data, request_id="req-signed")
        assert data["status"]["device_id"] == "IB-1234ABCD"
        assert data["status"]["display_name"] == "InstantLink Bridge"
        assert data["status"]["bridge_version"] == "9.8.7"
        assert data["status"]["api_version"] == "v1"
        assert data["status"]["readiness"] == "ready"
        assert data["status"]["active_upload_mode"] == "bridge_wifi"
        assert data["status"]["network"] == {
            "mode": "bridge_wifi",
            "label": "Bridge Wi-Fi",
            "address": "192.168.8.1",
            "connected": True,
        }
        assert data["status"]["update"]["phase"] == "idle"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_manager_http_admin_route_rejects_replayed_signed_request(
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
        request_id_factory=request_ids(),
        auth_verifier=verifier,
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        headers = signed_headers(private_key, path="/v1/status")
        first = await client.get("/v1/status", headers=headers)
        first_data = cast(dict[str, Any], await first.json())
        assert first.status == 200
        assert_success_envelope(first_data, request_id="req-admin-1")

        second = await client.get("/v1/status", headers=headers)
        second_data = cast(dict[str, Any], await second.json())
        assert second.status == 401
        assert_error_envelope(second_data, error_code="replay")
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_manager_http_admin_route_rejects_revoked_client(
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
            revoked_at="2026-05-26T16:00:00Z",
        )
    )
    verifier = SignedRequestVerifier(store, now_seconds=lambda: 1000)
    app = create_app(
        config_path=tmp_path / "missing.toml",
        request_id_factory=lambda: "req-revoked",
        auth_verifier=verifier,
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.get("/v1/status", headers=signed_headers(private_key))
        data = cast(dict[str, Any], await response.json())
        assert response.status == 401
        assert_error_envelope(data, error_code="client_revoked")
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


def test_manager_cli_pairing_open_close_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pairing_path = tmp_path / "management" / "pairing.json"

    manager_main(
        [
            "--pairing-window",
            str(pairing_path),
            "pairing-open",
            "--ttl-seconds",
            "45",
            "--json",
        ],
        request_id_factory=lambda: "req-open",
        now_seconds=lambda: 1000,
        confirmation_code_factory=lambda: "654321",
    )

    open_data = json.loads(capsys.readouterr().out)
    assert_success_envelope(open_data, request_id="req-open")
    assert open_data["pairing"]["open"] is True
    assert open_data["pairing"]["confirmation_code"] == "654321"
    assert open_data["pairing"]["expires_at"] == 1045
    assert stat.S_IMODE(pairing_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(pairing_path.stat().st_mode) == 0o600

    manager_main(
        ["--pairing-window", str(pairing_path), "pairing-close", "--json"],
        request_id_factory=lambda: "req-close",
        now_seconds=lambda: 1000,
        confirmation_code_factory=lambda: "654321",
    )

    close_data = json.loads(capsys.readouterr().out)
    assert_success_envelope(close_data, request_id="req-close")
    assert close_data["pairing"]["open"] is False
    assert not pairing_path.exists()


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


def pairing_body(
    *,
    public_key: str,
    confirmation_code: str,
    expected_device_id: str | None = None,
) -> dict[str, str]:
    body = {
        "client_id": "macbook",
        "client_name": "Test Mac",
        "public_key": public_key,
        "confirmation_code": confirmation_code,
    }
    if expected_device_id is not None:
        body["expected_device_id"] = expected_device_id
    return body


def signed_headers(
    private_key: SigningPrivateKey,
    *,
    method: str = "GET",
    path: str = "/v1/status",
    body: bytes = b"",
    timestamp: int = 1000,
    nonce: str = "nonce-0001",
    client_id: str = "macbook",
) -> dict[str, str]:
    signature = private_key.sign(
        canonical_request_payload(
            method=method,
            path=path,
            body_sha256=hashlib.sha256(body).hexdigest(),
            timestamp=timestamp,
            nonce=nonce,
        )
    )
    return {
        CLIENT_ID_HEADER: client_id,
        TIMESTAMP_HEADER: str(timestamp),
        NONCE_HEADER: nonce,
        SIGNATURE_HEADER: encode_base64url(signature),
    }
