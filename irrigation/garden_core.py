#!/usr/bin/env python3
"""garden_core — shared, secret-free irrigation primitives.

Pure logic + the Tuya client + the valve routine. Imported by both the
read-only CLI (garden.py) and the Tuya MCP gateway (mcp/gateway.py). The Tuya
client only holds a secret where it is *constructed* — this module never reads
credentials itself.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def clamp_minutes(minutes: float, zone: dict, watered_today: float) -> int:
    """Clamp a proposed duration to the zone's hard caps. Final safety guard."""
    m = max(0.0, float(minutes))
    m = min(m, float(zone.get("max_per_run", 15)))
    if "max_per_day" in zone:
        remaining = max(0.0, float(zone["max_per_day"]) - float(watered_today))
        m = min(m, remaining)
    m = int(round(m))
    if m < int(zone.get("min_run", 1)):
        return 0
    return m


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
    """Open the zone's valve for `minutes` via the countdown DP (hardware auto-off).

    Re-clamps to caps, verifies the countdown DP was accepted, and sends a
    failsafe OFF if it was not.
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
        tuya.send_commands(dev, [{"code": switch_dp, "value": False}])
        return {"zone": zone["name"], "minutes": m, "ok": False,
                "note": "countdown not accepted — sent failsafe OFF; valve auto-close NOT guaranteed"}
    switch_confirmed = bool(st.get(switch_dp) or st.get("switch_1"))
    return {"zone": zone["name"], "minutes": m, "ok": True,
            "switch_confirmed": switch_confirmed,
            "note": f"watering started (~{m}min, auto-off armed)"}


class BudgetState:
    """Per-zone daily watering budget, keyed by local day in a configured tz.

    JSON persisted with atomic replace. Owned exclusively by the MCP sidecar;
    the agent can neither read nor reset it.
    """
    def __init__(self, base_dir, timezone: str = "UTC"):
        self.dir = Path(base_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "budget.json"
        self.tz = ZoneInfo(timezone)

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
        import os
        os.replace(tmp, self.path)

    def _day(self, now: float) -> str:
        return datetime.fromtimestamp(now, self.tz).strftime("%Y-%m-%d")

    def watered_today(self, zone: str, now: float) -> float:
        rec = self._load().get("watered", {}).get(zone, {})
        return float(rec.get("minutes", 0)) if rec.get("day") == self._day(now) else 0.0

    def add_watered(self, zone: str, minutes: float, now: float) -> None:
        d = self._load()
        w = d.setdefault("watered", {})
        rec = w.get(zone, {})
        cur = float(rec.get("minutes", 0)) if rec.get("day") == self._day(now) else 0.0
        w[zone] = {"day": self._day(now), "minutes": cur + float(minutes)}
        self._save(d)
