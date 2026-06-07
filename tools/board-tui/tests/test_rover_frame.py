from pathlib import Path
from frames import parse_frame
from render import DashboardState, render_dashboard
from rich.console import Console

FIX = Path(__file__).parent / "fixtures" / "rover_sample.ndjson"


def test_rover_frame_parses():
    line = FIX.read_text().strip()
    f = parse_frame(line)
    assert f is not None
    assert f["dev"] == "rover-node-1"
    assert f["pins"] == {}                      # no analog pins on the rover
    assert f["sensors"]["camPresent"] is True
    assert f["board"]["psram_b"] > 0


def test_rover_frame_renders_in_tui():
    f = parse_frame(FIX.read_text().strip())
    st = DashboardState(source_label="tcp:rover")
    st.update(f, now=1.0)
    con = Console(width=100, record=True)
    con.print(render_dashboard(st, now=1.2))
    text = con.export_text()
    assert "rover-node-1" in text
