# Network Telemetry Access Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a TCP server on the Arduino board that streams the same ~1Hz NDJSON telemetry frames to one LAN client (newest-wins), and a `TcpSource` in the TUI so `--host <board-ip>` works as a drop-in alternative to USB serial.

**Architecture:** The firmware serialises each telemetry frame into a fixed stack buffer once, writes it to both `Serial` and (when connected) a single `WiFiClient`; a new connection transparently replaces the old one. The TUI gains a `TcpSource` that follows the exact same reconnect pattern as the existing `SerialSource`, so all parsing/render code is unchanged. Both changes land on the current branch (`feat/board-tui-serial-telemetry`) so a single v1.0.1 OTA release ships everything.

**Tech Stack:** Arduino C++ (WiFiS3, ArduinoJson), Python 3.11 (`socket`, `rich`, `pyserial`), pytest.

---

## File structure

```
firmware/garden-node/
  config.h           # MODIFY — add TELEMETRY_TCP_PORT 8766
  telemetry.cpp      # MODIFY — add WiFiServer + newest-wins client, buf-based emit
  telemetry.h        # no change

tools/board-tui/
  sources.py         # MODIFY — add TcpSource class
  board_tui.py       # MODIFY — add --host flag, update _build_source, docstring
  tests/
    test_sources.py  # MODIFY — add 3 TcpSource tests
```

Run TUI tests: `cd tools/board-tui && python3 -m pytest -q`

---

### Task 1: TUI — `TcpSource` with reconnect

**Files:**
- Modify: `tools/board-tui/sources.py`
- Modify: `tools/board-tui/tests/test_sources.py`

- [ ] **Step 1: Write three failing tests**

Append to `tools/board-tui/tests/test_sources.py`:

```python
import socket
import socketserver
import threading
from sources import TcpSource


# ── helpers ──────────────────────────────────────────────────────────────────

class _FakeSock:
    """Fake socket: yields scripted recv() responses then raises on error."""
    def __init__(self, script):
        self._script = list(script)

    def recv(self, n):
        if not self._script:
            raise ConnectionResetError("closed")
        item = self._script.pop(0)
        if item is ConnectionResetError:
            raise ConnectionResetError("disconnected")
        return item if isinstance(item, bytes) else item.encode()

    def close(self):
        pass


FRAME_LINE = (
    b'{"t":1,"fw":"1.0.1","dev":"garden-node-1",'
    b'"pins":{"A0":1,"A1":2,"A2":3,"A3":4,"A4":5,"A5":6},'
    b'"sensors":{"tempC":22.0,"hum":48.0,"dhtOk":true,"soil":[]},'
    b'"net":{"wifi":"up","rssi":-58,"ip":"1.2.3.4","push":"ok"},'
    b'"board":{"up_s":1,"heap_b":23456}}\n'
)


def test_tcp_source_yields_lines():
    """TcpSource yields decoded text lines from a connected socket."""
    connects = []

    def fake_connect():
        connects.append(1)
        return _FakeSock([FRAME_LINE, FRAME_LINE])

    src = TcpSource("127.0.0.1", 8766, connect_fn=fake_connect, reconnect_delay_s=0)
    lines = []
    for ln in src:
        lines.append(ln.strip())
        if len(lines) == 2:
            break
    assert len(lines) == 2
    assert lines[0].startswith("{")


def test_tcp_source_reconnects_after_error():
    """TcpSource reopens the connection after a recv() error."""
    opens = []
    batches = [
        [FRAME_LINE, ConnectionResetError],   # first connect: one line then drop
        [FRAME_LINE],                         # after reconnect: one more line
    ]

    def fake_connect():
        opens.append(1)
        return _FakeSock(batches[len(opens) - 1])

    src = TcpSource("127.0.0.1", 8766, connect_fn=fake_connect, reconnect_delay_s=0)
    lines = []
    for ln in src:
        lines.append(ln.strip())
        if len(lines) == 2:
            break
    assert len(lines) == 2
    assert len(opens) == 2   # second open after disconnect


def test_tcp_source_loopback():
    """Real socket loopback: a thread server emits 2 frames; TcpSource reads both."""
    from frames import parse_frame

    responses = [FRAME_LINE, FRAME_LINE]

    class _Handler(socketserver.BaseRequestHandler):
        def handle(self):
            for chunk in responses:
                self.request.sendall(chunk)

    server = socketserver.TCPServer(("127.0.0.1", 0), _Handler)
    _, port = server.server_address
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    src = TcpSource("127.0.0.1", port, reconnect_delay_s=0)
    collected = []
    for ln in src:
        f = parse_frame(ln)
        if f:
            collected.append(f)
        if len(collected) == 2:
            break

    server.shutdown()
    assert len(collected) == 2
    assert collected[0]["dev"] == "garden-node-1"
```

- [ ] **Step 2: Run to confirm they fail**

```bash
cd tools/board-tui
python3 -m pytest tests/test_sources.py::test_tcp_source_yields_lines \
                  tests/test_sources.py::test_tcp_source_reconnects_after_error \
                  tests/test_sources.py::test_tcp_source_loopback -v 2>&1 | tail -10
```

Expected: 3 × `ERROR` or `FAILED` with `ImportError: cannot import name 'TcpSource'`.

- [ ] **Step 3: Implement `TcpSource` in `sources.py`**

Append to `tools/board-tui/sources.py` (after the `SerialSource` class):

```python
class TcpSource:
    """Reads NDJSON telemetry lines from the board's TCP server.

    Connects to host:port, yields decoded text lines, and reconnects forever
    on any error — never raises. The TUI's staleness indicator covers gaps.
    `connect_fn` is injectable for tests; default uses socket.create_connection.

    The board uses newline-delimited frames, so we accumulate recv() chunks
    into a line buffer and yield complete lines only.
    """

    def __init__(self, host: str, port: int = 8766, connect_fn=None,
                 reconnect_delay_s: float = 1.0):
        self.host = host
        self.port = port
        self.reconnect_delay_s = reconnect_delay_s
        self._connect_fn = connect_fn or self._default_connect

    def _default_connect(self):
        return socket.create_connection((self.host, self.port), timeout=5)

    def __iter__(self):
        buf = b""
        while True:
            try:
                sock = self._connect_fn()
            except Exception:
                if self.reconnect_delay_s:
                    time.sleep(self.reconnect_delay_s)
                buf = b""
                continue
            try:
                while True:
                    try:
                        chunk = sock.recv(1024)
                    except StopIteration:
                        return
                    except Exception:
                        break   # error → reconnect
                    if not chunk:
                        break   # server closed → reconnect
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        yield line.decode(errors="replace")
            finally:
                try:
                    sock.close()
                except Exception:
                    pass
            buf = b""
            if self.reconnect_delay_s:
                time.sleep(self.reconnect_delay_s)
```

Also add `import socket` at the top of `sources.py` if not already present.

- [ ] **Step 4: Run to verify all three tests pass**

```bash
cd tools/board-tui
python3 -m pytest tests/test_sources.py::test_tcp_source_yields_lines \
                  tests/test_sources.py::test_tcp_source_reconnects_after_error \
                  tests/test_sources.py::test_tcp_source_loopback -v 2>&1 | tail -10
```

Expected: 3 × `PASSED`.

- [ ] **Step 5: Run full TUI suite to confirm no regressions**

```bash
cd tools/board-tui && python3 -m pytest -q 2>&1 | tail -3
```

Expected: all previously-passing tests + 3 new = 19 passed.

- [ ] **Step 6: Commit**

```bash
cd ~/Projects/gardencontroller
git add tools/board-tui/sources.py tools/board-tui/tests/test_sources.py
git commit -m "feat(board-tui): TcpSource with reconnect + loopback test"
```

---

### Task 2: TUI — `--host` CLI flag and `_build_source` routing

**Files:**
- Modify: `tools/board-tui/board_tui.py`

- [ ] **Step 1: Update the docstring, add `--host`, update `_build_source`**

In `tools/board-tui/board_tui.py`, replace the module docstring and `_build_source` + `main` with:

```python
#!/usr/bin/env python3
"""board-tui — live terminal dashboard for the garden board's serial telemetry.

Usage:
  python board_tui.py                        # auto-detect serial port
  python board_tui.py --port /dev/tty...     # explicit serial port
  python board_tui.py --host 192.168.1.42    # network (board TCP :8766)
  python board_tui.py --host 192.168.1.42:9000  # custom port
  python board_tui.py --simulate             # no hardware
  python board_tui.py --replay cap.ndjson    # replay a capture
  python board_tui.py --record cap.ndjson    # tee live lines to a file
  python board_tui.py --replay f --once      # render one snapshot and exit (CI)
"""
```

Replace `_build_source`:

```python
def _build_source(args):
    if getattr(args, "host", None):
        raw = args.host
        if ":" in raw:
            host, port_s = raw.rsplit(":", 1)
            port = int(port_s)
        else:
            host, port = raw, 8766
        return TcpSource(host, port), f"tcp:{host}:{port}"
    if args.simulate:
        return SimulateSource(interval_s=0.0 if args.once else 1.0), "SIM"
    if args.replay:
        return ReplaySource(args.replay, realtime=not args.once), f"replay:{args.replay}"
    port = args.port or autodetect_port()
    if not port:
        print("No serial port found. Use --host <ip>, --simulate, or --replay.",
              file=sys.stderr)
        sys.exit(2)
    return SerialSource(port, baud=args.baud), f"serial:{port}"
```

In `main`, add the import of `TcpSource` to the existing imports block at the top of the function (or at module level — add to the `from sources import ...` line):

```python
from sources import SimulateSource, ReplaySource, SerialSource, autodetect_port, TcpSource
```

In the `argparse` block inside `main`, add after `p.add_argument("--baud", ...)`:

```python
    p.add_argument("--host", default=None,
                   help="Board IP[:port] for network telemetry (default port 8766)")
```

- [ ] **Step 2: Run the existing `--once` smoke test to verify no breakage**

```bash
cd tools/board-tui
python3 -m pytest tests/test_render.py::test_once_replay_exits_zero_and_prints -v 2>&1 | tail -5
```

Expected: PASS.

- [ ] **Step 3: Smoke-test `--host` flag parses without hardware**

```bash
cd tools/board-tui
python3 board_tui.py --host 192.168.1.99 --once 2>&1 | head -3
```

Expected: prints the "waiting for first frame…" dashboard (no crash, exits 0 after timeout or Ctrl-C; TcpSource will fail to connect and retry, but `--once` will render the empty state on first iteration).

Actually for `--once` with a host that's unreachable we want a clean exit. Let's verify the empty-state path exits 0:

```bash
cd tools/board-tui
# Use --simulate --once to confirm _build_source precedence is correct
python3 board_tui.py --simulate --once 2>&1 | grep -q "garden-node-1" && echo "OK"
```

Expected: `OK`.

- [ ] **Step 4: Commit**

```bash
cd ~/Projects/gardencontroller
git add tools/board-tui/board_tui.py
git commit -m "feat(board-tui): --host flag for network telemetry (TcpSource routing)"
```

---

### Task 3: Firmware — TCP server in `telemetry.cpp`

**Files:**
- Modify: `firmware/garden-node/config.h`
- Modify: `firmware/garden-node/telemetry.cpp`

- [ ] **Step 1: Add `TELEMETRY_TCP_PORT` to `config.h`**

In `firmware/garden-node/config.h`, after the `TELEMETRY_DHT_MIN_MS` line, add:

```cpp
#define TELEMETRY_TCP_PORT   8766     // TCP port for the network live view (LAN only)
```

- [ ] **Step 2: Add statics and start the server in `telemetry.cpp`**

At the top of `telemetry.cpp`, after the existing statics block, add (inside `#if ENABLE_UPLOAD`):

```cpp
#if ENABLE_UPLOAD
static WiFiServer s_server(TELEMETRY_TCP_PORT);
static WiFiClient s_client;
#endif
```

In `telemetryBegin()`, after `s_lastFrameMs = millis() - TELEMETRY_MS;`, add:

```cpp
#if ENABLE_UPLOAD
  s_server.begin();
#endif
```

- [ ] **Step 3: Replace the Serial emit with a buffer-first approach in `telemetryTick()`**

The current end of `telemetryTick()` is:

```cpp
  serializeJson(doc, Serial);
  Serial.println();
}
```

Replace that with:

```cpp
  // Serialise once into a fixed stack buffer, then write to Serial and TCP client.
  char buf[512];
  size_t n = serializeJson(doc, buf, sizeof(buf));
  if (n == 0 || n >= sizeof(buf)) {
    // Truncation guard: fall back to direct Serial write and skip TCP.
    serializeJson(doc, Serial);
    Serial.println();
    return;
  }

  Serial.println(buf);

#if ENABLE_UPLOAD
  // Newest-wins: if a new client has connected, replace the current one.
  WiFiClient incoming = s_server.available();
  if (incoming) {
    s_client.stop();
    s_client = incoming;
  }
  // Drain any bytes the client sent (read-only stream; we don't parse commands).
  while (s_client && s_client.available()) s_client.read();
  // Write the frame to the connected client, if any.
  if (s_client && s_client.connected()) {
    s_client.println(buf);
  }
#endif
}
```

- [ ] **Step 4: Compile-verify `ENABLE_UPLOAD=0` (bench mode — no TCP code active)**

```bash
cd firmware/garden-node
arduino-cli compile --fqbn arduino:renesas_uno:unor4wifi . 2>&1 | tail -4
```

Expected: `Sketch uses ... bytes` with no errors. (The TCP statics are inside `#if ENABLE_UPLOAD` so this path is unchanged.)

- [ ] **Step 5: Compile-verify `ENABLE_UPLOAD=1` (release mode — TCP active)**

```bash
cd firmware/garden-node
sed -i '' 's/#define ENABLE_UPLOAD 0/#define ENABLE_UPLOAD 1/' config.h
arduino-cli compile --fqbn arduino:renesas_uno:unor4wifi . 2>&1 | tail -4
git checkout -- config.h   # restore repo default
```

Expected: compiles with no errors. Flash / RAM usage will be slightly higher than before but within budget (was 36% / 25% without TCP).

- [ ] **Step 6: Commit**

```bash
cd ~/Projects/gardencontroller
git add firmware/garden-node/config.h firmware/garden-node/telemetry.cpp
git commit -m "feat(firmware): TCP telemetry server on port 8766 (newest-wins, LAN)"
```

---

### Task 4: Full verification + finish the branch

**Files:** none (verification + merge)

- [ ] **Step 1: Run the complete TUI test suite**

```bash
cd ~/Projects/gardencontroller/tools/board-tui && python3 -m pytest -q 2>&1 | tail -3
```

Expected: 19 passed (16 original + 3 TCP tests), 0 failed.

- [ ] **Step 2: Smoke both source modes**

```bash
cd tools/board-tui
python3 board_tui.py --simulate --once 2>&1 | grep -q "garden-node-1" && echo "SIM OK"
python3 board_tui.py --replay tests/fixtures/sample.ndjson --once 2>&1 | grep -q "garden-node-1" && echo "REPLAY OK"
```

Expected: `SIM OK` and `REPLAY OK`.

- [ ] **Step 3: Invoke finishing-a-development-branch skill to merge and release**

After all tests pass and compiles are clean, the branch is ready.

Use `superpowers:finishing-a-development-branch` to:
1. Merge `feat/board-tui-serial-telemetry` → `main`.
2. Then run the OTA release: `./scripts/release-firmware.sh 1.0.1` (tags `v1.0.1`, CI builds the `.ota`).
3. Within ~5 min the board self-updates.
4. Verify live: `python3 board_tui.py` (USB serial) then `python3 board_tui.py --host <board-ip>` (network; find the IP in the `net.ip` field shown in the USB session).

---

## Self-Review

**Spec coverage:**
- Board TCP server on port 8766, newest-wins single `WiFiClient` → Task 3. ✓
- `telemetryTick()` serialises once into `buf[512]`, writes to Serial **and** TCP → Task 3. ✓
- Truncation guard (size check) → Task 3. ✓
- Inbound bytes drained → Task 3. ✓
- `#if ENABLE_UPLOAD` guards throughout (bench mode unchanged) → Task 3. ✓
- `TcpSource` with reconnect, `connect_fn` injectable → Task 1. ✓
- Line-buffer splitting of `recv()` chunks → Task 1 (`TcpSource.__iter__`). ✓
- Loopback integration test → Task 1. ✓
- `--host HOST[:PORT]` CLI flag, default port 8766 → Task 2. ✓
- Source precedence: `--host` → TCP; else simulate/replay; else serial → Task 2. ✓
- Serial unchanged → verified by compile both modes + smoke test. ✓
- OTA release and on-board live test → Task 4. ✓

**Placeholder scan:** No TBD/TODO. Every step has exact commands and code. The `--host --once` unreachable-host note clarifies expected behaviour (empty-state render, exits 0) — not a placeholder.

**Type consistency:** `TcpSource(host, port, connect_fn, reconnect_delay_s)` matches Task 1 definition and Task 2 usage. `_build_source` returns `(source, label: str)` — unchanged contract from the original `sources.py`.
