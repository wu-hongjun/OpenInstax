"""Update orchestration helpers wiring signed admin endpoints to primitives.

This module is the synchronous glue between the aiohttp management handlers and
the already-tested backup, installer, release-slot, and health primitives. It
does not own backup/install/rollback logic; it sequences the existing helpers
and maps their results into the stable JSON contract decoded by the macOS app.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from instantlink_bridge.manager.backup import (
    BackupArtifact,
    BackupError,
    create_backup_archive,
    discover_backup_artifacts,
    read_backup_manifest,
    restore_backup_archive,
)
from instantlink_bridge.manager.contract import JsonObject, JsonValue
from instantlink_bridge.manager.health import (
    BridgeHealthGate,
    HealthCheckContext,
    HealthProbe,
    UpdateHealthAction,
    decide_mark_good,
    run_health_checks,
)
from instantlink_bridge.manager.installer import (
    DEFAULT_INSTALL_ROOT,
    DEFAULT_SERVICE_NAME,
    UPDATE_STATE_FILE_NAME,
    FirmwareBundleError,
    FirmwareInstallError,
    OperationLockError,
    PrivilegedCommandRunner,
    install_release_slot_bundle,
)
from instantlink_bridge.manager.release_slots import (
    PREVIOUS_LINK_NAME,
    ReleaseSlotError,
    RollbackState,
    RollbackStateError,
    UpdateStateStatus,
    read_release_link,
    read_rollback_state,
    write_rollback_state,
)

DEFAULT_BACKUPS_DIR = Path("/var/lib/InstantLinkBridge/backups")
FIRMWARE_PACKAGE_KIND = "instantlink_bridge_firmware"
DEFAULT_EXPECTED_TARGET = "linux-aarch64"

_PREFLIGHT_CHECK_PASS = "pass"
_PREFLIGHT_CHECK_WARNING = "warning"
_PREFLIGHT_CHECK_FAIL = "fail"


class UpdateFlowError(Exception):
    """Raised when an update-flow operation fails with a contract error code."""

    def __init__(
        self,
        error_code: str,
        message: str,
        *,
        http_status: int = 400,
        recommended_action: str | None = None,
        details: Mapping[str, Any] | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.http_status = http_status
        self.recommended_action = recommended_action
        self.details = dict(details) if details is not None else None
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True, slots=True)
class ManagerEnvironment:
    """Injectable environment for update orchestration.

    Production defaults target the on-device install root; tests inject a temp
    install root, a fake privileged runner, and deterministic health probes.
    """

    install_root: Path = DEFAULT_INSTALL_ROOT
    backups_dir: Path = DEFAULT_BACKUPS_DIR
    uploads_dir: Path | None = None
    service_name: str = DEFAULT_SERVICE_NAME
    expected_target: str = DEFAULT_EXPECTED_TARGET
    privileged_runner: PrivilegedCommandRunner | None = None
    now: Callable[[], str] | None = None
    health_probes: Callable[[], Mapping[BridgeHealthGate, HealthProbe]] | None = None
    restart_service: bool = True
    _resolved_uploads_dir: Path = field(init=False)

    def __post_init__(self) -> None:
        resolved = self.uploads_dir or (self.install_root / "shared" / "uploads")
        object.__setattr__(self, "_resolved_uploads_dir", Path(resolved))

    @classmethod
    def production(cls) -> ManagerEnvironment:
        """Return the production environment with on-device defaults."""

        return cls()

    @property
    def resolved_uploads_dir(self) -> Path:
        """Return the directory used to stage uploaded firmware payloads."""

        return self._resolved_uploads_dir

    @property
    def update_state_path(self) -> Path:
        """Return the path of the release-slot update state file."""

        return self.install_root / UPDATE_STATE_FILE_NAME

    def timestamp(self) -> str:
        """Return the current UTC timestamp, using an injected factory if set."""

        if self.now is not None:
            return self.now()
        return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def update_state_payload(
    state: RollbackState | None,
    *,
    operation_id: str,
    message: str | None = None,
    progress: float | None = None,
    error: JsonObject | None = None,
) -> JsonObject:
    """Map a RollbackState (or absence) into the BridgeUpdateState payload."""

    phase: str
    safe_state: str
    installed_version: str | None
    resolved_operation_id = operation_id

    if state is None:
        phase = "idle"
        safe_state = "unknown"
        installed_version = None
    elif state.status is UpdateStateStatus.PENDING_VERIFICATION:
        phase = "pending_verification"
        safe_state = "installed"
        installed_version = state.target_release
        resolved_operation_id = state.target_release or state.active_release
    elif state.status is UpdateStateStatus.GOOD:
        phase = "done"
        safe_state = "installed"
        installed_version = state.active_release
        resolved_operation_id = state.active_release
    elif state.status is UpdateStateStatus.ROLLED_BACK:
        phase = "rolled_back"
        safe_state = "previous_version_restored"
        installed_version = state.active_release
        resolved_operation_id = state.target_release or state.active_release
    else:  # UpdateStateStatus.FAILED
        phase = "needs_recovery"
        safe_state = "bridge_needs_recovery"
        installed_version = state.active_release
        resolved_operation_id = state.target_release or state.active_release

    update: JsonObject = {
        "operation_id": resolved_operation_id,
        "phase": phase,
        "progress": progress,
        "message": message,
        "safe_state": safe_state,
        "installed_version": installed_version,
        "error": error,
    }
    return {"update": update}


def run_preflight(env: ManagerEnvironment, package: Mapping[str, Any]) -> JsonObject:
    """Validate a firmware package and return the preflight payload."""

    checks, allowed, rollback_available = _evaluate_preflight(env, package)
    operation_id = _package_version(package)
    preflight: JsonObject = {
        "package": dict(package),
        "allowed": allowed,
        "backup_required": True,
        "rollback_available": rollback_available,
        "checks": checks,
        "operation_id": operation_id,
    }
    return {"preflight": preflight}


def run_install(
    env: ManagerEnvironment,
    package: Mapping[str, Any],
    *,
    bundle_dir: Path | None = None,
) -> JsonObject:
    """Back up, install the staged bundle, and return the pending update state."""

    _checks, allowed, _rollback_available = _evaluate_preflight(env, package)
    if not allowed:
        raise UpdateFlowError(
            "preflight_failed",
            "The update package failed preflight validation.",
            http_status=409,
            recommended_action="Resolve the failing preflight checks before installing.",
        )

    resolved_bundle = bundle_dir or (env.resolved_uploads_dir / "staged")
    timestamp = env.timestamp()
    try:
        backup = create_backup_archive(
            env.backups_dir,
            root=env.install_root,
            version=_package_version(package),
            created_at=timestamp,
        )
    except BackupError as exc:
        raise UpdateFlowError(
            "backup_failed",
            "The pre-update backup could not be created.",
            http_status=500,
            recommended_action="Free disk space or inspect the backup directory, then retry.",
        ) from exc

    try:
        install_release_slot_bundle(
            resolved_bundle,
            root=env.install_root,
            privileged_runner=env.privileged_runner,
            restart_service=env.restart_service,
            service_name=env.service_name,
            now=timestamp,
        )
    except (FirmwareBundleError, OperationLockError, FirmwareInstallError) as exc:
        raise _install_error(exc) from exc

    state = _read_state_or_none(env)
    operation_id = _package_version(package) or "installing_update"
    payload = update_state_payload(state, operation_id=operation_id)
    update = _update_object(payload)
    update["backup"] = {
        "backup_id": backup.manifest.backup_id,
        "manifest_path": str(backup.manifest_path),
        "archive_sha256": backup.archive_sha256,
    }
    return payload


def read_update_status(env: ManagerEnvironment, operation_id: str | None) -> JsonObject:
    """Return the current update state payload, mapping absent state to idle."""

    state = _read_state_or_none(env)
    resolved_operation_id = operation_id or "idle"
    return update_state_payload(state, operation_id=resolved_operation_id)


def run_mark_good(env: ManagerEnvironment) -> JsonObject:
    """Run health checks and mark a pending update good when gates pass.

    Mark-good-failure behavior: when required health gates fail, this function
    does NOT auto-rollback and does NOT mutate the update state. It raises an
    UpdateFlowError with error_code "health_gates_failed" so the operator can
    decide to retry health checks or roll back explicitly via /v1/update/rollback.
    """

    state = _read_state_or_none(env)
    if state is None or state.status is not UpdateStateStatus.PENDING_VERIFICATION:
        raise UpdateFlowError(
            "no_pending_update",
            "There is no pending update to mark good.",
            http_status=409,
            recommended_action="Install an update before marking it good.",
        )

    probes = env.health_probes() if env.health_probes is not None else {}
    context = HealthCheckContext(expected_version=state.target_release)
    health = run_health_checks(probes, context=context)
    timestamp = env.timestamp()
    decision = decide_mark_good(state, health, now=timestamp)

    if decision.action is UpdateHealthAction.MARK_GOOD and decision.state is not None:
        write_rollback_state(env.update_state_path, decision.state)
        return update_state_payload(
            decision.state,
            operation_id=decision.state.active_release,
        )

    raise UpdateFlowError(
        "health_gates_failed",
        "Update health gates failed; the release was not marked good.",
        http_status=409,
        recommended_action="Review failing health gates or roll back the update.",
        details={"blocking_reason": decision.reason} if decision.reason else None,
    )


def run_rollback(env: ManagerEnvironment, reason: str) -> JsonObject:
    """Roll back the current release to the previous one and return state."""

    from instantlink_bridge.manager.installer import rollback_release_slot

    timestamp = env.timestamp()
    try:
        rollback_release_slot(
            root=env.install_root,
            reason=reason,
            privileged_runner=env.privileged_runner,
            restart_service=env.restart_service,
            service_name=env.service_name,
            now=timestamp,
        )
    except (ReleaseSlotError, RollbackStateError) as exc:
        raise UpdateFlowError(
            "rollback_unavailable",
            "The update could not be rolled back.",
            http_status=409,
            recommended_action="Confirm a previous release exists before rolling back.",
        ) from exc
    except OperationLockError as exc:
        raise UpdateFlowError(
            "update_in_progress",
            "Another update operation is already in progress.",
            http_status=409,
            recommended_action="Wait for the in-progress operation to finish, then retry.",
        ) from exc
    except FirmwareInstallError as exc:
        raise UpdateFlowError(
            "rollback_unavailable",
            "The update could not be rolled back.",
            http_status=409,
            recommended_action="Inspect the Bridge install root, then retry the rollback.",
        ) from exc

    state = _read_state_or_none(env)
    operation_id = state.target_release if state is not None else None
    if operation_id is None and state is not None:
        operation_id = state.active_release
    return update_state_payload(state, operation_id=operation_id or "rolled_back")


def store_upload(env: ManagerEnvironment, *, filename: str, data: bytes) -> JsonObject:
    """Store an uploaded firmware payload and return its sha256 digest."""

    safe_name = _safe_upload_filename(filename)
    uploads_dir = env.resolved_uploads_dir
    uploads_dir.mkdir(parents=True, exist_ok=True)
    target = uploads_dir / safe_name
    target.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    return {
        "upload": {
            "filename": safe_name,
            "stored_path": str(target),
            "size_bytes": len(data),
            "sha256": digest,
        }
    }


def run_backup_create(env: ManagerEnvironment) -> JsonObject:
    """Create and verify a backup archive, returning its identifiers."""

    try:
        backup = create_backup_archive(
            env.backups_dir,
            root=env.install_root,
            created_at=env.timestamp(),
        )
    except BackupError as exc:
        raise UpdateFlowError(
            "backup_failed",
            "The backup archive could not be created.",
            http_status=500,
            recommended_action="Free disk space or inspect the backup directory, then retry.",
        ) from exc
    return {
        "backup": {
            "backup_id": backup.manifest.backup_id,
            "manifest_path": str(backup.manifest_path),
            "archive_path": str(backup.archive_path),
            "archive_sha256": backup.archive_sha256,
            "verified": True,
        }
    }


def run_backup_restore(env: ManagerEnvironment, *, backup_id: str) -> JsonObject:
    """Restore a previously created backup archive into the install root."""

    safe_backup_id = _safe_backup_id(backup_id)
    artifact = _find_backup_artifact(env, safe_backup_id)
    if artifact is None or artifact.archive_path is None:
        raise UpdateFlowError(
            "backup_not_found",
            "No backup archive matches the requested backup id.",
            http_status=404,
            recommended_action="List available backups, then retry with a valid backup id.",
        )

    try:
        manifest = read_backup_manifest(artifact.manifest_path)
        result = restore_backup_archive(
            artifact.archive_path,
            manifest,
            root=env.install_root,
            allow_live_root=False,
        )
    except BackupError as exc:
        raise UpdateFlowError(
            "restore_failed",
            "The backup archive could not be restored.",
            http_status=500,
            recommended_action="Verify the backup archive integrity, then retry the restore.",
        ) from exc

    return {
        "restore": {
            "backup_id": safe_backup_id,
            "restored_paths": [str(path) for path in result.restored_paths],
            "restored_count": len(result.restored_paths),
        }
    }


def _evaluate_preflight(
    env: ManagerEnvironment,
    package: Mapping[str, Any],
) -> tuple[list[JsonValue], bool, bool]:
    checks: list[JsonValue] = []

    package_kind = package.get("package_kind")
    if package_kind == FIRMWARE_PACKAGE_KIND:
        checks.append(_check("package_kind", _PREFLIGHT_CHECK_PASS, "Package kind is supported."))
    else:
        checks.append(
            _check(
                "package_kind",
                _PREFLIGHT_CHECK_FAIL,
                f"Unsupported package kind: {package_kind!r}.",
            )
        )

    target = package.get("target")
    if target == env.expected_target:
        checks.append(_check("target_arch", _PREFLIGHT_CHECK_PASS, "Target matches this Bridge."))
    else:
        checks.append(
            _check(
                "target_arch",
                _PREFLIGHT_CHECK_FAIL,
                f"Package target {target!r} does not match {env.expected_target!r}.",
            )
        )

    rollback_available = _rollback_available(env)
    if rollback_available:
        checks.append(
            _check(
                "rollback_available",
                _PREFLIGHT_CHECK_PASS,
                "A previous release is available for rollback.",
            )
        )
    else:
        checks.append(
            _check(
                "rollback_available",
                _PREFLIGHT_CHECK_WARNING,
                "No previous release is available for rollback.",
            )
        )

    active_operation = _active_operation_pending(env)
    if active_operation:
        checks.append(
            _check(
                "no_active_operation",
                _PREFLIGHT_CHECK_FAIL,
                "Another update is pending verification.",
            )
        )
    else:
        checks.append(
            _check(
                "no_active_operation",
                _PREFLIGHT_CHECK_PASS,
                "No update operation is in progress.",
            )
        )

    allowed = not (
        package_kind != FIRMWARE_PACKAGE_KIND or target != env.expected_target or active_operation
    )
    return checks, allowed, rollback_available


def _rollback_available(env: ManagerEnvironment) -> bool:
    try:
        return read_release_link(env.install_root, PREVIOUS_LINK_NAME) is not None
    except ReleaseSlotError:
        return False


def _active_operation_pending(env: ManagerEnvironment) -> bool:
    state = _read_state_or_none(env)
    return state is not None and state.status is UpdateStateStatus.PENDING_VERIFICATION


def _read_state_or_none(env: ManagerEnvironment) -> RollbackState | None:
    state_path = env.update_state_path
    if not state_path.exists():
        return None
    try:
        return read_rollback_state(state_path)
    except RollbackStateError as exc:
        raise UpdateFlowError(
            "update_state_invalid",
            "The stored update state is malformed.",
            http_status=500,
            recommended_action="Inspect the Bridge update state file.",
        ) from exc


def _find_backup_artifact(env: ManagerEnvironment, backup_id: str) -> BackupArtifact | None:
    if not env.backups_dir.exists():
        return None
    for artifact in discover_backup_artifacts(env.backups_dir):
        if artifact.name == backup_id:
            return artifact
    return None


def _install_error(exc: FirmwareInstallError) -> UpdateFlowError:
    if isinstance(exc, OperationLockError):
        return UpdateFlowError(
            "update_in_progress",
            "Another update operation is already in progress.",
            http_status=409,
            recommended_action="Wait for the in-progress operation to finish, then retry.",
        )
    if isinstance(exc, FirmwareBundleError):
        return UpdateFlowError(
            "invalid_package",
            "The staged firmware bundle is invalid.",
            http_status=400,
            recommended_action="Re-upload a valid, verified firmware bundle.",
        )
    return UpdateFlowError(
        "install_failed",
        "The firmware bundle could not be installed.",
        http_status=500,
        recommended_action="Inspect the Bridge install logs, then retry.",
    )


def _package_version(package: Mapping[str, Any]) -> str | None:
    version = package.get("version")
    if isinstance(version, str) and version.strip():
        return version
    return None


def _check(name: str, status: str, message: str | None) -> JsonValue:
    check: JsonObject = {"name": name, "status": status, "message": message}
    return check


def _update_object(payload: JsonObject) -> JsonObject:
    update = payload["update"]
    assert isinstance(update, dict)
    return update


def _safe_upload_filename(filename: str) -> str:
    candidate = filename.strip()
    if (
        not candidate
        or "/" in candidate
        or "\\" in candidate
        or "\x00" in candidate
        or candidate in {".", ".."}
        or candidate.startswith(".")
    ):
        raise UpdateFlowError(
            "invalid_request",
            "The upload filename is invalid.",
            http_status=400,
            recommended_action="Provide a plain filename without path separators.",
        )
    return candidate


def _safe_backup_id(backup_id: str) -> str:
    candidate = backup_id.strip()
    if (
        not candidate
        or "/" in candidate
        or "\\" in candidate
        or "\x00" in candidate
        or candidate in {".", ".."}
    ):
        raise UpdateFlowError(
            "invalid_request",
            "The backup id is invalid.",
            http_status=400,
            recommended_action="Provide a plain backup id without path separators.",
        )
    return candidate
