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
    et_factor = (et0_mm / cfg["et0_baseline_mm"]) if cfg["et0_baseline_mm"] else 1.0
    minutes = base * et_factor

    # 5. Midday = heat-wave-only short burst.
    if run_phase == "midday":
        if temp_c is None or temp_c < cfg["heat_threshold_c"]:
            return out(0, f"skip: midday, not a heat wave (temp {temp_c}°C)")
        minutes = min(minutes, cfg["midday_cap_min"])
        reason = f"heat-wave burst: {temp_c:.0f}°C, soil {soil_pct:.0f}%"
    else:
        reason = (f"{run_phase}: soil {soil_pct:.0f}% vs target {zone['target_pct']:.0f}%, "
                  f"ET0 {et0_mm:.1f}mm")

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

    def seen_run(self, key: str) -> bool:
        return key in self._load().get("runs", [])

    def mark_run(self, key: str) -> None:
        d = self._load(); runs = d.setdefault("runs", [])
        if key not in runs:
            runs.append(key); self._save(d)


def tuya_string_to_sign(method: str, body: str, path: str) -> str:
    body_hash = hashlib.sha256(body.encode()).hexdigest()
    return f"{method}\n{body_hash}\n\n{path}"


def tuya_sign(*, client_id, secret, access_token, t, nonce, string_to_sign) -> str:
    payload = client_id + access_token + t + nonce + string_to_sign
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest().upper()


class TuyaError(Exception):
    pass


class Tuya:
    """Minimal Tuya Cloud OpenAPI client (token + device commands/status)."""
    REGION_HOST = {"us": "openapi.tuyaus.com", "eu": "openapi.tuyaeu.com",
                   "cn": "openapi.tuyacn.com", "in": "openapi.tuyain.com"}

    def __init__(self, client_id, secret, region="us", now_ms=None, http=None):
        self.client_id = client_id
        self.secret = secret
        self.host = self.REGION_HOST.get(region, self.REGION_HOST["us"])
        self._now_ms = now_ms or (lambda: str(int(time.time() * 1000)))
        self._http = http or self._urlopen
        self._token = None

    def _urlopen(self, method, url, headers, body):
        req = urllib.request.Request(url, data=(body.encode() if body else None),
                                     headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())

    def _call(self, method, path, body="", with_token=True):
        t = self._now_ms()
        nonce = ""
        access_token = (self._token or "") if with_token else ""
        sts = tuya_string_to_sign(method, body, path)
        sign = tuya_sign(client_id=self.client_id, secret=self.secret,
                         access_token=access_token, t=t, nonce=nonce, string_to_sign=sts)
        headers = {"client_id": self.client_id, "sign": sign, "t": t,
                   "sign_method": "HMAC-SHA256", "nonce": nonce,
                   "Content-Type": "application/json"}
        if with_token:
            headers["access_token"] = self._token
        resp = self._http(method, f"https://{self.host}{path}", headers, body)
        if not resp.get("success", False):
            raise TuyaError(resp.get("msg", str(resp)))
        return resp["result"]

    def token(self):
        self._token = None
        res = self._call("GET", "/v1.0/token?grant_type=1", with_token=False)
        self._token = res["access_token"]
        return self._token

    def send_commands(self, device_id, commands: list):
        body = json.dumps({"commands": commands})
        return self._call("POST", f"/v1.0/iot-03/devices/{device_id}/commands", body=body)

    def status(self, device_id) -> dict:
        res = self._call("GET", f"/v1.0/iot-03/devices/{device_id}/status")
        return {item["code"]: item["value"] for item in res}


def do_water(tuya, *, zone, minutes, watered_today, dry_run,
             confirm_attempts: int = 3, confirm_sleep_s: float = 1.0) -> dict:
    """Open the zone's valve for `minutes`, via the countdown DP (hardware auto-off).

    Re-clamps to caps as a final guard. Verifies the countdown DP was accepted
    (the actual auto-off safety) with retry logic for Tuya's eventual consistency.
    If countdown is not confirmed after all attempts, sends a failsafe OFF command
    so the valve doesn't stay open indefinitely.
    """
    m = clamp_minutes(minutes, zone, watered_today)
    dev = zone["tuya_device_id"]
    switch_dp = zone.get("switch_dp", "switch")
    countdown_dp = zone.get("countdown_dp", "countdown_1")
    if m == 0:
        return {"zone": zone["name"], "minutes": 0, "ok": True, "note": "nothing to do (capped to 0)"}
    if dry_run:
        return {"zone": zone["name"], "minutes": m, "ok": True, "dry_run": True,
                "would_send": [{"code": switch_dp, "value": True},
                               {"code": countdown_dp, "value": m * 60}]}
    tuya.send_commands(dev, [{"code": switch_dp, "value": True},
                             {"code": countdown_dp, "value": m * 60}])
    # Verify the countdown DP was accepted — it is the hardware auto-off safety.
    # Loop with sleep only *between* attempts so a fast fake incurs no delay.
    countdown_set = False
    st: dict = {}
    for attempt in range(confirm_attempts):
        if attempt > 0:
            time.sleep(confirm_sleep_s)
        st = tuya.status(dev)
        countdown_val = st.get(countdown_dp)
        if isinstance(countdown_val, (int, float)) and countdown_val > 0:
            countdown_set = True
            break
    if not countdown_set:
        # Failsafe: valve may be open with no auto-off — close it immediately.
        tuya.send_commands(dev, [{"code": switch_dp, "value": False}])
        return {
            "zone": zone["name"],
            "minutes": m,
            "ok": False,
            "note": "countdown not accepted — sent failsafe OFF; valve auto-close NOT guaranteed",
        }
    switch_confirmed = bool(st.get(switch_dp) or st.get("switch_1"))
    return {
        "zone": zone["name"],
        "minutes": m,
        "ok": True,
        "switch_confirmed": switch_confirmed,
        "note": f"watering started (~{m}min, auto-off armed)",
    }


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
    return {"precip_12h_mm": float(sum(precip)),
            "precip_prob_pct": float(max(prob) if prob else 0),
            "et0_mm": float(d["daily"]["et0_fao_evapotranspiration"][0]),
            "temp_high_c": float(d["daily"]["temperature_2m_max"][0])}


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
    tuya = _tuya_from_env()
    if not args.dry_run:
        tuya.token()
    r = do_water(tuya, zone=z, minutes=args.minutes,
                 watered_today=st.watered_today(z["name"], now), dry_run=args.dry_run)
    if r.get("ok") and not args.dry_run and r["minutes"] > 0:
        st.add_watered(z["name"], r["minutes"], now=now)
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

    stp = sub.add_parser("status", parents=[_defaults])
    stp.add_argument("--zone", required=True)

    args = p.parse_args(argv)
    try:
        cfg = load_config(args.config)
        return {"sensors": cmd_sensors, "weather": cmd_weather, "plan": cmd_plan,
                "water": cmd_water, "status": cmd_status}[args.cmd](args, cfg)
    except (OSError, urllib.error.URLError, TuyaError, json.JSONDecodeError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
