#include "sensors.h"
#include <DHT.h>

static DHT dht(DHT_PIN, DHT_TYPE);

// Read the UNO R4's 14-bit ADC (0..16383). We bump resolution in begin().
static int readAdc(uint8_t pin) {
  // Median of 5 reads to shrug off electrical noise on long sensor leads.
  int v[5];
  for (int i = 0; i < 5; i++) { v[i] = analogRead(pin); delay(5); }
  for (int i = 0; i < 4; i++)
    for (int j = i + 1; j < 5; j++)
      if (v[j] < v[i]) { int t = v[i]; v[i] = v[j]; v[j] = t; }
  return v[2];
}

void sensorsBegin() {
  analogReadResolution(14);            // UNO R4: 14-bit ADC (0..16383)
  dht.begin();
  if (SENSOR_POWER_PIN >= 0) {
    pinMode(SENSOR_POWER_PIN, OUTPUT);
    digitalWrite(SENSOR_POWER_PIN, LOW);  // off until a read
  }
  pinMode(RAIN_PIN_D, INPUT);
}

Reading sensorsRead() {
  Reading r{};

  // Energize analog sensors only during the read (longevity + power).
  if (SENSOR_POWER_PIN >= 0) { digitalWrite(SENSOR_POWER_PIN, HIGH); delay(50); }

  r.humidity = dht.readHumidity();
  r.tempC    = dht.readTemperature();
  r.dhtOk    = !(isnan(r.humidity) || isnan(r.tempC));

  for (size_t i = 0; i < SOIL_PROBE_COUNT; i++) {
    int raw = readAdc(SOIL_PROBES[i].pin);
    r.soil[i] = { SOIL_PROBES[i].probe, raw, soilMoisturePercent(raw) };
  }

  r.rainRaw      = readAdc(RAIN_PIN_A);
  r.rainPercent  = rainIntensityPercent(r.rainRaw);
  r.rainDetected = classifyRainDetected(r.rainRaw);

  if (SENSOR_POWER_PIN >= 0) digitalWrite(SENSOR_POWER_PIN, LOW);
  return r;
}

void sensorsPrint(const Reading& r) {
  Serial.print("DHT ok="); Serial.print(r.dhtOk);
  Serial.print(" temp="); Serial.print(r.tempC);
  Serial.print("C hum="); Serial.print(r.humidity); Serial.println("%");
  for (size_t i = 0; i < SOIL_PROBE_COUNT; i++) {
    Serial.print("  soil["); Serial.print(r.soil[i].probe);
    Serial.print("] raw="); Serial.print(r.soil[i].raw);
    Serial.print(" pct="); Serial.println(r.soil[i].percent);
  }
  Serial.print("  rain raw="); Serial.print(r.rainRaw);
  Serial.print(" pct="); Serial.print(r.rainPercent);
  Serial.print(" detected="); Serial.println(r.rainDetected);
}

// ─────────────────────────────────────────────────────────────────────────────
// CONTRIBUTION POINTS — tune these on the bench in Phase 0.
// These compile-ready defaults assume a CAPACITIVE soil probe (raw goes DOWN as
// it gets wetter) and a typical analog rain module (raw goes DOWN when wet).
// Measure your own dry/wet endpoints and replace the constants. See
// docs/calibration.md for the procedure.
// ─────────────────────────────────────────────────────────────────────────────

// Calibrated 2026-06 for the kit capacitive probe at 5V (dry=high, wet=low).
// Measured: dry towel/air ~16374 (rails near the 14-bit max at 5V), wet towel
// ~11000. map() handles the high→low direction. Re-measure in real soil to
// refine; powering at 3.3V instead of 5V would avoid the dry-end ADC railing.
static const int SOIL_RAW_DRY = 16374;
static const int SOIL_RAW_WET = 11000;

float soilMoisturePercent(int raw) {
  long pct = map((long)raw, SOIL_RAW_DRY, SOIL_RAW_WET, 0, 100);
  if (pct < 0)   pct = 0;
  if (pct > 100) pct = 100;
  return (float)pct;
}

// TODO(you) #2: calibrate rain. RAIN_RAW_DRY = plate bone dry,
// RAIN_RAW_WET = plate soaked. RAIN_DETECT_PCT = intensity to call it "raining".
static const int   RAIN_RAW_DRY     = 14000; // ← measure me
static const int   RAIN_RAW_WET     = 4000;  // ← measure me
static const float RAIN_DETECT_PCT  = 20.0f; // ← tune me

float rainIntensityPercent(int raw) {
  long pct = map((long)raw, RAIN_RAW_DRY, RAIN_RAW_WET, 0, 100);
  if (pct < 0)   pct = 0;
  if (pct > 100) pct = 100;
  return (float)pct;
}

bool classifyRainDetected(int raw) {
  return rainIntensityPercent(raw) >= RAIN_DETECT_PCT;
}
