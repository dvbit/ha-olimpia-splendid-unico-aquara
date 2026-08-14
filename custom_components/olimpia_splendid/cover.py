"""Cover entity per l'aletta orientabile — spec R4/R6.

L'aletta viene modellata come una serranda (`damper`) con sola gestione
dell'inclinazione: 0 % = chiusa, 100 % = completamente aperta. La
posizione principale della cover non e' supportata perche' l'aletta non
trasla.
"""

import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.cover import (
    ATTR_TILT_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_platform
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_ANGLE,
    CAL_ANGLE_MAX,
    CAL_ANGLE_MIN,
    DOMAIN,
    SERVICE_CALIBRATE_FLAP,
    SERVICE_HOME_FLAP,
    SERVICE_SET_FLAP_ANGLE,
)
from .coordinator import OlimpiaCoordinator
from .flap import SIGNAL_FLAP_UPDATED

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Crea la cover dell'aletta, se il sensore tilt e' configurato."""
    coordinator: OlimpiaCoordinator = hass.data[DOMAIN][entry.entry_id]
    if coordinator.flap is None:
        return

    async_add_entities([OlimpiaFlapCover(coordinator, entry)])

    # Servizi legati all'entita' (spec R6)
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_SET_FLAP_ANGLE,
        {vol.Required(ATTR_ANGLE): vol.Coerce(float)},
        "async_service_set_angle",
    )
    platform.async_register_entity_service(
        SERVICE_CALIBRATE_FLAP,
        None,
        "async_service_calibrate",
    )
    platform.async_register_entity_service(
        SERVICE_HOME_FLAP,
        None,
        "async_service_home",
    )


class OlimpiaFlapCover(CoordinatorEntity[OlimpiaCoordinator], CoverEntity):
    """Aletta orientabile come cover di tipo damper (spec R4)."""

    _attr_has_entity_name = True
    # Nome hardcoded in inglese: l'entity_id non deve dipendere dalla
    # lingua dell'istanza HA.
    _attr_name = "Flap"
    _attr_icon = "mdi:air-filter"
    _attr_device_class = CoverDeviceClass.DAMPER
    _attr_supported_features = (
        CoverEntityFeature.OPEN_TILT
        | CoverEntityFeature.CLOSE_TILT
        | CoverEntityFeature.STOP_TILT
        | CoverEntityFeature.SET_TILT_POSITION
    )

    def __init__(
        self, coordinator: OlimpiaCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        creds = entry.data.get("credentials", {})
        device_uid = creds.get("device_uid", entry.entry_id)
        self._attr_unique_id = f"{device_uid}_flap"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_uid)},
        )
        self._entry = entry

    async def async_added_to_hass(self) -> None:
        """Sottoscrive gli aggiornamenti interni del controller flap."""
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
    def _flap(self):
        return self.coordinator.flap

    @property
    def available(self) -> bool:
        return super().available and self._flap is not None

    @property
    def current_cover_tilt_position(self) -> int | None:
        return self._flap.tilt_position

    @property
    def is_closed(self) -> bool | None:
        """Chiusa quando l'inclinazione e' a 0 %."""
        position = self._flap.tilt_position
        if position is None:
            return None
        return position == 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        flap = self._flap
        attributes: dict[str, Any] = {
            "angle": flap.angle,
            "swinging": flap.swinging,
            "calibration_status": flap.status,
        }
        calibration = flap.calibration
        if calibration:
            attributes["angle_min"] = calibration[CAL_ANGLE_MIN]
            attributes["angle_max"] = calibration[CAL_ANGLE_MAX]
        return attributes

    # --- Comandi ---

    async def async_open_cover_tilt(self, **kwargs: Any) -> None:
        await self._flap.async_set_tilt_position(100)

    async def async_close_cover_tilt(self, **kwargs: Any) -> None:
        await self._flap.async_set_tilt_position(0)

    async def async_stop_cover_tilt(self, **kwargs: Any) -> None:
        await self._flap.async_stop()

    async def async_set_cover_tilt_position(self, **kwargs: Any) -> None:
        await self._flap.async_set_tilt_position(kwargs[ATTR_TILT_POSITION])

    # --- Servizi (spec R6) ---

    async def async_service_set_angle(self, angle: float) -> None:
        """Servizio `set_flap_angle`: posiziona a un angolo assoluto."""
        _LOGGER.debug("Service set_flap_angle(%s) called", angle)
        await self._flap.async_set_angle(angle)

    async def async_service_calibrate(self) -> None:
        """Servizio `calibrate_flap`: riesegue la calibrazione (spec R2)."""
        _LOGGER.debug("Service calibrate_flap called")
        await self._flap.async_calibrate()

    async def async_service_home(self) -> None:
        """Servizio `home_flap`: forza il riallineamento della posizione."""
        _LOGGER.debug("Service home_flap called")
        await self._flap.async_home()
