# Sensor calibration (Phase 0)

The UNO R4 ADC is set to **14-bit** in `sensorsBegin()`, so raw readings range
**0–16383**. Calibrate on the bench with `ENABLE_UPLOAD 0` and the Serial Monitor open.

## Soil moisture

Capacitive probes read **lower** raw values when wetter. Resistive probes are the
opposite — if yours go up when wet, swap the DRY/WET constants.

1. Hold the probe in **dry air** (or fully dry soil). Note the steady raw value → `SOIL_RAW_DRY`.
2. Submerge the sensing area in **water** to its max line. Note the raw value → `SOIL_RAW_WET`.
3. Put both into `sensors.cpp`:
   ```cpp
   static const int SOIL_RAW_DRY = <your dry reading>;
   static const int SOIL_RAW_WET = <your wet reading>;
   ```
4. Decide smoothing. `readAdc()` already takes a median of 5; increase the window
   if readings are jumpy (trade-off: responsiveness vs. noise).

## Rain-drop sensor

Also reads **lower** when wet (more conductive).

1. Plate bone dry → `RAIN_RAW_DRY`.
2. Plate soaked (sprinkle water across it) → `RAIN_RAW_WET`.
3. Choose `RAIN_DETECT_PCT` — the intensity at which you call it "raining". Start
   at 20 and adjust after watching real readings.

## Sanity check

After setting constants, reflash and confirm in Serial:
- dry soil → ~0%, wet soil → ~100%
- dry plate → `detected=0`, wet plate → `detected=1`
- DHT temp/humidity look plausible for the room
