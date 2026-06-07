// garden-node.ino — Arduino UNO R4 WiFi garden telemetry node.
//
// Phase 0 (ENABLE_UPLOAD=0 in config.h): read sensors, print to Serial, calibrate.
// Phase 2 (ENABLE_UPLOAD=1): also OAuth2 + HTTPS POST readings to garden-ingest.
//
// Required libraries (Library Manager): "ArduinoHttpClient", "ArduinoJson",
// "DHT sensor library". WiFiS3 and Arduino_LED_Matrix ship with the R4 core.
//
// NOTE: HTTPS to *.cat-herding.net needs the server's root CA present in the
// UNO R4 WiFi's ESP32 cert bundle. If TLS fails, add the cert via the Arduino
// IDE "WiFi firmware / certificates updater" tool (see README).

#include "config.h"
#include "sensors.h"
#include "display.h"
#include "telemetry.h"
#if ENABLE_UPLOAD || ENABLE_TCP_VIEW
#include "net.h"
#endif
#if ENABLE_UPLOAD
#include "ota.h"
#endif

static unsigned long lastSampleMs = 0;
#if ENABLE_UPLOAD
static unsigned long lastOtaCheckMs = 0;
#endif

void setup() {
  displayBegin();
  Serial.begin(115200);
  unsigned long t0 = millis();
  while (!Serial && millis() - t0 < 3000) {}  // wait briefly for USB serial
  Serial.println("garden-node booting: " DEVICE_ID " @ " LOCATION);

  sensorsBegin();
#if ENABLE_UPLOAD || ENABLE_TCP_VIEW
  netBegin();
  netEnsureWifi();   // connect WiFi; TCP view and/or cloud push need it
#if ENABLE_UPLOAD
  otaCheckAndApply();   // checks version, applies + reboots if update available
  lastOtaCheckMs = millis();
#endif
#endif
  telemetryBegin();  // starts TCP server after WiFi is up
  lastSampleMs = millis() - SAMPLE_INTERVAL_MS;  // sample immediately on boot
}

void loop() {
  displayTick();   // advance the LED matrix animation every iteration (non-blocking)
  telemetryTick(); // ~1 Hz NDJSON frame over Serial for board-tui (non-blocking)

#if ENABLE_UPLOAD
  // Periodic OTA poll: pick up a published release without a manual reboot.
  if (millis() - lastOtaCheckMs >= OTA_CHECK_INTERVAL_MS) {
    lastOtaCheckMs = millis();
    if (netEnsureWifi()) {
      otaCheckAndApply();   // applies + reboots if a newer version is published
    }
  }
#endif

  if (millis() - lastSampleMs < SAMPLE_INTERVAL_MS) return;
  lastSampleMs = millis();

  Reading r = sensorsRead();
  sensorsPrint(r);
  displaySetReadings(r.soil, SOIL_PROBE_COUNT, r.tempC, r.humidity, r.dhtOk);

#if !ENABLE_UPLOAD
  displaySetHealth(DISP_HEALTHY);   // bench: show the slideshow without networking
#endif

#if ENABLE_UPLOAD
  // TODO(you) #3: publish cadence + backoff policy.
  // Default: one attempt per sample interval; on failure we simply wait for the
  // next cycle. Consider exponential backoff, a bounded retry burst, or storing
  // unsent readings while offline. Trade-off: data completeness vs. power/network.
  bool ok = netPublish(r);
  telemetrySetPush(ok ? "ok" : "fail");
  if (!ok) Serial.println("publish failed; will retry next cycle");
#endif
}
