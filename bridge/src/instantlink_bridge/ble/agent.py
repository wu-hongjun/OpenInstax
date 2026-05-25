"""BlueZ pairing agent for headless Instax printer connections."""

# mypy: ignore-errors
# ruff: noqa: ANN201, F821, UP037

from __future__ import annotations

import logging
from dataclasses import dataclass

from dbus_fast import BusType, Variant
from dbus_fast.aio import MessageBus
from dbus_fast.aio.message_bus import MessageBus as AioMessageBus
from dbus_fast.proxy_object import BaseProxyInterface
from dbus_fast.service import ServiceInterface, method

LOGGER = logging.getLogger(__name__)

BLUEZ_SERVICE = "org.bluez"
BLUEZ_ROOT = "/org/bluez"
DEFAULT_ADAPTER_PATH = "/org/bluez/hci0"
DEFAULT_AGENT_PATH = "/com/instantlink_bridge/agent"


class NoInputNoOutputAgent(ServiceInterface):
    """Minimal BlueZ Agent1 implementation for Just-Works BLE bonding."""

    def __init__(self) -> None:
        super().__init__("org.bluez.Agent1")

    @method()
    def Release(self):
        """BlueZ released the agent."""

        LOGGER.info("bluetooth.agent_released")

    @method()
    def RequestPinCode(self, device: "o") -> "s":
        """Reject legacy PIN pairing; Instax Link uses BLE Just Works."""

        LOGGER.info("bluetooth.agent_request_pin_code device=%s", device)
        return "0000"

    @method()
    def DisplayPinCode(self, device: "o", pincode: "s"):
        """Accept display callbacks without UI interaction."""

        LOGGER.info("bluetooth.agent_display_pin_code device=%s pincode=%s", device, pincode)

    @method()
    def RequestPasskey(self, device: "o") -> "u":
        """Return a dummy passkey for stacks that ask despite NoInputNoOutput."""

        LOGGER.info("bluetooth.agent_request_passkey device=%s", device)
        return 0

    @method()
    def DisplayPasskey(self, device: "o", passkey: "u", entered: "q"):
        """Accept display callbacks without UI interaction."""

        LOGGER.info(
            "bluetooth.agent_display_passkey device=%s passkey=%s entered=%s",
            device,
            passkey,
            entered,
        )

    @method()
    def RequestConfirmation(self, device: "o", passkey: "u"):
        """Auto-confirm Just-Works pairing."""

        LOGGER.info("bluetooth.agent_confirm device=%s passkey=%s", device, passkey)

    @method()
    def RequestAuthorization(self, device: "o"):
        """Authorize a pairing request."""

        LOGGER.info("bluetooth.agent_authorize device=%s", device)

    @method()
    def AuthorizeService(self, device: "o", uuid: "s"):
        """Authorize service access for the selected printer."""

        LOGGER.info("bluetooth.agent_authorize_service device=%s uuid=%s", device, uuid)

    @method()
    def Cancel(self):
        """BlueZ cancelled the current pairing request."""

        LOGGER.info("bluetooth.agent_cancel")


@dataclass
class BluezAgentService:
    """Register a process-owned BlueZ NoInputNoOutput agent."""

    adapter_path: str = DEFAULT_ADAPTER_PATH
    agent_path: str = DEFAULT_AGENT_PATH

    def __post_init__(self) -> None:
        self._bus: AioMessageBus | None = None
        self._agent_manager: BaseProxyInterface | None = None
        self._registered = False

    async def start(self) -> None:
        """Connect to system D-Bus, export the agent, and make the adapter pairable."""

        if self._registered:
            return

        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        self._bus = bus
        bus.export(self.agent_path, NoInputNoOutputAgent())

        agent_manager = await self._proxy_interface(BLUEZ_ROOT, "org.bluez.AgentManager1")
        self._agent_manager = agent_manager
        await agent_manager.call_register_agent(self.agent_path, "NoInputNoOutput")
        await agent_manager.call_request_default_agent(self.agent_path)

        properties = await self._proxy_interface(
            self.adapter_path,
            "org.freedesktop.DBus.Properties",
        )
        await properties.call_set("org.bluez.Adapter1", "Powered", Variant("b", True))
        await properties.call_set("org.bluez.Adapter1", "Pairable", Variant("b", True))
        await properties.call_set("org.bluez.Adapter1", "PairableTimeout", Variant("u", 0))
        self._registered = True
        LOGGER.info(
            "bluetooth.agent_registered path=%s adapter=%s",
            self.agent_path,
            self.adapter_path,
        )

    async def stop(self) -> None:
        """Unregister the agent and close the D-Bus connection."""

        if self._agent_manager is not None and self._registered:
            try:
                await self._agent_manager.call_unregister_agent(self.agent_path)
            except Exception:
                LOGGER.exception("bluetooth.agent_unregister_failed path=%s", self.agent_path)
        self._registered = False
        if self._bus is not None:
            self._bus.disconnect()
        self._bus = None
        self._agent_manager = None

    async def _proxy_interface(self, path: str, interface_name: str) -> BaseProxyInterface:
        if self._bus is None:
            raise RuntimeError("BlueZ agent bus is not connected")
        introspection = await self._bus.introspect(BLUEZ_SERVICE, path)
        proxy = self._bus.get_proxy_object(BLUEZ_SERVICE, path, introspection)
        return proxy.get_interface(interface_name)
