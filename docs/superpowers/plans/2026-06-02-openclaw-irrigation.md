# OpenClaw Weather-Aware Irrigation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A self-contained `garden` CLI that the existing OpenClaw agent runs 3×/day to propose (in Discord) and — on approval — execute weather-aware, capped watering of two Tuya zones.

**Architecture:** One stdlib-only Python file `irrigation/garden.py` with pure functions (formula, caps, Tuya request signing) and thin I/O wrappers (Prometheus, Open-Meteo, Tuya Cloud API, PVC JSON state). OpenClaw fetches it into `.local/bin/garden` at startup; OpenClaw's cron + a skill doc drive the workflow; approval and reporting happen in OpenClaw's native Discord. Safety-critical logic (formula, caps, valve command) lives in the CLI, never in prompts.

**Tech Stack:** Python 3 (stdlib only: `argparse`, `urllib`, `json`, `hmac`, `hashlib`, `time`), pytest for tests. Tuya Cloud OpenAPI, Open-Meteo, Prometheus HTTP API. Deployed via the bigboy Flux repo (OpenClaw deployment + Key Vault secrets).

---

## Shared data contracts (used across tasks)

```python
# sensors  -> {"zone": str, "soil_pct": float|None, "temp_c": float|None, "stale": bool, "age_s": float}
# weather  -> {"precip_12h_mm": float, "precip_prob_pct": float, "et0_mm": float, "temp_high_c": float}
# plan     -> [{"zone": str, "minutes": int, "reason": str}]
# config (irrigation/garden.config.json):
# {
#   "lat": 0.0, "lon": 0.0,
#   "et0_baseline_mm": 4.0, "rain_skip_mm": 2.0, "rain_skip_prob_pct": 60.0,
#   "heat_threshold_c": 32.0, "midday_cap_min": 5,
#   "prometheus_url": "http://prometheus.default.svc.cluster.local:9090",
#   "zones": [
#     {"name": "zone1", "tuya_device_id": "REPLACE", "prom_device_id": "garden-node-1",
#      "probe": "bed1", "target_pct": 40.0, "min_per_pct": 0.5,
#      "max_per_run": 15, "max_per_day": 30, "min_run": 1}
#   ]
# }
```

`run_phase` is one of `"morning" | "midday" | "evening"` (passed by the scheduled call).

---

## Task 1: Scaffold the irrigation package + config

**Files:**
- Create: `irrigation/garden.py`
- Create: `irrigation/garden.config.example.json`
- Create: `irrigation/tests/__init__.py`
- Create: `irrigation/tests/test_smoke.py`
- Create: `irrigation/requirements-dev.txt`

- [ ] **Step 1: Create the dev requirements**

`irrigation/requirements-dev.txt`:
```
pytest==8.3.4
```

- [ ] **Step 2: Create the example config**

`irrigation/garden.config.example.json`:
```json
{
  "lat": 0.0,
  "lon": 0.0,
  "et0_baseline_mm": 4.0,
  "rain_skip_mm": 2.0,
  "rain_skip_prob_pct": 60.0,
  "heat_threshold_c": 32.0,
  "midday_cap_min": 5,
  "prometheus_url": "http://prometheus.default.svc.cluster.local:9090",
  "zones": [
    {
      "name": "zone1",
      "tuya_device_id": "REPLACE_WITH_DEVICE_ID",
      "prom_device_id": "garden-node-1",
      "probe": "bed1",
      "target_pct": 40.0,
      "min_per_pct": 0.5,
      "max_per_run": 15,
      "max_per_day": 30,
      "min_run": 1
    }
  ]
}
```

- [ ] **Step 3: Create `garden.py` with a version stub**

`irrigation/garden.py`:
```python
#!/usr/bin/env python3
"""garden — weather-aware irrigation CLI for the OpenClaw agent.

Self-contained, stdlib-only. Subcommands: sensors, weather, plan, water, status.
Safety-critical logic (formula, caps, valve commands) lives here, not in prompts.
"""
from __future__ import annotations

__version__ = "0.1.0"
```

- [ ] **Step 4: Write the smoke test**

`irrigation/tests/test_smoke.py`:
```python
import importlib.util
from pathlib import Path

def load_garden():
    path = Path(__file__).resolve().parent.parent / "garden.py"
    spec = importlib.util.spec_from_file_location("garden", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def test_version():
    assert load_garden().__version__ == "0.1.0"
```

- [ ] **Step 5: Run the test**

Run: `cd irrigation && python3 -m venv .venv && .venv/bin/pip install -q -r requirements-dev.txt && .venv/bin/python -m pytest tests/ -q`
Expected: PASS (1 test).

- [ ] **Step 6: Commit**

```bash
git add irrigation/ && git commit -m "feat(irrigation): scaffold garden CLI package + config"
```

> NOTE: all later test files reuse the `load_garden()` helper. Put it in
> `irrigation/tests/conftest.py` as a fixture in Task 2 so tests can `import` it.

---

## Task 2: Caps clamping (pure function)

**Files:**
- Create: `irrigation/tests/conftest.py`
- Modify: `irrigation/garden.py`
- Create: `irrigation/tests/test_caps.py`

- [ ] **Step 1: Create the shared fixture**

`irrigation/tests/conftest.py`:
```python
import importlib.util
from pathlib import Path
import pytest

@pytest.fixture
def garden():
    path = Path(__file__).resolve().parent.parent / "garden.py"
    spec = importlib.util.spec_from_file_location("garden", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
```

- [ ] **Step 2: Write the failing test**

`irrigation/tests/test_caps.py`:
```python
def test_clamp_minutes_rounds_and_bounds(garden):
    z = {"max_per_run": 15, "min_run": 1}
    assert garden.clamp_minutes(8.4, z, watered_today=0) == 8
    assert garden.clamp_minutes(99, z, watered_today=0) == 15      # max_per_run
    assert garden.clamp_minutes(0.4, z, watered_today=0) == 0      # below min_run -> 0
    assert garden.clamp_minutes(3, z, watered_today=0) == 3

def test_clamp_respects_daily_remaining(garden):
    z = {"max_per_run": 15, "min_run": 1, "max_per_day": 30}
    assert garden.clamp_minutes(15, z, watered_today=28) == 2      # only 2 left today
    assert garden.clamp_minutes(15, z, watered_today=30) == 0      # nothing left
```

- [ ] **Step 3: Run it (fails)**

Run: `cd irrigation && .venv/bin/python -m pytest tests/test_caps.py -q`
Expected: FAIL (`AttributeError: module 'garden' has no attribute 'clamp_minutes'`).

- [ ] **Step 4: Implement**

Append to `irrigation/garden.py`:
```python
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
```

- [ ] **Step 5: Run it (passes)**

Run: `cd irrigation && .venv/bin/python -m pytest tests/test_caps.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add irrigation/garden.py irrigation/tests/ && git commit -m "feat(irrigation): hard-cap clamping with daily budget"
```

---

## Task 3: The watering formula (pure function)

This is the safety-critical brain. Constants come from config; the gardener tunes them.

**Files:**
- Modify: `irrigation/garden.py`
- Create: `irrigation/tests/test_plan.py`

- [ ] **Step 1: Write the failing tests**

`irrigation/tests/test_plan.py`:
```python
import pytest

ZONE = {"name": "zone1", "target_pct": 40.0, "min_per_pct": 0.5,
        "max_per_run": 15, "max_per_day": 30, "min_run": 1}
CFG = {"et0_baseline_mm": 4.0, "rain_skip_mm": 2.0, "rain_skip_prob_pct": 60.0,
       "heat_threshold_c": 32.0, "midday_cap_min": 5}

def plan_one(garden, **kw):
    base = dict(zone=ZONE, cfg=CFG, soil_pct=20.0, temp_c=25.0, stale=False,
                precip_12h_mm=0.0, precip_prob_pct=0.0, et0_mm=4.0,
                run_phase="morning", watered_today=0.0)
    base.update(kw)
    return garden.plan_zone(**base)

def test_skip_when_rain_forecast(garden):
    r = plan_one(garden, precip_12h_mm=5.0)
    assert r["minutes"] == 0 and "rain" in r["reason"].lower()

def test_skip_when_soil_at_target(garden):
    r = plan_one(garden, soil_pct=45.0)
    assert r["minutes"] == 0

def test_skip_when_sensor_stale(garden):
    r = plan_one(garden, stale=True)
    assert r["minutes"] == 0 and "stale" in r["reason"].lower()

def test_deficit_drives_minutes(garden):
    # deficit 20% * 0.5 min/% * (et0 4/4) = 10 min
    r = plan_one(garden, soil_pct=20.0, et0_mm=4.0)
    assert r["minutes"] == 10

def test_et0_scales_up_on_hot_dry_day(garden):
    # deficit 20 * 0.5 * (8/4) = 20 -> capped at 15
    r = plan_one(garden, soil_pct=20.0, et0_mm=8.0)
    assert r["minutes"] == 15

def test_midday_only_waters_in_heat(garden):
    cool = plan_one(garden, run_phase="midday", temp_c=25.0, soil_pct=20.0)
    assert cool["minutes"] == 0          # not hot enough -> midday skips
    hot = plan_one(garden, run_phase="midday", temp_c=35.0, soil_pct=20.0)
    assert 0 < hot["minutes"] <= CFG["midday_cap_min"]   # short burst, midday cap
```

- [ ] **Step 2: Run it (fails)**

Run: `cd irrigation && .venv/bin/python -m pytest tests/test_plan.py -q`
Expected: FAIL (`plan_zone` undefined).

- [ ] **Step 3: Implement**

Append to `irrigation/garden.py`:
```python
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
```

- [ ] **Step 4: Run it (passes)**

Run: `cd irrigation && .venv/bin/python -m pytest tests/test_plan.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add irrigation/garden.py irrigation/tests/test_plan.py && git commit -m "feat(irrigation): deterministic watering formula"
```

---

## Task 4: PVC state (daily totals, pending plan TTL, idempotency)

**Files:**
- Modify: `irrigation/garden.py`
- Create: `irrigation/tests/test_state.py`

- [ ] **Step 1: Write the failing tests**

`irrigation/tests/test_state.py`:
```python
def test_watered_today_roundtrip(garden, tmp_path):
    st = garden.State(tmp_path)
    assert st.watered_today("zone1", now=1000.0) == 0
    st.add_watered("zone1", 10, now=1000.0)
    assert st.watered_today("zone1", now=1500.0) == 10
    # a day later, resets
    assert st.watered_today("zone1", now=1000.0 + 90000) == 0

def test_pending_plan_ttl(garden, tmp_path):
    st = garden.State(tmp_path)
    st.set_pending([{"zone": "zone1", "minutes": 8, "reason": "x"}], now=1000.0, ttl_s=7200)
    assert st.get_pending(now=2000.0) == [{"zone": "zone1", "minutes": 8, "reason": "x"}]
    assert st.get_pending(now=1000.0 + 7201) is None      # expired

def test_idempotency_key(garden, tmp_path):
    st = garden.State(tmp_path)
    assert st.seen_run("morning-2026-06-02") is False
    st.mark_run("morning-2026-06-02")
    assert st.seen_run("morning-2026-06-02") is True
```

- [ ] **Step 2: Run it (fails)**

Run: `cd irrigation && .venv/bin/python -m pytest tests/test_state.py -q`
Expected: FAIL (`State` undefined).

- [ ] **Step 3: Implement**

Append to `irrigation/garden.py`:
```python
import json, os
from pathlib import Path

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
        if not p or now > p["expires"]:
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
```

Also add `import time` near the top of `garden.py` if not already present (Task 1 only imported `__future__`).

- [ ] **Step 4: Run it (passes)**

Run: `cd irrigation && .venv/bin/python -m pytest tests/test_state.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add irrigation/garden.py irrigation/tests/test_state.py && git commit -m "feat(irrigation): PVC state (daily totals, pending TTL, idempotency)"
```

---

## Task 5: Tuya Cloud API request signing (pure) + client

**Files:**
- Modify: `irrigation/garden.py`
- Create: `irrigation/tests/test_tuya_sign.py`

Tuya OpenAPI signing: `sign = HMAC_SHA256(client_id + access_token + t + nonce + stringToSign, secret).hexdigest().upper()`, where `stringToSign = METHOD + "\n" + SHA256(body) + "\n" + "" + "\n" + path`. For the token request `access_token` is empty.

- [ ] **Step 1: Write the failing test (deterministic signing)**

`irrigation/tests/test_tuya_sign.py`:
```python
import hashlib, hmac

def test_string_to_sign(garden):
    sts = garden.tuya_string_to_sign("GET", "", "/v1.0/token?grant_type=1")
    body_hash = hashlib.sha256(b"").hexdigest()
    assert sts == f"GET\n{body_hash}\n\n/v1.0/token?grant_type=1"

def test_signature_matches_manual_hmac(garden):
    # Tuya concatenation order: client_id + access_token + t + nonce + stringToSign
    payload = "cid" + "" + "1700000000000" + "n" + "GET\nx\n\n/p"
    expected = hmac.new(b"sec", payload.encode(), hashlib.sha256).hexdigest().upper()
    sig = garden.tuya_sign(client_id="cid", secret="sec", access_token="",
                           t="1700000000000", nonce="n", string_to_sign="GET\nx\n\n/p")
    assert sig == expected
```

- [ ] **Step 2: Run it (fails)**

Run: `cd irrigation && .venv/bin/python -m pytest tests/test_tuya_sign.py -q`
Expected: FAIL (functions undefined).

- [ ] **Step 3: Implement signing + client**

Append to `irrigation/garden.py`:
```python
import hmac, hashlib, urllib.request, urllib.error

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
```

- [ ] **Step 4: Run it (passes)**

Run: `cd irrigation && .venv/bin/python -m pytest tests/test_tuya_sign.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add irrigation/garden.py irrigation/tests/test_tuya_sign.py && git commit -m "feat(irrigation): Tuya Cloud API signing + client"
```

---

## Task 6: Watering action with cap re-clamp + countdown + confirm (mocked Tuya)

Water timers expose a **countdown DP** (seconds) that auto-closes the valve — safer than blocking the process. DP codes vary by model; default `switch` + `countdown_1`, overridable in config (`switch_dp`, `countdown_dp`). Confirm by reading status back.

**Files:**
- Modify: `irrigation/garden.py`
- Create: `irrigation/tests/test_water.py`

- [ ] **Step 1: Write the failing tests (fake Tuya)**

`irrigation/tests/test_water.py`:
```python
class FakeTuya:
    def __init__(self): self.sent = []; self._status = {"switch": False}
    def send_commands(self, device_id, commands):
        self.sent.append((device_id, commands))
        for c in commands:
            if c["code"] in ("switch", "switch_1"): self._status["switch"] = c["value"]
            if c["code"].startswith("countdown"): self._status[c["code"]] = c["value"]
        return True
    def status(self, device_id): return dict(self._status)

def test_water_sends_countdown_and_reports(garden):
    z = {"name": "zone1", "tuya_device_id": "dev1", "max_per_run": 15,
         "max_per_day": 30, "min_run": 1, "switch_dp": "switch", "countdown_dp": "countdown_1"}
    t = FakeTuya()
    r = garden.do_water(t, zone=z, minutes=8, watered_today=0, dry_run=False)
    assert r["minutes"] == 8 and r["ok"] is True
    # set switch on + countdown 480s
    codes = {c["code"]: c["value"] for (_, cmds) in t.sent for c in cmds}
    assert codes["switch"] is True and codes["countdown_1"] == 480

def test_water_reclamps_over_cap(garden):
    z = {"name": "zone1", "tuya_device_id": "dev1", "max_per_run": 15,
         "max_per_day": 30, "min_run": 1, "switch_dp": "switch", "countdown_dp": "countdown_1"}
    t = FakeTuya()
    r = garden.do_water(t, zone=z, minutes=999, watered_today=0, dry_run=False)
    assert r["minutes"] == 15            # re-clamped even if asked for 999

def test_water_dry_run_sends_nothing(garden):
    z = {"name": "zone1", "tuya_device_id": "dev1", "max_per_run": 15,
         "max_per_day": 30, "min_run": 1, "switch_dp": "switch", "countdown_dp": "countdown_1"}
    t = FakeTuya()
    r = garden.do_water(t, zone=z, minutes=8, watered_today=0, dry_run=True)
    assert r["dry_run"] is True and t.sent == []
```

- [ ] **Step 2: Run it (fails)**

Run: `cd irrigation && .venv/bin/python -m pytest tests/test_water.py -q`
Expected: FAIL (`do_water` undefined).

- [ ] **Step 3: Implement**

Append to `irrigation/garden.py`:
```python
def do_water(tuya, *, zone, minutes, watered_today, dry_run) -> dict:
    """Open the zone's valve for `minutes`, via the countdown DP (hardware auto-off).
    Re-clamps to caps as a final guard. Confirms the switch reads on after sending.
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
    st = tuya.status(dev)
    on = bool(st.get(switch_dp) or st.get("switch_1"))
    return {"zone": zone["name"], "minutes": m, "ok": on,
            "note": "watering started" if on else "WARNING: valve did not confirm ON"}
```

- [ ] **Step 4: Run it (passes)**

Run: `cd irrigation && .venv/bin/python -m pytest tests/test_water.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add irrigation/garden.py irrigation/tests/test_water.py && git commit -m "feat(irrigation): valve watering via countdown DP + cap re-clamp + dry-run"
```

---

## Task 7: HTTP clients — Prometheus sensors + Open-Meteo weather (mocked)

**Files:**
- Modify: `irrigation/garden.py`
- Create: `irrigation/tests/test_clients.py`

- [ ] **Step 1: Write the failing tests (inject a fake fetch)**

`irrigation/tests/test_clients.py`:
```python
def test_read_sensors_parses_prom(garden):
    def fake_get(url):  # returns Prometheus instant-query JSON
        if "soil_moisture_percent" in url:
            return {"data": {"result": [{"value": [1000.0, "22.5"]}]}}
        if "push_timestamp" in url:
            return {"data": {"result": [{"value": [1000.0, "990.0"]}]}}  # 10s old
        if "temperature" in url:
            return {"data": {"result": [{"value": [1000.0, "31.0"]}]}}
        return {"data": {"result": []}}
    z = {"name": "zone1", "prom_device_id": "garden-node-1", "probe": "bed1"}
    r = garden.read_sensors(z, prom_url="http://p", now=1000.0, get_json=fake_get, max_age_s=300)
    assert r["soil_pct"] == 22.5 and r["temp_c"] == 31.0 and r["stale"] is False

def test_read_sensors_flags_stale(garden):
    def fake_get(url):
        if "soil_moisture_percent" in url:
            return {"data": {"result": [{"value": [5000.0, "22.5"]}]}}
        if "push_timestamp" in url:
            return {"data": {"result": [{"value": [5000.0, "1000.0"]}]}}  # 4000s old
        return {"data": {"result": []}}
    z = {"name": "zone1", "prom_device_id": "garden-node-1", "probe": "bed1"}
    r = garden.read_sensors(z, prom_url="http://p", now=5000.0, get_json=fake_get, max_age_s=300)
    assert r["stale"] is True

def test_read_weather_parses_open_meteo(garden):
    def fake_get(url):
        return {"hourly": {"precipitation": [0.0, 1.0, 0.5] + [0.0]*9,
                            "precipitation_probability": [10, 70, 20] + [0]*9},
                "daily": {"et0_fao_evapotranspiration": [5.2], "temperature_2m_max": [33.0]}}
    r = garden.read_weather(lat=1.0, lon=2.0, get_json=fake_get)
    assert round(r["precip_12h_mm"], 1) == 1.5
    assert r["precip_prob_pct"] == 70 and r["et0_mm"] == 5.2 and r["temp_high_c"] == 33.0
```

- [ ] **Step 2: Run it (fails)**

Run: `cd irrigation && .venv/bin/python -m pytest tests/test_clients.py -q`
Expected: FAIL (functions undefined).

- [ ] **Step 3: Implement**

Append to `irrigation/garden.py`:
```python
import urllib.parse

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
```

- [ ] **Step 4: Run it (passes)**

Run: `cd irrigation && .venv/bin/python -m pytest tests/test_clients.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add irrigation/garden.py irrigation/tests/test_clients.py && git commit -m "feat(irrigation): Prometheus + Open-Meteo clients"
```

---

## Task 8: CLI wiring (argparse subcommands) + config/env loading

**Files:**
- Modify: `irrigation/garden.py`
- Create: `irrigation/tests/test_cli.py`

- [ ] **Step 1: Write the failing test (invoke main with args)**

`irrigation/tests/test_cli.py`:
```python
import json

def test_cli_plan_outputs_json(garden, tmp_path, monkeypatch, capsys):
    cfg = {"lat": 1, "lon": 2, "et0_baseline_mm": 4.0, "rain_skip_mm": 2.0,
           "rain_skip_prob_pct": 60.0, "heat_threshold_c": 32.0, "midday_cap_min": 5,
           "prometheus_url": "http://p",
           "zones": [{"name": "zone1", "tuya_device_id": "d", "prom_device_id": "garden-node-1",
                      "probe": "bed1", "target_pct": 40.0, "min_per_pct": 0.5,
                      "max_per_run": 15, "max_per_day": 30, "min_run": 1}]}
    cfg_path = tmp_path / "c.json"; cfg_path.write_text(json.dumps(cfg))
    monkeypatch.setattr(garden, "read_sensors", lambda z, **k: {"zone": z["name"], "soil_pct": 20.0,
                        "temp_c": 25.0, "stale": False, "age_s": 5})
    monkeypatch.setattr(garden, "read_weather", lambda **k: {"precip_12h_mm": 0.0,
                        "precip_prob_pct": 0.0, "et0_mm": 4.0, "temp_high_c": 25.0})
    rc = garden.main(["plan", "--config", str(cfg_path), "--phase", "morning",
                      "--state", str(tmp_path)])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["zone"] == "zone1" and payload[0]["minutes"] == 10
```

- [ ] **Step 2: Run it (fails)**

Run: `cd irrigation && .venv/bin/python -m pytest tests/test_cli.py -q`
Expected: FAIL (`main` undefined).

- [ ] **Step 3: Implement the CLI**

Append to `irrigation/garden.py`:
```python
import argparse, sys

def load_config(path):
    return json.loads(Path(path).read_text())

def _zone(cfg, name):
    for z in cfg["zones"]:
        if z["name"] == name:
            return z
    raise SystemExit(f"unknown zone: {name}")

def _tuya_from_env():
    return Tuya(os.environ["TUYA_CLIENT_ID"], os.environ["TUYA_CLIENT_SECRET"],
                region=os.environ.get("TUYA_REGION", "us"))

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
    tuya = _tuya_from_env(); tuya.token()
    print(json.dumps(tuya.status(z["tuya_device_id"])))
    return 0

def main(argv=None):
    p = argparse.ArgumentParser(prog="garden")
    p.add_argument("--config", default=os.environ.get("GARDEN_CONFIG", "garden.config.json"))
    p.add_argument("--state", default=os.environ.get("GARDEN_STATE",
                   os.path.expanduser("~/.openclaw/garden")))
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("sensors"); sp.add_argument("--zone", required=True)
    sub.add_parser("weather")
    pp = sub.add_parser("plan"); pp.add_argument("--phase", default="morning",
                                                 choices=["morning", "midday", "evening"])
    wp = sub.add_parser("water"); wp.add_argument("--zone", required=True)
    wp.add_argument("--minutes", type=int, required=True); wp.add_argument("--dry-run", action="store_true")
    stp = sub.add_parser("status"); stp.add_argument("--zone", required=True)
    args = p.parse_args(argv)
    cfg = load_config(args.config)
    return {"sensors": cmd_sensors, "weather": cmd_weather, "plan": cmd_plan,
            "water": cmd_water, "status": cmd_status}[args.cmd](args, cfg)

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run it (passes) + full suite**

Run: `cd irrigation && .venv/bin/python -m pytest tests/ -q`
Expected: PASS (all tests).

- [ ] **Step 5: Make executable + commit**

```bash
chmod +x irrigation/garden.py
git add irrigation/garden.py irrigation/tests/test_cli.py && git commit -m "feat(irrigation): CLI subcommands (sensors/weather/plan/water/status)"
```

---

## Task 9: Skill doc + README

**Files:**
- Create: `irrigation/SKILL.md`
- Create: `irrigation/README.md`

- [ ] **Step 1: Write the skill/workflow doc**

`irrigation/SKILL.md` — describes, for the OpenClaw agent: the 3×/day workflow, exact commands, the Discord proposal format, the approve→`garden water` flow, and the rule "never water without explicit approval; never exceed caps; on any error, skip and report." Include the literal command sequence:
```
garden plan --phase <morning|midday|evening>      # -> JSON proposal, saved as pending
# post each zone with minutes>0 to Discord: "Zone X: N min — <reason>. Reply ✅ to approve."
# on approval:
garden water --zone <zone> --minutes <N>          # waters within caps, reports
```
State the dry-run rehearsal: `garden water --zone zone1 --minutes 5 --dry-run`.

- [ ] **Step 2: Write the README**

`irrigation/README.md` — local dev (venv + pytest), config (`cp garden.config.example.json garden.config.json`), env vars (`TUYA_CLIENT_ID/SECRET/REGION`, `GARDEN_CONFIG`, `GARDEN_STATE`), and the Tuya prerequisite setup (developer project, device IDs, DP codes).

- [ ] **Step 3: Commit**

```bash
git add irrigation/SKILL.md irrigation/README.md && git commit -m "docs(irrigation): skill workflow + README"
```

---

## Task 10: Self-review the full suite + push

- [ ] **Step 1: Run everything**

Run: `cd irrigation && .venv/bin/python -m pytest tests/ -q`
Expected: PASS (all).

- [ ] **Step 2: Lint the JSON config example loads**

Run: `cd irrigation && .venv/bin/python -c "import json; json.load(open('garden.config.example.json'))"`
Expected: no error.

- [ ] **Step 3: Push**

```bash
git push origin main
```

---

## Task 11: bigboy — fetch `garden` into OpenClaw at startup

**Files:**
- Modify: `~/projects/bigboy/k8s/apps/openclaw/base/deployment.yaml` (startup args block)

- [ ] **Step 1:** In the openclaw container's startup script (after the `opencode` install block, before the `exec node ...` line), add a block that fetches the CLI and ensures config/state dirs:

```sh
# Install/refresh the garden irrigation CLI (single self-contained file, source of truth in gardencontroller)
echo "Installing garden CLI..."
curl -fsSL https://raw.githubusercontent.com/ianlintner/gardencontroller/main/irrigation/garden.py \
  -o /home/node/.openclaw/.local/bin/garden && chmod +x /home/node/.openclaw/.local/bin/garden
mkdir -p /home/node/.openclaw/garden
# Write garden.config.json from the GARDEN_CONFIG_JSON secret if present
if [ -n "$GARDEN_CONFIG_JSON" ]; then
  printf '%s' "$GARDEN_CONFIG_JSON" > /home/node/.openclaw/garden/garden.config.json
fi
export GARDEN_CONFIG=/home/node/.openclaw/garden/garden.config.json
export GARDEN_STATE=/home/node/.openclaw/garden
```

- [ ] **Step 2:** Verify the deployment still renders:

Run: `cd ~/projects/bigboy && kustomize build k8s >/dev/null && echo OK`
Expected: `OK`.

- [ ] **Step 3:** Commit (do NOT push yet — push happens after secrets exist, Task 13):

```bash
cd ~/projects/bigboy && git add k8s/apps/openclaw/base/deployment.yaml \
  && git commit -m "feat(openclaw): install garden irrigation CLI at startup"
```

---

## Task 12: bigboy — add Tuya/location secrets to OpenClaw

**Files:**
- Modify: `~/projects/bigboy/k8s/apps/openclaw/overlays/prod/secret-provider-class.yaml`

Add these Key Vault objects + map them into the `openclaw-secrets` k8s secret (mirror the existing entries' structure exactly): `TUYA-CLIENT-ID`→`TUYA_CLIENT_ID`, `TUYA-CLIENT-SECRET`→`TUYA_CLIENT_SECRET`, `TUYA-REGION`→`TUYA_REGION`, `GARDEN-CONFIG-JSON`→`GARDEN_CONFIG_JSON` (the full config file as one secret, holding lat/lon + per-zone tuya_device_id + DP codes).

- [ ] **Step 1:** Read the existing file to copy its exact object/secretObject pattern:

Run: `sed -n '1,80p' ~/projects/bigboy/k8s/apps/openclaw/overlays/prod/secret-provider-class.yaml`

- [ ] **Step 2:** Add the four `objectName` entries under `parameters.objects` and the four `data` mappings under `secretObjects[0].data`, matching the existing indentation/style.

- [ ] **Step 3:** Render check:

Run: `cd ~/projects/bigboy && kustomize build k8s >/dev/null && echo OK`
Expected: `OK`.

- [ ] **Step 4:** Commit (still don't push):

```bash
cd ~/projects/bigboy && git add k8s/apps/openclaw/overlays/prod/secret-provider-class.yaml \
  && git commit -m "feat(openclaw): Tuya + garden config secrets via Key Vault"
```

> **MANUAL PREREQ (gardener):** before pushing, create the Key Vault secrets
> `TUYA-CLIENT-ID/SECRET/REGION` and `GARDEN-CONFIG-JSON` in `openclaw-kv-301919`.
> `GARDEN-CONFIG-JSON` = the filled-in `garden.config.json` (real device IDs, lat/lon,
> and the confirmed `switch_dp`/`countdown_dp` codes for the timers).

---

## Task 13: Determine OpenClaw cron + register the irrigation schedule

**Files:**
- Research, then modify OpenClaw config (the `openclaw.json` heredoc in `deployment.yaml`, or a scheduled-task config OpenClaw supports).

- [ ] **Step 1: Determine OpenClaw's scheduler mechanism.** Read OpenClaw's docs/config schema for cron/scheduled tasks (the gateway likely supports a `schedules`/`cron` block or a CLI). Check the running container's help:

Run: `kubectl -n bot exec deploy/openclaw -c openclaw -- node dist/index.js --help 2>&1 | head -40`
Then search docs for "cron"/"schedule".

- [ ] **Step 2:** Add three scheduled tasks (≈05:30, 12:30, 18:00 local) that start an agent run with the irrigation prompt: *"Run the garden irrigation check for phase <morning|midday|evening> per SKILL.md."* Encode them in whatever form OpenClaw supports (config block or a registered task). Reference `irrigation/SKILL.md` content as the agent instructions (copy it into the agent's workspace or system prompt).

- [ ] **Step 3:** Render check + commit:

```bash
cd ~/projects/bigboy && kustomize build k8s >/dev/null && echo OK
git add -A k8s/apps/openclaw && git commit -m "feat(openclaw): register 3x/day irrigation schedule + skill"
```

---

## Task 14: Deploy + dry-run rehearsal (after manual prereqs)

- [ ] **Step 1:** Confirm Key Vault secrets exist (Task 12 manual prereq done), then push bigboy `main`:

```bash
cd ~/projects/bigboy && git push origin main
```

- [ ] **Step 2:** Wait for Flux + OpenClaw restart; verify the CLI installed:

Run: `kubectl -n bot exec deploy/openclaw -c openclaw -- sh -lc 'garden --help'`
Expected: argparse help.

- [ ] **Step 3:** Live read-only checks from inside the pod:

Run: `kubectl -n bot exec deploy/openclaw -c openclaw -- sh -lc 'garden weather && garden sensors --zone zone1'`
Expected: weather JSON + sensor JSON (real soil %).

- [ ] **Step 4:** Dry-run the valve path (sends nothing):

Run: `kubectl -n bot exec deploy/openclaw -c openclaw -- sh -lc 'garden water --zone zone1 --minutes 5 --dry-run'`
Expected: `{"...","dry_run": true,"would_send":[...]}`.

- [ ] **Step 5:** In Discord, trigger an irrigation check; confirm the agent posts a proposal and that approving it runs a **real** short water (start with `max_per_run` low, e.g. 3, for the first live test) and the valve confirms on, then auto-closes via countdown. Verify `garden status --zone zone1` shows the switch off after the countdown.

- [ ] **Step 6:** Done — irrigation loop live.

---

## Notes / known-unknowns (resolve during execution)

- **Tuya DP codes** (`switch` vs `switch_1`, `countdown_1` value units) vary by timer model — confirm in the Tuya IoT "Device Debugging" panel and set in `garden.config.json`. Tests are model-agnostic (DP codes are config).
- **OpenClaw scheduler syntax** (Task 13) is the main unknown — Step 1 resolves it before wiring.
- **Tuya region host** — set `TUYA_REGION` to match where the Smart Life account is registered (us/eu/cn/in).
