# OpenClaw Irrigation Skill

This document describes the 3×/day irrigation workflow that the OpenClaw agent follows.
All safety-critical decisions (formula, caps, valve commands) live in `garden.py`, not in prompts.
The agent's job is to run the commands, report results to Discord, and gate watering on explicit human approval.

---

## Workflow Overview

Three times per day — morning (~05:30 local), midday (~12:30 local), evening (~18:00 local) — the agent runs the following sequence:

### Step 1: Generate a watering proposal

```
garden plan --phase <morning|midday|evening>
```

This reads live soil/temperature sensors from Prometheus and current weather from Open-Meteo, runs the deterministic formula for each zone, and outputs a JSON array to stdout. It is a pure proposal — it touches no hardware and stores no state.

Example output:
```json
[
  {"zone": "zone1", "minutes": 8, "reason": "morning: soil 22% vs target 40%, ET0 4.2mm"},
  {"zone": "zone2", "minutes": 0, "reason": "skip: rain forecast (3.1mm / 72%)"}
]
```

### Step 2: Post the proposal to Discord

For each zone where `minutes > 0`, post a message to the irrigation channel:

> Zone zone1: 8 min — morning: soil 22% vs target 40%, ET0 4.2mm. Reply ✅ to approve.

Zones with `minutes == 0` should be reported as skipped with their reason — no approval needed.

If all zones are skipped (all `minutes == 0`), post a summary message and stop. No approval is needed.

### Step 3: Wait for approval

Wait for a ✅ reply (or explicit approval message) in the Discord thread. Pick a reasonable approval window (e.g. ~2 hours); if no approval arrives, stop without watering.

**Never water without explicit human approval.**

### Step 4: Execute watering (on approval)

For each approved zone with `minutes > 0`, call the MCP tool:

```text
water_zone(zone="zone1", minutes=8)
water_zone(zone="zone2", minutes=5)
```

The `garden-tuya` MCP sidecar clamps `minutes` through the zone's hard caps
(`max_per_run`, then the per-zone daily budget) **before** sending any command —
so it is safe to pass the plan's minutes directly. The valve opens via a
countdown DP (hardware auto-off) and the result is confirmed. The agent holds no
Tuya key and cannot bypass these caps.

Report the result back to Discord (the tool returns `{requested, granted, ok, reason}`):
- On success (`ok: true`): "zone1: watering started (~{granted} min, auto-off armed)." If `granted < requested`, note the clamp.
- On failure (`ok: false`): "zone1: ERROR — {reason}. Skipping." (do not retry; report and move on)

### Step 5: Update Discord with final status

Post a completion summary: zones watered, minutes each, any errors or skips.

---

## Command Reference

```bash
# Check current sensor readings for a zone (read-only):
garden sensors --zone zone1

# Check weather forecast (read-only):
garden weather

# Generate a watering plan for a phase (read-only proposal; no valve action):
garden plan --phase <morning|midday|evening>
```

Valve actions are NOT in this CLI. They are **MCP tools** exposed by the
`garden-tuya` sidecar (the only holder of the Tuya key). Call them as MCP tools:

```text
# See approved zones + remaining daily budget before proposing:
list_zones()

# Open a valve. The MCP clamps `minutes` through max_per_run then the per-zone
# daily budget and verifies hardware auto-off. Returns {requested, granted, ok, reason}.
water_zone(zone="zone1", minutes=8)

# Read live valve state / countdown:
get_zone_status(zone="zone1")

# Emergency close:
stop_zone(zone="zone1")
```

---

## Safety Rules (non-negotiable)

1. **Never water without explicit human approval.** `garden plan` is always safe (a proposal). Post each zone with `minutes > 0` to Discord and wait for a ✅ before calling `water_zone`. (Human approval is orchestrated by you; the MCP additionally enforces the hard caps below regardless.)
2. **Never exceed hard caps — and you can't.** `water_zone` clamps the request through `max_per_run` then the per-zone daily budget **inside the MCP sidecar**, which holds the only Tuya key. The agent cannot exceed it or reach Tuya directly. If `granted < requested`, report the clamp. There is no `--force`.
3. **On any error, skip and report.** If `water_zone` returns `"ok": false`, report the `reason` to Discord and move on to the next zone. Do not retry.
4. **Midday run is heat-wave-only.** If all zones return `minutes == 0` at midday because it is not hot enough, that is correct behavior — report it and stop.
5. **Stale sensors abort watering.** If the sensor reading is flagged `"stale": true`, `plan_zone` returns 0 minutes automatically. Do not override this.
6. **The daily budget is the hard limit.** It resets at local midnight (configured timezone) and is tracked by the MCP on its own volume — you cannot read or reset it. `list_zones()` shows each zone's `remaining_today`.

---

## Rehearsal

Before the first live run in a new environment, verify connectivity without
opening valves using the read-only paths:

```text
garden plan --phase morning      # formula proposal (no hardware)
list_zones()                     # MCP reachable; shows caps + remaining budget
get_zone_status(zone="zone1")    # MCP can read the device (confirms key + DP wiring)
```

Confirm the DP codes (`switch_dp`/`countdown_dp`) in the MCP's `config.json` match
the Tuya IoT "Device Debugging" panel. The Tuya key lives only in the sidecar.

---

## Environment Variables

The **agent** (this CLI) needs only the read-only planning vars — **no Tuya
credential**:

| Variable | Description |
|---|---|
| `GARDEN_CONFIG` | Path to `garden.config.json` for planning (zones, targets, `prometheus_url`) |

The **`garden-tuya` MCP sidecar** holds everything secret/stateful (set on the
sidecar container only, never on the agent): `TUYA_CLIENT_ID`,
`TUYA_CLIENT_SECRET`, plus `GARDEN_MCP_CONFIG` (caps + timezone) and
`GARDEN_MCP_STATE` (budget volume).

`garden sensors`, `garden weather`, and `garden plan` are read-only and require no
Tuya credential. Valve actions (`water_zone`/`stop_zone`/`get_zone_status`) are MCP
tools served by the sidecar, which is the only component with the Tuya key.

---

## Config File Location

The config is loaded from `$GARDEN_CONFIG` (or `garden.config.json` in the working directory). On the OpenClaw pod it is written from the `GARDEN_CONFIG_JSON` Key Vault secret at startup:

```
/home/node/.openclaw/garden/garden.config.json
```
