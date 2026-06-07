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
