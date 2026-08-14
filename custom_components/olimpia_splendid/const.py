"""Costanti per l'integrazione Olimpia Splendid Unico."""

from homeassistant.components.climate import (
    HVACMode,
)

DOMAIN = "olimpia_splendid"

DEFAULT_PORT = 2000
SCAN_INTERVAL = 30

# Mapping mode device → HVACMode HA
MODE_DEVICE_TO_HA = {
    0: HVACMode.HEAT,
    1: HVACMode.COOL,
    2: HVACMode.DRY,
    3: HVACMode.FAN_ONLY,
    4: HVACMode.AUTO,
}

MODE_HA_TO_DEVICE = {v: k for k, v in MODE_DEVICE_TO_HA.items()}

# Mapping fan device → stringa HA
FAN_DEVICE_TO_HA = {
    0: "low",
    1: "medium",
    2: "high",
    3: "auto",
}

FAN_HA_TO_DEVICE = {v: k for k, v in FAN_DEVICE_TO_HA.items()}


# ---------------------------------------------------------------------------
# Controllo posizione aletta (flap) — spec R1..R6
#
# Il flap del Unico si comanda con un unico opcode toggle (0x16, stateless):
# ON avvia l'oscillazione continua min↔max, OFF ferma l'aletta *dove si
# trova* (confermato sul campo — spec R1). Il firmware non riporta alcuna
# posizione, quindi il feedback arriva da un sensore di vibrazione/tilt
# esterno (Aqara DJT11LM) incollato sull'aletta.
#
# Vincolo del sensore (zigbee2mqtt DJT11LM): gli angoli si aggiornano solo
# alcuni secondi DOPO un evento `tilt`, e le action `vibration` non sono
# emesse piu' di una volta al minuto. Nessuna lettura e' quindi possibile
# mentre l'aletta e' in moto → il controllo e' open-loop a tempo, con
# risincronizzazione dell'angolo a ogni fermata (spec R3).
# ---------------------------------------------------------------------------

# --- Chiavi di configurazione (entry.options) ---
CONF_FLAP_SENSOR_DEVICE = "flap_sensor_device"   # device_id del sensore tilt
CONF_FLAP_AXIS = "flap_axis"                     # asse monitorato: x | y | z
CONF_FLAP_ANGLE_ENTITY = "flap_angle_entity"     # entity_id risolto dal device
CONF_FLAP_INVERT = "flap_invert"                 # True se angle_max = chiuso
CONF_CALIBRATION = "calibration"                 # risultati calibrazione

FLAP_AXES = ["x", "y", "z"]

# --- Chiavi del blocco calibrazione ---
CAL_ANGLE_MIN = "angle_min"       # gradi all'estremo "chiuso" (spec R2)
CAL_ANGLE_MAX = "angle_max"       # gradi all'estremo "aperto"
CAL_TRAVEL_TIME = "travel_time"   # secondi per la corsa completa min→max
CAL_CURVE = "curve"               # [[pos_frazione, angolo], ...] monotona
CAL_TIMESTAMP = "timestamp"       # ISO 8601 dell'ultima calibrazione

# --- Stati del sensore diagnostico di calibrazione (spec R4) ---
CAL_STATE_UNCALIBRATED = "uncalibrated"
CAL_STATE_CALIBRATING = "calibrating"
CAL_STATE_CALIBRATED = "calibrated"
CAL_STATE_ERROR = "error"
CAL_STATES = [
    CAL_STATE_UNCALIBRATED,
    CAL_STATE_CALIBRATING,
    CAL_STATE_CALIBRATED,
    CAL_STATE_ERROR,
]

# --- Parametri di temporizzazione (spec R2, R3, R5) ---
# Timeout di attesa del report angolo dopo una fermata. Il DJT11LM pubblica
# tipicamente entro ~5 s dall'evento tilt; 12 s copre i casi lenti.
ANGLE_REPORT_TIMEOUT = 12.0
# Ritardo aggiuntivo dopo il primo cambio di stato: l'accelerometro puo'
# pubblicare due aggiornamenti ravvicinati, teniamo il piu' recente.
ANGLE_SETTLE_DELAY = 1.5
# Variazione angolare sotto la quale consideriamo l'aletta ferma (rumore).
ANGLE_NOISE_DEG = 2.0

# Durata di un singolo step della fase di ricerca estremi (fase A).
SEEK_STEP_DURATION = 2.0
# Limite di sicurezza: oltre questo numero di step la calibrazione aborta.
SEEK_MAX_STEPS = 20
# Numero di punti intermedi della curva tempo-angolo (fase B, spec R2).
CURVE_POINTS = 5
# Micro-corsa usata per determinare la direzione corrente (spec R3/R5).
PROBE_DURATION = 1.0
# Tetto di sicurezza su una singola sessione TCP di movimento (spec R3).
MAX_MOVE_DURATION = 25.0

# --- Verifica post-posizionamento (spec R5) ---
POSITION_TOLERANCE_DEG = 3.0
POSITION_RETRIES = 1

# --- Servizi (spec R6) ---
SERVICE_SET_FLAP_ANGLE = "set_flap_angle"
SERVICE_CALIBRATE_FLAP = "calibrate_flap"
SERVICE_HOME_FLAP = "home_flap"
ATTR_ANGLE = "angle"
