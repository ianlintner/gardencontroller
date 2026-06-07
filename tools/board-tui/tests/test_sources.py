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
