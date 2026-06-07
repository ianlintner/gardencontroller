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
