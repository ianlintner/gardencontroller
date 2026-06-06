#!/usr/bin/env python3
"""garden — weather-aware irrigation CLI for the OpenClaw agent.

Self-contained, stdlib-only. Subcommands: sensors, weather, plan, water, status.
Safety-critical logic (formula, caps, valve commands) lives here, not in prompts.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from garden_core import (
    clamp_minutes, do_water, Tuya, TuyaError, tuya_string_to_sign, tuya_sign,
)

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


class State:
    """JSON state persisted on OpenClaw's PVC (default ~/.openclaw/garden)."""
    def __init__(self, base_dir):
        self.dir = Path(base_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "state.json"

    def _load(self) -> dict:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text())
            except Exception:
                return {}
        return {}

    def _save(self, d: dict) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, indent=2))
        os.replace(tmp, self.path)

    @staticmethod
    def _day(now: float) -> str:
        return time.strftime("%Y-%m-%d", time.gmtime(now))

    def watered_today(self, zone: str, now: float) -> float:
        d = self._load()
        rec = d.get("watered", {}).get(zone, {})
        return float(rec.get("minutes", 0)) if rec.get("day") == self._day(now) else 0.0

    def add_watered(self, zone: str, minutes: float, now: float) -> None:
        d = self._load()
        w = d.setdefault("watered", {})
        rec = w.get(zone, {})
        cur = float(rec.get("minutes", 0)) if rec.get("day") == self._day(now) else 0.0
        w[zone] = {"day": self._day(now), "minutes": cur + float(minutes)}
        self._save(d)

    def set_pending(self, plan, now: float, ttl_s: int = 7200) -> None:
        d = self._load()
        d["pending"] = {"plan": plan, "expires": now + ttl_s}
        self._save(d)

    def get_pending(self, now: float):
        d = self._load()
        p = d.get("pending")
        if not p or now > p.get("expires", 0):
            return None
        return p["plan"]

    def clear_pending(self) -> None:
        d = self._load(); d.pop("pending", None); self._save(d)

    def remove_pending_zone(self, zone: str) -> None:
        d = self._load()
        p = d.get("pending")
        if not p:
            return
        p["plan"] = [e for e in p.get("plan", []) if e.get("zone") != zone]
        if not p["plan"]:
            d.pop("pending", None)
        self._save(d)

    def seen_run(self, key: str) -> bool:
        return key in self._load().get("runs", [])

    def mark_run(self, key: str) -> None:
        d = self._load(); runs = d.setdefault("runs", [])
        if key not in runs:
            runs.append(key); self._save(d)


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


def _tuya_from_env():
    client_id = os.environ.get("TUYA_CLIENT_ID", "")
    client_secret = os.environ.get("TUYA_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise SystemExit("error: TUYA_CLIENT_ID and TUYA_CLIENT_SECRET must be set")
    return Tuya(client_id, client_secret, region=os.environ.get("TUYA_REGION", "us"))


def cmd_sensors(args, cfg):
    z = _zone(cfg, args.zone)
    print(json.dumps(read_sensors(z, prom_url=cfg["prometheus_url"], now=time.time())))
    return 0


def cmd_weather(args, cfg):
    print(json.dumps(read_weather(lat=cfg["lat"], lon=cfg["lon"])))
    return 0


def cmd_plan(args, cfg):
    now = time.time()
    st = State(args.state)
    weather = read_weather(lat=cfg["lat"], lon=cfg["lon"])
    out = []
    for z in cfg["zones"]:
        s = read_sensors(z, prom_url=cfg["prometheus_url"], now=now)
        out.append(plan_zone(zone=z, cfg=cfg, soil_pct=s["soil_pct"], temp_c=s["temp_c"],
                             stale=s["stale"], precip_12h_mm=weather["precip_12h_mm"],
                             precip_prob_pct=weather["precip_prob_pct"], et0_mm=weather["et0_mm"],
                             run_phase=args.phase, watered_today=st.watered_today(z["name"], now)))
    st.set_pending(out, now=now)
    print(json.dumps(out))
    return 0


def cmd_water(args, cfg):
    now = time.time()
    st = State(args.state)
    z = _zone(cfg, args.zone)
    if not args.dry_run and not args.force:
        pending = st.get_pending(now) or []
        match = next((e for e in pending if e.get("zone") == args.zone and e.get("minutes", 0) > 0), None)
        if match is None:
            raise SystemExit(f"error: no un-expired approved plan for zone {args.zone}; run 'garden plan' first")
        if args.minutes > match["minutes"]:
            raise SystemExit(f"error: requested {args.minutes}min exceeds approved {match['minutes']}min for zone {args.zone}")
    tuya = _tuya_from_env()
    if not args.dry_run:
        tuya.token()
    r = do_water(tuya, zone=z, minutes=args.minutes,
                 watered_today=st.watered_today(z["name"], now), dry_run=args.dry_run)
    if r.get("ok") and not args.dry_run and r["minutes"] > 0:
        st.add_watered(z["name"], r["minutes"], now=now)
        st.remove_pending_zone(z["name"])
    print(json.dumps(r))
    return 0 if r.get("ok") else 1


def cmd_status(args, cfg):
    z = _zone(cfg, args.zone)
    tuya = _tuya_from_env()
    tuya.token()
    print(json.dumps(tuya.status(z["tuya_device_id"])))
    return 0


def main(argv=None):
    # Shared options (--config/--state) are attached ONLY to subparsers via a
    # common parent so they must appear after the subcommand name (e.g.
    # `garden plan --config foo.json`).  Adding them to the top-level parser
    # would let `garden --config X plan` silently use the wrong config because
    # the subparser default would win.
    _defaults = argparse.ArgumentParser(add_help=False)
    _defaults.add_argument("--config", default=os.environ.get("GARDEN_CONFIG", "garden.config.json"))
    _defaults.add_argument("--state", default=os.environ.get("GARDEN_STATE",
                           os.path.expanduser("~/.openclaw/garden")))

    p = argparse.ArgumentParser(prog="garden")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("sensors", parents=[_defaults])
    sp.add_argument("--zone", required=True)

    sub.add_parser("weather", parents=[_defaults])

    pp = sub.add_parser("plan", parents=[_defaults])
    pp.add_argument("--phase", default="morning", choices=["morning", "midday", "evening"])

    wp = sub.add_parser("water", parents=[_defaults])
    wp.add_argument("--zone", required=True)
    wp.add_argument("--minutes", type=int, required=True)
    wp.add_argument("--dry-run", action="store_true")
    wp.add_argument("--force", action="store_true")

    stp = sub.add_parser("status", parents=[_defaults])
    stp.add_argument("--zone", required=True)

    args = p.parse_args(argv)
    try:
        cfg = load_config(args.config)
        return {"sensors": cmd_sensors, "weather": cmd_weather, "plan": cmd_plan,
                "water": cmd_water, "status": cmd_status}[args.cmd](args, cfg)
    except (OSError, urllib.error.URLError, TuyaError, json.JSONDecodeError, ValueError, TypeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
