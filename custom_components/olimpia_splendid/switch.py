"""Switch entity per Olimpia Splendid Unico — Scheduler control."""

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import OlimpiaCoordinator
from .flap import SIGNAL_FLAP_UPDATED

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: OlimpiaCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SwitchEntity] = [OlimpiaSchedulerSwitch(coordinator, entry)]
    # Oscillazione continua: ha senso solo con il controller flap attivo,
    # perche' lo stato ON/OFF e' tracciato dal controller (spec R4).
    if coordinator.flap is not None:
        entities.append(OlimpiaFlapSwingSwitch(coordinator, entry))
    async_add_entities(entities)


class OlimpiaSchedulerSwitch(CoordinatorEntity[OlimpiaCoordinator], SwitchEntity):
    """Switch per abilitare/disabilitare lo scheduler interno del device."""

    _attr_has_entity_name = True
    _attr_name = "Scheduler"
    _attr_icon = "mdi:calendar-clock"

    def __init__(
        self, coordinator: OlimpiaCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        creds = entry.data.get("credentials", {})
        device_uid = creds.get("device_uid", entry.entry_id)
        self._attr_unique_id = f"{device_uid}_scheduler"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_uid)},
        )

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        if not data:
            return None
        return bool(data.get("scheduler"))

    async def async_turn_on(self, **kwargs) -> None:
        ok = await self.coordinator.async_send_command("toggle_scheduler", True)
        if ok:
            self._update_scheduler_state(True)

    async def async_turn_off(self, **kwargs) -> None:
        ok = await self.coordinator.async_send_command("toggle_scheduler", False)
        if ok:
            self._update_scheduler_state(False)

    def _update_scheduler_state(self, enabled: bool) -> None:
        if self.coordinator.data:
            data = dict(self.coordinator.data)
            data["scheduler"] = enabled
            self.coordinator.async_set_updated_data(data)


class OlimpiaFlapSwingSwitch(CoordinatorEntity[OlimpiaCoordinator], SwitchEntity):
    """Oscillazione continua dell'aletta (spec R4).

    Il comando device e' un toggle cieco: lo stato ON/OFF e' mantenuto dal
    FlapController, che lo invalida quando la posizione diventa ignota.
    """

    _attr_has_entity_name = True
    # Nome hardcoded in inglese: evita entity_id dipendenti dalla lingua.
    _attr_name = "Continuous swing"
    _attr_icon = "mdi:arrow-oscillating"

    def __init__(
        self, coordinator: OlimpiaCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        creds = entry.data.get("credentials", {})
        device_uid = creds.get("device_uid", entry.entry_id)
        self._attr_unique_id = f"{device_uid}_flap_swing"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_uid)},
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_FLAP_UPDATED}_{self._entry.entry_id}",
                self._handle_flap_update,
            )
        )

    @callback
    def _handle_flap_update(self) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.flap.swinging)

    async def async_turn_on(self, **kwargs) -> None:
        _LOGGER.debug("Continuous swing: turn on")
        await self.coordinator.flap.async_set_swing(True)

    async def async_turn_off(self, **kwargs) -> None:
        _LOGGER.debug("Continuous swing: turn off")
        await self.coordinator.flap.async_set_swing(False)
