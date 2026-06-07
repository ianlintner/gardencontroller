// telemetry.cpp — ~1 Hz NDJSON frame: all analog pins, translated sensors,
// board/net metrics. Decoupled from the 60s cloud sample.
#include "telemetry.h"
#include "config.h"
#include "sensors.h"
#include <ArduinoJson.h>
#if ENABLE_UPLOAD || ENABLE_TCP_VIEW
#include <WiFiS3.h>
#if ENABLE_UPLOAD
#include "net.h"
#endif
#endif

static unsigned long s_lastFrameMs = 0;
static unsigned long s_lastDhtMs = 0;
static float s_tempC = NAN, s_hum = NAN;
static bool  s_dhtOk = false;
static const char* s_push = "n/a";

#if ENABLE_UPLOAD || ENABLE_TCP_VIEW
static WiFiServer s_server(TELEMETRY_TCP_PORT);
static WiFiClient s_client;
#endif

// Approx free RAM on the RA4M1 (newlib): gap between heap end and stack top.
extern "C" char* sbrk(int incr);
static int freeRamBytes() {
  char top;
  return (int)(&top - reinterpret_cast<char*>(sbrk(0)));
}

void telemetryBegin() {
  analogReadResolution(14);   // 0..16383, matches soil calibration
  s_lastFrameMs = millis() - TELEMETRY_MS;
#if ENABLE_UPLOAD || ENABLE_TCP_VIEW
  s_server.begin();
#endif
}

void telemetrySetPush(const char* status) { s_push = status ? status : "n/a"; }

void telemetryTick() {
  unsigned long now = millis();
  if (now - s_lastFrameMs < TELEMETRY_MS) return;
  s_lastFrameMs = now;

  // DHT throttled refresh (cached between frames)
  if (now - s_lastDhtMs >= TELEMETRY_DHT_MIN_MS) {
    s_lastDhtMs = now;
    float t, h;
    if (sensorsReadDht(t, h)) { s_tempC = t; s_hum = h; s_dhtOk = true; }
    else { s_dhtOk = false; }
  }

  // Read all 6 analog pins once, with soil sensors energized (D4 gates power).
  const uint8_t pinNums[6] = {A0, A1, A2, A3, A4, A5};
  const char* pinNames[6] = {"A0", "A1", "A2", "A3", "A4", "A5"};
  int rawByPin[6];
  if (SENSOR_POWER_PIN >= 0) { digitalWrite(SENSOR_POWER_PIN, HIGH); delay(50); }
  for (int i = 0; i < 6; i++) rawByPin[i] = analogRead(pinNums[i]);
  if (SENSOR_POWER_PIN >= 0) digitalWrite(SENSOR_POWER_PIN, LOW);

  JsonDocument doc;
  doc["t"] = now;
  doc["fw"] = FIRMWARE_VERSION;
  doc["dev"] = DEVICE_ID;

  JsonObject pins = doc["pins"].to<JsonObject>();
  for (int i = 0; i < 6; i++) pins[pinNames[i]] = rawByPin[i];

  JsonObject sensors = doc["sensors"].to<JsonObject>();
  sensors["tempC"] = s_tempC;
  sensors["hum"] = s_hum;
  sensors["dhtOk"] = s_dhtOk;
  JsonArray soil = sensors["soil"].to<JsonArray>();
  for (size_t i = 0; i < SOIL_PROBE_COUNT; i++) {
    // reuse the raw we already read for this probe's pin (keeps pins.* and soil consistent)
    int raw = analogRead(SOIL_PROBES[i].pin);  // fallback if not in pinNums
    for (int j = 0; j < 6; j++) if (pinNums[j] == SOIL_PROBES[i].pin) { raw = rawByPin[j]; break; }
    JsonObject o = soil.add<JsonObject>();
    o["probe"] = SOIL_PROBES[i].probe;
    o["pin"] = (SOIL_PROBES[i].pin == A1) ? "A1" : "A0";
    o["raw"] = raw;
    o["pct"] = soilMoisturePercent(raw);
  }

  JsonObject net = doc["net"].to<JsonObject>();
#if ENABLE_UPLOAD || ENABLE_TCP_VIEW
  bool up = (WiFi.status() == WL_CONNECTED);
  net["wifi"] = up ? "up" : "down";
  net["rssi"] = up ? (int)WiFi.RSSI() : 0;
  if (up) {
    IPAddress ip = WiFi.localIP();
    char b[16];
    snprintf(b, sizeof(b), "%u.%u.%u.%u", ip[0], ip[1], ip[2], ip[3]);
    net["ip"] = b;
  } else {
    net["ip"] = "0.0.0.0";
  }
  net["push"] = s_push;   // "n/a" in view-only mode; "ok"/"fail" in upload mode
#else
  net["wifi"] = "off";
  net["rssi"] = 0;
  net["ip"] = "0.0.0.0";
  net["push"] = "n/a";
#endif

  JsonObject board = doc["board"].to<JsonObject>();
  board["up_s"] = now / 1000UL;
  board["heap_b"] = freeRamBytes();

  // Serialise once into a fixed stack buffer, then write to Serial and TCP client.
  char buf[512];
  size_t n = serializeJson(doc, buf, sizeof(buf));
  if (n == 0 || n >= sizeof(buf)) {
    // Truncation guard: fall back to direct Serial write and skip TCP.
    serializeJson(doc, Serial);
    Serial.println();
    return;
  }

  Serial.println(buf);

#if ENABLE_UPLOAD || ENABLE_TCP_VIEW
  // Newest-wins: if a new client has connected, replace the current one.
  WiFiClient incoming = s_server.available();
  if (incoming) {
    s_client.stop();
    s_client = incoming;
  }
  // Drain any bytes the client sent (read-only stream; we don't parse commands).
  while (s_client && s_client.available()) s_client.read();
  // Write the frame to the connected client, if any.
  if (s_client && s_client.connected()) {
    s_client.println(buf);
  }
#endif
}
