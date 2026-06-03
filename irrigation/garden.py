#!/usr/bin/env python3
"""garden — weather-aware irrigation CLI for the OpenClaw agent.

Self-contained, stdlib-only. Subcommands: sensors, weather, plan, water, status.
Safety-critical logic (formula, caps, valve commands) lives here, not in prompts.
"""
from __future__ import annotations

__version__ = "0.1.0"


def clamp_minutes(minutes: float, zone: dict, watered_today: float) -> int:
    """Clamp a proposed duration to the zone's hard caps. Final safety guard.

    - never exceed max_per_run
    - never exceed the day's remaining budget (max_per_day - watered_today)
    - anything below min_run becomes 0 (don't bother / don't dribble)
    Returns whole minutes.
    """
    m = max(0.0, float(minutes))
    m = min(m, float(zone.get("max_per_run", 15)))
    if "max_per_day" in zone:
        remaining = max(0.0, float(zone["max_per_day"]) - float(watered_today))
        m = min(m, remaining)
    m = int(round(m))
    if m < int(zone.get("min_run", 1)):
        return 0
    return m
