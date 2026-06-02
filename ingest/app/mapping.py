"""Translate a board's JSON reading into Prometheus samples for Pushgateway.

This module is the seam between "whatever the firmware sends" and "the metric
schema Grafana queries". Keeping it here (not on the board) means we can fix
labels, clamp ranges, and reject garbage without reflashing hardware.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass

# A Prometheus label value we control; keep cardinality sane.
_LABEL_RE = re.compile(r"[^a-zA-Z0-9_./:-]")
_MAX_LABEL_LEN = 64


class ValidationError(ValueError):
    """Raised when a payload can't be safely mapped to metrics."""


@dataclass(frozen=True)
class Sample:
    metric: str
    labels: dict[str, str]
    value: float


def _clean_label(value: str) -> str:
    """Normalize a free-text label value (location, probe, device_id)."""
    value = _LABEL_RE.sub("_", str(value)).strip("_")
    if not value:
        raise ValidationError("empty label value after cleaning")
    return value[:_MAX_LABEL_LEN]


def _num(d: dict, key: str, lo: float, hi: float) -> float | None:
    """Pull a number and clamp to a sane physical range; None if absent/NaN."""
    if key not in d or d[key] is None:
        return None
    try:
        v = float(d[key])
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{key!r} is not a number") from exc
    if v != v:  # NaN
        return None
    return max(lo, min(hi, v))


# ─────────────────────────────────────────────────────────────────────────────
# CONTRIBUTION POINT #4 — the label schema and validation policy.
#
# Decide:
#   * which readings are required vs. optional,
#   * the allowed/normalized label values (location, probe naming),
#   * range clamps (above) and what to do with out-of-range / missing data,
#   * label cardinality (every distinct label combo is a new Prometheus series).
#
# The implementation below is a working default. Tune it to your garden.
# ─────────────────────────────────────────────────────────────────────────────
def to_samples(payload: dict) -> list[Sample]:
    if not isinstance(payload, dict):
        raise ValidationError("payload must be a JSON object")

    device_id = _clean_label(payload.get("device_id", ""))
    location = _clean_label(payload.get("location", "unknown"))
    base = {"device_id": device_id, "location": location}
    readings = payload.get("readings") or {}
    if not isinstance(readings, dict):
        raise ValidationError("'readings' must be an object")

    samples: list[Sample] = []

    temp = _num(readings, "air_temperature_celsius", -40, 85)
    if temp is not None:
        samples.append(Sample("garden_air_temperature_celsius", base, temp))

    hum = _num(readings, "air_humidity_percent", 0, 100)
    if hum is not None:
        samples.append(Sample("garden_air_humidity_percent", base, hum))

    for probe in readings.get("soil", []) or []:
        labels = {**base, "probe": _clean_label(probe.get("probe", "p0"))}
        raw = _num(probe, "raw", 0, 16383)
        pct = _num(probe, "percent", 0, 100)
        if raw is not None:
            samples.append(Sample("garden_soil_moisture_raw", labels, raw))
        if pct is not None:
            samples.append(Sample("garden_soil_moisture_percent", labels, pct))

    rain_pct = _num(readings, "rain_percent", 0, 100)
    if rain_pct is not None:
        samples.append(Sample("garden_rain_intensity_percent", base, rain_pct))
    if "rain_detected" in readings and readings["rain_detected"] is not None:
        samples.append(Sample("garden_rain_detected", base, 1.0 if readings["rain_detected"] else 0.0))

    board = payload.get("board") or {}
    rssi = _num(board, "rssi_dbm", -120, 0)
    if rssi is not None:
        samples.append(Sample("garden_board_rssi_dbm", {"device_id": device_id}, rssi))
    uptime = _num(board, "uptime_seconds", 0, 10**12)
    if uptime is not None:
        samples.append(Sample("garden_board_uptime_seconds", {"device_id": device_id}, uptime))

    # Reject a payload that carried no actual sensor/board data — a bare
    # heartbeat is not a valid reading. (Board health counts as data.)
    if not samples:
        raise ValidationError("no usable readings in payload")

    # Emit a heartbeat so Grafana/alerts can detect a dead board.
    samples.append(Sample("garden_push_timestamp_seconds", {"device_id": device_id}, time.time()))
    return samples


def render_exposition(samples: list[Sample]) -> str:
    """Render samples in Prometheus text exposition format for Pushgateway."""
    lines: list[str] = []
    for s in samples:
        if s.labels:
            label_str = ",".join(f'{k}="{v}"' for k, v in sorted(s.labels.items()))
            lines.append(f"{s.metric}{{{label_str}}} {s.value}")
        else:
            lines.append(f"{s.metric} {s.value}")
    return "\n".join(lines) + "\n"
