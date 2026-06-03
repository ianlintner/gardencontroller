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

This reads live soil/temperature sensors from Prometheus and current weather from Open-Meteo, runs the deterministic formula for each zone, and outputs a JSON array to stdout. The plan is also saved as a pending proposal in the state directory with a 2-hour TTL.

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

Wait for a ✅ reply (or explicit approval message) in the Discord thread. The approval window is 2 hours (matching the pending plan TTL). If no approval arrives within the window, the plan expires and the agent stops without watering.

**Never water without explicit human approval.**

### Step 4: Execute watering (on approval)

For each approved zone with `minutes > 0`:

```
garden water --zone <zone> --minutes <N>
```

Example:
```
garden water --zone zone1 --minutes 8
garden water --zone zone2 --minutes 5
```

This re-clamps `N` through the zone's hard caps (max per run, daily budget) before sending any command, so it is safe to pass the plan's minutes directly. The valve opens via a countdown DP (hardware auto-off) and the result is confirmed.

Report the result back to Discord:
- On success (`ok: true`): "zone1: watering started (~8 min, auto-off armed)."
- On failure (`ok: false`): "zone1: ERROR — {note}. Skipping." (do not retry; report and move on)

### Step 5: Update Discord with final status

Post a completion summary: zones watered, minutes each, any errors or skips.

---

## Command Reference

```bash
# Check current sensor readings for a zone (read-only):
garden sensors --zone zone1

# Check weather forecast (read-only):
garden weather

# Generate a watering plan for a phase (saves pending plan):
garden plan --phase <morning|midday|evening>

# Execute watering for a zone (requires Tuya env vars, touches hardware):
garden water --zone <zone> --minutes <N>

# Dry-run: shows what would be sent, sends nothing:
garden water --zone zone1 --minutes 5 --dry-run

# Read current Tuya device status (valve state, countdown):
garden status --zone zone1
```

---

## Safety Rules (non-negotiable)

1. **Never water without explicit human approval.** The plan subcommand is always safe; the water subcommand requires a ✅.
2. **Never exceed hard caps.** `garden water` re-clamps through `max_per_run` and the daily budget (`max_per_day`) automatically. Do not try to work around this.
3. **On any error, skip and report.** If `garden water` exits non-zero or returns `"ok": false`, report the error to Discord and move on to the next zone. Do not retry.
4. **Midday run is heat-wave-only.** If all zones return `minutes == 0` at midday because it is not hot enough, that is correct behavior — report it and stop.
5. **Stale sensors abort watering.** If the sensor reading is flagged `"stale": true`, `plan_zone` returns 0 minutes automatically. Do not override this.
6. **The pending plan has a 2-hour TTL.** If approval does not arrive within 2 hours of the plan being generated, the plan is expired. Do not execute an expired plan.

---

## Dry-Run Rehearsal

Before the first live run in a new environment, perform a dry-run rehearsal to verify Tuya connectivity and DP codes without opening any valves:

```bash
# Verify the plan formula produces sensible output:
garden plan --phase morning

# Verify Tuya signing and DP codes (sends nothing, prints would_send):
garden water --zone zone1 --minutes 5 --dry-run
garden water --zone zone2 --minutes 5 --dry-run
```

Expected dry-run output:
```json
{"zone": "zone1", "minutes": 5, "ok": true, "dry_run": true,
 "would_send": [{"code": "switch", "value": true}, {"code": "countdown_1", "value": 300}]}
```

Confirm the `code` values match the DP codes visible in the Tuya IoT "Device Debugging" panel. If they differ, update `switch_dp` and `countdown_dp` in `garden.config.json`.

---

## Environment Variables Required

| Variable | Description |
|---|---|
| `TUYA_CLIENT_ID` | Tuya Cloud project client ID |
| `TUYA_CLIENT_SECRET` | Tuya Cloud project client secret |
| `TUYA_REGION` | Region: `us`, `eu`, `cn`, or `in` (default: `us`) |
| `GARDEN_CONFIG` | Path to `garden.config.json` (default: `garden.config.json` in CWD) |
| `GARDEN_STATE` | Path to state directory (default: `~/.openclaw/garden`) |

`garden sensors`, `garden weather`, and `garden plan` do not require Tuya env vars.
`garden water` and `garden status` require all three Tuya vars.

---

## Config File Location

The config is loaded from `$GARDEN_CONFIG` (or `garden.config.json` in the working directory). On the OpenClaw pod it is written from the `GARDEN_CONFIG_JSON` Key Vault secret at startup:

```
/home/node/.openclaw/garden/garden.config.json
```
