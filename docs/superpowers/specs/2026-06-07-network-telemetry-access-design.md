# Network Telemetry Access (board TCP server) — Design

**Date:** 2026-06-07
**Status:** Approved (brainstorming) — pending implementation plan
**Extends:** [[2026-06-07-board-tui-and-serial-telemetry-design]] (same frame schema)

## Problem

The board TUI currently reads the live telemetry stream only over USB serial,
which tethers the viewer to the board. We want to watch the board from any
machine on the LAN, without USB.

## Goal

Add a TCP server on the board that streams the **exact same** ~1Hz NDJSON
telemetry frames to a connected client, and a `TcpSource` in the TUI so it can
read from `host:port` instead of serial. Fold into the unreleased
`feat/board-tui-serial-telemetry` branch so a single OTA release (v1.0.1) carries
serial + network telemetry + periodic OTA.

Decisions (from brainstorming): **single viewer, newest-connection-wins**; **no
access control** (LAN-only, read-only, non-sensitive); coexists with USB serial.

Non-goals (YAGNI): multiple concurrent clients; TLS/auth; control commands over
the socket (read-only telemetry only); discovery/mDNS.

## Component 1 — Firmware TCP server (`telemetry.cpp`, `config.h`)

- New constant `TELEMETRY_TCP_PORT` in `config.h` (default `8766`).
- The TCP path is compiled only under `#if ENABLE_UPLOAD` (WiFi present). In
  `ENABLE_UPLOAD=0` bench mode there is no WiFi, so serial-only — unchanged.
- Module statics (under `#if ENABLE_UPLOAD`): `WiFiServer s_server(TELEMETRY_TCP_PORT)`
  and one `WiFiClient s_client` (the single newest viewer).
- `telemetryBegin()` calls `s_server.begin()` (under `#if ENABLE_UPLOAD`).
- `telemetryTick()` change: serialize the frame **once** into a fixed stack
  buffer `char buf[512]` via `serializeJson(doc, buf, sizeof(buf))`, then:
  1. `Serial.println(buf)` — unchanged USB behavior.
  2. (`#if ENABLE_UPLOAD`) newest-wins adopt: `WiFiClient c = s_server.available();`
     if `c` is truthy, `s_client.stop()` then `s_client = c;`.
  3. Drain inbound bytes so a chatty client can't back up: `while (s_client &&
     s_client.available()) s_client.read();`.
  4. If `s_client && s_client.connected()`, `s_client.println(buf)`. Otherwise skip.
- Buffer sizing: a frame is ~300 bytes; `buf[512]` has margin. If
  `serializeJson` ever truncates (returns 0 / overflow), still print what serial
  has — acceptable for a monitor. (Frames are bounded: 6 pins + 2 soil + fixed
  net/board keys.)
- RAM cost: one extra socket + 512B stack buffer; well within the 25%-used budget
  measured at `ENABLE_UPLOAD=1`.

`WiFiServer.available()` (WiFiS3) returns a `WiFiClient` for an incoming/active
connection; reassigning `s_client` and stopping the prior one implements
newest-wins without needing `accept()` (max compatibility with the R4 core).

## Component 2 — TUI `TcpSource` (`sources.py`, `board_tui.py`)

- `TcpSource(host, port=8766, connect_fn=None, reconnect_delay_s=1.0)` — mirrors
  `SerialSource`:
  - Iterable yielding decoded text lines; **never raises** on drop — closes,
    waits `reconnect_delay_s`, reconnects, resumes. The TUI's staleness indicator
    covers the gap.
  - Reads with a buffered line splitter over `socket.recv()` (frames are
    newline-delimited).
  - `connect_fn` is injectable for tests; default uses
    `socket.create_connection((host, port), timeout=5)`.
- `board_tui.py` CLI: add `--host HOST[:PORT]` (default port `8766`). Source
  precedence in `_build_source`: `--host` → `TcpSource`; elif `--simulate` /
  `--replay`; else serial autodetect. Source label `tcp:<host>:<port>`.
- `--record` and `--once` work with TCP unchanged (they operate on lines).

## Data flow

board `telemetryTick()` → one serialized frame → (Serial.println) **and**
(`s_client.println` when connected) → TCP → `TcpSource` → `parse_frame` → same
`DashboardState` / Rich render. The serial and TCP legs are interchangeable
producers of identical lines.

## Error handling

- Board: no client connected → frames still go to Serial; TCP write is skipped.
  A new connection supersedes a stale one on the next tick (newest-wins), so a
  half-open client cannot wedge the port. Inbound bytes are drained, not parsed.
- TUI: connection refused / reset / timeout → `TcpSource` retries with backoff,
  never crashing; staleness shows in the header. Bad/partial line → `parse_frame`
  returns None (ignored), same as serial.

## Testing

- `test_sources.py::test_tcp_source_reconnects_across_error` — `connect_fn`
  returns a scripted fake socket (yields two framed chunks, then raises on
  `recv`, then a fresh socket yields one more); assert lines parse and a reconnect
  occurred.
- `test_sources.py::test_tcp_source_line_splitting` — a fake socket delivering a
  frame split across two `recv()` chunks yields exactly one complete line.
- `test_sources.py::test_tcp_source_loopback` — a `socketserver.TCPServer` in a
  thread writes 2 NDJSON frames; a real `TcpSource` connects and reads both;
  `parse_frame` accepts them. Hardware-free, CI-safe (binds `127.0.0.1:0`).
- Firmware: `arduino-cli compile --fqbn arduino:renesas_uno:unor4wifi` with
  `ENABLE_UPLOAD=1` (the TCP branch) succeeds.

## Delivery & verification

1. Implement firmware TCP server + TUI `TcpSource` (TDD on the Python side).
2. `arduino-cli` compile-verify both `ENABLE_UPLOAD` modes.
3. Finish the branch → merge to `main`.
4. Cut **one** `v1.0.1` OTA release (serial + network + periodic OTA).
5. Board self-updates within ~5 min. Test both: `board_tui.py --port <usb>` and
   `board_tui.py --host <board-ip>` (find the IP in the dashboard's `net.ip`,
   from a USB session or the cloud).

**Note:** the port is open on the LAN with no auth; the stream is read-only and
the board cannot be controlled through it.

## Success criteria

1. With the board on v1.0.1 and on WiFi, `python board_tui.py --host <board-ip>`
   shows the same live dashboard as the USB path, updating ~1Hz.
2. Connecting a second viewer transparently takes over (newest-wins); the first
   simply goes stale.
3. `TcpSource` reconnects automatically after the board reboots or WiFi blips.
4. TUI TCP logic is covered by passing unit + loopback tests with no hardware.
5. USB serial viewing is unaffected.
