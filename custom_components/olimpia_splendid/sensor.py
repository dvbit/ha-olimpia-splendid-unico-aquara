"""Sensor entity per Olimpia Splendid Unico — inclinazione aletta.

Spec R4: esposizione dell'angolo corrente in gradi e di un sensore
diagnostico con lo stato della calibrazione.
"""

import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import DEGREE, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    CAL_ANGLE_MAX,
    CAL_ANGLE_MIN,
    CAL_STATES,
    CAL_TIMESTAMP,
    CAL_TRAVEL_TIME,
    DOMAIN,
)
from .coordinator import OlimpiaCoordinator
from .flap import SIGNAL_FLAP_UPDATED

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Crea i sensori dell'aletta, se il sensore tilt e' configurato."""
    coordinator: OlimpiaCoordinator = hass.data[DOMAIN][entry.entry_id]
    if coordinator.flap is None:
        return
    async_add_entities(
        [
            OlimpiaFlapAngleSensor(coordinator, entry),
            OlimpiaFlapCalibrationSensor(coordinator, entry),
        ]
    )


class _OlimpiaFlapSensorBase(SensorEntity):
    """Base comune: device info e sottoscrizione agli update del controller."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self, coordinator: OlimpiaCoordinator, entry: ConfigEntry, suffix: str
    ) -> None:
        self._coordinator = coordinator
        self._entry = entry
        creds = entry.data.get("credentials", {})
        device_uid = creds.get("device_uid", entry.entry_id)
        self._attr_unique_id = f"{device_uid}_{suffix}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_uid)},
        )

    @property
    def _flap(self):
        return self._coordinator.flap

    @property
    def available(self) -> bool:
        return self._flap is not None

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


class OlimpiaFlapAngleSensor(_OlimpiaFlapSensorBase):
    """Angolo corrente dell'aletta, in gradi (spec R4).

    Rispecchia il sensore di inclinazione esterno: si aggiorna quindi solo
    quando il DJT11LM pubblica un nuovo valore (alcuni secondi dopo un
    evento tilt), non in modo continuo durante il movimento.
    """

    # Nome hardcoded in inglese: evita entity_id dipendenti dalla lingua.
    _attr_name = "Flap angle"
    _attr_icon = "mdi:angle-acute"
    _attr_native_unit_of_measurement = DEGREE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    def __init__(
        self, coordinator: OlimpiaCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, entry, "flap_angle")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Segue direttamente l'entita' sorgente: cosi' il valore resta
        # allineato anche per movimenti non comandati dall'integrazione.
        self.async_on_remove(
            async_track_state_change_event(
                self.hass,
                [self._flap.angle_entity_id],
                self._handle_source_update,
            )
        )

    @callback
    def _handle_source_update(self, event) -> None:
        _LOGGER.debug("Flap angle source updated: %s", event.data.get("new_state"))
        self.async_write_ha_state()

    @property
    def native_value(self) -> float | None:
        return self._flap.angle

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "position": self._flap.tilt_position,
            "source_entity": self._flap.angle_entity_id,
        }


class OlimpiaFlapCalibrationSensor(_OlimpiaFlapSensorBase):
    """Stato della calibrazione dell'aletta (diagnostico, spec R4)."""

    _attr_name = "Flap calibration"
    _attr_icon = "mdi:tune-variant"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = CAL_STATES
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    # translation_key solo per la traduzione dei VALORI di stato: il nome
    # dell'entita' resta in inglese per non alterare l'entity_id.
    _attr_translation_key = "flap_calibration"

    def __init__(
        self, coordinator: OlimpiaCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, entry, "flap_calibration")

    @property
    def native_value(self) -> str:
        return self._flap.status

    @property
    def extra_state_attributes(self) -> dict:
        attributes: dict = {"phase": self._flap.progress}
        calibration = self._flap.calibration
        if calibration:
            attributes.update(
                {
                    "angle_min": calibration.get(CAL_ANGLE_MIN),
                    "angle_max": calibration.get(CAL_ANGLE_MAX),
                    "travel_time": calibration.get(CAL_TRAVEL_TIME),
                    "calibrated_at": calibration.get(CAL_TIMESTAMP),
                }
            )
        return attributes
