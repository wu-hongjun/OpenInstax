from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
from collections.abc import Callable, Mapping
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
DowngradePolicy: TypeAlias = Callable[[str, str], bool]

FIRMWARE_SCHEMA_VERSION = 1
PACKAGE_KIND = "instantlink_bridge_firmware"
PACKAGE_MANIFEST_KIND = "instantlink_bridge_firmware_package_manifest"
RELEASE_INDEX_KIND = "instantlink_bridge_firmware_release_index"
SIGNATURE_SCHEMA_VERSION = 1
SIGNATURE_KIND = "instantlink_bridge_firmware_manifest_signature"
PACKAGE_MANIFEST_SIGNATURE_KIND = SIGNATURE_KIND
RELEASE_INDEX_SIGNATURE_KIND = "instantlink_bridge_firmware_release_index_signature"
SIGNATURE_ALGORITHM = "Ed25519"
SIGNED_PAYLOAD = "canonical-json-v1"
SUPPORTED_BRIDGE_API_VERSION = 1
LINUX_AARCH64_TARGET = "linux-aarch64"

TRUSTED_PUBLIC_KEYS_ENV = "INSTANTLINK_BRIDGE_FIRMWARE_TRUSTED_PUBLIC_KEYS"
TRUSTED_PUBLIC_KEYS_FILE_ENV = "INSTANTLINK_BRIDGE_FIRMWARE_TRUSTED_PUBLIC_KEYS_FILE"

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


@dataclass(frozen=True, slots=True)
class TrustedFirmwarePublicKey:
    key_id: str
    public_key: PublicKeyLike


EMBEDDED_TRUSTED_FIRMWARE_PUBLIC_KEYS: tuple[TrustedFirmwarePublicKey, ...] = ()


@dataclass(frozen=True, slots=True)
class TrustedFirmwareKeyStore:
    """Trusted firmware release signing keys from embedded, config, and env sources."""

    trusted_public_keys: Mapping[str, PublicKeyLike]

    @classmethod
    def empty(cls) -> TrustedFirmwareKeyStore:
        return cls({})

    @classmethod
    def from_mapping(
        cls,
        trusted_public_keys: Mapping[str, PublicKeyLike],
    ) -> TrustedFirmwareKeyStore:
        return cls(dict(trusted_public_keys))

    @classmethod
    def from_records(
        cls,
        records: tuple[TrustedFirmwarePublicKey, ...],
    ) -> TrustedFirmwareKeyStore:
        return cls({record.key_id: record.public_key for record in records})

    def merged(self, *stores: TrustedFirmwareKeyStore) -> TrustedFirmwareKeyStore:
        merged_keys = dict(self.trusted_public_keys)
        for store in stores:
            merged_keys.update(store.trusted_public_keys)
        return TrustedFirmwareKeyStore(merged_keys)

    def as_mapping(self) -> Mapping[str, PublicKeyLike]:
        return self.trusted_public_keys


@dataclass(frozen=True, slots=True)
class FirmwareBundleVerification:
    bundle_dir: Path
    latest_path: Path
    manifest_path: Path
    archive_path: Path
    checksum_path: Path
    bridge_version: str
    latest_signature: FirmwareManifestVerification
    manifest_signature: FirmwareManifestVerification
    latest: JsonObject
    manifest: JsonObject
    archive_sha256: str
    manifest_sha256: str
    checksum_sha256: str


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


def embedded_trusted_firmware_key_store() -> TrustedFirmwareKeyStore:
    return TrustedFirmwareKeyStore.from_records(EMBEDDED_TRUSTED_FIRMWARE_PUBLIC_KEYS)


def trusted_firmware_key_store_from_config(
    *,
    config: object | None = None,
    config_path: str | Path | None = None,
) -> TrustedFirmwareKeyStore:
    if config is None:
        from instantlink_bridge.config import DEFAULT_CONFIG_PATH, load_config

        config = load_config(Path(config_path) if config_path is not None else DEFAULT_CONFIG_PATH)

    firmware_config = getattr(config, "firmware", None)
    raw_records = getattr(firmware_config, "trusted_public_keys", ())
    records: dict[str, PublicKeyLike] = {}
    for index, record in enumerate(raw_records):
        key_id = getattr(record, "key_id", None)
        public_key = getattr(record, "public_key", None)
        if not isinstance(key_id, str) or not key_id:
            raise FirmwareSignatureError(f"configured firmware trusted key {index} missing key id")
        if not isinstance(public_key, str) or not public_key:
            raise FirmwareSignatureError(
                f"configured firmware trusted key {key_id} missing public key"
            )
        records[key_id] = public_key
    return TrustedFirmwareKeyStore.from_mapping(records)


def trusted_firmware_key_store_from_environment(
    environ: Mapping[str, str] | None = None,
) -> TrustedFirmwareKeyStore:
    env = os.environ if environ is None else environ
    records: dict[str, PublicKeyLike] = {}

    inline_keys = env.get(TRUSTED_PUBLIC_KEYS_ENV)
    if inline_keys:
        records.update(_parse_trusted_key_records(json.loads(inline_keys), TRUSTED_PUBLIC_KEYS_ENV))

    keys_file = env.get(TRUSTED_PUBLIC_KEYS_FILE_ENV)
    if keys_file:
        path = Path(keys_file)
        records.update(
            _parse_trusted_key_records(
                json.loads(path.read_text(encoding="utf-8")),
                str(path),
            )
        )

    return TrustedFirmwareKeyStore.from_mapping(records)


def default_trusted_firmware_key_store(
    *,
    config: object | None = None,
    config_path: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> TrustedFirmwareKeyStore:
    return (
        embedded_trusted_firmware_key_store()
        .merged(trusted_firmware_key_store_from_config(config=config, config_path=config_path))
        .merged(trusted_firmware_key_store_from_environment(environ))
    )


def sign_manifest(
    manifest: Mapping[str, object],
    private_key: PrivateKeyLike,
    *,
    key_id: str | None = None,
) -> dict[str, object]:
    metadata_kind = firmware_metadata_kind(manifest)
    validate_firmware_metadata(manifest, expected_metadata_kind=metadata_kind)
    private_key = _coerce_private_key(private_key)
    resolved_key_id = key_id or key_id_for_public_key(private_key)
    if not resolved_key_id:
        raise FirmwareSignatureError("firmware signing key id is required")

    signature = private_key.sign(canonical_json_bytes(manifest))
    return {
        "schema_version": SIGNATURE_SCHEMA_VERSION,
        "signature_kind": signature_kind_for_metadata_kind(metadata_kind),
        "algorithm": SIGNATURE_ALGORITHM,
        "signed_payload": SIGNED_PAYLOAD,
        "key_id": resolved_key_id,
        "signature": _encode_base64url(signature),
    }


def verify_manifest_signature(
    manifest: Mapping[str, object],
    signature: Mapping[str, object] | None,
    trusted_public_keys: Mapping[str, PublicKeyLike] | TrustedFirmwareKeyStore | None = None,
    *,
    require_signature: bool = True,
    expected_metadata_kind: str | None = None,
) -> FirmwareManifestVerification:
    metadata_kind = expected_metadata_kind or firmware_metadata_kind(manifest)
    validate_firmware_metadata(manifest, expected_metadata_kind=metadata_kind)
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

    key_id, signature_bytes = _parse_signature(
        signature,
        expected_signature_kind=signature_kind_for_metadata_kind(metadata_kind),
    )
    trusted_keys = _trusted_public_keys_mapping(trusted_public_keys)
    if not trusted_keys:
        raise FirmwareSignatureError("no trusted firmware signing keys are configured")
    if key_id not in trusted_keys:
        raise FirmwareSignatureError(f"untrusted firmware signing key id: {key_id}")

    _ed25519, _serialization, invalid_signature = _crypto_modules()
    public_key = _coerce_public_key(trusted_keys[key_id])
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
    trusted_public_keys: Mapping[str, PublicKeyLike] | TrustedFirmwareKeyStore | None = None,
    *,
    require_signature: bool = True,
    expected_metadata_kind: str | None = None,
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
        expected_metadata_kind=expected_metadata_kind,
    )


def verify_firmware_bundle_directory(
    bundle_dir: str | Path,
    trusted_public_keys: Mapping[str, PublicKeyLike] | TrustedFirmwareKeyStore | None = None,
    *,
    supported_bridge_api_version: int = SUPPORTED_BRIDGE_API_VERSION,
    current_bridge_version: str | None = None,
    downgrade_policy: DowngradePolicy | None = None,
    config: object | None = None,
    config_path: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> FirmwareBundleVerification:
    root = Path(bundle_dir)
    if not root.is_dir():
        raise FirmwareManifestError(f"firmware bundle directory does not exist: {root}")

    trusted_keys = _trusted_bundle_public_keys_mapping(
        trusted_public_keys,
        config=config,
        config_path=config_path,
        environ=environ,
    )

    latest_path = _require_file(root / "latest.json", "firmware release index")
    latest_signature_path = _require_file(
        default_signature_path(latest_path),
        "firmware release index signature",
    )
    latest = load_json_object(latest_path)
    latest_signature = verify_manifest_file(
        latest_path,
        latest_signature_path,
        trusted_keys,
        expected_metadata_kind=RELEASE_INDEX_KIND,
    )

    manifest_name = _required_artifact_name(latest, "manifest_name")
    archive_name = _required_artifact_name(latest, "archive_name")
    checksum_name = _required_artifact_name(latest, "checksum_name")
    manifest_path = _require_file(root / manifest_name, "firmware package manifest")
    manifest_signature_path = _require_file(
        default_signature_path(manifest_path),
        "firmware package manifest signature",
    )
    archive_path = _require_file(root / archive_name, "firmware archive")
    checksum_path = _require_file(root / checksum_name, "firmware archive checksum")

    manifest_sha256 = _required_sha256_field(latest, "manifest_sha256")
    archive_sha256 = _required_sha256_field(latest, "archive_sha256")
    checksum_sha256 = _required_sha256_field(latest, "checksum_sha256")
    _verify_file_sha256(manifest_path, manifest_sha256, "firmware package manifest")
    _verify_file_sha256(checksum_path, checksum_sha256, "firmware archive checksum")
    checksum_archive_sha256 = _archive_sha256_from_checksum_file(checksum_path, archive_name)
    if checksum_archive_sha256 != archive_sha256:
        raise FirmwareManifestError("firmware checksum file does not match release index")
    _verify_file_sha256(archive_path, archive_sha256, "firmware archive")

    manifest = load_json_object(manifest_path)
    manifest_signature = verify_manifest_file(
        manifest_path,
        manifest_signature_path,
        trusted_keys,
        expected_metadata_kind=PACKAGE_MANIFEST_KIND,
    )

    _verify_package_matches_release_index(latest, manifest, archive_name=archive_name)
    _verify_api_compatibility(latest, supported_bridge_api_version)
    _verify_api_compatibility(manifest, supported_bridge_api_version)
    bridge_version = _required_string_field(manifest, "bridge_version")
    _verify_downgrade_policy(
        bridge_version,
        current_bridge_version=current_bridge_version,
        downgrade_policy=downgrade_policy,
    )

    return FirmwareBundleVerification(
        bundle_dir=root,
        latest_path=latest_path,
        manifest_path=manifest_path,
        archive_path=archive_path,
        checksum_path=checksum_path,
        bridge_version=bridge_version,
        latest_signature=latest_signature,
        manifest_signature=manifest_signature,
        latest=latest,
        manifest=manifest,
        archive_sha256=archive_sha256,
        manifest_sha256=manifest_sha256,
        checksum_sha256=checksum_sha256,
    )


def default_signature_path(manifest_path: str | Path) -> Path:
    path = Path(manifest_path)
    if path.name.endswith(".manifest.json"):
        return path.with_name(f"{path.name.removesuffix('.manifest.json')}.manifest.sig")
    return path.with_name(f"{path.name}.sig")


def firmware_metadata_kind(manifest: Mapping[str, object]) -> str:
    if not isinstance(manifest, Mapping):
        raise FirmwareManifestError("firmware manifest must be a JSON object")
    explicit_kind = manifest.get("manifest_kind")
    if explicit_kind in {PACKAGE_MANIFEST_KIND, RELEASE_INDEX_KIND}:
        # mypy 1.11 does not narrow `object` to `str` on this membership
        # check — the cast was redundant on the mypy revision active when
        # b0eefa2 landed, but is needed again under the current pin to
        # avoid an `object → str` return-value error.
        return cast(str, explicit_kind)
    if explicit_kind is not None:
        raise FirmwareManifestError("unsupported firmware manifest kind")
    if "manifest_name" in manifest or "archive_name" in manifest:
        return RELEASE_INDEX_KIND
    if "archive" in manifest:
        return PACKAGE_MANIFEST_KIND
    raise FirmwareManifestError("firmware manifest kind is required")


def signature_kind_for_metadata_kind(metadata_kind: str) -> str:
    if metadata_kind == PACKAGE_MANIFEST_KIND:
        return PACKAGE_MANIFEST_SIGNATURE_KIND
    if metadata_kind == RELEASE_INDEX_KIND:
        return RELEASE_INDEX_SIGNATURE_KIND
    raise FirmwareManifestError("unsupported firmware manifest kind")


def validate_firmware_metadata(
    manifest: Mapping[str, object],
    *,
    expected_metadata_kind: str | None = None,
) -> None:
    metadata_kind = firmware_metadata_kind(manifest)
    if expected_metadata_kind is not None and metadata_kind != expected_metadata_kind:
        raise FirmwareManifestError(
            f"expected firmware manifest kind {expected_metadata_kind}, got {metadata_kind}"
        )
    if metadata_kind == PACKAGE_MANIFEST_KIND:
        validate_firmware_manifest(manifest)
        return
    if metadata_kind == RELEASE_INDEX_KIND:
        validate_release_index(manifest)
        return
    raise FirmwareManifestError("unsupported firmware manifest kind")


def validate_firmware_manifest(manifest: Mapping[str, object]) -> None:
    _validate_metadata_header(manifest, expected_metadata_kind=PACKAGE_MANIFEST_KIND)

    _validate_required_manifest_fields(manifest)
    _validate_package_manifest_fields(manifest)
    _validate_digest_fields(manifest)
    _validate_artifact_references(manifest)


def validate_release_index(manifest: Mapping[str, object]) -> None:
    _validate_metadata_header(manifest, expected_metadata_kind=RELEASE_INDEX_KIND)

    _validate_required_manifest_fields(manifest)
    _validate_release_index_fields(manifest)
    _validate_digest_fields(manifest)
    _validate_artifact_references(manifest)


def _validate_metadata_header(
    manifest: Mapping[str, object],
    *,
    expected_metadata_kind: str,
) -> None:
    if not isinstance(manifest, Mapping):
        raise FirmwareManifestError("firmware manifest must be a JSON object")
    if manifest.get("schema_version") != FIRMWARE_SCHEMA_VERSION:
        raise FirmwareManifestError("unsupported firmware manifest schema version")
    if manifest.get("package_kind") != PACKAGE_KIND:
        raise FirmwareManifestError("unsupported firmware manifest package kind")
    metadata_kind = firmware_metadata_kind(manifest)
    if metadata_kind != expected_metadata_kind:
        raise FirmwareManifestError(
            f"expected firmware manifest kind {expected_metadata_kind}, got {metadata_kind}"
        )


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


def _validate_package_manifest_fields(manifest: Mapping[str, object]) -> None:
    archive = manifest.get("archive")
    if not isinstance(archive, Mapping):
        raise FirmwareManifestError("firmware package manifest archive metadata is required")
    _validate_artifact_name(archive.get("name"), "$.archive.name")
    compression = archive.get("compression")
    if compression != "gzip":
        raise FirmwareManifestError("firmware package archive compression must be gzip")

    native_artifacts = manifest.get("native_artifacts")
    if not isinstance(native_artifacts, Mapping) or not native_artifacts:
        raise FirmwareManifestError("firmware package native_artifacts metadata is required")

    install = manifest.get("install")
    if not isinstance(install, Mapping):
        raise FirmwareManifestError("firmware package install metadata is required")
    _validate_relative_artifact_path(install.get("script"), "$.install.script")


def _validate_release_index_fields(manifest: Mapping[str, object]) -> None:
    for field in (
        "archive_name",
        "archive_sha256",
        "manifest_name",
        "manifest_sha256",
        "checksum_name",
        "checksum_sha256",
    ):
        if field not in manifest:
            raise FirmwareManifestError(f"firmware release index missing {field}")
    for field in ("archive_name", "manifest_name", "checksum_name"):
        _validate_artifact_name(manifest.get(field), f"$.{field}")


def _trusted_public_keys_mapping(
    trusted_public_keys: Mapping[str, PublicKeyLike] | TrustedFirmwareKeyStore | None,
    *,
    config: object | None = None,
    config_path: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> Mapping[str, PublicKeyLike]:
    if trusted_public_keys is None:
        return default_trusted_firmware_key_store(
            config=config,
            config_path=config_path,
            environ=environ,
        ).as_mapping()
    if isinstance(trusted_public_keys, TrustedFirmwareKeyStore):
        return trusted_public_keys.as_mapping()
    return trusted_public_keys


def _trusted_bundle_public_keys_mapping(
    trusted_public_keys: Mapping[str, PublicKeyLike] | TrustedFirmwareKeyStore | None,
    *,
    config: object | None,
    config_path: str | Path | None,
    environ: Mapping[str, str] | None,
) -> Mapping[str, PublicKeyLike]:
    key_store = default_trusted_firmware_key_store(
        config=config,
        config_path=config_path,
        environ=environ,
    )
    if trusted_public_keys is not None:
        key_store = key_store.merged(_trusted_key_store_from_input(trusted_public_keys))
    return key_store.as_mapping()


def _trusted_key_store_from_input(
    trusted_public_keys: Mapping[str, PublicKeyLike] | TrustedFirmwareKeyStore,
) -> TrustedFirmwareKeyStore:
    if isinstance(trusted_public_keys, TrustedFirmwareKeyStore):
        return trusted_public_keys
    return TrustedFirmwareKeyStore.from_mapping(trusted_public_keys)


def _parse_trusted_key_records(value: object, source: str) -> dict[str, PublicKeyLike]:
    if isinstance(value, Mapping):
        records: dict[str, PublicKeyLike] = {}
        for key_id, public_key in value.items():
            if not isinstance(key_id, str) or not key_id:
                raise FirmwareSignatureError(f"{source} contains a trusted key without key id")
            if not isinstance(public_key, str) or not public_key:
                raise FirmwareSignatureError(
                    f"{source} trusted key {key_id} must be a non-empty string"
                )
            records[key_id] = public_key
        return records

    if isinstance(value, list):
        records = {}
        for index, item in enumerate(value):
            if not isinstance(item, Mapping):
                raise FirmwareSignatureError(f"{source}[{index}] must be an object")
            key_id = item.get("key_id")
            public_key = item.get("public_key")
            if not isinstance(key_id, str) or not key_id:
                raise FirmwareSignatureError(f"{source}[{index}].key_id is required")
            if not isinstance(public_key, str) or not public_key:
                raise FirmwareSignatureError(f"{source}[{index}].public_key is required")
            records[key_id] = public_key
        return records

    raise FirmwareSignatureError(f"{source} must be a JSON object or array")


def _required_string_field(manifest: Mapping[str, object], field: str) -> str:
    value = manifest.get(field)
    if not isinstance(value, str) or not value:
        raise FirmwareManifestError(f"firmware metadata missing {field}")
    return value


def _required_artifact_name(manifest: Mapping[str, object], field: str) -> str:
    value = _required_string_field(manifest, field)
    _validate_artifact_name(value, f"$.{field}")
    return value


def _required_sha256_field(manifest: Mapping[str, object], field: str) -> str:
    value = _required_string_field(manifest, field)
    if _SHA256_RE.fullmatch(value) is None:
        raise FirmwareManifestError(f"invalid SHA-256 digest at $.{field}")
    return value


def _require_file(path: Path, label: str) -> Path:
    if not path.is_file():
        raise FirmwareManifestError(f"missing {label}: {path}")
    return path


def _verify_file_sha256(path: Path, expected_sha256: str, label: str) -> None:
    actual_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual_sha256 != expected_sha256:
        raise FirmwareManifestError(
            f"{label} SHA-256 mismatch: expected {expected_sha256}, got {actual_sha256}"
        )


def _archive_sha256_from_checksum_file(checksum_path: Path, archive_name: str) -> str:
    checksum_lines = checksum_path.read_text(encoding="utf-8").splitlines()
    for line_number, raw_line in enumerate(checksum_lines, 1):
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            raise FirmwareManifestError(f"malformed checksum line {line_number}")
        digest, name = parts
        name = name.removeprefix("*")
        if _SHA256_RE.fullmatch(digest) is None:
            raise FirmwareManifestError(f"invalid SHA-256 digest in checksum line {line_number}")
        if name == archive_name:
            return digest
    raise FirmwareManifestError("firmware checksum file does not list archive")


def _verify_package_matches_release_index(
    latest: Mapping[str, object],
    manifest: Mapping[str, object],
    *,
    archive_name: str,
) -> None:
    latest_version = _required_string_field(latest, "bridge_version")
    manifest_version = _required_string_field(manifest, "bridge_version")
    if latest_version != manifest_version:
        raise FirmwareManifestError("release index bridge_version does not match package manifest")

    latest_required_api = latest.get("required_bridge_api_version")
    manifest_required_api = manifest.get("required_bridge_api_version")
    if latest_required_api != manifest_required_api:
        raise FirmwareManifestError(
            "release index required_bridge_api_version does not match package manifest"
        )

    archive = manifest.get("archive")
    if not isinstance(archive, Mapping) or archive.get("name") != archive_name:
        raise FirmwareManifestError("release index archive_name does not match package manifest")

    if (
        _target_label(latest) != LINUX_AARCH64_TARGET
        or _target_label(manifest) != LINUX_AARCH64_TARGET
    ):
        raise FirmwareManifestError("firmware package target must be linux-aarch64")


def _verify_api_compatibility(
    manifest: Mapping[str, object],
    supported_bridge_api_version: int,
) -> None:
    required_api = manifest.get("required_bridge_api_version")
    if not isinstance(required_api, int) or required_api < 1:
        raise FirmwareManifestError("required_bridge_api_version must be a positive integer")
    if required_api > supported_bridge_api_version:
        raise FirmwareManifestError(
            "firmware package requires unsupported Bridge API version "
            f"{required_api}; supported version is {supported_bridge_api_version}"
        )


def _verify_downgrade_policy(
    bridge_version: str,
    *,
    current_bridge_version: str | None,
    downgrade_policy: DowngradePolicy | None,
) -> None:
    if current_bridge_version is None or not _is_semver_downgrade(
        bridge_version,
        current_bridge_version,
    ):
        return
    if downgrade_policy is not None and downgrade_policy(bridge_version, current_bridge_version):
        return
    raise FirmwareManifestError(
        f"firmware downgrade from {current_bridge_version} to {bridge_version} is not allowed"
    )


def _is_semver_downgrade(candidate: str, current: str) -> bool:
    if _SEMVER_RE.fullmatch(candidate) is None or _SEMVER_RE.fullmatch(current) is None:
        return False
    return _semver_core(candidate) < _semver_core(current)


def _semver_core(version: str) -> tuple[int, int, int]:
    core = re.split(r"[-+]", version, maxsplit=1)[0]
    major, minor, patch = core.split(".")
    return (int(major), int(minor), int(patch))


def _target_label(manifest: Mapping[str, object]) -> str:
    target = manifest.get("target")
    if isinstance(target, str):
        return target
    if isinstance(target, Mapping):
        platform = target.get("platform")
        architecture = target.get("architecture")
        if isinstance(platform, str) and isinstance(architecture, str):
            return f"{platform}-{architecture}"
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


def _parse_signature(
    signature: Mapping[str, object],
    *,
    expected_signature_kind: str,
) -> tuple[str, bytes]:
    if not isinstance(signature, Mapping):
        raise FirmwareSignatureError("firmware signature must be a JSON object")
    if signature.get("schema_version") != SIGNATURE_SCHEMA_VERSION:
        raise FirmwareSignatureError("unsupported firmware signature schema version")
    if signature.get("signature_kind") != expected_signature_kind:
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
