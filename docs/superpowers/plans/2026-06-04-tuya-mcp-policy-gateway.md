# Tuya MCP Policy Gateway Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the Tuya credential and all non-bypassable irrigation enforcement out of the OpenClaw agent into a sidecar MCP server, so the agent can only open valves through a narrow 4-tool API bounded by per-run and per-zone daily caps.

**Architecture:** Extract the proven Tuya client + caps + auto-off logic into a shared `garden_core.py`. Build a testable `GatewayService` (enforcement + audit) that a thin FastMCP server wraps. Strip every Tuya code path out of the agent-facing `garden.py` CLI so it needs no credential. Deploy the MCP as a sidecar container in the OpenClaw pod with the Tuya secret and budget-state PVC mounted only to the sidecar.

**Tech Stack:** Python 3.11 (stdlib + `mcp` official SDK / FastMCP), pytest, Docker, Azure Key Vault CSI, Flux/kustomize (bigboy repo).

---

## File Structure

```
irrigation/
  garden_core.py          # NEW — shared: clamp_minutes, Tuya, do_water, BudgetState
  garden.py               # MODIFIED — keeps sensors/weather/plan; loses all Tuya code
  mcp/
    gateway.py            # NEW — GatewayService: enforcement + audit (testable, no I/O server)
    server.py             # NEW — FastMCP wrapper: 4 tools, builds service from env+config
    config.example.json   # NEW — authoritative caps config (sidecar-mounted)
    requirements.txt      # NEW — mcp SDK
    Dockerfile            # NEW — sidecar image
    tests/
      conftest.py         # NEW — loads gateway.py + garden_core.py
      test_budget.py      # NEW — tz-aware daily reset
      test_gateway.py     # NEW — enforcement: allowlist, clamp, budget, failsafe, audit
      test_server_build.py# NEW — service builds from config
  tests/
    conftest.py           # MODIFIED — add `core` fixture
    test_caps.py          # MODIFIED — use `core` fixture
    test_tuya_sign.py     # MODIFIED — use `core` fixture
    test_water.py         # MODIFIED — use `core` fixture
    test_state.py         # DELETED in Task 7 (State removed)
    test_cli.py           # MODIFIED in Task 7 (water/status gone; key-free)
  SKILL.md                # MODIFIED in Task 9 — MCP tools replace garden water/status
.github/workflows/
  mcp-image.yml           # NEW — build/push garden-tuya-mcp image
```

Run all irrigation tests with: `cd irrigation && python3 -m pytest -q`
Run MCP tests with: `cd irrigation && python3 -m pytest mcp/tests -q`

---

### Task 1: Extract shared core module (`garden_core.py`)

Move the Tuya/caps/water primitives out of `garden.py` into a new shared module. `garden.py` keeps importing them (still used by the CLI until Task 7). Re-point the tests for those primitives at a new `core` fixture.

**Files:**
- Create: `irrigation/garden_core.py`
- Modify: `irrigation/garden.py` (remove moved defs, add import)
- Modify: `irrigation/tests/conftest.py` (add `core` fixture)
- Modify: `irrigation/tests/test_caps.py`, `test_tuya_sign.py`, `test_water.py` (use `core`)

- [ ] **Step 1: Create `garden_core.py` with the moved code**

Create `irrigation/garden_core.py` with exactly these definitions (cut verbatim from `garden.py`):

```python
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
```

(The `datetime`/`ZoneInfo` imports are unused until Task 2 — leave them in, they belong to `BudgetState`.)

- [ ] **Step 2: Delete the moved defs from `garden.py` and import them**

In `irrigation/garden.py`, DELETE these definitions (now in `garden_core`): `clamp_minutes`, `tuya_string_to_sign`, `tuya_sign`, `class TuyaError`, `class Tuya`, `do_water`. Keep `plan_zone`, `class State`, `read_sensors`, `read_weather`, and the whole CLI.

Add this import directly under the existing `from pathlib import Path` line:

```python
from garden_core import (
    clamp_minutes, do_water, Tuya, TuyaError, tuya_string_to_sign, tuya_sign,
)
```

(The CLI's `cmd_water`/`cmd_status` and `plan_zone` still reference these names — the import keeps them resolvable until Task 7.)

- [ ] **Step 3: Add a `core` fixture to the test conftest**

In `irrigation/tests/conftest.py`, append:

```python
@pytest.fixture
def core():
    path = Path(__file__).resolve().parent.parent / "garden_core.py"
    spec = importlib.util.spec_from_file_location("garden_core", path)
    mod = importlib.util.module_from_spec(spec)
    import sys
    sys.modules["garden_core"] = mod  # so garden.py's `from garden_core import ...` resolves
    spec.loader.exec_module(mod)
    return mod
```

Also ensure the existing `garden` fixture can import `garden_core`: change the `garden` fixture body to register the core module first. Replace the `garden` fixture with:

```python
@pytest.fixture
def garden():
    import sys
    core_path = Path(__file__).resolve().parent.parent / "garden_core.py"
    core_spec = importlib.util.spec_from_file_location("garden_core", core_path)
    core_mod = importlib.util.module_from_spec(core_spec)
    sys.modules["garden_core"] = core_mod
    core_spec.loader.exec_module(core_mod)

    path = Path(__file__).resolve().parent.parent / "garden.py"
    spec = importlib.util.spec_from_file_location("garden", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
```

- [ ] **Step 4: Re-point the primitive tests at `core`**

In `irrigation/tests/test_caps.py`, `test_tuya_sign.py`, and `test_water.py`, change every `garden` fixture reference to `core` (function arg `garden` → `core`, and `garden.` → `core.`). Example for `test_caps.py`:

```python
def test_clamp_minutes_rounds_and_bounds(core):
    z = {"max_per_run": 15, "min_run": 1}
    assert core.clamp_minutes(8.4, z, watered_today=0) == 8
    assert core.clamp_minutes(99, z, watered_today=0) == 15
    assert core.clamp_minutes(0.4, z, watered_today=0) == 0
    assert core.clamp_minutes(3, z, watered_today=0) == 3

def test_clamp_respects_daily_remaining(core):
    z = {"max_per_run": 15, "min_run": 1, "max_per_day": 30}
    assert core.clamp_minutes(15, z, watered_today=28) == 2
    assert core.clamp_minutes(15, z, watered_today=30) == 0
```

Apply the same `garden`→`core` rename throughout `test_tuya_sign.py` and `test_water.py`.

- [ ] **Step 5: Run the full suite to verify the refactor is behavior-preserving**

Run: `cd irrigation && python3 -m pytest -q`
Expected: PASS (same count as before; `test_plan.py`/`test_cli.py`/`test_state.py`/`test_clients.py` still use `garden` and pass because `garden.py` re-imports the moved names).

- [ ] **Step 6: Commit**

```bash
cd ~/Projects/gardencontroller
git add irrigation/garden_core.py irrigation/garden.py irrigation/tests/
git commit -m "refactor: extract garden_core (Tuya, caps, do_water) shared module"
```

---

### Task 2: tz-aware `BudgetState` in `garden_core.py`

The MCP-owned daily budget, keyed by local day in a configured timezone, persisted atomically.

**Files:**
- Modify: `irrigation/garden_core.py` (add `BudgetState`)
- Test: `irrigation/mcp/tests/test_budget.py`

- [ ] **Step 1: Create the MCP test conftest**

Create `irrigation/mcp/tests/conftest.py`:

```python
import importlib.util
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[2]  # irrigation/

def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

@pytest.fixture
def core():
    return _load("garden_core", "garden_core.py")

@pytest.fixture
def gateway(core):
    return _load("gateway", "mcp/gateway.py")
```

Create empty `irrigation/mcp/tests/__init__.py`.

- [ ] **Step 2: Write the failing budget test**

Create `irrigation/mcp/tests/test_budget.py`:

```python
def test_budget_roundtrip(core, tmp_path):
    b = core.BudgetState(tmp_path, timezone="UTC")
    assert b.watered_today("zone1", now=1000.0) == 0
    b.add_watered("zone1", 10, now=1000.0)
    assert b.watered_today("zone1", now=1500.0) == 10

def test_budget_resets_at_local_midnight(core, tmp_path):
    # America/Chicago is UTC-5 (CDT) in June. Local midnight 2026-06-04 == 05:00Z.
    b = core.BudgetState(tmp_path, timezone="America/Chicago")
    # 04:59Z on 2026-06-04 is still 2026-06-03 *local* (23:59 CDT)
    before = 1780545540.0  # 2026-06-04T04:59:00Z
    after = 1780545660.0   # 2026-06-04T05:01:00Z  (00:01 CDT, new local day)
    b.add_watered("zone1", 20, now=before)
    assert b.watered_today("zone1", now=before) == 20
    assert b.watered_today("zone1", now=after) == 0  # rolled to a new local day

def test_budget_isolated_per_zone(core, tmp_path):
    b = core.BudgetState(tmp_path, timezone="UTC")
    b.add_watered("zone1", 5, now=1000.0)
    assert b.watered_today("zone2", now=1000.0) == 0
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd irrigation && python3 -m pytest mcp/tests/test_budget.py -q`
Expected: FAIL with `AttributeError: module 'garden_core' has no attribute 'BudgetState'`

- [ ] **Step 4: Implement `BudgetState`**

Append to `irrigation/garden_core.py`:

```python
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
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd irrigation && python3 -m pytest mcp/tests/test_budget.py -q`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
cd ~/Projects/gardencontroller
git add irrigation/garden_core.py irrigation/mcp/tests/
git commit -m "feat: tz-aware BudgetState for MCP daily-budget enforcement"
```

---

### Task 3: `GatewayService` — `list_zones` + `water_zone` enforcement

The non-bypassable enforcement core: allowlist, clamp through caps + daily budget, record on success, audit every call.

**Files:**
- Create: `irrigation/mcp/gateway.py`
- Test: `irrigation/mcp/tests/test_gateway.py`

- [ ] **Step 1: Write the failing enforcement tests**

Create `irrigation/mcp/tests/test_gateway.py`:

```python
import json


class FakeTuya:
    """Records commands; reflects countdown into status (auto-off confirmed)."""
    def __init__(self):
        self.sent = []
        self._status = {"switch": False}
        self.token_calls = 0
    def token(self):
        self.token_calls += 1
        return "tok"
    def send_commands(self, device_id, commands):
        self.sent.append((device_id, commands))
        for c in commands:
            if c["code"] in ("switch", "switch_1"):
                self._status["switch"] = c["value"]
            if c["code"].startswith("countdown"):
                self._status[c["code"]] = c["value"]
        return True
    def status(self, device_id):
        return dict(self._status)


ZONES = [
    {"name": "zone1", "tuya_device_id": "dev1", "switch_dp": "switch",
     "countdown_dp": "countdown_1", "max_per_run": 15, "max_per_day": 30, "min_run": 1},
    {"name": "zone2", "tuya_device_id": "dev2", "switch_dp": "switch",
     "countdown_dp": "countdown_1", "max_per_run": 10, "max_per_day": 20, "min_run": 1},
]


def _svc(gateway, core, tmp_path, tuya=None, now=1000.0):
    budget = core.BudgetState(tmp_path, timezone="UTC")
    audit = []
    svc = gateway.GatewayService(
        zones=ZONES, tuya=tuya or FakeTuya(), budget=budget,
        now_fn=lambda: now, audit=audit.append, confirm_sleep_s=0,
    )
    return svc, audit


def test_list_zones_reports_remaining_budget(gateway, core, tmp_path):
    svc, _ = _svc(gateway, core, tmp_path)
    rows = {r["zone"]: r for r in svc.list_zones()}
    assert rows["zone1"]["remaining_today"] == 30
    assert rows["zone2"]["max_per_run"] == 10

def test_water_unknown_zone_rejected(gateway, core, tmp_path):
    t = FakeTuya()
    svc, audit = _svc(gateway, core, tmp_path, tuya=t)
    r = svc.water_zone("zoneX", 5)
    assert r["ok"] is False and r["reason"] == "unknown zone"
    assert t.sent == []  # nothing sent
    assert json.loads(json.dumps(audit[-1]))["decision"] == "unknown zone"

def test_water_clamps_to_max_per_run(gateway, core, tmp_path):
    svc, _ = _svc(gateway, core, tmp_path)
    r = svc.water_zone("zone1", 999)
    assert r["requested"] == 999 and r["granted"] == 15 and r["ok"] is True

def test_water_accumulates_toward_daily_budget(gateway, core, tmp_path):
    svc, _ = _svc(gateway, core, tmp_path)            # zone1 max_per_day=30
    assert svc.water_zone("zone1", 15)["granted"] == 15
    assert svc.water_zone("zone1", 15)["granted"] == 15  # total 30
    third = svc.water_zone("zone1", 15)
    assert third["granted"] == 0 and third["ok"] is True  # budget exhausted -> clamp to 0

def test_water_sends_token_and_commands_on_success(gateway, core, tmp_path):
    t = FakeTuya()
    svc, _ = _svc(gateway, core, tmp_path, tuya=t)
    r = svc.water_zone("zone1", 8)
    assert r["granted"] == 8 and r["ok"] is True
    assert t.token_calls == 1
    codes = {c["code"]: c["value"] for (_, cmds) in t.sent for c in cmds}
    assert codes["switch"] is True and codes["countdown_1"] == 480
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd irrigation && python3 -m pytest mcp/tests/test_gateway.py -q`
Expected: FAIL with `ModuleNotFoundError`/`AttributeError` (no `gateway.py`).

- [ ] **Step 3: Implement `GatewayService` (list_zones + water_zone)**

Create `irrigation/mcp/gateway.py`:

```python
"""GatewayService — the non-bypassable irrigation policy enforcement point.

Holds the only live Tuya client and the only writable budget state. The MCP
server (server.py) is a thin wrapper that exposes these methods as tools.
Every method is pure of network I/O except via the injected `tuya` client, so
the whole service is unit-testable with a fake.
"""
from __future__ import annotations

import json
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd irrigation && python3 -m pytest mcp/tests/test_gateway.py -q`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
cd ~/Projects/gardencontroller
git add irrigation/mcp/gateway.py irrigation/mcp/tests/test_gateway.py
git commit -m "feat: GatewayService enforcement (allowlist, caps, daily budget, audit)"
```

---

### Task 4: `GatewayService` — `get_zone_status`, `stop_zone`, and auto-off failsafe

**Files:**
- Modify: `irrigation/mcp/gateway.py`
- Modify: `irrigation/mcp/tests/test_gateway.py`

- [ ] **Step 1: Write the failing tests**

Append to `irrigation/mcp/tests/test_gateway.py`:

```python
class FakeTuyaNoCountdown(FakeTuya):
    """Accepts commands but never reflects the countdown -> auto-off unconfirmed."""
    def status(self, device_id):
        s = dict(self._status)
        s.pop("countdown_1", None)
        return s


def test_get_zone_status_passthrough(gateway, core, tmp_path):
    t = FakeTuya()
    t._status = {"switch": True, "countdown_1": 300}
    svc, _ = _svc(gateway, core, tmp_path, tuya=t)
    r = svc.get_zone_status("zone1")
    assert r["ok"] is True and r["status"]["countdown_1"] == 300

def test_get_status_unknown_zone(gateway, core, tmp_path):
    svc, _ = _svc(gateway, core, tmp_path)
    assert svc.get_zone_status("nope")["ok"] is False

def test_stop_zone_sends_switch_off(gateway, core, tmp_path):
    t = FakeTuya()
    svc, _ = _svc(gateway, core, tmp_path, tuya=t)
    r = svc.stop_zone("zone1")
    assert r["ok"] is True
    codes = {c["code"]: c["value"] for (_, cmds) in t.sent for c in cmds}
    assert codes["switch"] is False

def test_water_failsafe_when_countdown_unconfirmed(gateway, core, tmp_path):
    t = FakeTuyaNoCountdown()
    svc, _ = _svc(gateway, core, tmp_path, tuya=t)
    r = svc.water_zone("zone1", 8)
    assert r["ok"] is False and "failsafe OFF" in r["reason"]
    # budget NOT charged on a failed run
    assert svc.list_zones()[0]["watered_today"] == 0
    # last command sent must be the failsafe OFF
    last_codes = {c["code"]: c["value"] for c in t.sent[-1][1]}
    assert last_codes["switch"] is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd irrigation && python3 -m pytest mcp/tests/test_gateway.py -q`
Expected: FAIL (`get_zone_status`/`stop_zone` undefined).

- [ ] **Step 3: Implement the two methods**

Append to `class GatewayService` in `irrigation/mcp/gateway.py`:

```python
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd irrigation && python3 -m pytest mcp/tests/test_gateway.py -q`
Expected: PASS (9 passed)

- [ ] **Step 5: Commit**

```bash
cd ~/Projects/gardencontroller
git add irrigation/mcp/gateway.py irrigation/mcp/tests/test_gateway.py
git commit -m "feat: gateway get_zone_status + stop_zone + auto-off failsafe coverage"
```

---

### Task 5: FastMCP server wrapper + config + requirements

A thin server that builds the service from env + config and exposes the four methods as MCP tools over localhost streamable-HTTP.

**Files:**
- Create: `irrigation/mcp/server.py`
- Create: `irrigation/mcp/config.example.json`
- Create: `irrigation/mcp/requirements.txt`
- Test: `irrigation/mcp/tests/test_server_build.py`

- [ ] **Step 1: Write the failing build test**

Create `irrigation/mcp/tests/test_server_build.py`:

```python
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def _load_server():
    # ensure deps are importable
    for name, rel in [("garden_core", "garden_core.py"), ("gateway", "mcp/gateway.py")]:
        spec = importlib.util.spec_from_file_location(name, ROOT / rel)
        mod = importlib.util.module_from_spec(spec); sys.modules[name] = mod
        spec.loader.exec_module(mod)
    spec = importlib.util.spec_from_file_location("server", ROOT / "mcp" / "server.py")
    mod = importlib.util.module_from_spec(spec); sys.modules["server"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_build_service_from_config(tmp_path, monkeypatch):
    cfg = {"region": "us", "timezone": "UTC",
           "zones": [{"name": "zone1", "tuya_device_id": "d", "switch_dp": "switch",
                      "countdown_dp": "countdown_1", "max_per_run": 15,
                      "max_per_day": 30, "min_run": 1}]}
    cfg_path = tmp_path / "c.json"; cfg_path.write_text(json.dumps(cfg))
    monkeypatch.setenv("GARDEN_MCP_CONFIG", str(cfg_path))
    monkeypatch.setenv("GARDEN_MCP_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("TUYA_CLIENT_ID", "cid")
    monkeypatch.setenv("TUYA_CLIENT_SECRET", "sec")
    server = _load_server()
    svc = server.build_service()
    assert "zone1" in svc.zones
    assert svc.list_zones()[0]["remaining_today"] == 30


def test_build_service_requires_credentials(tmp_path, monkeypatch):
    cfg_path = tmp_path / "c.json"
    cfg_path.write_text(json.dumps({"region": "us", "timezone": "UTC", "zones": []}))
    monkeypatch.setenv("GARDEN_MCP_CONFIG", str(cfg_path))
    monkeypatch.setenv("GARDEN_MCP_STATE", str(tmp_path / "state"))
    monkeypatch.delenv("TUYA_CLIENT_ID", raising=False)
    monkeypatch.delenv("TUYA_CLIENT_SECRET", raising=False)
    server = _load_server()
    try:
        server.build_service(); assert False, "expected SystemExit"
    except SystemExit:
        pass
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd irrigation && python3 -m pytest mcp/tests/test_server_build.py -q`
Expected: FAIL (no `server.py`).

- [ ] **Step 3: Implement `server.py` (build_service is import-safe; mcp run is guarded)**

Create `irrigation/mcp/server.py`:

```python
#!/usr/bin/env python3
"""garden-tuya-mcp — sidecar MCP server: the only holder of the Tuya key.

Exposes four tools (list_zones, get_zone_status, water_zone, stop_zone) over
localhost streamable-HTTP. All enforcement lives in GatewayService.

Env:
  GARDEN_MCP_CONFIG  path to caps config JSON (default mcp/config.json)
  GARDEN_MCP_STATE   budget-state dir (default /var/lib/garden-mcp)
  TUYA_CLIENT_ID / TUYA_CLIENT_SECRET / TUYA_REGION
  GARDEN_MCP_HOST / GARDEN_MCP_PORT  (default 127.0.0.1 / 8765)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from garden_core import Tuya, BudgetState
from gateway import GatewayService


def build_service() -> GatewayService:
    cfg_path = os.environ.get("GARDEN_MCP_CONFIG", "mcp/config.json")
    cfg = json.loads(Path(cfg_path).read_text())
    client_id = os.environ.get("TUYA_CLIENT_ID", "")
    secret = os.environ.get("TUYA_CLIENT_SECRET", "")
    if not client_id or not secret:
        raise SystemExit("error: TUYA_CLIENT_ID and TUYA_CLIENT_SECRET must be set")
    region = cfg.get("region", os.environ.get("TUYA_REGION", "us"))
    tuya = Tuya(client_id, secret, region=region)
    state_dir = os.environ.get("GARDEN_MCP_STATE", "/var/lib/garden-mcp")
    budget = BudgetState(state_dir, timezone=cfg.get("timezone", "UTC"))

    def audit(rec):
        print(json.dumps(rec), flush=True)

    return GatewayService(zones=cfg["zones"], tuya=tuya, budget=budget, audit=audit)


def main():
    from mcp.server.fastmcp import FastMCP
    svc = build_service()
    host = os.environ.get("GARDEN_MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("GARDEN_MCP_PORT", "8765"))
    mcp = FastMCP("garden-tuya", host=host, port=port)

    @mcp.tool()
    def list_zones() -> list:
        """List approved zones with their caps and today's remaining budget."""
        return svc.list_zones()

    @mcp.tool()
    def get_zone_status(zone: str) -> dict:
        """Return live valve state and countdown for a zone."""
        return svc.get_zone_status(zone)

    @mcp.tool()
    def water_zone(zone: str, minutes: int) -> dict:
        """Open a zone's valve for up to `minutes`, clamped to caps + daily budget."""
        return svc.water_zone(zone, minutes)

    @mcp.tool()
    def stop_zone(zone: str) -> dict:
        """Immediately close a zone's valve."""
        return svc.stop_zone(zone)

    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Create config example and requirements**

Create `irrigation/mcp/config.example.json`:

```json
{
  "region": "us",
  "timezone": "America/Chicago",
  "zones": [
    {"name": "zone1", "tuya_device_id": "REPLACE_DEVICE_ID_1", "switch_dp": "switch",
     "countdown_dp": "countdown_1", "max_per_run": 15, "max_per_day": 30, "min_run": 1},
    {"name": "zone2", "tuya_device_id": "REPLACE_DEVICE_ID_2", "switch_dp": "switch",
     "countdown_dp": "countdown_1", "max_per_run": 15, "max_per_day": 30, "min_run": 1}
  ]
}
```

Create `irrigation/mcp/requirements.txt`:

```
mcp>=1.2.0
```

- [ ] **Step 5: Install the SDK and run the build test**

Run:
```bash
cd ~/Projects/gardencontroller/irrigation
python3 -m pip install -r mcp/requirements.txt
python3 -m pytest mcp/tests/test_server_build.py -q
```
Expected: PASS (2 passed). (`build_service` does not import `mcp.server.fastmcp` — that import is inside `main()` — so the build test passes even before/without the SDK; installing it is needed for `main()` to run live.)

- [ ] **Step 6: Smoke-run the live server (manual sanity, then Ctrl-C)**

Run:
```bash
cd ~/Projects/gardencontroller/irrigation
GARDEN_MCP_CONFIG=mcp/config.example.json GARDEN_MCP_STATE=/tmp/gmcp \
TUYA_CLIENT_ID=x TUYA_CLIENT_SECRET=y \
timeout 4 python3 mcp/server.py || true
```
Expected: starts a Uvicorn/streamable-HTTP server bound to 127.0.0.1:8765 with no traceback (it exits after the 4s timeout). If it errors on `mcp` import, re-run Step 5's pip install.

- [ ] **Step 7: Commit**

```bash
cd ~/Projects/gardencontroller
git add irrigation/mcp/server.py irrigation/mcp/config.example.json irrigation/mcp/requirements.txt irrigation/mcp/tests/test_server_build.py
git commit -m "feat: FastMCP server wrapper (4 tools) + config + requirements"
```

---

### Task 6: Make `garden.py` key-free (strip all Tuya code paths)

Security-critical: the agent's CLI must have no Tuya credential and no Tuya code path. Remove `water`/`status` subcommands, `_tuya_from_env`, the `State` class (pending gate no longer enforced here), and the now-unused imports.

**Files:**
- Modify: `irrigation/garden.py`
- Delete: `irrigation/tests/test_state.py`
- Modify: `irrigation/tests/test_cli.py`

- [ ] **Step 1: Write the failing key-free test**

Replace the body of `irrigation/tests/test_cli.py` with these tests (delete the old `_DummyTuya`/water tests):

```python
import json
import pytest


def _make_cfg(tmp_path):
    cfg = {"lat": 1, "lon": 2, "et0_baseline_mm": 4.0, "rain_skip_mm": 2.0,
           "rain_skip_prob_pct": 60.0, "heat_threshold_c": 32.0, "midday_cap_min": 5,
           "prometheus_url": "http://p",
           "zones": [{"name": "zone1", "tuya_device_id": "d", "prom_device_id": "garden-node-1",
                      "probe": "bed1", "target_pct": 40.0, "min_per_pct": 0.5,
                      "max_per_run": 15, "max_per_day": 30, "min_run": 1}]}
    p = tmp_path / "c.json"; p.write_text(json.dumps(cfg)); return str(p)


def test_cli_plan_runs_without_tuya_env(garden, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("TUYA_CLIENT_ID", raising=False)
    monkeypatch.delenv("TUYA_CLIENT_SECRET", raising=False)
    monkeypatch.setattr(garden, "read_sensors", lambda z, **k: {"zone": z["name"], "soil_pct": 20.0,
                        "temp_c": 25.0, "stale": False, "age_s": 5})
    monkeypatch.setattr(garden, "read_weather", lambda **k: {"precip_12h_mm": 0.0,
                        "precip_prob_pct": 0.0, "et0_mm": 4.0, "temp_high_c": 25.0})
    rc = garden.main(["plan", "--config", _make_cfg(tmp_path), "--phase", "morning"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)[0]["minutes"] == 10


def test_cli_has_no_water_or_status_subcommand(garden, tmp_path):
    # argparse exits with code 2 on an unknown subcommand
    with pytest.raises(SystemExit):
        garden.main(["water", "--zone", "zone1", "--minutes", "5",
                     "--config", _make_cfg(tmp_path)])
    with pytest.raises(SystemExit):
        garden.main(["status", "--zone", "zone1", "--config", _make_cfg(tmp_path)])


def test_garden_module_has_no_tuya_symbols(garden):
    # the agent CLI must not carry a Tuya code path
    assert not hasattr(garden, "Tuya")
    assert not hasattr(garden, "do_water")
    assert not hasattr(garden, "_tuya_from_env")
    assert not hasattr(garden, "cmd_water")
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd irrigation && python3 -m pytest tests/test_cli.py -q`
Expected: FAIL (`garden` still has `Tuya`, `cmd_water`, and the `water` subcommand).

- [ ] **Step 3: Strip `garden.py`**

In `irrigation/garden.py`:

1. Change the core import to only what `plan` needs:
```python
from garden_core import clamp_minutes
```
(remove `do_water, Tuya, TuyaError, tuya_string_to_sign, tuya_sign`)

2. DELETE the entire `class State:` definition.

3. DELETE `_tuya_from_env`, `cmd_water`, and `cmd_status` functions.

4. In `cmd_plan`, remove all `State`/pending usage. Replace `cmd_plan` with:
```python
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
```
(The MCP clamps on execution; the plan is now a pure proposal. `watered_today=0.0` is safe — `water_zone` re-applies the authoritative budget.)

5. In `main()`, remove the `--state` argument from `_defaults`, remove the `water` and `status` subparsers, and remove `water`/`status` from the dispatch dict. The dispatch becomes:
```python
        return {"sensors": cmd_sensors, "weather": cmd_weather,
                "plan": cmd_plan}[args.cmd](args, cfg)
```
6. In `main()`'s except clause, drop `TuyaError` from the caught tuple (it's no longer imported):
```python
    except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError, TypeError) as e:
```

- [ ] **Step 4: Delete the dead state tests**

```bash
git rm irrigation/tests/test_state.py
```

- [ ] **Step 5: Run the full suite**

Run: `cd irrigation && python3 -m pytest -q`
Expected: PASS. (`test_cli.py` key-free tests pass; `test_plan.py` still passes — `plan_zone` and its internal `clamp_minutes` reference resolve via the `from garden_core import clamp_minutes` in `garden.py`.)

- [ ] **Step 6: Commit**

```bash
cd ~/Projects/gardencontroller
git add irrigation/garden.py irrigation/tests/
git commit -m "refactor!: strip all Tuya code from garden.py CLI (agent is now key-free)"
```

---

### Task 7: Sidecar Dockerfile

**Files:**
- Create: `irrigation/mcp/Dockerfile`
- Create: `irrigation/mcp/.dockerignore`

- [ ] **Step 1: Write the Dockerfile**

Create `irrigation/mcp/Dockerfile` (build context is the `irrigation/` dir so it can copy `garden_core.py`):

```dockerfile
# Build context: the irrigation/ directory.
#   docker build -f mcp/Dockerfile -t garden-tuya-mcp .
FROM python:3.11-slim

WORKDIR /app

# Non-root for parity with the cluster securityContext.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin appuser

COPY mcp/requirements.txt /app/mcp/requirements.txt
RUN pip install --no-cache-dir -r /app/mcp/requirements.txt

COPY garden_core.py /app/garden_core.py
COPY mcp/gateway.py /app/mcp/gateway.py
COPY mcp/server.py /app/mcp/server.py

# Budget-state dir (a PVC is mounted here in the cluster).
RUN mkdir -p /var/lib/garden-mcp && chown appuser:appuser /var/lib/garden-mcp
USER appuser

ENV GARDEN_MCP_CONFIG=/etc/garden-mcp/config.json \
    GARDEN_MCP_STATE=/var/lib/garden-mcp \
    GARDEN_MCP_HOST=127.0.0.1 \
    GARDEN_MCP_PORT=8765 \
    PYTHONPATH=/app:/app/mcp

EXPOSE 8765
CMD ["python", "/app/mcp/server.py"]
```

Create `irrigation/mcp/.dockerignore`:

```
tests/
__pycache__/
*.pyc
config.json
```

- [ ] **Step 2: Build the image to verify it compiles**

Run:
```bash
cd ~/Projects/gardencontroller/irrigation
docker build -f mcp/Dockerfile -t garden-tuya-mcp:dev .
```
Expected: build succeeds (final image tagged `garden-tuya-mcp:dev`).

- [ ] **Step 3: Run the container briefly to confirm it boots**

Run:
```bash
docker run --rm -e TUYA_CLIENT_ID=x -e TUYA_CLIENT_SECRET=y \
  -e GARDEN_MCP_HOST=0.0.0.0 \
  -v "$PWD/mcp/config.example.json:/etc/garden-mcp/config.json:ro" \
  --name gmcp-test -d -p 18765:8765 garden-tuya-mcp:dev
sleep 3 && docker logs gmcp-test && docker rm -f gmcp-test
```
Expected: logs show the server started with no traceback.

- [ ] **Step 4: Commit**

```bash
cd ~/Projects/gardencontroller
git add irrigation/mcp/Dockerfile irrigation/mcp/.dockerignore
git commit -m "build: Dockerfile for garden-tuya-mcp sidecar image"
```

---

### Task 8: CI workflow to build + push the sidecar image to GHCR

Mirror the existing ingest image workflow.

**Files:**
- Create: `.github/workflows/mcp-image.yml`
- Reference (read first): the existing ingest image workflow under `.github/workflows/`.

- [ ] **Step 1: Read the existing ingest workflow to copy registry/login/buildx conventions**

Run: `ls .github/workflows && sed -n '1,80p' .github/workflows/*ingest*.yml 2>/dev/null || sed -n '1,80p' .github/workflows/docker*.yml`
Note the exact `permissions`, `actions/checkout`, `docker/login-action`, `docker/build-push-action`, and image-name conventions used.

- [ ] **Step 2: Write the workflow**

Create `.github/workflows/mcp-image.yml` (adjust `IMAGE_NAME`/registry to match what Step 1 showed if different):

```yaml
name: Build garden-tuya-mcp image

on:
  push:
    branches: [main]
    paths:
      - "irrigation/garden_core.py"
      - "irrigation/mcp/**"
      - ".github/workflows/mcp-image.yml"
  workflow_dispatch:

permissions:
  contents: read
  packages: write

env:
  REGISTRY: ghcr.io
  IMAGE_NAME: ${{ github.repository_owner }}/garden-tuya-mcp

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: docker/setup-qemu-action@v3
      - uses: docker/setup-buildx-action@v3

      - name: Log in to GHCR
        uses: docker/login-action@v3
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Build and push
        uses: docker/build-push-action@v6
        with:
          context: irrigation
          file: irrigation/mcp/Dockerfile
          platforms: linux/amd64,linux/arm64
          push: true
          tags: |
            ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:latest
            ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:${{ github.sha }}
```

- [ ] **Step 3: Validate the workflow YAML**

Run: `python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/mcp-image.yml')); print('yaml ok')"`
Expected: `yaml ok`

- [ ] **Step 4: Commit**

```bash
cd ~/Projects/gardencontroller
git add .github/workflows/mcp-image.yml
git commit -m "ci: build + push garden-tuya-mcp multi-arch image to GHCR"
```

---

### Task 9: bigboy Flux wiring — sidecar, secret move, PVC, MCP client config

Wire the sidecar into the OpenClaw pod, move the Tuya secret to the sidecar only, add the budget PVC, and register the MCP endpoint. **Files live in `~/projects/bigboy/k8s` (separate repo).**

**Files (explore exact paths in Step 1):**
- Modify: OpenClaw Deployment manifest (add sidecar container; remove Tuya env from the agent container).
- Modify/Create: Tuya `SecretProviderClass` mount → sidecar only.
- Create: `PersistentVolumeClaim` for budget state.
- Modify: OpenClaw MCP client config (register `http://127.0.0.1:8765/mcp`).

- [ ] **Step 1: Locate the OpenClaw deployment and current Tuya secret wiring**

Run:
```bash
cd ~/projects/bigboy/k8s
grep -rl "openclaw" --include=*.yaml apps/ | head
grep -rln "TUYA_CLIENT_SECRET\|tuya" --include=*.yaml apps/ infrastructure/ 2>/dev/null
```
Read the OpenClaw Deployment and its SecretProviderClass. Identify: the agent container name, how `TUYA_CLIENT_ID/SECRET` are currently injected (env from CSI-mounted secret), and the MCP client config location (file or env the OpenClaw runtime reads for MCP servers).

- [ ] **Step 2: Add the budget-state PVC**

Create `apps/openclaw/.../garden-mcp-state-pvc.yaml` (match the storageClass used elsewhere in bigboy; 1Gi is ample):

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: garden-mcp-state
  namespace: openclaw   # match the OpenClaw namespace found in Step 1
spec:
  accessModes: ["ReadWriteOnce"]
  resources:
    requests:
      storage: 1Gi
  # storageClassName: <match the cluster default / what other PVCs use>
```
Add it to the relevant `kustomization.yaml` resources list.

- [ ] **Step 3: Add the sidecar container and move the Tuya secret to it**

In the OpenClaw Deployment `spec.template.spec.containers`, add a second container (use the GHCR image from Task 8). Move the Tuya CSI secret env block here and **remove it from the agent container**. Add the config (from a ConfigMap or the existing `GARDEN_CONFIG_JSON` Key Vault secret) and the state PVC mount:

```yaml
        - name: garden-tuya-mcp
          image: ghcr.io/<owner>/garden-tuya-mcp:latest   # pin to a sha in prod
          env:
            - name: TUYA_CLIENT_ID
              valueFrom:
                secretKeyRef: { name: <tuya-secret>, key: TUYA_CLIENT_ID }
            - name: TUYA_CLIENT_SECRET
              valueFrom:
                secretKeyRef: { name: <tuya-secret>, key: TUYA_CLIENT_SECRET }
            - name: GARDEN_MCP_CONFIG
              value: /etc/garden-mcp/config.json
            - name: GARDEN_MCP_STATE
              value: /var/lib/garden-mcp
            - name: GARDEN_MCP_HOST
              value: "127.0.0.1"
            - name: GARDEN_MCP_PORT
              value: "8765"
          volumeMounts:
            - name: garden-mcp-config
              mountPath: /etc/garden-mcp
              readOnly: true
            - name: garden-mcp-state
              mountPath: /var/lib/garden-mcp
          securityContext:
            runAsNonRoot: true
            runAsUser: 10001
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities: { drop: ["ALL"] }
```
Add the two volumes to `spec.template.spec.volumes`:
```yaml
        - name: garden-mcp-config
          secret: { secretName: <garden-mcp-config-secret> }   # contains config.json (region/tz/zones)
        - name: garden-mcp-state
          persistentVolumeClaim: { claimName: garden-mcp-state }
```
**Critically: delete the `TUYA_CLIENT_ID`/`TUYA_CLIENT_SECRET` env entries from the OpenClaw agent container** so the key no longer exists in the agent's environment.

- [ ] **Step 4: Register the MCP endpoint in OpenClaw's MCP client config**

Per what Step 1 revealed about how OpenClaw discovers MCP servers, add a `garden-tuya` server entry pointing at `http://127.0.0.1:8765/mcp` (streamable-HTTP transport). If OpenClaw uses a JSON config, add:
```json
{ "mcpServers": { "garden-tuya": { "url": "http://127.0.0.1:8765/mcp", "transport": "streamable-http" } } }
```

- [ ] **Step 5: Validate the kustomization builds**

Run:
```bash
cd ~/projects/bigboy/k8s
kustomize build apps/openclaw/overlays/prod >/dev/null && echo "kustomize ok"
```
Expected: `kustomize ok` (adjust the overlay path to the real one). Fix any reference errors.

- [ ] **Step 6: Commit (in the bigboy repo)**

```bash
cd ~/projects/bigboy
git add k8s/apps/openclaw
git commit -m "feat(openclaw): add garden-tuya-mcp sidecar; move Tuya key off the agent container"
```
(Push per the normal bigboy Flux workflow — Flux reconciles on merge to main.)

---

### Task 10: Update the OpenClaw irrigation SKILL.md

Replace the `garden water`/`garden status` instructions with the MCP tools and document the new enforcement model.

**Files:**
- Modify: `irrigation/SKILL.md`

- [ ] **Step 1: Update the command reference and safety sections**

In `irrigation/SKILL.md`:
- In **Command Reference**, replace the `garden water ...` and `garden status ...` lines with:
  ```
  # Open a valve (enforced: clamped to per-run + per-zone daily caps by the MCP):
  MCP tool: water_zone(zone="zone1", minutes=8)   -> {requested, granted, ok, reason}

  # Read live valve state / countdown:
  MCP tool: get_zone_status(zone="zone1")

  # See approved zones + remaining daily budget before proposing:
  MCP tool: list_zones()

  # Emergency close:
  MCP tool: stop_zone(zone="zone1")
  ```
- Remove the `--force` and `--dry-run` references and the entire "pending plan single-use" enforcement paragraph (that gate moved out; the MCP daily budget is now the hard limit).
- In **Safety Rules**, replace rule #2 with: "Never exceed hard caps. `water_zone` clamps the request through `max_per_run` then the per-zone daily budget **inside the MCP** — the agent cannot exceed it. If `granted < requested`, report the clamp."
- In **Environment Variables Required**, remove `TUYA_CLIENT_ID/SECRET/REGION` from the agent's required vars (note they now live only in the sidecar) and keep `GARDEN_CONFIG`/`prometheus_url` for the read-only planning commands.
- Update Step 4 of the workflow: "Execute watering (on approval)" now calls the `water_zone` MCP tool instead of `garden water`, and reports `granted` vs `requested`.

- [ ] **Step 2: Verify no stale references remain**

Run: `grep -nE "garden water|garden status|--force|--dry-run|TUYA_CLIENT_SECRET" irrigation/SKILL.md`
Expected: no matches (or only inside a clearly-marked "old/deprecated" note if you keep one).

- [ ] **Step 3: Commit**

```bash
cd ~/Projects/gardencontroller
git add irrigation/SKILL.md
git commit -m "docs: SKILL.md uses MCP water_zone/stop_zone; document MCP-enforced caps"
```

---

## Self-Review

**Spec coverage:**
- Sidecar + localhost HTTP + secret-only-on-sidecar → Tasks 7, 9. ✓
- Tool surface (list_zones/get_zone_status/water_zone/stop_zone, requested-vs-granted) → Tasks 3, 4, 5. ✓
- Per-run + per-zone local-midnight daily budget on a sidecar PVC → Tasks 2 (tz reset), 3 (accumulation), 9 (PVC). ✓
- Approved-zone allowlist → Task 3 (`unknown zone`). ✓
- Auto-off verification / failsafe preserved → Task 1 (`do_water`), Task 4 (failsafe test). ✓
- Audit-to-stdout → Task 3 (`_log`), Task 5 (`audit` prints JSON, flush). ✓
- garden.py becomes key-free; water/status/pending/force removed → Task 6. ✓
- Shared core module → Task 1. ✓
- New GHCR image + CI → Tasks 7, 8. ✓
- SKILL.md updated → Task 10. ✓
- Testing (cross-call budget, tz reset, allowlist, failsafe, requested-vs-granted, key-free CLI) → Tasks 2, 3, 4, 6. ✓

**Placeholder scan:** Concrete config values (`REPLACE_DEVICE_ID_*`) are intentional config templates, not plan placeholders. Bigboy paths in Task 9 are discovered in Step 1 then filled with the exact blocks given — no "TBD".

**Type consistency:** `GatewayService.__init__(zones, tuya, budget, now_fn, audit, confirm_attempts, confirm_sleep_s)` matches all `_svc(...)` test constructions. Result dicts use the same keys throughout (`requested`, `granted`, `ok`, `reason`, `switch_confirmed`). `BudgetState(base_dir, timezone=...)`/`watered_today(zone, now)`/`add_watered(zone, minutes, now)` consistent across Tasks 2, 3, 5. `do_water(..., confirm_attempts, confirm_sleep_s)` signature matches the call in Task 3.
