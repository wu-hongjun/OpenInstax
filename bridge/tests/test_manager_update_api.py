from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, cast

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

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
from instantlink_bridge.manager.health import (
    BridgeHealthGate,
    HealthCheckContext,
    HealthGateResult,
    HealthProbe,
)
from instantlink_bridge.manager.installer import (
    UPDATE_STATE_FILE_NAME,
    PrivilegedCommand,
)
from instantlink_bridge.manager.release_slots import (
    ensure_release_slot_layout,
    read_rollback_state,
    release_symlink_target,
)
from instantlink_bridge.manager.update_flow import ManagerEnvironment

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )

ed25519 = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")


class SigningPrivateKey(Protocol):
    def sign(self, data: bytes) -> bytes: ...

    def public_key(self) -> Ed25519PublicKey: ...


# --- shared fixtures / helpers ---------------------------------------------


def _private_key() -> Ed25519PrivateKey:
    key: Ed25519PrivateKey = ed25519.Ed25519PrivateKey.generate()
    return key


def _verifier(tmp_path: Path, private_key: SigningPrivateKey) -> SignedRequestVerifier:
    store = ClientStore(tmp_path / "clients")
    store.save_client(
        AuthorizedClient(
            client_id="macbook",
            client_name="Test Mac",
            public_key=public_key_text(private_key.public_key()),
            created_at="2026-05-26T15:30:00Z",
        )
    )
    return SignedRequestVerifier(store, now_seconds=lambda: 1000)


def _make_app(
    tmp_path: Path,
    private_key: SigningPrivateKey,
    environment: ManagerEnvironment,
    *,
    request_id: str = "req-update",
) -> web.Application:
    return create_app(
        config_path=tmp_path / "missing.toml",
        request_id_factory=lambda: request_id,
        auth_verifier=_verifier(tmp_path, private_key),
        environment=environment,
    )


def signed_headers(
    private_key: SigningPrivateKey,
    *,
    method: str,
    path: str,
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


def _json_body(body: Mapping[str, Any]) -> bytes:
    return json.dumps(body).encode()


def _firmware_package(*, target: str = "linux-aarch64", version: str = "0.2.0") -> dict[str, Any]:
    return {
        "package_kind": "instantlink_bridge_firmware",
        "version": version,
        "target": target,
        "archive_url": "https://example.invalid/firmware.tar.gz",
        "archive_sha256": "a" * 64,
        "manifest_url": "https://example.invalid/manifest.json",
        "manifest_sha256": "b" * 64,
        "checksum_url": "https://example.invalid/SHA256SUMS",
    }


def write_firmware_bundle(tmp_path: Path) -> Path:
    bundle = tmp_path / "bundle"
    bridge_dir = bundle / "bridge"
    native_bin = bundle / "native" / "bin"
    native_lib = bundle / "native" / "lib"
    bridge_dir.mkdir(parents=True)
    native_bin.mkdir(parents=True)
    native_lib.mkdir(parents=True)

    (bridge_dir / "pyproject.toml").write_text(
        '[project]\nname = "instantlink-bridge"\n',
        encoding="utf-8",
    )
    instantlink = native_bin / "instantlink"
    instantlink.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    instantlink.chmod(0o755)
    ffi = native_lib / "libinstantlink_ffi.so"
    ffi.write_bytes(b"ffi")
    artifacts_manifest = bundle / "native" / "instantlink-artifacts-manifest.json"
    artifacts_manifest.write_text('{"schema_version": 1}\n', encoding="utf-8")

    manifest = {
        "schema_version": 1,
        "package_kind": "instantlink_bridge_firmware",
        "bridge_version": "0.2.0",
        "source_ref": "v0.2.0",
        "built_at_utc": "2026-05-26T15:30:00Z",
        "required_bridge_api_version": 1,
        "minimum_rollback_version": None,
        "migration_notes": [],
        "instantlink_workspace": {
            "commit_sha": "1" * 40,
            "branch": "main",
            "dirty": False,
        },
        "target": {
            "platform": "linux",
            "architecture": "aarch64",
            "rust_triple": "aarch64-unknown-linux-gnu",
        },
        "archive": {
            "name": "InstantLinkBridgeFirmware-v0.2.0-linux-aarch64.tar.gz",
            "compression": "gzip",
        },
        "python": {
            "package": "instantlink-bridge",
            "constraints": "bridge/requirements/constraints.txt",
        },
        "native_artifacts": {
            "instantlink": {
                "path": "native/bin/instantlink",
                "sha256": _sha256_file(instantlink),
            },
            "libinstantlink_ffi.so": {
                "path": "native/lib/libinstantlink_ffi.so",
                "sha256": _sha256_file(ffi),
            },
            "build_manifest": {
                "path": "native/instantlink-artifacts-manifest.json",
                "sha256": _sha256_file(artifacts_manifest),
            },
        },
        "install": {
            "script": "install-firmware-bundle.sh",
            "default_target": "/opt/InstantLinkBridge",
        },
    }
    (bundle / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (bundle / "SHA256SUMS").write_text("verified elsewhere\n", encoding="utf-8")
    install_script = bundle / "install-firmware-bundle.sh"
    install_script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    install_script.chmod(0o755)
    return bundle


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _path_exists(value: str) -> bool:
    return Path(value).exists()


class RecordingRunner:
    def __init__(self) -> None:
        self.commands: list[tuple[str, ...]] = []

    def run(self, command: PrivilegedCommand) -> None:
        self.commands.append(command.argv)


def _passing_probes() -> Mapping[BridgeHealthGate, HealthProbe]:
    def make(gate: BridgeHealthGate) -> HealthProbe:
        def probe(_context: HealthCheckContext) -> HealthGateResult:
            return HealthGateResult.passed(gate)

        return probe

    return {gate: make(gate) for gate in BridgeHealthGate}


def _failing_probes() -> Mapping[BridgeHealthGate, HealthProbe]:
    probes = dict(_passing_probes())

    def failing(_context: HealthCheckContext) -> HealthGateResult:
        return HealthGateResult.failed(
            BridgeHealthGate.RUNTIME_SERVICE_STABLE,
            "service_restarting",
        )

    probes[BridgeHealthGate.RUNTIME_SERVICE_STABLE] = failing
    return probes


def _stage_install_bundle(tmp_path: Path) -> tuple[Path, RecordingRunner]:
    """Return (install_root, runner) with a staged firmware bundle ready.

    The install root pre-seeds a current release so the previous link is
    populated after install, and a backup-able config file so the pre-update
    backup succeeds.
    """

    install_root = tmp_path / "InstantLinkBridge"
    bundle = write_firmware_bundle(tmp_path)
    layout = ensure_release_slot_layout(install_root)
    old_release = "2026-05-24T153000Z-v0.1.5"
    (layout.releases_dir / old_release).mkdir()
    layout.current_link.symlink_to(release_symlink_target(old_release))
    staged_dir = install_root / "shared" / "uploads" / "staged"
    staged_dir.parent.mkdir(parents=True, exist_ok=True)
    bundle.rename(staged_dir)
    config_path = install_root / "etc" / "InstantLinkBridge" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("quality = 100\n", encoding="utf-8")
    return install_root, RecordingRunner()


# --- preflight --------------------------------------------------------------


@pytest.mark.asyncio
async def test_preflight_allows_valid_package_with_previous_release(tmp_path: Path) -> None:
    private_key = _private_key()
    install_root = tmp_path / "InstantLinkBridge"
    layout = ensure_release_slot_layout(install_root)
    previous = "2026-05-20T101500Z-v0.1.0"
    current = "2026-05-24T153000Z-v0.1.5"
    for release_id in (previous, current):
        (layout.releases_dir / release_id).mkdir()
    layout.previous_link.symlink_to(release_symlink_target(previous))
    layout.current_link.symlink_to(release_symlink_target(current))
    env = ManagerEnvironment(install_root=install_root, backups_dir=tmp_path / "backups")
    app = _make_app(tmp_path, private_key, env, request_id="req-preflight")

    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        package = _firmware_package()
        body = _json_body({"package": package})
        path = "/v1/update/preflight"
        response = await client.post(
            path,
            data=body,
            headers={
                **signed_headers(private_key, method="POST", path=path, body=body),
                "Content-Type": "application/json",
            },
        )
        data = cast(dict[str, Any], await response.json())
        assert response.status == 200
        preflight = data["preflight"]
        assert preflight["allowed"] is True
        assert preflight["backup_required"] is True
        assert preflight["rollback_available"] is True
        assert preflight["operation_id"] == "0.2.0"
        assert preflight["package"] == package
        names = {check["name"] for check in preflight["checks"]}
        assert names == {
            "package_kind",
            "target_arch",
            "rollback_available",
            "no_active_operation",
        }
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_preflight_rejects_wrong_target(tmp_path: Path) -> None:
    private_key = _private_key()
    install_root = tmp_path / "InstantLinkBridge"
    ensure_release_slot_layout(install_root)
    env = ManagerEnvironment(install_root=install_root, backups_dir=tmp_path / "backups")
    app = _make_app(tmp_path, private_key, env)

    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        package = _firmware_package(target="darwin-arm64")
        body = _json_body({"package": package})
        path = "/v1/update/preflight"
        response = await client.post(
            path,
            data=body,
            headers={
                **signed_headers(private_key, method="POST", path=path, body=body),
                "Content-Type": "application/json",
            },
        )
        data = cast(dict[str, Any], await response.json())
        assert response.status == 200
        preflight = data["preflight"]
        assert preflight["allowed"] is False
        target_check = next(c for c in preflight["checks"] if c["name"] == "target_arch")
        assert target_check["status"] == "fail"
    finally:
        await client.close()


# --- install ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_install_creates_backup_and_pending_state(tmp_path: Path) -> None:
    private_key = _private_key()
    install_root, runner = _stage_install_bundle(tmp_path)
    backups_dir = tmp_path / "backups"
    env = ManagerEnvironment(
        install_root=install_root,
        backups_dir=backups_dir,
        privileged_runner=runner,
        now=lambda: "2026-05-26T15:30:00Z",
    )
    # The backup reads from install_root; ensure a backup-able file exists there.
    app = _make_app(tmp_path, private_key, env, request_id="req-install")

    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        package = _firmware_package()
        body = _json_body({"package": package})
        path = "/v1/update/install"
        response = await client.post(
            path,
            data=body,
            headers={
                **signed_headers(private_key, method="POST", path=path, body=body),
                "Content-Type": "application/json",
            },
        )
        data = cast(dict[str, Any], await response.json())
        assert response.status == 200, data
        update = data["update"]
        assert update["phase"] == "pending_verification"
        assert update["safe_state"] == "installed"
        assert update["operation_id"] == "2026-05-26T153000Z-v0.2.0"

        state = read_rollback_state(install_root / UPDATE_STATE_FILE_NAME)
        assert state.status.value == "pending_verification"
        assert state.active_release == "2026-05-26T153000Z-v0.2.0"
        assert list(backups_dir.glob("*.tar.gz"))
        assert ("systemctl", "daemon-reload") in runner.commands
    finally:
        await client.close()


# --- update status ----------------------------------------------------------


@pytest.mark.asyncio
async def test_update_status_idle_when_no_state(tmp_path: Path) -> None:
    private_key = _private_key()
    install_root = tmp_path / "InstantLinkBridge"
    ensure_release_slot_layout(install_root)
    env = ManagerEnvironment(install_root=install_root, backups_dir=tmp_path / "backups")
    app = _make_app(tmp_path, private_key, env, request_id="req-status")

    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        path = "/v1/update/status"
        response = await client.get(
            path,
            headers=signed_headers(private_key, method="GET", path=path),
        )
        data = cast(dict[str, Any], await response.json())
        assert response.status == 200
        assert data["update"]["phase"] == "idle"
        assert data["update"]["safe_state"] == "unknown"
        assert data["update"]["operation_id"] == "idle"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_update_status_reports_pending_after_install(tmp_path: Path) -> None:
    private_key = _private_key()
    install_root, runner = _stage_install_bundle(tmp_path)
    env = ManagerEnvironment(
        install_root=install_root,
        backups_dir=tmp_path / "backups",
        privileged_runner=runner,
        now=lambda: "2026-05-26T15:30:00Z",
    )
    app = _make_app(tmp_path, private_key, env, request_id="req-status2")

    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        install_body = _json_body({"package": _firmware_package()})
        install_path = "/v1/update/install"
        await client.post(
            install_path,
            data=install_body,
            headers={
                **signed_headers(private_key, method="POST", path=install_path, body=install_body),
                "Content-Type": "application/json",
            },
        )

        status_path = "/v1/update/status"
        response = await client.get(
            status_path,
            headers=signed_headers(private_key, method="GET", path=status_path, nonce="nonce-0002"),
        )
        data = cast(dict[str, Any], await response.json())
        assert response.status == 200
        assert data["update"]["phase"] == "pending_verification"
    finally:
        await client.close()


# --- mark good --------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_good_marks_done_when_gates_pass(tmp_path: Path) -> None:
    private_key = _private_key()
    install_root, runner = _stage_install_bundle(tmp_path)
    env = ManagerEnvironment(
        install_root=install_root,
        backups_dir=tmp_path / "backups",
        privileged_runner=runner,
        now=lambda: "2026-05-26T15:30:00Z",
        health_probes=_passing_probes,
    )
    app = _make_app(tmp_path, private_key, env, request_id="req-markgood")

    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        install_body = _json_body({"package": _firmware_package()})
        install_path = "/v1/update/install"
        await client.post(
            install_path,
            data=install_body,
            headers={
                **signed_headers(private_key, method="POST", path=install_path, body=install_body),
                "Content-Type": "application/json",
            },
        )

        path = "/v1/update/mark-good"
        response = await client.post(
            path,
            data=b"",
            headers=signed_headers(private_key, method="POST", path=path, nonce="nonce-0002"),
        )
        data = cast(dict[str, Any], await response.json())
        assert response.status == 200, data
        assert data["update"]["phase"] == "done"
        assert data["update"]["safe_state"] == "installed"
        state = read_rollback_state(install_root / UPDATE_STATE_FILE_NAME)
        assert state.status.value == "good"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_mark_good_fails_when_gates_fail(tmp_path: Path) -> None:
    private_key = _private_key()
    install_root, runner = _stage_install_bundle(tmp_path)
    env = ManagerEnvironment(
        install_root=install_root,
        backups_dir=tmp_path / "backups",
        privileged_runner=runner,
        now=lambda: "2026-05-26T15:30:00Z",
        health_probes=_failing_probes,
    )
    app = _make_app(tmp_path, private_key, env, request_id="req-markbad")

    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        install_body = _json_body({"package": _firmware_package()})
        install_path = "/v1/update/install"
        await client.post(
            install_path,
            data=install_body,
            headers={
                **signed_headers(private_key, method="POST", path=install_path, body=install_body),
                "Content-Type": "application/json",
            },
        )

        path = "/v1/update/mark-good"
        response = await client.post(
            path,
            data=b"",
            headers=signed_headers(private_key, method="POST", path=path, nonce="nonce-0002"),
        )
        data = cast(dict[str, Any], await response.json())
        assert response.status == 409
        assert data["error_code"] == "health_gates_failed"
        state = read_rollback_state(install_root / UPDATE_STATE_FILE_NAME)
        assert state.status.value == "pending_verification"
    finally:
        await client.close()


# --- rollback ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_rollback_restores_previous_release(tmp_path: Path) -> None:
    private_key = _private_key()
    install_root, runner = _stage_install_bundle(tmp_path)
    env = ManagerEnvironment(
        install_root=install_root,
        backups_dir=tmp_path / "backups",
        privileged_runner=runner,
        now=lambda: "2026-05-26T15:30:00Z",
    )
    app = _make_app(tmp_path, private_key, env, request_id="req-rollback")

    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        install_body = _json_body({"package": _firmware_package()})
        install_path = "/v1/update/install"
        await client.post(
            install_path,
            data=install_body,
            headers={
                **signed_headers(private_key, method="POST", path=install_path, body=install_body),
                "Content-Type": "application/json",
            },
        )

        path = "/v1/update/rollback"
        response = await client.post(
            path,
            data=b"",
            headers=signed_headers(private_key, method="POST", path=path, nonce="nonce-0002"),
        )
        data = cast(dict[str, Any], await response.json())
        assert response.status == 200, data
        assert data["update"]["phase"] == "rolled_back"
        assert data["update"]["safe_state"] == "previous_version_restored"
    finally:
        await client.close()


# --- backup create / restore ------------------------------------------------


@pytest.mark.asyncio
async def test_backup_create_produces_verified_archive(tmp_path: Path) -> None:
    private_key = _private_key()
    install_root = tmp_path / "InstantLinkBridge"
    install_root.mkdir()
    (install_root / "etc" / "InstantLinkBridge").mkdir(parents=True)
    (install_root / "etc" / "InstantLinkBridge" / "config.toml").write_text(
        "quality = 100\n", encoding="utf-8"
    )
    backups_dir = tmp_path / "backups"
    env = ManagerEnvironment(
        install_root=install_root,
        backups_dir=backups_dir,
        now=lambda: "2026-05-26T15:30:00Z",
    )
    app = _make_app(tmp_path, private_key, env, request_id="req-bcreate")

    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        path = "/v1/backup/create"
        response = await client.post(
            path,
            data=b"",
            headers=signed_headers(private_key, method="POST", path=path),
        )
        data = cast(dict[str, Any], await response.json())
        assert response.status == 200, data
        backup = data["backup"]
        assert backup["verified"] is True
        assert _path_exists(backup["archive_path"])
        assert _path_exists(backup["manifest_path"])
        assert len(backup["archive_sha256"]) == 64
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_backup_restore_restores_files(tmp_path: Path) -> None:
    private_key = _private_key()
    install_root = tmp_path / "InstantLinkBridge"
    install_root.mkdir()
    config_path = install_root / "etc" / "InstantLinkBridge" / "config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("quality = 100\n", encoding="utf-8")
    backups_dir = tmp_path / "backups"
    env = ManagerEnvironment(
        install_root=install_root,
        backups_dir=backups_dir,
        now=lambda: "2026-05-26T15:30:00Z",
    )
    app = _make_app(tmp_path, private_key, env, request_id="req-brestore")

    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        create_path = "/v1/backup/create"
        create_response = await client.post(
            create_path,
            data=b"",
            headers=signed_headers(private_key, method="POST", path=create_path),
        )
        created = cast(dict[str, Any], await create_response.json())
        backup_id = created["backup"]["backup_id"]

        # Tamper with the live file; restore should bring it back.
        config_path.write_text("quality = 1\n", encoding="utf-8")

        restore_body = _json_body({"backup_id": backup_id})
        restore_path = "/v1/backup/restore"
        restore_response = await client.post(
            restore_path,
            data=restore_body,
            headers={
                **signed_headers(
                    private_key,
                    method="POST",
                    path=restore_path,
                    body=restore_body,
                    nonce="nonce-0002",
                ),
                "Content-Type": "application/json",
            },
        )
        data = cast(dict[str, Any], await restore_response.json())
        assert restore_response.status == 200, data
        assert data["restore"]["restored_count"] >= 1
        assert config_path.read_text(encoding="utf-8") == "quality = 100\n"
    finally:
        await client.close()


# --- upload -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_stores_bytes_and_returns_sha256(tmp_path: Path) -> None:
    private_key = _private_key()
    install_root = tmp_path / "InstantLinkBridge"
    uploads_dir = tmp_path / "uploads"
    env = ManagerEnvironment(
        install_root=install_root,
        backups_dir=tmp_path / "backups",
        uploads_dir=uploads_dir,
    )
    app = _make_app(tmp_path, private_key, env, request_id="req-upload")

    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        payload = b"firmware-bytes"
        path = "/v1/update/upload"
        response = await client.post(
            path,
            data=payload,
            headers={
                **signed_headers(private_key, method="POST", path=path, body=payload),
                "X-Upload-Filename": "firmware.tar.gz",
            },
        )
        data = cast(dict[str, Any], await response.json())
        assert response.status == 200, data
        upload = data["upload"]
        assert upload["sha256"] == hashlib.sha256(payload).hexdigest()
        assert upload["filename"] == "firmware.tar.gz"
        assert (uploads_dir / "firmware.tar.gz").read_bytes() == payload
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_upload_rejects_path_traversal_filename(tmp_path: Path) -> None:
    private_key = _private_key()
    env = ManagerEnvironment(
        install_root=tmp_path / "InstantLinkBridge",
        backups_dir=tmp_path / "backups",
        uploads_dir=tmp_path / "uploads",
    )
    app = _make_app(tmp_path, private_key, env, request_id="req-upbad")

    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        payload = b"firmware-bytes"
        path = "/v1/update/upload"
        response = await client.post(
            path,
            data=payload,
            headers={
                **signed_headers(private_key, method="POST", path=path, body=payload),
                "X-Upload-Filename": "../escape.tar.gz",
            },
        )
        data = cast(dict[str, Any], await response.json())
        assert response.status == 400
        assert data["error_code"] == "invalid_request"
    finally:
        await client.close()


# --- auth --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_endpoints_require_signed_request(tmp_path: Path) -> None:
    private_key = _private_key()
    env = ManagerEnvironment(
        install_root=tmp_path / "InstantLinkBridge",
        backups_dir=tmp_path / "backups",
    )
    app = _make_app(tmp_path, private_key, env)

    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        response = await client.post(
            "/v1/update/install",
            headers={REQUEST_ID_HEADER: "req-unsigned"},
        )
        data = cast(dict[str, Any], await response.json())
        assert response.status == 401
        assert data["error_code"] == "auth_required"
        assert data["operation_id"] == "update_install"
    finally:
        await client.close()
