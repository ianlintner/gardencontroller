#include "net.h"
#include "config.h"
#include "arduino_secrets.h"

#include <WiFiS3.h>
#include <ArduinoHttpClient.h>
#include <ArduinoJson.h>
#include "Arduino_LED_Matrix.h"

static WiFiSSLClient tls;
static ArduinoLEDMatrix matrix;

// Minimal base64 encoder for the OAuth2 HTTP Basic auth header.
static String base64Encode(const String& in) {
  static const char* T = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  String out;
  int n = in.length();
  for (int i = 0; i < n; i += 3) {
    uint32_t b = (uint8_t)in[i] << 16;
    if (i + 1 < n) b |= (uint8_t)in[i + 1] << 8;
    if (i + 2 < n) b |= (uint8_t)in[i + 2];
    out += T[(b >> 18) & 0x3F];
    out += T[(b >> 12) & 0x3F];
    out += (i + 1 < n) ? T[(b >> 6) & 0x3F] : '=';
    out += (i + 2 < n) ? T[b & 0x3F] : '=';
  }
  return out;
}

// Cached OAuth2 access token.
static String accessToken;
static unsigned long tokenExpiresAtMs = 0;  // millis() when token goes stale

// ─── LED matrix display: growing plant (healthy) vs X (error) ────────────────
// 12x8 monochrome. Frames are ASCII art ('#' = lit) so they're easy to tweak —
// edit the art below to reshape the plant. Each row string is exactly 12 chars.

// A plant growing from a seed up to a bloom, looped slowly when healthy.
static const char PLANT[][8][13] = {
  { "            ", "            ", "            ", "            ",
    "            ", "            ", "            ", "     ##     " },  // seed
  { "            ", "            ", "            ", "            ",
    "            ", "            ", "     ##     ", "     ##     " },  // sprout
  { "            ", "            ", "            ", "            ",
    "            ", "     ##     ", "    ####    ", "     ##     " },  // first leaves
  { "            ", "            ", "            ", "            ",
    "     ##     ", "    ####    ", "     ##     ", "     ##     " },  // taller
  { "            ", "            ", "            ", "     ##     ",
    "   # ## #   ", "    ####    ", "     ##     ", "     ##     " },  // side leaves
  { "            ", "            ", "     ##     ", "   # ## #   ",
    "    ####    ", "   # ## #   ", "     ##     ", "     ##     " },  // bigger
  { "    ####    ", "   #    #   ", "    ####    ", "   # ## #   ",
    "    ####    ", "     ##     ", "     ##     ", "     ##     " },  // bloom
};
static const int PLANT_FRAMES = sizeof(PLANT) / sizeof(PLANT[0]);

// Shown when something is wrong (WiFi/auth/publish failure).
static const char X_GLYPH[8][13] = {
  "  #      #  ", "   #    #   ", "    #  #    ", "     ##     ",
  "     ##     ", "    #  #    ", "   #    #   ", "  #      #  ",
};

enum DispState { DISP_CONNECTING, DISP_HEALTHY, DISP_ERROR };
static DispState _disp = DISP_CONNECTING;
static int _frame = 0;
static unsigned long _lastFrameMs = 0;
static bool _needsRender = true;   // force a static re-render on state change

static void renderAscii(const char art[8][13]) {
  uint8_t f[8][12];
  for (int y = 0; y < 8; y++)
    for (int x = 0; x < 12; x++) f[y][x] = (art[y][x] != ' ') ? 1 : 0;
  matrix.renderBitmap(f, 8, 12);
}

static void setDisp(DispState s) {
  if (s == _disp) return;
  _disp = s;
  _frame = 0;
  _lastFrameMs = 0;
  _needsRender = true;
}

void netSetHealth(bool ok) { setDisp(ok ? DISP_HEALTHY : DISP_ERROR); }

void netDisplayTick() {
  unsigned long now = millis();
  if (_disp == DISP_HEALTHY) {
    // Advance the plant one frame every PLANT_FRAME_MS, then loop.
    if (_needsRender || now - _lastFrameMs >= PLANT_FRAME_MS) {
      _lastFrameMs = now;
      _needsRender = false;
      renderAscii(PLANT[_frame]);
      _frame = (_frame + 1) % PLANT_FRAMES;
    }
  } else if (_needsRender) {
    // Static glyphs; render once on entry to avoid flicker.
    renderAscii(_disp == DISP_ERROR ? X_GLYPH : PLANT[0]);
    _needsRender = false;
  }
}

void netBegin() {
  matrix.begin();
  setDisp(DISP_CONNECTING);
  netDisplayTick();   // show the seed while we connect
}

int netRssi() { return WiFi.RSSI(); }

bool netEnsureWifi() {
  if (WiFi.status() == WL_CONNECTED) return true;
  netSetHealth(false);
  Serial.print("WiFi connecting to "); Serial.println(SECRET_WIFI_SSID);
  WiFi.begin(SECRET_WIFI_SSID, SECRET_WIFI_PASS);
  // Bounded wait so the loop stays responsive; the loop retries next cycle.
  for (int i = 0; i < 20 && WiFi.status() != WL_CONNECTED; i++) delay(500);
  bool ok = WiFi.status() == WL_CONNECTED;
  if (ok) { Serial.print("WiFi up, IP="); Serial.println(WiFi.localIP()); }
  return ok;
}

// OAuth2 client-credentials grant against roauth2. Caches until ~60s before exp.
static bool ensureToken() {
  if (accessToken.length() && (long)(tokenExpiresAtMs - millis()) > 0) return true;

  HttpClient http(tls, OAUTH_TOKEN_HOST, HTTPS_PORT);
  // client_secret_basic: credentials go in the Authorization header, not the body.
  String basic = base64Encode(String(SECRET_OAUTH_CLIENT_ID) + ":" + SECRET_OAUTH_CLIENT_SECRET);
  String body = String("grant_type=client_credentials&audience=") + OAUTH_AUDIENCE
              + "&scope=" + OAUTH_SCOPE;

  http.beginRequest();
  http.post(OAUTH_TOKEN_PATH);
  http.sendHeader("Authorization", String("Basic ") + basic);
  http.sendHeader("Content-Type", "application/x-www-form-urlencoded");
  http.sendHeader("Content-Length", body.length());
  http.beginBody();
  http.print(body);
  http.endRequest();

  int code = http.responseStatusCode();
  String resp = http.responseBody();
  http.stop();
  if (code != 200) {
    Serial.print("token HTTP "); Serial.println(code);
    return false;
  }

  JsonDocument doc;
  if (deserializeJson(doc, resp)) { Serial.println("token parse error"); return false; }
  accessToken = (const char*)doc["access_token"];
  long expiresIn = doc["expires_in"] | 3600;
  tokenExpiresAtMs = millis() + (unsigned long)(expiresIn > 60 ? expiresIn - 60 : expiresIn) * 1000UL;
  Serial.println("token acquired");
  return accessToken.length() > 0;
}

static String buildPayload(const Reading& r) {
  JsonDocument doc;
  doc["device_id"] = DEVICE_ID;
  doc["location"]  = LOCATION;
  JsonObject rd = doc["readings"].to<JsonObject>();
  if (r.dhtOk) {
    rd["air_temperature_celsius"] = r.tempC;
    rd["air_humidity_percent"]    = r.humidity;
  }
  JsonArray soil = rd["soil"].to<JsonArray>();
  for (size_t i = 0; i < SOIL_PROBE_COUNT; i++) {
    JsonObject s = soil.add<JsonObject>();
    s["probe"]        = r.soil[i].probe;
    s["raw"]          = r.soil[i].raw;
    s["percent"]      = r.soil[i].percent;
  }
  rd["rain_raw"]      = r.rainRaw;
  rd["rain_percent"]  = r.rainPercent;
  rd["rain_detected"] = r.rainDetected;
  JsonObject board = doc["board"].to<JsonObject>();
  board["rssi_dbm"]       = netRssi();
  board["uptime_seconds"] = (long)(millis() / 1000UL);
  String out; serializeJson(doc, out); return out;
}

bool netPublish(const Reading& r) {
  if (!netEnsureWifi()) return false;
  if (!ensureToken())   { netSetHealth(false); return false; }

  HttpClient http(tls, INGEST_HOST, HTTPS_PORT);
  String body = buildPayload(r);

  http.beginRequest();
  http.post(INGEST_PATH);
  http.sendHeader("Content-Type", "application/json");
  http.sendHeader("Authorization", String("Bearer ") + accessToken);
  http.sendHeader("Content-Length", body.length());
  http.beginBody();
  http.print(body);
  http.endRequest();

  int code = http.responseStatusCode();
  http.stop();
  Serial.print("ingest HTTP "); Serial.println(code);

  if (code == 401 || code == 403) {       // token rejected → force refresh next time
    accessToken = ""; tokenExpiresAtMs = 0;
    netSetHealth(false);
    return false;
  }
  bool ok = code >= 200 && code < 300;
  netSetHealth(ok);   // ok → plant resumes growing; else → X
  return ok;
}
