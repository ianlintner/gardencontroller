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
