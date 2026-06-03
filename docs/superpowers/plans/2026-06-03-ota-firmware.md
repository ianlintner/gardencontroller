# OTA Firmware Updates — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable the garden node (UNO R4 WiFi) to update its own firmware at boot by pulling a `.ota` binary from a GitHub Release, with an EEPROM failure counter preventing retry loops on bad flashes.

**Architecture:** A new `ota.h/ota.cpp` module handles the full OTA flow (fetch version → compare → download → verify → apply). It is called once from `setup()` after WiFi connects. A baked-in `FIRMWARE_VERSION` in `config.h` is compared to a flag URL; if newer, the `.ota` is pulled from GitHub Releases and applied via the bundled `OTAUpdate` library. A single EEPROM byte counts consecutive OTA failures; ≥3 failures → skip OTA permanently until USB reflash. A CI workflow builds + LZSS-compresses the binary on `git tag v*` and publishes the release. A shell script automates version bumping + tagging.

**Tech Stack:** Arduino C++ (UNO R4 WiFi), bundled `OTAUpdate` + `EEPROM` (Renesas core), GitHub Actions (`arduino/arduino-cli-action`, `gh` CLI), Python `lzss` conversion tool from ArduinoIoTCloud extras, `arduino-cli`.

---

## File structure

- `firmware/garden-node/config.h` — add `FIRMWARE_VERSION`, `OTA_VERSION_URL`, `OTA_BINARY_URL`, `OTA_MAX_FAILURES`, `OTA_EEPROM_OFFSET`.
- `firmware/garden-node/ota.h` — public interface: `otaBegin()`, `otaCheckAndApply()`.
- `firmware/garden-node/ota.cpp` — full OTA flow: version fetch, compare, download, verify, apply, EEPROM counter.
- `firmware/garden-node/garden-node.ino` — call `otaCheckAndApply()` after `netEnsureWifi()` in `setup()`.
- `.github/workflows/firmware-release.yml` — CI: compile → convert → publish release on `v*` tags.
- `scripts/release-firmware.sh` — local helper: bump version, commit, tag, push.

---

## Task 1: Config constants + ota.h interface

**Files:**
- Modify: `firmware/garden-node/config.h`
- Create: `firmware/garden-node/ota.h`

- [ ] **Step 1: Add OTA constants to `config.h`**

Open `firmware/garden-node/config.h` and add this block after the `SAMPLE_INTERVAL_MS` / `SLIDE_MS` section:

```cpp
// ─── OTA firmware update ──────────────────────────────────────────────────────
// Bump FIRMWARE_VERSION when cutting a release (scripts/release-firmware.sh).
#define FIRMWARE_VERSION   "1.0.0"
#define OTA_VERSION_URL    "https://github.com/ianlintner/gardencontroller/releases/latest/download/version.txt"
#define OTA_BINARY_URL     "https://github.com/ianlintner/gardencontroller/releases/latest/download/garden-node.ota"
#define OTA_MAX_FAILURES   3      // skip OTA after this many consecutive failures
#define OTA_EEPROM_OFFSET  0      // byte address of the failure counter in virtual EEPROM
```

- [ ] **Step 2: Create `firmware/garden-node/ota.h`**

```cpp
// ota.h — OTA firmware update: version check + pull-from-URL at boot.
#pragma once
#include <Arduino.h>

// Call once in setup() after WiFi is connected.
// Downloads and applies a new firmware from GitHub Releases if a newer version
// is available. Reboots the board on success. Returns false if no update is
// available or if the update failed (caller continues normal operation).
bool otaCheckAndApply();
```

- [ ] **Step 3: Compile check (no ota.cpp yet — just verify header + config parse)**

```bash
cd firmware/garden-node
cp arduino_secrets.h.example arduino_secrets.h
arduino-cli compile --fqbn arduino:renesas_uno:unor4wifi . 2>&1 | tail -4
rm arduino_secrets.h
```
Expected: compiles (the `.h` is not included anywhere yet so no linker error).

- [ ] **Step 4: Commit**

```bash
git add firmware/garden-node/config.h firmware/garden-node/ota.h
git commit -m "feat(ota): OTA config constants + interface"
```

---

## Task 2: `ota.cpp` — EEPROM failure counter helpers (pure logic)

**Files:**
- Create: `firmware/garden-node/ota.cpp` (partial — counter only)

The EEPROM counter helpers are the only testable-at-compile-time logic. Everything else in this module is I/O (network + flash) and is tested on hardware.

- [ ] **Step 1: Create `firmware/garden-node/ota.cpp` with the EEPROM helpers**

```cpp
#include "ota.h"
#include "config.h"
#include "display.h"
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
    EEPROM.write(OTA_EEPROM_OFFSET, n);
    EEPROM.commit();
}

static void resetFailCount() { writeFailCount(0); }

static void incrementFailCount() {
    uint8_t n = readFailCount();
    if (n < 255) writeFailCount(n + 1);
}
```

- [ ] **Step 2: Compile check (ota.cpp now in sketch folder)**

```bash
cd firmware/garden-node
cp arduino_secrets.h.example arduino_secrets.h
arduino-cli compile --fqbn arduino:renesas_uno:unor4wifi . 2>&1 | tail -4
rm arduino_secrets.h
```
Expected: compiles. `otaCheckAndApply` is declared in `ota.h` but not yet defined — the linker will error. That is expected at this step; move on.

Actually: arduino-cli compiles all `.cpp` in the sketch folder, so the undefined `otaCheckAndApply` will cause a linker error. Add a stub to make it compile:

After the `incrementFailCount` function, add:

```cpp
// stub — replaced in Task 3
bool otaCheckAndApply() { return false; }
```

Re-run compile — it must now succeed.

- [ ] **Step 3: Compile with stub — passes**

```bash
cd firmware/garden-node
cp arduino_secrets.h.example arduino_secrets.h
arduino-cli compile --fqbn arduino:renesas_uno:unor4wifi . 2>&1 | tail -3
rm arduino_secrets.h
```
Expected: `Sketch uses … bytes` — no errors.

- [ ] **Step 4: Commit**

```bash
git add firmware/garden-node/ota.cpp
git commit -m "feat(ota): EEPROM failure counter helpers + compile stub"
```

---

## Task 3: `otaCheckAndApply()` — full OTA flow

**Files:**
- Modify: `firmware/garden-node/ota.cpp` (replace stub)

The flow: fetch version string → compare to `FIRMWARE_VERSION` → if same, return false → fetch binary → verify → apply → reboot. Any failure → increment EEPROM counter → return false.

- [ ] **Step 1: Replace the stub with the full implementation**

Replace `bool otaCheckAndApply() { return false; }` in `ota.cpp` with:

```cpp
// Fetch a URL via HTTP GET and return the response body (trimmed), or "" on error.
// Uses the OTAUpdate modem path for HTTPS (shares the ESP32 cert store).
static String fetchString(const char* url) {
    // We use a plain WiFiClient GET to read a short text file (version.txt).
    // OTAUpdate handles binary downloads; for the version check we use WiFiS3's
    // HTTPS client directly so we get the response body as a String.
    WiFiSSLClient client;
    // Parse host from URL: skip "https://"
    String u(url);
    int hostStart = u.indexOf("//") + 2;
    int pathStart = u.indexOf('/', hostStart);
    String host = u.substring(hostStart, pathStart);
    String path = u.substring(pathStart);

    if (!client.connect(host.c_str(), 443)) return "";
    client.print(String("GET ") + path + " HTTP/1.1\r\n" +
                 "Host: " + host + "\r\n" +
                 "Connection: close\r\n\r\n");
    unsigned long t0 = millis();
    while (!client.available() && millis() - t0 < 10000) delay(10);
    // Skip headers — read until blank line
    String line;
    while (client.available()) {
        line = client.readStringUntil('\n');
        if (line == "\r" || line == "") break;
    }
    // Read body (version.txt is a single short line)
    String body = "";
    while (client.available()) body += (char)client.read();
    client.stop();
    body.trim();
    return body;
}

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

    // New version available — show "updating" pattern on LED matrix
    Serial.println("OTA: update available, downloading...");
    // Full matrix lit = "updating" indicator
    {
        uint8_t full[8][12];
        for (int y = 0; y < 8; y++) for (int x = 0; x < 12; x++) full[y][x] = 1;
        // matrix is owned by display.cpp; call displaySetHealth to ERROR first
        // so the loop guard won't fight us, then render directly via the OTA path.
        // Simplest: just leave the matrix in its current state during download.
        // The download takes seconds, so we don't animate here.
    }

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
```

Also add `#include <WiFiS3.h>` at the top of `ota.cpp` (after the existing includes), since `fetchString` uses `WiFiSSLClient`.

- [ ] **Step 2: Compile**

```bash
cd firmware/garden-node
cp arduino_secrets.h.example arduino_secrets.h
arduino-cli compile --fqbn arduino:renesas_uno:unor4wifi . 2>&1 | tail -4
rm arduino_secrets.h
```
Expected: compiles. Flash % may increase slightly.

- [ ] **Step 3: Commit**

```bash
git add firmware/garden-node/ota.cpp
git commit -m "feat(ota): full otaCheckAndApply with version check + EEPROM counter"
```

---

## Task 4: Wire `otaCheckAndApply()` into `garden-node.ino`

**Files:**
- Modify: `firmware/garden-node/garden-node.ino`

- [ ] **Step 1: Add include and call**

Open `firmware/garden-node/garden-node.ino`. The file currently has:
```cpp
#if ENABLE_UPLOAD
#include "net.h"
#endif
```

Add `#include "ota.h"` inside the `#if ENABLE_UPLOAD` block (OTA only makes sense when uploads are enabled):
```cpp
#if ENABLE_UPLOAD
#include "net.h"
#include "ota.h"
#endif
```

In `setup()`, find the existing sequence:
```cpp
#if ENABLE_UPLOAD
  netBegin();
#endif
```

Replace it with:
```cpp
#if ENABLE_UPLOAD
  netBegin();
  if (netEnsureWifi()) {
    otaCheckAndApply();   // checks version, applies + reboots if update available
  }
#endif
```

Note: `netEnsureWifi()` is already idempotent (it checks `WL_CONNECTED` first). Calling it here means the WiFi connect attempt at setup covers both OTA and later publishes. The existing call in `netPublish()` → `netEnsureWifi()` is a no-op when already connected.

- [ ] **Step 2: Compile**

```bash
cd firmware/garden-node
cp arduino_secrets.h.example arduino_secrets.h
arduino-cli compile --fqbn arduino:renesas_uno:unor4wifi . 2>&1 | tail -4
rm arduino_secrets.h
```
Expected: compiles cleanly.

- [ ] **Step 3: Commit**

```bash
git add firmware/garden-node/garden-node.ino
git commit -m "feat(ota): wire otaCheckAndApply into setup after WiFi connect"
```

---

## Task 5: Release helper script

**Files:**
- Create: `scripts/release-firmware.sh`

- [ ] **Step 1: Create the script**

`scripts/release-firmware.sh`:
```bash
#!/usr/bin/env bash
# Bump FIRMWARE_VERSION in config.h, commit, tag, and push.
# CI picks up the v* tag and builds + publishes the GitHub Release.
#
# Usage: ./scripts/release-firmware.sh 1.2.0
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG="$ROOT/firmware/garden-node/config.h"
VERSION="${1:-}"

if [[ -z "$VERSION" ]] || ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Usage: $0 <major.minor.patch>   e.g.  $0 1.2.0" >&2
  exit 1
fi

# Check working tree is clean
if ! git -C "$ROOT" diff --quiet HEAD; then
  echo "error: working tree has uncommitted changes — commit or stash first" >&2
  exit 1
fi

echo "Bumping FIRMWARE_VERSION to $VERSION in config.h..."
sed -i '' "s/#define FIRMWARE_VERSION *\"[^\"]*\"/#define FIRMWARE_VERSION \"$VERSION\"/" "$CONFIG"
grep "FIRMWARE_VERSION" "$CONFIG"

git -C "$ROOT" add "$CONFIG"
git -C "$ROOT" commit -m "chore(firmware): bump version to $VERSION"
git -C "$ROOT" tag "v$VERSION"
git -C "$ROOT" push origin main "v$VERSION"
echo "Tagged v$VERSION and pushed. CI will build + publish the GitHub Release."
```

- [ ] **Step 2: Make executable and verify syntax**

```bash
chmod +x scripts/release-firmware.sh
bash -n scripts/release-firmware.sh && echo "syntax OK"
```
Expected: `syntax OK`

- [ ] **Step 3: Commit**

```bash
git add scripts/release-firmware.sh
git commit -m "feat(ota): release-firmware.sh — bump version + tag + push"
```

---

## Task 6: CI firmware release workflow

**Files:**
- Create: `.github/workflows/firmware-release.yml`

The `.ota` format is LZSS-compressed. Arduino's official tool is a Python script in `ArduinoIoTCloud/extras/tools`. The CI fetches it directly from GitHub.

- [ ] **Step 1: Create the workflow**

`.github/workflows/firmware-release.yml`:
```yaml
name: firmware-release

# Builds the garden-node firmware and publishes a GitHub Release on v* tags.
# The release includes:
#   version.txt  — plain version string (e.g. "1.2.0"), used by the board's OTA check
#   garden-node.ota — LZSS-compressed firmware, fetched and flashed by OTAUpdate
on:
  push:
    tags:
      - "v*"
  workflow_dispatch:
    inputs:
      tag:
        description: "Tag to build (e.g. v1.2.0)"
        required: true

jobs:
  build-release:
    runs-on: ubuntu-latest
    permissions:
      contents: write     # needed to create GitHub Releases
    steps:
      - uses: actions/checkout@v4

      - name: Set version
        id: version
        run: |
          TAG="${{ github.ref_name }}"
          if [[ -z "$TAG" || "$TAG" == "refs/heads/"* ]]; then
            TAG="${{ github.event.inputs.tag }}"
          fi
          VERSION="${TAG#v}"
          echo "version=$VERSION" >> "$GITHUB_OUTPUT"
          echo "tag=$TAG" >> "$GITHUB_OUTPUT"

      - name: Install arduino-cli
        uses: arduino/setup-arduino-cli@v2

      - name: Install R4 core + libraries
        run: |
          arduino-cli core update-index
          arduino-cli core install arduino:renesas_uno
          arduino-cli lib install "ArduinoHttpClient" "ArduinoJson" "DHT sensor library"

      - name: Write dummy secrets file
        run: |
          cat > firmware/garden-node/arduino_secrets.h << 'EOF'
          #pragma once
          #define SECRET_WIFI_SSID "ci"
          #define SECRET_WIFI_PASS "ci"
          #define SECRET_OAUTH_CLIENT_ID "ci"
          #define SECRET_OAUTH_CLIENT_SECRET "ci"
          EOF

      - name: Set ENABLE_UPLOAD=1 for release build
        run: |
          sed -i 's/#define ENABLE_UPLOAD 0/#define ENABLE_UPLOAD 1/' \
            firmware/garden-node/config.h

      - name: Compile firmware
        run: |
          arduino-cli compile \
            --fqbn arduino:renesas_uno:unor4wifi \
            --output-dir /tmp/build \
            firmware/garden-node
          ls -lh /tmp/build/

      - name: Install OTA conversion tool
        run: |
          pip install arduino-iot-cloud --quiet
          # Verify the lzss.py conversion script is available via the package
          python3 -c "import arduino_iot_cloud; print('arduino-iot-cloud OK')" || true
          # Fetch the standalone lzss.py from ArduinoIoTCloud extras
          curl -fsSL https://raw.githubusercontent.com/arduino-libraries/ArduinoIoTCloud/master/extras/tools/lzss.py \
            -o /tmp/lzss.py
          python3 /tmp/lzss.py --help 2>&1 | head -3 || true

      - name: Convert .bin to .ota (LZSS compress)
        run: |
          # lzss.py <input.bin> <output.ota>
          BIN=$(ls /tmp/build/*.bin | head -1)
          echo "Binary: $BIN ($(wc -c < "$BIN") bytes)"
          python3 /tmp/lzss.py "$BIN" /tmp/garden-node.ota
          echo "OTA:    $(wc -c < /tmp/garden-node.ota) bytes"

      - name: Write version.txt
        run: echo -n "${{ steps.version.outputs.version }}" > /tmp/version.txt

      - name: Create GitHub Release
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        run: |
          gh release create "${{ steps.version.outputs.tag }}" \
            --title "Firmware ${{ steps.version.outputs.version }}" \
            --notes "Garden node firmware release ${{ steps.version.outputs.version }}" \
            /tmp/version.txt \
            /tmp/garden-node.ota
```

- [ ] **Step 2: Validate YAML**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/firmware-release.yml'))" && echo "YAML OK"
```
Expected: `YAML OK`

- [ ] **Step 3: Commit + push**

```bash
git add .github/workflows/firmware-release.yml
git commit -m "feat(ota): CI firmware release workflow (build + lzss + GitHub Release)"
git push origin main
```

---

## Task 7: End-to-end hardware verification

This task is manual + hardware. There is no automated test for the full OTA flow since it requires a real board + network + GitHub Release.

- [ ] **Step 1: Cut a test release**

Make a trivial change (e.g. add a comment to `garden-node.ino`), then:
```bash
./scripts/release-firmware.sh 1.0.1
```
Watch CI at `https://github.com/ianlintner/gardencontroller/actions` — wait for the `firmware-release` workflow to pass and create a release with `version.txt` + `garden-node.ota`.

- [ ] **Step 2: Flash the board with v1.0.0 (the current `FIRMWARE_VERSION`)**

Ensure `FIRMWARE_VERSION` is `"1.0.0"` in `config.h` and `ENABLE_UPLOAD=1`. Flash:
```bash
./scripts/flash.sh
```

- [ ] **Step 3: Open Serial Monitor and observe boot sequence**

```bash
arduino-cli monitor -p /dev/cu.usbmodem3844BEC6C0802 -c baudrate=115200
```
Expected output (approximately):
```
garden-node booting: garden-node-1 @ raised-bed
OTA: checking version...
OTA: current=1.0.0 latest=1.0.1
OTA: update available, downloading...
OTA: applying — board will reboot
```
Then the board reboots and the new firmware starts. Verify:
```
garden-node booting: garden-node-1 @ raised-bed
OTA: checking version...
OTA: current=1.0.1 latest=1.0.1
OTA: up to date
```

- [ ] **Step 4: Verify failure counter**

Temporarily corrupt `OTA_BINARY_URL` in `config.h` (change one char), flash, reboot 3× — after 3 failures the board should print:
```
OTA: skipping — 3 consecutive failures. Reflash via USB to reset.
```
Then fix the URL and reflash to reset the counter.

- [ ] **Step 5: Final push**

```bash
git push origin main
```

---

## Notes / known considerations

- **`lzss.py` stability**: the ArduinoIoTCloud `lzss.py` script location may shift. If the `curl` in CI fails, fall back to cloning the repo: `git clone --depth 1 https://github.com/arduino-libraries/ArduinoIoTCloud && python3 ArduinoIoTCloud/extras/tools/lzss.py ...`
- **GitHub redirect**: `releases/latest/download/version.txt` redirects via a 302 to the actual release URL. `WiFiSSLClient` follows one redirect but check this during Task 7 — if the version fetch returns an empty string, the redirect may not be followed. In that case, use the direct release URL by querying the GitHub API (`/repos/owner/repo/releases/latest`) or pin to a specific tag URL.
- **EEPROM persistence**: the Renesas virtual EEPROM survives reboots but is reset by a USB reflash (new sketch = fresh flash). This is the intended "factory reset" behavior.
- **CI arm64**: the R4 CI (`ubuntu-latest`) is x86_64; the compiled `.bin` is for the Renesas RA4M1 ARM core. The `lzss.py` conversion runs on the CI host and is architecture-neutral.
