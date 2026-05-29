"""GPIO input bindings for the Waveshare LCD HAT."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import suppress
from typing import Protocol, cast

from instantlink_bridge.config import UiSurface
from instantlink_bridge.ui.models import UiAction

LOGGER = logging.getLogger(__name__)

JOYSTICK_UP = 6
JOYSTICK_DOWN = 19
JOYSTICK_LEFT = 5
JOYSTICK_RIGHT = 26
JOYSTICK_PRESS = 13
KEY1 = 21
KEY2 = 20
KEY3 = 16


class _ButtonDevice(Protocol):
    when_pressed: Callable[[], None] | None
    when_released: Callable[[], None] | None
    when_held: Callable[[], None] | None

    def close(self) -> None:
        """Release the GPIO pin."""


class NullInput:
    """No-op input for local development."""

    def start(
        self,
        queue: asyncio.Queue[UiAction],
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        return

    def close(self) -> None:
        return


class GpioUiInput:
    """Map joystick and KEY1-KEY3 to UI actions."""

    def __init__(self) -> None:
        self._buttons: list[_ButtonDevice] = []

    def start(
        self,
        queue: asyncio.Queue[UiAction],
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        from gpiozero import Button, Device
        from gpiozero.pins.lgpio import LGPIOFactory

        if not isinstance(Device.pin_factory, LGPIOFactory):
            Device.pin_factory = LGPIOFactory()

        action_pins = {
            JOYSTICK_UP: UiAction.UP,
            JOYSTICK_DOWN: UiAction.DOWN,
            JOYSTICK_LEFT: UiAction.LEFT,
            JOYSTICK_RIGHT: UiAction.RIGHT,
            JOYSTICK_PRESS: UiAction.SELECT,
            KEY1: UiAction.SELECT,
            KEY2: UiAction.BACK,
        }
        for pin, action in action_pins.items():
            button = cast(_ButtonDevice, Button(pin, pull_up=True, bounce_time=0.05))
            button.when_pressed = _enqueue(queue, loop, action)
            self._buttons.append(button)

        pair_button = cast(
            _ButtonDevice,
            Button(KEY3, pull_up=True, bounce_time=0.05, hold_time=1.2),
        )
        pair_held = False

        def hold_pair() -> None:
            nonlocal pair_held
            pair_held = True
            _enqueue(queue, loop, UiAction.PAIR)()

        def release_help() -> None:
            nonlocal pair_held
            if pair_held:
                pair_held = False
                return
            _enqueue(queue, loop, UiAction.HELP)()

        pair_button.when_held = hold_pair
        pair_button.when_released = release_help
        self._buttons.append(pair_button)

    def close(self) -> None:
        for button in self._buttons:
            with suppress(Exception):
                button.close()
        self._buttons.clear()


def create_input(surface: UiSurface | None = None) -> GpioUiInput | NullInput:
    """Create GPIO input, falling back to no-op input if unavailable.

    When *surface* is ``UiSurface.HEADLESS`` the GPIO probe is skipped entirely
    and a ``NullInput`` is returned immediately.
    """

    if surface is UiSurface.HEADLESS:
        return NullInput()
    try:
        gpio_input = GpioUiInput()
        return gpio_input
    except Exception:
        LOGGER.exception("ui.input_unavailable")
        return NullInput()


def _enqueue(
    queue: asyncio.Queue[UiAction],
    loop: asyncio.AbstractEventLoop,
    action: UiAction,
) -> Callable[[], None]:
    def callback() -> None:
        LOGGER.debug("ui.input_press action=%s", action)

        def put_action() -> None:
            with suppress(asyncio.QueueFull):
                queue.put_nowait(action)

        loop.call_soon_threadsafe(put_action)

    return callback
