"""Event-driven LCD UI controller."""

from __future__ import annotations

import asyncio
import logging
import math
import os
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from typing import Literal, Protocol, cast

from PIL import Image

from instantlink_bridge.ble.instantlink import instantlink_backend_enabled
from instantlink_bridge.ble.models import PrinterModel
from instantlink_bridge.camera.ftp import ReceivedImage
from instantlink_bridge.config import (
    BridgeConfig,
    FtpConfig,
    FtpReceiveMode,
    FtpSourceKind,
    PowerBackend,
    write_config,
)
from instantlink_bridge.imaging.pipeline import (
    ImagePipelineError,
    PrintEdit,
    create_preview_from_prepared,
)
from instantlink_bridge.imaging.worker import prepare_for_instax_async
from instantlink_bridge.net.health import (
    ConnectionHealth,
    FtpActivityTracker,
    detect_camera_link_health,
)
from instantlink_bridge.power.battery_estimator import (
    BatteryEstimate,
    BatteryEstimateState,
    BatteryLifeEstimator,
)
from instantlink_bridge.power.monitor import BatteryAlert, IdleStage, PowerEvent, PowerEventKind
from instantlink_bridge.power.pisugar import BatteryState
from instantlink_bridge.printing import PrintProgress
from instantlink_bridge.system_info import SystemInfo, default_hotspot_ssid, read_system_info
from instantlink_bridge.ui.display import Display, create_display
from instantlink_bridge.ui.input import GpioUiInput, NullInput, create_input
from instantlink_bridge.ui.models import PairedPrinter, SettingsRow, UiAction, UiMode, UiSnapshot
from instantlink_bridge.ui.pairing import (
    BluetoothctlPrinterPairer,
    InstantLinkPrinterSelector,
    PrinterPairer,
    PrinterPairingError,
    normalize_instax_name,
)
from instantlink_bridge.ui.settings import (
    ADJUSTABLE_SETTING_KEYS,
    INFO_SETTING_KEYS,
    PAGE_FOR_OPEN_KEY,
    PAGE_TITLES,
    SETTINGS_BY_PAGE,
    SettingKey,
    SettingsPage,
    WifiMode,
    bool_label,
    config_with_setting_value,
    fit_label,
    ftp_receive_mode_label,
    model_label,
    seconds_label,
    selected_option_index,
    setting_action_hint,
    setting_help_text,
    setting_options,
)
from instantlink_bridge.ui.status import (
    BlePrinterStatusProvider,
    InstantLinkPrinterStatusProvider,
    PrinterStatusProvider,
    PrinterStatusSnapshot,
    PrinterStatusUnavailableError,
)

LOGGER = logging.getLogger(__name__)
OFFLINE_STATUS_RETRY_S = 1.0
OFFLINE_STATUS_BACKOFF_RETRY_S = 5.0
RESTART_PRINTER_RETRY_S = 5.0
# Exponential backoff for a genuinely-offline printer: instead of rescanning every
# OFFLINE_STATUS_BACKOFF_RETRY_S forever, grow the delay with the consecutive-miss count so a
# missing printer is not polled aggressively. Base applies once the "offline" threshold is hit;
# the delay doubles per additional miss up to the cap.
OFFLINE_BACKOFF_BASE_S = 2.0
OFFLINE_BACKOFF_CAP_S = 30.0
PRINTER_STATUS_WARNING_INTERVAL_S = 30.0
OFFLINE_MESSAGE_AFTER_MISSES = 3
# Auto-rebond recovery: when a printer is power-cycled it clears its BLE pairing while the Pi
# keeps the stale bond key. The connection comes up (late GATT stage) but the first encrypted
# write fails. We detect that signature and automatically remove the BlueZ bond so the
# NoInputNoOutput agent re-bonds on reconnect. Guards keep this from thrashing:
#   * Act as soon as the signature appears (=1): on a healthy bond the post-subscribe write
#     succeeds, so a write failure after reaching notification_subscribe is a confident stale-bond
#     signal. Acting on the first one keeps power-cycle recovery fast; the cooldown below bounds
#     the cost of any rare false positive.
#   * Allow at most one rebond per device per cooldown window; if re-pairing does not fix it the
#     signature will recur but we fall back to normal backoff instead of looping.
AUTO_REBOND_SIGNATURE_THRESHOLD = 1
AUTO_REBOND_COOLDOWN_S = 120.0
# Silent-link recovery: when a bonded printer is power-cycled, BlueZ frequently auto-reconnects it
# and holds a silent link. A connected peripheral stops advertising, so InstantLink's
# advertisement-based scan can never find it and status connects loop on PrinterNotFound forever
# ("Finding Printer" that never resolves). When a not-found failure coincides with such a link we
# drop the BlueZ connection so the printer re-advertises and the next scan adopts it. The cooldown
# bounds churn; a genuinely off printer holds no connected link, so the recovery is a safe no-op.
SILENT_LINK_RECOVERY_COOLDOWN_S = 30.0
# Interchangeable generic "searching" placeholders that the live retry tick may overwrite. Specific
# diagnostics (e.g. "No printer signal", "Restart printer") are not listed here so they survive.
_GENERIC_SEARCHING_MESSAGES = frozenset({"Looking for printer", "Searching for printer"})
# Periodic render cadence. A lightweight tick re-renders the latest snapshot so the LCD never
# shows a stale frame while a coroutine is busy. The `snapshot == last_rendered` short-circuit in
# `_render` keeps this cheap and prevents render-spam.
RENDER_TICK_S = 0.35
USB_STATUS_POLL_S = 1.0
# Readiness freshness gate: "Ready to print" must be backed by a printer status that succeeded
# recently, not just stale cached film/mode. If the printer powers off, status polls fail and the
# last success ages out past this TTL, so the display leaves "Ready" even if a PRINTER_SEARCHING
# mode transition was dropped. The effective TTL is a small multiple of the keepalive interval so a
# few missed polls do not flip readiness, but a genuinely off printer downgrades within ~30 s.
PRINTER_STATUS_FRESH_TTL_S = 30.0
PREVIEW_BUILD_TIMEOUT_S = 20.0
RETURN_HOME_DELAY_S = 2.0
WIFI_MODE_HELPER = Path(
    os.environ.get(
        "INSTANTLINK_BRIDGE_WIFI_MODE_HELPER",
        "/usr/local/sbin/instantlink-bridge-wifi-mode",
    )
)
DEFAULT_HOTSPOT_SSID = default_hotspot_ssid()

WifiModeSetter = Callable[[WifiMode], Awaitable[str]]
PowerActivityCallback = Callable[[], Awaitable[None]]
FtpConfigAppliedCallback = Callable[[FtpConfig], None]
PreviewTool = Literal["zoom", "crop", "rotate"]
PREVIEW_TOOLS: tuple[PreviewTool, ...] = ("zoom", "crop", "rotate")
STATUS_VISIBLE_MODES = {
    UiMode.READY,
    UiMode.NO_FILM,
    UiMode.VALIDATION,
    UiMode.PRINTER_SEARCHING,
    UiMode.PRINTER_OFFLINE,
    UiMode.SETTINGS,
}


class KeepaliveConfigurableStatusProvider(Protocol):
    """Optional status provider hook for native printer keepalive."""

    async def configure_keepalive(self, interval_s: float | None) -> None:
        """Configure native printer keepalive interval."""


def _default_printer_pairer() -> PrinterPairer:
    if instantlink_backend_enabled():
        return InstantLinkPrinterSelector()
    return BluetoothctlPrinterPairer()


def _default_printer_status_provider() -> PrinterStatusProvider:
    if instantlink_backend_enabled():
        return InstantLinkPrinterStatusProvider()
    return BlePrinterStatusProvider()


class BridgeUi:
    """Own the LCD state, hardware input, and printer pairing actions."""

    def __init__(
        self,
        config: BridgeConfig,
        *,
        config_path: Path | None = None,
        display: Display | None = None,
        input_device: GpioUiInput | NullInput | None = None,
        pairer: PrinterPairer | None = None,
        status_provider: PrinterStatusProvider | None = None,
        ftp_activity: FtpActivityTracker | None = None,
        wifi_mode_setter: WifiModeSetter | None = None,
        power_activity_callback: PowerActivityCallback | None = None,
        ftp_config_applied_callback: FtpConfigAppliedCallback | None = None,
        system_info: SystemInfo | None = None,
    ) -> None:
        self._config = config
        self._config_path = config_path
        self._display = display if display is not None else create_display()
        self._input = input_device if input_device is not None else create_input()
        self._pairer = pairer if pairer is not None else _default_printer_pairer()
        self._status_provider = (
            status_provider if status_provider is not None else _default_printer_status_provider()
        )
        self._wifi_host: str | None = None
        self._hotspot_host: str | None = None
        self._usb_connected = False
        self._camera_receive_ready = False
        self._camera_connected = False
        self._camera_status_message: str | None = None
        self._camera_transport_message: str | None = None
        self._ftp_activity = ftp_activity
        self._wifi_mode_setter = (
            wifi_mode_setter if wifi_mode_setter is not None else set_wifi_mode_with_helper
        )
        self._power_activity_callback = power_activity_callback
        self._ftp_config_applied_callback = ftp_config_applied_callback
        self._system_info = system_info
        self._bridge_battery_percent: int | None = None
        self._bridge_power_model: str | None = power_backend_label(config.power.backend)
        self._bridge_power_status: str | None = None
        self._bridge_power_alert: str = BatteryAlert.UNKNOWN.value
        self._bridge_external_power: bool | None = None
        self._idle_stage = IdleStage.ACTIVE
        self._printer_keepalive_interval_s = config.printer.keepalive_interval_s
        self._battery_estimator = BatteryLifeEstimator()
        self._battery_minutes_remaining: int | None = None
        self._printer_status_misses = 0
        self._auto_rebond_signature_streak = 0
        self._last_auto_rebond_at: dict[str, float] = {}
        self._auto_rebond_task: asyncio.Task[None] | None = None
        self._last_silent_link_recovery_at: dict[str, float] = {}
        self._silent_link_recovery_task: asyncio.Task[None] | None = None
        self._last_printer_status_warning_at = -math.inf
        self._last_printer_status_warning_signature: tuple[str, ...] | None = None
        # Monotonic timestamp of the last successful printer status. Readiness ("Ready to print")
        # is only asserted while this is within PRINTER_STATUS_FRESH_TTL_S; -inf means never proven.
        self._last_printer_status_ok_at: float = float("-inf")
        self._actions: asyncio.Queue[UiAction] = asyncio.Queue(maxsize=20)
        self._snapshot = self._build_snapshot(
            mode=UiMode.BOOTING,
            printer_model=config.printer.model,
        )
        self._last_rendered_snapshot: UiSnapshot | None = None
        self._action_task: asyncio.Task[None] | None = None
        self._pairing_task: asyncio.Task[None] | None = None
        self._status_task: asyncio.Task[None] | None = None
        self._status_generation = 0
        self._render_tick_task: asyncio.Task[None] | None = None
        self._network_task: asyncio.Task[None] | None = None
        self._ftp_mode_task: asyncio.Task[None] | None = None
        self._image_reset_task: asyncio.Task[None] | None = None
        self._network_refresh_lock = asyncio.Lock()
        self._settings_operation_pending = False
        self._settings_picker_key: SettingKey | None = None
        self._pairing_generation = 0
        self._pair_return_page: SettingsPage | None = None
        self._forget_confirm_pending = False
        self._pending_print_result: asyncio.Future[PrintEdit | None] | None = None
        self._preview_edit = PrintEdit()
        self._preview_tool: PreviewTool = "zoom"
        self._preview_image: Image.Image | None = None
        self._preview_received: ReceivedImage | None = None
        self._preview_session_token = 0
        self._ignore_actions_until = 0.0
        self._settings_page = SettingsPage.MAIN
        self._settings_indices: dict[SettingsPage, int] = {page: 0 for page in SETTINGS_BY_PAGE}

    @property
    def config(self) -> BridgeConfig:
        """Return the current runtime config."""

        return self._config

    async def start(self) -> None:
        """Start rendering and input handling."""

        self._render()
        loop = asyncio.get_running_loop()
        self._ignore_actions_until = loop.time() + 1.5
        try:
            self._input.start(self._actions, loop)
        except Exception:
            LOGGER.exception("ui.input_start_failed")
        await self._refresh_network_status()
        self._action_task = asyncio.create_task(self._run_actions())
        self._render_tick_task = asyncio.create_task(self._run_render_tick())
        self._network_task = asyncio.create_task(self._run_network_status())
        if self._config.ftp.mode is not FtpReceiveMode.AUTO:
            self._ftp_mode_task = asyncio.create_task(self._apply_configured_ftp_mode_at_start())
        await self._configure_printer_keepalive()
        await self.refresh_printer_status()

    async def stop(self) -> None:
        """Stop background UI tasks."""

        for task in (
            self._action_task,
            self._pairing_task,
            self._status_task,
            self._render_tick_task,
            self._network_task,
            self._ftp_mode_task,
            self._image_reset_task,
            self._auto_rebond_task,
            self._silent_link_recovery_task,
        ):
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        self._input.close()
        await self._close_cached_printer_session()
        await self._status_provider.close()
        self._display.close()

    async def refresh_printer_status(self) -> None:
        """Detect whether an Instax printer is already paired."""

        try:
            printers = await self._pairer.list_paired()
        except Exception:
            LOGGER.exception("ui.paired_printer_lookup_failed")
            await self._cancel_status_refresh()
            self._snapshot = self._build_snapshot(
                mode=UiMode.ERROR,
                message="Bluetooth lookup failed",
            )
            self._render()
            return

        printer = self._select_printer(printers)
        if printer is None:
            self._snapshot = self._build_snapshot(
                mode=UiMode.NEEDS_PAIRING,
                printer_model=self._config.printer.model,
            )
            LOGGER.info("ui.status mode=needs_pairing paired_printer=none")
        else:
            self._printer_status_misses = 0
            self._snapshot = self._build_snapshot(
                mode=UiMode.PRINTER_SEARCHING,
                paired_printer=printer,
                printer_model=printer.model or self._known_printer_model(),
                printer_status_message="Looking for printer",
            )
            LOGGER.info("ui.status mode=printer_searching paired_printer=%s", printer.name)
        self._render()
        await self._schedule_printer_status_refresh()

    async def image_received(self, received: ReceivedImage) -> None:
        """Show a short image-received confirmation."""

        await self._record_power_activity()
        self._cancel_image_reset()
        self._snapshot = replace(
            self._snapshot,
            mode=UiMode.IMAGE_RECEIVED,
            last_image_name=received.path.name,
        )
        self._render()
        self._image_reset_task = asyncio.create_task(self._return_home_after_delay())

    async def await_print_confirmation(
        self,
        received: ReceivedImage,
        *,
        timeout_s: float | None = 0.0,
    ) -> PrintEdit | None:
        """Show preview/edit UI and return edits if the image should print."""

        self._cancel_image_reset()
        if self._preview_blocked_by_no_film(received):
            return None

        if timeout_s == 0:
            return PrintEdit()

        self._preview_edit = PrintEdit()
        self._preview_tool = "zoom"
        self._preview_image = None
        self._preview_received = received
        self._preview_session_token += 1
        session_token = self._preview_session_token
        loop = asyncio.get_running_loop()
        result: asyncio.Future[PrintEdit | None] = loop.create_future()
        self._pending_print_result = result
        deadline = None if timeout_s is None else loop.time() + timeout_s
        try:
            self._show_print_preview(received, None, timeout_s, title="Preparing preview")
            try:
                preview_image = await self._build_preview_image(received, self._preview_edit)
            except ImagePipelineError:
                if result.done() or not self._preview_session_is_current(
                    session_token,
                    received,
                    result,
                ):
                    return await asyncio.shield(result) if result.done() else None
                raise
            if result.done():
                return await asyncio.shield(result)
            if not self._preview_session_can_apply(session_token, received, result):
                return None
            self._preview_image = preview_image
            if deadline is None:
                self._show_print_preview(received, None, timeout_s)
                return await asyncio.shield(result)
            while True:
                remaining_s = max(0.0, deadline - loop.time())
                self._show_print_preview(received, remaining_s, timeout_s)
                if remaining_s <= 0:
                    if self._preview_blocked_by_no_film(received):
                        return None
                    return self._preview_edit
                wait_timeout = min(1.0, remaining_s)
                try:
                    return await asyncio.wait_for(
                        asyncio.shield(result),
                        timeout=wait_timeout,
                    )
                except TimeoutError:
                    continue
        finally:
            if self._preview_session_is_current(session_token, received, result):
                self._preview_session_token += 1
                self._pending_print_result = None
                self._preview_image = None
                self._preview_received = None

    async def _build_preview_image(
        self,
        received: ReceivedImage,
        edit: PrintEdit,
    ) -> Image.Image | None:
        model = await self._resolve_printer_model_for_preview()
        if model is None:
            raise ImagePipelineError("printer type unknown")
        prepared = await prepare_for_instax_async(
            received.path,
            model,
            fit=self._config.printer.fit,
            quality=self._config.printer.quality,
            edit=edit,
            timeout_s=PREVIEW_BUILD_TIMEOUT_S,
        )
        return await asyncio.to_thread(create_preview_from_prepared, prepared)

    async def _resolve_printer_model_for_preview(self) -> PrinterModel | None:
        model = self._known_printer_model()
        if model is not None:
            return model

        printer = await self._selected_printer_for_preview_detection()
        if printer is None:
            return None

        LOGGER.info(
            "ui.preview_detect_model_start address=%s name=%s",
            printer.address,
            printer.name,
        )
        try:
            status = await self._status_provider.fetch(printer)
        except asyncio.CancelledError:
            raise
        except PrinterStatusUnavailableError as exc:
            LOGGER.warning(
                "ui.preview_detect_model_unavailable address=%s name=%s error=%s diagnostics=%s",
                printer.address,
                printer.name,
                exc,
                scanner_diagnostics_summary(exc),
            )
            raise ImagePipelineError("printer offline") from exc
        except TimeoutError as exc:
            LOGGER.warning(
                "ui.preview_detect_model_timeout address=%s name=%s error=%s",
                printer.address,
                printer.name,
                exc,
            )
            raise ImagePipelineError("printer timed out") from exc
        except Exception as exc:
            LOGGER.warning(
                "ui.preview_detect_model_failed address=%s name=%s error_type=%s error=%s",
                printer.address,
                printer.name,
                type(exc).__name__,
                exc,
            )
            return None

        if self._snapshot.paired_printer is None:
            self._snapshot = replace(
                self._snapshot,
                paired_printer=printer,
                printer_model=printer.model,
            )
        self._printer_status_misses = 0
        self._last_printer_status_ok_at = self._monotonic()
        self._apply_printer_status(printer, status)
        return status.model or self._known_printer_model()

    async def _selected_printer_for_preview_detection(self) -> PairedPrinter | None:
        if self._snapshot.paired_printer is not None:
            return self._snapshot.paired_printer
        try:
            printers = await self._pairer.list_paired()
        except Exception:
            LOGGER.exception("ui.preview_paired_printer_lookup_failed")
            return None
        return self._select_printer(printers)

    def _preview_session_is_current(
        self,
        session_token: int,
        received: ReceivedImage,
        result: asyncio.Future[PrintEdit | None],
    ) -> bool:
        return (
            self._preview_session_token == session_token
            and self._preview_received == received
            and self._pending_print_result is result
        )

    def _preview_session_can_apply(
        self,
        session_token: int,
        received: ReceivedImage,
        result: asyncio.Future[PrintEdit | None],
    ) -> bool:
        return (
            self._preview_session_is_current(session_token, received, result)
            and not result.done()
            and self._snapshot.mode is UiMode.AWAITING_CONFIRM
        )

    def _preview_blocked_by_no_film(self, received: ReceivedImage) -> bool:
        if self._config.workflow.allow_print_without_film:
            return False
        if self._snapshot.film_remaining is None or self._snapshot.film_remaining > 0:
            return False
        self._cancel_image_reset()
        self._snapshot = replace(
            self._snapshot,
            mode=UiMode.NO_FILM,
            last_image_name=received.path.name,
            print_title=None,
            print_detail=None,
            print_progress_percent=None,
            preview_image=None,
        )
        self._render()
        self._image_reset_task = asyncio.create_task(self._return_home_after_delay())
        return True

    def _show_print_preview(
        self,
        received: ReceivedImage,
        remaining_s: float | None,
        timeout_s: float | None,
        *,
        title: str | None = None,
    ) -> None:
        if title is not None:
            resolved_title = title
            percent = None
        elif remaining_s is None:
            resolved_title = "Preview"
            percent = None
        else:
            resolved_title = f"Print in {math.ceil(remaining_s)}s"
            percent = (
                100
                if timeout_s is None or timeout_s <= 0
                else int((1 - remaining_s / timeout_s) * 100)
            )
        self._snapshot = replace(
            self._snapshot,
            mode=UiMode.AWAITING_CONFIRM,
            last_image_name=received.path.name,
            print_title=resolved_title,
            print_detail=self._preview_detail_text(),
            print_progress_percent=None if percent is None else max(0, min(100, percent)),
            preview_image=self._preview_image,
            preview_tool=self._preview_tool,
            preview_zoom=self._preview_edit.zoom,
            preview_rotation_degrees=self._preview_edit.rotate_degrees,
            preview_offset_x=self._preview_edit.offset_x,
            preview_offset_y=self._preview_edit.offset_y,
        )
        self._render()

    def _preview_detail_text(self) -> str:
        if self._preview_tool == "zoom":
            return "Zoom: Up/Down  K3 tool"
        if self._preview_tool == "crop":
            return "Crop: joystick  K3 tool"
        return "Rotate: Left/Right  K3 tool"

    async def _handle_preview_action(self, action: UiAction) -> None:
        result = self._pending_print_result
        if result is None or result.done():
            return
        session_token = self._preview_session_token
        if action is UiAction.BACK:
            self._return_to_cached_status_after_preview_cancel()
            result.set_result(None)
            return
        if action is UiAction.SELECT:
            received = self._preview_received
            if received is not None and self._preview_blocked_by_no_film(received):
                result.set_result(None)
                return
            result.set_result(self._preview_edit)
            return
        if action in {UiAction.HELP, UiAction.PAIR}:
            self._preview_tool = _next_preview_tool(self._preview_tool)
            self._snapshot = replace(
                self._snapshot,
                print_detail=self._preview_detail_text(),
                preview_tool=self._preview_tool,
            )
            self._render()
            return
        if action not in {UiAction.UP, UiAction.DOWN, UiAction.LEFT, UiAction.RIGHT}:
            return
        self._preview_edit = _adjust_preview_edit(self._preview_edit, self._preview_tool, action)
        received = self._preview_received
        if received is None:
            return
        edit = self._preview_edit
        self._show_print_preview(received, None, None, title="Updating preview")
        try:
            preview_image = await self._build_preview_image(received, edit)
        except ImagePipelineError:
            if not self._preview_session_can_apply(session_token, received, result):
                return
            LOGGER.exception("ui.preview_update_failed path=%s", received.path)
            result.set_result(None)
            self._snapshot = replace(
                self._snapshot,
                mode=UiMode.ERROR,
                message="Preview failed",
                preview_image=None,
            )
            self._render()
            return
        if not self._preview_session_can_apply(session_token, received, result):
            return
        self._preview_image = preview_image
        if not self._preview_session_can_apply(session_token, received, result):
            return
        self._snapshot = replace(
            self._snapshot,
            print_detail=self._preview_detail_text(),
            preview_image=self._preview_image,
            preview_tool=self._preview_tool,
            preview_zoom=self._preview_edit.zoom,
            preview_rotation_degrees=self._preview_edit.rotate_degrees,
            preview_offset_x=self._preview_edit.offset_x,
            preview_offset_y=self._preview_edit.offset_y,
        )
        self._render()

    def _return_to_cached_status_after_preview_cancel(self) -> None:
        self._show_cached_home_status()

    def _cached_home_mode(self) -> UiMode:
        if self._snapshot.paired_printer is None:
            return UiMode.NEEDS_PAIRING
        if (
            self._snapshot.film_remaining is not None
            and self._snapshot.film_remaining <= 0
            and not self._config.workflow.allow_print_without_film
        ):
            return UiMode.NO_FILM
        if self._snapshot.film_remaining is None or not self._camera_receive_ready:
            return UiMode.VALIDATION
        return UiMode.READY

    def _show_cached_home_status(self) -> None:
        self._snapshot = replace(
            self._snapshot,
            mode=self._cached_home_mode(),
            print_title=None,
            print_detail=None,
            print_progress_percent=None,
            preview_image=None,
        )
        self._render()

    async def printing_started(self, received: ReceivedImage) -> None:
        """Show that the received image is being sent to the printer."""

        await self._record_power_activity()
        self._cancel_image_reset()
        await self.pause_printer_status()
        self._snapshot = replace(
            self._snapshot,
            mode=UiMode.PRINTING,
            last_image_name=received.path.name,
            print_title="Starting print",
            print_detail="Checking printer",
            print_progress_percent=0,
            preview_image=None,
        )
        self._render()

    async def print_progress(self, progress: PrintProgress) -> None:
        """Show current print stage on the LCD."""

        if self._snapshot.mode is not UiMode.PRINTING:
            return
        self._snapshot = replace(
            self._snapshot,
            print_title=progress.title,
            print_detail=progress.detail,
            print_progress_percent=progress.percent,
        )
        self._render()

    async def print_complete(self, received: ReceivedImage) -> None:
        """Show a short print completion state before returning home."""

        self._cancel_image_reset()
        self._snapshot = replace(
            self._snapshot,
            mode=UiMode.PRINT_COMPLETE,
            last_image_name=received.path.name,
            print_title=None,
            print_detail=None,
            print_progress_percent=None,
            preview_image=None,
        )
        self._render()
        await self.resume_printer_status()
        self._image_reset_task = asyncio.create_task(self._return_home_after_print_complete())

    async def print_failed(self, message: str) -> None:
        """Show a print failure that requires user attention."""

        self._cancel_image_reset()
        self._snapshot = replace(
            self._snapshot,
            mode=UiMode.ERROR,
            message=message,
            print_title=None,
            print_detail=None,
            print_progress_percent=None,
            preview_image=None,
        )
        self._render()
        await self.resume_printer_status()

    async def pause_printer_status(self) -> None:
        """Pause background printer status polling and close cached BLE connection."""

        await self._cancel_status_refresh()

    async def resume_printer_status(self) -> None:
        """Refresh the boot printer status after a modal print state."""

        await self._schedule_printer_status_refresh()

    async def _run_actions(self) -> None:
        while True:
            action = await self._actions.get()
            try:
                await self._record_power_activity()
                loop = asyncio.get_running_loop()
                if loop.time() < self._ignore_actions_until:
                    LOGGER.info("ui.input_ignored action=%s reason=startup_settle", action)
                    continue
                LOGGER.info("ui.input action=%s mode=%s", action, self._snapshot.mode)
                await self._handle_action(action)
            finally:
                self._actions.task_done()

    async def apply_power_event(self, event: PowerEvent) -> None:
        """Apply bridge power and idle updates from the orchestrator."""

        if event.kind in {
            PowerEventKind.BATTERY_UPDATE,
            PowerEventKind.BATTERY_ALERT_CHANGED,
        }:
            self._apply_battery_event(event.battery, event.battery_alert)
            return
        if event.kind is PowerEventKind.IDLE_STAGE_CHANGED and event.idle_state is not None:
            self._apply_idle_stage(event.idle_state.stage)
            return
        if event.kind is PowerEventKind.SHUTDOWN_REQUESTED:
            self._apply_shutdown_requested(event)

    def _apply_battery_event(
        self,
        state: BatteryState | None,
        alert: BatteryAlert | None,
    ) -> None:
        previous_snapshot = self._snapshot
        if state is not None:
            self._bridge_battery_percent = (
                None if state.percentage is None else round(state.percentage)
            )
            self._bridge_power_model = state.model or self._bridge_power_model
            self._bridge_external_power = state.external_power
            self._bridge_power_status = bridge_power_status_text(state, alert)
        if alert is not None:
            self._bridge_power_alert = alert.value
        self._snapshot = replace(
            self._snapshot,
            bridge_battery_percent=self._bridge_battery_percent,
            bridge_power_model=self._bridge_power_model,
            bridge_power_status=self._bridge_power_status,
            bridge_power_alert=self._bridge_power_alert,
            bridge_external_power=self._bridge_external_power,
        )
        if self._snapshot.mode is UiMode.SETTINGS:
            self._snapshot = replace(self._snapshot, settings_rows=self._current_settings_rows())
        if self._snapshot == previous_snapshot:
            return
        if self._snapshot.mode in STATUS_VISIBLE_MODES:
            self._render()

    def _apply_idle_stage(self, stage: IdleStage) -> None:
        self._idle_stage = stage
        self._snapshot = replace(self._snapshot, idle_stage=stage.value)
        self._set_display_idle_stage(stage)
        if stage is IdleStage.ACTIVE or self._snapshot.mode is UiMode.SETTINGS:
            self._render()

    def _apply_shutdown_requested(self, event: PowerEvent) -> None:
        reason = event.shutdown_reason.value if event.shutdown_reason is not None else "shutdown"
        message = "Bridge battery critical" if reason == "low_battery" else "Idle shutdown"
        self._snapshot = replace(
            self._snapshot,
            mode=UiMode.ERROR,
            message=message,
            bridge_power_alert=(
                event.battery_alert.value if event.battery_alert is not None else "critical"
            ),
            bridge_power_status=message,
        )
        self._set_display_idle_stage(IdleStage.ACTIVE)
        self._render()

    async def _record_power_activity(self) -> None:
        if self._power_activity_callback is None:
            return
        await self._power_activity_callback()

    async def _handle_action(self, action: UiAction) -> None:
        if self._snapshot.mode is UiMode.SETTINGS:
            await self._handle_settings_action(action)
            return
        if self._snapshot.mode is UiMode.AWAITING_CONFIRM:
            await self._handle_preview_action(action)
            return
        if self._snapshot.mode is UiMode.PRINTING:
            return
        if self._snapshot.mode is UiMode.PAIRING:
            if action is UiAction.BACK:
                await self._cancel_pairing()
            return
        if action is UiAction.PAIR:
            await self._start_pairing()
            return
        if action is UiAction.HELP:
            if self._snapshot.mode is UiMode.PAIR_FAILED:
                await self._start_pairing()
                return
            if self._snapshot.paired_printer is not None:
                self._show_settings("Wi-Fi + FTP credentials", page=SettingsPage.CAMERA)
                return
            return
        if action is UiAction.BACK:
            if self._snapshot.mode is UiMode.PAIR_FAILED:
                if self._pair_return_page is not None:
                    page = self._pair_return_page
                    self._pair_return_page = None
                    self._show_settings(page=page)
                    return
                await self.refresh_printer_status()
                return
            await self.refresh_printer_status()
            return
        if action in {UiAction.UP, UiAction.DOWN} and self._snapshot.mode in {
            UiMode.NEEDS_PAIRING,
            UiMode.PAIR_FAILED,
        }:
            next_index = 0 if self._snapshot.mode is UiMode.NEEDS_PAIRING else 1
            if self._snapshot.mode is UiMode.PAIR_FAILED:
                next_index = 0
            self._snapshot = self._build_snapshot(
                mode=self._snapshot.mode,
                selected_index=next_index,
                printer_model=self._snapshot.printer_model,
                message=self._snapshot.message,
            )
            self._render()
            return
        if action is UiAction.SELECT:
            if self._snapshot.mode in {UiMode.NEEDS_PAIRING, UiMode.PAIR_FAILED}:
                if (
                    self._snapshot.mode is UiMode.NEEDS_PAIRING
                    or self._snapshot.selected_index == 0
                ):
                    await self._start_pairing()
                else:
                    await self.refresh_printer_status()
            elif self._snapshot.mode is UiMode.PRINT_COMPLETE:
                self._show_settings()
            else:
                self._show_settings()

    def _show_settings(
        self,
        message: str | None = None,
        *,
        page: SettingsPage | None = None,
    ) -> None:
        self._cancel_image_reset()
        self._settings_picker_key = None
        if page is not None:
            self._settings_page = page
        keys = SETTINGS_BY_PAGE[self._settings_page]
        selected_index = min(self._settings_indices[self._settings_page], len(keys) - 1)
        self._settings_indices[self._settings_page] = selected_index
        self._snapshot = replace(
            self._snapshot,
            mode=UiMode.SETTINGS,
            selected_index=selected_index,
            settings_title=PAGE_TITLES[self._settings_page],
            settings_rows=self._settings_rows(),
            settings_message=message if message is not None else self._settings_default_message(),
        )
        self._render()

    async def _handle_settings_action(self, action: UiAction) -> None:
        if self._settings_operation_pending:
            self._show_settings("Please wait")
            return
        if self._settings_picker_key is not None:
            await self._handle_setting_picker_action(action)
            return
        if action in {UiAction.HELP, UiAction.PAIR}:
            self._forget_confirm_pending = False
            if self._settings_page is SettingsPage.MAIN:
                self._show_settings("KEY1 opens category")
                return
            keys = SETTINGS_BY_PAGE[self._settings_page]
            self._show_settings(setting_help_text(keys[self._snapshot.selected_index]))
            return
        if action in {UiAction.BACK, UiAction.LEFT}:
            self._forget_confirm_pending = False
            if self._settings_page is SettingsPage.MAIN:
                await self.refresh_printer_status()
            else:
                self._show_settings(page=SettingsPage.MAIN)
            return
        if action in {UiAction.UP, UiAction.DOWN}:
            self._forget_confirm_pending = False
            direction = -1 if action is UiAction.UP else 1
            keys = SETTINGS_BY_PAGE[self._settings_page]
            selected_index = (self._snapshot.selected_index + direction) % len(keys)
            self._settings_indices[self._settings_page] = selected_index
            self._snapshot = replace(
                self._snapshot,
                selected_index=selected_index,
                settings_rows=self._settings_rows(),
                settings_message=self._settings_default_message(),
            )
            self._render()
            return
        if action in {UiAction.RIGHT, UiAction.SELECT}:
            keys = SETTINGS_BY_PAGE[self._settings_page]
            await self._activate_setting(keys[self._snapshot.selected_index])

    async def _activate_setting(self, key: SettingKey) -> None:
        LOGGER.info(
            "ui.setting_activate page=%s key=%s index=%s",
            self._settings_page.value,
            key.value,
            self._snapshot.selected_index,
        )
        if key in PAGE_FOR_OPEN_KEY:
            self._forget_confirm_pending = False
            self._show_settings(page=PAGE_FOR_OPEN_KEY[key])
            return
        if key is SettingKey.PAIR_PRINTER:
            self._forget_confirm_pending = False
            self._pair_return_page = self._settings_page
            await self._start_pairing()
            return
        if key is SettingKey.RESET_PRINTER_LINK:
            self._forget_confirm_pending = False
            await self._reset_printer_link_from_settings()
            return
        if key is SettingKey.FORGET_PRINTER:
            await self._confirm_or_forget_selected_printer()
            return
        self._forget_confirm_pending = False
        if key is SettingKey.REFRESH_STATUS:
            await self._refresh_status_from_settings()
            return
        if key in INFO_SETTING_KEYS:
            self._show_settings(self._info_message_for_setting(key))
            return
        if key not in ADJUSTABLE_SETTING_KEYS:
            LOGGER.error("ui.setting_unhandled key=%s", key.value)
            self._show_settings("Not implemented")
            return
        self._show_setting_picker(key)

    def _show_setting_picker(self, key: SettingKey, message: str | None = None) -> None:
        options = setting_options(key)
        if not options:
            self._show_settings("No choices")
            return
        self._settings_picker_key = key
        selected_index = min(selected_option_index(self._config, key), len(options) - 1)
        self._snapshot = replace(
            self._snapshot,
            mode=UiMode.SETTINGS,
            selected_index=selected_index,
            settings_title=self._setting_picker_title(key),
            settings_rows=self._setting_picker_rows(key, selected_index),
            settings_message=message or "Choose option",
        )
        self._render()

    async def _handle_setting_picker_action(self, action: UiAction) -> None:
        key = self._settings_picker_key
        if key is None:
            return
        options = setting_options(key)
        if action in {UiAction.BACK, UiAction.LEFT}:
            self._show_settings()
            return
        if action in {UiAction.HELP, UiAction.PAIR}:
            self._show_setting_picker(key, setting_help_text(key))
            return
        if action in {UiAction.UP, UiAction.DOWN, UiAction.RIGHT}:
            direction = -1 if action is UiAction.UP else 1
            selected_index = (self._snapshot.selected_index + direction) % len(options)
            self._snapshot = replace(
                self._snapshot,
                selected_index=selected_index,
                settings_rows=self._setting_picker_rows(key, selected_index),
                settings_message="KEY1 saves option",
            )
            self._render()
            return
        if action is not UiAction.SELECT:
            return
        option = options[self._snapshot.selected_index]
        updated = config_with_setting_value(self._config, key, option.value)
        if updated == self._config:
            if key is SettingKey.FTP_RECEIVE_MODE:
                await self._set_ftp_receive_mode(updated)
                return
            self._show_settings("Already selected")
            return
        if key is SettingKey.FTP_RECEIVE_MODE:
            await self._set_ftp_receive_mode(updated)
            return
        await self._set_config(updated, message="Saved")

    def _setting_picker_rows(
        self,
        key: SettingKey,
        _selected_index: int,
    ) -> tuple[SettingsRow, ...]:
        current_index = selected_option_index(self._config, key)
        return tuple(
            SettingsRow(
                option.label,
                "saved" if index == current_index else "",
                "KEY1 save",
            )
            for index, option in enumerate(setting_options(key))
        )

    def _setting_picker_title(self, key: SettingKey) -> str:
        row = self._settings_row_for_key(key, "")
        return row.label

    async def _set_config(self, config: BridgeConfig, *, message: str) -> bool:
        if self._config_path is not None:
            try:
                await asyncio.to_thread(write_config, config, self._config_path)
            except PermissionError:
                LOGGER.exception("ui.config_save_permission_denied path=%s", self._config_path)
                self._show_settings("Config not writable")
                return False
            except Exception:
                LOGGER.exception("ui.config_save_failed path=%s", self._config_path)
                self._show_settings("Save failed")
                return False
        previous_ftp = self._config.ftp
        previous_keepalive = self._config.printer.keepalive_interval_s
        self._config = config
        self._printer_keepalive_interval_s = config.printer.keepalive_interval_s
        if config.ftp != previous_ftp:
            self._notify_ftp_config_applied(config.ftp)
        if config.printer.keepalive_interval_s != previous_keepalive:
            await self._configure_printer_keepalive()
        self._snapshot = replace(
            self._snapshot,
            ftp_receive_mode=config.ftp.mode.value,
            allow_print_without_film=config.workflow.allow_print_without_film,
        )
        self._show_settings(message)
        return True

    def _notify_ftp_config_applied(self, config: FtpConfig) -> None:
        if self._ftp_config_applied_callback is None:
            return
        try:
            self._ftp_config_applied_callback(config)
        except Exception:
            LOGGER.exception("ui.ftp_config_applied_callback_failed")

    async def _configure_printer_keepalive(self) -> None:
        configure = getattr(self._status_provider, "configure_keepalive", None)
        if not callable(configure):
            return
        try:
            await cast(
                KeepaliveConfigurableStatusProvider,
                self._status_provider,
            ).configure_keepalive(self._printer_keepalive_interval_s)
        except Exception:
            LOGGER.exception(
                "ui.printer_keepalive_config_failed interval_s=%s",
                self._printer_keepalive_interval_s,
            )

    async def _set_ftp_receive_mode(self, config: BridgeConfig) -> None:
        mode = config.ftp.mode
        label = ftp_receive_mode_label(mode)
        self._settings_picker_key = None
        self._settings_operation_pending = True
        self._snapshot = replace(
            self._snapshot,
            settings_rows=self._settings_rows(),
            settings_message=_ftp_mode_switching_message(mode),
        )
        self._render()
        try:
            await self._apply_ftp_mode_network(mode)
        except Exception as exc:
            LOGGER.warning(
                "ui.ftp_mode_failed mode=%s error_type=%s error=%s",
                mode.value,
                type(exc).__name__,
                exc,
            )
            self._show_settings(_ftp_mode_failed_message(mode))
            self._settings_operation_pending = False
            return
        try:
            if await self._set_config(config, message=_ftp_mode_saved_message(mode, label)):
                await self._refresh_network_status()
        finally:
            self._settings_operation_pending = False

    async def _apply_ftp_mode_network(self, mode: FtpReceiveMode) -> None:
        wifi_mode = _wifi_mode_for_ftp_receive_mode(mode)
        if wifi_mode is None:
            return
        await self._wifi_mode_setter(wifi_mode)

    async def _apply_configured_ftp_mode_at_start(self) -> None:
        mode = self._config.ftp.mode
        try:
            await self._apply_ftp_mode_network(mode)
            await self._refresh_network_status()
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.warning("ui.ftp_mode_boot_apply_failed mode=%s", mode.value, exc_info=True)

    async def _confirm_or_forget_selected_printer(self) -> None:
        if self._snapshot.paired_printer is None:
            self._forget_confirm_pending = False
            self._show_settings("No printer saved")
            return
        if not self._forget_confirm_pending:
            self._forget_confirm_pending = True
            self._show_settings("Press again to forget")
            return
        self._forget_confirm_pending = False
        await self._forget_selected_printer()

    async def _forget_selected_printer(self) -> None:
        if self._snapshot.paired_printer is None:
            self._show_settings("No printer saved")
            return
        await self._cancel_status_refresh()
        await self._close_cached_printer_session()
        try:
            await self._pairer.forget_selected()
        except Exception:
            LOGGER.exception("ui.printer_forget_failed")
            self._show_settings("Forget failed")
            return
        LOGGER.info("ui.printer_forgot")
        self._snapshot = replace(
            self._snapshot,
            paired_printer=None,
            film_remaining=None,
            printer_battery=None,
            printer_is_charging=None,
            printer_model=self._config.printer.model,
            printer_status_message=None,
        )
        self._show_settings("Printer forgotten")

    def _settings_rows(self) -> tuple[SettingsRow, ...]:
        printer_name = (
            self._snapshot.paired_printer.name
            if self._snapshot.paired_printer is not None
            else "none"
        )
        return tuple(
            replace(
                self._settings_row_for_key(key, printer_name),
                hint=setting_action_hint(key),
            )
            for key in SETTINGS_BY_PAGE[self._settings_page]
        )

    def _current_settings_rows(self) -> tuple[SettingsRow, ...]:
        key = self._settings_picker_key
        if key is None:
            return self._settings_rows()
        return self._setting_picker_rows(key, self._snapshot.selected_index)

    def _settings_default_message(self) -> str | None:
        if self._settings_page is SettingsPage.MAIN:
            return "Choose category"
        if self._settings_page is SettingsPage.CAMERA:
            return "Wi-Fi + FTP credentials"
        return None

    def _settings_row_for_key(self, key: SettingKey, printer_name: str) -> SettingsRow:
        if key is SettingKey.OPEN_PRINTER:
            return SettingsRow("Printer", "")
        if key is SettingKey.OPEN_CAMERA:
            return SettingsRow("Upload FTP", "")
        if key is SettingKey.OPEN_NETWORK:
            return SettingsRow("Network", "")
        if key is SettingKey.OPEN_PRINT:
            return SettingsRow("Print", "")
        if key is SettingKey.OPEN_SYSTEM:
            return SettingsRow("System", "")
        if key is SettingKey.PAIR_PRINTER:
            return SettingsRow("Find printer", printer_name)
        if key is SettingKey.RESET_PRINTER_LINK:
            return SettingsRow("Reset BLE link", "run")
        if key is SettingKey.FORGET_PRINTER:
            return SettingsRow(
                "Forget printer",
                "saved" if self._snapshot.paired_printer else "none",
            )
        if key is SettingKey.PRINTER_MODEL:
            return SettingsRow("Printer type", model_label(self._config.printer.model))
        if key is SettingKey.KEEPALIVE:
            return SettingsRow(
                "Keepalive",
                seconds_label(self._config.printer.keepalive_interval_s),
            )
        if key is SettingKey.FTP_RECEIVE_MODE:
            return SettingsRow("FTP mode", self._ftp_receive_mode_value())
        if key is SettingKey.FTP_HOST_INFO:
            return SettingsRow("FTP host", self._camera_ftp_host_value())
        if key is SettingKey.FTP_MODE_INFO:
            return SettingsRow("Active Wi-Fi", self._ftp_mode_value())
        if key is SettingKey.FTP_USERNAME_INFO:
            return SettingsRow("FTP user", self._config.ftp.username)
        if key is SettingKey.FTP_PASSWORD_INFO:
            return SettingsRow("FTP pass", self._config.ftp.password)
        if key is SettingKey.CAMERA_SETUP_INFO:
            return SettingsRow("Upload note", self._camera_setup_value())
        if key is SettingKey.IMAGE_FIT:
            return SettingsRow("Image fit", fit_label(self._config.printer.fit))
        if key is SettingKey.JPEG_QUALITY:
            return SettingsRow("JPEG quality", str(self._config.printer.quality))
        if key is SettingKey.AUTO_PRINT_DELAY:
            return SettingsRow(
                "Auto print",
                seconds_label(self._config.workflow.auto_print_delay_s),
            )
        if key is SettingKey.ALLOW_PRINT_WITHOUT_FILM:
            return SettingsRow(
                "No-film test",
                bool_label(self._config.workflow.allow_print_without_film),
            )
        if key is SettingKey.NETWORK_ETHERNET_INFO:
            return SettingsRow("USB debug", self._ethernet_network_value())
        if key is SettingKey.NETWORK_WIFI_INFO:
            return SettingsRow("Same Wi-Fi adv", self._wifi_network_value())
        if key is SettingKey.NETWORK_HOTSPOT_INFO:
            return SettingsRow("Bridge FTP", self._hotspot_network_value())
        if key is SettingKey.NETWORK_HOTSPOT_SSID_INFO:
            return SettingsRow("Bridge Wi-Fi", self._hotspot_ssid_value())
        if key is SettingKey.NETWORK_HOTSPOT_PASSWORD_INFO:
            return SettingsRow("Wi-Fi PIN", self._hotspot_pin_value())
        if key is SettingKey.NETWORK_BLUETOOTH_INFO:
            return SettingsRow("Bluetooth", self._bluetooth_network_value())
        if key is SettingKey.SYSTEM_DEVICE_ID:
            return SettingsRow("Device ID", self._system_info_snapshot().device_id)
        if key is SettingKey.SYSTEM_APP_VERSION:
            return SettingsRow("App version", self._system_info_snapshot().app_version)
        if key is SettingKey.SYSTEM_PYTHON_VERSION:
            return SettingsRow("Python", self._system_info_snapshot().python_version)
        if key is SettingKey.SYSTEM_BLUEZ_VERSION:
            return SettingsRow("BlueZ", self._system_info_snapshot().bluez_version)
        if key is SettingKey.SYSTEM_OS_VERSION:
            return SettingsRow("OS", self._system_info_snapshot().os_version)
        if key is SettingKey.SYSTEM_POWER_INFO:
            return SettingsRow("Power", self._power_summary_value())
        if key is SettingKey.SYSTEM_BATTERY_INFO:
            return SettingsRow("Battery", self._battery_power_value())
        if key is SettingKey.SYSTEM_IDLE_INFO:
            return SettingsRow("Idle", self._idle_power_value())
        if key is SettingKey.SYSTEM_IDLE_POWEROFF:
            return SettingsRow(
                "Idle poweroff",
                bool_label(self._config.power.idle_poweroff_enabled),
            )
        if key is SettingKey.REFRESH_STATUS:
            return SettingsRow("Refresh status", "run")
        return SettingsRow("Unknown", "")

    def _system_info_snapshot(self) -> SystemInfo:
        if self._system_info is None:
            self._system_info = read_system_info()
        return self._system_info

    def _network_summary_value(self) -> str:
        parts: list[str] = []
        if self._usb_connected:
            parts.append("USB")
        if self._wifi_host is not None:
            parts.append("Wi-Fi")
        if self._hotspot_host is not None:
            parts.append("Bridge")
        if self._snapshot.paired_printer is not None:
            parts.append("BT")
        return "+".join(parts) if parts else "info"

    def _camera_ftp_summary_value(self) -> str:
        mode = self._config.ftp.mode
        if mode is FtpReceiveMode.HOTSPOT:
            return "Bridge ready" if self._hotspot_host is not None else "Bridge off"
        if mode is FtpReceiveMode.WIRED:
            if self._usb_connected:
                return "USB debug"
            return "USB debug off"
        if mode is FtpReceiveMode.PEER:
            return "Same Wi-Fi adv" if self._wifi_host is not None else "Same Wi-Fi adv off"
        return self._ftp_mode_value()

    def _ethernet_network_value(self) -> str:
        if self._camera_transport_message in {"Admin USB no IP", "USB debug no IP"}:
            return "no IP"
        if self._usb_connected:
            return f"SSH {self._config.ftp.host}"
        return "off"

    def _wifi_network_value(self) -> str:
        return self._wifi_host or "off"

    def _hotspot_network_value(self) -> str:
        return self._hotspot_host or f"off {self._config.ftp.hotspot_host}"

    def _bluetooth_network_value(self) -> str:
        if self._snapshot.paired_printer is None:
            return "not selected"
        message = self._snapshot.printer_status_message
        if self._snapshot.mode is UiMode.PRINTER_OFFLINE or message == "Hold K3 to re-pair":
            return "offline"
        if self._snapshot.mode is UiMode.PRINTER_SEARCHING:
            return "searching"
        if message is not None and message.startswith(
            ("Scanning", "Looking", "Saw ", "Printer seen", "Retrying")
        ):
            return "searching"
        if self._snapshot.film_remaining is not None or self._snapshot.printer_battery is not None:
            return "connected"
        if message is None:
            return "saved"
        return "saved"

    def _ftp_receive_mode_value(self) -> str:
        return ftp_receive_mode_label(self._config.ftp.mode)

    def _camera_ftp_host_value(self) -> str:
        mode = self._config.ftp.mode
        if mode is FtpReceiveMode.WIRED:
            return self._config.ftp.host
        if mode is FtpReceiveMode.HOTSPOT:
            return self._config.ftp.hotspot_host
        if mode is FtpReceiveMode.PEER:
            return self._wifi_host or self._config.ftp.preferred_wifi_host or "see Network"
        if self._camera_transport_message is not None:
            if self._camera_transport_message.startswith("Bridge"):
                return self._config.ftp.hotspot_host
            if (
                self._camera_transport_message.startswith("Same Wi-Fi")
                and self._wifi_host is not None
            ):
                return self._wifi_host
        if self._hotspot_host is not None:
            return self._config.ftp.hotspot_host
        if self._wifi_host is not None:
            return self._wifi_host
        return self._config.ftp.hotspot_host

    def _ftp_mode_value(self) -> str:
        if self._camera_transport_message is not None:
            if self._camera_transport_message.startswith(("Admin USB", "USB debug")):
                return "USB debug"
            if self._camera_transport_message.startswith("Bridge"):
                return "Bridge Wi-Fi"
            if self._camera_transport_message.startswith("Same Wi-Fi"):
                return "Same Wi-Fi adv"
        if self._hotspot_host is not None:
            return "Bridge Wi-Fi"
        if self._wifi_host is not None:
            return "Same Wi-Fi adv"
        return "No FTP Wi-Fi"

    def _camera_setup_value(self) -> str:
        mode = self._config.ftp.mode
        if mode is FtpReceiveMode.WIRED:
            return "USB debug only"
        if mode is FtpReceiveMode.HOTSPOT:
            return "join bridge"
        if mode is FtpReceiveMode.PEER:
            return "same Wi-Fi adv"
        return "Wi-Fi profile"

    def _hotspot_ssid_value(self) -> str:
        value = _read_first_line(_hotspot_ssid_file())
        return value or DEFAULT_HOTSPOT_SSID

    def _hotspot_pin_value(self) -> str:
        value = _read_first_line(_hotspot_psk_file())
        if value is None:
            return "not set"
        return value

    def _info_message_for_setting(self, key: SettingKey) -> str:
        if key is SettingKey.FTP_RECEIVE_MODE:
            return f"FTP mode: {self._ftp_receive_mode_value()}"
        if key is SettingKey.FTP_HOST_INFO:
            return f"FTP host: {self._camera_ftp_host_value()}"
        if key is SettingKey.FTP_MODE_INFO:
            return f"Active Wi-Fi: {self._ftp_mode_value()}"
        if key is SettingKey.FTP_USERNAME_INFO:
            return f"FTP user: {self._config.ftp.username}"
        if key is SettingKey.FTP_PASSWORD_INFO:
            return f"FTP pass: {self._config.ftp.password}"
        if key is SettingKey.CAMERA_SETUP_INFO:
            return _camera_setup_info_message(self._config.ftp.mode)
        if key is SettingKey.NETWORK_ETHERNET_INFO:
            return f"USB debug: {self._ethernet_network_value()}"
        if key is SettingKey.NETWORK_WIFI_INFO:
            return f"Same Wi-Fi adv: {self._wifi_network_value()}"
        if key is SettingKey.NETWORK_HOTSPOT_INFO:
            return f"Bridge FTP: {self._hotspot_network_value()}"
        if key is SettingKey.NETWORK_HOTSPOT_SSID_INFO:
            return f"Wi-Fi: {self._hotspot_ssid_value()}"
        if key is SettingKey.NETWORK_HOTSPOT_PASSWORD_INFO:
            return f"Wi-Fi PIN: {self._hotspot_pin_value()}"
        if key is SettingKey.NETWORK_BLUETOOTH_INFO:
            printer = self._snapshot.paired_printer
            if printer is not None:
                return f"BT: {printer.name}"
            return "BT: not selected"
        if key is SettingKey.SYSTEM_DEVICE_ID:
            return f"Device: {self._system_info_snapshot().device_id}"
        if key is SettingKey.SYSTEM_APP_VERSION:
            return f"App: {self._system_info_snapshot().app_version}"
        if key is SettingKey.SYSTEM_PYTHON_VERSION:
            return f"Python: {self._system_info_snapshot().python_version}"
        if key is SettingKey.SYSTEM_BLUEZ_VERSION:
            return f"BlueZ: {self._system_info_snapshot().bluez_version}"
        if key is SettingKey.SYSTEM_OS_VERSION:
            return f"OS: {self._system_info_snapshot().os_version}"
        if key is SettingKey.SYSTEM_POWER_INFO:
            return f"Power: {self._power_summary_value()}"
        if key is SettingKey.SYSTEM_BATTERY_INFO:
            return f"Battery: {self._battery_power_value()}"
        if key is SettingKey.SYSTEM_IDLE_INFO:
            return f"Idle: {self._idle_power_value()}"
        if key is SettingKey.SYSTEM_IDLE_POWEROFF:
            return f"Idle poweroff: {bool_label(self._config.power.idle_poweroff_enabled)}"
        return _info_message_for_setting(key)

    async def _refresh_status_from_settings(self) -> None:
        page = self._settings_page
        self._show_settings("Refreshing status")
        try:
            await self._refresh_network_status()
        except Exception:
            LOGGER.exception("ui.network_status_refresh_failed_from_settings")
        try:
            printers = await self._pairer.list_paired()
        except Exception:
            LOGGER.exception("ui.paired_printer_lookup_failed")
            self._show_settings("Refresh failed", page=page)
            return
        printer = self._select_printer(printers)
        if printer is None:
            await self._cancel_status_refresh()
            self._snapshot = replace(
                self._snapshot,
                paired_printer=None,
                film_remaining=None,
                printer_battery=None,
                printer_is_charging=None,
                printer_model=self._config.printer.model,
                printer_status_message=None,
            )
            self._show_settings("No printer saved", page=page)
            return
        self._printer_status_misses = 0
        self._snapshot = replace(
            self._snapshot,
            paired_printer=printer,
            printer_model=printer.model or self._known_printer_model(),
            printer_status_message="Looking for printer",
        )
        await self._schedule_printer_status_refresh()
        self._show_settings("Status refreshed", page=page)

    async def _reset_printer_link_from_settings(self) -> None:
        page = self._settings_page
        if self._snapshot.paired_printer is None:
            self._show_settings("No printer saved", page=page)
            return
        self._show_settings("Resetting BLE link", page=page)
        await self._cancel_status_refresh()
        await self._close_cached_printer_session()
        self._printer_status_misses = 0
        self._snapshot = replace(
            self._snapshot,
            film_remaining=None,
            printer_battery=None,
            printer_is_charging=None,
            printer_status_message="Looking for printer",
        )
        await self._schedule_printer_status_refresh()
        self._show_settings("BLE link reset", page=page)

    async def _start_pairing(self) -> None:
        if self._pairing_task is not None and not self._pairing_task.done():
            return
        self._cancel_image_reset()
        await self._cancel_status_refresh()
        await self._close_cached_printer_session()
        previous_printer = self._snapshot.paired_printer
        self._pairing_generation += 1
        generation = self._pairing_generation
        self._snapshot = self._build_snapshot(
            mode=UiMode.PAIRING,
            paired_printer=previous_printer,
            printer_model=self._known_printer_model(),
        )
        self._render()
        self._pairing_task = asyncio.create_task(
            self._pair_in_background(generation, previous_printer)
        )

    async def _cancel_pairing(self) -> None:
        self._pairing_generation += 1
        if self._pairing_task is not None and not self._pairing_task.done():
            self._pairing_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._pairing_task
        self._pairing_task = None
        LOGGER.info("ui.printer_pair_cancelled")
        if self._pair_return_page is not None:
            page = self._pair_return_page
            self._pair_return_page = None
            self._show_settings("Pairing cancelled", page=page)
            return
        await self.refresh_printer_status()

    async def _pair_in_background(
        self,
        generation: int | None = None,
        previous_printer: PairedPrinter | None = None,
    ) -> None:
        if generation is None:
            generation = self._pairing_generation
        if previous_printer is None:
            previous_printer = self._snapshot.paired_printer
        try:
            printer = await self._pairer.pair_first_available()
        except PrinterPairingError as exc:
            LOGGER.warning("ui.printer_pair_failed error=%s", exc)
            if not self._pairing_result_is_current(generation):
                return
            self._snapshot = self._build_snapshot(
                mode=UiMode.PAIR_FAILED,
                paired_printer=previous_printer,
                printer_model=self._known_printer_model(),
                message=str(exc),
            )
        except Exception:
            LOGGER.exception("ui.printer_pair_unexpected_error")
            if not self._pairing_result_is_current(generation):
                return
            self._snapshot = self._build_snapshot(
                mode=UiMode.PAIR_FAILED,
                paired_printer=previous_printer,
                printer_model=self._known_printer_model(),
                message="Bluetooth setup failed",
            )
        else:
            if not self._pairing_result_is_current(generation):
                return
            LOGGER.info("ui.printer_paired name=%s address=%s", printer.name, printer.address)
            self._pair_return_page = None
            first_pairing = previous_printer is None
            self._snapshot = self._build_snapshot(
                mode=UiMode.PRINTER_SEARCHING,
                paired_printer=printer,
                printer_model=self._config.printer.model,
                printer_status_message="Looking for printer",
            )
            await self._schedule_printer_status_refresh()
            if first_pairing:
                self._show_settings("Enter these on sender", page=SettingsPage.CAMERA)
        finally:
            if self._pairing_task is asyncio.current_task():
                self._pairing_task = None
        self._render()

    def _pairing_result_is_current(self, generation: int) -> bool:
        return generation == self._pairing_generation and self._snapshot.mode is UiMode.PAIRING

    async def _return_home_after_delay(self) -> None:
        await asyncio.sleep(RETURN_HOME_DELAY_S)
        await self.refresh_printer_status()

    async def _return_home_after_print_complete(self) -> None:
        await asyncio.sleep(RETURN_HOME_DELAY_S)
        self._show_cached_home_status()

    def _select_printer(self, printers: list[PairedPrinter]) -> PairedPrinter | None:
        configured_name = self._config.printer.device_name
        if configured_name is not None:
            for printer in printers:
                if printer.name == configured_name:
                    return printer
        return printers[0] if printers else None

    def _known_printer_model(self) -> PrinterModel | None:
        paired_model = (
            self._snapshot.paired_printer.model
            if self._snapshot.paired_printer is not None
            else None
        )
        return self._snapshot.printer_model or paired_model or self._config.printer.model

    def _build_snapshot(
        self,
        *,
        mode: UiMode,
        paired_printer: PairedPrinter | None = None,
        selected_index: int = 0,
        last_image_name: str | None = None,
        film_remaining: int | None = None,
        film_capacity: int = 10,
        printer_battery: int | None = None,
        printer_is_charging: bool | None = None,
        printer_model: PrinterModel | None = None,
        printer_status_message: str | None = None,
        message: str | None = None,
        settings_rows: tuple[SettingsRow, ...] = (),
        settings_message: str | None = None,
        settings_title: str = "Settings",
    ) -> UiSnapshot:
        return UiSnapshot(
            mode=mode,
            ftp_host=self._config.ftp.host,
            ftp_receive_mode=self._config.ftp.mode.value,
            wifi_host=self._wifi_host,
            hotspot_host=self._hotspot_host,
            hotspot_ftp_host=self._config.ftp.hotspot_host,
            preferred_wifi_host=self._config.ftp.preferred_wifi_host,
            usb_connected=self._usb_connected,
            camera_receive_ready=self._camera_receive_ready,
            camera_connected=self._camera_connected,
            camera_status_message=self._camera_status_message,
            camera_transport_message=self._camera_transport_message,
            paired_printer=paired_printer,
            selected_index=selected_index,
            last_image_name=last_image_name,
            film_remaining=film_remaining,
            film_capacity=film_capacity,
            printer_battery=printer_battery,
            printer_is_charging=printer_is_charging,
            printer_battery_minutes_remaining=self._battery_minutes_remaining,
            printer_model=printer_model,
            printer_status_message=printer_status_message,
            printer_status_fresh=self._printer_status_is_fresh(),
            bridge_battery_percent=self._bridge_battery_percent,
            bridge_power_model=self._bridge_power_model,
            bridge_power_status=self._bridge_power_status,
            bridge_power_alert=self._bridge_power_alert,
            bridge_external_power=self._bridge_external_power,
            idle_stage=self._idle_stage.value,
            message=message,
            allow_print_without_film=self._config.workflow.allow_print_without_film,
            settings_title=settings_title,
            settings_rows=settings_rows,
            settings_message=settings_message,
        )

    def _cancel_image_reset(self) -> None:
        if self._image_reset_task is not None:
            self._image_reset_task.cancel()
            self._image_reset_task = None

    async def _schedule_printer_status_refresh(self) -> None:
        await self._cancel_status_refresh()
        printer = self._snapshot.paired_printer
        if printer is None:
            return
        generation = self._status_generation
        self._status_task = asyncio.create_task(self._poll_printer_status(printer, generation))

    async def _poll_printer_status(self, printer: PairedPrinter, generation: int) -> None:
        try:
            while True:
                if generation != self._status_generation:
                    return
                self._show_printer_searching_if_retrying(printer, "Searching for printer")
                online = await self._refresh_printer_status_in_background(printer, generation)
                await asyncio.sleep(self._printer_status_retry_delay(online))
        finally:
            await self._status_provider.close()

    async def _refresh_printer_status_in_background(
        self,
        printer: PairedPrinter,
        generation: int | None = None,
    ) -> bool:
        try:
            status = await self._status_provider.fetch(printer)
        except asyncio.CancelledError:
            raise
        except PrinterStatusUnavailableError as exc:
            diagnostics = scanner_diagnostics_summary(exc)
            if self._should_log_printer_status_warning(
                "unavailable",
                printer,
                str(exc),
                diagnostics,
            ):
                LOGGER.warning(
                    "ui.printer_status_unavailable address=%s name=%s error=%s diagnostics=%s",
                    printer.address,
                    printer.name,
                    exc,
                    diagnostics,
                )
            self._printer_status_misses += 1
            self._maybe_auto_rebond(printer, exc)
            self._maybe_recover_silent_link(printer, exc)
            self._apply_status_failure(printer, self._unavailable_message(exc), generation)
            return False
        except TimeoutError as exc:
            if self._should_log_printer_status_warning("timeout", printer, str(exc)):
                LOGGER.warning(
                    "ui.printer_status_connect_timeout address=%s name=%s error=%s",
                    printer.address,
                    printer.name,
                    exc,
                )
            self._printer_status_misses += 1
            self._apply_status_failure(
                printer,
                self._connect_failure_message("Printer seen; connecting"),
                generation,
            )
            return False
        except Exception as exc:
            if self._should_log_printer_status_warning(
                "refresh_failed",
                printer,
                type(exc).__name__,
                str(exc),
            ):
                LOGGER.warning(
                    "ui.printer_status_refresh_failed address=%s name=%s error_type=%s error=%s",
                    printer.address,
                    printer.name,
                    type(exc).__name__,
                    exc,
                )
            self._printer_status_misses += 1
            self._apply_status_failure(
                printer,
                self._connect_failure_message("Printer seen; connecting"),
                generation,
            )
            return False
        if generation is not None and generation != self._status_generation:
            return True
        self._printer_status_misses = 0
        self._auto_rebond_signature_streak = 0
        self._last_auto_rebond_at.pop(_auto_rebond_key(printer), None)
        self._clear_printer_status_warning_state()
        self._last_printer_status_ok_at = self._monotonic()
        estimate = self._feed_battery_estimator(status)
        LOGGER.info(
            "ui.printer_status film_remaining=%s battery=%s charging=%s model=%s "
            "battery_minutes_remaining=%s keepalive_interval_s=%s",
            status.film_remaining,
            status.battery,
            status.is_charging,
            status.model.value if status.model is not None else "unknown",
            estimate.minutes_remaining if estimate is not None else None,
            self._printer_keepalive_interval_s,
        )
        self._apply_printer_status(printer, status)
        return True

    def _feed_battery_estimator(
        self,
        status: PrinterStatusSnapshot,
    ) -> BatteryEstimate | None:
        """Sample the battery estimator from a successful status and cache the estimate.

        Returns ``None`` when the status carries no battery reading (nothing to sample); otherwise
        updates ``self._battery_minutes_remaining`` for the next snapshot. While charging the
        estimator reports no drain estimate and resets its discharge history internally.
        """

        if status.battery is None or status.is_charging is None:
            return None
        estimate = self._battery_estimator.add_sample(
            status.battery,
            is_charging=status.is_charging,
        )
        if estimate.state is BatteryEstimateState.DISCHARGING:
            self._battery_minutes_remaining = estimate.minutes_remaining
        else:
            self._battery_minutes_remaining = None
        return estimate

    def _printer_status_retry_delay(self, online: bool) -> float:
        if online:
            return self._printer_keepalive_interval_s
        if self._snapshot.printer_status_message == "Restart printer":
            return RESTART_PRINTER_RETRY_S
        if self._printer_status_misses >= OFFLINE_MESSAGE_AFTER_MISSES:
            return self._offline_backoff_delay()
        return OFFLINE_STATUS_RETRY_S

    def _maybe_auto_rebond(
        self,
        printer: PairedPrinter,
        exc: PrinterStatusUnavailableError,
    ) -> None:
        """Trigger a background auto-rebond when the stale-bond signature is confirmed.

        The bridge rebonds as soon as the stale-bond signature appears
        (``AUTO_REBOND_SIGNATURE_THRESHOLD`` = 1); a non-signature failure resets the streak
        counter. At most one rebond runs per device per
        ``AUTO_REBOND_COOLDOWN_S``; if a recent rebond did not fix it the signature recurs but we
        fall back to normal searching/backoff instead of removing the bond again. The actual
        bond removal + reconnect runs fire-and-forget so the poll/action loops never block.
        """

        if not exc.stale_bond_suspected:
            # Any non-signature failure breaks the "consecutive" requirement.
            self._auto_rebond_signature_streak = 0
            return

        self._auto_rebond_signature_streak += 1
        if self._auto_rebond_signature_streak < AUTO_REBOND_SIGNATURE_THRESHOLD:
            return
        if self._auto_rebond_task is not None and not self._auto_rebond_task.done():
            return

        key = _auto_rebond_key(printer)
        now = self._monotonic()
        last_at = self._last_auto_rebond_at.get(key)
        if last_at is not None and now - last_at < AUTO_REBOND_COOLDOWN_S:
            LOGGER.info(
                "instantlink.auto_rebond skipped=cooldown address=%s since_last_s=%.1f",
                printer.address,
                now - last_at,
            )
            return

        self._last_auto_rebond_at[key] = now
        self._auto_rebond_signature_streak = 0
        LOGGER.warning(
            "instantlink.auto_rebond action=remove_bond address=%s reason=stale_bond_write_failed",
            printer.address,
        )
        self._auto_rebond_task = asyncio.create_task(self._run_auto_rebond(printer))

    async def _run_auto_rebond(self, printer: PairedPrinter) -> None:
        """Remove the BlueZ bond for ``printer`` (keeping selection) and force a fresh reconnect."""

        remove_bond = getattr(self._pairer, "remove_bluez_bond", None)
        if remove_bond is None:
            LOGGER.info("instantlink.auto_rebond unsupported_pairer")
            return
        try:
            await self._close_cached_printer_session()
            await remove_bond(printer)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("instantlink.auto_rebond_failed address=%s", printer.address)
            return
        LOGGER.info("instantlink.auto_rebond done=remove_bond address=%s", printer.address)
        # Force a fresh status cycle so the next connect re-bonds via the pairing agent. The poll
        # loop is left running; bumping the generation just resets pacing/backoff state.
        await self._schedule_printer_status_refresh()

    def _maybe_recover_silent_link(
        self,
        printer: PairedPrinter,
        exc: PrinterStatusUnavailableError,
    ) -> None:
        """Recover the "BlueZ holds a silent auto-reconnected link" deadlock.

        Only a not-found failure (the advertisement scan saw nothing) is eligible: that is exactly
        the case where BlueZ may be holding a connected-but-silent link. The actual disconnect runs
        fire-and-forget and is itself a no-op unless a connected link exists, so a genuinely off
        printer is unaffected. At most one recovery per device per cooldown window bounds churn.
        """

        if not exc.printer_not_found:
            return
        task = self._silent_link_recovery_task
        if task is not None and not task.done():
            return
        if getattr(self._pairer, "disconnect_bluez_link", None) is None:
            return

        key = _auto_rebond_key(printer)
        now = self._monotonic()
        last_at = self._last_silent_link_recovery_at.get(key)
        if last_at is not None and now - last_at < SILENT_LINK_RECOVERY_COOLDOWN_S:
            return

        self._last_silent_link_recovery_at[key] = now
        self._silent_link_recovery_task = asyncio.create_task(
            self._run_silent_link_recovery(printer)
        )

    async def _run_silent_link_recovery(self, printer: PairedPrinter) -> None:
        """Drop any connected-but-silent BlueZ link so the printer re-advertises."""

        disconnect_link = getattr(self._pairer, "disconnect_bluez_link", None)
        if disconnect_link is None:
            return
        try:
            disconnected = await disconnect_link(printer)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("instantlink.silent_link_recovery_failed address=%s", printer.address)
            return
        if not disconnected:
            # No connected link: the printer is genuinely off/absent, not deadlocked. Nothing to do.
            return
        LOGGER.warning(
            "instantlink.silent_link_recovery done=disconnect address=%s "
            "reason=connected_but_not_advertising",
            printer.address,
        )
        # Re-advertisement takes a moment; bump the generation so the next poll does a fresh scan
        # that can now see (and adopt) the printer.
        await self._schedule_printer_status_refresh()

    def _monotonic(self) -> float:
        return asyncio.get_running_loop().time()

    def _printer_status_fresh_ttl_s(self) -> float:
        """Return the readiness freshness TTL: a small multiple of the keepalive interval."""

        return max(PRINTER_STATUS_FRESH_TTL_S, 3.0 * self._printer_keepalive_interval_s)

    def _printer_status_is_fresh(self) -> bool:
        """Return whether a printer status succeeded within the freshness TTL.

        Never-proven state (``-inf``) is reported stale without consulting the clock so this is
        safe to call before the event loop is running (e.g. the BOOTING snapshot in ``__init__``).
        """

        if self._last_printer_status_ok_at == float("-inf"):
            return False
        age = self._monotonic() - self._last_printer_status_ok_at
        return age < self._printer_status_fresh_ttl_s()

    def _offline_backoff_delay(self) -> float:
        """Return an exponentially backed-off retry delay for an offline printer.

        Starts at ``OFFLINE_BACKOFF_BASE_S`` once the offline threshold is reached and doubles
        for each additional consecutive miss, capped at ``OFFLINE_BACKOFF_CAP_S`` so a missing
        printer is not rescanned every few seconds indefinitely.
        """

        extra_misses = self._printer_status_misses - OFFLINE_MESSAGE_AFTER_MISSES
        delay = OFFLINE_BACKOFF_BASE_S * (2.0**extra_misses)
        return min(delay, OFFLINE_BACKOFF_CAP_S)

    def _apply_printer_status(
        self,
        printer: PairedPrinter,
        status: PrinterStatusSnapshot,
    ) -> None:
        current = self._snapshot.paired_printer
        if current is None:
            return
        if current.address != printer.address or current.name != printer.name:
            return
        mode = self._snapshot.mode
        should_render = mode in STATUS_VISIBLE_MODES
        if mode in {
            UiMode.READY,
            UiMode.NO_FILM,
            UiMode.VALIDATION,
            UiMode.PRINTER_SEARCHING,
            UiMode.PRINTER_OFFLINE,
        }:
            if status.film_remaining is None:
                mode = UiMode.VALIDATION
            elif status.film_remaining <= 0:
                mode = (
                    UiMode.READY
                    if self._config.workflow.allow_print_without_film
                    else UiMode.NO_FILM
                )
            else:
                mode = UiMode.READY
        printer_model = status.model or self._known_printer_model()
        paired_printer = current
        if status.model is not None:
            paired_printer = self._save_detected_printer_model(current, status.model)
        self._snapshot = replace(
            self._snapshot,
            mode=mode,
            paired_printer=paired_printer,
            film_remaining=status.film_remaining,
            printer_battery=status.battery,
            printer_is_charging=status.is_charging,
            printer_battery_minutes_remaining=self._battery_minutes_remaining,
            printer_model=printer_model,
            printer_status_message=status.message,
            printer_status_fresh=True,
            allow_print_without_film=self._config.workflow.allow_print_without_film,
        )
        if self._snapshot.mode is UiMode.SETTINGS:
            self._snapshot = replace(self._snapshot, settings_rows=self._current_settings_rows())
        if should_render:
            self._render()

    def _save_detected_printer_model(
        self,
        printer: PairedPrinter,
        model: PrinterModel,
    ) -> PairedPrinter:
        current = self._snapshot.paired_printer
        target = (
            current if current is not None and _same_paired_printer(current, printer) else printer
        )
        if target.model == model:
            return target
        paired_printer = replace(target, model=model)
        try:
            self._pairer.save_selected(paired_printer)
        except Exception:
            LOGGER.exception("ui.printer_model_persist_failed")
        return paired_printer

    def _show_printer_searching_if_retrying(self, printer: PairedPrinter, message: str) -> None:
        if self._snapshot.mode not in {UiMode.PRINTER_SEARCHING, UiMode.PRINTER_OFFLINE}:
            return
        current = self._snapshot.printer_status_message
        # Keep the live "searching" copy refreshing between fetches, but never clobber a more
        # specific diagnostic the last fetch surfaced (e.g. "No printer signal", "Restart printer").
        # The generic placeholders are interchangeable so the screen always reflects active work.
        if current not in {None, message, *_GENERIC_SEARCHING_MESSAGES}:
            return
        self._apply_printer_searching(printer, message)

    def _apply_printer_searching(self, printer: PairedPrinter, message: str) -> None:
        current = self._snapshot.paired_printer
        if current is None:
            return
        if current.address != printer.address or current.name != printer.name:
            return
        mode = (
            UiMode.PRINTER_OFFLINE if message == "Hold K3 to re-pair" else UiMode.PRINTER_SEARCHING
        )
        if self._snapshot.mode is UiMode.SETTINGS:
            self._snapshot = replace(
                self._snapshot,
                printer_status_message=message,
                printer_status_fresh=False,
                film_remaining=None,
                printer_battery=None,
                printer_is_charging=None,
                printer_model=self._known_printer_model(),
            )
            self._snapshot = replace(self._snapshot, settings_rows=self._current_settings_rows())
            self._render()
            return
        if self._snapshot.mode not in STATUS_VISIBLE_MODES:
            self._snapshot = replace(
                self._snapshot,
                printer_status_message=message,
                printer_status_fresh=False,
                film_remaining=None,
                printer_battery=None,
                printer_is_charging=None,
                printer_model=self._known_printer_model(),
            )
            return
        self._snapshot = replace(
            self._snapshot,
            mode=mode,
            printer_status_message=message,
            printer_status_fresh=False,
            film_remaining=None,
            printer_battery=None,
            printer_is_charging=None,
            printer_model=self._known_printer_model(),
        )
        self._render()

    def _should_log_printer_status_warning(
        self,
        kind: str,
        printer: PairedPrinter,
        *details: str,
    ) -> bool:
        signature = (kind, printer.address, printer.name, *details)
        now = asyncio.get_running_loop().time()
        if (
            signature != self._last_printer_status_warning_signature
            or now - self._last_printer_status_warning_at >= PRINTER_STATUS_WARNING_INTERVAL_S
        ):
            self._last_printer_status_warning_signature = signature
            self._last_printer_status_warning_at = now
            return True
        return False

    def _clear_printer_status_warning_state(self) -> None:
        self._last_printer_status_warning_signature = None
        self._last_printer_status_warning_at = -math.inf

    def _apply_status_failure(
        self,
        printer: PairedPrinter,
        message: str,
        generation: int | None,
    ) -> None:
        """Apply a failure message unless the poll generation has been superseded."""

        if generation is not None and generation != self._status_generation:
            return
        self._apply_printer_searching(printer, message)

    def _unavailable_message(self, exc: PrinterStatusUnavailableError) -> str:
        """Return live "searching" copy for an unavailable printer.

        Always-auto-scanning means we keep the printer in ``PRINTER_SEARCHING`` and never flip to
        the manual re-pair screen on a transient miss. Only a genuinely absent or stale selected
        printer (per bridge policy, the one case re-pair is appropriate) escalates to the manual
        ``Hold K3 to re-pair`` affordance. A printer that is visible but failing to connect stays
        searching and, past the threshold, surfaces ``Restart printer`` recovery copy.
        """

        diagnostics = exc.diagnostics
        printer_seen = diagnostics is not None and diagnostics.selected_visible
        if printer_seen:
            if self._printer_status_misses >= OFFLINE_MESSAGE_AFTER_MISSES:
                return "Restart printer"
            return printer_unavailable_message(exc)
        if exc.stale_selected and self._printer_status_misses >= OFFLINE_MESSAGE_AFTER_MISSES:
            return "Hold K3 to re-pair"
        return printer_unavailable_message(exc)

    def _connect_failure_message(self, default: str) -> str:
        """Return live connect copy, escalating to recovery copy past the miss threshold.

        The miss counter is bumped by the caller so the threshold is single-sourced; this helper
        only maps the current miss count to a user-facing message and never routes to the manual
        re-pair screen.
        """

        if self._printer_status_misses >= OFFLINE_MESSAGE_AFTER_MISSES:
            return "Restart printer"
        return default

    async def _cancel_status_refresh(self) -> None:
        """Stop the in-flight status poll without blocking the caller.

        Cancellation is fire-and-forget: the poll task runs the BLE status/connect on a shielded,
        cancel-resistant worker that can take seconds to unwind. Awaiting it here would park the
        action loop (and therefore input/render). Instead we bump the status generation so any
        stale result the cancelled task may still produce is ignored, drop the task reference, and
        let the task tear down its own resources (its ``finally`` calls ``_status_provider.close``).
        """

        self._status_generation += 1
        task = self._status_task
        self._status_task = None
        if task is not None:
            task.cancel()

    async def _close_cached_printer_session(self) -> None:
        # A dropped/closed BLE session means the next status is a fresh connect; discard the drain
        # history so a stale pre-disconnect trend cannot pollute the next estimate.
        self._battery_estimator.reset()
        self._battery_minutes_remaining = None
        # A closed session means the next status is a fresh connect; readiness must not survive it.
        self._last_printer_status_ok_at = float("-inf")
        close_cached_session = getattr(self._status_provider, "close_cached_session", None)
        if close_cached_session is None:
            return
        await close_cached_session()

    async def _run_render_tick(self) -> None:
        """Periodically re-render the latest snapshot.

        The action loop and background coroutines mutate ``self._snapshot`` and call
        ``_render`` directly, but a busy coroutine could otherwise leave the LCD showing a stale
        frame. This tick guarantees the screen converges on the latest snapshot. ``_render`` keeps
        the ``snapshot == last_rendered`` short-circuit, so this is cheap and never render-spams.
        """

        while True:
            await asyncio.sleep(RENDER_TICK_S)
            # Age out readiness: if no status succeeded within the TTL, downgrade the cached
            # snapshot so a stale "Ready to print" leaves the screen even when no explicit failure
            # arrived (e.g. a dropped PRINTER_SEARCHING transition). The snapshot short-circuit in
            # _render still suppresses re-rendering an unchanged frame.
            fresh = self._printer_status_is_fresh()
            if fresh != self._snapshot.printer_status_fresh:
                self._snapshot = replace(self._snapshot, printer_status_fresh=fresh)
            self._render()

    async def _run_network_status(self) -> None:
        while True:
            await asyncio.sleep(USB_STATUS_POLL_S)
            await self._refresh_network_status()

    async def _refresh_network_status(self) -> None:
        async with self._network_refresh_lock:
            ftp_activity = self._ftp_activity.snapshot() if self._ftp_activity is not None else None
            try:
                health = await asyncio.to_thread(
                    detect_camera_link_health,
                    expected_usb_ipv4=self._config.ftp.host,
                    expected_hotspot_ipv4=self._config.ftp.hotspot_host,
                    ftp_activity=ftp_activity,
                )
            except Exception:
                LOGGER.exception("ui.network_status_detect_failed")
                return
            wifi_host = health.home_wifi_ipv4
            hotspot_host = health.hotspot_ipv4
            usb_connected = health.usb_carrier
            ftp_mode = self._config.ftp.mode
            camera_receive_ready = ftp_receive_mode_ready_for_health(health, ftp_mode)
            recent_source_ready = _recent_ftp_source_ready_for_health(health, ftp_mode)
            camera_connected = (
                ftp_mode is FtpReceiveMode.WIRED and health.camera_lease_active
            ) or recent_source_ready
            camera_status_message = camera_status_message_for_health(health, ftp_mode)
            camera_transport_message = camera_transport_message_for_health(health, ftp_mode)
            if (
                wifi_host == self._wifi_host
                and hotspot_host == self._hotspot_host
                and usb_connected == self._usb_connected
                and camera_receive_ready == self._camera_receive_ready
                and camera_connected == self._camera_connected
                and camera_status_message == self._camera_status_message
                and camera_transport_message == self._camera_transport_message
            ):
                return
            self._wifi_host = wifi_host
            self._hotspot_host = hotspot_host
            self._usb_connected = usb_connected
            self._camera_receive_ready = camera_receive_ready
            self._camera_connected = camera_connected
            self._camera_status_message = camera_status_message
            self._camera_transport_message = camera_transport_message
            self._snapshot = replace(
                self._snapshot,
                ftp_receive_mode=self._config.ftp.mode.value,
                wifi_host=wifi_host,
                hotspot_host=hotspot_host,
                usb_connected=usb_connected,
                camera_receive_ready=camera_receive_ready,
                camera_connected=camera_connected,
                camera_status_message=camera_status_message,
                camera_transport_message=camera_transport_message,
            )
            if self._snapshot.mode is UiMode.SETTINGS:
                self._snapshot = replace(
                    self._snapshot, settings_rows=self._current_settings_rows()
                )
            LOGGER.info(
                "ui.network_status home_wifi_host=%s hotspot_host=%s usb_carrier=%s "
                "usb_configured=%s camera_lease=%s ftp_recent=%s recent_source_ready=%s "
                "receive_ready=%s preferred_wifi_host=%s wifi_preferred=%s",
                wifi_host or "none",
                hotspot_host or "none",
                usb_connected,
                health.usb_configured,
                health.camera_lease.ipv4 if health.camera_lease is not None else "none",
                health.ftp_recently_active_for_mode(ftp_mode),
                recent_source_ready,
                camera_receive_ready,
                self._config.ftp.preferred_wifi_host or "none",
                (
                    self._config.ftp.preferred_wifi_host is None
                    or wifi_host == self._config.ftp.preferred_wifi_host
                ),
            )
            if self._snapshot.mode in STATUS_VISIBLE_MODES:
                self._render()

    def _render(self) -> None:
        try:
            if self._snapshot == self._last_rendered_snapshot:
                return
            self._display.render(self._snapshot)
            self._last_rendered_snapshot = self._snapshot
        except Exception:
            LOGGER.exception("ui.render_failed mode=%s", self._snapshot.mode)

    def _set_display_idle_stage(self, stage: IdleStage) -> None:
        set_idle_stage = getattr(self._display, "set_idle_stage", None)
        if set_idle_stage is None:
            return
        try:
            set_idle_stage(stage.value)
        except Exception:
            LOGGER.exception("ui.display_idle_stage_failed stage=%s", stage.value)

    def _power_summary_value(self) -> str:
        if self._bridge_power_status is not None:
            return self._bridge_power_status
        return self._bridge_power_model or power_backend_label(self._config.power.backend)

    def _battery_power_value(self) -> str:
        if self._bridge_battery_percent is not None:
            suffix = " plugged" if self._bridge_external_power else ""
            return f"{self._bridge_battery_percent}%{suffix}"
        if self._config.power.backend.value == "x306":
            return "LED only"
        return self._bridge_power_alert

    def _idle_power_value(self) -> str:
        if not self._config.power.idle_poweroff_enabled:
            return f"{self._idle_stage.value} no-off"
        return f"{self._idle_stage.value} {self._config.power.idle_poweroff_after_s:g}s"


def ftp_receive_mode_ready_for_health(
    health: ConnectionHealth,
    mode: FtpReceiveMode = FtpReceiveMode.AUTO,
) -> bool:
    """Return whether the selected receive mode has an addressable FTP path."""

    if mode is FtpReceiveMode.WIRED:
        return False
    if mode is FtpReceiveMode.HOTSPOT:
        return health.hotspot_ftp_ready
    if mode is FtpReceiveMode.PEER:
        return health.home_wifi_ftp_ready
    return health.hotspot_ftp_ready or health.home_wifi_ftp_ready


def camera_status_message_for_health(
    health: ConnectionHealth,
    mode: FtpReceiveMode = FtpReceiveMode.AUTO,
) -> str:
    """Return a concise user-facing FTP receive-path status."""

    if mode is FtpReceiveMode.WIRED:
        if health.wired_ftp_ready:
            return "USB debug only"
        if health.usb_carrier and not health.usb_configured:
            return "USB debug no IP"
        if health.usb_carrier:
            return "USB debug connected"
        return "USB debug off"
    if mode is FtpReceiveMode.HOTSPOT:
        if health.hotspot_ftp_ready:
            return "Bridge Wi-Fi ready"
        return "Bridge Wi-Fi off"
    if mode is FtpReceiveMode.PEER:
        if health.ftp_recently_active_for_mode(mode) and health.ftp_activity is not None:
            return f"FTP active {health.ftp_activity.last_remote_ip or 'client'}"
        if health.home_wifi_ftp_ready:
            return "Same Wi-Fi adv ready"
        if health.wifi_subnet_conflict:
            return "Same-Wi-Fi subnet conflict"
        return "Same Wi-Fi adv off"
    if _recent_ftp_source_ready_for_health(health, mode):
        return _ftp_active_message_for_health(health)
    if health.ftp_recently_active_for_mode(mode) and health.ftp_activity is not None:
        return _ftp_active_message_for_health(health)
    if health.hotspot_ftp_ready:
        return "Bridge Wi-Fi ready"
    if health.home_wifi_ftp_ready:
        return "Same Wi-Fi adv ready"
    if health.wifi_subnet_conflict:
        return "Same-Wi-Fi subnet conflict"
    return "No FTP Wi-Fi"


def camera_transport_message_for_health(
    health: ConnectionHealth,
    mode: FtpReceiveMode = FtpReceiveMode.AUTO,
) -> str:
    """Return the transport line used on the LCD."""

    if mode is FtpReceiveMode.WIRED:
        if health.wired_ftp_ready:
            return f"USB debug {health.expected_usb_ipv4}"
        if health.usb_carrier and not health.usb_configured:
            return "USB debug no IP"
        if health.usb_carrier:
            return "USB debug connected"
        return "USB debug off"
    if mode is FtpReceiveMode.HOTSPOT:
        if health.hotspot_ftp_ready and health.hotspot_ipv4 is not None:
            return f"Bridge FTP {health.hotspot_ipv4}"
        return f"Bridge Wi-Fi off {health.expected_hotspot_ipv4}"
    if mode is FtpReceiveMode.PEER:
        if health.home_wifi_ftp_ready and health.home_wifi_ipv4 is not None:
            return f"Same Wi-Fi adv {health.home_wifi_ipv4}"
        if health.wifi_subnet_conflict:
            return "Same-Wi-Fi subnet conflict"
        return "Same Wi-Fi adv off"
    recent_transport = _recent_ftp_transport_message_for_health(health, mode)
    if recent_transport is not None:
        return recent_transport
    if health.hotspot_ftp_ready and health.hotspot_ipv4 is not None:
        return f"Bridge FTP {health.hotspot_ipv4}"
    if health.home_wifi_ftp_ready and health.home_wifi_ipv4 is not None:
        return f"Same Wi-Fi adv {health.home_wifi_ipv4}"
    if health.wifi_subnet_conflict:
        return "Same-Wi-Fi subnet conflict"
    return "No FTP Wi-Fi"


def _ftp_active_message_for_health(health: ConnectionHealth) -> str:
    if health.ftp_activity is None:
        return "FTP active client"
    return f"FTP active {health.ftp_activity.last_remote_ip or 'client'}"


def _recent_ftp_source_ready_for_health(
    health: ConnectionHealth,
    mode: FtpReceiveMode,
) -> bool:
    source = health.recent_ftp_source_for_mode(mode)
    if source is FtpSourceKind.USB and mode is not FtpReceiveMode.WIRED:
        return False
    return source is not None and health.ftp_source_ready(source)


def _recent_ftp_transport_message_for_health(
    health: ConnectionHealth,
    mode: FtpReceiveMode,
) -> str | None:
    source = health.recent_ftp_source_for_mode(mode)
    if source is None or not health.ftp_source_ready(source):
        return None
    if source is FtpSourceKind.USB:
        if mode is FtpReceiveMode.WIRED:
            return f"USB debug {health.expected_usb_ipv4}"
        return None
    if source is FtpSourceKind.HOTSPOT and health.hotspot_ipv4 is not None:
        return f"Bridge FTP {health.hotspot_ipv4}"
    if source is FtpSourceKind.PEER and health.home_wifi_ipv4 is not None:
        return f"Same Wi-Fi adv {health.home_wifi_ipv4}"
    return None


def printer_unavailable_message(error: PrinterStatusUnavailableError) -> str:
    """Return an LCD-safe message from BLE scanner diagnostics."""

    if error.status_message is not None:
        return error.status_message
    diagnostics = error.diagnostics
    if diagnostics is None:
        return "Scanning for printer"
    if diagnostics.selected_visible:
        return "Printer seen; connecting"
    if diagnostics.candidate_count == 0:
        return "No printer signal"
    if diagnostics.candidate_count == 1:
        return "Saw other Instax"
    return f"Saw {diagnostics.candidate_count} Instax"


def bridge_power_status_text(
    state: BatteryState,
    alert: BatteryAlert | None,
) -> str:
    """Return a compact bridge power status for LCD settings."""

    if state.available and state.percentage is not None:
        suffix = " plugged" if state.external_power else ""
        return f"Bridge {round(state.percentage)}%{suffix}"
    if state.model is not None and "X306" in state.model:
        return "Battery case"
    if alert is BatteryAlert.UNAVAILABLE:
        return "No telemetry"
    if alert is BatteryAlert.UNKNOWN:
        return "Battery unknown"
    return "Power monitor"


def power_backend_label(backend: PowerBackend) -> str:
    """Return a product-safe label for the configured bridge power backend."""

    if backend is PowerBackend.X306:
        return "Battery case"
    if backend is PowerBackend.PISUGAR:
        return "PiSugar"
    return "No battery"


def scanner_diagnostics_summary(error: PrinterStatusUnavailableError) -> str:
    """Format BLE scanner diagnostics for logs."""

    diagnostics = error.diagnostics
    if diagnostics is None:
        return "none"
    names = ",".join(diagnostics.candidate_names) if diagnostics.candidate_names else "none"
    return (
        f"selected_visible={diagnostics.selected_visible} "
        f"candidate_count={diagnostics.candidate_count} candidates={names}"
    )


def _info_message_for_setting(key: SettingKey) -> str:
    if key is SettingKey.CAMERA_SETUP_INFO:
        return "Use these FTP settings"
    if key is SettingKey.FTP_RECEIVE_MODE:
        return "Choose Bridge or Same-Wi-Fi FTP"
    if key is SettingKey.FTP_MODE_INFO:
        return "Bridge Wi-Fi is primary"
    if key is SettingKey.FTP_HOST_INFO:
        return "FTP host"
    if key is SettingKey.FTP_USERNAME_INFO:
        return "FTP username"
    if key is SettingKey.FTP_PASSWORD_INFO:
        return "FTP password"
    if key is SettingKey.NETWORK_ETHERNET_INFO:
        return "USB debug SSH/update link"
    if key is SettingKey.NETWORK_WIFI_INFO:
        return "Advanced Same-Wi-Fi status"
    if key is SettingKey.NETWORK_HOTSPOT_INFO:
        return "Bridge Wi-Fi FTP"
    if key is SettingKey.NETWORK_HOTSPOT_SSID_INFO:
        return "Bridge Wi-Fi name"
    if key is SettingKey.NETWORK_HOTSPOT_PASSWORD_INFO:
        return "Bridge Wi-Fi PIN"
    if key is SettingKey.NETWORK_BLUETOOTH_INFO:
        return "Printer Bluetooth"
    if key is SettingKey.SYSTEM_POWER_INFO:
        return "Bridge power hardware"
    if key is SettingKey.SYSTEM_BATTERY_INFO:
        return "Bridge battery telemetry"
    if key is SettingKey.SYSTEM_IDLE_INFO:
        return "Idle dim and poweroff"
    if key is SettingKey.SYSTEM_IDLE_POWEROFF:
        return "Allow 10 min idle shutdown"
    return "Info only"


def _ftp_mode_switching_message(mode: FtpReceiveMode) -> str:
    if mode is FtpReceiveMode.HOTSPOT:
        return "Starting bridge Wi-Fi"
    if mode is FtpReceiveMode.PEER:
        return "Joining saved Wi-Fi"
    if mode is FtpReceiveMode.WIRED:
        return "USB debug unchanged"
    return "Selecting Wi-Fi"


def _ftp_mode_failed_message(mode: FtpReceiveMode) -> str:
    if mode is FtpReceiveMode.HOTSPOT:
        return "Bridge Wi-Fi failed"
    if mode is FtpReceiveMode.PEER:
        return "Wi-Fi join failed"
    if mode is FtpReceiveMode.WIRED:
        return "USB debug unchanged"
    return "Connection failed"


def _ftp_mode_saved_message(mode: FtpReceiveMode, label: str) -> str:
    if mode is FtpReceiveMode.HOTSPOT:
        return "Bridge Wi-Fi ready"
    if mode is FtpReceiveMode.PEER:
        return "Same Wi-Fi adv selected"
    if mode is FtpReceiveMode.WIRED:
        return "USB debug selected"
    return f"FTP {label}"


def _camera_setup_info_message(mode: FtpReceiveMode) -> str:
    if mode is FtpReceiveMode.HOTSPOT:
        return "Sender joins Bridge Wi-Fi"
    if mode is FtpReceiveMode.WIRED:
        return "USB is debug/update only"
    if mode is FtpReceiveMode.PEER:
        return "Sender uses saved Wi-Fi"
    return "Use a Wi-Fi FTP profile"


def _auto_rebond_key(printer: PairedPrinter) -> str:
    """Return a stable per-device key for auto-rebond cooldown tracking."""

    return normalize_instax_name(printer.name).casefold() or printer.address.upper()


def _same_paired_printer(left: PairedPrinter, right: PairedPrinter) -> bool:
    if left.address.upper() == right.address.upper():
        return True
    left_name = normalize_instax_name(left.name).casefold()
    right_name = normalize_instax_name(right.name).casefold()
    return bool(left_name and right_name and left_name == right_name)


def _next_preview_tool(tool: PreviewTool) -> PreviewTool:
    index = PREVIEW_TOOLS.index(tool)
    return PREVIEW_TOOLS[(index + 1) % len(PREVIEW_TOOLS)]


def _adjust_preview_edit(edit: PrintEdit, tool: PreviewTool, action: UiAction) -> PrintEdit:
    if tool == "zoom":
        step = 0.25
        if action in {UiAction.UP, UiAction.RIGHT}:
            return replace(edit, zoom=min(3.0, edit.zoom + step))
        if action in {UiAction.DOWN, UiAction.LEFT}:
            return replace(edit, zoom=max(1.0, edit.zoom - step), offset_x=0.0, offset_y=0.0)
        return edit
    if tool == "crop":
        zoom = max(1.25, edit.zoom)
        step = 0.2
        if action is UiAction.LEFT:
            return replace(edit, zoom=zoom, offset_x=max(-1.0, edit.offset_x - step))
        if action is UiAction.RIGHT:
            return replace(edit, zoom=zoom, offset_x=min(1.0, edit.offset_x + step))
        if action is UiAction.UP:
            return replace(edit, zoom=zoom, offset_y=max(-1.0, edit.offset_y - step))
        if action is UiAction.DOWN:
            return replace(edit, zoom=zoom, offset_y=min(1.0, edit.offset_y + step))
        return edit
    if action is UiAction.LEFT:
        return replace(edit, rotate_degrees=(edit.rotate_degrees - 90) % 360)
    if action is UiAction.RIGHT:
        return replace(edit, rotate_degrees=(edit.rotate_degrees + 90) % 360)
    return edit


def _wifi_mode_for_ftp_receive_mode(mode: FtpReceiveMode) -> WifiMode | None:
    if mode is FtpReceiveMode.WIRED:
        return None
    if mode is FtpReceiveMode.HOTSPOT:
        return WifiMode.HOTSPOT
    if mode is FtpReceiveMode.PEER:
        return WifiMode.HOME
    return None


def _hotspot_ssid_file() -> Path:
    return Path(
        os.environ.get(
            "INSTANTLINK_BRIDGE_HOTSPOT_SSID_FILE",
            "/etc/InstantLinkBridge/hotspot.ssid",
        )
    )


def _hotspot_psk_file() -> Path:
    return Path(
        os.environ.get(
            "INSTANTLINK_BRIDGE_HOTSPOT_PSK_FILE",
            "/etc/InstantLinkBridge/hotspot.psk",
        )
    )


def _read_first_line(path: Path) -> str | None:
    try:
        value = path.read_text(encoding="utf-8").splitlines()[0].strip()
    except (OSError, IndexError):
        return None
    return value or None


async def set_wifi_mode_with_helper(mode: WifiMode) -> str:
    """Switch Wi-Fi mode through the provisioned root helper."""

    args = {
        WifiMode.HOTSPOT: "hotspot",
        WifiMode.HOME: "home-saved",
        WifiMode.OFF: "off",
    }
    command = ("sudo", "-n", str(WIFI_MODE_HELPER), args[mode])
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        output_bytes, _ = await asyncio.wait_for(process.communicate(), timeout=30.0)
    except TimeoutError as exc:
        with suppress(ProcessLookupError):
            process.kill()
        with suppress(Exception):
            await process.communicate()
        raise TimeoutError("wifi helper timed out") from exc
    output = output_bytes.decode("utf-8", errors="replace") if output_bytes else ""
    if process.returncode != 0:
        raise RuntimeError(output.strip() or f"wifi helper exited {process.returncode}")
    return output
