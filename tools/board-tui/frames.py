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
