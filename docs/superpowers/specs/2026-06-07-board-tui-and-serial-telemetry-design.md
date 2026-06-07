# Board TUI + Serial Telemetry Stream — Design

**Date:** 2026-06-07
**Status:** Approved (brainstorming) — pending implementation plan

## Problem

There is no fast, always-available way to watch the garden board's live state.
The current firmware prints unstructured human-readable lines only once per 60s
sample, which is too slow and too loose to parse for a real-time view. We want a
local terminal client that connects to the USB-attached board and shows live pin
values, translated sensor data, and board/network metrics — usable any time for
testing, and runnable without hardware for demos/CI. Delivery of the new firmware
should exercise the OTA path, and OTA should poll periodically so future updates
land without a manual reboot.

## Goal

1. **Firmware:** emit a structured, ~1Hz NDJSON telemetry frame over USB serial
   (all analog pins, translated sensors, board/net metrics), decoupled from the
   60s cloud push. Add periodic OTA polling (~5 min).
2. **TUI:** a Python (Rich) live dashboard that consumes the frame from a serial
   port, a built-in simulator, or a replayed capture; reconnects on drop.
3. **Delivery:** ship the firmware via an OTA release (v1.0.1) and verify the
   board self-updates, then run the TUI over USB.

Non-goals (YAGNI): writing back to the board from the TUI (read-only monitor);
charting/history persistence; per-digital-pin raw dumps (D0/D1 are the USB UART;
DHT on D7 is a protocol sensor surfaced as parsed values, not a raw pin).

## Component 1 — Firmware telemetry stream

New `firmware/garden-node/telemetry.h` / `telemetry.cpp`.

- `telemetryTick()` is called every `loop()` iteration and is **non-blocking**:
  it does work only when `millis() - last >= TELEMETRY_MS` (default 1000ms).
- Each tick reads the 6 analog inputs **live** (`analogRead` at the configured
  14-bit resolution) and emits one NDJSON line on `Serial`.
- DHT22 cannot sustain 1Hz; the temp/humidity are re-read at most every
  `TELEMETRY_DHT_MIN_MS` (2500ms) and cached between frames.
- Emitted unconditionally (coexists with cloud push and the existing boot/OTA log
  lines; the TUI ignores any non-JSON line).

**Frame schema** (compact, stable keys; one JSON object per line):

```json
{"t":12345,"fw":"1.0.1","dev":"garden-node-1",
 "pins":{"A0":16021,"A1":11002,"A2":8190,"A3":8175,"A4":8201,"A5":8188},
 "sensors":{"tempC":22.4,"hum":48.2,"dhtOk":true,
   "soil":[{"probe":"bed1","pin":"A0","raw":16021,"pct":12.5},
           {"probe":"bed2","pin":"A1","raw":11002,"pct":78.0}]},
 "net":{"wifi":"up","rssi":-58,"ip":"192.168.1.42","push":"ok"},
 "board":{"up_s":1234,"heap_b":23456}}
```

- `t` = `millis()` uptime; `fw` = `FIRMWARE_VERSION`; `dev` = `DEVICE_ID`.
- `pins` = all of A0–A5 raw. A0/A1 are the soil probes (also under `sensors.soil`
  with translated `pct`); A2–A5 are unconnected/floating (the TUI dims them).
- `net.wifi` ∈ {`up`,`down`,`off`}; `net.push` ∈ {`ok`,`fail`,`n/a`} reflecting the
  last cloud push. When `ENABLE_UPLOAD=0`, `net` reports `off`/`n/a`.
- `board.up_s` = uptime seconds; `board.heap_b` = free RAM bytes.
- Built with a fixed-capacity `ArduinoJson` `JsonDocument` and `serializeJson(doc,
  Serial)` + newline.

**Frame producer wiring:** `telemetryBegin()` (sets `analogReadResolution(14)`)
called from `setup()`; `telemetryTick()` from `loop()`. `net`/`board` fields are
fed from existing state (last push result, `WiFi.status()/RSSI()/localIP()`,
free-heap helper). When `ENABLE_UPLOAD=0`, net fields are static (`off`).

## Component 2 — Periodic OTA polling

Today `otaCheckAndApply()` runs once in `setup()`. Add a periodic, non-blocking
call in `loop()`:

- New `OTA_CHECK_INTERVAL_MS` in `config.h` (default `300000UL` = 5 min).
- In `loop()` (under `#if ENABLE_UPLOAD`), every `OTA_CHECK_INTERVAL_MS`: ensure
  WiFi, then `otaCheckAndApply()` (fetch `version.txt`, compare, download+apply if
  newer — applying reboots into the new image).
- The boot-time check in `setup()` stays. The existing EEPROM consecutive-failure
  guard (`OTA_MAX_FAILURES`) continues to apply, so a flaky network can't loop.
- Cost: a small HTTPS GET of `version.txt` every 5 min; the binary is fetched only
  when the version changes.

## Component 3 — TUI client (`tools/board-tui/`)

Files:
```
tools/board-tui/
  board_tui.py        # CLI entry + Rich Live render loop
  sources.py          # input sources: Serial / Simulate / Replay (+ record)
  frames.py           # parse_frame(); frame dataclass/validation helpers
  requirements.txt    # rich, pyserial
  tests/
    test_frames.py    # parse valid/invalid/banner lines
    test_sources.py   # simulate yields valid frames; replay reads fixture
    test_render.py    # render functions build panels without error
    fixtures/sample.ndjson
  README.md
```

- **`frames.parse_frame(line: str) -> dict | None`** — pure: strip, JSON-parse,
  return the dict if it looks like a telemetry frame (has `pins`/`sensors`), else
  `None` (so banner/OTA log lines are ignored).
- **`sources.py`** — a common iterator interface yielding raw lines:
  - `SerialSource(port, baud=115200)` — pyserial; `--port` optional, else
    auto-detect (`/dev/tty.usbmodem*`, `/dev/ttyACM*`). Reconnect loop on drop.
  - `SimulateSource(seed?)` — synthesizes plausible frames (~1Hz wall clock; vary
    by an internal counter, not RNG-only, so output is deterministic-ish for
    tests) so the TUI runs with no hardware.
  - `ReplaySource(path, realtime=False)` — yields lines from a captured `.ndjson`.
  - `--record PATH` tees live lines to a file.
- **`board_tui.py`** — Rich `Live` layout:
  - Header: device · fw · source (serial port / SIM / replay) · connection state ·
    board uptime.
  - **Pins** panel: A0–A5 raw with mini bars; A2–A5 dimmed/"floating".
  - **Sensors** panel: bed1/bed2 % bars + raw, temp/hum, DHT ok indicator.
  - **Net/Board** panel: wifi/rssi/ip, free heap, last push result.
  - **Raw** footer: last N raw frames (scrolling), plus a "stale: Ns since last
    frame" indicator that turns red on disconnect.
  - Render functions are **pure** (`frame dict -> Rich renderable`) for testability;
    the Live loop just pulls frames and repaints.
- CLI: `python board_tui.py [--port P] [--baud N] [--simulate] [--replay F] [--record F] [--once]`.
  `--once` prints a single rendered snapshot and exits (used in tests/CI).

## Data flow

board `loop()` → `telemetryTick()` → `Serial` NDJSON → USB → `SerialSource` →
`parse_frame` → latest-frame state → Rich `Live` repaint (~4–10 Hz, showing the
most recent frame). Simulate/replay sources substitute for the serial leg.

## Error handling

- TUI: malformed/non-JSON line → ignored by `parse_frame` (logged to raw footer
  only). Serial open failure / disconnect → banner "disconnected — retrying",
  auto-reopen with backoff; never crash. No board found on auto-detect → clear
  message listing candidate ports and the `--simulate` hint.
- Firmware: telemetry never blocks the control loop; a failed DHT read emits
  `dhtOk:false` and last-good cached values. OTA failures increment the existing
  counter and are skipped past `OTA_MAX_FAILURES`.

## Delivery & verification (this session)

1. Implement firmware (telemetry + periodic OTA) and the TUI (TDD on TUI logic).
2. Compile firmware locally with `arduino-cli` (verify build).
3. Run the TUI in `--simulate` to verify the dashboard renders.
4. `./scripts/release-firmware.sh 1.0.1` → CI builds the `.ota` and publishes the
   GitHub Release.
5. Board self-updates within ≤5 min via periodic OTA (no manual reset needed).
6. Run the TUI over USB against the updated board → live frames. (Fallback if
   needed: USB-flash via `flash.sh`, since the board is plugged in.)

**Operational note:** USB serial is single-owner — the Arduino IDE Serial Monitor
must be closed while the TUI holds the port.

## Testing

- `test_frames.py`: valid frame parses; banner/OTA/log lines → `None`; truncated
  JSON → `None`.
- `test_sources.py`: `SimulateSource` yields frames that `parse_frame` accepts and
  that contain `pins.A0..A5` + `sensors.soil`; `ReplaySource` reads the fixture and
  yields the expected count.
- `test_render.py`: each panel render fn and the full layout build without error
  from a sample frame and from an empty/no-frame-yet state; `--once` exits 0.
- Firmware: verified by `arduino-cli compile` and live via the TUI on hardware.

## Success criteria

1. With the board on v1.0.1 and plugged in, `python board_tui.py` shows a live
   dashboard updating ~1Hz: A0–A5 raw, bed1/bed2 %, temp/hum, wifi/rssi/ip, heap,
   uptime, push status.
2. `python board_tui.py --simulate` runs a full dashboard with no hardware.
3. The board picks up the OTA release within ~5 min with no manual reboot.
4. TUI logic (parse/sources/render) is covered by passing unit tests with no
   hardware.
