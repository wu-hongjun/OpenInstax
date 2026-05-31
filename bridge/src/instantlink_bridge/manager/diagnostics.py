"""Bridge diagnostics: log stream + support bundle creation.

Phase E of plan 038 introduces two operator-facing diagnostics surfaces:

* ``/v1/logs/stream`` — Server-Sent Events stream of redacted Bridge
  journal entries. Each event is a ``data:`` line carrying a JSON object
  with ``timestamp``, ``level``, and ``message`` fields plus an explicit
  ``id`` for client de-duplication.
* ``/v1/support-bundle/create`` — synchronous handler that stages a
  redacted zip archive on the bridge and returns its location + sha256
  metadata. The Mac downloads the archive in a follow-up request.

Both surfaces share the same redaction pass: secrets in TOML values
(``password = "..."``), Wi-Fi PSKs, FTP credentials, and authorization
header bytes are masked before being written.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import zipfile
from collections.abc import AsyncIterator, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

# ----------------------------------------------------------------------------
# Public log-event contract
# ----------------------------------------------------------------------------

DEFAULT_LOG_LEVELS: tuple[str, ...] = ("info", "warning", "error")
SSE_HEARTBEAT_INTERVAL_SECONDS = 15.0
SUPPORT_BUNDLE_SCHEMA_VERSION = 1
SUPPORT_BUNDLE_KIND = "instantlink_bridge_support"


@dataclass(frozen=True, slots=True)
class BridgeLogEvent:
    """One redacted log entry surfaced to a paired Mac client."""

    event_id: str
    timestamp: str
    level: str
    message: str

    def to_json(self) -> dict[str, object]:
        return {
            "id": self.event_id,
            "timestamp": self.timestamp,
            "level": self.level,
            "message": self.message,
        }


# ----------------------------------------------------------------------------
# Redaction
# ----------------------------------------------------------------------------

_PASSWORD_PATTERN = re.compile(
    r"(?P<key>password|psk|secret|auth|token|signature|private_key)"
    r"(?P<sep>\s*[:=]\s*)"
    r"(?P<quote>[\"\']?)"
    r"(?P<value>[^\"\'\s,;}\n]+)"
    r"(?P=quote)",
    re.IGNORECASE,
)
_BEARER_PATTERN = re.compile(
    r"(?P<scheme>Bearer\s+|Authorization:\s*)(?P<value>[A-Za-z0-9+/=._\-]+)",
    re.IGNORECASE,
)


def redact_log_line(line: str) -> str:
    """Redact secrets from a single log line.

    The pass is intentionally conservative: it strips quoted/unquoted
    values for password-shaped keys and bearer tokens. Other fields are
    left intact so the line still reads as a journal entry.
    """

    def _mask_keyval(match: re.Match[str]) -> str:
        key = match.group("key")
        sep = match.group("sep")
        quote = match.group("quote")
        return f"{key}{sep}{quote}***redacted***{quote}"

    def _mask_bearer(match: re.Match[str]) -> str:
        return f"{match.group('scheme')}***redacted***"

    masked = _PASSWORD_PATTERN.sub(_mask_keyval, line)
    masked = _BEARER_PATTERN.sub(_mask_bearer, masked)
    return masked


# ----------------------------------------------------------------------------
# SSE formatter
# ----------------------------------------------------------------------------


def format_sse_event(event: BridgeLogEvent) -> bytes:
    """Encode a log event as a single SSE record."""

    payload = json.dumps(event.to_json(), separators=(",", ":"))
    return f"id: {event.event_id}\nevent: log\ndata: {payload}\n\n".encode()


def format_sse_heartbeat() -> bytes:
    """SSE keepalive comment line."""

    return b": keepalive\n\n"


# ----------------------------------------------------------------------------
# Log stream source
# ----------------------------------------------------------------------------


class LogStreamSource:
    """Async source of redacted log events.

    Production deployments hook this up to ``journalctl --output=json``;
    tests substitute an in-memory iterable. The default implementation
    accepts a synchronous iterable of pre-formatted events and emits
    them with a short delay so the SSE handler can yield to the loop.
    """

    def __init__(
        self,
        events: Iterable[BridgeLogEvent] | None = None,
        *,
        sleep_seconds: float = 0.0,
    ) -> None:
        self._events: list[BridgeLogEvent] = list(events or [])
        self._sleep_seconds = max(0.0, sleep_seconds)

    async def iter_events(
        self,
        *,
        level_filter: str | None = None,
    ) -> AsyncIterator[BridgeLogEvent]:
        """Yield events that match the requested ``level_filter``.

        ``level_filter`` of ``None`` or ``"all"`` returns every event.
        Other values match the event's ``level`` field case-insensitively.
        """

        normalized = (level_filter or "").lower().strip()
        for event in self._events:
            if normalized and normalized != "all" and event.level.lower() != normalized:
                continue
            if self._sleep_seconds > 0:
                await asyncio.sleep(self._sleep_seconds)
            yield event


# ----------------------------------------------------------------------------
# Support bundle
# ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SupportBundleResult:
    """Outcome of a successful support-bundle creation."""

    bundle_id: str
    archive_path: Path
    size_bytes: int
    sha256: str
    contents: tuple[str, ...]
    created_at: str


@dataclass(frozen=True, slots=True)
class SupportBundleSource:
    """One source path to include in the bundle."""

    archive_path: str
    on_disk_path: Path
    required: bool = False


def default_support_bundle_sources(root: Path) -> tuple[SupportBundleSource, ...]:
    """Return the default redacted source set for a support bundle.

    Plan 029 / plan 038 require: bridge config snapshot, redacted journal
    tail, manager service health, network mode, paired-client metadata.
    Secrets (FTP password, Wi-Fi PSK, signing keys) are excluded.
    """

    return (
        SupportBundleSource(
            archive_path="etc/InstantLinkBridge/config.toml",
            on_disk_path=root / "etc" / "InstantLinkBridge" / "config.toml",
            required=False,
        ),
        SupportBundleSource(
            archive_path="var/log/instantlink-bridge.log",
            on_disk_path=root / "var" / "log" / "instantlink-bridge.log",
            required=False,
        ),
        SupportBundleSource(
            archive_path="var/lib/InstantLinkBridge/management/health.json",
            on_disk_path=(
                root
                / "var"
                / "lib"
                / "InstantLinkBridge"
                / "management"
                / "health.json"
            ),
            required=False,
        ),
    )


def _redact_text(content: str) -> str:
    """Apply line-wise redaction to a text blob."""

    return "\n".join(redact_log_line(line) for line in content.splitlines())


def _read_redacted(source: SupportBundleSource) -> str | None:
    """Read and redact a text source, returning ``None`` when absent."""

    path = source.on_disk_path
    if not path.exists():
        if source.required:
            raise FileNotFoundError(f"required support bundle source missing: {path}")
        return None
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return _redact_text(raw)


def create_support_bundle(
    *,
    bundles_dir: Path,
    sources: Iterable[SupportBundleSource],
    extra_payloads: Mapping[str, str] | None = None,
    bundle_id: str | None = None,
    created_at: str | None = None,
    now_seconds: float | None = None,
) -> SupportBundleResult:
    """Stage a redacted support bundle into ``bundles_dir``.

    ``extra_payloads`` lets the caller embed runtime-collected diagnostics
    (e.g. an `network-status.json` snapshot) without having to read it
    from disk first. Values must already be redacted by the caller.
    """

    bundles_dir.mkdir(parents=True, exist_ok=True)
    seconds = now_seconds if now_seconds is not None else time.time()
    timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(seconds))
    resolved_bundle_id = bundle_id or f"support-{timestamp}"
    created = created_at or time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime(seconds)
    )
    archive_path = bundles_dir / f"{resolved_bundle_id}.zip"

    contents: list[str] = []
    with zipfile.ZipFile(
        archive_path, mode="w", compression=zipfile.ZIP_DEFLATED
    ) as archive:
        manifest = {
            "schema_version": SUPPORT_BUNDLE_SCHEMA_VERSION,
            "bundle_kind": SUPPORT_BUNDLE_KIND,
            "bundle_id": resolved_bundle_id,
            "created_at": created,
        }
        archive.writestr(
            "manifest.json", json.dumps(manifest, indent=2, sort_keys=True)
        )
        contents.append("manifest.json")

        for source in sources:
            redacted = _read_redacted(source)
            if redacted is None:
                continue
            archive.writestr(source.archive_path, redacted)
            contents.append(source.archive_path)

        for archive_name, payload in (extra_payloads or {}).items():
            archive.writestr(archive_name, _redact_text(payload))
            contents.append(archive_name)

    size_bytes = archive_path.stat().st_size
    sha256 = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    return SupportBundleResult(
        bundle_id=resolved_bundle_id,
        archive_path=archive_path,
        size_bytes=size_bytes,
        sha256=sha256,
        contents=tuple(contents),
        created_at=created,
    )
