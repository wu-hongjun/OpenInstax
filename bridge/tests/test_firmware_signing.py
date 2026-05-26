from __future__ import annotations

import copy
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeAlias, cast

import pytest

from instantlink_bridge.update import signing

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    PrivateKey: TypeAlias = Ed25519PrivateKey
else:
    PrivateKey: TypeAlias = Any

serialization = pytest.importorskip("cryptography.hazmat.primitives.serialization")
ed25519 = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")

BRIDGE_ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = BRIDGE_ROOT / "scripts" / "build-firmware-bundle.sh"


def firmware_manifest() -> dict[str, object]:
    return {
        "schema_version": 1,
        "package_kind": "instantlink_bridge_firmware",
        "bridge_version": "0.1.0",
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
            "name": "InstantLinkBridgeFirmware-v0.1.0-linux-aarch64.tar.gz",
            "compression": "gzip",
        },
        "python": {
            "package": "instantlink-bridge",
            "constraints": "bridge/requirements/constraints.txt",
        },
        "native_artifacts": {
            "instantlink": {
                "path": "native/bin/instantlink",
                "sha256": "a" * 64,
            },
            "libinstantlink_ffi.so": {
                "path": "native/lib/libinstantlink_ffi.so",
                "sha256": "b" * 64,
            },
            "build_manifest": {
                "path": "native/instantlink-artifacts-manifest.json",
                "sha256": "c" * 64,
            },
        },
        "install": {
            "script": "install-firmware-bundle.sh",
            "default_target": "/opt/InstantLinkBridge",
        },
    }


def latest_manifest() -> dict[str, object]:
    return {
        "schema_version": 1,
        "package_kind": "instantlink_bridge_firmware",
        "bridge_version": "0.1.0",
        "required_bridge_api_version": 1,
        "minimum_rollback_version": None,
        "migration_notes": [],
        "instantlink_workspace": {
            "commit_sha": "1" * 40,
            "branch": "main",
            "dirty": False,
        },
        "target": "linux-aarch64",
        "archive_name": "InstantLinkBridgeFirmware-v0.1.0-linux-aarch64.tar.gz",
        "archive_sha256": "d" * 64,
        "manifest_name": "InstantLinkBridgeFirmware-v0.1.0-linux-aarch64.manifest.json",
        "manifest_sha256": "e" * 64,
        "checksum_name": "InstantLinkBridgeFirmware-v0.1.0-linux-aarch64.tar.gz.sha256",
        "checksum_sha256": "f" * 64,
    }


@pytest.fixture
def private_key() -> PrivateKey:
    return cast(PrivateKey, ed25519.Ed25519PrivateKey.generate())


def trusted_keys(private_key: PrivateKey) -> dict[str, str]:
    key_id = signing.key_id_for_public_key(private_key)
    return {key_id: signing.public_key_text(private_key)}


def write_private_key(path: Path, private_key: PrivateKey) -> None:
    path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )


def write_fake_native_artifacts(path: Path) -> None:
    path.mkdir()
    instantlink = path / "instantlink"
    instantlink.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    instantlink.chmod(0o755)
    (path / "libinstantlink_ffi.so").write_bytes(b"fake ffi\n")
    (path / "instantlink-artifacts-manifest.json").write_text(
        '{"schema_version": 1}\n',
        encoding="utf-8",
    )


def base_bundle_env(tmp_path: Path, artifact_dir: Path, app_bundle: Path) -> dict[str, str]:
    env = {
        **os.environ,
        "PYTHON_BIN": sys.executable,
        "INSTANTLINK_BRIDGE_BUILD_NATIVE": "0",
        "INSTANTLINK_BRIDGE_INSTANTLINK_ARTIFACT_DIR": str(artifact_dir),
        "INSTANTLINK_BRIDGE_FIRMWARE_DIST_DIR": str(tmp_path / "dist"),
        "INSTANTLINK_BRIDGE_FIRMWARE_STAGE_ROOT": str(tmp_path / "stage"),
        "INSTANTLINK_BRIDGE_FIRMWARE_APP_BUNDLE_DIR": str(app_bundle),
    }
    for key in (
        "INSTANTLINK_BRIDGE_FIRMWARE_SIGNING_KEY",
        "INSTANTLINK_BRIDGE_FIRMWARE_SIGNING_KEY_ID",
        "INSTANTLINK_BRIDGE_FIRMWARE_SIGNING_KEY_PASSWORD_ENV",
    ):
        env.pop(key, None)
    return env


def prepend_fake_clean_git(tmp_path: Path, env: dict[str, str]) -> None:
    """Make the build subprocess see a clean git tree without mutating this checkout."""

    real_git = shutil.which("git")
    assert real_git is not None
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_git = fake_bin / "git"
    fake_git.write_text(
        f"""#!/usr/bin/env bash
set -euo pipefail
if [[ "${{1:-}}" == "-C" ]]; then
  shift 2
fi
case "${{1:-}}" in
  status)
    exit 0
    ;;
  rev-parse)
    if [[ "${{2:-}}" == "--verify" && "${{3:-}}" == "HEAD" ]]; then
      printf '%s\\n' '1111111111111111111111111111111111111111'
      exit 0
    fi
    ;;
  symbolic-ref)
    if [[ "${{2:-}}" == "--quiet" && "${{3:-}}" == "--short" && "${{4:-}}" == "HEAD" ]]; then
      printf '%s\\n' 'main'
      exit 0
    fi
    ;;
esac
exec {real_git!r} "$@"
""",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"


def test_canonical_json_is_deterministic() -> None:
    assert signing.canonical_json_bytes({"z": [3, 2], "a": "é"}) == b'{"a":"\xc3\xa9","z":[3,2]}'


def test_sign_and_verify_manifest(private_key: PrivateKey) -> None:
    manifest = firmware_manifest()
    signature = signing.sign_manifest(manifest, private_key)

    verification = signing.verify_manifest_signature(
        manifest,
        signature,
        trusted_keys(private_key),
    )

    assert verification.signed is True
    assert verification.key_id == signature["key_id"]
    assert len(verification.canonical_sha256) == 64


def test_missing_signature_rejected_when_required(
    private_key: PrivateKey,
) -> None:
    with pytest.raises(signing.FirmwareSignatureError, match="signature is required"):
        signing.verify_manifest_signature(
            firmware_manifest(),
            None,
            trusted_keys(private_key),
            require_signature=True,
        )


def test_unsigned_manifest_allowed_when_not_required(
    private_key: PrivateKey,
) -> None:
    verification = signing.verify_manifest_signature(
        firmware_manifest(),
        None,
        trusted_keys(private_key),
        require_signature=False,
    )

    assert verification.signed is False
    assert verification.key_id is None


def test_tampered_manifest_rejected(private_key: PrivateKey) -> None:
    manifest = firmware_manifest()
    signature = signing.sign_manifest(manifest, private_key)
    tampered = copy.deepcopy(manifest)
    tampered["bridge_version"] = "0.2.0"

    with pytest.raises(signing.FirmwareSignatureError, match="invalid firmware manifest signature"):
        signing.verify_manifest_signature(tampered, signature, trusted_keys(private_key))


def test_wrong_key_id_rejected(private_key: PrivateKey) -> None:
    manifest = firmware_manifest()
    signature = signing.sign_manifest(manifest, private_key) | {"key_id": "not-a-trusted-key"}

    with pytest.raises(signing.FirmwareSignatureError, match="untrusted firmware signing key id"):
        signing.verify_manifest_signature(manifest, signature, trusted_keys(private_key))


def test_bad_digest_shape_rejected() -> None:
    manifest = firmware_manifest()
    native_artifacts = manifest["native_artifacts"]
    assert isinstance(native_artifacts, dict)
    instantlink = native_artifacts["instantlink"]
    assert isinstance(instantlink, dict)
    instantlink["sha256"] = "not-a-sha256"

    with pytest.raises(signing.FirmwareManifestError, match="invalid SHA-256 digest"):
        signing.verify_manifest_signature(manifest, None, {}, require_signature=False)


def test_path_traversal_artifact_name_rejected() -> None:
    manifest = latest_manifest()
    manifest["archive_name"] = "../InstantLinkBridgeFirmware.tar.gz"

    with pytest.raises(signing.FirmwareManifestError, match="artifact name"):
        signing.verify_manifest_signature(manifest, None, {}, require_signature=False)


def test_missing_release_provenance_rejected() -> None:
    manifest = firmware_manifest()
    del manifest["instantlink_workspace"]

    with pytest.raises(signing.FirmwareManifestError, match="metadata is required"):
        signing.verify_manifest_signature(manifest, None, {}, require_signature=False)


def test_dirty_release_metadata_rejected() -> None:
    manifest = firmware_manifest()
    workspace = manifest["instantlink_workspace"]
    assert isinstance(workspace, dict)
    workspace["dirty"] = True

    with pytest.raises(signing.FirmwareManifestError, match="clean workspace"):
        signing.verify_manifest_signature(manifest, None, {}, require_signature=False)


def test_default_signature_path_matches_release_asset_names() -> None:
    assert signing.default_signature_path(Path("firmware.manifest.json")) == Path(
        "firmware.manifest.sig"
    )
    assert signing.default_signature_path(Path("latest.json")) == Path("latest.json.sig")


def test_build_script_signed_mode_emits_manifest_sidecars(
    tmp_path: Path,
    private_key: PrivateKey,
) -> None:
    artifact_dir = tmp_path / "artifacts"
    write_fake_native_artifacts(artifact_dir)

    key_path = tmp_path / "firmware-signing-key.pem"
    write_private_key(key_path, private_key)

    dist = tmp_path / "dist"
    app_bundle = tmp_path / "app-bundle" / "BridgeFirmware"
    env = base_bundle_env(tmp_path, artifact_dir, app_bundle)
    prepend_fake_clean_git(tmp_path, env)
    env.update(
        {
            "INSTANTLINK_BRIDGE_FIRMWARE_SIGNING_KEY": str(key_path),
            "INSTANTLINK_BRIDGE_FIRMWARE_SIGNING_KEY_ID": "test-release-key",
        }
    )
    result = subprocess.run(
        ["bash", str(BUILD_SCRIPT), "0.9.0"],
        cwd=BRIDGE_ROOT.parent,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    manifest_path = dist / "InstantLinkBridgeFirmware-v0.9.0-linux-aarch64.manifest.json"
    manifest_sig_path = dist / "InstantLinkBridgeFirmware-v0.9.0-linux-aarch64.manifest.sig"
    latest_path = dist / "latest.json"
    latest_sig_path = dist / "latest.json.sig"

    for path in (manifest_path, manifest_sig_path, latest_path, latest_sig_path):
        assert path.exists()
    assert (app_bundle / manifest_sig_path.name).exists()
    assert (app_bundle / latest_sig_path.name).exists()

    trusted = {"test-release-key": signing.public_key_text(private_key)}
    assert signing.verify_manifest_file(manifest_path, manifest_sig_path, trusted).signed is True
    assert signing.verify_manifest_file(latest_path, latest_sig_path, trusted).signed is True


def test_build_script_unsigned_by_default_omits_signature_sidecars(tmp_path: Path) -> None:
    artifact_dir = tmp_path / "artifacts"
    write_fake_native_artifacts(artifact_dir)

    dist = tmp_path / "dist"
    app_bundle = tmp_path / "app-bundle" / "BridgeFirmware"
    result = subprocess.run(
        ["bash", str(BUILD_SCRIPT), "0.9.1"],
        cwd=BRIDGE_ROOT.parent,
        env=base_bundle_env(tmp_path, artifact_dir, app_bundle),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (dist / "InstantLinkBridgeFirmware-v0.9.1-linux-aarch64.manifest.json").exists()
    assert (dist / "latest.json").exists()
    assert not (dist / "InstantLinkBridgeFirmware-v0.9.1-linux-aarch64.manifest.sig").exists()
    assert not (dist / "latest.json.sig").exists()
    assert not (app_bundle / "InstantLinkBridgeFirmware-v0.9.1-linux-aarch64.manifest.sig").exists()
    assert not (app_bundle / "latest.json.sig").exists()
