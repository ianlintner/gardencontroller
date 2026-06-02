# garden-node firmware (Arduino UNO R4 WiFi)

Reads garden sensors and pushes telemetry to `garden-ingest` over HTTPS,
authenticated with an OAuth2 client-credentials JWT from `roauth2.cat-herding.net`.

## Wiring (MVP)

| Sensor | Pin | Notes |
|---|---|---|
| DHT22 data | D2 | 10kΩ pull-up from data to 3V3 |
| Capacitive soil probe `bed1` | A0 | add more probes on A2..A5 in `config.h` |
| Rain-drop analog (AO) | A1 | intensity |
| Rain-drop digital (DO) | D3 | optional comparator threshold |
| Sensor power gate | D4 | drives VCC of soil/rain sensors (HIGH=on) via transistor/MOSFET |

> Prefer **capacitive** soil probes — resistive ones corrode. Power the analog
> sensors through D4 so they're only energized during a read (longevity + power).

## Libraries

Install via Arduino Library Manager: **ArduinoHttpClient**, **ArduinoJson**,
**DHT sensor library**. `WiFiS3` and `Arduino_LED_Matrix` ship with the UNO R4 core.

## Setup

1. `cp arduino_secrets.h.example arduino_secrets.h` and fill in WiFi + OAuth client.
2. Register the board's OAuth2 client in roauth2 (client-credentials grant,
   audience `garden-ingest`); put the client_id/secret in `arduino_secrets.h`.
3. Set `DEVICE_ID` / `LOCATION` in `config.h` (unique per board).
4. **HTTPS root CA**: the UNO R4 WiFi validates TLS against certs flashed to its
   ESP32 module. If the POSTs fail at TLS, add the `*.cat-herding.net` cert via the
   Arduino IDE *Tools → WiFi101 / WiFiNINA Firmware Updater → certificates* (R4
   equivalent), or via `arduino-cli`.

## Phases

- **Phase 0 (calibrate)**: keep `ENABLE_UPLOAD 0` in `config.h`. Flash, open
  Serial Monitor @115200, watch raw/percent values. Fill the calibration
  constants in `sensors.cpp` (see [../../docs/calibration.md](../../docs/calibration.md)).
- **Phase 2 (upload)**: set `ENABLE_UPLOAD 1`, reflash. The LED matrix shows
  status: dot=boot, X=WiFi down, !=auth fail, check=ok, full=pushing.

## Contribution points (you tune these)

1. `soilMoisturePercent()` — calibration mapping + smoothing (`sensors.cpp`).
2. `classifyRainDetected()` / `rainIntensityPercent()` — rain thresholds (`sensors.cpp`).
3. Publish cadence + backoff (`garden-node.ino` loop, `SAMPLE_INTERVAL_MS`).
