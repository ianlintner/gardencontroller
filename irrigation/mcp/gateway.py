"""GatewayService — the non-bypassable irrigation policy enforcement point.

Holds the only live Tuya client and the only writable budget state. The MCP
server (server.py) is a thin wrapper that exposes these methods as tools.
Every method is pure of network I/O except via the injected `tuya` client, so
the whole service is unit-testable with a fake.
"""
from __future__ import annotations

import time

from garden_core import clamp_minutes, do_water


class GatewayService:
    def __init__(self, *, zones, tuya, budget, now_fn=time.time, audit=print,
                 confirm_attempts: int = 3, confirm_sleep_s: float = 1.0):
        self.zones = {z["name"]: z for z in zones}
        self.tuya = tuya
        self.budget = budget
        self.now_fn = now_fn
        self._audit = audit
        self._confirm_attempts = confirm_attempts
        self._confirm_sleep_s = confirm_sleep_s
        self._token_ok = False

    # --- audit -----------------------------------------------------------
    def _log(self, tool, zone, requested, granted, decision, ok):
        self._audit({"ts": int(self.now_fn()), "tool": tool, "zone": zone,
                     "requested": requested, "granted": granted,
                     "decision": decision, "ok": ok})

    def _ensure_token(self):
        if not self._token_ok:
            self.tuya.token()
            self._token_ok = True

    # --- tools -----------------------------------------------------------
    def list_zones(self):
        now = self.now_fn()
        out = []
        for name, z in self.zones.items():
            w = self.budget.watered_today(name, now)
            out.append({"zone": name,
                        "max_per_run": z["max_per_run"],
                        "max_per_day": z["max_per_day"],
                        "min_run": z.get("min_run", 1),
                        "watered_today": w,
                        "remaining_today": max(0.0, z["max_per_day"] - w)})
        self._log("list_zones", None, None, None, "ok", True)
        return out

    def water_zone(self, zone: str, minutes: int):
        z = self.zones.get(zone)
        if z is None:
            self._log("water_zone", zone, minutes, 0, "unknown zone", False)
            return {"zone": zone, "requested": minutes, "granted": 0,
                    "ok": False, "reason": "unknown zone"}
        now = self.now_fn()
        watered = self.budget.watered_today(zone, now)
        granted = clamp_minutes(minutes, z, watered)
        if granted == 0:
            reason = "clamped to 0 (below min_run or daily budget exhausted)"
            self._log("water_zone", zone, minutes, 0, reason, True)
            return {"zone": zone, "requested": minutes, "granted": 0,
                    "ok": True, "reason": reason}
        self._ensure_token()
        r = do_water(self.tuya, zone=z, minutes=granted, watered_today=watered,
                     dry_run=False, confirm_attempts=self._confirm_attempts,
                     confirm_sleep_s=self._confirm_sleep_s)
        actual = r.get("minutes", 0)
        if r.get("ok") and actual > 0:
            self.budget.add_watered(zone, actual, now)
        reason = r.get("note", "")
        self._log("water_zone", zone, minutes, actual, reason, bool(r.get("ok")))
        return {"zone": zone, "requested": minutes, "granted": actual,
                "ok": bool(r.get("ok")), "reason": reason,
                "switch_confirmed": r.get("switch_confirmed")}

    def get_zone_status(self, zone: str):
        z = self.zones.get(zone)
        if z is None:
            self._log("get_zone_status", zone, None, None, "unknown zone", False)
            return {"zone": zone, "ok": False, "reason": "unknown zone"}
        self._ensure_token()
        st = self.tuya.status(z["tuya_device_id"])
        self._log("get_zone_status", zone, None, None, "ok", True)
        return {"zone": zone, "ok": True, "status": st}

    def stop_zone(self, zone: str):
        z = self.zones.get(zone)
        if z is None:
            self._log("stop_zone", zone, None, None, "unknown zone", False)
            return {"zone": zone, "ok": False, "reason": "unknown zone"}
        self._ensure_token()
        switch_dp = z.get("switch_dp", "switch")
        self.tuya.send_commands(z["tuya_device_id"], [{"code": switch_dp, "value": False}])
        self._log("stop_zone", zone, None, None, "valve closed", True)
        return {"zone": zone, "ok": True}
