"""Local authorization primitives for Bridge management requests."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

AUTH_SCHEMA_VERSION = 1
CLIENT_RECORD_KIND = "instantlink_bridge_management_client"
REQUEST_SIGNATURE_CONTEXT = "instantlink-bridge-management-v1"

CLIENT_ID_HEADER = "X-Bridge-Client-Id"
TIMESTAMP_HEADER = "X-Bridge-Timestamp"
NONCE_HEADER = "X-Bridge-Nonce"
SIGNATURE_HEADER = "X-Bridge-Signature"

DEFAULT_MAX_REQUEST_AGE_S = 300
DEFAULT_MAX_FUTURE_SKEW_S = 30
DEFAULT_NONCE_TTL_S = DEFAULT_MAX_REQUEST_AGE_S + DEFAULT_MAX_FUTURE_SKEW_S

_CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_NONCE_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_BASE64URL_RE = re.compile(r"^[A-Za-z0-9_-]+={0,2}$")

JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject = dict[str, JsonValue]
NowSeconds = Callable[[], int]


class ManagementAuthError(ValueError):
    """Base error for management authorization failures."""

    def __init__(self, message: str, *, error_code: str = "auth_invalid") -> None:
        super().__init__(message)
        self.error_code = error_code


class ClientRecordError(ManagementAuthError):
    """Raised when a management client record is unsafe or malformed."""


class PairingWindowError(ManagementAuthError):
    """Raised when a pairing window is closed or the confirmation code is wrong."""


@dataclass(frozen=True, slots=True)
class AuthorizedClient:
    """One Mac authorized to call Bridge management endpoints."""

    client_id: str
    client_name: str
    public_key: str
    created_at: str
    revoked_at: str | None = None

    def __post_init__(self) -> None:
        validate_client_id(self.client_id)
        if not self.client_name.strip():
            raise ClientRecordError("client_name is required", error_code="client_record_invalid")
        parse_public_key(self.public_key)

    @property
    def revoked(self) -> bool:
        return self.revoked_at is not None

    def to_dict(self) -> JsonObject:
        return {
            "schema_version": AUTH_SCHEMA_VERSION,
            "record_kind": CLIENT_RECORD_KIND,
            "client_id": self.client_id,
            "client_name": self.client_name,
            "public_key": self.public_key,
            "created_at": self.created_at,
            "revoked_at": self.revoked_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> AuthorizedClient:
        if value.get("schema_version") != AUTH_SCHEMA_VERSION:
            raise ClientRecordError(
                "unsupported client record schema version",
                error_code="client_record_invalid",
            )
        if value.get("record_kind") != CLIENT_RECORD_KIND:
            raise ClientRecordError(
                "unsupported client record kind",
                error_code="client_record_invalid",
            )
        return cls(
            client_id=_required_str(value, "client_id"),
            client_name=_required_str(value, "client_name"),
            public_key=_required_str(value, "public_key"),
            created_at=_required_str(value, "created_at"),
            revoked_at=_optional_str(value, "revoked_at"),
        )


@dataclass(frozen=True, slots=True)
class PairingWindow:
    """Short physical-code authorization window opened from the Bridge LCD."""

    confirmation_code: str
    opened_at: int
    expires_at: int

    def verify(self, confirmation_code: str, *, now: int) -> None:
        if now > self.expires_at:
            raise PairingWindowError("pairing window is closed", error_code="pairing_not_open")
        if not self.confirmation_code or confirmation_code != self.confirmation_code:
            raise PairingWindowError(
                "pairing confirmation code did not match",
                error_code="pairing_code_invalid",
            )


@dataclass(frozen=True, slots=True)
class AuthorizedRequest:
    """A successfully verified signed management request."""

    client: AuthorizedClient
    timestamp: int
    nonce: str
    body_sha256: str


class ClientStore:
    """Filesystem-backed management client store."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def client_path(self, client_id: str) -> Path:
        safe_client_id = validate_client_id(client_id)
        return self.root / f"{safe_client_id}.json"

    def read_client(self, client_id: str) -> AuthorizedClient:
        path = self.client_path(client_id)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ManagementAuthError("management client is not authorized") from exc
        if not isinstance(value, dict):
            raise ClientRecordError("client record must be a JSON object")
        return AuthorizedClient.from_dict(value)

    def save_client(self, client: AuthorizedClient) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        path = self.client_path(client.client_id)
        tmp_path = path.with_name(f".{path.name}.tmp-{os.getpid()}")
        tmp_path.write_text(
            json.dumps(client.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, path)

    def revoke_client(self, client_id: str, *, revoked_at: str | None = None) -> AuthorizedClient:
        client = self.read_client(client_id)
        revoked = AuthorizedClient(
            client_id=client.client_id,
            client_name=client.client_name,
            public_key=client.public_key,
            created_at=client.created_at,
            revoked_at=revoked_at or utc_timestamp(),
        )
        self.save_client(revoked)
        return revoked

    def list_clients(self) -> tuple[AuthorizedClient, ...]:
        if not self.root.exists():
            return ()
        clients: list[AuthorizedClient] = []
        for path in sorted(self.root.glob("*.json")):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ClientRecordError(f"client record could not be read: {path.name}") from exc
            if not isinstance(value, dict):
                raise ClientRecordError(f"client record must be an object: {path.name}")
            clients.append(AuthorizedClient.from_dict(value))
        return tuple(clients)


class MemoryNonceStore:
    """Small replay cache for signed management request nonces."""

    def __init__(self) -> None:
        self._nonces: dict[tuple[str, str], int] = {}

    def remember(self, client_id: str, nonce: str, *, now: int, ttl_s: int) -> bool:
        self._purge(now)
        key = (client_id, nonce)
        if key in self._nonces:
            return False
        self._nonces[key] = now + ttl_s
        return True

    def _purge(self, now: int) -> None:
        expired = [key for key, expires_at in self._nonces.items() if expires_at <= now]
        for key in expired:
            del self._nonces[key]


class SignedRequestVerifier:
    """Verify Ed25519-signed Bridge management requests."""

    def __init__(
        self,
        client_store: ClientStore,
        *,
        nonce_store: MemoryNonceStore | None = None,
        max_request_age_s: int = DEFAULT_MAX_REQUEST_AGE_S,
        max_future_skew_s: int = DEFAULT_MAX_FUTURE_SKEW_S,
        now_seconds: NowSeconds | None = None,
    ) -> None:
        self.client_store = client_store
        self.nonce_store = nonce_store or MemoryNonceStore()
        self.max_request_age_s = max_request_age_s
        self.max_future_skew_s = max_future_skew_s
        self.now_seconds = now_seconds or current_unix_seconds

    def verify(
        self,
        *,
        headers: Mapping[str, str],
        method: str,
        path: str,
        body: bytes,
    ) -> AuthorizedRequest:
        now = self.now_seconds()
        client_id = validate_client_id(_required_header(headers, CLIENT_ID_HEADER))
        timestamp = _parse_timestamp(_required_header(headers, TIMESTAMP_HEADER), now=now)
        if timestamp < now - self.max_request_age_s:
            raise ManagementAuthError("management request timestamp is stale", error_code="stale")
        if timestamp > now + self.max_future_skew_s:
            raise ManagementAuthError(
                "management request timestamp is in the future",
                error_code="timestamp_future",
            )

        nonce = _required_header(headers, NONCE_HEADER)
        if _NONCE_RE.fullmatch(nonce) is None:
            raise ManagementAuthError("management request nonce is invalid", error_code="bad_nonce")

        client = self.client_store.read_client(client_id)
        if client.revoked:
            raise ManagementAuthError("management client is revoked", error_code="client_revoked")

        body_sha256 = hashlib.sha256(body).hexdigest()
        payload = canonical_request_payload(
            method=method,
            path=path,
            body_sha256=body_sha256,
            timestamp=timestamp,
            nonce=nonce,
        )
        signature = decode_base64url(_required_header(headers, SIGNATURE_HEADER))
        if len(signature) != 64:
            raise ManagementAuthError("management request signature is invalid")

        public_key = parse_public_key(client.public_key)
        try:
            public_key.verify(signature, payload)
        except InvalidSignature as exc:
            raise ManagementAuthError("management request signature is invalid") from exc

        if not self.nonce_store.remember(
            client_id,
            nonce,
            now=now,
            ttl_s=self.max_request_age_s + self.max_future_skew_s,
        ):
            raise ManagementAuthError("management request nonce was replayed", error_code="replay")

        return AuthorizedRequest(
            client=client,
            timestamp=timestamp,
            nonce=nonce,
            body_sha256=body_sha256,
        )


def canonical_request_payload(
    *,
    method: str,
    path: str,
    body_sha256: str,
    timestamp: int,
    nonce: str,
) -> bytes:
    """Return the canonical request bytes signed by management clients."""

    if not re.fullmatch(r"[0-9a-f]{64}", body_sha256):
        raise ManagementAuthError("request body digest is invalid")
    return "\n".join(
        (
            REQUEST_SIGNATURE_CONTEXT,
            method.upper(),
            path,
            body_sha256,
            str(timestamp),
            nonce,
        )
    ).encode("utf-8")


def public_key_text(public_key: ed25519.Ed25519PublicKey) -> str:
    return encode_base64url(
        public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    )


def parse_public_key(value: str) -> ed25519.Ed25519PublicKey:
    text = value.strip()
    if text.startswith("-----BEGIN"):
        loaded = serialization.load_pem_public_key(text.encode("utf-8"))
        if not isinstance(loaded, ed25519.Ed25519PublicKey):
            raise ClientRecordError("management client public key must be Ed25519")
        return loaded
    raw = decode_base64url(text)
    if len(raw) != 32:
        raise ClientRecordError("management client public key must be 32 bytes")
    return ed25519.Ed25519PublicKey.from_public_bytes(raw)


def validate_client_id(client_id: str) -> str:
    if _CLIENT_ID_RE.fullmatch(client_id) is None:
        raise ClientRecordError("management client id is unsafe", error_code="client_id_invalid")
    return client_id


def encode_base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def decode_base64url(value: str) -> bytes:
    if not value or _BASE64URL_RE.fullmatch(value) is None:
        raise ManagementAuthError("invalid base64url encoding")
    try:
        padded = value + ("=" * (-len(value) % 4))
        return base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ManagementAuthError("invalid base64url encoding") from exc


def current_unix_seconds() -> int:
    return int(datetime.now(UTC).timestamp())


def utc_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _required_header(headers: Mapping[str, str], name: str) -> str:
    value = headers.get(name)
    if value is None or not value.strip():
        raise ManagementAuthError(
            f"missing required management auth header: {name}",
            error_code="auth_required",
        )
    return value.strip()


def _parse_timestamp(value: str, *, now: int) -> int:
    del now
    try:
        timestamp = int(value)
    except ValueError as exc:
        raise ManagementAuthError("management request timestamp is invalid") from exc
    if timestamp < 0:
        raise ManagementAuthError("management request timestamp is invalid")
    return timestamp


def _required_str(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise ClientRecordError(f"client record {key} must be a non-empty string")
    return item


def _optional_str(value: Mapping[str, object], key: str) -> str | None:
    item = value.get(key)
    if item is None:
        return None
    if not isinstance(item, str) or not item:
        raise ClientRecordError(f"client record {key} must be a string or null")
    return item
