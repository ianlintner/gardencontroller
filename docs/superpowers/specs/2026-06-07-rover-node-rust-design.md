# Rover Node (Rust on ESP32-WROVER) — Phase 1 Backbone Parity — Design

**Date:** 2026-06-07
**Status:** Approved (brainstorming) — pending implementation plan
**Related:** the C++ `firmware/garden-node/` whose telemetry/OTA backbone this ports

## Problem

A new AITRIP **ESP32-WROVER** camera board (classic ESP32, Xtensa LX6 dual-core,
4MB PSRAM, OV2640) is on the way, and we want its firmware in **Rust**. The
existing garden node is C++ on an Arduino UNO R4. We want the new board to share
the observability/update backbone (live telemetry, cloud push, OTA) so the same
tooling (`board-tui`, Grafana, OTA flow) works against it — with the camera as a
later phase.

## Goal (Phase 1)

A Rust firmware for the ESP32-WROVER that reaches **backbone feature parity** with
the C++ garden node:

1. WiFi (STA) with reconnect.
2. ~1Hz NDJSON telemetry over **USB serial and TCP `:8766`** (newest-wins single
   client) — using the **same frame schema** so the existing `board-tui` works
   unchanged over both transports.
3. OAuth2 client-credentials token + HTTPS POST of board-health readings to the
   existing `garden-ingest` (behind an `enable_upload` flag).
4. Periodic (5-min) **`esp_ota`** firmware update from a GitHub release.
5. Camera present only as a **stub** (probe → `camPresent`; no streaming).

A `rover-core` crate holds pure logic that is **unit-tested on the host** with
plain `cargo test`, so we have green CI before the hardware arrives.

Non-goals (separate specs later): camera capture/MJPEG streaming (Phase 2); rover
motor/drive control (Phase 3); any LED-matrix equivalent (the WROVER has none — at
most an optional status-LED blink, not in Phase 1).

## Decisions (from brainstorming)

- **Chip:** ESP32-WROVER (Xtensa LX6 + PSRAM). Rust toolchain via **espup**.
- **Runtime:** **std on ESP-IDF** (`esp-idf-svc`) — reuses ESP-IDF WiFi, mbedTLS,
  HTTP client, `esp_ota`, and (Phase 2) the esp32-camera driver.
- **Phase 1 = backbone parity, camera stubbed.**

## Architecture

A Cargo **workspace** under `firmware/rover-node-rs/`, splitting pure logic from the
ESP-specific binary (mirrors the Python `garden_core` pattern):

```
firmware/rover-node-rs/
  Cargo.toml                 # [workspace] members = rover-core, rover-firmware
  rover-core/                # pure Rust, host-testable, NO esp deps
    Cargo.toml               # deps: serde, serde_json (std)
    src/lib.rs               # Frame struct + NDJSON serialize; version_is_newer()
  rover-firmware/            # esp-idf-svc binary (Xtensa target)
    Cargo.toml               # deps: esp-idf-svc, esp-idf-hal, anyhow, log, rover-core
    rust-toolchain.toml      # channel = "esp"
    .cargo/config.toml       # target = xtensa-esp32-espidf, runner, ESP_IDF_VERSION
    sdkconfig.defaults       # PSRAM on, etc.
    partitions.csv           # factory + 2 OTA app slots
    build.rs                 # embuild (esp-idf-sys)
    src/main.rs              # orchestration loop
    src/wifi.rs              # EspWifi STA connect/reconnect
    src/telemetry.rs         # build frame (via rover-core) → serial + TCP server
    src/cloud.rs             # OAuth2 token + HTTPS POST /ingest (enable_upload)
    src/ota.rs               # periodic version check + esp_ota apply
    src/camera.rs            # STUB: probe() -> bool (camPresent); init no-op
    src/config.rs            # device id, endpoints, intervals, flags
```

### Data flow
`main` loop (timer-gated, like the C++ `loop()`):
- every `TELEMETRY_MS` (1000ms): `telemetry::emit()` builds a `rover_core::Frame`,
  serializes to one NDJSON line, writes to `stdout` (USB serial) and to the TCP
  client if connected (newest-wins).
- every `SAMPLE_INTERVAL_MS` (60s): if `enable_upload`, `cloud::push()` board health.
- every `OTA_CHECK_INTERVAL_MS` (300s): `ota::check_and_apply()`.

## Telemetry frame schema (board-tui compatible)

`rover-core` serializes exactly these top-level keys (so `parse_frame` accepts it
and the Rich panels render without changes):

```json
{"t":12345,"fw":"r1.0.0","dev":"rover-node-1",
 "pins":{},
 "sensors":{"camPresent":true,"camFps":0},
 "net":{"wifi":"up","rssi":-58,"ip":"192.168.1.50","push":"ok"},
 "board":{"up_s":1234,"heap_b":210000,"psram_b":3800000}}
```

- `fw` uses an `r` prefix (e.g. `r1.0.0`) to distinguish rover releases from the
  garden node's `1.0.x`.
- `pins` is `{}` (no analog soil array on this board); `board-tui`'s pins panel
  renders empty rows gracefully.
- `sensors.camPresent` from `camera::probe()`; `camFps` is `0` until Phase 2.
- `net.push` ∈ {`ok`,`fail`,`n/a`} (last cloud push; `n/a` when `enable_upload=false`).
- `board.psram_b` is new (free PSRAM bytes); `heap_b` is free internal heap.

## Cloud push (parity, with one integration note)

- OAuth2 client-credentials → JWT (cached to expiry), then HTTPS POST to
  `https://garden.cat-herding.net/ingest` with `Authorization: Bearer <jwt>`, via
  the esp-idf HTTP client over mbedTLS. Same auth model as the C++ node.
- Body reuses the `/ingest` contract shape `{device_id, location, readings:{…}}`,
  carrying the readings the rover actually has (board health: rssi/heap/psram/
  uptime). **Integration note:** `garden-ingest`'s mapping currently expects
  soil/temp/rain fields; it may need to tolerate the rover's reading subset. That
  is a small, separate change to the ingest service — Phase 1 ships the firmware
  side behind `enable_upload` (default off in-repo, on for release builds, exactly
  like the C++ `ENABLE_UPLOAD`), so an un-updated ingest never blocks the build or
  the telemetry/OTA paths.

## OTA (esp_ota — concept parity, ESP32 mechanism)

- `partitions.csv`: `factory` + `ota_0` + `ota_1` + `otadata`.
- Every `OTA_CHECK_INTERVAL_MS`: GET `version.txt` from the rover GitHub release;
  if newer than `FIRMWARE_VERSION` (compared by `rover_core::version_is_newer`),
  GET `garden-rover.bin`, stream into the inactive OTA partition via the esp-idf
  OTA API, set it as boot partition, reboot.
- Artifact is a **plain `.bin`** (no lzss — that was Renesas-specific).
- A consecutive-failure guard (stored in NVS) mirrors the C++ `OTA_MAX_FAILURES`
  so a bad release can't boot-loop.

## Build, CI, and testing

- **`rover-core` host tests** (`cargo test -p rover-core`, plain target):
  - a `Frame` with empty pins + camera sensors serializes to JSON containing the
    four required keys and round-trips through `serde_json`.
  - `version_is_newer("1.0.1","1.0.0") == true`, `("1.0.0","1.0.0") == false`,
    `("1.0.0","1.1.0") == false`, and a malformed string is treated as not-newer.
  - the serialized line ends with no embedded newline (so the TCP/serial splitter
    sees one frame per line).
- **`board-tui` compatibility test**: a `rover-core` JSON sample is added as a
  fixture and the existing `board-tui` `parse_frame` accepts it (a tiny test in
  `tools/board-tui/tests` reading the new fixture) — proves cross-language contract.
- **Firmware build CI** (`.github/workflows/rover-firmware.yml`):
  - host job: `cargo test -p rover-core`.
  - build job: `esp-rs/xtensa-toolchain` action (or `espup`) → `cargo build
    --release -p rover-firmware`; on a `rover-v*` tag, publish `version.txt` +
    `garden-rover.bin` to a GitHub release.
- **This session** authors the workspace, modules, configs, CI, and the
  host-tested `rover-core`. The Xtensa/esp-idf compile of `rover-firmware` runs in
  CI; flashing/runtime verification happens when the board arrives. (The espup +
  esp-idf toolchain is a large local install not assumed here.)

## Error handling

- WiFi down → telemetry still emits over serial with `net.wifi="down"`; TCP server
  and cloud/OTA simply skip until reconnected.
- TCP write to a dead client → drop it; newest connection supersedes on next tick.
- Cloud push failure → `net.push="fail"`, logged; no retry storm (next 60s cycle).
- OTA fetch/apply failure → increment NVS failure counter; skip past the cap.
- Camera absent → `camPresent=false`; everything else unaffected.
- `main` uses `anyhow::Result` with a top-level catch that logs and continues the
  loop rather than panicking the device.

## Success criteria (Phase 1)

1. `cargo test -p rover-core` passes on the host (CI green before hardware).
2. `rover-firmware` builds for `xtensa-esp32-espidf` in CI, producing
   `garden-rover.bin`.
3. The documented NDJSON frame is accepted by the existing `board-tui`
   `parse_frame` (fixture test).
4. On the board (when it arrives): `board-tui --host <rover-ip>` shows a live
   dashboard; the node appears distinctly as `rover-node-1` / `fw r1.0.0`; a
   `rover-v*` release is picked up by OTA within ~5 min.
