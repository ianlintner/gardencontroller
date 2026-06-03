#include "ota.h"
#include "config.h"
#include "display.h"
#include <WiFiS3.h>
#include <EEPROM.h>
#include <OTAUpdate.h>

// ─── EEPROM failure counter ───────────────────────────────────────────────────
// A single byte at OTA_EEPROM_OFFSET tracks consecutive OTA failures.
// 0xFF (erased flash) is treated as 0. After OTA_MAX_FAILURES the board stops
// trying OTA until USB-reflashed (which resets the counter to 0 via fresh flash).

static uint8_t readFailCount() {
    uint8_t v = EEPROM.read(OTA_EEPROM_OFFSET);
    return (v == 0xFF) ? 0 : v;            // 0xFF = blank flash = 0 failures
}

static void writeFailCount(uint8_t n) {
    // On Renesas (UNO R4 WiFi) the virtual EEPROM commits automatically on write.
    EEPROM.write(OTA_EEPROM_OFFSET, n);
}

static void resetFailCount() { writeFailCount(0); }

static void incrementFailCount() {
    uint8_t n = readFailCount();
    if (n < 255) writeFailCount(n + 1);
}

// Fetch a short text body from an HTTPS URL.
// Follows a single cross-host redirect (needed for GitHub releases/latest/download/).
static String fetchString(const char* url) {
    String u(url);
    // Try up to 2 hops (one redirect)
    for (int hop = 0; hop < 2; hop++) {
        int hostStart = u.indexOf("//") + 2;
        int pathStart = u.indexOf('/', hostStart);
        String host = u.substring(hostStart, pathStart);
        String path = (pathStart >= 0) ? u.substring(pathStart) : String("/");

        WiFiSSLClient client;
        if (!client.connect(host.c_str(), 443)) return "";
        client.print(String("GET ") + path + " HTTP/1.1\r\n" +
                     "Host: " + host + "\r\n" +
                     "User-Agent: Arduino-garden-node/1.0\r\n" +
                     "Connection: close\r\n\r\n");

        unsigned long t0 = millis();
        while (!client.available() && millis() - t0 < 10000) delay(10);

        // Read status line
        String statusLine = client.readStringUntil('\n');
        statusLine.trim();
        bool isRedirect = statusLine.indexOf(" 30") > 0;

        // Read headers, capture Location if redirect
        String location = "";
        while (client.available()) {
            String line = client.readStringUntil('\n');
            line.trim();
            if (line.length() == 0) break;   // blank line = end of headers
            if (isRedirect && line.startsWith("Location:")) {
                location = line.substring(9); location.trim();
            }
        }

        if (isRedirect && location.length() > 0) {
            client.stop();
            u = location;   // follow redirect
            continue;
        }

        // Read body
        String body = "";
        while (client.available()) body += (char)client.read();
        client.stop();
        body.trim();
        return body;
    }
    return "";  // too many redirects
}

// ─── OTA check + apply ────────────────────────────────────────────────────────
// Sequence: read fail count → fetch version.txt → compare → download/verify/apply
// Only network-hiccup (empty version fetch) skips incrementing the fail counter;
// all OTAUpdate error paths increment before returning false.
bool otaCheckAndApply() {
    uint8_t fails = readFailCount();
    if (fails >= OTA_MAX_FAILURES) {
        Serial.print("OTA: skipping — ");
        Serial.print(fails);
        Serial.println(" consecutive failures. Reflash via USB to reset.");
        return false;
    }

    Serial.println("OTA: checking version...");
    String latest = fetchString(OTA_VERSION_URL);
    if (latest.length() == 0) {
        Serial.println("OTA: version fetch failed — skipping");
        return false;           // network hiccup: don't count as OTA failure
    }
    Serial.print("OTA: current="); Serial.print(FIRMWARE_VERSION);
    Serial.print(" latest="); Serial.println(latest);
    if (latest == FIRMWARE_VERSION) {
        Serial.println("OTA: up to date");
        resetFailCount();       // clear any old partial-failure count
        return false;
    }

    Serial.println("OTA: update available, downloading...");
    OTAUpdate ota;
    int err;

    err = ota.begin();
    if (err != OTAUpdate::OTA_ERROR_NONE) {
        Serial.print("OTA: begin() failed: "); Serial.println(err);
        incrementFailCount(); return false;
    }

    err = ota.download(OTA_BINARY_URL);
    if (err != OTAUpdate::OTA_ERROR_NONE) {
        Serial.print("OTA: download() failed: "); Serial.println(err);
        incrementFailCount(); return false;
    }

    err = ota.verify();
    if (err != OTAUpdate::OTA_ERROR_NONE) {
        Serial.print("OTA: verify() failed: "); Serial.println(err);
        incrementFailCount(); return false;
    }

    Serial.println("OTA: applying — board will reboot");
    resetFailCount();           // applied successfully: clear failure count
    ota.update();               // reboots board; does not return on success
    // If update() somehow returns, treat as failure
    Serial.println("OTA: update() returned unexpectedly");
    incrementFailCount();
    return false;
}
