"""Backup manifest helpers for Bridge management updates.

This module only plans and verifies local backup contents. It does not switch
release slots, stop services, or create exported support bundles.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

BACKUP_SCHEMA_VERSION = 1
BACKUP_KIND = "instantlink_bridge_local_update_backup"
DEFAULT_RETENTION_COUNT = 3
DEFAULT_EXCLUDED_PATHS: tuple[Path, ...] = (
    Path("/var/lib/InstantLinkBridge/incoming"),
    Path("/var/lib/InstantLinkBridge/uploads"),
    Path("/var/log"),
    Path("/root/.ssh"),
    Path("/home/ib/.ssh"),
    Path("/etc/ssh"),
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_BACKUP_ID_UNSAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


class BackupError(ValueError):
    """Base error for backup manifest failures."""


class BackupPathError(BackupError):
    """Raised when a backup path is outside the configured root or unsafe."""


class BackupManifestError(BackupError):
    """Raised when a backup manifest is malformed."""


class BackupVerificationError(BackupError):
    """Raised when backup verification fails and strict mode is requested."""


@dataclass(frozen=True, slots=True)
class BackupSource:
    """One configured source path for a local update backup."""

    path: Path
    required: bool = True


@dataclass(frozen=True, slots=True)
class BackupFileEntry:
    """One hashed file recorded in a backup manifest."""

    path: str
    source_path: str
    size_bytes: int
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "source_path": self.source_path,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> BackupFileEntry:
        path = _required_str(value, "path")
        _safe_relative_posix_path(path)
        source_path = _required_str(value, "source_path")
        size_bytes = value.get("size_bytes")
        if not isinstance(size_bytes, int) or size_bytes < 0:
            raise BackupManifestError("backup file size_bytes must be a non-negative integer")
        sha256 = _required_str(value, "sha256")
        if _SHA256_RE.fullmatch(sha256) is None:
            raise BackupManifestError(f"invalid SHA-256 digest for backup file: {path}")
        return cls(
            path=path,
            source_path=source_path,
            size_bytes=size_bytes,
            sha256=sha256,
        )


@dataclass(frozen=True, slots=True)
class MissingBackupSource:
    """A configured backup source that was absent when the manifest was created."""

    source_path: str
    required: bool
    reason: str = "missing"

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "required": self.required,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> MissingBackupSource:
        source_path = _required_str(value, "source_path")
        required = value.get("required")
        if not isinstance(required, bool):
            raise BackupManifestError("missing backup source required must be a boolean")
        reason = _required_str(value, "reason")
        return cls(source_path=source_path, required=required, reason=reason)


@dataclass(frozen=True, slots=True)
class BackupManifest:
    """Serializable manifest for a local update backup."""

    schema_version: int
    backup_kind: str
    backup_id: str
    created_at: str
    root: str
    version: str | None
    files: tuple[BackupFileEntry, ...]
    missing_sources: tuple[MissingBackupSource, ...] = ()
    excluded_paths: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "backup_kind": self.backup_kind,
            "backup_id": self.backup_id,
            "created_at": self.created_at,
            "root": self.root,
            "version": self.version,
            "files": [entry.to_dict() for entry in self.files],
            "missing_sources": [entry.to_dict() for entry in self.missing_sources],
            "excluded_paths": list(self.excluded_paths),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> BackupManifest:
        if value.get("schema_version") != BACKUP_SCHEMA_VERSION:
            raise BackupManifestError("unsupported backup manifest schema version")
        if value.get("backup_kind") != BACKUP_KIND:
            raise BackupManifestError("unsupported backup manifest kind")

        files_value = value.get("files")
        if not isinstance(files_value, list):
            raise BackupManifestError("backup manifest files must be a list")
        files = tuple(_parse_file_entry(item) for item in files_value)

        missing_value = value.get("missing_sources", [])
        if not isinstance(missing_value, list):
            raise BackupManifestError("backup manifest missing_sources must be a list")
        missing_sources = tuple(_parse_missing_source(item) for item in missing_value)

        excluded_value = value.get("excluded_paths", [])
        if not isinstance(excluded_value, list) or not all(
            isinstance(item, str) for item in excluded_value
        ):
            raise BackupManifestError("backup manifest excluded_paths must be a list of strings")

        version = value.get("version")
        if version is not None and not isinstance(version, str):
            raise BackupManifestError("backup manifest version must be a string or null")

        return cls(
            schema_version=BACKUP_SCHEMA_VERSION,
            backup_kind=BACKUP_KIND,
            backup_id=_required_str(value, "backup_id"),
            created_at=_required_str(value, "created_at"),
            root=_required_str(value, "root"),
            version=version,
            files=files,
            missing_sources=missing_sources,
            excluded_paths=tuple(excluded_value),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"


@dataclass(frozen=True, slots=True)
class BackupHashMismatch:
    """A file whose current hash differs from the backup manifest."""

    path: str
    expected_sha256: str
    actual_sha256: str


@dataclass(frozen=True, slots=True)
class BackupVerificationResult:
    """Result of verifying a manifest against local files."""

    ok: bool
    checked_paths: tuple[str, ...]
    missing_paths: tuple[str, ...]
    mismatches: tuple[BackupHashMismatch, ...]
    invalid_paths: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BackupArtifact:
    """Manifest/archive pair used when planning backup retention."""

    name: str
    manifest_path: Path
    archive_path: Path | None
    sort_key: str

    @property
    def paths(self) -> tuple[Path, ...]:
        if self.archive_path is None:
            return (self.manifest_path,)
        return (self.manifest_path, self.archive_path)


@dataclass(frozen=True, slots=True)
class BackupRetentionPlan:
    """Which backup artifacts should be retained or pruned."""

    keep: tuple[BackupArtifact, ...]
    prune: tuple[BackupArtifact, ...]
    prune_paths: tuple[Path, ...]


BackupSourceLike = BackupSource | str | Path
BackupManifestLike = BackupManifest | Mapping[str, Any]
BackupArtifactLike = BackupArtifact | str | Path


def create_backup_manifest(
    sources: Iterable[BackupSourceLike],
    *,
    root: str | Path = Path("/"),
    exclude_paths: Iterable[str | Path] = DEFAULT_EXCLUDED_PATHS,
    backup_id: str | None = None,
    version: str | None = None,
    created_at: str | None = None,
) -> BackupManifest:
    """Create a backup manifest from configured local source paths."""

    root_path = _resolved_root(root)
    exclusions = _resolved_exclusions(exclude_paths, root_path)
    created_at_value = created_at or _utc_timestamp()
    backup_id_value = backup_id or default_backup_id(created_at_value, version=version)

    files: list[BackupFileEntry] = []
    missing_sources: list[MissingBackupSource] = []
    excluded_paths: set[str] = set()

    for source in sources:
        backup_source = _coerce_backup_source(source)
        source_path = _source_path_under_root(backup_source.path, root_path)
        if _is_excluded_path(source_path, exclusions):
            excluded_paths.add(_backup_relative_path(source_path, root_path))
            continue
        if not source_path.exists():
            missing = MissingBackupSource(
                source_path=str(source_path),
                required=backup_source.required,
            )
            if backup_source.required:
                raise BackupManifestError(f"required backup source is missing: {source_path}")
            missing_sources.append(missing)
            continue
        if source_path.is_dir():
            _add_directory_entries(source_path, root_path, exclusions, files, excluded_paths)
        elif source_path.is_file():
            file_path = _existing_path_under_root(source_path, root_path)
            files.append(_backup_entry_for_file(file_path, root_path))
        else:
            raise BackupManifestError(
                f"backup source is not a regular file or directory: {source_path}"
            )

    files.sort(key=lambda entry: entry.path)
    return BackupManifest(
        schema_version=BACKUP_SCHEMA_VERSION,
        backup_kind=BACKUP_KIND,
        backup_id=backup_id_value,
        created_at=created_at_value,
        root=str(root_path),
        version=version,
        files=tuple(files),
        missing_sources=tuple(missing_sources),
        excluded_paths=tuple(sorted(excluded_paths)),
    )


def verify_backup_manifest(
    manifest: BackupManifestLike,
    *,
    root: str | Path | None = None,
    raise_on_error: bool = False,
) -> BackupVerificationResult:
    """Verify manifest file paths and SHA-256 hashes against local files."""

    backup_manifest = _coerce_manifest(manifest)
    root_path = _resolved_root(root if root is not None else backup_manifest.root)
    checked_paths: list[str] = []
    missing_paths: list[str] = []
    mismatches: list[BackupHashMismatch] = []
    invalid_paths: list[str] = []

    for entry in backup_manifest.files:
        relative_path = _safe_relative_posix_path(entry.path)
        candidate = (root_path / Path(relative_path.as_posix())).resolve(strict=False)
        if not _is_relative_to(candidate, root_path):
            raise BackupPathError(f"backup manifest path escapes root: {entry.path}")
        if not candidate.exists():
            missing_paths.append(entry.path)
            continue
        resolved = candidate.resolve(strict=True)
        if not _is_relative_to(resolved, root_path) or not resolved.is_file():
            invalid_paths.append(entry.path)
            continue
        checked_paths.append(entry.path)
        actual_sha256 = sha256_file(resolved)
        if actual_sha256 != entry.sha256:
            mismatches.append(
                BackupHashMismatch(
                    path=entry.path,
                    expected_sha256=entry.sha256,
                    actual_sha256=actual_sha256,
                )
            )

    result = BackupVerificationResult(
        ok=not missing_paths and not mismatches and not invalid_paths,
        checked_paths=tuple(checked_paths),
        missing_paths=tuple(missing_paths),
        mismatches=tuple(mismatches),
        invalid_paths=tuple(invalid_paths),
    )
    if raise_on_error and not result.ok:
        raise BackupVerificationError(_verification_error_message(result))
    return result


def read_backup_manifest(path: str | Path) -> BackupManifest:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BackupManifestError("backup manifest file must contain a JSON object")
    return BackupManifest.from_dict(value)


def write_backup_manifest(path: str | Path, manifest: BackupManifestLike) -> None:
    backup_manifest = _coerce_manifest(manifest)
    Path(path).write_text(backup_manifest.to_json(), encoding="utf-8")


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def default_backup_id(created_at: str | None = None, *, version: str | None = None) -> str:
    timestamp = created_at or _utc_timestamp()
    compact_timestamp = (
        timestamp.replace("-", "")
        .replace(":", "")
        .replace("T", "-")
        .replace("+0000", "Z")
        .replace("+00:00", "Z")
    )
    compact_timestamp = compact_timestamp.removesuffix("Z").split(".")[0]
    version_component = _safe_backup_id_component(version or "unknown")
    return f"update-{compact_timestamp}-{version_component}"


def is_excluded_backup_path(
    path: str | Path,
    *,
    root: str | Path = Path("/"),
    exclude_paths: Iterable[str | Path] = DEFAULT_EXCLUDED_PATHS,
) -> bool:
    root_path = _resolved_root(root)
    source_path = _source_path_under_root(path, root_path)
    exclusions = _resolved_exclusions(exclude_paths, root_path)
    return _is_excluded_path(source_path, exclusions)


def discover_backup_artifacts(
    backups_dir: str | Path,
    *,
    manifest_pattern: str = "update-*.manifest.json",
) -> tuple[BackupArtifact, ...]:
    """Return backup manifest/archive pairs found in a backups directory."""

    directory = Path(backups_dir)
    artifacts: list[BackupArtifact] = []
    for manifest_path in sorted(directory.glob(manifest_pattern)):
        if not manifest_path.is_file():
            continue
        name = manifest_path.name.removesuffix(".manifest.json")
        archive_path = directory / f"{name}.tar.gz"
        artifacts.append(
            BackupArtifact(
                name=name,
                manifest_path=manifest_path,
                archive_path=archive_path if archive_path.exists() else None,
                sort_key=_artifact_sort_key(manifest_path),
            )
        )
    return tuple(artifacts)


def plan_backup_retention(
    artifacts: Iterable[BackupArtifactLike],
    *,
    keep: int = DEFAULT_RETENTION_COUNT,
) -> BackupRetentionPlan:
    """Plan retention, keeping the newest backup artifacts by manifest metadata."""

    if keep < 1:
        raise BackupError("backup retention keep count must be at least 1")
    backup_artifacts = tuple(_coerce_backup_artifact(artifact) for artifact in artifacts)
    ordered = tuple(
        sorted(
            backup_artifacts,
            key=lambda artifact: (artifact.sort_key, artifact.manifest_path.name),
            reverse=True,
        )
    )
    kept = ordered[:keep]
    pruned = ordered[keep:]
    prune_paths = tuple(path for artifact in pruned for path in artifact.paths)
    return BackupRetentionPlan(keep=kept, prune=pruned, prune_paths=prune_paths)


def select_backups_to_prune(
    backups_dir: str | Path,
    *,
    keep: int = DEFAULT_RETENTION_COUNT,
) -> tuple[Path, ...]:
    """Return backup manifest/archive paths that should be pruned."""

    plan = plan_backup_retention(discover_backup_artifacts(backups_dir), keep=keep)
    return plan.prune_paths


def _add_directory_entries(
    source_path: Path,
    root_path: Path,
    exclusions: tuple[Path, ...],
    files: list[BackupFileEntry],
    excluded_paths: set[str],
) -> None:
    for dirpath, dirnames, filenames in os.walk(source_path, followlinks=False):
        current_dir = Path(dirpath)
        kept_dirnames: list[str] = []
        for dirname in sorted(dirnames):
            directory = _existing_path_under_root(current_dir / dirname, root_path)
            if _is_excluded_path(directory, exclusions) or (current_dir / dirname).is_symlink():
                excluded_paths.add(_backup_relative_path(directory, root_path))
            else:
                kept_dirnames.append(dirname)
        dirnames[:] = kept_dirnames

        for filename in sorted(filenames):
            candidate = current_dir / filename
            if candidate.is_symlink():
                excluded_paths.add(_backup_relative_path(candidate, root_path))
                continue
            file_path = _existing_path_under_root(candidate, root_path)
            if _is_excluded_path(file_path, exclusions):
                excluded_paths.add(_backup_relative_path(file_path, root_path))
                continue
            if file_path.is_file():
                files.append(_backup_entry_for_file(file_path, root_path))


def _backup_entry_for_file(file_path: Path, root_path: Path) -> BackupFileEntry:
    return BackupFileEntry(
        path=_backup_relative_path(file_path, root_path),
        source_path=str(file_path),
        size_bytes=file_path.stat().st_size,
        sha256=sha256_file(file_path),
    )


def _backup_relative_path(path: Path, root_path: Path) -> str:
    try:
        relative = path.relative_to(root_path)
    except ValueError as exc:
        raise BackupPathError(f"backup path escapes root: {path}") from exc
    return PurePosixPath(*relative.parts).as_posix()


def _source_path_under_root(path: str | Path, root_path: Path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root_path / candidate
    candidate = candidate.resolve(strict=False)
    if not _is_relative_to(candidate, root_path):
        raise BackupPathError(f"backup source path escapes root: {path}")
    return candidate


def _existing_path_under_root(path: str | Path, root_path: Path) -> Path:
    candidate = Path(path).resolve(strict=True)
    if not _is_relative_to(candidate, root_path):
        raise BackupPathError(f"backup source path escapes root: {path}")
    return candidate


def _resolved_root(root: str | Path) -> Path:
    return Path(root).resolve(strict=False)


def _resolved_exclusions(exclude_paths: Iterable[str | Path], root_path: Path) -> tuple[Path, ...]:
    exclusions: list[Path] = []
    for path in exclude_paths:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = root_path / candidate
        exclusions.append(candidate.resolve(strict=False))
    return tuple(exclusions)


def _is_excluded_path(path: Path, exclusions: tuple[Path, ...]) -> bool:
    return any(path == exclusion or _is_relative_to(path, exclusion) for exclusion in exclusions)


def _safe_relative_posix_path(value: str) -> PurePosixPath:
    if "\x00" in value or "\\" in value:
        raise BackupPathError(f"backup manifest path is unsafe: {value}")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts:
        raise BackupPathError(f"backup manifest path must be relative: {value}")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise BackupPathError(f"backup manifest path is unsafe: {value}")
    return path


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _coerce_backup_source(source: BackupSourceLike) -> BackupSource:
    if isinstance(source, BackupSource):
        return source
    return BackupSource(path=Path(source), required=True)


def _coerce_manifest(manifest: BackupManifestLike) -> BackupManifest:
    if isinstance(manifest, BackupManifest):
        return manifest
    return BackupManifest.from_dict(manifest)


def _coerce_backup_artifact(artifact: BackupArtifactLike) -> BackupArtifact:
    if isinstance(artifact, BackupArtifact):
        return artifact
    manifest_path = Path(artifact)
    name = manifest_path.name.removesuffix(".manifest.json")
    archive_path = manifest_path.with_name(f"{name}.tar.gz")
    return BackupArtifact(
        name=name,
        manifest_path=manifest_path,
        archive_path=archive_path if archive_path.exists() else None,
        sort_key=_artifact_sort_key(manifest_path),
    )


def _artifact_sort_key(manifest_path: Path) -> str:
    try:
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return manifest_path.name
    if not isinstance(value, dict):
        return manifest_path.name
    created_at = value.get("created_at")
    if isinstance(created_at, str):
        return created_at
    backup_id = value.get("backup_id")
    if isinstance(backup_id, str):
        return backup_id
    return manifest_path.name


def _parse_file_entry(value: object) -> BackupFileEntry:
    if not isinstance(value, Mapping):
        raise BackupManifestError("backup manifest file entries must be JSON objects")
    return BackupFileEntry.from_dict(value)


def _parse_missing_source(value: object) -> MissingBackupSource:
    if not isinstance(value, Mapping):
        raise BackupManifestError("backup manifest missing source entries must be JSON objects")
    return MissingBackupSource.from_dict(value)


def _required_str(value: Mapping[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise BackupManifestError(f"backup manifest {key} must be a non-empty string")
    return item


def _verification_error_message(result: BackupVerificationResult) -> str:
    parts: list[str] = []
    if result.missing_paths:
        parts.append(f"missing={','.join(result.missing_paths)}")
    if result.mismatches:
        parts.append(f"mismatched={','.join(item.path for item in result.mismatches)}")
    if result.invalid_paths:
        parts.append(f"invalid={','.join(result.invalid_paths)}")
    return f"backup verification failed ({'; '.join(parts)})"


def _safe_backup_id_component(value: str) -> str:
    component = _BACKUP_ID_UNSAFE_RE.sub("_", value.strip()).strip("._-")
    return component or "unknown"


def _utc_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
