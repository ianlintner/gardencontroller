# Board TUI + Serial Telemetry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Python (Rich) terminal dashboard that shows the garden board's live state (all analog pins, translated sensors, board/net metrics) from a ~1Hz NDJSON serial stream — plus the firmware change that emits that stream and a periodic (5-min) OTA check — delivered via an OTA release.

**Architecture:** Firmware gains a non-blocking `telemetry` module that prints one NDJSON frame per second over USB serial, and a periodic OTA poll in `loop()`. A standalone `tools/board-tui/` Python app reads frames from a swappable source (serial / simulate / replay), parses them, and renders a Rich `Live` dashboard. The serial frame schema is the contract between the two.

**Tech Stack:** Arduino C++ (UNO R4 WiFi, ArduinoJson), Python 3.11 (`rich`, `pyserial`), pytest, arduino-cli.

---

## File Structure

```
tools/board-tui/
  frames.py          # parse_frame() + frame field helpers (pure)
  sources.py         # SimulateSource, ReplaySource, SerialSource, autodetect_port
  render.py          # pure frame-state -> Rich renderables
  board_tui.py       # CLI + reader thread + Live loop
  requirements.txt   # rich, pyserial
  README.md
  tests/
    conftest.py
    test_frames.py
    test_sources.py
    test_render.py
    fixtures/sample.ndjson
firmware/garden-node/
  telemetry.h        # NEW
  telemetry.cpp      # NEW
  sensors.h          # MODIFY: add sensorsReadDht()
  sensors.cpp        # MODIFY: implement sensorsReadDht()
  config.h           # MODIFY: TELEMETRY_MS, TELEMETRY_DHT_MIN_MS, OTA_CHECK_INTERVAL_MS
  garden-node.ino    # MODIFY: wire telemetry + periodic OTA
```

TUI tests: `cd tools/board-tui && python3 -m pytest -q`

---

### Task 1: TUI scaffold + `parse_frame`

**Files:**
- Create: `tools/board-tui/frames.py`, `tools/board-tui/tests/test_frames.py`, `tools/board-tui/tests/conftest.py`, `tools/board-tui/requirements.txt`

- [ ] **Step 1: Create requirements + conftest**

`tools/board-tui/requirements.txt`:
```
rich>=13.0
pyserial>=3.5
```

`tools/board-tui/tests/conftest.py`:
```python
import sys
from pathlib import Path

# Make the board-tui modules importable as top-level (frames, sources, render).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
```

- [ ] **Step 2: Write the failing test**

`tools/board-tui/tests/test_frames.py`:
```python
import json
from frames import parse_frame

VALID = json.dumps({
    "t": 12345, "fw": "1.0.1", "dev": "garden-node-1",
    "pins": {"A0": 16021, "A1": 11002, "A2": 8190, "A3": 8175, "A4": 8201, "A5": 8188},
    "sensors": {"tempC": 22.4, "hum": 48.2, "dhtOk": True,
                "soil": [{"probe": "bed1", "pin": "A0", "raw": 16021, "pct": 12.5}]},
    "net": {"wifi": "up", "rssi": -58, "ip": "192.168.1.42", "push": "ok"},
    "board": {"up_s": 1234, "heap_b": 23456},
})


def test_parses_valid_frame():
    f = parse_frame(VALID)
    assert f is not None
    assert f["dev"] == "garden-node-1"
    assert f["pins"]["A0"] == 16021


def test_ignores_banner_line():
    assert parse_frame("garden-node booting: garden-node-1 @ raised-bed") is None


def test_ignores_ota_log_line():
    assert parse_frame("OTA: up to date") is None


def test_ignores_truncated_json():
    assert parse_frame('{"pins": {"A0": 1') is None


def test_ignores_json_without_telemetry_keys():
    # valid JSON, but not a telemetry frame
    assert parse_frame('{"hello": "world"}') is None


def test_ignores_blank_line():
    assert parse_frame("   ") is None
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd tools/board-tui && python3 -m pytest tests/test_frames.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'frames'`)

- [ ] **Step 4: Implement `frames.py`**

```python
"""Telemetry frame parsing — pure, no I/O."""
from __future__ import annotations

import json


def parse_frame(line: str) -> dict | None:
    """Parse one serial line into a telemetry frame dict, or None.

    Returns None for blank lines, non-JSON banner/log lines, malformed JSON,
    and JSON objects that are not telemetry frames (missing pins+sensors).
    """
    s = (line or "").strip()
    if not s or s[0] != "{":
        return None
    try:
        obj = json.loads(s)
    except (ValueError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None
    if "pins" not in obj or "sensors" not in obj:
        return None
    return obj
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd tools/board-tui && python3 -m pytest tests/test_frames.py -q`
Expected: PASS (6 passed)

- [ ] **Step 6: Commit**

```bash
cd ~/Projects/gardencontroller
git add tools/board-tui/frames.py tools/board-tui/tests/ tools/board-tui/requirements.txt
git commit -m "feat(board-tui): frame parser + scaffold"
```

---

### Task 2: Simulate + Replay sources

**Files:**
- Create: `tools/board-tui/sources.py`, `tools/board-tui/tests/fixtures/sample.ndjson`, `tools/board-tui/tests/test_sources.py`

- [ ] **Step 1: Create the replay fixture**

`tools/board-tui/tests/fixtures/sample.ndjson` (3 lines: a banner, a valid frame, a log line — so replay exercises filtering):
```
garden-node booting: garden-node-1 @ raised-bed
{"t":1000,"fw":"1.0.1","dev":"garden-node-1","pins":{"A0":16021,"A1":11002,"A2":8190,"A3":8175,"A4":8201,"A5":8188},"sensors":{"tempC":22.4,"hum":48.2,"dhtOk":true,"soil":[{"probe":"bed1","pin":"A0","raw":16021,"pct":12.5},{"probe":"bed2","pin":"A1","raw":11002,"pct":78.0}]},"net":{"wifi":"up","rssi":-58,"ip":"192.168.1.42","push":"ok"},"board":{"up_s":1,"heap_b":23456}}
OTA: up to date
```

- [ ] **Step 2: Write the failing test**

`tools/board-tui/tests/test_sources.py`:
```python
from pathlib import Path
from frames import parse_frame
from sources import SimulateSource, ReplaySource

FIX = Path(__file__).parent / "fixtures" / "sample.ndjson"


def test_simulate_yields_parseable_frames():
    src = SimulateSource()
    it = iter(src)
    lines = [next(it) for _ in range(3)]
    frames = [parse_frame(ln) for ln in lines]
    assert all(f is not None for f in frames)
    f = frames[0]
    # all six analog pins present
    assert set(f["pins"]) == {"A0", "A1", "A2", "A3", "A4", "A5"}
    # two translated soil probes
    probes = {s["probe"] for s in f["sensors"]["soil"]}
    assert probes == {"bed1", "bed2"}


def test_simulate_uptime_advances():
    it = iter(SimulateSource())
    f0 = parse_frame(next(it))
    f1 = parse_frame(next(it))
    assert f1["board"]["up_s"] >= f0["board"]["up_s"]


def test_replay_reads_only_valid_frames():
    src = ReplaySource(FIX)
    frames = [parse_frame(ln) for ln in src]
    valid = [f for f in frames if f is not None]
    assert len(valid) == 1
    assert valid[0]["sensors"]["soil"][1]["probe"] == "bed2"
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd tools/board-tui && python3 -m pytest tests/test_sources.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'sources'`)

- [ ] **Step 4: Implement Simulate + Replay in `sources.py`**

```python
"""Input sources for the board TUI: simulate, replay, serial.

Each source is iterable and yields raw text lines (str). parse_frame() filters
non-telemetry lines downstream, so sources may emit banner/log lines too.
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path


class SimulateSource:
    """Synthesizes plausible telemetry frames (~1 Hz) with no hardware.

    Deterministic-ish: values are driven by an internal tick counter so tests
    don't depend on RNG. Soil A0 dries down, A1 stays wet, temp/hum wave gently.
    """

    def __init__(self, interval_s: float = 0.0):
        self.interval_s = interval_s
        self._tick = 0

    def __iter__(self):
        return self

    def __next__(self) -> str:
        if self.interval_s:
            time.sleep(self.interval_s)
        n = self._tick
        self._tick += 1
        a0 = 16021 - (n * 7) % 5000           # bed1 drying
        a1 = 11002 + int(300 * math.sin(n / 5))
        floating = [8190, 8175, 8201, 8188]
        pins = {"A0": a0, "A1": a1,
                "A2": floating[0], "A3": floating[1],
                "A4": floating[2], "A5": floating[3]}
        # crude raw->pct (dry≈16374, wet≈10700) just for display
        def pct(raw):
            p = (16374 - raw) / (16374 - 10700) * 100
            return round(max(0.0, min(100.0, p)), 1)
        frame = {
            "t": n * 1000, "fw": "1.0.1", "dev": "garden-node-1",
            "pins": pins,
            "sensors": {
                "tempC": round(22.0 + math.sin(n / 8), 1),
                "hum": round(48.0 + 3 * math.cos(n / 6), 1),
                "dhtOk": True,
                "soil": [
                    {"probe": "bed1", "pin": "A0", "raw": a0, "pct": pct(a0)},
                    {"probe": "bed2", "pin": "A1", "raw": a1, "pct": pct(a1)},
                ],
            },
            "net": {"wifi": "up", "rssi": -58 - (n % 5), "ip": "192.168.1.42", "push": "ok"},
            "board": {"up_s": n, "heap_b": 23456 - (n % 100)},
        }
        return json.dumps(frame)


class ReplaySource:
    """Yields lines from a captured .ndjson file (one read-through)."""

    def __init__(self, path, realtime: bool = False, interval_s: float = 0.1):
        self.path = Path(path)
        self.realtime = realtime
        self.interval_s = interval_s

    def __iter__(self):
        with self.path.open() as fh:
            for line in fh:
                if self.realtime:
                    time.sleep(self.interval_s)
                yield line.rstrip("\n")
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd tools/board-tui && python3 -m pytest tests/test_sources.py -q`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
cd ~/Projects/gardencontroller
git add tools/board-tui/sources.py tools/board-tui/tests/test_sources.py tools/board-tui/tests/fixtures/
git commit -m "feat(board-tui): simulate + replay input sources"
```

---

### Task 3: Serial source + port autodetect (hardware-free reconnect logic)

**Files:**
- Modify: `tools/board-tui/sources.py`
- Modify: `tools/board-tui/tests/test_sources.py`

- [ ] **Step 1: Write the failing test (inject a fake opener; no pyserial needed)**

Append to `tools/board-tui/tests/test_sources.py`:
```python
from sources import SerialSource, autodetect_port


class _FakePort:
    """Fake pyserial-like port: returns queued lines, then raises once to
    simulate a disconnect, then returns more lines after reopen."""
    def __init__(self, script):
        self._script = list(script)
    def readline(self):
        if not self._script:
            raise StopIteration
        item = self._script.pop(0)
        if item is OSError:
            raise OSError("device disconnected")
        return item if isinstance(item, bytes) else item.encode()
    def close(self):
        pass


def test_serial_source_reconnects_across_error():
    opens = []
    batches = [
        ['{"pins":1}\n', OSError],          # first open: one line then disconnect
        ['{"pins":2}\n'],                    # after reconnect: one more line
    ]
    def fake_open():
        opens.append(1)
        return _FakePort(batches[len(opens) - 1])
    src = SerialSource(port="/dev/fake", open_fn=fake_open, reconnect_delay_s=0)
    out = []
    for line in src:
        out.append(line.strip())
        if len(out) == 2:
            break
    assert out == ['{"pins":1}', '{"pins":2}']
    assert len(opens) == 2   # reopened after the error


def test_autodetect_returns_none_when_no_ports(monkeypatch):
    monkeypatch.setattr("glob.glob", lambda pat: [])
    assert autodetect_port() is None


def test_autodetect_prefers_usbmodem(monkeypatch):
    monkeypatch.setattr("glob.glob",
                        lambda pat: ["/dev/tty.usbmodem1101"] if "usbmodem" in pat else [])
    assert autodetect_port() == "/dev/tty.usbmodem1101"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd tools/board-tui && python3 -m pytest tests/test_sources.py -k serial_or_autodetect -q` (or run the file)
Expected: FAIL (`ImportError: cannot import name 'SerialSource'`)

- [ ] **Step 3: Implement SerialSource + autodetect in `sources.py`**

Add at top: `import glob`. Then append:
```python
def autodetect_port() -> str | None:
    """Best-effort: find the UNO R4 USB serial device. macOS then Linux."""
    for pattern in ("/dev/tty.usbmodem*", "/dev/cu.usbmodem*", "/dev/ttyACM*", "/dev/ttyUSB*"):
        hits = sorted(glob.glob(pattern))
        if hits:
            return hits[0]
    return None


class SerialSource:
    """Reads lines from a serial port; reconnects forever on error.

    Never raises on disconnect — it closes, waits reconnect_delay_s, reopens,
    and resumes yielding. The TUI shows staleness while no lines arrive.
    `open_fn` is injectable for tests; the default opens pyserial lazily.
    """

    def __init__(self, port: str, baud: int = 115200, open_fn=None,
                 reconnect_delay_s: float = 1.0):
        self.port = port
        self.baud = baud
        self.reconnect_delay_s = reconnect_delay_s
        self._open_fn = open_fn or self._default_open

    def _default_open(self):
        import serial  # pyserial, imported lazily so tests need no hardware/dep
        return serial.Serial(self.port, self.baud, timeout=1)

    def __iter__(self):
        while True:
            try:
                sp = self._open_fn()
            except Exception:
                if self.reconnect_delay_s:
                    time.sleep(self.reconnect_delay_s)
                continue
            try:
                while True:
                    try:
                        raw = sp.readline()
                    except StopIteration:
                        return
                    except Exception:
                        break  # disconnect -> reopen
                    if not raw:
                        continue  # read timeout; loop (lets caller see staleness)
                    yield raw.decode(errors="replace")
            finally:
                try:
                    sp.close()
                except Exception:
                    pass
            if self.reconnect_delay_s:
                time.sleep(self.reconnect_delay_s)
```

Note: the test breaks out of the loop after 2 lines, so the infinite reconnect loop terminates in practice.

- [ ] **Step 4: Run to verify it passes**

Run: `cd tools/board-tui && python3 -m pytest tests/test_sources.py -q`
Expected: PASS (6 passed total)

- [ ] **Step 5: Commit**

```bash
cd ~/Projects/gardencontroller
git add tools/board-tui/sources.py tools/board-tui/tests/test_sources.py
git commit -m "feat(board-tui): serial source with reconnect + port autodetect"
```

---

### Task 4: Pure render functions

**Files:**
- Create: `tools/board-tui/render.py`, `tools/board-tui/tests/test_render.py`

- [ ] **Step 1: Write the failing test**

`tools/board-tui/tests/test_render.py`:
```python
import json
from rich.console import Console
from frames import parse_frame
from render import render_dashboard, DashboardState

FRAME = parse_frame(json.dumps({
    "t": 12345, "fw": "1.0.1", "dev": "garden-node-1",
    "pins": {"A0": 16021, "A1": 11002, "A2": 8190, "A3": 8175, "A4": 8201, "A5": 8188},
    "sensors": {"tempC": 22.4, "hum": 48.2, "dhtOk": True,
                "soil": [{"probe": "bed1", "pin": "A0", "raw": 16021, "pct": 12.5},
                         {"probe": "bed2", "pin": "A1", "raw": 11002, "pct": 78.0}]},
    "net": {"wifi": "up", "rssi": -58, "ip": "192.168.1.42", "push": "ok"},
    "board": {"up_s": 1234, "heap_b": 23456},
}))


def _render_to_text(renderable):
    con = Console(width=100, record=True)
    con.print(renderable)
    return con.export_text()


def test_renders_frame_without_error():
    st = DashboardState(source_label="SIM")
    st.update(FRAME, now=100.0)
    text = _render_to_text(render_dashboard(st, now=100.5))
    assert "garden-node-1" in text
    assert "A0" in text and "A5" in text
    assert "bed1" in text and "bed2" in text
    assert "192.168.1.42" in text


def test_renders_empty_state_without_error():
    st = DashboardState(source_label="SIM")
    text = _render_to_text(render_dashboard(st, now=0.0))
    assert "waiting" in text.lower()


def test_marks_stale_when_no_recent_frame():
    st = DashboardState(source_label="serial")
    st.update(FRAME, now=100.0)
    text = _render_to_text(render_dashboard(st, now=110.0))  # 10s later
    assert "stale" in text.lower()
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd tools/board-tui && python3 -m pytest tests/test_render.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'render'`)

- [ ] **Step 3: Implement `render.py`**

```python
"""Pure rendering: DashboardState + frame -> Rich renderables."""
from __future__ import annotations

from dataclasses import dataclass, field

from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

SOIL_PINS = {"A0", "A1"}
STALE_AFTER_S = 5.0


@dataclass
class DashboardState:
    source_label: str = "?"
    frame: dict | None = None
    last_rx: float | None = None
    raw_tail: list = field(default_factory=list)

    def update(self, frame: dict, now: float):
        self.frame = frame
        self.last_rx = now

    def push_raw(self, line: str, limit: int = 8):
        self.raw_tail.append(line)
        if len(self.raw_tail) > limit:
            self.raw_tail = self.raw_tail[-limit:]


def _bar(value: float, lo: float, hi: float, width: int = 20) -> str:
    if hi == lo:
        return " " * width
    frac = max(0.0, min(1.0, (value - lo) / (hi - lo)))
    filled = int(round(frac * width))
    return "█" * filled + "░" * (width - filled)


def _pins_panel(frame: dict) -> Panel:
    t = Table(expand=True)
    t.add_column("pin"); t.add_column("raw", justify="right"); t.add_column("")
    for pin in ("A0", "A1", "A2", "A3", "A4", "A5"):
        raw = frame["pins"].get(pin)
        bar = _bar(float(raw), 0, 16383, 24) if raw is not None else ""
        label = Text(pin)
        if pin not in SOIL_PINS:
            label.stylize("dim"); bar = f"[dim]{bar}[/dim] (floating)"
        t.add_row(label, str(raw), bar)
    return Panel(t, title="Pins (A0–A5, 14-bit)")


def _sensors_panel(frame: dict) -> Panel:
    s = frame["sensors"]
    t = Table(expand=True)
    t.add_column("sensor"); t.add_column("value", justify="right"); t.add_column("")
    for probe in s.get("soil", []):
        t.add_row(f"soil {probe['probe']} ({probe['pin']})",
                  f"{probe['pct']:.1f}%", _bar(probe["pct"], 0, 100, 24))
    dht = "ok" if s.get("dhtOk") else "[red]FAIL[/red]"
    t.add_row("temp", f"{s.get('tempC', float('nan')):.1f} °C", "")
    t.add_row("humidity", f"{s.get('hum', float('nan')):.1f} %", "")
    t.add_row("DHT22", dht, "")
    return Panel(t, title="Sensors")


def _net_panel(frame: dict) -> Panel:
    n = frame.get("net", {}); b = frame.get("board", {})
    t = Table.grid(padding=(0, 2))
    t.add_row("wifi", str(n.get("wifi"))); t.add_row("rssi", f"{n.get('rssi')} dBm")
    t.add_row("ip", str(n.get("ip"))); t.add_row("push", str(n.get("push")))
    t.add_row("uptime", f"{b.get('up_s')} s"); t.add_row("free RAM", f"{b.get('heap_b')} B")
    return Panel(t, title="Net / Board")


def render_dashboard(state: DashboardState, now: float):
    if state.frame is None:
        return Panel(Text("waiting for first frame…", justify="center"),
                     title=f"garden board · source={state.source_label}")
    f = state.frame
    age = (now - state.last_rx) if state.last_rx is not None else 1e9
    stale = age > STALE_AFTER_S
    head = Text.assemble(
        (f"{f.get('dev','?')} ", "bold"),
        (f"fw {f.get('fw','?')}  ", ""),
        (f"source={state.source_label}  ", ""),
        ((f"STALE {age:.0f}s", "bold red") if stale else (f"live ({age:.1f}s)", "green")),
    )
    body = Table.grid(expand=True)
    body.add_column(ratio=1); body.add_column(ratio=1)
    body.add_row(_pins_panel(f), _sensors_panel(f))
    body.add_row(_net_panel(f), Panel(Text("\n".join(state.raw_tail) or "—"), title="raw"))
    return Panel(Group(head, body), title="garden board")
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd tools/board-tui && python3 -m pytest tests/test_render.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
cd ~/Projects/gardencontroller
git add tools/board-tui/render.py tools/board-tui/tests/test_render.py
git commit -m "feat(board-tui): pure Rich render functions"
```

---

### Task 5: CLI + Live loop + README

**Files:**
- Create: `tools/board-tui/board_tui.py`, `tools/board-tui/README.md`
- Modify: `tools/board-tui/tests/test_render.py` (add a `--once` smoke test via subprocess)

- [ ] **Step 1: Write the failing `--once` smoke test**

Append to `tools/board-tui/tests/test_render.py`:
```python
import subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_once_replay_exits_zero_and_prints():
    fix = ROOT / "tests" / "fixtures" / "sample.ndjson"
    r = subprocess.run([sys.executable, str(ROOT / "board_tui.py"),
                        "--replay", str(fix), "--once"],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert "garden-node-1" in r.stdout
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd tools/board-tui && python3 -m pytest tests/test_render.py::test_once_replay_exits_zero_and_prints -q`
Expected: FAIL (board_tui.py does not exist)

- [ ] **Step 3: Implement `board_tui.py`**

```python
#!/usr/bin/env python3
"""board-tui — live terminal dashboard for the garden board's serial telemetry.

Usage:
  python board_tui.py                      # auto-detect serial port
  python board_tui.py --port /dev/tty...   # explicit port
  python board_tui.py --simulate           # no hardware
  python board_tui.py --replay cap.ndjson  # replay a capture
  python board_tui.py --record cap.ndjson  # tee live lines to a file
  python board_tui.py --replay f --once    # render one snapshot and exit (CI)
"""
from __future__ import annotations

import argparse
import queue
import sys
import threading
import time

from frames import parse_frame
from render import DashboardState, render_dashboard
from sources import SimulateSource, ReplaySource, SerialSource, autodetect_port


def _build_source(args):
    if args.simulate:
        return SimulateSource(interval_s=0.0 if args.once else 1.0), "SIM"
    if args.replay:
        return ReplaySource(args.replay, realtime=not args.once), f"replay:{args.replay}"
    port = args.port or autodetect_port()
    if not port:
        print("No serial port found. Plug in the board or use --simulate / --replay.",
              file=sys.stderr)
        sys.exit(2)
    return SerialSource(port, baud=args.baud), f"serial:{port}"


def _reader(source, q: "queue.Queue", record_fp):
    for line in source:
        if record_fp:
            record_fp.write(line if line.endswith("\n") else line + "\n")
            record_fp.flush()
        q.put(line)


def run_once(source, label) -> int:
    from rich.console import Console
    st = DashboardState(source_label=label)
    for line in source:
        fr = parse_frame(line)
        if fr:
            st.update(fr, now=time.monotonic())
            st.push_raw(line.strip())
            break
    Console().print(render_dashboard(st, now=time.monotonic()))
    return 0


def run_live(source, label, record_fp) -> int:
    from rich.live import Live
    st = DashboardState(source_label=label)
    q: "queue.Queue" = queue.Queue()
    t = threading.Thread(target=_reader, args=(source, q, record_fp), daemon=True)
    t.start()
    with Live(render_dashboard(st, now=time.monotonic()), refresh_per_second=8,
              screen=True) as live:
        while True:
            try:
                while True:
                    line = q.get_nowait()
                    fr = parse_frame(line)
                    if fr:
                        st.update(fr, now=time.monotonic())
                    st.push_raw(line.strip())
            except queue.Empty:
                pass
            live.update(render_dashboard(st, now=time.monotonic()))
            time.sleep(0.12)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="board-tui")
    p.add_argument("--port"); p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--simulate", action="store_true")
    p.add_argument("--replay")
    p.add_argument("--record")
    p.add_argument("--once", action="store_true")
    args = p.parse_args(argv)

    source, label = _build_source(args)
    record_fp = open(args.record, "w") if args.record else None
    try:
        if args.once:
            return run_once(source, label)
        return run_live(source, label, record_fp)
    except KeyboardInterrupt:
        return 0
    finally:
        if record_fp:
            record_fp.close()


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run to verify it passes + full suite**

Run: `cd tools/board-tui && python3 -m pip install -r requirements.txt && python3 -m pytest -q`
Expected: PASS (all tests). Then manual: `python3 board_tui.py --simulate` shows a live dashboard (Ctrl-C to exit).

- [ ] **Step 5: Write README**

`tools/board-tui/README.md`:
```markdown
# board-tui — live garden board monitor

Live terminal dashboard of the garden board's USB serial telemetry: all analog
pins (A0–A5), translated soil %, temp/humidity, and board/network metrics.

## Install
    cd tools/board-tui && pip install -r requirements.txt

## Run
    python board_tui.py                 # auto-detect the board's serial port
    python board_tui.py --port /dev/tty.usbmodem1101
    python board_tui.py --simulate      # no hardware (demo/test)
    python board_tui.py --replay cap.ndjson
    python board_tui.py --record cap.ndjson   # save a live session

Close the Arduino IDE Serial Monitor first — the USB serial port is single-owner.
Requires board firmware >= 1.0.1 (emits the NDJSON telemetry stream).
```

- [ ] **Step 6: Commit**

```bash
cd ~/Projects/gardencontroller
git add tools/board-tui/board_tui.py tools/board-tui/README.md tools/board-tui/tests/test_render.py
git commit -m "feat(board-tui): CLI + Live loop + README"
```

---

### Task 6: Firmware — config constants + DHT accessor + telemetry module

**Files:**
- Modify: `firmware/garden-node/config.h`
- Modify: `firmware/garden-node/sensors.h`, `firmware/garden-node/sensors.cpp`
- Create: `firmware/garden-node/telemetry.h`, `firmware/garden-node/telemetry.cpp`

(Firmware isn't unit-tested in this repo; it's verified by `arduino-cli compile` in Task 8. Make small, reviewable changes.)

- [ ] **Step 1: Add config constants**

In `firmware/garden-node/config.h`, after the `SLIDE_MS`/temp block, add:
```cpp
// LED + live telemetry cadence
#define TELEMETRY_MS         1000UL   // NDJSON serial frame interval (live view)
#define TELEMETRY_DHT_MIN_MS 2500UL   // DHT22 can't sustain 1Hz; re-read at most this often
```
And in the OTA section, after `OTA_EEPROM_OFFSET`, add:
```cpp
#define OTA_CHECK_INTERVAL_MS 300000UL  // periodic OTA poll (5 min) while running
```

- [ ] **Step 2: Bump firmware version**

In `config.h` change:
```cpp
#define FIRMWARE_VERSION   "1.0.1"
```
(The release script also bumps this, but setting it now keeps the emitted `fw`
field correct when compiling/flashing locally before the tag.)

- [ ] **Step 3: Add a DHT accessor to sensors**

In `sensors.h`, after `Reading sensorsRead();`, add:
```cpp
// Lightweight DHT read for the live telemetry stream (no soil/rain work).
// Returns true on a successful read; writes tempC/hum on success.
bool sensorsReadDht(float& tempC, float& hum);
```

In `sensors.cpp`, find the existing DHT object (e.g. `static DHT dht(DHT_PIN, DHT_TYPE);`) and add this function near `sensorsRead()`:
```cpp
bool sensorsReadDht(float& tempC, float& hum) {
  float t = dht.readTemperature();
  float h = dht.readHumidity();
  if (isnan(t) || isnan(h)) return false;
  tempC = t; hum = h;
  return true;
}
```
(If the DHT instance has a different name in `sensors.cpp`, use that name.)

- [ ] **Step 4: Create `telemetry.h`**

```cpp
// telemetry.h — emit a structured NDJSON telemetry frame over Serial (~1 Hz).
#pragma once
#include <Arduino.h>

void telemetryBegin();                      // call in setup() (sets ADC resolution)
void telemetrySetPush(const char* status);  // "ok"/"fail"/"n/a" from the publish path
void telemetryTick();                       // call every loop(); non-blocking
```

- [ ] **Step 5: Create `telemetry.cpp`**

```cpp
// telemetry.cpp — ~1 Hz NDJSON frame: all analog pins, translated sensors,
// board/net metrics. Decoupled from the 60s cloud sample.
#include "telemetry.h"
#include "config.h"
#include "sensors.h"
#include <ArduinoJson.h>
#if ENABLE_UPLOAD
#include <WiFiS3.h>
#include "net.h"
#endif

static unsigned long s_lastFrameMs = 0;
static unsigned long s_lastDhtMs = 0;
static float s_tempC = NAN, s_hum = NAN;
static bool  s_dhtOk = false;
static const char* s_push = "n/a";

// Approx free RAM on the RA4M1 (newlib): gap between heap end and stack top.
extern "C" char* sbrk(int incr);
static int freeRamBytes() {
  char top;
  return (int)(&top - reinterpret_cast<char*>(sbrk(0)));
}

void telemetryBegin() {
  analogReadResolution(14);   // 0..16383, matches soil calibration
  s_lastFrameMs = millis() - TELEMETRY_MS;
}

void telemetrySetPush(const char* status) { s_push = status ? status : "n/a"; }

void telemetryTick() {
  unsigned long now = millis();
  if (now - s_lastFrameMs < TELEMETRY_MS) return;
  s_lastFrameMs = now;

  // DHT throttled refresh (cached between frames)
  if (now - s_lastDhtMs >= TELEMETRY_DHT_MIN_MS) {
    s_lastDhtMs = now;
    float t, h;
    if (sensorsReadDht(t, h)) { s_tempC = t; s_hum = h; s_dhtOk = true; }
    else { s_dhtOk = false; }
  }

  JsonDocument doc;
  doc["t"] = now;
  doc["fw"] = FIRMWARE_VERSION;
  doc["dev"] = DEVICE_ID;

  const uint8_t pinNums[6] = {A0, A1, A2, A3, A4, A5};
  const char* pinNames[6] = {"A0", "A1", "A2", "A3", "A4", "A5"};
  int rawByPin[6];
  JsonObject pins = doc["pins"].to<JsonObject>();
  for (int i = 0; i < 6; i++) {
    rawByPin[i] = analogRead(pinNums[i]);
    pins[pinNames[i]] = rawByPin[i];
  }

  JsonObject sensors = doc["sensors"].to<JsonObject>();
  sensors["tempC"] = s_tempC;
  sensors["hum"] = s_hum;
  sensors["dhtOk"] = s_dhtOk;
  JsonArray soil = sensors["soil"].to<JsonArray>();
  for (size_t i = 0; i < SOIL_PROBE_COUNT; i++) {
    int raw = analogRead(SOIL_PROBES[i].pin);
    JsonObject o = soil.add<JsonObject>();
    o["probe"] = SOIL_PROBES[i].probe;
    // map A0..A5 enum to a label for display
    o["pin"] = (SOIL_PROBES[i].pin == A1) ? "A1" : "A0";
    o["raw"] = raw;
    o["pct"] = soilMoisturePercent(raw);
  }

  JsonObject net = doc["net"].to<JsonObject>();
#if ENABLE_UPLOAD
  bool up = (WiFi.status() == WL_CONNECTED);
  net["wifi"] = up ? "up" : "down";
  net["rssi"] = up ? (int)WiFi.RSSI() : 0;
  if (up) { IPAddress ip = WiFi.localIP(); char b[16];
            snprintf(b, sizeof(b), "%u.%u.%u.%u", ip[0], ip[1], ip[2], ip[3]); net["ip"] = b; }
  else { net["ip"] = "0.0.0.0"; }
  net["push"] = s_push;
#else
  net["wifi"] = "off";
  net["rssi"] = 0;
  net["ip"] = "0.0.0.0";
  net["push"] = "n/a";
#endif

  JsonObject board = doc["board"].to<JsonObject>();
  board["up_s"] = now / 1000UL;
  board["heap_b"] = freeRamBytes();

  serializeJson(doc, Serial);
  Serial.println();
}
```

- [ ] **Step 6: Commit**

```bash
cd ~/Projects/gardencontroller
git add firmware/garden-node/config.h firmware/garden-node/sensors.h firmware/garden-node/sensors.cpp firmware/garden-node/telemetry.h firmware/garden-node/telemetry.cpp
git commit -m "feat(firmware): NDJSON serial telemetry module + config + DHT accessor"
```

---

### Task 7: Wire telemetry + periodic OTA into the sketch

**Files:**
- Modify: `firmware/garden-node/garden-node.ino`

- [ ] **Step 1: Include telemetry + add the OTA poll timer**

At the top includes, after `#include "display.h"`, add:
```cpp
#include "telemetry.h"
```
After `static unsigned long lastSampleMs = 0;` add:
```cpp
#if ENABLE_UPLOAD
static unsigned long lastOtaCheckMs = 0;
#endif
```

- [ ] **Step 2: Call `telemetryBegin()` in setup()**

In `setup()`, after `sensorsBegin();`, add:
```cpp
  telemetryBegin();
```
And right after the existing boot-time OTA block (inside `#if ENABLE_UPLOAD`,
after `otaCheckAndApply();`), initialize the poll timer:
```cpp
    lastOtaCheckMs = millis();
```

- [ ] **Step 3: Tick telemetry + poll OTA in loop()**

In `loop()`, just after `displayTick();`, add:
```cpp
  telemetryTick();   // ~1 Hz NDJSON frame over Serial (non-blocking)

#if ENABLE_UPLOAD
  if (millis() - lastOtaCheckMs >= OTA_CHECK_INTERVAL_MS) {
    lastOtaCheckMs = millis();
    if (netEnsureWifi()) {
      otaCheckAndApply();   // applies + reboots if a newer version is published
    }
  }
#endif
```

- [ ] **Step 4: Feed push status to telemetry**

In `loop()`, replace the existing publish block:
```cpp
  bool ok = netPublish(r);
  if (!ok) Serial.println("publish failed; will retry next cycle");
```
with:
```cpp
  bool ok = netPublish(r);
  telemetrySetPush(ok ? "ok" : "fail");
  if (!ok) Serial.println("publish failed; will retry next cycle");
```

- [ ] **Step 5: Commit**

```bash
cd ~/Projects/gardencontroller
git add firmware/garden-node/garden-node.ino
git commit -m "feat(firmware): wire telemetry tick + 5-min periodic OTA poll"
```

---

### Task 8: Compile-verify the firmware

**Files:** none (verification only)

- [ ] **Step 1: Compile with arduino-cli for the UNO R4 WiFi**

Run:
```bash
cd ~/Projects/gardencontroller/firmware/garden-node
arduino-cli compile --fqbn arduino:renesas_uno:unor4wifi . 2>&1 | tail -20
```
Expected: `Sketch uses ... bytes` with no errors. If `arduino:renesas_uno` core
is missing: `arduino-cli core install arduino:renesas_uno`. If ArduinoJson/DHT
libs are missing: `arduino-cli lib install ArduinoJson "DHT sensor library"`.

- [ ] **Step 2: Sanity-check the emitted schema offline (optional, fast)**

The telemetry JSON shape mirrors `tools/board-tui/tests/fixtures/sample.ndjson`.
Confirm the TUI parses that exact shape (already covered by Task 2's replay test):
```bash
cd ~/Projects/gardencontroller/tools/board-tui && python3 -m pytest -q
```
Expected: PASS.

- [ ] **Step 3: Commit (no-op if nothing changed)**

Nothing to commit unless the compile surfaced a fix. If a fix was needed, commit it:
```bash
cd ~/Projects/gardencontroller
git commit -am "fix(firmware): resolve compile issues for telemetry build"
```

---

### Task 9: Cut the OTA release (run on `main`, after merge)

**Files:** `firmware/garden-node/config.h` (version bump via script)

> Run this only after the branch is merged to `main` (so the tag points at the
> merged commit and CI builds the release from it). The release script requires a
> clean tree and pushes `main` + the tag.

- [ ] **Step 1: Cut the release**

Run:
```bash
cd ~/Projects/gardencontroller
git checkout main && git pull
./scripts/release-firmware.sh 1.0.1
```
Expected: bumps `FIRMWARE_VERSION` (already 1.0.1 — script is idempotent on the
value), commits, tags `v1.0.1`, pushes. (If the version is already 1.0.1 and the
tag doesn't exist yet, the commit may be empty — create the tag manually:
`git tag v1.0.1 && git push origin v1.0.1`.)

- [ ] **Step 2: Verify CI built the GitHub Release**

Run:
```bash
gh run list --workflow=firmware-release.yml --limit 3
gh release view v1.0.1 --json assets -q '.assets[].name'
```
Expected: the run is green and the release has `version.txt` + `garden-node.ota`.

- [ ] **Step 3: Verify the board self-updates and stream the live view**

- Ensure the board is powered, on WiFi, running `ENABLE_UPLOAD=1` firmware.
- Within ~5 minutes the periodic OTA poll pulls v1.0.1 and reboots.
- Close the Arduino Serial Monitor, then:
```bash
cd ~/Projects/gardencontroller/tools/board-tui
python3 board_tui.py            # auto-detect, or --port /dev/tty.usbmodemXXXX
```
Expected: a live dashboard updating ~1 Hz with A0–A5, bed1/bed2 %, temp/hum,
wifi/rssi/ip, heap, uptime, push status. (Fallback if OTA hasn't landed:
`./scripts/flash.sh` to USB-flash v1.0.1 directly.)

---

## Self-Review

**Spec coverage:**
- ~1Hz NDJSON frame (pins A0–A5, translated soil, temp/hum, net, board) → Task 6 (`telemetry.cpp`). ✓
- Decoupled from 60s push; emitted always → Task 6/7 (separate `TELEMETRY_MS` timer). ✓
- DHT throttled to ≥2.5s → Task 6 (`s_lastDhtMs`). ✓
- Periodic 5-min OTA poll + boot check retained + failure guard → Task 6 (const), Task 7 (loop). ✓
- TUI sources: serial (autodetect+reconnect) / simulate / replay / record → Tasks 2,3,5. ✓
- `parse_frame` tolerant of banner/log/malformed → Task 1. ✓
- Rich Live dashboard panels (pins/sensors/net/raw) + staleness + empty state → Task 4. ✓
- `--once` for CI; hardware-free tests → Tasks 4,5. ✓
- Delivery via OTA release v1.0.1; USB-flash fallback → Task 9. ✓
- Single-owner serial note → Task 5 README, Task 9. ✓

**Placeholder scan:** Fixture `sample.ndjson` is a real artifact (Task 2). No TBD/TODO left. The DHT-object-name caveat in Task 6 Step 3 is a concrete instruction (use the actual name found), not a placeholder.

**Type/consistency:** Frame keys identical across firmware emit (Task 6), fixture (Task 2), parse test (Task 1), render (Task 4): `t,fw,dev,pins.{A0..A5},sensors.{tempC,hum,dhtOk,soil[].{probe,pin,raw,pct}},net.{wifi,rssi,ip,push},board.{up_s,heap_b}`. `DashboardState.update(frame, now)` / `render_dashboard(state, now)` signatures consistent across Tasks 4–5. `SerialSource(port, baud, open_fn, reconnect_delay_s)` consistent Task 3↔5.
