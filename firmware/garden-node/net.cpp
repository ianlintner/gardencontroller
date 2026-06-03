#include "net.h"
#include "config.h"
#include "arduino_secrets.h"
#include "display.h"

#include <WiFiS3.h>
#include <ArduinoHttpClient.h>
#include <ArduinoJson.h>

static WiFiSSLClient tls;

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

void netBegin() {
  // displayBegin() already called unconditionally in setup(); nothing to do here.
}

int netRssi() { return WiFi.RSSI(); }

bool netEnsureWifi() {
  if (WiFi.status() == WL_CONNECTED) return true;
  displaySetHealth(DISP_ERROR);
  if (WiFi.status() == WL_NO_MODULE) { Serial.println("WiFi module not found"); return false; }
  Serial.print("WiFi connecting to '"); Serial.print(SECRET_WIFI_SSID); Serial.println("'");
  WiFi.begin(SECRET_WIFI_SSID, SECRET_WIFI_PASS);
  for (int i = 0; i < 30 && WiFi.status() != WL_CONNECTED; i++) delay(500);  // up to 15s
  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("WiFi up, IP="); Serial.print(WiFi.localIP());
    Serial.print(" RSSI="); Serial.println(WiFi.RSSI());
    return true;
  }
  // Diagnostics: status code + what the (2.4GHz-only) radio can actually see.
  Serial.print("WiFi failed, status="); Serial.println(WiFi.status());
  int n = WiFi.scanNetworks();
  Serial.print("visible networks ("); Serial.print(n); Serial.println("):");
  for (int i = 0; i < n; i++) {
    Serial.print("  '"); Serial.print(WiFi.SSID(i));
    Serial.print("'  RSSI="); Serial.println(WiFi.RSSI(i));
  }
  return false;
}

// OAuth2 client-credentials grant against roauth2. Caches until ~60s before exp.
static bool ensureToken() {
  if (accessToken.length() && (long)(tokenExpiresAtMs - millis()) > 0) return true;

  HttpClient http(tls, OAUTH_TOKEN_HOST, HTTPS_PORT);
  http.setHttpResponseTimeout(15000);   // give TLS + response up to 15s
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
  if (code != 200) { Serial.print("token HTTP "); Serial.println(code); return false; }

  // roauth2 replies with Transfer-Encoding: chunked and ArduinoHttpClient does
  // not strip the chunk-size line, so the body looks like "387\r\n{json...}".
  // Parse from the first '{'; ArduinoJson ignores the trailing chunk markers.
  int brace = resp.indexOf('{');
  JsonDocument doc;
  DeserializationError perr = (brace >= 0)
      ? deserializeJson(doc, resp.c_str() + brace)
      : DeserializationError(DeserializationError::InvalidInput);
  if (perr) {
    Serial.print("token parse error: "); Serial.println(perr.c_str());
    return false;
  }
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
#if ENABLE_RAIN
  rd["rain_raw"]      = r.rainRaw;
  rd["rain_percent"]  = r.rainPercent;
  rd["rain_detected"] = r.rainDetected;
#endif
  JsonObject board = doc["board"].to<JsonObject>();
  board["rssi_dbm"]       = netRssi();
  board["uptime_seconds"] = (long)(millis() / 1000UL);
  String out; serializeJson(doc, out); return out;
}

bool netPublish(const Reading& r) {
  if (!netEnsureWifi()) return false;
  if (!ensureToken())   { displaySetHealth(DISP_ERROR); return false; }

  HttpClient http(tls, INGEST_HOST, HTTPS_PORT);
  http.setHttpResponseTimeout(15000);   // R4's first TLS handshake can be slow
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
    displaySetHealth(DISP_ERROR);
    return false;
  }
  bool ok = code >= 200 && code < 300;
  displaySetHealth(ok ? DISP_HEALTHY : DISP_ERROR);
  return ok;
}
