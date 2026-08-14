"""DataUpdateCoordinator per Olimpia Splendid Unico."""

import asyncio
import logging
import threading
import time as _time
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, MAX_MOVE_DURATION, SCAN_INTERVAL
from .olimpia.client import OlimpiaClient

_LOGGER = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRY_DELAYS = [3, 5]  # secondi tra tentativi
COMMAND_GRACE_PERIOD = 5.0  # secondi: salta poll dopo un comando recente
COALESCE_WINDOW = 2.0  # secondi: raggruppa service call ravvicinate (issue #12)


class OlimpiaCoordinator(DataUpdateCoordinator):
    """Coordinator per polling stato device Olimpia.

    Ogni operazione (poll o comando) apre una connessione TCP dedicata,
    esattamente come lo script CLI locale:
      connect → authenticate → comando(i) → disconnect
    Elimina tutti i problemi di sessioni long-lived (desync crypto,
    buffer corruption, timeout firmware).
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=SCAN_INTERVAL),
        )
        self.entry = entry
        self.host: str = entry.data["host"]
        self.port: int = entry.data.get("port", 2000)
        self.credentials: dict = dict(entry.data["credentials"])
        self._tcp_lock = threading.Lock()
        self._last_known_mode: int | None = None
        self._last_known_power: bool | None = None
        self._last_known_status: dict | None = None
        self._last_command_time: float = 0
        # Coalescing settaggi climate (issue #12)
        self._pending_batch: dict | None = None
        self._batch_future: asyncio.Future | None = None
        self._batch_handle: asyncio.TimerHandle | None = None
        # Controller aletta, valorizzato da async_setup_entry solo se il
        # sensore di inclinazione e' configurato (spec R1).
        self.flap = None
        # Snapshot delle opzioni strutturali: distingue un cambio di
        # configurazione da un semplice salvataggio della calibrazione.
        from .const import (
            CONF_FLAP_ANGLE_ENTITY,
            CONF_FLAP_AXIS,
            CONF_FLAP_INVERT,
            CONF_FLAP_SENSOR_DEVICE,
        )

        self.options_snapshot = {
            key: entry.options.get(key)
            for key in (
                CONF_FLAP_SENSOR_DEVICE,
                CONF_FLAP_AXIS,
                CONF_FLAP_ANGLE_ENTITY,
                CONF_FLAP_INVERT,
            )
        }

    # --- Persistenza counter ---

    def _persist_counter(self, client: OlimpiaClient) -> None:
        """Salva user_counter aggiornato nel config entry."""
        new_counter = client._user_counter
        old_counter = self.credentials.get("user_counter")
        if new_counter != old_counter:
            _LOGGER.debug("Persisting user_counter: %s -> %s", old_counter, new_counter)
            self.credentials["user_counter"] = new_counter
            new_data = dict(self.entry.data)
            new_data["credentials"] = self.credentials
            self.hass.config_entries.async_update_entry(self.entry, data=new_data)

    # --- Connessione singola per operazione ---

    def _connect_and_auth(self, for_command: bool = False) -> OlimpiaClient:
        """Crea client, connetti, autentica. Raise se fallisce dopo retry."""
        from .olimpia.enums import Opcode
        warm_up = Opcode.PING if for_command else Opcode.GET_MODE
        for attempt in range(MAX_ATTEMPTS):
            client = OlimpiaClient(self.host, self.port)
            client.verbose = True
            try:
                client.connect()
                ok = client.authenticate_from_dict(self.credentials,
                                                   warm_up_opcode=warm_up)
                if ok:
                    _LOGGER.debug("Connected to %s (attempt %d)", self.host, attempt + 1)
                    return client
                _LOGGER.warning("Auth failed on attempt %d/%d", attempt + 1, MAX_ATTEMPTS)
            except (ConnectionError, OSError, Exception) as err:
                _LOGGER.warning("Connection attempt %d/%d failed: %s", attempt + 1, MAX_ATTEMPTS, err)

            client.disconnect()
            if attempt < len(RETRY_DELAYS):
                import time
                time.sleep(RETRY_DELAYS[attempt])

        raise ConnectionError(f"Failed to connect to {self.host} after {MAX_ATTEMPTS} attempts")

    # --- Polling periodico ---

    async def _async_update_data(self) -> dict:
        """Polling: connect → auth → status → disconnect."""
        try:
            data = await self.hass.async_add_executor_job(self._sync_update)
            self._persist_counter_from_data(data)
            return data["status"]
        except Exception as err:
            raise UpdateFailed(f"Update failed: {err}") from err

    def _sync_update(self) -> dict:
        with self._tcp_lock:
            # Grace period: dopo un comando recente, salta il poll per evitare
            # che una lettura intermedia sovrascriva lo stato appena applicato
            since_cmd = _time.monotonic() - self._last_command_time
            if self._last_command_time > 0 and since_cmd < COMMAND_GRACE_PERIOD:
                _LOGGER.debug(
                    "Skipping poll (%.1fs since last command, grace=%ss)",
                    since_cmd, COMMAND_GRACE_PERIOD,
                )
                return {
                    "status": dict(self.data or {}),
                    "counter": self.credentials.get("user_counter"),
                }
            client = self._connect_and_auth()
            try:
                # GET_MODE + poll per ClimaStateEvent (NO COMMIT per evitare
                # che stati SET pendenti vengano applicati dal firmware;
                # ping causa beep su alcune unità — issue #2)
                client._last_clima_event = None
                client.get_mode()
                client._poll_for_events(2.0)
                if client._last_clima_event:
                    status = dict(client._last_clima_event)
                elif self._last_known_power is False and self.data:
                    # Device OFF: NON inviare GET_ROOM_TEMP — il firmware
                    # attiva brevemente fan_only per misurare la temperatura,
                    # e a volte non si rispegne (phantom power on).
                    # Usa i dati cached finche' il device resta spento.
                    _LOGGER.debug(
                        "Device OFF, no ClimaStateEvent after GET_MODE — "
                        "using cached data to avoid GET_ROOM_TEMP activation"
                    )
                    status = dict(self.data)
                else:
                    status = client.get_status_safe()
                if status.get("scheduler"):
                    _LOGGER.warning(
                        "Device scheduler is active — this may cause "
                        "unexpected HVAC mode changes"
                    )
                # Traccia cambi di modo non richiesti dall'utente
                new_mode = status.get("mode")
                if (
                    self._last_known_mode is not None
                    and new_mode is not None
                    and new_mode != self._last_known_mode
                ):
                    since_cmd = _time.monotonic() - self._last_command_time
                    _LOGGER.warning(
                        "HVAC mode changed without user command: "
                        "%s -> %s (%.1fs since last command, "
                        "scheduler=%s, power=%s)",
                        self._last_known_mode, new_mode, since_cmd,
                        status.get("scheduler"), status.get("power"),
                    )
                self._last_known_mode = new_mode
                # Traccia transizioni power non richieste dall'utente
                new_power = status.get("power")
                if (
                    self._last_known_power is not None
                    and new_power is not None
                    and new_power != self._last_known_power
                ):
                    since_cmd = _time.monotonic() - self._last_command_time
                    raw_hex = ""
                    if hasattr(client, '_last_clima_raw'):
                        raw_hex = client._last_clima_raw.hex() if client._last_clima_raw else ""
                    if new_power and since_cmd > COMMAND_GRACE_PERIOD:
                        _LOGGER.warning(
                            "PHANTOM POWER ON: device turned ON without user "
                            "command (%.1fs since last cmd, scheduler=%s, "
                            "mode=%s, raw_event=%s, "
                            "prev_state=%s, new_state=%s)",
                            since_cmd, status.get("scheduler"),
                            status.get("mode"), raw_hex,
                            self._last_known_status, status,
                        )
                    elif not new_power and since_cmd > COMMAND_GRACE_PERIOD:
                        _LOGGER.warning(
                            "Device turned OFF without user command "
                            "(%.1fs since last cmd, scheduler=%s, "
                            "raw_event=%s, prev_state=%s, new_state=%s)",
                            since_cmd, status.get("scheduler"),
                            raw_hex, self._last_known_status, status,
                        )
                self._last_known_power = new_power
                self._last_known_status = dict(status)
                _LOGGER.debug("poll data: %s", status)
                return {"status": status, "counter": client._user_counter}
            finally:
                client.disconnect()

    def _persist_counter_from_data(self, data: dict) -> None:
        """Persisti counter dal risultato sync (chiamato in event loop)."""
        new_counter = data.get("counter")
        if new_counter is not None:
            old_counter = self.credentials.get("user_counter")
            if new_counter != old_counter:
                _LOGGER.debug("Persisting user_counter: %s -> %s", old_counter, new_counter)
                self.credentials["user_counter"] = new_counter
                new_data = dict(self.entry.data)
                new_data["credentials"] = self.credentials
                self.hass.config_entries.async_update_entry(self.entry, data=new_data)

    # --- Comandi HVAC ---

    async def async_send_command(self, method_name: str, *args) -> bool:
        """Invia comando HVAC: connect → auth → comando → disconnect."""
        try:
            result = await self.hass.async_add_executor_job(
                self._sync_command, method_name, *args
            )
            return result
        except Exception as err:
            _LOGGER.warning("Command %s failed: %s", method_name, err)
            return False

    def _sync_command(self, method_name: str, *args) -> bool:
        with self._tcp_lock:
            client = self._connect_and_auth(for_command=True)
            try:
                method = getattr(client, method_name)
                result = method(*args)
                self._last_command_time = _time.monotonic()
                _LOGGER.debug("Command %s(%s) -> %s", method_name, args, result)
                if result and client._last_clima_event:
                    _LOGGER.debug("Post-commit device state: %s", client._last_clima_event)
                return result
            finally:
                client.disconnect()

    # --- Movimento flap a tempo (spec R3) ---

    async def async_flap_move(self, duration: float) -> float | None:
        """Muove l'aletta per `duration` secondi.

        Ritorna il tempo di moto effettivamente misurato, o None in caso di
        errore. La precisione temporale e' critica: vedi _sync_flap_move.
        """
        try:
            return await self.hass.async_add_executor_job(
                self._sync_flap_move, duration
            )
        except Exception as err:
            _LOGGER.warning("Flap move (%.2fs) failed: %s", duration, err)
            return None

    def _sync_flap_move(self, duration: float) -> float:
        """toggle → attesa → toggle in UNA SOLA sessione TCP.

        Motivazione (spec R3): ogni sessione costa connect+auth (1-3 s). Due
        comandi in sessioni separate introdurrebbero un errore dello stesso
        ordine di grandezza della corsa da misurare (5-15 s). Con una sola
        sessione la latenza residua e' identica sui due toggle e si annulla
        nella differenza, perche' il tempo di moto viene misurato fra i due
        *inizi* di comando, non fra le due risposte.
        """
        duration = max(0.0, min(duration, MAX_MOVE_DURATION))
        with self._tcp_lock:
            client = self._connect_and_auth(for_command=True)
            fallback_needed = False
            try:
                t_start = _time.monotonic()
                if not client.toggle_flap():
                    raise ConnectionError("flap start toggle rejected by device")
                overhead = _time.monotonic() - t_start
                _time.sleep(max(0.0, duration - overhead))
                t_stop = _time.monotonic()
                stopped = client.toggle_flap()
                measured = t_stop - t_start
                self._last_command_time = _time.monotonic()
                if not stopped:
                    fallback_needed = True
                _LOGGER.debug(
                    "Flap move: requested=%.2fs measured=%.2fs overhead=%.2fs",
                    duration,
                    measured,
                    overhead,
                )
            finally:
                client.disconnect()

        if fallback_needed:
            # Sessione degradata: l'aletta potrebbe essere ancora in moto.
            # Riproviamo lo stop in una sessione pulita; la misura resta
            # valida solo in via approssimativa.
            _LOGGER.warning(
                "Flap stop toggle failed inside the move session — "
                "retrying stop in a new session (timing may be inaccurate)"
            )
            with self._tcp_lock:
                retry_client = self._connect_and_auth(for_command=True)
                try:
                    retry_client.toggle_flap()
                    self._last_command_time = _time.monotonic()
                finally:
                    retry_client.disconnect()

        return measured

    # --- Coalescing settaggi climate (issue #12) ---

    async def async_queue_setting(self, **settings) -> bool:
        """Accoda settaggi climate e li applica in un'unica sessione TCP.

        Service call ravvicinate (scene: hvac_mode + fan_mode + temperature)
        diventavano sessioni TCP separate con COMMIT immediati: sugli
        inverter i SET inviati durante lo startup post power-on venivano
        persi (issue #12). Come l'app ufficiale, i SET vengono raggruppati
        e applicati con un solo COMMIT dopo la conferma del power-on.
        Chiavi: power (bool: accendi/spegni), mode, fan, temp.
        """
        if self._pending_batch is None:
            self._pending_batch = {}
            self._batch_future = self.hass.loop.create_future()
        self._pending_batch.update(settings)
        # Grace: evita che un poll intermedio sovrascriva lo stato
        # ottimistico durante la finestra di coalescing
        self._last_command_time = _time.monotonic()
        if self._batch_handle:
            self._batch_handle.cancel()
        self._batch_handle = self.hass.loop.call_later(
            COALESCE_WINDOW, self._flush_batch_soon
        )
        # shield: la cancellazione di un singolo service call non deve
        # cancellare il future condiviso dagli altri chiamanti
        return await asyncio.shield(self._batch_future)

    def _flush_batch_soon(self) -> None:
        """Callback del debounce (loop thread): avvia il flush del batch."""
        self.hass.async_create_task(self._async_flush_batch())

    async def _async_flush_batch(self) -> None:
        batch = self._pending_batch
        future = self._batch_future
        self._pending_batch = None
        self._batch_future = None
        self._batch_handle = None
        if batch is None or future is None:
            return
        try:
            ok, confirmed = await self.hass.async_add_executor_job(
                self._sync_apply_batch, batch
            )
        except Exception as err:
            _LOGGER.warning("Batch %s failed: %s", batch, err)
            ok, confirmed = False, None
        if confirmed:
            # Stato reale post-commit dal device (0x61)
            self.async_set_updated_data(confirmed)
        if not future.done():
            future.set_result(ok)
        if not ok and not confirmed:
            # Stato ottimistico probabilmente sbagliato: risincronizza
            await self.async_request_refresh()

    def _sync_apply_batch(self, batch: dict) -> tuple:
        with self._tcp_lock:
            client = self._connect_and_auth(for_command=True)
            try:
                if batch.get("power") is False:
                    ok = client.power_off_and_disable_scheduler()
                    ignored = set(batch) - {"power"}
                    if ignored:
                        _LOGGER.warning(
                            "Batch: power off richiesto, ignoro %s", ignored
                        )
                else:
                    ok = client.apply_settings(
                        power_on=bool(batch.get("power")),
                        mode=batch.get("mode"),
                        fan=batch.get("fan"),
                        temp=batch.get("temp"),
                    )
                confirmed = (
                    dict(client._last_clima_event)
                    if client._last_clima_event else None
                )
                if ok:
                    self._last_command_time = _time.monotonic()
                else:
                    # niente grace: lascia che il poll risincronizzi subito
                    self._last_command_time = 0
                _LOGGER.debug(
                    "Batch %s -> ok=%s confirmed=%s", batch, ok, confirmed
                )
                return ok, confirmed
            finally:
                client.disconnect()

    async def async_shutdown(self) -> None:
        """Cancella batch pendenti al teardown."""
        if self._batch_handle:
            self._batch_handle.cancel()
            self._batch_handle = None
        if self._batch_future and not self._batch_future.done():
            self._batch_future.set_result(False)
        self._pending_batch = None
        self._batch_future = None
        await super().async_shutdown()
