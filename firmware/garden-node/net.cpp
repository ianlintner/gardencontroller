#include "net.h"
#include "config.h"
#include "arduino_secrets.h"

#include <WiFiS3.h>
#include <ArduinoHttpClient.h>
#include <ArduinoJson.h>
#include "Arduino_LED_Matrix.h"

static WiFiSSLClient tls;
static ArduinoLEDMatrix matrix;

// Cached OAuth2 access token.
static String accessToken;
static unsigned long tokenExpiresAtMs = 0;  // millis() when token goes stale

// ─── LED matrix status frames (12x8) ─────────────────────────────────────────
// Minimal glyphs: a dot=boot, "x"=wifi down, "!"=auth fail, check=ok, full=push.
static void fill(uint8_t frame[8][12], bool on) {
  for (int y = 0; y < 8; y++) for (int x = 0; x < 12; x++) frame[y][x] = on;
}

void netShowStatus(NetStatus s) {
  uint8_t f[8][12]; fill(f, false);
  switch (s) {
    case NET_BOOT:      f[3][5] = f[3][6] = f[4][5] = f[4][6] = 1; break;       // center dot
    case NET_WIFI_DOWN: for (int i = 0; i < 8; i++) { f[i][i+2] = 1; f[i][9-i] = 1; } break; // X
    case NET_AUTH_FAIL: for (int i = 0; i < 6; i++) f[i][6] = 1; f[7][6] = 1; break;          // !
    case NET_OK:        f[5][3]=f[6][4]=f[5][5]=f[4][6]=f[3][7]=f[2][8]=1; break;             // check
    case NET_PUSHING:   fill(f, true); break;
  }
  matrix.renderBitmap(f, 8, 12);
}

void netBegin() {
  matrix.begin();
  netShowStatus(NET_BOOT);
}

int netRssi() { return WiFi.RSSI(); }

bool netEnsureWifi() {
  if (WiFi.status() == WL_CONNECTED) return true;
  netShowStatus(NET_WIFI_DOWN);
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
  String body = String("grant_type=client_credentials")
              + "&client_id="     + SECRET_OAUTH_CLIENT_ID
              + "&client_secret=" + SECRET_OAUTH_CLIENT_SECRET
              + "&audience="      + OAUTH_AUDIENCE;

  http.beginRequest();
  http.post(OAUTH_TOKEN_PATH);
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
  if (!ensureToken())   { netShowStatus(NET_AUTH_FAIL); return false; }

  netShowStatus(NET_PUSHING);
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
    netShowStatus(NET_AUTH_FAIL);
    return false;
  }
  bool ok = code >= 200 && code < 300;
  netShowStatus(ok ? NET_OK : NET_WIFI_DOWN);
  return ok;
}
