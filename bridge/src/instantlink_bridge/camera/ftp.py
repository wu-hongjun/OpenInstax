"""pyftpdlib receive service for camera FTP uploads."""

from __future__ import annotations

import asyncio
import logging
import shutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock, Thread
from types import TracebackType
from typing import Protocol

from instantlink_bridge.config import FtpConfig, FtpSourceDecision, ftp_config_source_decision
from instantlink_bridge.net.addresses import detect_ipv4_addresses_for_interface
from instantlink_bridge.net.health import DEFAULT_WIFI_INTERFACE, FtpActivityTracker
from instantlink_bridge.ui.models import UiMode, UiSnapshot

LOGGER = logging.getLogger(__name__)
INSECURE_FTP_PASSWORDS = {"", "change-me", "instax"}

BridgeSnapshotProvider = Callable[[], UiSnapshot]


class FtpServiceFailedError(RuntimeError):
    """Raised when the background FTP service has stopped unexpectedly."""


class _FtpServer(Protocol):
    def close_all(self) -> None:
        """Close all FTP connections."""

    def serve_forever(self) -> None:
        """Run the FTP server loop."""


@dataclass(frozen=True, slots=True)
class ReceivedImage:
    """Completed FTP upload."""

    path: Path
    remote_ip: str


FtpQueueOverflowCallback = Callable[[ReceivedImage, int, int], None]
ActivePeerHostProvider = Callable[[], Iterable[str]]


def _printer_reachable(snap: UiSnapshot) -> bool:
    """Return True when the printer is considered online and reachable.

    Requires a fresh status poll (printer_status_fresh=True) and a mode that
    is not one of the non-operational states.
    """
    if not snap.printer_status_fresh:
        return False
    # PAIRING and PAIR_FAILED are included because mid-pairing the printer is
    # not in a state to receive prints; treat as unreachable so STOR returns a
    # descriptive 451 instead of falling through to a confusing data-channel error.
    unreachable_modes = {
        UiMode.PRINTER_SEARCHING,
        UiMode.PRINTER_OFFLINE,
        UiMode.BOOTING,
        UiMode.NEEDS_PAIRING,
        UiMode.PAIRING,
        UiMode.PAIR_FAILED,
        UiMode.ERROR,
    }
    return snap.mode not in unreachable_modes


class FtpReceiveService:
    """Run pyftpdlib in a background thread and hand completed files to asyncio."""

    def __init__(
        self,
        config: FtpConfig,
        queue: asyncio.Queue[ReceivedImage],
        loop: asyncio.AbstractEventLoop,
        activity_tracker: FtpActivityTracker | None = None,
        queue_overflow_callback: FtpQueueOverflowCallback | None = None,
        active_peer_host_provider: ActivePeerHostProvider | None = None,
        *,
        bridge_snapshot_provider: BridgeSnapshotProvider | None = None,
    ) -> None:
        self._config = config
        self._config_lock = Lock()
        self._queue = queue
        self._loop = loop
        self._activity_tracker = activity_tracker
        self._queue_overflow_callback = queue_overflow_callback
        self._active_peer_host_provider = active_peer_host_provider
        self._bridge_snapshot_provider = bridge_snapshot_provider
        self._thread: Thread | None = None
        self._server: _FtpServer | None = None
        self._started = Event()
        self._startup_error: Exception | None = None
        self._failure: Exception | None = None
        self._stopping = False

    def set_config(self, config: FtpConfig) -> None:
        """Update runtime FTP source policy without restarting the listener."""

        validate_runtime_ftp_config(config)
        with self._config_lock:
            previous_mode = self._config.mode
            self._config = config
        LOGGER.info(
            "ftp.config_updated previous_mode=%s mode=%s usb_host=%s hotspot_host=%s",
            previous_mode.value,
            config.mode.value,
            config.host,
            config.hotspot_host,
        )

    def start(self) -> None:
        """Start the FTP server thread."""

        if self._thread is not None:
            raise RuntimeError("FTP receive service is already running")
        validate_runtime_ftp_config(self._config)
        self._started.clear()
        self._startup_error = None
        self._failure = None
        self._stopping = False
        self._config.incoming_dir.parent.mkdir(parents=True, exist_ok=True)
        self._config.incoming_dir.mkdir(parents=True, exist_ok=True)
        self._thread = Thread(target=self._run_server, name="instantlink-bridge-ftp", daemon=True)
        self._thread.start()
        if not self._started.wait(timeout=5):
            raise RuntimeError("FTP server did not start within 5 seconds")
        if self._startup_error is not None:
            raise RuntimeError("FTP server failed to start") from self._startup_error

    def raise_if_failed(self) -> None:
        """Raise if the background FTP thread has failed after startup."""

        if self._failure is not None:
            raise FtpServiceFailedError("FTP receive service stopped") from self._failure

    def stop(self) -> None:
        """Stop the FTP server."""

        self._stopping = True
        server = self._server
        if server is not None:
            server.close_all()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        self._server = None

    def _handoff_received_image(self, path: Path, remote_ip: str) -> None:
        def enqueue() -> None:
            received = ReceivedImage(path=path, remote_ip=remote_ip)
            try:
                self._queue.put_nowait(received)
            except asyncio.QueueFull:
                queue_depth = self._queue.qsize()
                queue_max_size = self._queue.maxsize
                LOGGER.error(
                    "ftp.queue_full path=%s remote_ip=%s queue_depth=%s queue_max_size=%s",
                    path,
                    remote_ip,
                    queue_depth,
                    queue_max_size,
                )
                if self._queue_overflow_callback is not None:
                    try:
                        self._queue_overflow_callback(received, queue_depth, queue_max_size)
                    except Exception:
                        LOGGER.exception("ftp.queue_overflow_callback_failed path=%s", path)
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    LOGGER.exception("ftp.queue_full_cleanup_failed path=%s", path)

        self._loop.call_soon_threadsafe(enqueue)

    def _source_decision(self, remote_ip: str) -> FtpSourceDecision:
        config = self.config
        return ftp_config_source_decision(
            config,
            remote_ip,
            active_peer_hosts=self._active_peer_hosts(),
        )

    @property
    def config(self) -> FtpConfig:
        """Return the current runtime FTP policy config."""

        with self._config_lock:
            return self._config

    def _active_peer_hosts(self) -> tuple[str, ...]:
        try:
            if self._active_peer_host_provider is not None:
                return tuple(self._active_peer_host_provider())
            return tuple(detect_ipv4_addresses_for_interface(DEFAULT_WIFI_INTERFACE))
        except Exception:
            LOGGER.debug("ftp.active_peer_host_lookup_failed", exc_info=True)
            return ()

    def _log_source_rejected(
        self,
        *,
        event: str,
        remote_ip: str,
        decision: FtpSourceDecision,
        path: Path | None = None,
    ) -> None:
        if path is None:
            LOGGER.warning(
                "%s mode=%s remote_ip=%s source=%s reason=%s",
                event,
                self.config.mode.value,
                remote_ip,
                decision.source.value,
                decision.reason,
            )
            return
        LOGGER.warning(
            "%s path=%s mode=%s remote_ip=%s source=%s reason=%s",
            event,
            path,
            self.config.mode.value,
            remote_ip,
            decision.source.value,
            decision.reason,
        )

    def _accept_connection_source(self, remote_ip: str, *, event: str) -> bool:
        decision = self._source_decision(remote_ip)
        if not decision.allowed:
            self._log_source_rejected(
                event=event,
                remote_ip=remote_ip,
                decision=decision,
            )
            return False
        return True

    def _record_authenticated_login(self, remote_ip: str) -> bool:
        if not self._accept_connection_source(remote_ip, event="ftp.login_rejected"):
            return False
        if self._activity_tracker is not None:
            self._activity_tracker.record_connection(remote_ip)
        return True

    def _handle_received_file(self, file: str, remote_ip: str) -> None:
        path = self._normalize_received_file_path(Path(file), remote_ip)
        if path is None:
            return

        incoming_dir = self._config.incoming_dir.resolve()
        try:
            resolved_path = path.resolve()
            resolved_path.relative_to(incoming_dir)
        except ValueError:
            LOGGER.warning(
                "ftp.image_rejected_outside_incoming path=%s remote_ip=%s",
                path,
                remote_ip,
            )
            self._unlink_rejected_file(path)
            return

        decision = self._source_decision(remote_ip)
        if not decision.allowed:
            self._log_source_rejected(
                event="ftp.image_rejected_source",
                path=path,
                remote_ip=remote_ip,
                decision=decision,
            )
            self._unlink_rejected_file(path)
            return

        if self._activity_tracker is not None:
            self._activity_tracker.record_upload(remote_ip)
        LOGGER.info(
            "ftp.image_received path=%s remote_ip=%s source=%s mode=%s queue_depth=%s",
            path,
            remote_ip,
            decision.source.value,
            self.config.mode.value,
            self._queue.qsize(),
        )
        self._handoff_received_image(path, remote_ip)

    def _normalize_received_file_path(self, path: Path, remote_ip: str) -> Path | None:
        incoming_dir = self._config.incoming_dir.resolve()
        ftp_root = ftp_home_dir_for_incoming(incoming_dir).resolve()
        try:
            resolved_path = path.resolve()
            resolved_path.relative_to(incoming_dir)
        except ValueError:
            pass
        else:
            return path

        try:
            resolved_path.relative_to(ftp_root)
        except ValueError:
            LOGGER.warning(
                "ftp.image_rejected_outside_ftp_root path=%s remote_ip=%s",
                path,
                remote_ip,
            )
            self._unlink_rejected_file(path)
            return None

        if resolved_path.parent != ftp_root:
            return path

        target = _unique_received_path(incoming_dir / resolved_path.name)
        try:
            incoming_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(target))
        except OSError:
            LOGGER.exception(
                "ftp.root_upload_relocate_failed path=%s target=%s remote_ip=%s",
                path,
                target,
                remote_ip,
            )
            self._unlink_rejected_file(path)
            return None

        LOGGER.info(
            "ftp.root_upload_relocated path=%s target=%s remote_ip=%s",
            path,
            target,
            remote_ip,
        )
        return target

    def _unlink_rejected_file(self, path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            LOGGER.exception("ftp.rejected_cleanup_failed path=%s", path)

    def _record_failure(self, error: Exception) -> None:
        if not self._started.is_set():
            self._startup_error = error
            self._started.set()
            return
        self._failure = error

    def _ftp_preflight_reply(self, remote_ip: str | None = None) -> str | None:
        """Return an FTP error reply string if the bridge cannot accept a print job right now.

        Check ordering follows the architect's Phase 6 rationale: hard-fail states first
        (booting, not paired, printer offline, no film), then transient-busy last (printing).
        Returns None to fall through to the normal STOR handler on success.

        Gracefully degrades to None when no bridge_snapshot_provider is wired.
        """
        if self._bridge_snapshot_provider is None:
            return None

        snap = self._bridge_snapshot_provider()

        if snap.mode is UiMode.BOOTING:
            reply = "451 Bridge starting, try again in a moment."
            LOGGER.info(
                "ftp.preflight_rejected reply=%r remote_ip=%s",
                reply,
                remote_ip,
            )
            return reply

        if snap.paired_printer is None:
            reply = "501 Bridge not paired. Pair from the Mac app."
            LOGGER.info(
                "ftp.preflight_rejected reply=%r remote_ip=%s",
                reply,
                remote_ip,
            )
            return reply

        if not _printer_reachable(snap):
            printer_name = snap.paired_printer.name
            if len(printer_name) > 15:
                printer_name = printer_name[:15]
            reply = f"451 {printer_name} offline. Power on printer."
            LOGGER.info(
                "ftp.preflight_rejected reply=%r remote_ip=%s",
                reply,
                remote_ip,
            )
            return reply

        # film_remaining is None when the printer status is not yet populated;
        # treat as unknown and fall through. The printer itself rejects at the
        # BLE layer if the cartridge is truly empty, so this guard only fires
        # when we have a confirmed zero.
        if (
            snap.film_remaining is not None
            and snap.film_remaining <= 0
            and not snap.allow_print_without_film
        ):
            reply = "552 No film. Load film and retry."
            LOGGER.info(
                "ftp.preflight_rejected reply=%r remote_ip=%s",
                reply,
                remote_ip,
            )
            return reply

        if snap.mode is UiMode.PRINTING:
            reply = "450 Printer busy, try again."
            LOGGER.info(
                "ftp.preflight_rejected reply=%r remote_ip=%s",
                reply,
                remote_ip,
            )
            return reply

        return None

    def _run_server(self) -> None:
        from pyftpdlib.authorizers import DummyAuthorizer
        from pyftpdlib.handlers import FTPHandler
        from pyftpdlib.servers import FTPServer

        service = self

        class InstantLinkBridgeFtpHandler(FTPHandler):  # type: ignore[misc]
            def on_connect(self) -> None:
                if not service._accept_connection_source(
                    self.remote_ip,
                    event="ftp.connection_rejected",
                ):
                    self.respond("421 InstantLink Bridge FTP source rejected for receive mode.")
                    self.close_when_done()

            def on_login(self, username: str) -> None:
                _ = username
                if not service._record_authenticated_login(self.remote_ip):
                    self.respond("530 InstantLink Bridge FTP source rejected for receive mode.")
                    self.close_when_done()

            def on_file_received(self, file: str) -> None:
                service._handle_received_file(file, self.remote_ip)

            def ftp_STOR(self, file: str, mode: str = "w") -> object:
                reply = service._ftp_preflight_reply(self.remote_ip)
                if reply is not None:
                    self.respond(reply)
                    return None
                return super().ftp_STOR(file, mode)

        try:
            authorizer = DummyAuthorizer()
            authorizer.add_user(
                self._config.username,
                self._config.password,
                str(ftp_home_dir_for_incoming(self._config.incoming_dir)),
                perm="elrw",
            )
            InstantLinkBridgeFtpHandler.authorizer = authorizer
            InstantLinkBridgeFtpHandler.banner = "InstantLink Bridge FTP ready"
            address = (self._config.bind_host, self._config.port)
            server: _FtpServer = FTPServer(address, InstantLinkBridgeFtpHandler)
            self._server = server
            LOGGER.info(
                "ftp.server_started mode=%s bind_host=%s usb_host=%s hotspot_host=%s "
                "port=%s ftp_root=%s incoming=%s",
                self._config.mode.value,
                self._config.bind_host,
                self._config.host,
                self._config.hotspot_host,
                self._config.port,
                ftp_home_dir_for_incoming(self._config.incoming_dir),
                self._config.incoming_dir,
            )
            self._started.set()
            server.serve_forever()
            if not self._stopping:
                self._record_failure(RuntimeError("FTP server exited unexpectedly"))
        except Exception as error:
            if not self._stopping:
                self._record_failure(error)
            LOGGER.exception("ftp.server_failed")
        finally:
            LOGGER.info("ftp.server_stopped")

    def __enter__(self) -> FtpReceiveService:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.stop()


def validate_runtime_ftp_config(config: FtpConfig) -> None:
    if not config.username.strip():
        raise RuntimeError("FTP username is empty")
    if config.password.strip() in INSECURE_FTP_PASSWORDS:
        raise RuntimeError(
            "Refusing to start FTP with the default password; run provisioning first"
        )


def _unique_received_path(path: Path) -> Path:
    """Return a non-existing path so queued uploads cannot overwrite each other."""

    if not path.exists():
        return path
    for index in range(1, 10_000):
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise OSError(f"could not find available upload name for {path}")


def ftp_home_dir_for_incoming(incoming_dir: Path) -> Path:
    """Return the FTP root that exposes incoming_dir as /incoming to cameras."""

    return incoming_dir.parent
