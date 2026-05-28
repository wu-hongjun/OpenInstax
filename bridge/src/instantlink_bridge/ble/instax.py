"""High-level Instax protocol client.

This is the Python porting target for InstantLink's protocol flow. It is transport-agnostic:
the concrete BLE layer supplies `send`, `receive`, and `send_and_receive`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from instantlink_bridge.ble import commands
from instantlink_bridge.ble.models import PrinterModel, detect_model, is_success_status, spec_for
from instantlink_bridge.ble.protocol import Packet

# Defer importing `instantlink_bridge.imaging.pipeline` (transitively pulls Pillow / pillow-heif)
# out of module-load — the bridge only needs it at print time, not at startup. See docs/plans/032
# (Q2). `PreparedImage` is annotation-only thanks to `from __future__ import annotations` above;
# `chunk_image_data` is imported lazily inside `print_prepared`.
if TYPE_CHECKING:
    from instantlink_bridge.imaging.pipeline import PreparedImage

DEFAULT_TIMEOUT_S = 10.0
DOWNLOAD_CANCEL_TIMEOUT_S = 3.0
PRINT_START_LED = (248, 120, 67)
LED_PATTERN_SOLID = 0
LOGGER = logging.getLogger(__name__)


class PrinterError(RuntimeError):
    """Base printer protocol error."""


class UnexpectedResponseError(PrinterError):
    """Raised when a printer response does not match the command."""


class PrintRejectedError(PrinterError):
    """Raised when the printer rejects a print operation."""


class NoFilmError(PrintRejectedError):
    """Raised when no film remains."""


class CoverOpenError(PrintRejectedError):
    """Raised when the printer cover is open."""


class PrinterBusyError(PrintRejectedError):
    """Raised when the printer is busy."""


class LowPrinterBatteryError(PrintRejectedError):
    """Raised when the printer battery is too low."""


class InstaxTransport(Protocol):
    """Minimal transport needed by the protocol client."""

    async def send(self, data: bytes) -> None:
        """Send raw protocol bytes."""

    async def receive(self, timeout_s: float = DEFAULT_TIMEOUT_S) -> Packet:
        """Receive the next parsed protocol packet."""

    async def send_and_receive(self, data: bytes, timeout_s: float = DEFAULT_TIMEOUT_S) -> Packet:
        """Send bytes and receive one parsed response."""

    def model_number_hint(self) -> str | None:
        """Return optional DIS Model Number string."""


@dataclass(frozen=True, slots=True)
class PrinterStatus:
    """Printer status summary."""

    battery: int
    is_charging: bool
    film_remaining: int
    print_count: int | None
    model: PrinterModel
    name: str


@dataclass(frozen=True, slots=True)
class InstaxPrintPlan:
    """Prepared command data for one print."""

    jpeg_data: bytes
    chunks: list[bytes]
    model: PrinterModel
    print_option: int = 0


class InstaxProtocolClient:
    """Model-aware Instax Link protocol client."""

    def __init__(self, transport: InstaxTransport, name: str, model: PrinterModel) -> None:
        self._transport = transport
        self._name = name
        self._model = model
        self._operation_lock = asyncio.Lock()

    @property
    def model(self) -> PrinterModel:
        """Detected printer model."""

        return self._model

    @property
    def name(self) -> str:
        """BLE device name."""

        return self._name

    @classmethod
    async def create(
        cls,
        transport: InstaxTransport,
        name: str,
        *,
        model_override: PrinterModel | None = None,
    ) -> InstaxProtocolClient:
        """Create a protocol client and detect the printer model."""

        packet = await transport.send_and_receive(commands.image_support_info())
        response = commands.decode_response(packet)
        if response.kind != commands.ResponseKind.IMAGE_SUPPORT_INFO:
            raise UnexpectedResponseError("expected ImageSupportInfo response")
        if response.width is None or response.height is None:
            raise UnexpectedResponseError("image support response missing dimensions")
        detected_model = detect_model(
            response.width, response.height, transport.model_number_hint()
        )
        model = _compatible_model_override(detected_model, model_override)
        return cls(transport=transport, name=name, model=model)

    async def status(self) -> PrinterStatus:
        """Fetch current printer status."""

        async with self._operation_lock:
            battery = await self._battery_unlocked()
            film_remaining, is_charging = await self._film_and_charging_unlocked()
        return PrinterStatus(
            battery=battery,
            is_charging=is_charging,
            film_remaining=film_remaining,
            print_count=None,
            model=self._model,
            name=self._name,
        )

    async def print_prepared(
        self,
        prepared: PreparedImage,
        print_option: int = 0,
        progress: ProgressCallback | None = None,
    ) -> None:
        """Print a prepared model-matched image."""

        # Lazy-imported here so the bridge entrypoint does not pull Pillow/pillow-heif at startup
        # (only when an image is actually being printed). See docs/plans/032 (Q2).
        from instantlink_bridge.imaging.pipeline import chunk_image_data

        if prepared.model != self._model:
            raise ValueError(f"prepared image is for {prepared.model}, printer is {self._model}")
        plan = InstaxPrintPlan(
            jpeg_data=prepared.data,
            chunks=chunk_image_data(prepared.data, self._model),
            model=self._model,
            print_option=print_option,
        )
        await self.print_plan(plan, progress=progress)

    async def print_plan(
        self,
        plan: InstaxPrintPlan,
        progress: ProgressCallback | None = None,
    ) -> None:
        """Send a prepared print plan to the printer."""

        if plan.model != self._model:
            raise ValueError(f"print plan is for {plan.model}, printer is {self._model}")
        started_at = time.perf_counter()
        LOGGER.info(
            "instax.print_plan_start model=%s bytes=%s chunks=%s print_option=%s",
            plan.model.value,
            len(plan.jpeg_data),
            len(plan.chunks),
            plan.print_option,
        )
        async with self._operation_lock:
            await self._send_image_data(plan, progress=progress)
            await self._signal_print_handoff_unlocked()
            pre_delay = spec_for(self._model).pre_execute_delay_ms / 1000
            if pre_delay > 0:
                await asyncio.sleep(pre_delay)
            response = await self._command_unlocked(commands.print_image())
            if response.kind != commands.ResponseKind.PRINT_STATUS or response.status is None:
                raise UnexpectedResponseError("expected PrintStatus response")
            self._check_status(response.status, "print")
        LOGGER.info(
            "instax.print_plan_complete model=%s elapsed_s=%.3f",
            plan.model.value,
            time.perf_counter() - started_at,
        )

    async def set_led(self, red: int, green: int, blue: int, pattern: int) -> None:
        """Set printer LED color/pattern."""

        async with self._operation_lock:
            response = await self._command_unlocked(commands.led_pattern(red, green, blue, pattern))
            if response.kind != commands.ResponseKind.LED_ACK:
                raise UnexpectedResponseError("expected LedAck response")

    async def shutdown(self) -> None:
        """Send printer shutdown command; no response is expected."""

        async with self._operation_lock:
            await self._transport.send(commands.shutdown())

    async def reset(self) -> None:
        """Send printer reset command; no response is expected."""

        async with self._operation_lock:
            await self._transport.send(commands.reset())

    async def _command_unlocked(self, command: bytes) -> commands.DecodedResponse:
        packet = await self._transport.send_and_receive(command)
        return commands.decode_response(packet)

    async def _battery_unlocked(self) -> int:
        response = await self._command_unlocked(commands.battery_status())
        if response.kind != commands.ResponseKind.BATTERY_STATUS or response.battery_level is None:
            raise UnexpectedResponseError("expected BatteryStatus response")
        return response.battery_level

    async def _film_and_charging_unlocked(self) -> tuple[int, bool]:
        response = await self._command_unlocked(commands.printer_function_info())
        if (
            response.kind != commands.ResponseKind.PRINTER_FUNCTION_INFO
            or response.film_remaining is None
            or response.is_charging is None
        ):
            raise UnexpectedResponseError("expected PrinterFunctionInfo response")
        return response.film_remaining, response.is_charging

    async def _print_count_unlocked(self) -> int:
        response = await self._command_unlocked(commands.history_info())
        if response.kind != commands.ResponseKind.HISTORY_INFO or response.print_count is None:
            raise UnexpectedResponseError("expected HistoryInfo response")
        return response.print_count

    async def _send_image_data(
        self,
        plan: InstaxPrintPlan,
        progress: ProgressCallback | None,
    ) -> None:
        started_at = time.perf_counter()
        start = await self._command_unlocked(
            commands.download_start(len(plan.jpeg_data), plan.print_option)
        )
        if start.kind != commands.ResponseKind.DOWNLOAD_ACK or start.status is None:
            raise UnexpectedResponseError("expected DownloadAck for DownloadStart")
        self._check_status(start.status, "download start")

        try:
            total = len(plan.chunks)
            delay_s = spec_for(self._model).packet_delay_ms / 1000
            for index, chunk in enumerate(plan.chunks):
                response = await self._command_unlocked(commands.data_chunk(index, chunk))
                if response.kind != commands.ResponseKind.DOWNLOAD_ACK or response.status is None:
                    raise UnexpectedResponseError("expected DownloadAck for Data")
                self._check_status(response.status, f"data chunk {index}")
                if progress is not None:
                    progress(index + 1, total)
                if delay_s > 0:
                    await asyncio.sleep(delay_s)

            end = await self._command_unlocked(commands.download_end())
            if end.kind != commands.ResponseKind.DOWNLOAD_ACK or end.status is None:
                raise UnexpectedResponseError("expected DownloadAck for DownloadEnd")
            self._check_status(end.status, "download end")
            LOGGER.info(
                "instax.image_transfer_complete model=%s chunks=%s bytes=%s elapsed_s=%.3f",
                plan.model.value,
                len(plan.chunks),
                len(plan.jpeg_data),
                time.perf_counter() - started_at,
            )
        except asyncio.CancelledError:
            await self._cancel_download_unlocked()
            raise
        except Exception:
            await self._cancel_download_unlocked()
            raise

    async def _cancel_download_unlocked(self) -> None:
        try:
            await asyncio.wait_for(
                asyncio.shield(self._transport.send(commands.download_cancel())),
                timeout=DOWNLOAD_CANCEL_TIMEOUT_S,
            )
        except Exception:
            LOGGER.warning("instax.download_cancel_failed", exc_info=True)

    async def _signal_print_handoff_unlocked(self) -> None:
        try:
            red, green, blue = PRINT_START_LED
            await self._command_unlocked(commands.led_pattern(red, green, blue, LED_PATTERN_SOLID))
        except PrinterError:
            raise
        except Exception:
            # LED handoff is best-effort; printing should continue.
            return

    def _check_status(self, status: int, context: str) -> None:
        if is_success_status(self._model, status):
            return
        match status:
            case 178:
                raise NoFilmError("no film remaining")
            case 179:
                raise CoverOpenError("printer cover is open")
            case 180:
                raise LowPrinterBatteryError("printer battery too low")
            case 181:
                raise PrinterBusyError("printer is busy")
            case _:
                raise PrintRejectedError(f"{context} rejected with status {status}")


ProgressCallback = Callable[[int, int], None]


def _compatible_model_override(
    detected_model: PrinterModel,
    model_override: PrinterModel | None,
) -> PrinterModel:
    if model_override is None or model_override is detected_model:
        return detected_model
    detected_spec = spec_for(detected_model)
    override_spec = spec_for(model_override)
    if (override_spec.width, override_spec.height) == (detected_spec.width, detected_spec.height):
        LOGGER.info(
            "instax.model_override detected=%s override=%s",
            detected_model.value,
            model_override.value,
        )
        return model_override
    LOGGER.warning(
        "instax.model_override_ignored detected=%s override=%s",
        detected_model.value,
        model_override.value,
    )
    return detected_model
