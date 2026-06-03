#!/usr/bin/env python3
"""garden — weather-aware irrigation CLI for the OpenClaw agent.

Self-contained, stdlib-only. Subcommands: sensors, weather, plan, water, status.
Safety-critical logic (formula, caps, valve commands) lives here, not in prompts.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
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
