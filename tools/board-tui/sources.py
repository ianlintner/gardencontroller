"""Input sources for the board TUI: simulate, replay, serial, tcp.

Each source is iterable and yields raw text lines (str). parse_frame() filters
non-telemetry lines downstream, so sources may emit banner/log lines too.
"""
from __future__ import annotations

import glob
import json
import math
import socket
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
