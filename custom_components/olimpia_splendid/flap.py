"""Controllo posizione aletta (flap) con feedback da sensore tilt esterno.

Spec di riferimento (vedi README, sezione "Specifica"):
  R1  configurazione sensore (device + asse)
  R2  calibrazione automatica in due fasi, ripetibile
  R3  modello di posizione open-loop a onda triangolare
  R5  posizionamento con verifica post-stop e blocco a climate OFF
  R6  servizi esposti

Principio di funzionamento
--------------------------
Il comando flap del Unico e' un toggle stateless: ON avvia l'oscillazione
continua fra i due estremi meccanici, OFF ferma l'aletta dove si trova.
La posizione non e' leggibile dal condizionatore, e il sensore Aqara
DJT11LM pubblica l'angolo solo alcuni secondi DOPO un evento `tilt`
(nessuno streaming continuo). Di conseguenza:

  * il movimento e' comandato a tempo (open-loop);
  * l'angolo viene letto solo ad aletta ferma, e serve a risincronizzare
    il modello dopo ogni spostamento;
  * la posizione e' modellata come un'onda triangolare di periodo 2*T,
    dove T = tempo di corsa completa fra i due estremi. Questo gestisce
    nativamente i rimbalzi agli estremi: non esiste una "direzione
    sbagliata", solo un percorso piu' lungo.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    ANGLE_NOISE_DEG,
    ANGLE_REPORT_TIMEOUT,
    ANGLE_SETTLE_DELAY,
    CAL_ANGLE_MAX,
    CAL_ANGLE_MIN,
    CAL_CURVE,
    CAL_STATE_CALIBRATED,
    CAL_STATE_CALIBRATING,
    CAL_STATE_ERROR,
    CAL_STATE_UNCALIBRATED,
    CAL_TIMESTAMP,
    CAL_TRAVEL_TIME,
    CONF_CALIBRATION,
    CONF_FLAP_INVERT,
    CURVE_POINTS,
    DOMAIN,
    POSITION_RETRIES,
    POSITION_TOLERANCE_DEG,
    PROBE_DURATION,
    SEEK_MAX_STEPS,
    SEEK_STEP_DURATION,
)

_LOGGER = logging.getLogger(__name__)

# Signal di dispatch per notificare le entita' di un cambio di stato interno
SIGNAL_FLAP_UPDATED = f"{DOMAIN}_flap_updated"

# Durata della finestra di osservazione usata per capire se l'aletta e'
# gia' in movimento all'avvio (nessun comando inviato — spec R3 homing).
MOTION_OBSERVE_WINDOW = 15.0

# Scostamento massimo (in frazione di corsa) fra posizione prevista dal
# modello e posizione misurata dal sensore. Oltre questa soglia il verso di
# marcia memorizzato non e' piu' attendibile e viene invalidato.
MODEL_DRIFT_LIMIT = 0.15


def _interp(points: list[tuple[float, float]], x: float) -> float:
    """Interpolazione lineare a tratti su una lista ordinata di (x, y)."""
    if not points:
        raise ValueError("empty interpolation table")
    if x <= points[0][0]:
        return points[0][1]
    if x >= points[-1][0]:
        return points[-1][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 <= x <= x1:
            if x1 == x0:
                return y0
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return points[-1][1]


class FlapCalibrationError(HomeAssistantError):
    """Calibrazione fallita o interrotta."""


class FlapController:
    """Stato e logica di posizionamento dell'aletta per un config entry.

    Convenzioni interne:
      * `pos` = frazione 0.0..1.0 del tempo di corsa a partire dall'estremo
        con angolo minimo (0.0 = estremo `angle_min`, 1.0 = `angle_max`);
      * `direction` = +1 se l'aletta stava salendo verso `angle_max`,
        -1 se stava scendendo, None se sconosciuta.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        coordinator,
        angle_entity_id: str,
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        self.angle_entity_id = angle_entity_id

        self._lock = asyncio.Lock()
        self._pos: float | None = None
        self._direction: int | None = None
        self._swinging: bool = False
        self._status: str = CAL_STATE_UNCALIBRATED
        self._progress: str = ""
        self._angle: float | None = None

        if self.calibration:
            self._status = CAL_STATE_CALIBRATED

    # ------------------------------------------------------------------
    # Proprieta' di sola lettura
    # ------------------------------------------------------------------

    @property
    def calibration(self) -> dict | None:
        """Blocco calibrazione persistito in entry.options (spec R2)."""
        cal = self.entry.options.get(CONF_CALIBRATION)
        if not cal or not cal.get(CAL_CURVE):
            return None
        return cal

    @property
    def is_calibrated(self) -> bool:
        return self.calibration is not None

    @property
    def inverted(self) -> bool:
        """True se l'estremo ad angolo massimo corrisponde a "chiuso"."""
        return bool(self.entry.options.get(CONF_FLAP_INVERT, False))

    @property
    def status(self) -> str:
        return self._status

    @property
    def progress(self) -> str:
        """Descrizione sintetica della fase corrente (attributo diagnostico)."""
        return self._progress

    @property
    def angle(self) -> float | None:
        """Ultimo angolo noto, in gradi."""
        if self._angle is not None:
            return self._angle
        return self._read_state_angle()

    @property
    def swinging(self) -> bool:
        return self._swinging

    @property
    def tilt_position(self) -> int | None:
        """Posizione 0-100 % lineare sull'angolo (spec R4).

        0 % = estremo chiuso, 100 % = estremo aperto. L'orientamento
        dipende dall'opzione `flap_invert`.
        """
        cal = self.calibration
        angle = self.angle
        if not cal or angle is None:
            return None
        a_min = cal[CAL_ANGLE_MIN]
        a_max = cal[CAL_ANGLE_MAX]
        if a_max == a_min:
            return None
        pct = (angle - a_min) / (a_max - a_min) * 100.0
        if self.inverted:
            pct = 100.0 - pct
        return int(round(min(100.0, max(0.0, pct))))

    # ------------------------------------------------------------------
    # Utilita' di conversione angolo <-> posizione
    # ------------------------------------------------------------------

    def _curve(self) -> list[tuple[float, float]]:
        cal = self.calibration
        if not cal:
            raise FlapCalibrationError("flap not calibrated")
        return [(float(p), float(a)) for p, a in cal[CAL_CURVE]]

    def _angle_from_pos(self, pos: float) -> float:
        return _interp(self._curve(), pos)

    def _pos_from_angle(self, angle: float) -> float:
        """Inversa della curva: richiede curva monotona crescente in angolo."""
        inverse = [(a, p) for p, a in self._curve()]
        return min(1.0, max(0.0, _interp(inverse, angle)))

    def angle_from_tilt_position(self, pct: float) -> float:
        """Converte una posizione 0-100 % nell'angolo target in gradi."""
        cal = self.calibration
        if not cal:
            raise FlapCalibrationError("flap not calibrated")
        if self.inverted:
            pct = 100.0 - pct
        a_min = cal[CAL_ANGLE_MIN]
        a_max = cal[CAL_ANGLE_MAX]
        return a_min + (a_max - a_min) * pct / 100.0

    def _travel_time(self) -> float:
        cal = self.calibration
        if not cal:
            raise FlapCalibrationError("flap not calibrated")
        return float(cal[CAL_TRAVEL_TIME])

    # ------------------------------------------------------------------
    # Modello a onda triangolare (spec R3)
    # ------------------------------------------------------------------

    @staticmethod
    def _phase(pos: float, direction: int, travel: float) -> float:
        """Converte (posizione, direzione) nella fase 0..2T dell'onda."""
        if direction > 0:
            return pos * travel
        return 2.0 * travel - pos * travel

    @staticmethod
    def _from_phase(phase: float, travel: float) -> tuple[float, int]:
        """Converte una fase 0..2T in (posizione, direzione)."""
        phase %= 2.0 * travel
        if phase <= travel:
            return phase / travel, 1
        return (2.0 * travel - phase) / travel, -1

    def _solve_move(
        self, pos_from: float, direction: int, pos_to: float
    ) -> tuple[float, int]:
        """Durata minima di moto per andare da pos_from a pos_to.

        Restituisce (durata_secondi, direzione_all_arrivo). Gli estremi
        (0.0 e 1.0) hanno un solo punto di fase e invertono la direzione.
        """
        travel = self._travel_time()
        period = 2.0 * travel
        p0 = self._phase(pos_from, direction, travel) % period

        if pos_to <= 0.0:
            candidates = [0.0]
            arrival_dir = 1          # dal minimo si riparte in salita
        elif pos_to >= 1.0:
            candidates = [travel]
            arrival_dir = -1         # dal massimo si riparte in discesa
        else:
            candidates = [pos_to * travel, period - pos_to * travel]
            arrival_dir = 0          # determinata dal candidato scelto

        best_dt: float | None = None
        best_c: float = 0.0
        for cand in candidates:
            delta = (cand - p0) % period
            if best_dt is None or delta < best_dt:
                best_dt, best_c = delta, cand

        assert best_dt is not None
        if arrival_dir == 0:
            arrival_dir = 1 if best_c < travel else -1
        return best_dt, arrival_dir

    # ------------------------------------------------------------------
    # Lettura angolo dal sensore esterno
    # ------------------------------------------------------------------

    def _read_state_angle(self) -> float | None:
        """Legge il valore corrente dell'entita' angolo, senza attendere."""
        state = self.hass.states.get(self.angle_entity_id)
        if state is None or state.state in ("unknown", "unavailable", ""):
            return None
        try:
            return float(state.state)
        except (TypeError, ValueError):
            _LOGGER.warning(
                "Flap angle sensor %s has non-numeric state: %s",
                self.angle_entity_id,
                state.state,
            )
            return None

    async def async_read_angle(self, wait: bool = True) -> float | None:
        """Legge l'angolo, attendendo il report post-tilt del sensore.

        Il DJT11LM pubblica l'angolo alcuni secondi dopo l'evento tilt
        generato dalla fermata dell'aletta: attendiamo un cambio di stato
        entro ANGLE_REPORT_TIMEOUT, poi un breve assestamento per
        catturare un eventuale secondo aggiornamento.
        """
        if not wait:
            self._angle = self._read_state_angle()
            return self._angle

        future: asyncio.Future = self.hass.loop.create_future()

        @callback
        def _state_changed(event) -> None:
            if not future.done():
                future.set_result(True)

        unsub = async_track_state_change_event(
            self.hass, [self.angle_entity_id], _state_changed
        )
        try:
            await asyncio.wait_for(future, ANGLE_REPORT_TIMEOUT)
            # Assestamento: alcuni firmware pubblicano due update ravvicinati
            await asyncio.sleep(ANGLE_SETTLE_DELAY)
        except asyncio.TimeoutError:
            _LOGGER.warning(
                "No angle report from %s within %.0fs — using last known value",
                self.angle_entity_id,
                ANGLE_REPORT_TIMEOUT,
            )
        finally:
            unsub()

        self._angle = self._read_state_angle()
        _LOGGER.debug("Flap angle read: %s", self._angle)
        return self._angle

    # ------------------------------------------------------------------
    # Movimento
    # ------------------------------------------------------------------

    def _check_power(self) -> None:
        """Blocca il posizionamento a condizionatore spento (spec R5)."""
        data = self.coordinator.data or {}
        if not data.get("power"):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="flap_ac_off",
            )

    async def _async_move(self, duration: float) -> float:
        """Muove l'aletta per `duration` secondi e aggiorna il modello."""
        measured = await self.coordinator.async_flap_move(duration)
        if measured is None:
            raise HomeAssistantError("flap move command failed")
        _LOGGER.debug(
            "Flap move requested=%.2fs measured=%.2fs", duration, measured
        )
        if self._pos is not None and self._direction is not None and self.is_calibrated:
            travel = self._travel_time()
            phase = self._phase(self._pos, self._direction, travel) + measured
            self._pos, self._direction = self._from_phase(phase, travel)
        self._async_notify()
        return measured

    async def _async_detect_and_stop_motion(self) -> None:
        """Determina se l'aletta e' in moto e in tal caso la ferma.

        Non invia alcun comando in fase di rilevamento: un toggle cieco su
        stato ignoto non risolverebbe l'ambiguita'. Si osservano invece due
        campioni dell'angolo distanziati nel tempo — se l'aletta oscilla, il
        sensore genera eventi tilt e il valore cambia.
        """
        first = self._read_state_angle()
        _LOGGER.debug("Motion probe: first sample=%s", first)
        await asyncio.sleep(MOTION_OBSERVE_WINDOW)
        second = self._read_state_angle()
        _LOGGER.debug("Motion probe: second sample=%s", second)

        moving = (
            first is not None
            and second is not None
            and abs(second - first) > ANGLE_NOISE_DEG
        )
        if moving:
            _LOGGER.info("Flap was swinging — stopping it to establish position")
            await self.coordinator.async_send_command("toggle_flap")
            await self.async_read_angle(wait=True)
        else:
            _LOGGER.debug("Flap already at rest")
            await self.async_read_angle(wait=False)
        self._swinging = False

    async def _async_ensure_ready(self) -> None:
        """Homing: garantisce posizione e direzione note (spec R3).

        Eseguito pigramente al primo comando utile dopo un riavvio di HA.
        """
        if self._pos is not None and self._direction is not None:
            return
        if not self.is_calibrated:
            raise FlapCalibrationError("flap not calibrated")

        _LOGGER.info("Flap homing started")
        self._progress = "homing"
        self._async_notify()

        if self._pos is None:
            await self._async_detect_and_stop_motion()
            angle = self.angle
            if angle is None:
                raise HomeAssistantError("flap angle unavailable — cannot home")
            self._pos = self._pos_from_angle(angle)

        if self._direction is None:
            # Micro-corsa di sondaggio: il segno della variazione angolare
            # rivela il verso di marcia corrente.
            before = self.angle
            await self._async_move(PROBE_DURATION)
            after = await self.async_read_angle(wait=True)
            if before is None or after is None:
                _LOGGER.warning(
                    "Direction probe inconclusive — assuming upward travel"
                )
                self._direction = 1
            else:
                delta = after - before
                if abs(delta) <= ANGLE_NOISE_DEG:
                    # Variazione nel rumore: probabilmente su un estremo
                    self._direction = 1 if self._pos_from_angle(after) < 0.5 else -1
                    _LOGGER.debug(
                        "Direction probe below noise (%.1f deg) — assuming %+d",
                        delta,
                        self._direction,
                    )
                else:
                    self._direction = 1 if delta > 0 else -1
                self._pos = self._pos_from_angle(after)

        _LOGGER.info(
            "Flap homing done: pos=%.2f direction=%+d angle=%s",
            self._pos,
            self._direction,
            self.angle,
        )
        self._progress = ""
        self._async_notify()

    # ------------------------------------------------------------------
    # API pubblica usata dalle entita' e dai servizi
    # ------------------------------------------------------------------

    async def async_set_angle(self, target_angle: float) -> None:
        """Porta l'aletta all'angolo indicato, con verifica (spec R5)."""
        if not self.is_calibrated:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="flap_not_calibrated",
            )
        self._check_power()

        cal = self.calibration
        lo = min(cal[CAL_ANGLE_MIN], cal[CAL_ANGLE_MAX])
        hi = max(cal[CAL_ANGLE_MIN], cal[CAL_ANGLE_MAX])
        clamped = min(hi, max(lo, target_angle))
        if clamped != target_angle:
            _LOGGER.warning(
                "Requested flap angle %.1f deg out of range [%.1f, %.1f] — clamped",
                target_angle,
                lo,
                hi,
            )

        async with self._lock:
            await self._async_ensure_ready()
            target_pos = self._pos_from_angle(clamped)

            for attempt in range(POSITION_RETRIES + 1):
                duration, arrival_dir = self._solve_move(
                    self._pos, self._direction, target_pos
                )
                if duration < 0.2:
                    _LOGGER.debug("Flap already within one step of target")
                    break
                _LOGGER.debug(
                    "Flap move attempt %d: pos %.2f -> %.2f (%.2fs)",
                    attempt + 1,
                    self._pos,
                    target_pos,
                    duration,
                )
                predicted_pos = target_pos
                await self._async_move(duration)
                self._direction = arrival_dir

                angle = await self.async_read_angle(wait=True)
                if angle is None:
                    _LOGGER.warning(
                        "Position not verified: no angle report (dead reckoning kept)"
                    )
                    break
                # Risincronizzazione: il sensore e' la fonte autorevole
                self._pos = self._pos_from_angle(angle)
                # Se il modello ha sbagliato di molto la posizione prevista,
                # anche la direzione di arrivo e' inaffidabile (tipicamente
                # travel_time impreciso su un percorso con rimbalzo): meglio
                # rifare il sondaggio al comando successivo.
                if abs(predicted_pos - self._pos) > MODEL_DRIFT_LIMIT:
                    _LOGGER.warning(
                        "Flap model drift %.2f > %.2f — direction invalidated, "
                        "next command will re-probe",
                        abs(predicted_pos - self._pos),
                        MODEL_DRIFT_LIMIT,
                    )
                    self._direction = None
                error = abs(angle - clamped)
                if error <= POSITION_TOLERANCE_DEG:
                    _LOGGER.info(
                        "Flap positioned at %.1f deg (target %.1f, error %.1f)",
                        angle,
                        clamped,
                        error,
                    )
                    break
                if attempt < POSITION_RETRIES:
                    _LOGGER.debug(
                        "Flap off target by %.1f deg — corrective retry", error
                    )
                else:
                    # Verifica fallita: la prossima operazione ripartira' da
                    # un sondaggio della direzione invece di fidarsi del
                    # modello (spec R5).
                    self._direction = None
                    _LOGGER.warning(
                        "Flap position off target by %.1f deg after %d attempt(s) "
                        "(target %.1f, actual %.1f) — direction invalidated",
                        error,
                        POSITION_RETRIES + 1,
                        clamped,
                        angle,
                    )
            self._async_notify()

    async def async_set_tilt_position(self, pct: float) -> None:
        """Porta l'aletta alla percentuale di apertura indicata (spec R4)."""
        await self.async_set_angle(self.angle_from_tilt_position(pct))

    async def async_set_swing(self, enable: bool) -> None:
        """Avvia/ferma l'oscillazione continua (spec R4)."""
        if enable:
            self._check_power()
        async with self._lock:
            if enable == self._swinging:
                _LOGGER.debug("Swing already %s", "on" if enable else "off")
                return
            ok = await self.coordinator.async_send_command("toggle_flap")
            if not ok:
                raise HomeAssistantError("flap toggle command failed")
            self._swinging = enable
            if enable:
                _LOGGER.info("Flap continuous swing enabled — position unknown")
                self._pos = None
                self._direction = None
            else:
                # Dopo lo stop la posizione e' rileggibile, il verso no
                angle = await self.async_read_angle(wait=True)
                self._direction = None
                if angle is not None and self.is_calibrated:
                    self._pos = self._pos_from_angle(angle)
                _LOGGER.info("Flap continuous swing stopped at %s deg", angle)
            self._async_notify()

    async def async_stop(self) -> None:
        """Ferma l'aletta se in oscillazione (STOP_TILT)."""
        if self._swinging:
            await self.async_set_swing(False)
        else:
            _LOGGER.debug("Stop requested but flap is not swinging")

    async def async_home(self) -> None:
        """Forza la procedura di homing (servizio `home_flap`, spec R6)."""
        async with self._lock:
            self._pos = None
            self._direction = None
            await self._async_ensure_ready()

    # ------------------------------------------------------------------
    # Calibrazione (spec R2)
    # ------------------------------------------------------------------

    async def async_calibrate(self) -> dict:
        """Esegue la calibrazione completa e persiste il risultato.

        Fase A — ricerca estremi: step a tempo fisso con lettura dell'angolo
        dopo ogni fermata, finche' non si osservano due inversioni di verso
        (quindi entrambi gli estremi meccanici).
        Fase B — curva: dall'estremo di angolo minimo, CURVE_POINTS corse
        equispaziate nel tempo, per mappare la relazione (tempo -> angolo),
        che non e' necessariamente lineare.
        """
        if self._lock.locked():
            raise HomeAssistantError("another flap operation is in progress")
        async with self._lock:
            self._status = CAL_STATE_CALIBRATING
            self._async_notify()
            _LOGGER.info("Flap calibration started")
            try:
                result = await self._async_calibrate_inner()
            except Exception as err:
                self._status = CAL_STATE_ERROR
                self._progress = ""
                self._async_notify()
                _LOGGER.error("Flap calibration failed: %s", err)
                raise
            self._status = CAL_STATE_CALIBRATED
            self._progress = ""
            self._async_notify()
            _LOGGER.info(
                "Flap calibration done: min=%.1f deg max=%.1f deg travel=%.1fs",
                result[CAL_ANGLE_MIN],
                result[CAL_ANGLE_MAX],
                result[CAL_TRAVEL_TIME],
            )
            return result

    async def _async_calibrate_inner(self) -> dict:
        self._check_power()

        # --- Fase A: ricerca degli estremi -----------------------------
        self._progress = "seeking"
        self._async_notify()
        await self._async_detect_and_stop_motion()

        samples: list[tuple[float, float]] = []  # (tempo cumulato, angolo)
        angle = self.angle
        if angle is None:
            # Nessun valore noto: una micro-corsa genera l'evento tilt
            await self._async_move(PROBE_DURATION)
            angle = await self.async_read_angle(wait=True)
            if angle is None:
                raise FlapCalibrationError(
                    f"angle sensor {self.angle_entity_id} produced no reading"
                )
        elapsed = 0.0
        samples.append((elapsed, angle))

        reversals = 0
        last_sign = 0
        for step in range(SEEK_MAX_STEPS):
            measured = await self._async_move(SEEK_STEP_DURATION)
            elapsed += measured
            angle = await self.async_read_angle(wait=True)
            if angle is None:
                raise FlapCalibrationError("lost angle sensor during seek phase")
            delta = angle - samples[-1][1]
            samples.append((elapsed, angle))
            _LOGGER.debug(
                "Seek step %d: t=%.1fs angle=%.1f delta=%+.1f",
                step + 1,
                elapsed,
                angle,
                delta,
            )
            if abs(delta) <= ANGLE_NOISE_DEG:
                continue
            sign = 1 if delta > 0 else -1
            if last_sign and sign != last_sign:
                reversals += 1
                _LOGGER.debug("Seek: reversal %d detected", reversals)
            last_sign = sign
            if reversals >= 2:
                break
        else:
            raise FlapCalibrationError(
                f"no travel limits found after {SEEK_MAX_STEPS} steps — "
                "check the sensor axis and that the flap actually moves"
            )

        angles = [a for _, a in samples]
        angle_min, angle_max = min(angles), max(angles)
        if angle_max - angle_min <= ANGLE_NOISE_DEG * 2:
            raise FlapCalibrationError(
                f"angle span too small ({angle_max - angle_min:.1f} deg) — "
                "wrong axis selected?"
            )
        t_at_min = min(samples, key=lambda s: s[1])[0]
        t_at_max = max(samples, key=lambda s: s[1])[0]
        travel_estimate = abs(t_at_max - t_at_min)
        if travel_estimate < SEEK_STEP_DURATION:
            raise FlapCalibrationError(
                f"implausible travel time estimate ({travel_estimate:.1f}s)"
            )
        _LOGGER.info(
            "Seek phase done: min=%.1f deg max=%.1f deg travel~%.1fs (%d samples)",
            angle_min,
            angle_max,
            travel_estimate,
            len(samples),
        )

        # Calibrazione provvisoria: serve al modello per la fase B
        provisional = {
            CAL_ANGLE_MIN: angle_min,
            CAL_ANGLE_MAX: angle_max,
            CAL_TRAVEL_TIME: travel_estimate,
            CAL_CURVE: [[0.0, angle_min], [1.0, angle_max]],
        }
        self._store_calibration(provisional)
        self._pos = self._pos_from_angle(samples[-1][1])
        self._direction = last_sign or 1

        # --- Fase B: parcheggio sull'estremo minimo --------------------
        self._progress = "parking"
        self._async_notify()
        for attempt in range(2):
            duration, arrival_dir = self._solve_move(self._pos, self._direction, 0.0)
            if duration >= 0.2:
                await self._async_move(duration)
                self._direction = arrival_dir
            angle = await self.async_read_angle(wait=True)
            if angle is None:
                raise FlapCalibrationError("lost angle sensor while parking")
            self._pos = self._pos_from_angle(angle)
            if abs(angle - angle_min) <= POSITION_TOLERANCE_DEG:
                break
            _LOGGER.debug(
                "Parking attempt %d off by %.1f deg — retrying",
                attempt + 1,
                abs(angle - angle_min),
            )
        angle_min = min(angle_min, angle)
        _LOGGER.info("Parked at lower limit (%.1f deg)", angle)

        # --- Fase B: costruzione della curva ---------------------------
        self._progress = "curve"
        self._async_notify()
        step_duration = travel_estimate / CURVE_POINTS
        curve: list[list[float]] = [[0.0, angle_min]]
        cumulative = 0.0
        previous = angle_min
        self._direction = 1
        for index in range(CURVE_POINTS):
            measured = await self._async_move(step_duration)
            cumulative += measured
            angle = await self.async_read_angle(wait=True)
            if angle is None:
                raise FlapCalibrationError("lost angle sensor during curve phase")
            _LOGGER.debug(
                "Curve point %d/%d: t=%.1fs angle=%.1f",
                index + 1,
                CURVE_POINTS,
                cumulative,
                angle,
            )
            if angle < previous - ANGLE_NOISE_DEG:
                # L'aletta ha superato l'estremo e sta tornando indietro:
                # la stima di travel_time era eccessiva. Tronchiamo qui.
                _LOGGER.warning(
                    "Upper limit reached early at %.1fs — truncating curve",
                    cumulative - measured,
                )
                break
            curve.append([cumulative, angle])
            previous = angle

        if len(curve) < 3:
            raise FlapCalibrationError(
                f"curve has too few usable points ({len(curve)})"
            )

        travel_time = curve[-1][0]
        angle_max = curve[-1][1]
        # Normalizzazione dell'ascissa in frazione 0..1 del tempo di corsa
        normalized = [[round(t / travel_time, 4), round(a, 2)] for t, a in curve]
        normalized[0][0] = 0.0
        normalized[-1][0] = 1.0
        normalized = self._enforce_monotonic(normalized)

        result = {
            CAL_ANGLE_MIN: round(angle_min, 2),
            CAL_ANGLE_MAX: round(angle_max, 2),
            CAL_TRAVEL_TIME: round(travel_time, 2),
            CAL_CURVE: normalized,
            CAL_TIMESTAMP: datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        self._store_calibration(result)
        self._pos = 1.0
        self._direction = -1
        return result

    @staticmethod
    def _enforce_monotonic(curve: list[list[float]]) -> list[list[float]]:
        """Rende la curva strettamente crescente in angolo.

        L'interpolazione inversa (angolo -> posizione) richiede monotonia;
        piccole regressioni dovute al rumore del sensore vengono corrette.
        """
        cleaned = [list(curve[0])]
        for pos, angle in curve[1:]:
            if angle <= cleaned[-1][1]:
                angle = cleaned[-1][1] + 0.01
                _LOGGER.debug("Curve point at pos=%.2f nudged for monotonicity", pos)
            cleaned.append([pos, angle])
        return cleaned

    def _store_calibration(self, calibration: dict) -> None:
        """Persiste la calibrazione in entry.options."""
        options = dict(self.entry.options)
        options[CONF_CALIBRATION] = calibration
        self.hass.config_entries.async_update_entry(self.entry, options=options)

    # ------------------------------------------------------------------

    @callback
    def _async_notify(self) -> None:
        """Notifica alle entita' che lo stato interno e' cambiato."""
        async_dispatcher_send(self.hass, f"{SIGNAL_FLAP_UPDATED}_{self.entry.entry_id}")


def resolve_angle_entity(
    hass: HomeAssistant, device_id: str, axis: str
) -> str | None:
    """Trova l'entita' `angle_<axis>` appartenente al device selezionato.

    Copre le convenzioni di naming di zigbee2mqtt (`sensor.<nome>_angle_x`)
    e di ZHA / altre integrazioni (nome originale "Angle X").
    """
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    suffix = f"angle_{axis}".lower()
    fallback: str | None = None

    for entry in er.async_entries_for_device(registry, device_id, True):
        if entry.domain != "sensor":
            continue
        object_id = entry.entity_id.split(".", 1)[1].lower()
        original = (entry.original_name or "").lower().replace(" ", "_")
        if object_id.endswith(suffix) or original.endswith(suffix):
            return entry.entity_id
        if suffix in object_id or suffix in original:
            fallback = entry.entity_id

    if fallback:
        _LOGGER.debug("Angle entity resolved by partial match: %s", fallback)
    return fallback
