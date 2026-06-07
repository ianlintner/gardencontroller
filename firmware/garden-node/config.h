// config.h — committed, non-secret configuration for one garden node.
// Secrets (WiFi password, OAuth client secret) live in arduino_secrets.h.
#pragma once

// ─── Identity ────────────────────────────────────────────────────────────────
// Unique per board. Becomes the Pushgateway grouping key (instance=<DEVICE_ID>)
// and the `device_id` label on every metric. Change this for board #2, #3, ...
#define DEVICE_ID "garden-node-1"
#define LOCATION  "raised-bed"   // human-friendly `location` label

// ─── Cloud endpoints ─────────────────────────────────────────────────────────
#define OAUTH_TOKEN_HOST "roauth2.cat-herding.net"
#define OAUTH_TOKEN_PATH "/oauth/token"   // from /.well-known/openid-configuration
#define OAUTH_AUDIENCE   "garden-ingest"
#define OAUTH_SCOPE      "garden:write"
// roauth2 clients use token_endpoint_auth_method=client_secret_basic, i.e. the
// client_id/secret go in an HTTP Basic Authorization header, NOT the form body.

#define INGEST_HOST "garden.cat-herding.net"
#define INGEST_PATH "/ingest"
#define HTTPS_PORT  443

// ─── Cadence (Phase 2 / contribution point #3) ───────────────────────────────
// Keep >= 30s: Pushgateway only retains the last value and Prometheus scrapes
// every 30s, so faster pushes are wasted. 60s is a good default for a garden.
#define SAMPLE_INTERVAL_MS 60000UL

// LED matrix plant animation: ms per growth frame (higher = slower growth).
// ~7 frames, so 900ms ≈ a 6s grow-and-loop cycle.
#define PLANT_FRAME_MS 900UL

// LED slideshow
#define SLIDE_MS    5000UL   // ms per slide (5 slides -> 25s cycle)
#define TEMP_MIN_C  0.0f     // temp bar range
#define TEMP_MAX_C  40.0f

// Live telemetry stream (USB serial NDJSON for the board-tui client)
#define TELEMETRY_MS         1000UL   // NDJSON serial frame interval (live view)
#define TELEMETRY_DHT_MIN_MS 2500UL   // DHT22 can't sustain 1Hz; re-read at most this often
#define TELEMETRY_TCP_PORT   8766     // TCP port for the network live view (LAN only)

// ─── OTA firmware update ──────────────────────────────────────────────────────
// Bump FIRMWARE_VERSION when cutting a release (scripts/release-firmware.sh).
#define FIRMWARE_VERSION "1.0.1"
#define OTA_VERSION_URL    "https://github.com/ianlintner/gardencontroller/releases/latest/download/version.txt"
#define OTA_BINARY_URL     "https://github.com/ianlintner/gardencontroller/releases/latest/download/garden-node.ota"
#define OTA_MAX_FAILURES   3      // skip OTA after this many consecutive failures
#define OTA_EEPROM_OFFSET  0      // byte address of the failure counter in virtual EEPROM
#define OTA_CHECK_INTERVAL_MS 300000UL  // periodic OTA poll (5 min) while running

// ─── Feature flags ───────────────────────────────────────────────────────────
// Phase 0 (bench bring-up) runs with uploads OFF: just read sensors and print
// to Serial so you can calibrate. Flip to 1 once net.cpp is wired (Phase 2).
#define ENABLE_UPLOAD 0

// ─── Pin map (UNO R4 WiFi) ───────────────────────────────────────────────────
#define DHT_PIN       7     // DHT22 data (digital, single-wire; 10k pull-up to 3V3)
#define DHT_TYPE      DHT22 // change to DHT11 if that's what the kit shipped
#define ENABLE_RAIN   0     // no rain sensor connected (A1 is a 2nd soil probe)
#define RAIN_PIN_A    A1    // (only used when ENABLE_RAIN=1)
#define RAIN_PIN_D    3     // (only used when ENABLE_RAIN=1)
#define SENSOR_POWER_PIN 4  // gates power to soil sensors (HIGH = on); -1 to disable

// Soil moisture probes. Add rows for more probes (one analog pin each: A0..A5).
// `probe` is the per-probe label; keep names short and stable.
struct SoilProbe { const char* probe; uint8_t pin; };
static const SoilProbe SOIL_PROBES[] = {
  { "bed1", A0 },
  { "bed2", A1 },
};
static const size_t SOIL_PROBE_COUNT = sizeof(SOIL_PROBES) / sizeof(SOIL_PROBES[0]);
