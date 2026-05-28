"""InstantLink Bridge vertical-slice application entry point."""

from __future__ import annotations

import argparse
import asyncio
import inspect
import logging
import os
import signal
from collections.abc import Awaitable, Callable, Sequence
from concurrent.futures import Future as ThreadFuture
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from instantlink_bridge.ble.client import (
    DiscoveredPrinter,
    close_default_ble_session_manager,
    scan_instax_printers,
)
from instantlink_bridge.ble.client import (
    print_file_to_printer as print_file_to_printer_bleak,
)
from instantlink_bridge.ble.instantlink import (
    close_default_instantlink_backend,
    instantlink_backend_enabled,
)
from instantlink_bridge.ble.instantlink import (
    print_file_to_printer as print_file_to_printer_instantlink,
)
from instantlink_bridge.ble.instax import (
    CoverOpenError,
    LowPrinterBatteryError,
    NoFilmError,
    PrinterBusyError,
    PrintRejectedError,
)
from instantlink_bridge.config import (
    DEFAULT_CONFIG_PATH,
    BridgeConfig,
    FtpConfig,
    PowerBackend,
    load_config,
)
from instantlink_bridge.imaging.pipeline import (
    ImagePipelineError,
    ImageTooLargeError,
    PrintEdit,
)
from instantlink_bridge.imaging.worker import (
    ImagePreparationTimeoutError,
    close_default_image_preparation_worker,
)
from instantlink_bridge.net.health import FtpActivityTracker
from instantlink_bridge.power.monitor import BatteryPolicy, IdlePolicy, PowerMonitor, PowerPolicy
from instantlink_bridge.power.pisugar import PiSugarClient
from instantlink_bridge.power.x306 import NoBatteryClient, X306BatteryClient
from instantlink_bridge.printing import PrintProgress, PrintProgressCallback, PrintStage
from instantlink_bridge.system_info import format_status_report, format_version_summary
from instantlink_bridge.ui.controller import BridgeUi
from instantlink_bridge.ui.models import PairedPrinter
from instantlink_bridge.ui.pairing import BluetoothctlPrinterPairer, PrinterPairer
from instantlink_bridge.ui.status import scan_bluez_instax_printers, status_target_for_visible_match
from instantlink_bridge.watchdog import WatchdogNotifier, run_watchdog_heartbeat

if TYPE_CHECKING:
    from instantlink_bridge.camera.ftp import FtpReceiveService, ReceivedImage

    # Full signature visible to type-checkers (ReceivedImage is importable here).
    PrinterSender = Callable[
        [PairedPrinter, ReceivedImage, BridgeConfig, PrintEdit, PrintProgressCallback],
        Awaitable[None],
    ]
else:
    # At runtime PrinterSender resolves to a generic callable so `typing.get_type_hints()` on a
    # function annotated with `PrinterSender` does NOT need to evaluate a forward reference to
    # `ReceivedImage` (codex finding 3 — keeps the TYPE_CHECKING-only `camera.ftp` import safe).
    PrinterSender = Callable[..., Awaitable[None]]

LOGGER = logging.getLogger(__name__)
AUTO_PRINT_DELAY_S = 5.0
IMAGE_QUEUE_MAXSIZE = 100
PRINT_JOB_TIMEOUT_S = 120.0
PRINT_JOB_HARD_TIMEOUT_S = 300.0
PRINT_TARGET_SCAN_TIMEOUT_S = 1.0
PRINT_TARGET_SCAN_ATTEMPTS = 5
POWEROFF_HELPER = "/usr/local/sbin/instantlink-bridge-poweroff"


class PrintJobError(RuntimeError):
    """Raised when a received image cannot be printed."""


class PrintUi(Protocol):
    """UI operations needed by the print orchestration flow."""

    async def image_received(self, received: ReceivedImage) -> None:
        """Show that a received image has been dequeued for processing."""

    async def await_print_confirmation(
        self,
        received: ReceivedImage,
        *,
        timeout_s: float | None = AUTO_PRINT_DELAY_S,
    ) -> PrintEdit | None:
        """Return preview edits when printing should continue."""

    async def printing_started(self, received: ReceivedImage) -> None:
        """Show printing state."""

    async def print_progress(self, progress: PrintProgress) -> None:
        """Show current print progress."""

    async def print_complete(self, received: ReceivedImage) -> None:
        """Show print complete state."""

    async def print_failed(self, message: str) -> None:
        """Show print failure state."""


class ImageQueueStatusUi(Protocol):
    """Optional UI hooks for receive queue state."""

    async def image_queue_changed(self, *, depth: int, max_size: int) -> None:
        """Show current receive queue depth."""

    async def image_queue_overflow(
        self,
        received: ReceivedImage,
        *,
        depth: int,
        max_size: int,
    ) -> None:
        """Show that a received image was dropped because the queue was full."""


class AsyncStopService(Protocol):
    """Small protocol for optional runtime helpers."""

    async def stop(self) -> None:
        """Stop the helper."""


async def run_ftp_receive_slice(config_path: Path) -> None:
    """Run the first vertical slice: FTP upload -> logged file path."""

    config = load_config(config_path)
    queue: asyncio.Queue[ReceivedImage] = asyncio.Queue(maxsize=IMAGE_QUEUE_MAXSIZE)
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    _install_signal_handlers(stop_event)
    notifier = WatchdogNotifier()
    watchdog_task: asyncio.Task[None] | None = None
    power_task: asyncio.Task[None] | None = None
    ui_event_tasks: set[asyncio.Task[None]] = set()
    pairer = BluetoothctlPrinterPairer()
    ftp_activity = FtpActivityTracker()
    power_monitor: PowerMonitor | None = None
    service: FtpReceiveService | None = None
    bluez_agent: AsyncStopService | None = None

    async def record_power_activity() -> None:
        if power_monitor is not None:
            await power_monitor.record_activity()

    def apply_runtime_ftp_config(ftp_config: FtpConfig) -> None:
        if service is not None:
            service.set_config(ftp_config)

    ui = BridgeUi(
        config,
        config_path=config_path,
        pairer=pairer,
        ftp_activity=ftp_activity,
        power_activity_callback=record_power_activity,
        ftp_config_applied_callback=apply_runtime_ftp_config,
    )
    power_monitor = build_power_monitor(config, ui=ui)

    def notify_queue_overflow(
        received: ReceivedImage,
        depth: int,
        max_size: int,
    ) -> None:
        task = loop.create_task(
            notify_image_queue_overflow(ui, received, depth=depth, max_size=max_size)
        )
        ui_event_tasks.add(task)
        task.add_done_callback(ui_event_tasks.discard)

    def _setup_ftp_service_sync() -> FtpReceiveService:
        # Run the WHOLE FTP setup off the event loop: the `from instantlink_bridge.camera.ftp
        # import FtpReceiveService` line transitively pulls pyftpdlib (~1 s of synchronous import
        # on a cold Pi Zero 2 W). Doing that import on the main thread would block the BLE
        # gather sibling for the duration of the import, defeating M3. Performing the import +
        # construction + .start() in the executor thread lets the BLE branch make real progress
        # in parallel.
        from instantlink_bridge.camera.ftp import FtpReceiveService

        ftp_service = FtpReceiveService(
            config.ftp,
            queue,
            loop,
            activity_tracker=ftp_activity,
            queue_overflow_callback=notify_queue_overflow,
        )
        ftp_service.start()  # blocks until the FTP thread's listener is bound
        return ftp_service

    async def start_ftp_service() -> FtpReceiveService:
        return await asyncio.to_thread(_setup_ftp_service_sync)

    async def start_ble_stack() -> AsyncStopService | None:
        agent = await start_bluez_agent_if_needed()
        await ui.start()
        return agent

    try:
        # Run FTP-server startup and BLE/UI startup concurrently. Use
        # return_exceptions so a failure in one branch does not prevent us from
        # capturing the other branch's already-constructed resources (FTP server,
        # BlueZ agent), which the outer finally still needs to tear down cleanly.
        ftp_result, ble_result = await asyncio.gather(
            start_ftp_service(),
            start_ble_stack(),
            return_exceptions=True,
        )
        if not isinstance(ftp_result, BaseException):
            service = ftp_result
        if not isinstance(ble_result, BaseException):
            bluez_agent = ble_result
        # If BOTH branches failed, log the secondary failure before re-raising the primary so
        # the second exception is never silently dropped (codex finding 1).
        failures = [
            exc for exc in (ftp_result, ble_result) if isinstance(exc, BaseException)
        ]
        for exc in failures[1:]:
            LOGGER.error("bridge.startup_secondary_failure", exc_info=exc)
        if failures:
            raise failures[0]
        # Robust runtime guard (not just `assert`, which would be stripped under `python -O`).
        if service is None:
            raise RuntimeError("BUG: FTP service not assigned after successful gather")
        notifier.ready()
        watchdog_task = asyncio.create_task(run_watchdog_heartbeat(stop_event, notifier))
        power_task = asyncio.create_task(power_monitor.run(stop_event))
        LOGGER.info(
            "bridge.ready mode=ftp_receive bind_host=%s usb_host=%s port=%s incoming=%s",
            config.ftp.bind_host,
            config.ftp.host,
            config.ftp.port,
            config.ftp.incoming_dir,
        )
        while not stop_event.is_set():
            service.raise_if_failed()
            try:
                received = await asyncio.wait_for(queue.get(), timeout=0.5)
            except TimeoutError:
                continue
            LOGGER.info(
                "bridge.image_dequeued path=%s remote_ip=%s queue_depth=%s",
                received.path,
                received.remote_ip,
                queue.qsize(),
            )
            await notify_image_queue_changed(
                ui,
                depth=queue.qsize(),
                max_size=queue.maxsize,
            )
            await power_monitor.record_activity()
            try:
                current_config = ui.config
                await ui.image_received(received)
                await handle_received_image(
                    received,
                    config=current_config,
                    ui=ui,
                    pairer=pairer,
                    timeout_s=current_config.workflow.auto_print_delay_s,
                    notify_received=False,
                )
            except Exception as exc:
                LOGGER.exception(
                    "bridge.image_job_unhandled path=%s error_type=%s",
                    received.path,
                    type(exc).__name__,
                )
            finally:
                queue.task_done()
    finally:
        notifier.stopping()
        stop_event.set()
        if watchdog_task is not None:
            watchdog_task.cancel()
            with suppress(asyncio.CancelledError):
                await watchdog_task
        if power_task is not None:
            power_task.cancel()
            with suppress(asyncio.CancelledError):
                await power_task
        for task in ui_event_tasks:
            task.cancel()
        if ui_event_tasks:
            await asyncio.gather(*ui_event_tasks, return_exceptions=True)
        await ui.stop()
        await close_default_instantlink_backend()
        if bluez_agent is not None:
            await bluez_agent.stop()
        await close_default_ble_session_manager()
        await close_default_image_preparation_worker()
        if service is not None:
            service.stop()


async def start_bluez_agent_if_needed() -> AsyncStopService | None:
    """Register a NoInputNoOutput BlueZ agent for InstantLink printer bonding."""

    if not instantlink_backend_enabled():
        return None
    try:
        from instantlink_bridge.ble.agent import BluezAgentService

        agent = BluezAgentService()
        await agent.start()
        return agent
    except Exception:
        LOGGER.exception("bluetooth.agent_start_failed")
        raise


def build_power_monitor(config: BridgeConfig, *, ui: BridgeUi) -> PowerMonitor:
    """Create the power monitor wired to UI events and system shutdown."""

    power = config.power
    policy = PowerPolicy(
        battery=BatteryPolicy(
            poll_interval_s=power.battery_poll_interval_s,
            warning_threshold_percent=power.battery_warning_threshold_percent,
            safe_shutdown_threshold_percent=power.battery_safe_shutdown_threshold_percent,
        ),
        idle=IdlePolicy(
            dim_after_s=power.idle_dim_after_s,
            screen_off_after_s=power.idle_screen_off_after_s,
            deep_idle_after_s=power.idle_deep_after_s,
            poweroff_after_s=power.idle_poweroff_after_s,
            poweroff_enabled=power.idle_poweroff_enabled,
        ),
    )
    return PowerMonitor(
        battery_client=battery_client_for_backend(power.backend),
        policy=policy,
        shutdown=request_system_poweroff,
        event_handler=ui.apply_power_event,
    )


def battery_client_for_backend(
    backend: PowerBackend,
) -> X306BatteryClient | PiSugarClient | NoBatteryClient:
    """Return a battery backend for the configured hardware."""

    if backend is PowerBackend.PISUGAR:
        return PiSugarClient()
    if backend is PowerBackend.NONE:
        return NoBatteryClient()
    return X306BatteryClient()


async def request_system_poweroff() -> None:
    """Ask systemd to power off through the provisioned sudo helper."""

    if os.environ.get("INSTANTLINK_BRIDGE_DISABLE_POWEROFF") == "1":
        LOGGER.warning("power.shutdown_disabled_by_environment")
        return

    helper = os.environ.get("INSTANTLINK_BRIDGE_POWEROFF_HELPER", POWEROFF_HELPER)
    process = await asyncio.create_subprocess_exec(
        "sudo",
        "-n",
        helper,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(
            "poweroff helper failed "
            f"rc={process.returncode} stdout={stdout.decode(errors='replace').strip()!r} "
            f"stderr={stderr.decode(errors='replace').strip()!r}"
        )


async def handle_received_image(
    received: ReceivedImage,
    *,
    config: BridgeConfig,
    ui: PrintUi,
    pairer: PrinterPairer,
    printer_sender: PrinterSender | None = None,
    timeout_s: float | None = AUTO_PRINT_DELAY_S,
    notify_received: bool = True,
) -> None:
    """Run one FTP-received image through the auto-print flow."""

    progress_tasks: list[asyncio.Future[None] | ThreadFuture[None]] = []
    accepting_progress = False
    try:
        if notify_received:
            await ui.image_received(received)
        edit = await ui.await_print_confirmation(received, timeout_s=timeout_s)
        if edit is None:
            LOGGER.info("bridge.print_cancelled path=%s", received.path)
            return

        sender = printer_sender if printer_sender is not None else send_print_to_printer
        await ui.printing_started(received)
        await ui.print_progress(
            PrintProgress(PrintStage.SELECTING_PRINTER, "Checking printer", "Looking up printer")
        )
        selected = select_configured_printer(
            await pairer.list_paired(),
            configured_name=config.printer.device_name,
        )
        if selected is None:
            raise PrintJobError("Select printer first")
        await ui.print_progress(
            PrintProgress(PrintStage.SELECTING_PRINTER, "Finding printer", selected.name)
        )
        target = await resolve_print_target(selected)
        LOGGER.info(
            "bridge.print_start path=%s target=%s name=%s",
            received.path,
            target.address,
            target.name,
        )
        loop = asyncio.get_running_loop()
        accepting_progress = True

        def progress_callback(progress: PrintProgress) -> None:
            if not accepting_progress:
                return
            progress_tasks.append(
                asyncio.run_coroutine_threadsafe(ui.print_progress(progress), loop)
            )

        await await_print_sender_without_cancelling_on_timeout(
            sender(target, received, config, edit, progress_callback),
            ui=ui,
            received=received,
            slow_after_s=PRINT_JOB_TIMEOUT_S,
            hard_after_s=PRINT_JOB_HARD_TIMEOUT_S,
        )
        accepting_progress = False
        await drain_print_progress(progress_tasks)
    except ImagePipelineError as exc:
        accepting_progress = False
        await drain_print_progress(progress_tasks)
        LOGGER.warning("bridge.print_image_rejected path=%s error=%s", received.path, exc)
        await ui.print_failed(print_error_message(exc))
    except PrintJobError as exc:
        accepting_progress = False
        await drain_print_progress(progress_tasks)
        LOGGER.warning("bridge.print_not_ready path=%s error=%s", received.path, exc)
        await ui.print_failed(str(exc))
    except PrintRejectedError as exc:
        accepting_progress = False
        await drain_print_progress(progress_tasks)
        LOGGER.warning("bridge.print_rejected path=%s error=%s", received.path, exc)
        await ui.print_failed(printer_rejection_message(exc))
    except Exception as exc:
        accepting_progress = False
        await drain_print_progress(progress_tasks)
        LOGGER.exception(
            "bridge.print_failed path=%s error_type=%s", received.path, type(exc).__name__
        )
        await ui.print_failed("Print failed")
    else:
        LOGGER.info("bridge.print_complete path=%s", received.path)
        await ui.print_complete(received)


async def await_print_sender_without_cancelling_on_timeout(
    send: Awaitable[None],
    *,
    ui: PrintUi,
    received: ReceivedImage,
    slow_after_s: float | None,
    hard_after_s: float | None = None,
) -> None:
    """Wait for one print send while keeping an over-time hardware job serialized."""

    send_task = asyncio.ensure_future(send)
    try:
        await asyncio.wait_for(asyncio.shield(send_task), timeout=slow_after_s)
    except TimeoutError:
        LOGGER.warning(
            "bridge.print_slow path=%s timeout_s=%s",
            received.path,
            slow_after_s,
        )
        await ui.print_progress(
            PrintProgress(PrintStage.FINISHING, "Still printing", "Waiting for printer")
        )
        if hard_after_s is None or slow_after_s is None:
            await send_task
            return
        remaining_s = max(1.0, hard_after_s - slow_after_s)
        try:
            await asyncio.wait_for(asyncio.shield(send_task), timeout=remaining_s)
        except TimeoutError:
            LOGGER.critical(
                "bridge.print_hard_timeout path=%s hard_timeout_s=%s",
                received.path,
                hard_after_s,
            )
            await ui.print_progress(
                PrintProgress(PrintStage.FINISHING, "Print stalled", "Restarting bridge")
            )
            os._exit(1)


async def drain_print_progress(
    progress_tasks: Sequence[asyncio.Future[None] | ThreadFuture[None]],
) -> None:
    """Let app-scheduled progress updates finish before terminal UI states."""

    if not progress_tasks:
        return
    awaitables = [
        asyncio.wrap_future(task) if isinstance(task, ThreadFuture) else task
        for task in progress_tasks
    ]
    results = await asyncio.gather(*awaitables, return_exceptions=True)
    for result in results:
        if isinstance(result, asyncio.CancelledError):
            continue
        if isinstance(result, Exception):
            LOGGER.warning(
                "bridge.print_progress_ui_failed error_type=%s error=%s",
                type(result).__name__,
                result,
            )


async def notify_image_queue_changed(
    ui: object,
    *,
    depth: int,
    max_size: int,
) -> None:
    """Notify UI implementations that opt in to queue depth updates."""

    await call_optional_ui_hook(
        ui,
        "image_queue_changed",
        depth=depth,
        max_size=max_size,
    )


async def notify_image_queue_overflow(
    ui: object,
    received: ReceivedImage,
    *,
    depth: int,
    max_size: int,
) -> None:
    """Notify UI implementations that opt in to queue overflow updates."""

    await call_optional_ui_hook(
        ui,
        "image_queue_overflow",
        received,
        depth=depth,
        max_size=max_size,
    )


async def call_optional_ui_hook(
    ui: object,
    hook_name: str,
    /,
    *args: object,
    **kwargs: object,
) -> None:
    hook = getattr(ui, hook_name, None)
    if hook is None:
        return
    try:
        result = hook(*args, **kwargs)
        if inspect.isawaitable(result):
            await result
    except Exception:
        LOGGER.exception("bridge.ui_hook_failed hook=%s", hook_name)


async def send_print_to_printer(
    printer: PairedPrinter,
    received: ReceivedImage,
    config: BridgeConfig,
    edit: PrintEdit,
    progress: PrintProgressCallback,
) -> None:
    """Print a received file through the model-detecting BLE path."""

    if instantlink_backend_enabled():
        await print_file_to_printer_instantlink(
            printer.address,
            received.path,
            name=printer.name,
            fit=config.printer.fit,
            quality=config.printer.quality,
            edit=edit,
            print_option=config.printer.print_option,
            model=config.printer.model,
            progress=progress,
        )
    else:
        await print_file_to_printer_bleak(
            printer.address,
            received.path,
            name=printer.name,
            fit=config.printer.fit,
            quality=config.printer.quality,
            edit=edit,
            print_option=config.printer.print_option,
            model=config.printer.model,
            progress=progress,
        )


async def resolve_print_target(selected: PairedPrinter) -> PairedPrinter:
    """Resolve a selected printer to the currently connectable BLE endpoint."""

    if instantlink_backend_enabled():
        return selected

    for attempt in range(PRINT_TARGET_SCAN_ATTEMPTS):
        target = await _visible_print_target(selected, scan_instax_printers)
        if target is not None:
            return target
        LOGGER.info(
            "bridge.print_target_retry selected=%s attempt=%s attempts=%s",
            selected.name,
            attempt + 1,
            PRINT_TARGET_SCAN_ATTEMPTS,
        )
    target = await _visible_print_target(
        selected,
        scan_bluez_instax_printers,
        timeout_s=max(PRINT_TARGET_SCAN_TIMEOUT_S, 2.0),
    )
    if target is not None:
        return target
    raise PrintJobError("Printer offline")


async def _visible_print_target(
    selected: PairedPrinter,
    scanner: Callable[[float], Awaitable[Sequence[DiscoveredPrinter | PairedPrinter]]],
    *,
    timeout_s: float = PRINT_TARGET_SCAN_TIMEOUT_S,
) -> PairedPrinter | None:
    try:
        candidates = await scanner(timeout_s)
    except Exception:
        LOGGER.exception("bridge.print_target_scan_failed selected=%s", selected.name)
        return None
    target = status_target_for_visible_match(selected, candidates)
    LOGGER.info(
        "bridge.print_target_scan count=%s selected=%s target=%s",
        len(candidates),
        selected.name,
        target.address if target is not None else "none",
    )
    return target


def select_configured_printer(
    printers: Sequence[PairedPrinter],
    *,
    configured_name: str | None,
) -> PairedPrinter | None:
    """Select the configured printer, falling back to the first selected printer."""

    if configured_name is not None:
        for printer in printers:
            if printer.name == configured_name:
                return printer
    return printers[0] if printers else None


def print_error_message(error: ImagePipelineError) -> str:
    """Map image pipeline errors to short LCD-friendly text."""

    message = str(error).lower()
    if "printer offline" in message:
        return "Printer offline"
    if "printer timed out" in message:
        return "Printer timed out"
    if "printer type unknown" in message:
        return "Printer type unknown"
    if isinstance(error, ImagePreparationTimeoutError):
        return "Image timed out"
    if isinstance(error, ImageTooLargeError):
        return "Image too large"
    return "Image unsupported"


def printer_rejection_message(error: PrintRejectedError) -> str:
    """Map printer rejection errors to short LCD-friendly text."""

    if isinstance(error, NoFilmError):
        return "No film"
    if isinstance(error, CoverOpenError):
        return "Cover open"
    if isinstance(error, LowPrinterBatteryError):
        return "Battery low"
    if isinstance(error, PrinterBusyError):
        return "Printer busy"
    return "Print rejected"


def main(argv: Sequence[str] | None = None) -> None:
    """CLI entry point."""

    parser = argparse.ArgumentParser(description="InstantLink Bridge service")
    info_group = parser.add_mutually_exclusive_group()
    info_group.add_argument(
        "--version",
        action="store_true",
        help="show InstantLink Bridge and runtime versions, then exit",
    )
    info_group.add_argument(
        "--status",
        action="store_true",
        help="show read-only system status, then exit",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"config file path (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Python logging level",
    )
    args = parser.parse_args(argv)
    if args.version:
        print(format_version_summary())
        return
    if args.status:
        print(format_status_report())
        return
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run_ftp_receive_slice(args.config))


def _install_signal_handlers(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop_event.set)
        except NotImplementedError:
            signal.signal(signum, lambda _signum, _frame: stop_event.set())


if __name__ == "__main__":
    main()
