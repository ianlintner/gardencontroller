#!/usr/bin/env python3
"""garden — weather-aware irrigation CLI for the OpenClaw agent (read-only).

Self-contained, stdlib-only. Subcommands: sensors, weather, plan.
This CLI holds NO Tuya credential and has NO valve code path — all valve
actions go through the garden-tuya-mcp sidecar (see irrigation/mcp/). It only
reads Prometheus + Open-Meteo and emits a watering *proposal*; the MCP enforces
caps and opens valves.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from garden_core import clamp_minutes

__version__ = "0.1.0"


def plan_zone(*, zone, cfg, soil_pct, temp_c, stale, precip_12h_mm,
              precip_prob_pct, et0_mm, run_phase, watered_today) -> dict:
    """Deterministic per-zone watering decision. Returns {zone, minutes, reason}.

    Gardener-tunable constants live in `zone` and `cfg` (config file).
    Order matters: fail-safe skips first, then compute, then cap.
    """
    name = zone["name"]

    def out(minutes, reason):
        return {"zone": name, "minutes": int(minutes), "reason": reason}

    # Fail-safe: never water on stale/missing sensor data.
    if stale or soil_pct is None:
        return out(0, "skip: soil reading stale/missing")

    # 1. Rain skip — let nature do it.
    if precip_12h_mm >= cfg["rain_skip_mm"] or precip_prob_pct >= cfg["rain_skip_prob_pct"]:
        return out(0, f"skip: rain forecast ({precip_12h_mm:.1f}mm / {precip_prob_pct:.0f}%)")

    # 2. Soil gate — already moist enough.
    if soil_pct >= zone["target_pct"]:
        return out(0, f"skip: soil {soil_pct:.0f}% >= target {zone['target_pct']:.0f}%")

    # 3. Deficit -> base minutes.
    deficit = zone["target_pct"] - soil_pct
    base = deficit * zone["min_per_pct"]

    # 4. ET0 scaling (hot/dry day -> more, mild -> less).
    et_factor = (et0_mm / cfg["et0_baseline_mm"]) if (et0_mm and cfg["et0_baseline_mm"]) else 1.0
    minutes = base * et_factor

    # 5. Midday = heat-wave-only short burst.
    if run_phase == "midday":
        if temp_c is None or temp_c < cfg["heat_threshold_c"]:
            return out(0, f"skip: midday, not a heat wave (temp {temp_c}°C)")
        minutes = min(minutes, cfg["midday_cap_min"])
        reason = f"heat-wave burst: {temp_c:.0f}°C, soil {soil_pct:.0f}%"
    else:
        et0_str = f"{et0_mm:.1f}mm" if et0_mm is not None else "n/a"
        reason = (f"{run_phase}: soil {soil_pct:.0f}% vs target {zone['target_pct']:.0f}%, "
                  f"ET0 {et0_str}")

    # 6. Hard caps (final guard, incl. daily budget).
    capped = clamp_minutes(minutes, zone, watered_today)
    if capped == 0:
        return out(0, f"skip: computed {minutes:.1f}min below min or daily budget spent")
    return out(capped, reason)




def http_get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def _prom_scalar(get_json, prom_url, query):
    url = prom_url.rstrip("/") + "/api/v1/query?" + urllib.parse.urlencode({"query": query})
    res = get_json(url)["data"]["result"]
    return float(res[0]["value"][1]) if res else None


def read_sensors(zone, *, prom_url, now, get_json=http_get_json, max_age_s=900) -> dict:
    dev, probe = zone["prom_device_id"], zone["probe"]
    sel = f'device_id="{dev}"'
    soil = _prom_scalar(get_json, prom_url,
                        f'garden_soil_moisture_percent{{{sel},probe="{probe}"}}')
    temp = _prom_scalar(get_json, prom_url, f'garden_air_temperature_celsius{{{sel}}}')
    pushed = _prom_scalar(get_json, prom_url, f'garden_push_timestamp_seconds{{{sel}}}')
    age = (now - pushed) if pushed is not None else float("inf")
    return {"zone": zone["name"], "soil_pct": soil, "temp_c": temp,
            "stale": age > max_age_s, "age_s": age}


def read_weather(*, lat, lon, get_json=http_get_json) -> dict:
    params = {"latitude": lat, "longitude": lon,
              "hourly": "precipitation,precipitation_probability",
              "daily": "et0_fao_evapotranspiration,temperature_2m_max",
              "forecast_days": 1, "timezone": "UTC"}
    url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params)
    d = get_json(url)
    precip = d["hourly"]["precipitation"][:12]
    prob = d["hourly"]["precipitation_probability"][:12]
    et0_arr = d["daily"].get("et0_fao_evapotranspiration", [])
    et0_raw = et0_arr[0] if et0_arr else None
    et0 = float(et0_raw) if et0_raw is not None else None
    temp_arr = d["daily"].get("temperature_2m_max", [])
    temp_high = float(temp_arr[0]) if temp_arr else None
    return {"precip_12h_mm": float(sum(precip)),
            "precip_prob_pct": float(max(prob) if prob else 0),
            "et0_mm": et0,
            "temp_high_c": temp_high}


# ---------------------------------------------------------------------------
# CLI — argparse subcommands
# ---------------------------------------------------------------------------


def load_config(path):
    return json.loads(Path(path).read_text())


def _zone(cfg, name):
    for z in cfg["zones"]:
        if z["name"] == name:
            return z
    raise SystemExit(f"unknown zone: {name}")


def cmd_sensors(args, cfg):
    z = _zone(cfg, args.zone)
    print(json.dumps(read_sensors(z, prom_url=cfg["prometheus_url"], now=time.time())))
    return 0


def cmd_weather(args, cfg):
    print(json.dumps(read_weather(lat=cfg["lat"], lon=cfg["lon"])))
    return 0


def cmd_plan(args, cfg):
    now = time.time()
    weather = read_weather(lat=cfg["lat"], lon=cfg["lon"])
    out = []
    for z in cfg["zones"]:
        s = read_sensors(z, prom_url=cfg["prometheus_url"], now=now)
        out.append(plan_zone(zone=z, cfg=cfg, soil_pct=s["soil_pct"], temp_c=s["temp_c"],
                             stale=s["stale"], precip_12h_mm=weather["precip_12h_mm"],
                             precip_prob_pct=weather["precip_prob_pct"], et0_mm=weather["et0_mm"],
                             run_phase=args.phase, watered_today=0.0))
    print(json.dumps(out))
    return 0


def main(argv=None):
    # Shared options (--config) are attached ONLY to subparsers via a common
    # parent so they must appear after the subcommand name (e.g.
    # `garden plan --config foo.json`).  Adding them to the top-level parser
    # would let `garden --config X plan` silently use the wrong config because
    # the subparser default would win.
    _defaults = argparse.ArgumentParser(add_help=False)
    _defaults.add_argument("--config", default=os.environ.get("GARDEN_CONFIG", "garden.config.json"))

    p = argparse.ArgumentParser(prog="garden")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("sensors", parents=[_defaults])
    sp.add_argument("--zone", required=True)

    sub.add_parser("weather", parents=[_defaults])

    pp = sub.add_parser("plan", parents=[_defaults])
    pp.add_argument("--phase", default="morning", choices=["morning", "midday", "evening"])

    args = p.parse_args(argv)
    try:
        cfg = load_config(args.config)
        return {"sensors": cmd_sensors, "weather": cmd_weather,
                "plan": cmd_plan}[args.cmd](args, cfg)
    except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError, TypeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
