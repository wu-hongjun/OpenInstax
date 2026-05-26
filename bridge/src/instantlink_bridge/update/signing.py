from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, TypeAlias, cast

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )

    PrivateKeyLike: TypeAlias = Ed25519PrivateKey | bytes
    PublicKeyLike: TypeAlias = Ed25519PrivateKey | Ed25519PublicKey | bytes | str
else:
    PrivateKeyLike: TypeAlias = object
    PublicKeyLike: TypeAlias = object

JsonValue: TypeAlias = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = Mapping[str, JsonValue]

SIGNATURE_SCHEMA_VERSION = 1
SIGNATURE_KIND = "instantlink_bridge_firmware_manifest_signature"
SIGNATURE_ALGORITHM = "Ed25519"
SIGNED_PAYLOAD = "canonical-json-v1"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_BASE64URL_RE = re.compile(r"^[A-Za-z0-9_-]+={0,2}$")
_SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")


class FirmwareSigningError(ValueError):
    """Base error for firmware manifest signing failures."""


class FirmwareSigningUnavailableError(FirmwareSigningError):
    """Raised when the Ed25519 implementation is unavailable."""


class FirmwareManifestError(FirmwareSigningError):
    """Raised when a firmware manifest is malformed or unsafe."""


class FirmwareSignatureError(FirmwareSigningError):
    """Raised when a firmware manifest signature is missing or invalid."""


@dataclass(frozen=True)
class FirmwareManifestVerification:
    signed: bool
    key_id: str | None
    canonical_sha256: str


def canonical_json_bytes(manifest: Mapping[str, object]) -> bytes:
    """Return deterministic canonical JSON bytes for manifest signatures."""

    if not isinstance(manifest, Mapping):
        raise FirmwareManifestError("firmware manifest must be a JSON object")
    try:
        payload = json.dumps(
            manifest,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise FirmwareManifestError("firmware manifest is not canonical JSON serializable") from exc
    return payload.encode("utf-8")


def load_json_object(path: str | Path) -> dict[str, JsonValue]:
    value: object = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise FirmwareManifestError(f"{path} must contain a JSON object")
    return cast(dict[str, JsonValue], value)


def write_json_object(path: str | Path, value: Mapping[str, object]) -> None:
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_private_key(path: str | Path, *, password: str | bytes | None = None) -> Ed25519PrivateKey:
    ed25519, serialization, _invalid_signature = _crypto_modules()
    data = Path(path).read_bytes()
    password_bytes = password.encode("utf-8") if isinstance(password, str) else password

    if data.lstrip().startswith(b"-----BEGIN"):
        key = serialization.load_pem_private_key(data, password=password_bytes)
        if not isinstance(key, ed25519.Ed25519PrivateKey):
            raise FirmwareSignatureError(f"{path} is not an Ed25519 private key")
        return cast("Ed25519PrivateKey", key)

    raw = data.strip()
    if len(raw) != 32:
        raw = _decode_base64url(raw.decode("ascii"))
    if len(raw) != 32:
        raise FirmwareSignatureError("Ed25519 private keys must be 32 raw bytes")
    return cast("Ed25519PrivateKey", ed25519.Ed25519PrivateKey.from_private_bytes(raw))


def public_key_bytes(public_key: PublicKeyLike) -> bytes:
    ed25519, serialization, _invalid_signature = _crypto_modules()
    if isinstance(public_key, ed25519.Ed25519PrivateKey):
        public_key = public_key.public_key()
    if not isinstance(public_key, ed25519.Ed25519PublicKey):
        raise FirmwareSignatureError("expected an Ed25519 public key")
    return cast(
        bytes,
        public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        ),
    )


def public_key_text(public_key: PublicKeyLike) -> str:
    return _encode_base64url(public_key_bytes(public_key))


def key_id_for_public_key(public_key: PublicKeyLike) -> str:
    return f"ed25519-sha256:{hashlib.sha256(public_key_bytes(public_key)).hexdigest()}"


def sign_manifest(
    manifest: Mapping[str, object],
    private_key: PrivateKeyLike,
    *,
    key_id: str | None = None,
) -> dict[str, object]:
    validate_firmware_manifest(manifest)
    private_key = _coerce_private_key(private_key)
    resolved_key_id = key_id or key_id_for_public_key(private_key)
    if not resolved_key_id:
        raise FirmwareSignatureError("firmware signing key id is required")

    signature = private_key.sign(canonical_json_bytes(manifest))
    return {
        "schema_version": SIGNATURE_SCHEMA_VERSION,
        "signature_kind": SIGNATURE_KIND,
        "algorithm": SIGNATURE_ALGORITHM,
        "signed_payload": SIGNED_PAYLOAD,
        "key_id": resolved_key_id,
        "signature": _encode_base64url(signature),
    }


def verify_manifest_signature(
    manifest: Mapping[str, object],
    signature: Mapping[str, object] | None,
    trusted_public_keys: Mapping[str, PublicKeyLike],
    *,
    require_signature: bool = True,
) -> FirmwareManifestVerification:
    validate_firmware_manifest(manifest)
    canonical_payload = canonical_json_bytes(manifest)
    canonical_sha256 = hashlib.sha256(canonical_payload).hexdigest()

    if signature is None:
        if require_signature:
            raise FirmwareSignatureError("firmware manifest signature is required")
        return FirmwareManifestVerification(
            signed=False,
            key_id=None,
            canonical_sha256=canonical_sha256,
        )

    key_id, signature_bytes = _parse_signature(signature)
    if key_id not in trusted_public_keys:
        raise FirmwareSignatureError(f"untrusted firmware signing key id: {key_id}")

    _ed25519, _serialization, invalid_signature = _crypto_modules()
    public_key = _coerce_public_key(trusted_public_keys[key_id])
    try:
        public_key.verify(signature_bytes, canonical_payload)
    except invalid_signature as exc:
        raise FirmwareSignatureError("invalid firmware manifest signature") from exc

    return FirmwareManifestVerification(
        signed=True,
        key_id=key_id,
        canonical_sha256=canonical_sha256,
    )


def verify_manifest_file(
    manifest_path: str | Path,
    signature_path: str | Path | None,
    trusted_public_keys: Mapping[str, Any],
    *,
    require_signature: bool = True,
) -> FirmwareManifestVerification:
    manifest_file = Path(manifest_path)
    if signature_path is not None:
        signature_file = Path(signature_path)
    else:
        signature_file = default_signature_path(manifest_file)
    manifest = load_json_object(manifest_file)
    signature = None
    if signature_file.exists():
        signature = load_json_object(signature_file)
    return verify_manifest_signature(
        manifest,
        signature,
        trusted_public_keys,
        require_signature=require_signature,
    )


def default_signature_path(manifest_path: str | Path) -> Path:
    path = Path(manifest_path)
    if path.name.endswith(".manifest.json"):
        return path.with_name(f"{path.name.removesuffix('.manifest.json')}.manifest.sig")
    return path.with_name(f"{path.name}.sig")


def validate_firmware_manifest(manifest: Mapping[str, object]) -> None:
    if not isinstance(manifest, Mapping):
        raise FirmwareManifestError("firmware manifest must be a JSON object")
    if manifest.get("schema_version") != 1:
        raise FirmwareManifestError("unsupported firmware manifest schema version")
    if manifest.get("package_kind") != "instantlink_bridge_firmware":
        raise FirmwareManifestError("unsupported firmware manifest package kind")

    _validate_required_manifest_fields(manifest)
    _validate_digest_fields(manifest)
    _validate_artifact_references(manifest)


def _validate_required_manifest_fields(manifest: Mapping[str, object]) -> None:
    bridge_version = manifest.get("bridge_version")
    if not isinstance(bridge_version, str) or _SEMVER_RE.fullmatch(bridge_version) is None:
        raise FirmwareManifestError("firmware manifest bridge_version must be semantic version")

    required_api = manifest.get("required_bridge_api_version")
    if not isinstance(required_api, int) or required_api < 1:
        raise FirmwareManifestError("required_bridge_api_version must be a positive integer")
    if not isinstance(manifest.get("migration_notes"), list):
        raise FirmwareManifestError("migration_notes must be a list")
    rollback_version = manifest.get("minimum_rollback_version")
    if rollback_version is not None and not isinstance(rollback_version, str):
        raise FirmwareManifestError("minimum_rollback_version must be a string or null")

    workspace = manifest.get("instantlink_workspace")
    if not isinstance(workspace, Mapping):
        raise FirmwareManifestError("instantlink_workspace metadata is required")
    dirty = workspace.get("dirty")
    if dirty is not False:
        raise FirmwareManifestError("signed firmware manifest must come from a clean workspace")
    commit_sha = workspace.get("commit_sha")
    if not isinstance(commit_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", commit_sha):
        raise FirmwareManifestError("instantlink_workspace.commit_sha must be a full SHA")

    target = manifest.get("target")
    if isinstance(target, Mapping):
        platform = target.get("platform")
        architecture = target.get("architecture")
        if platform != "linux" or architecture != "aarch64":
            raise FirmwareManifestError("firmware manifest target must be linux/aarch64")
    elif isinstance(target, str):
        if target != "linux-aarch64":
            raise FirmwareManifestError("firmware manifest target must be linux-aarch64")
    else:
        raise FirmwareManifestError("firmware manifest target is required")


def _crypto_modules() -> tuple[Any, Any, type[Exception]]:
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519
    except ImportError as exc:
        raise FirmwareSigningUnavailableError(
            "cryptography with Ed25519 support is required for firmware manifest signing"
        ) from exc
    return ed25519, serialization, InvalidSignature


def _coerce_private_key(private_key: PrivateKeyLike) -> Ed25519PrivateKey:
    ed25519, _serialization, _invalid_signature = _crypto_modules()
    if isinstance(private_key, ed25519.Ed25519PrivateKey):
        return cast("Ed25519PrivateKey", private_key)
    if isinstance(private_key, bytes) and len(private_key) == 32:
        return cast(
            "Ed25519PrivateKey",
            ed25519.Ed25519PrivateKey.from_private_bytes(private_key),
        )
    raise FirmwareSignatureError("expected an Ed25519 private key")


def _coerce_public_key(public_key: PublicKeyLike) -> Ed25519PublicKey:
    ed25519, serialization, _invalid_signature = _crypto_modules()
    if isinstance(public_key, ed25519.Ed25519PublicKey):
        return cast("Ed25519PublicKey", public_key)
    if isinstance(public_key, str):
        return _coerce_public_key(public_key.encode("utf-8"))
    if not isinstance(public_key, bytes):
        raise FirmwareSignatureError("expected an Ed25519 public key")

    data = public_key.strip()
    if data.startswith(b"-----BEGIN"):
        loaded = serialization.load_pem_public_key(data)
        if not isinstance(loaded, ed25519.Ed25519PublicKey):
            raise FirmwareSignatureError("trusted firmware public key is not Ed25519")
        return cast("Ed25519PublicKey", loaded)

    raw = data if len(data) == 32 else _decode_base64url(data.decode("ascii"))
    if len(raw) != 32:
        raise FirmwareSignatureError("trusted Ed25519 public keys must be 32 raw bytes")
    return cast("Ed25519PublicKey", ed25519.Ed25519PublicKey.from_public_bytes(raw))


def _parse_signature(signature: Mapping[str, object]) -> tuple[str, bytes]:
    if not isinstance(signature, Mapping):
        raise FirmwareSignatureError("firmware signature must be a JSON object")
    if signature.get("schema_version") != SIGNATURE_SCHEMA_VERSION:
        raise FirmwareSignatureError("unsupported firmware signature schema version")
    if signature.get("signature_kind") != SIGNATURE_KIND:
        raise FirmwareSignatureError("unsupported firmware signature kind")
    if signature.get("algorithm") != SIGNATURE_ALGORITHM:
        raise FirmwareSignatureError("unsupported firmware signature algorithm")
    if signature.get("signed_payload") != SIGNED_PAYLOAD:
        raise FirmwareSignatureError("unsupported firmware signature payload")

    key_id = signature.get("key_id")
    if not isinstance(key_id, str) or not key_id:
        raise FirmwareSignatureError("firmware signature key id is required")

    encoded_signature = signature.get("signature")
    if not isinstance(encoded_signature, str):
        raise FirmwareSignatureError("firmware signature bytes are required")
    signature_bytes = _decode_base64url(encoded_signature)
    if len(signature_bytes) != 64:
        raise FirmwareSignatureError("Ed25519 signatures must be 64 bytes")
    return key_id, signature_bytes


def _validate_digest_fields(value: object, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if isinstance(key, str) and (key == "sha256" or key.endswith("_sha256")):
                if not isinstance(child, str) or not _SHA256_RE.fullmatch(child):
                    raise FirmwareManifestError(f"invalid SHA-256 digest at {child_path}")
            _validate_digest_fields(child, child_path)
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _validate_digest_fields(child, f"{path}[{index}]")


def _validate_artifact_references(manifest: Mapping[str, object]) -> None:
    archive = manifest.get("archive")
    if isinstance(archive, Mapping) and "name" in archive:
        _validate_artifact_name(archive["name"], "$.archive.name")

    for field in ("archive_name", "manifest_name", "checksum_name"):
        if field in manifest:
            _validate_artifact_name(manifest[field], f"$.{field}")

    python = manifest.get("python")
    if isinstance(python, Mapping) and "constraints" in python:
        _validate_relative_artifact_path(python["constraints"], "$.python.constraints")

    install = manifest.get("install")
    if isinstance(install, Mapping) and "script" in install:
        _validate_relative_artifact_path(install["script"], "$.install.script")

    native_artifacts = manifest.get("native_artifacts")
    if not isinstance(native_artifacts, Mapping):
        return
    for artifact_name, artifact in native_artifacts.items():
        if isinstance(artifact, Mapping) and "path" in artifact:
            _validate_relative_artifact_path(
                artifact["path"],
                f"$.native_artifacts.{artifact_name}.path",
            )


def _validate_artifact_name(value: object, path: str) -> None:
    if not isinstance(value, str) or not value:
        raise FirmwareManifestError(f"artifact name at {path} must be a non-empty string")
    if "\x00" in value or "/" in value or "\\" in value:
        raise FirmwareManifestError(f"artifact name at {path} must be a basename")
    if value in {".", ".."}:
        raise FirmwareManifestError(f"artifact name at {path} must not traverse directories")


def _validate_relative_artifact_path(value: object, path: str) -> None:
    if not isinstance(value, str) or not value:
        raise FirmwareManifestError(f"artifact path at {path} must be a non-empty string")
    if "\x00" in value or "\\" in value or "//" in value:
        raise FirmwareManifestError(f"artifact path at {path} must be a clean relative path")

    parsed = PurePosixPath(value)
    if parsed.is_absolute():
        raise FirmwareManifestError(f"artifact path at {path} must be relative")
    if any(part in {"", ".", ".."} for part in parsed.parts):
        raise FirmwareManifestError(f"artifact path at {path} must not traverse directories")


def _encode_base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _decode_base64url(value: str) -> bytes:
    if not value or not _BASE64URL_RE.fullmatch(value):
        raise FirmwareSignatureError("invalid base64url encoding")
    try:
        padded = value + ("=" * (-len(value) % 4))
        return base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as exc:
        raise FirmwareSignatureError("invalid base64url encoding") from exc
