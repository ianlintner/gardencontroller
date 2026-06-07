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
