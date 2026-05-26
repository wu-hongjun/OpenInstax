from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from instantlink_bridge.manager.backup import (
    BACKUP_KIND,
    BACKUP_SCHEMA_VERSION,
    BackupManifest,
    BackupManifestError,
    BackupPathError,
    BackupSource,
    BackupVerificationError,
    create_backup_manifest,
    discover_backup_artifacts,
    plan_backup_retention,
    sha256_file,
    verify_backup_manifest,
)


def test_backup_manifest_hash_verification_detects_tampering(tmp_path: Path) -> None:
    config_path = tmp_path / "etc" / "InstantLinkBridge" / "config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("quality = 100\n", encoding="utf-8")

    manifest = create_backup_manifest(
        [config_path],
        root=tmp_path,
        version="v0.2.0",
        created_at="2026-05-26T15:30:00Z",
    )

    assert manifest.backup_id == "update-20260526-153000-v0.2.0"
    assert [entry.path for entry in manifest.files] == ["etc/InstantLinkBridge/config.toml"]
    assert manifest.files[0].sha256 == hashlib.sha256(config_path.read_bytes()).hexdigest()
    assert sha256_file(config_path) == manifest.files[0].sha256

    valid_result = verify_backup_manifest(manifest, root=tmp_path)

    assert valid_result.ok
    assert valid_result.checked_paths == ("etc/InstantLinkBridge/config.toml",)

    config_path.write_text("quality = 95\n", encoding="utf-8")

    tampered_result = verify_backup_manifest(manifest, root=tmp_path)

    assert not tampered_result.ok
    assert tampered_result.mismatches[0].path == "etc/InstantLinkBridge/config.toml"
    with pytest.raises(BackupVerificationError):
        verify_backup_manifest(manifest, root=tmp_path, raise_on_error=True)


def test_backup_manifest_records_optional_missing_and_refuses_required_missing(
    tmp_path: Path,
) -> None:
    optional_manifest = create_backup_manifest(
        [BackupSource(tmp_path / "missing-optional.toml", required=False)],
        root=tmp_path,
        created_at="2026-05-26T15:30:00Z",
    )

    assert optional_manifest.files == ()
    assert optional_manifest.missing_sources[0].source_path.endswith("missing-optional.toml")
    assert not optional_manifest.missing_sources[0].required
    assert verify_backup_manifest(optional_manifest, root=tmp_path).ok

    with pytest.raises(BackupManifestError, match="required backup source is missing"):
        create_backup_manifest([tmp_path / "missing-required.toml"], root=tmp_path)


def test_backup_manifest_verification_reports_missing_files(tmp_path: Path) -> None:
    config_path = tmp_path / "etc" / "InstantLinkBridge" / "config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("quality = 100\n", encoding="utf-8")
    manifest = create_backup_manifest([config_path], root=tmp_path)

    config_path.unlink()

    result = verify_backup_manifest(manifest, root=tmp_path)

    assert not result.ok
    assert result.missing_paths == ("etc/InstantLinkBridge/config.toml",)


def test_backup_manifest_rejects_path_traversal(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-config.toml"
    outside.write_text("secret = true\n", encoding="utf-8")
    try:
        with pytest.raises(BackupPathError):
            create_backup_manifest([outside], root=tmp_path)
    finally:
        outside.unlink()

    unsafe_manifest = {
        "schema_version": BACKUP_SCHEMA_VERSION,
        "backup_kind": BACKUP_KIND,
        "backup_id": "update-test",
        "created_at": "2026-05-26T15:30:00Z",
        "root": str(tmp_path),
        "version": None,
        "files": [
            {
                "path": "../etc/shadow",
                "source_path": str(tmp_path / "etc" / "shadow"),
                "size_bytes": 1,
                "sha256": "0" * 64,
            }
        ],
        "missing_sources": [],
        "excluded_paths": [],
    }

    with pytest.raises(BackupPathError):
        BackupManifest.from_dict(unsafe_manifest)


def test_backup_manifest_enforces_exclusions(tmp_path: Path) -> None:
    config_path = tmp_path / "etc" / "InstantLinkBridge" / "config.toml"
    upload_path = tmp_path / "var" / "lib" / "InstantLinkBridge" / "incoming" / "photo.jpg"
    config_path.parent.mkdir(parents=True)
    upload_path.parent.mkdir(parents=True)
    config_path.write_text("quality = 100\n", encoding="utf-8")
    upload_path.write_bytes(b"uploaded image")

    manifest = create_backup_manifest(
        [tmp_path / "etc", tmp_path / "var"],
        root=tmp_path,
        exclude_paths=[Path("var/lib/InstantLinkBridge/incoming")],
    )

    assert [entry.path for entry in manifest.files] == ["etc/InstantLinkBridge/config.toml"]
    assert manifest.excluded_paths == ("var/lib/InstantLinkBridge/incoming",)


def test_backup_retention_selects_oldest_artifacts(tmp_path: Path) -> None:
    backups_dir = tmp_path / "backups"
    backups_dir.mkdir()
    created_at_values = [
        "2026-05-20T10:15:00Z",
        "2026-05-21T10:15:00Z",
        "2026-05-22T10:15:00Z",
        "2026-05-23T10:15:00Z",
    ]
    for created_at in created_at_values:
        backup_id = f"update-{created_at[:10].replace('-', '')}"
        manifest_path = backups_dir / f"{backup_id}.manifest.json"
        archive_path = backups_dir / f"{backup_id}.tar.gz"
        manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": BACKUP_SCHEMA_VERSION,
                    "backup_kind": BACKUP_KIND,
                    "backup_id": backup_id,
                    "created_at": created_at,
                    "root": str(tmp_path),
                    "version": None,
                    "files": [],
                    "missing_sources": [],
                    "excluded_paths": [],
                }
            ),
            encoding="utf-8",
        )
        archive_path.write_bytes(b"backup")

    artifacts = discover_backup_artifacts(backups_dir)
    plan = plan_backup_retention(artifacts, keep=3)

    assert [artifact.sort_key for artifact in plan.keep] == [
        "2026-05-23T10:15:00Z",
        "2026-05-22T10:15:00Z",
        "2026-05-21T10:15:00Z",
    ]
    assert [artifact.sort_key for artifact in plan.prune] == ["2026-05-20T10:15:00Z"]
    assert {path.name for path in plan.prune_paths} == {
        "update-20260520.manifest.json",
        "update-20260520.tar.gz",
    }
