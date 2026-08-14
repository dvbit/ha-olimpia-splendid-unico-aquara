# Olimpia Splendid Unico

[![HACS Validation](https://github.com/dvbit/ha-olimpia-splendid-unico-aquara/actions/workflows/validate.yml/badge.svg)](https://github.com/dvbit/ha-olimpia-splendid-unico-aquara/actions/workflows/validate.yml)
[![Hassfest](https://github.com/dvbit/ha-olimpia-splendid-unico-aquara/actions/workflows/hassfest.yml/badge.svg)](https://github.com/dvbit/ha-olimpia-splendid-unico-aquara/actions/workflows/hassfest.yml)

**[Versione italiana / Italian version](README.it.md)**

> Fork of [Daneel87/ha-olimpia-splendid-unico](https://github.com/Daneel87/ha-olimpia-splendid-unico), extended with **closed-loop flap position control** using an external Aqara DJT11LM tilt sensor.

Custom [Home Assistant](https://www.home-assistant.io/) integration for **Olimpia Splendid Unico** air conditioners via **local TCP** control (no cloud required). Optional BLE setup for initial pairing and WiFi configuration.

> [!WARNING]
> **This integration only works with units equipped with the B1015 WiFi board** — the one managed by the *Olimpia Splendid Unico* mobile app, which advertises itself as **"OL01"** over Bluetooth. Units with a different or newer WiFi module (for example those managed by a different Olimpia Splendid app) use a different protocol and **will not be detected**. See [Compatibility](#compatibility) below.

## Features

- **HVAC modes**: Heat, Cool, Dry, Fan Only, Auto
- **Fan speed**: Low, Medium, High, Auto
- **Swing**: stateless toggle button (the firmware does not report flap state, so the integration mirrors the official app's toggle-button design)
- **Flap positioning** (optional, v0.2.0): set the flap to any angle between its mechanical limits, using an [Aqara DJT11LM](https://www.zigbee2mqtt.io/devices/DJT11LM.html) vibration/tilt sensor glued to the flap — with automatic, repeatable calibration
- **Target temperature** control
- **Room temperature** reading
- **Scheduler** switch entity
- **Local polling** (30s) with automatic reconnect and keepalive
- **BLE setup flow**: scan, ECDH pairing, WiFi provisioning — all from the HA config UI
- **Manual IP** setup with credential paste or file import

## Requirements

- Home Assistant **2024.8.0** or newer
- The Unico unit must be on the same LAN as your HA instance (TCP port 2000)
- For BLE setup: a Bluetooth adapter accessible to HA

## Installation

### HACS (recommended)

1. Open HACS in Home Assistant
2. Click the three-dot menu > **Custom repositories**
3. Add `https://github.com/dvbit/ha-olimpia-splendid-unico-aquara` with category **Integration**
4. Search for "Olimpia Splendid Unico" and install
5. Restart Home Assistant

### Manual

1. Copy `custom_components/olimpia_splendid/` into your HA `custom_components/` directory
2. Restart Home Assistant

## Configuration

Go to **Settings > Devices & Services > Add Integration** and search for **Olimpia Splendid Unico**. You will be offered two setup paths:

### Option A: BLE Setup (recommended)

The easiest method if your HA instance has access to a Bluetooth adapter.

1. Choose **"New device — BLE setup"**
2. The integration scans for nearby Unico devices via Bluetooth
3. Select your device from the list (it appears as "OL01")
4. Enter:
   - **Device PIN**: printed on a label on the unit (default: `12345678`)
   - **WiFi SSID**: your network name
   - **WiFi Password**: your network password
5. Wait for pairing to complete (up to 60 seconds)
6. The integration automatically discovers the device IP — done!

### Option B: External BLE Pairing + Manual IP

Use this method when HA does not have Bluetooth access (VM, Docker without BT passthrough, remote machine, etc.).

#### Step 1: Run the BLE pairing tool

On a machine with a Bluetooth adapter (laptop, Raspberry Pi, etc.):

```bash
# Clone the repository (or download just the tools/ directory)
git clone https://github.com/dvbit/ha-olimpia-splendid-unico-aquara.git
cd ha-olimpia-splendid-unico-aquara/tools

# Install dependencies
pip install -r requirements.txt

# Scan for your device
python olimpia_ble.py scan

# Run full setup (pairing + WiFi)
python olimpia_ble.py setup <MAC_ADDRESS> --pin <PIN> --ssid "YourWiFi" --password "YourPassword"
```

On success, the tool saves credentials to `~/.olimpia/<IP>.json` and prints the device IP.

See [tools/README.md](tools/README.md) for the full command reference.

#### Step 2: Add the integration in HA

1. Go to **Settings > Devices & Services > Add Integration > Olimpia Splendid Unico**
2. Choose **"Configured device (enter IP)"**
3. Enter the device IP address
4. **Paste the credentials JSON** (recommended): open `~/.olimpia/<IP>.json` from the machine where you ran the tool, copy its full contents, and paste it into the "Credentials JSON" field
5. Alternatively, leave the credentials field empty if you've copied the file to the HA machine (see [tools/README.md](tools/README.md#method-2-copy-file-to-ha-machine) for paths)

### Post-setup

- **Assign a static IP** to the device via your router's DHCP reservation. This prevents the IP from changing and the integration losing contact with the unit.
- Verify the integration is working: check that the climate entity shows the current room temperature.

## Troubleshooting

### BLE scan finds no devices

- Ensure the Bluetooth adapter is working: `hcitool dev` should list it
- Move closer to the unit (BLE range is ~10m)
- The device appears as "OL01" — try `python olimpia_ble.py scan --name OL01`
- Some adapters need a reset: `sudo hciconfig hci0 reset`

### BLE pairing fails

- Check the PIN (printed on the unit label, default: `12345678`)
- The device allows limited concurrent users — try a factory reset of the WiFi board if needed
- Use `-v` for verbose output to diagnose which pairing step fails
- Retry: BLE can be flaky with weak signal, the tool retries automatically up to 3 times

### "No credentials found" on Manual IP

- Ensure you ran the BLE pairing tool successfully first
- Paste the JSON content from `~/.olimpia/<IP>.json` into the "Credentials JSON" field
- If loading from disk: the file must be at `~/.olimpia/<IP>.json` on the machine running HA (where `~` is the home directory of the HA process — `/root/` for HAOS/Docker)

### Device shows as unavailable

- Check that the device is on the same network and reachable: `ping <IP>`
- TCP port 2000 must be accessible
- After a router reboot, the device may get a new IP — update the integration or set up DHCP reservation

## Compatibility

Tested on **Olimpia Splendid Unico Pro** with **B1015 WiFi board**. Should work with all Unico models equipped with the same B1015 board (same protocol and app — Olimpia Splendid Unico v1.0.9).

**How to check if your unit is compatible:**

- Your unit is controlled by the **"Olimpia Splendid Unico"** mobile app (not a different Olimpia Splendid app)
- In pairing mode, the unit advertises over Bluetooth as **"OL01"**

If neither applies, your unit uses a different WiFi module and protocol, and this integration will not work with it — regardless of the model name being "Unico".

## Flap position control (optional)

The Unico exposes a single **stateless toggle** for the flap: switching it on
starts a continuous oscillation between the two mechanical limits, switching it
off stops the flap **exactly where it is**. The unit never reports the flap
angle. By gluing an [Aqara DJT11LM](https://www.zigbee2mqtt.io/devices/DJT11LM.html)
vibration/tilt sensor to the flap, the integration can measure the angle and
drive the flap to an arbitrary position.

### How it works — and why it is time-based

The DJT11LM **does not stream** its orientation: the `angle_x` / `angle_y` /
`angle_z` values are refreshed only a few seconds *after* a `tilt` event, and
`vibration` actions are rate-limited to roughly one per minute. Reading the
angle while the flap is moving is therefore impossible.

The integration works around this with an **open-loop, time-based model**:

1. **Calibration** measures the two limit angles and the full travel time `T`,
   and samples a `time → angle` curve (the relationship is generally not
   linear).
2. **Positioning** models the flap as a **triangular wave** of period `2·T`.
   Given the current position and travel direction, the shortest move duration
   is computed in closed form — bounces at the limits are handled natively, so
   there is no such thing as a "wrong direction".
3. **Verification**: after every stop the angle is read back and the model is
   re-synchronised against the sensor, which is treated as the authoritative
   source. If the residual error exceeds ±3°, one corrective move is attempted.

### Setup

1. Glue the DJT11LM to the flap so that one of its axes tracks the rotation.
   Pair it (Zigbee2MQTT or ZHA) **before** configuring this integration.
2. During the config flow, the **"Flap tilt sensor"** step asks for the sensor
   device and the axis to monitor. Leave it empty to skip — everything else
   keeps working, only the blind swing toggle will be available.
3. Open **Settings → Devices & Services → Olimpia Splendid Unico → Configure →
   Calibrate the flap**.

> [!IMPORTANT]
> Calibration takes roughly **3 to 5 minutes** and moves the flap repeatedly.
> The air conditioner must be **on**. Do not operate the unit meanwhile.
> Calibration can be repeated at any time, from the options or with the
> `Calibrate flap` button entity.

**Which axis?** Watch the sensor entities in Developer Tools while moving the
flap by hand: pick the axis with the largest excursion. If the reported
percentage runs backwards (100 % when the flap is closed), enable
**"Invert direction"** in the options.

### Entities

| Entity | Type | Notes |
| --- | --- | --- |
| `cover.<device>_flap` | Cover (damper) | Tilt 0–100 %, open/close/stop/set tilt |
| `sensor.<device>_flap_angle` | Sensor (°) | Mirrors the tilt sensor; attributes: `position`, `source_entity` |
| `sensor.<device>_flap_calibration` | Sensor (diagnostic) | `uncalibrated` / `calibrating` / `calibrated` / `error` |
| `switch.<device>_continuous_swing` | Switch | Continuous oscillation |
| `button.<device>_calibrate_flap` | Button (config) | Re-runs calibration |
| `button.<device>_toggle_swing` | Button | Original blind toggle, always available |

These flap entities are created **only** when the tilt sensor is configured.

### Services

| Service | Fields | Description |
| --- | --- | --- |
| `olimpia_splendid.set_flap_angle` | `angle` (°) | Moves the flap to an absolute angle, clamped to the calibrated limits |
| `olimpia_splendid.calibrate_flap` | — | Runs the automatic calibration |
| `olimpia_splendid.home_flap` | — | Re-synchronises the known position |

### Usage examples

Set the flap half-open:

```yaml
action: cover.set_cover_tilt_position
target:
  entity_id: cover.olimpia_splendid_unico_flap
data:
  tilt_position: 50
```

Move to an absolute angle:

```yaml
action: olimpia_splendid.set_flap_angle
target:
  entity_id: cover.olimpia_splendid_unico_flap
data:
  angle: 12.5
```

Point the flap upwards in heating and downwards in cooling:

```yaml
automation:
  - alias: Flap follows HVAC mode
    triggers:
      - trigger: state
        entity_id: climate.olimpia_splendid_unico
        attribute: hvac_action
    conditions:
      - condition: not
        conditions:
          - condition: state
            entity_id: climate.olimpia_splendid_unico
            state: "off"
    actions:
      - action: cover.set_cover_tilt_position
        target:
          entity_id: cover.olimpia_splendid_unico_flap
        data:
          tilt_position: >-
            {{ 20 if is_state_attr('climate.olimpia_splendid_unico',
                                   'hvac_action', 'heating') else 80 }}
    mode: single
```

Re-calibrate every six months, at night:

```yaml
automation:
  - alias: Periodic flap calibration
    triggers:
      - trigger: time
        at: "03:30:00"
    conditions:
      - condition: template
        value_template: "{{ now().day == 1 and now().month in [1, 7] }}"
      - condition: state
        entity_id: climate.olimpia_splendid_unico
        state: fan_only
    actions:
      - action: olimpia_splendid.calibrate_flap
        target:
          entity_id: cover.olimpia_splendid_unico_flap
    mode: single
```

Notify when the flap drifts out of tolerance:

```yaml
automation:
  - alias: Flap calibration error
    triggers:
      - trigger: state
        entity_id: sensor.olimpia_splendid_unico_flap_calibration
        to: error
    actions:
      - action: notify.persistent_notification
        data:
          message: Flap calibration failed — check the tilt sensor battery.
    mode: single
```

### Limitations

- **The tilt sensor is the bottleneck.** Every stop costs 5–12 s of waiting for
  the angle report. A single positioning command therefore takes 10–30 s.
- **No live feedback during movement**: the angle sensor only updates when the
  flap stops.
- **After a Home Assistant restart** the position is unknown. The first flap
  command triggers an automatic homing (~30–40 s): the integration observes the
  sensor to detect whether the flap is oscillating, stops it if needed, then
  performs a 1 s probe move to establish the travel direction.
- **Positioning is blocked while the air conditioner is off** — the flap does
  not respond to commands in that state.
- Turning on **Continuous swing** invalidates the known position; it is
  re-established on the next positioning command.
- Expect a typical accuracy of **1–3°**. Occasional larger errors are detected
  via a model-drift check and corrected on the next command.

## Logging

Enable detailed logs in `configuration.yaml`:

```yaml
logger:
  default: warning
  logs:
    custom_components.olimpia_splendid: debug
```

- `DEBUG` — every toggle, measured travel time, angle reading, calibration step
- `INFO` — calibration start/end and results, homing, successful positioning
- `WARNING` — missing angle report, degraded TCP session, position out of
  tolerance, model drift
- `ERROR` — calibration aborted

## Specification

This is the consolidated requirement the flap feature was built from (v0.2.0).

**R1 — Configuration.** A new, skippable config-flow step asks for the tilt
sensor *device* and the *axis* (`x`/`y`/`z`) to monitor; the corresponding
`angle_<axis>` entity is resolved automatically. If skipped, the integration
behaves as in v0.1.x.

**R2 — Automatic calibration, repeatable.** Two phases, all readings taken with
the flap at rest:
*Phase A (seek)* — fixed-duration steps with an angle reading after each stop,
until two travel reversals are observed; yields `angle_min`, `angle_max` and a
coarse travel-time estimate.
*Phase B (curve)* — park at the lower limit, then 5 equally-spaced timed moves
recording `(time, angle)` pairs; refines the limits and travel time and stores a
piecewise-linear curve.
Triggerable from the options flow, the `Calibrate flap` button and the
`calibrate_flap` service. Results are persisted in the config entry options.

**R3 — Position model.** Triangular wave of period `2·T`, re-synchronised
against the sensor at each stop. Homing is performed lazily at the first
command after a restart.

**R4 — Entities.** Cover (`damper`, tilt only), angle sensor, diagnostic
calibration sensor, continuous-swing switch, calibrate button.

**R5 — Positioning.** Post-stop verification with a **±3°** tolerance and **one**
corrective retry; blocked when the climate entity is off.

**R6 — Services.** `set_flap_angle`, `calibrate_flap`, `home_flap`.

**R7 — Logging.** Meaningful messages at DEBUG / INFO / WARNING / ERROR.

**R8 — Delivery.** HACS-compatible layout, translations in EN/IT/FR/ES/DE,
README in English and Italian.

## Credits

Original integration by [@Daneel87](https://github.com/Daneel87). Flap position
control and tilt-sensor calibration by [@dvbit](https://github.com/dvbit).

## Protocol Documentation

The BLE and WiFi protocol is documented in [PROTOCOL_BLE_WIFI.md](PROTOCOL_BLE_WIFI.md) for those interested in the technical details.

## License

MIT
