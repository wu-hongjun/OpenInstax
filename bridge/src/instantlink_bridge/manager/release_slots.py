"""Release-slot planning helpers for Bridge management updates.

The helpers here operate on a release-slot directory in ordinary filesystem
paths. They do not call systemd, require root, or change live deployment unless
the caller explicitly applies the returned symlink actions.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any

UPDATE_STATE_SCHEMA_VERSION = 1
UPDATE_STATE_KIND = "instantlink_bridge_update_state"

CURRENT_LINK_NAME = "current"
PREVIOUS_LINK_NAME = "previous"
RELEASES_DIR_NAME = "releases"
SHARED_DIR_NAME = "shared"

_RELEASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


class ReleaseSlotError(ValueError):
    """Base error for release-slot planning failures."""


class ReleaseSlotPathError(ReleaseSlotError):
    """Raised when a release id, symlink, or state path is unsafe."""


class RollbackStateError(ReleaseSlotError):
    """Raised when rollback state JSON is malformed."""


class UpdateStateStatus(StrEnum):
    """Lifecycle state for a release-slot update."""

    PENDING_VERIFICATION = "pending_verification"
    GOOD = "good"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ReleaseSlotLayout:
    """Resolved paths for one release-slot root."""

    root: Path
    current_link: Path
    previous_link: Path
    releases_dir: Path
    shared_dir: Path

    @classmethod
    def from_root(cls, root: str | Path) -> ReleaseSlotLayout:
        root_path = Path(root).resolve(strict=False)
        return cls(
            root=root_path,
            current_link=root_path / CURRENT_LINK_NAME,
            previous_link=root_path / PREVIOUS_LINK_NAME,
            releases_dir=root_path / RELEASES_DIR_NAME,
            shared_dir=root_path / SHARED_DIR_NAME,
        )


@dataclass(frozen=True, slots=True)
class ReleaseReference:
    """A release id and its resolved release directory path."""

    release_id: str
    path: Path


@dataclass(frozen=True, slots=True)
class SymlinkUpdate:
    """One planned symlink replacement."""

    link_name: str
    link_path: Path
    target: str
    existing_target: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "link_name": self.link_name,
            "link_path": str(self.link_path),
            "target": self.target,
            "existing_target": self.existing_target,
        }


@dataclass(frozen=True, slots=True)
class RollbackState:
    """Serializable update state used to plan health-gated rollback."""

    schema_version: int
    state_kind: str
    status: UpdateStateStatus
    active_release: str
    previous_release: str | None
    target_release: str | None
    backup_manifest_path: str | None
    reason: str | None
    created_at: str
    updated_at: str

    @classmethod
    def pending_verification(
        cls,
        *,
        active_release: str,
        previous_release: str | None,
        backup_manifest_path: str | Path | None = None,
        now: str | None = None,
    ) -> RollbackState:
        timestamp = now or _utc_timestamp()
        return cls(
            schema_version=UPDATE_STATE_SCHEMA_VERSION,
            state_kind=UPDATE_STATE_KIND,
            status=UpdateStateStatus.PENDING_VERIFICATION,
            active_release=validate_release_id(active_release),
            previous_release=_optional_release_id(previous_release),
            target_release=validate_release_id(active_release),
            backup_manifest_path=_optional_json_path(backup_manifest_path),
            reason=None,
            created_at=timestamp,
            updated_at=timestamp,
        )

    @classmethod
    def rolled_back(
        cls,
        *,
        restored_release: str,
        failed_release: str | None,
        reason: str,
        backup_manifest_path: str | Path | None = None,
        now: str | None = None,
    ) -> RollbackState:
        timestamp = now or _utc_timestamp()
        return cls(
            schema_version=UPDATE_STATE_SCHEMA_VERSION,
            state_kind=UPDATE_STATE_KIND,
            status=UpdateStateStatus.ROLLED_BACK,
            active_release=validate_release_id(restored_release),
            previous_release=_optional_release_id(failed_release),
            target_release=validate_release_id(restored_release),
            backup_manifest_path=_optional_json_path(backup_manifest_path),
            reason=reason,
            created_at=timestamp,
            updated_at=timestamp,
        )

    def mark_good(self, *, now: str | None = None) -> RollbackState:
        return RollbackState(
            schema_version=self.schema_version,
            state_kind=self.state_kind,
            status=UpdateStateStatus.GOOD,
            active_release=self.active_release,
            previous_release=self.previous_release,
            target_release=None,
            backup_manifest_path=self.backup_manifest_path,
            reason=None,
            created_at=self.created_at,
            updated_at=now or _utc_timestamp(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "state_kind": self.state_kind,
            "status": self.status.value,
            "active_release": self.active_release,
            "previous_release": self.previous_release,
            "target_release": self.target_release,
            "backup_manifest_path": self.backup_manifest_path,
            "reason": self.reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> RollbackState:
        if value.get("schema_version") != UPDATE_STATE_SCHEMA_VERSION:
            raise RollbackStateError("unsupported update state schema version")
        if value.get("state_kind") != UPDATE_STATE_KIND:
            raise RollbackStateError("unsupported update state kind")

        status_value = _required_str(value, "status")
        try:
            status = UpdateStateStatus(status_value)
        except ValueError as exc:
            raise RollbackStateError(f"unsupported update state status: {status_value}") from exc

        backup_manifest_path = value.get("backup_manifest_path")
        if backup_manifest_path is not None and not isinstance(backup_manifest_path, str):
            raise RollbackStateError("backup_manifest_path must be a string or null")
        reason = value.get("reason")
        if reason is not None and not isinstance(reason, str):
            raise RollbackStateError("reason must be a string or null")

        return cls(
            schema_version=UPDATE_STATE_SCHEMA_VERSION,
            state_kind=UPDATE_STATE_KIND,
            status=status,
            active_release=validate_release_id(_required_str(value, "active_release")),
            previous_release=_optional_release_id(value.get("previous_release")),
            target_release=_optional_release_id(value.get("target_release")),
            backup_manifest_path=_optional_json_path(backup_manifest_path),
            reason=reason,
            created_at=_required_str(value, "created_at"),
            updated_at=_required_str(value, "updated_at"),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"


@dataclass(frozen=True, slots=True)
class ReleaseSwitchPlan:
    """Plan for installing a new release by switching current/previous links."""

    root: Path
    new_release_id: str
    current_release_id: str | None
    previous_release_id: str | None
    actions: tuple[SymlinkUpdate, ...]
    state: RollbackState

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "new_release_id": self.new_release_id,
            "current_release_id": self.current_release_id,
            "previous_release_id": self.previous_release_id,
            "actions": [action.to_dict() for action in self.actions],
            "state": self.state.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class RollbackPlan:
    """Plan for switching current back to the previous known release."""

    root: Path
    restored_release_id: str
    failed_release_id: str | None
    actions: tuple[SymlinkUpdate, ...]
    state: RollbackState

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "restored_release_id": self.restored_release_id,
            "failed_release_id": self.failed_release_id,
            "actions": [action.to_dict() for action in self.actions],
            "state": self.state.to_dict(),
        }


def ensure_release_slot_layout(root: str | Path) -> ReleaseSlotLayout:
    """Create the release-slot directory skeleton in a normal filesystem path."""

    layout = ReleaseSlotLayout.from_root(root)
    layout.releases_dir.mkdir(parents=True, exist_ok=True)
    layout.shared_dir.mkdir(parents=True, exist_ok=True)
    return layout


def release_path(root: str | Path, release_id: str) -> Path:
    layout = ReleaseSlotLayout.from_root(root)
    return layout.releases_dir / validate_release_id(release_id)


def release_symlink_target(release_id: str) -> str:
    return (PurePosixPath(RELEASES_DIR_NAME) / validate_release_id(release_id)).as_posix()


def read_release_link(root: str | Path, link_name: str) -> ReleaseReference | None:
    """Read a current/previous link without requiring the target to exist."""

    layout = ReleaseSlotLayout.from_root(root)
    link_path = _slot_link_path(layout, link_name)
    if not link_path.is_symlink():
        if link_path.exists():
            raise ReleaseSlotPathError(f"{link_name} exists but is not a symlink: {link_path}")
        return None

    target_text = os.readlink(link_path)
    target = Path(target_text)
    candidate = target if target.is_absolute() else layout.root / target
    resolved = candidate.resolve(strict=False)
    releases_dir = layout.releases_dir.resolve(strict=False)
    if not _is_relative_to(resolved, releases_dir):
        raise ReleaseSlotPathError(f"{link_name} symlink target escapes releases dir")
    relative = resolved.relative_to(releases_dir)
    if len(relative.parts) != 1:
        raise ReleaseSlotPathError(f"{link_name} symlink target must name one release")
    release_id = validate_release_id(relative.parts[0])
    return ReleaseReference(release_id=release_id, path=resolved)


def plan_release_switch(
    root: str | Path,
    new_release_id: str,
    *,
    backup_manifest_path: str | Path | None = None,
    now: str | None = None,
    require_release: bool = True,
) -> ReleaseSwitchPlan:
    """Plan switching current to a new release and previous to the old current."""

    layout = ReleaseSlotLayout.from_root(root)
    release_id = validate_release_id(new_release_id)
    if require_release:
        _require_plain_release_dir(layout, release_id)

    current = read_release_link(layout.root, CURRENT_LINK_NAME)
    previous = read_release_link(layout.root, PREVIOUS_LINK_NAME)
    if current is not None and current.release_id == release_id:
        raise ReleaseSlotError(f"release is already current: {release_id}")

    actions: list[SymlinkUpdate] = []
    if current is not None:
        actions.append(_symlink_action(layout, PREVIOUS_LINK_NAME, current.release_id))
    actions.append(_symlink_action(layout, CURRENT_LINK_NAME, release_id))

    state = RollbackState.pending_verification(
        active_release=release_id,
        previous_release=current.release_id if current is not None else None,
        backup_manifest_path=backup_manifest_path,
        now=now,
    )
    return ReleaseSwitchPlan(
        root=layout.root,
        new_release_id=release_id,
        current_release_id=current.release_id if current is not None else None,
        previous_release_id=previous.release_id if previous is not None else None,
        actions=tuple(actions),
        state=state,
    )


def plan_current_previous_switch(
    root: str | Path,
    new_release_id: str,
    *,
    backup_manifest_path: str | Path | None = None,
    now: str | None = None,
    require_release: bool = True,
) -> ReleaseSwitchPlan:
    """Alias for the release-slot install switch named after the two live links."""

    return plan_release_switch(
        root,
        new_release_id,
        backup_manifest_path=backup_manifest_path,
        now=now,
        require_release=require_release,
    )


def plan_rollback(
    root: str | Path,
    *,
    state: RollbackState | None = None,
    reason: str,
    now: str | None = None,
    require_release: bool = True,
) -> RollbackPlan:
    """Plan switching current back to the rollback release."""

    layout = ReleaseSlotLayout.from_root(root)
    current = read_release_link(layout.root, CURRENT_LINK_NAME)
    previous = read_release_link(layout.root, PREVIOUS_LINK_NAME)

    if state is not None:
        if state.status is not UpdateStateStatus.PENDING_VERIFICATION:
            raise RollbackStateError("rollback state is not pending verification")
        if current is None or current.release_id != state.active_release:
            raise RollbackStateError("rollback state does not match the current release")

    restored_release_id = state.previous_release if state is not None else None
    if restored_release_id is None and previous is not None:
        restored_release_id = previous.release_id
    if restored_release_id is None:
        raise ReleaseSlotError("cannot roll back without a previous release")

    failed_release_id = state.active_release if state is not None else None
    if failed_release_id is None and current is not None:
        failed_release_id = current.release_id

    restored_release_id = validate_release_id(restored_release_id)
    failed_release_id = _optional_release_id(failed_release_id)
    if require_release:
        _require_plain_release_dir(layout, restored_release_id)

    actions: list[SymlinkUpdate] = []
    if failed_release_id is not None and failed_release_id != restored_release_id:
        actions.append(_symlink_action(layout, PREVIOUS_LINK_NAME, failed_release_id))
    actions.append(_symlink_action(layout, CURRENT_LINK_NAME, restored_release_id))

    backup_manifest_path = state.backup_manifest_path if state is not None else None
    rollback_state = RollbackState.rolled_back(
        restored_release=restored_release_id,
        failed_release=failed_release_id,
        reason=reason,
        backup_manifest_path=backup_manifest_path,
        now=now,
    )
    return RollbackPlan(
        root=layout.root,
        restored_release_id=restored_release_id,
        failed_release_id=failed_release_id,
        actions=tuple(actions),
        state=rollback_state,
    )


def apply_symlink_updates(actions: Iterable[SymlinkUpdate]) -> None:
    """Apply planned symlink updates with atomic replacements in the target directory."""

    for action in actions:
        if action.link_name not in {CURRENT_LINK_NAME, PREVIOUS_LINK_NAME}:
            raise ReleaseSlotPathError(f"unsupported release slot link: {action.link_name}")
        if action.link_path.name != action.link_name:
            raise ReleaseSlotPathError(f"link path does not match link name: {action.link_path}")
        _validate_link_target(action.target)
        if action.link_path.exists() and not action.link_path.is_symlink():
            raise ReleaseSlotPathError(f"cannot replace non-symlink: {action.link_path}")
        action.link_path.parent.mkdir(parents=True, exist_ok=True)
        temp_link = action.link_path.with_name(f".{action.link_path.name}.tmp-{os.getpid()}")
        if temp_link.exists() or temp_link.is_symlink():
            temp_link.unlink()
        temp_link.symlink_to(action.target)
        os.replace(temp_link, action.link_path)
        _fsync_directory(action.link_path.parent)


def read_rollback_state(path: str | Path) -> RollbackState:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RollbackStateError("update state file must contain a JSON object")
    return RollbackState.from_dict(value)


def write_rollback_state(path: str | Path, state: RollbackState) -> None:
    state_path = Path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=state_path.parent,
        prefix=f".{state_path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        tmp_path = Path(handle.name)
        handle.write(state.to_json())
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp_path, state_path)
    _fsync_directory(state_path.parent)


def validate_release_id(release_id: str) -> str:
    if (
        not release_id
        or release_id in {".", ".."}
        or "/" in release_id
        or "\\" in release_id
        or "\x00" in release_id
    ):
        raise ReleaseSlotPathError(f"unsafe release id: {release_id!r}")
    if _RELEASE_ID_RE.fullmatch(release_id) is None:
        raise ReleaseSlotPathError(f"unsafe release id: {release_id!r}")
    return release_id


def _require_plain_release_dir(layout: ReleaseSlotLayout, release_id: str) -> None:
    path = release_path(layout.root, release_id)
    if path.is_symlink() or not path.is_dir():
        raise ReleaseSlotPathError(f"release directory is missing or unsafe: {release_id}")
    resolved = path.resolve(strict=True)
    releases_dir = layout.releases_dir.resolve(strict=True)
    if not _is_relative_to(resolved, releases_dir):
        raise ReleaseSlotPathError(f"release directory escapes releases dir: {release_id}")


def _symlink_action(
    layout: ReleaseSlotLayout,
    link_name: str,
    release_id: str,
) -> SymlinkUpdate:
    link_path = _slot_link_path(layout, link_name)
    return SymlinkUpdate(
        link_name=link_name,
        link_path=link_path,
        target=release_symlink_target(release_id),
        existing_target=_readlink_text(link_path),
    )


def _slot_link_path(layout: ReleaseSlotLayout, link_name: str) -> Path:
    if link_name == CURRENT_LINK_NAME:
        return layout.current_link
    if link_name == PREVIOUS_LINK_NAME:
        return layout.previous_link
    raise ReleaseSlotPathError(f"unsupported release slot link: {link_name}")


def _readlink_text(path: Path) -> str | None:
    if path.is_symlink():
        return os.readlink(path)
    if path.exists():
        raise ReleaseSlotPathError(f"release slot path exists but is not a symlink: {path}")
    return None


def _validate_link_target(target: str) -> None:
    if "\x00" in target or "\\" in target:
        raise ReleaseSlotPathError(f"unsafe symlink target: {target}")
    path = PurePosixPath(target)
    if path.is_absolute() or len(path.parts) != 2 or path.parts[0] != RELEASES_DIR_NAME:
        raise ReleaseSlotPathError(f"symlink target must be releases/<release-id>: {target}")
    validate_release_id(path.parts[1])


def _fsync_directory(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _optional_release_id(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise RollbackStateError("release id fields must be strings or null")
    return validate_release_id(value)


def _optional_json_path(value: str | Path | None) -> str | None:
    if value is None:
        return None
    text = str(value)
    if "\x00" in text:
        raise ReleaseSlotPathError("JSON path contains a NUL byte")
    return text


def _required_str(value: Mapping[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise RollbackStateError(f"update state {key} must be a non-empty string")
    return item


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _utc_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
