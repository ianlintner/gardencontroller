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
from __future__ import annotations

import argparse
import queue
import sys
import threading
import time

from frames import parse_frame
from render import DashboardState, render_dashboard
from sources import SimulateSource, ReplaySource, SerialSource, autodetect_port, TcpSource


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
    p.add_argument("--host", default=None,
                   help="Board IP[:port] for network telemetry (default port 8766)")
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
