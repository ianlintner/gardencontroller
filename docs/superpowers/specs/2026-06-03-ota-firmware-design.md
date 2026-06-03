# OTA Firmware Updates — Design

**Date:** 2026-06-03
**Status:** Approved design, pending implementation plan

## Context

The garden node (Arduino UNO R4 WiFi) lives outdoors and is currently flashed
via USB. Adding OTA update support means firmware can be pushed without physical
access. The board checks a flag URL at boot, compares to its baked-in version,
downloads + applies a new `.ota` from a GitHub Release if needed, and reboots.
A watchdog failure counter in EEPROM prevents a bricked board from entering an
infinite OTA retry loop.

## Decisions (from brainstorming)

| Decision | Choice |
|---|---|
| Update trigger | **On-demand via flag URL** — board checks at boot, compares version |
| Hosting | **GitHub Releases** (public repo, free, CI already exists) |
| Version tracking | **Baked-in `CURRENT_VERSION`** in `config.h`, compared to flag URL response |
| Rollback/safety | **EEPROM failure counter** — after 3 consecutive OTA failures, skip OTA + alert |
| OTA mechanism | **Built-in `OTAUpdate` library** (Renesas core, pull model, LZSS-compressed `.ota`) |

## Architecture

```
Boot sequence (after WiFi connects):
  1. GET https://github.com/ianlintner/gardencontroller/releases/latest/download/version.txt
  2. if response == CURRENT_VERSION  →  skip, start normal sensor/publish loop
  3. else (new version available)   →  download garden-node.ota from same releases/latest/download/
  4. OTAUpdate.verify() + OTAUpdate.update()  →  reboot into new firmware
  5. if any OTA step fails:
       increment EEPROM failure counter
       if counter >= OTA_MAX_FAILURES (3)  →  skip OTA permanently until USB reflash
       show X on LED matrix + log to Serial; continue normal operation
```

**Flag URL:**
`https://github.com/ianlintner/gardencontroller/releases/latest/download/version.txt`
GitHub transparently redirects `latest/download/` to the most recent release. No
separate flag service needed.

**Firmware binary URL:**
`https://github.com/ianlintner/gardencontroller/releases/latest/download/garden-node.ota`

## Components

### 1. Firmware additions

**`firmware/garden-node/ota.h` / `ota.cpp`**
- `bool otaCheckAndApply()` — full OTA flow (WiFi must be connected). Returns
  `true` if update was applied (board reboots internally), `false` if no update
  or update failed. Called once from `setup()` after `netEnsureWifi()`.
- Uses `OTAUpdate` (bundled with Renesas core), `WiFiClientSecure` or the
  library's built-in HTTPS for download.
- Reads/writes EEPROM failure counter at a fixed offset (`OTA_EEPROM_OFFSET`,
  default byte 0; value 0xFF = fresh/erased = treat as 0).

**`config.h` additions**
- `#define FIRMWARE_VERSION "1.0.0"` — bumped on each release (by the release
  script; this is the single source of truth).
- `#define OTA_VERSION_URL "https://github.com/ianlintner/gardencontroller/releases/latest/download/version.txt"`
- `#define OTA_BINARY_URL  "https://github.com/ianlintner/gardencontroller/releases/latest/download/garden-node.ota"`
- `#define OTA_MAX_FAILURES 3`
- `#define OTA_EEPROM_OFFSET 0` — byte address for the failure counter.

**`garden-node.ino`** — call `otaCheckAndApply()` in `setup()`, after
`netEnsureWifi()` succeeds but before `lastSampleMs` is set.

**LED matrix during OTA** — show a distinct "updating" pattern (e.g. all LEDs
lit / a scrolling bar) while downloading/flashing; revert to seed/error on
failure.

### 2. CI — firmware release workflow

**`.github/workflows/firmware-release.yml`** — triggers on `git tag v*`:
1. Checkout repo.
2. `arduino-cli` compile for `arduino:renesas_uno:unor4wifi` → produces `.bin`.
3. Convert `.bin` → `.ota` using the official Arduino IoT Cloud conversion tool
   (`arduino-iot-cloud-ota-portenta` or the Python `lzss.py` from
   `arduino-libraries/ArduinoIoTCloud/extras/tools`) — applies LZSS compression.
   The raw `.bin` is **not** valid for OTAUpdate; compression is required.
4. Extract version from tag (strip leading `v`).
5. Create GitHub Release with two assets: `version.txt` (just the version
   string, e.g. `1.2.0`) and `garden-node.ota`.
6. Uses `GITHUB_TOKEN` (automatic, no extra secret needed for public repo
   release creation).

### 3. Release helper script

**`scripts/release-firmware.sh <version>`** — local convenience:
1. Validates `<version>` is `N.N.N`.
2. Updates `FIRMWARE_VERSION` in `config.h`.
3. Commits the version bump.
4. Creates and pushes `git tag v<version>`.
5. CI picks up the tag and does the rest.

## Safety details

- **Failure counter in EEPROM**: a single byte at `OTA_EEPROM_OFFSET`. On every
  failed OTA attempt, increment (saturating at 255). On successful apply, reset
  to 0. On boot, if value ≥ `OTA_MAX_FAILURES`, skip OTA entirely and print a
  warning — the board stays operational on its current firmware until manually
  reflashed via USB.
- **No mid-run OTA**: check happens once at boot, before the sensor loop
  starts. A watering event or publish cycle is never interrupted.
- **Checksum verification**: `OTAUpdate.verify()` checks the `.ota` internal
  checksum before calling `update()`. A corrupted download is rejected before
  any flash write.
- **HTTPS only**: both the version check and binary download use HTTPS. The
  R4's ESP32-S3 cert store includes GitHub's root CA (DigiCert Global Root G2).
  If the TLS handshake fails, the OTA step is skipped (treat as "no update
  available" — fail-safe, not fail-open).

## `.ota` file format note

The R4's `OTAUpdate` library requires **LZSS-compressed** `.ota` files — a raw
`.bin` will fail `verify()`. CI must run the compression step. The conversion
tool is a Python script from the ArduinoIoTCloud extras; CI installs it with
`pip install arduino-iot-cloud` or fetches the script directly.

## Testing

- **Unit**: `otaCheckAndApply()` has no pure-logic unit test (it's all I/O).
  Tested by: mock version URL returning current version → no download; mock
  returning a different version → download triggered (verified via Serial log).
  Both paths tested on real hardware before first release.
- **CI smoke**: the release workflow runs `arduino-cli compile` and converts the
  binary; a failed compile or conversion fails the release (no partial release).
- **First real OTA**: flash `v1.0.0` via USB, push a `v1.0.1` tag (only bumps
  `FIRMWARE_VERSION`), reboot the board, observe it downloads + reboots into
  `v1.0.1` (confirmed via Serial + Prometheus `garden_firmware_version` gauge).

## Out of scope

- Rollback to a previous version (the counter halts retries; USB reflash is
  the recovery path).
- Signature verification of the `.ota` beyond the built-in checksum.
- OTA triggered by the cloud/OpenClaw (boot-time check only for MVP).
- Multi-board OTA coordination (each board checks its own flag URL independently).
