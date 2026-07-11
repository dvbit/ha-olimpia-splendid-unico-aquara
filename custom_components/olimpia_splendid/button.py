"""Button entity per Olimpia Splendid Unico — Flap toggle."""

import logging
import time

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import OlimpiaCoordinator

_LOGGER = logging.getLogger(__name__)

# Il firmware non riporta lo stato flap nei poll TCP, quindi il comando
# è un toggle cieco: l'app ufficiale (FlapButtonWidget) usa un pulsante
# stateless con lockout di 2s dopo ogni pressione. Replichiamo entrambi.
PRESS_DEBOUNCE = 2.0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: OlimpiaCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([OlimpiaFlapToggleButton(coordinator, entry)])


class OlimpiaFlapToggleButton(CoordinatorEntity[OlimpiaCoordinator], ButtonEntity):
    """Pulsante toggle flap FIXED↔SWING (opcode 0x16, stateless)."""

    _attr_has_entity_name = True
    _attr_name = "Toggle swing"
    _attr_icon = "mdi:arrow-oscillating"

    def __init__(
        self, coordinator: OlimpiaCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        creds = entry.data.get("credentials", {})
        device_uid = creds.get("device_uid", entry.entry_id)
        self._attr_unique_id = f"{device_uid}_flap_toggle"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_uid)},
        )
        self._last_press: float = 0

    async def async_press(self) -> None:
        now = time.monotonic()
        if now - self._last_press < PRESS_DEBOUNCE:
            _LOGGER.debug("Flap toggle ignored (debounce)")
            return
        self._last_press = now
        await self.coordinator.async_send_command("toggle_flap")
