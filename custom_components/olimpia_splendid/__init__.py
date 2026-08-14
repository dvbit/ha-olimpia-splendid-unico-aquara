"""Integrazione Olimpia Splendid Unico per Home Assistant."""

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import (
    CONF_FLAP_ANGLE_ENTITY,
    CONF_FLAP_AXIS,
    CONF_FLAP_INVERT,
    CONF_FLAP_SENSOR_DEVICE,
    DOMAIN,
)
from .coordinator import OlimpiaCoordinator
from .flap import FlapController

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.COVER,
    Platform.SENSOR,
    Platform.SWITCH,
]

# Opzioni la cui modifica richiede un reload dell'entry. Il blocco di
# calibrazione e' volutamente escluso: viene riscritto DURANTE la
# calibrazione stessa e un reload la interromperebbe (spec R2).
RELOAD_OPTION_KEYS = (
    CONF_FLAP_SENSOR_DEVICE,
    CONF_FLAP_AXIS,
    CONF_FLAP_ANGLE_ENTITY,
    CONF_FLAP_INVERT,
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Olimpia Splendid from a config entry."""
    coordinator = OlimpiaCoordinator(hass, entry)
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception as err:
        raise ConfigEntryNotReady(
            f"Device {entry.data.get('host')} not ready: {err}"
        ) from err

    # Controller flap: creato solo se l'utente ha configurato il sensore
    # di inclinazione (step opzionale del config flow — spec R1).
    angle_entity = entry.options.get(CONF_FLAP_ANGLE_ENTITY)
    if angle_entity:
        coordinator.flap = FlapController(hass, entry, coordinator, angle_entity)
        _LOGGER.debug("Flap controller enabled on %s", angle_entity)
    else:
        coordinator.flap = None
        _LOGGER.debug("Flap controller disabled (no tilt sensor configured)")

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_update_options))
    return True


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Ricarica l'entry solo se sono cambiate le opzioni strutturali."""
    coordinator: OlimpiaCoordinator | None = hass.data.get(DOMAIN, {}).get(
        entry.entry_id
    )
    if coordinator is None:
        return
    snapshot = {key: entry.options.get(key) for key in RELOAD_OPTION_KEYS}
    if snapshot == coordinator.options_snapshot:
        _LOGGER.debug("Options updated (calibration only) — no reload needed")
        return
    _LOGGER.info("Flap sensor options changed — reloading entry")
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: OlimpiaCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()
    return unload_ok
