"""Config flow per Olimpia Splendid Unico."""

import asyncio
import json
import logging
import socket
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import (
    BooleanSelector,
    DeviceSelector,
    DeviceSelectorConfig,
    EntityFilterSelectorConfig,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_CALIBRATION,
    CONF_FLAP_ANGLE_ENTITY,
    CONF_FLAP_AXIS,
    CONF_FLAP_INVERT,
    CONF_FLAP_SENSOR_DEVICE,
    DEFAULT_PORT,
    DOMAIN,
    FLAP_AXES,
)
from .flap import resolve_angle_entity
from .olimpia.client import OlimpiaClient
from .olimpia.credentials import load_credentials


def flap_sensor_schema(defaults: dict | None = None) -> vol.Schema:
    """Schema dello step di configurazione del sensore aletta (spec R1).

    Tutti i campi sono opzionali: lasciando vuoto il device il controllo di
    posizione resta disattivato e l'integrazione espone solo il toggle cieco.
    """
    defaults = defaults or {}
    device_default = defaults.get(CONF_FLAP_SENSOR_DEVICE)
    device_field = (
        vol.Optional(CONF_FLAP_SENSOR_DEVICE, description={"suggested_value": device_default})
        if device_default
        else vol.Optional(CONF_FLAP_SENSOR_DEVICE)
    )
    return vol.Schema(
        {
            device_field: DeviceSelector(
                DeviceSelectorConfig(
                    entity=[EntityFilterSelectorConfig(domain="sensor")]
                )
            ),
            vol.Optional(
                CONF_FLAP_AXIS, default=defaults.get(CONF_FLAP_AXIS, "x")
            ): SelectSelector(
                SelectSelectorConfig(
                    options=FLAP_AXES,
                    translation_key="flap_axis",
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(
                CONF_FLAP_INVERT, default=defaults.get(CONF_FLAP_INVERT, False)
            ): BooleanSelector(),
        }
    )

_LOGGER = logging.getLogger(__name__)


class OlimpiaSplendidConfigFlow(ConfigFlow, domain=DOMAIN):
    """Config flow duale: IP manuale o BLE setup."""

    VERSION = 1

    def __init__(self) -> None:
        self._ble_address: str | None = None
        self._ble_devices: list[dict] = []
        self._ble_pin: int = 0
        self._ble_ssid: str = ""
        self._ble_password: str = ""
        self._pairing_task: asyncio.Task | None = None
        self._pairing_result: dict | None = None
        # Dati dell'entry in attesa dello step opzionale sul sensore aletta
        self._entry_data: dict | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> "OlimpiaOptionsFlow":
        """Espone il pulsante Configura (opzioni + calibrazione)."""
        return OlimpiaOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step iniziale: scelta metodo."""
        return self.async_show_menu(
            step_id="user",
            menu_options=["ble_scan", "manual_ip"],
        )

    # --- Path A: IP manuale ---

    async def async_step_manual_ip(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Inserimento IP manuale."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input["host"]
            port = user_input.get("port", DEFAULT_PORT)
            creds_json = user_input.get("credentials_json", "").strip()

            creds = None
            if creds_json:
                # Parse pasted credentials JSON
                try:
                    creds = json.loads(creds_json)
                    required = ("user_hash", "user_counter", "device_uid", "crypto")
                    if not all(k in creds for k in required):
                        errors["base"] = "invalid_credentials_json"
                        creds = None
                except (json.JSONDecodeError, ValueError):
                    errors["base"] = "invalid_credentials_json"

            if not creds and not errors:
                # Fallback: load from disk
                creds = await self.hass.async_add_executor_job(
                    load_credentials, host
                )
                if not creds:
                    errors["base"] = "no_credentials"

            if creds and not errors:
                # Tenta connessione + auth
                try:
                    ok = await self.hass.async_add_executor_job(
                        self._test_connection, host, port, creds
                    )
                    if ok:
                        device_uid = creds.get("device_uid", host)
                        await self.async_set_unique_id(device_uid)
                        self._abort_if_unique_id_configured()

                        self._entry_data = {
                            "host": host,
                            "port": port,
                            "credentials": creds,
                            "device_uid": device_uid,
                        }
                        return await self.async_step_flap_sensor()
                    else:
                        errors["base"] = "invalid_auth"
                except (ConnectionError, OSError, socket.timeout):
                    errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="manual_ip",
            data_schema=vol.Schema(
                {
                    vol.Required("host"): str,
                    vol.Optional("port", default=DEFAULT_PORT): int,
                    vol.Optional("credentials_json", default=""): str,
                }
            ),
            errors=errors,
        )

    @staticmethod
    def _test_connection(host: str, port: int, creds: dict) -> bool:
        """Test sync connessione + autenticazione."""
        client = OlimpiaClient(host, port)
        try:
            client.connect()
            return client.authenticate_from_dict(creds)
        finally:
            client.disconnect()

    # --- Path B: BLE setup ---

    async def async_step_ble_scan(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Scan BLE per device Olimpia."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._ble_address = user_input["ble_device"]
            return await self.async_step_ble_pin()

        # Esegui scan
        from .olimpia_ble import OlimpiaBLE

        devices = await OlimpiaBLE.scan(timeout=10)
        olimpia_devices = devices

        if not olimpia_devices:
            errors["base"] = "ble_no_devices"
            return self.async_show_form(
                step_id="ble_scan",
                data_schema=vol.Schema({}),
                errors=errors,
            )

        self._ble_devices = olimpia_devices
        device_options = {
            d["address"]: f"{d['name']} ({d['address']}) RSSI:{d['rssi']}"
            for d in olimpia_devices
        }

        return self.async_show_form(
            step_id="ble_scan",
            data_schema=vol.Schema(
                {
                    vol.Required("ble_device"): vol.In(device_options),
                }
            ),
            errors=errors,
        )

    async def async_step_ble_pin(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Inserimento PIN e credenziali WiFi."""
        if user_input is not None:
            self._ble_pin = user_input["pin"]
            self._ble_ssid = user_input["ssid"]
            self._ble_password = user_input["wifi_password"]
            return await self.async_step_ble_pairing()

        return self.async_show_form(
            step_id="ble_pin",
            data_schema=vol.Schema(
                {
                    vol.Required("pin"): str,
                    vol.Required("ssid"): str,
                    vol.Required("wifi_password"): str,
                }
            ),
        )

    async def async_step_ble_pairing(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Esegue pairing BLE con progress."""
        if not self._pairing_task:
            self._pairing_task = self.hass.async_create_task(
                self._do_ble_pairing()
            )
            return self.async_show_progress(
                step_id="ble_pairing",
                progress_action="ble_pairing",
                progress_task=self._pairing_task,
            )

        # Task completato — HA ci richiama automaticamente
        try:
            await self._pairing_task
        except Exception:
            _LOGGER.exception("BLE pairing failed")
            return self.async_show_progress_done(next_step_id="ble_pairing_failed")

        if self._pairing_result:
            return self.async_show_progress_done(next_step_id="ble_pairing_done")

        return self.async_show_progress_done(next_step_id="ble_pairing_failed")

    async def async_step_ble_pairing_done(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Pairing riuscito — crea config entry."""
        creds = self._pairing_result
        host = creds.get("host", "")
        device_uid = creds.get("device_uid", host)

        if host:
            await self.async_set_unique_id(device_uid)
            self._abort_if_unique_id_configured()

            self._entry_data = {
                "host": host,
                "port": DEFAULT_PORT,
                "credentials": creds,
                "device_uid": device_uid,
            }
            return await self.async_step_flap_sensor()

        return self.async_abort(reason="ble_pairing_failed")

    async def async_step_ble_pairing_failed(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """BLE pairing fallito."""
        return self.async_abort(reason="ble_pairing_failed")

    async def _do_ble_pairing(self) -> None:
        """Task asincrono per BLE pairing + WiFi config."""
        from .olimpia_ble import OlimpiaBLE, ble_full_setup

        ble = OlimpiaBLE(verbose=True)
        try:
            from bleak import BleakScanner

            _LOGGER.debug("BLE scan for %s...", self._ble_address)
            device = await BleakScanner.find_device_by_address(
                self._ble_address, timeout=10
            )
            if device is None:
                _LOGGER.error("BLE device %s not found during scan", self._ble_address)
                return

            _LOGGER.debug("BLE connecting to %s (%s)...", device.name, device.address)
            await ble.connect(device)
            _LOGGER.debug("BLE connected, starting full setup...")

            result = await ble_full_setup(
                ble,
                pin=int(self._ble_pin),
                ssid=self._ble_ssid,
                password=self._ble_password,
                return_creds=True,
            )
            _LOGGER.debug("BLE full setup result: %s", type(result).__name__)
            if result and isinstance(result, dict):
                _LOGGER.debug("BLE pairing OK, host=%s", result.get("host"))
                self._pairing_result = result
            else:
                _LOGGER.error("BLE pairing returned falsy: %r", result)
        except Exception:
            _LOGGER.exception("BLE pairing exception")
            raise
        finally:
            await ble.disconnect()
            _LOGGER.debug("BLE disconnected")

    # --- Step opzionale: sensore di inclinazione aletta (spec R1) ---

    async def async_step_flap_sensor(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Seleziona il device del sensore tilt e l'asse da monitorare.

        Lo step e' saltabile: senza sensore l'integrazione funziona come
        prima (solo toggle cieco dell'oscillazione).
        """
        errors: dict[str, str] = {}
        assert self._entry_data is not None
        host = self._entry_data["host"]

        if user_input is not None:
            options, error = _build_flap_options(self.hass, user_input)
            if error:
                errors["base"] = error
            else:
                if options:
                    _LOGGER.info(
                        "Flap tilt sensor configured: %s (axis %s)",
                        options[CONF_FLAP_ANGLE_ENTITY],
                        options[CONF_FLAP_AXIS],
                    )
                else:
                    _LOGGER.debug("Flap tilt sensor step skipped by user")
                return self.async_create_entry(
                    title=f"Olimpia Splendid ({host})",
                    data=self._entry_data,
                    options=options,
                )

        return self.async_show_form(
            step_id="flap_sensor",
            data_schema=flap_sensor_schema(),
            errors=errors,
        )


def _build_flap_options(hass, user_input: dict[str, Any]) -> tuple[dict, str | None]:
    """Valida l'input del sensore aletta e costruisce il blocco opzioni.

    Ritorna (opzioni, codice_errore). Opzioni vuote = sensore non
    configurato (step saltato dall'utente).
    """
    device_id = user_input.get(CONF_FLAP_SENSOR_DEVICE)
    if not device_id:
        return {}, None

    axis = user_input.get(CONF_FLAP_AXIS, "x")
    angle_entity = resolve_angle_entity(hass, device_id, axis)
    if not angle_entity:
        _LOGGER.warning(
            "No angle_%s entity found on device %s", axis, device_id
        )
        return {}, "angle_entity_not_found"

    return {
        CONF_FLAP_SENSOR_DEVICE: device_id,
        CONF_FLAP_AXIS: axis,
        CONF_FLAP_ANGLE_ENTITY: angle_entity,
        CONF_FLAP_INVERT: bool(user_input.get(CONF_FLAP_INVERT, False)),
    }, None


class OlimpiaOptionsFlow(OptionsFlow):
    """Opzioni: riconfigurazione sensore e ricalibrazione (spec R1/R2).

    NOTA: non assegnare mai self.config_entry qui — sovrascriverebbe la
    proprieta' fornita da Home Assistant e il pulsante "Configura" non
    comparirebbe.
    """

    def __init__(self) -> None:
        self._calibration_task: asyncio.Task | None = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Menu principale delle opzioni."""
        menu_options = ["flap_sensor"]
        if self.config_entry.options.get(CONF_FLAP_ANGLE_ENTITY):
            menu_options.append("calibrate")
        return self.async_show_menu(step_id="init", menu_options=menu_options)

    async def async_step_flap_sensor(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Riconfigura device e asse del sensore di inclinazione."""
        errors: dict[str, str] = {}
        current = dict(self.config_entry.options)

        if user_input is not None:
            options, error = _build_flap_options(self.hass, user_input)
            if error:
                errors["base"] = error
            else:
                new_options = dict(options)
                if options and current.get(CONF_FLAP_ANGLE_ENTITY) == options.get(
                    CONF_FLAP_ANGLE_ENTITY
                ):
                    # Stesso sensore: la calibrazione esistente resta valida
                    if CONF_CALIBRATION in current:
                        new_options[CONF_CALIBRATION] = current[CONF_CALIBRATION]
                else:
                    _LOGGER.info(
                        "Flap sensor changed — previous calibration discarded"
                    )
                return self.async_create_entry(title="", data=new_options)

        return self.async_show_form(
            step_id="flap_sensor",
            data_schema=flap_sensor_schema(current),
            errors=errors,
        )

    # --- Calibrazione con barra di avanzamento (spec R2) ---

    def _controller(self):
        """Ritorna il FlapController attivo, se l'entry e' caricata."""
        coordinator = self.hass.data.get(DOMAIN, {}).get(
            self.config_entry.entry_id
        )
        return getattr(coordinator, "flap", None)

    async def async_step_calibrate(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Esegue la calibrazione mostrando l'avanzamento."""
        if self._calibration_task is None:
            controller = self._controller()
            if controller is None:
                return self.async_abort(reason="flap_not_configured")
            _LOGGER.info("Flap calibration requested from options flow")
            self._calibration_task = self.hass.async_create_task(
                controller.async_calibrate()
            )
            return self.async_show_progress(
                step_id="calibrate",
                progress_action="calibrating",
                progress_task=self._calibration_task,
            )

        try:
            await self._calibration_task
        except Exception as err:  # noqa: BLE001 — l'errore e' gia' loggato
            _LOGGER.debug("Calibration task ended with error: %s", err)
            return self.async_show_progress_done(next_step_id="calibrate_failed")
        return self.async_show_progress_done(next_step_id="calibrate_done")

    async def async_step_calibrate_done(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        return self.async_abort(reason="calibration_done")

    async def async_step_calibrate_failed(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        return self.async_abort(reason="calibration_failed")
