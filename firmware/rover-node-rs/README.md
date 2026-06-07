# rover-node-rs — Rust firmware for the ESP32-WROVER camera board

Phase 1: backbone parity with the C++ garden node — WiFi, ~1Hz NDJSON telemetry
over USB serial **and** TCP `:8766`, OAuth2/HTTPS cloud push (flagged), and
`esp_ota` updates. Camera is stubbed (Phase 2). Motors are Phase 3.

## Layout
- `rover-core/` — pure, host-tested logic (frame schema, version compare,
  newest-wins TCP broadcaster). `cargo test` runs on any machine.
- `rover-firmware/` — esp-idf-svc binary (Xtensa). Built in CI; flashed to the board.

## Host tests (no hardware)
    cd rover-core && cargo test

## Build the firmware (needs the ESP Rust toolchain)
    # one-time: cargo install espup && espup install && . ~/export-esp.sh
    cd rover-firmware
    ROVER_WIFI_SSID=... ROVER_WIFI_PASS=... cargo build --release

## Watch it live (same client as the garden node)
    cd ../../tools/board-tui
    python board_tui.py --host <rover-ip>     # TCP :8766
    python board_tui.py --port /dev/tty.usbserial-XXXX   # USB serial

## Release / OTA
Tag `rover-v1.0.0` → CI publishes `version.txt` + `garden-rover.bin`; the board's
5-min OTA poll picks it up.
