# OpenClaw Weather-Aware Irrigation — Design

**Date:** 2026-06-02
**Status:** Approved design, pending implementation plan

## Context

The garden telemetry MVP is live: an Arduino UNO R4 WiFi pushes soil/temp/rain
readings through `garden-ingest` → Pushgateway → Prometheus → Grafana. We now
want to **close the loop and water the garden automatically**, driven by that
sensor data plus weather.

Two **Smart Life (Tuya) water timers** control the valves. The existing
**OpenClaw** AI agent (in the `bot` namespace, `openclaw.cat-herding.net`) should
be the brain: 3×/day it evaluates each zone, proposes a watering plan in Discord,
and — after human approval — opens the valve for a computed duration, bounded by
hard safety caps.

Hard constraint: the AKS cluster is in Azure and the timers are on home WiFi, so
local-LAN control is impossible — control goes via the **Tuya Cloud API**.

## Decisions (from brainstorming)

| Decision | Choice |
|---|---|
| Device control | **Tuya Cloud API** (cluster → Tuya cloud → timers) |
| Decision engine | **Hybrid** — deterministic formula + hard caps; LLM explains & may nudge *within* caps |
| Autonomy | **Approve-first** — no valve opens without a human ✅ |
| Approval channel | **Discord** (OpenClaw's native Discord integration) |
| Weather source | **Open-Meteo** (free, no key; precip + FAO ET₀) |
| Zones | **Two independent zones**, each its own Tuya timer + soil probe (1 now, 2nd later) |
| Cadence | **3×/day** — ~05:30 (main), ~12:30 (heat-wave burst only), ~18:00 (top-up) |
| Implementation | **Standardized `garden` CLI toolkit** driven by the existing OpenClaw agent — **no new service** |
| Trigger | **OpenClaw's built-in cron/scheduler** (not a k8s CronJob) |

## Architecture

Extend the **existing OpenClaw** instance — no new Deployment/Service/agent.

```
OpenClaw cron (3×/day)
      │ starts an "irrigation check" agent run (per the skill doc)
      ▼
OpenClaw agent (LLM)  ── shells out to ──▶  `garden` CLI (self-contained Python, stdlib-only)
      │                                         ├─ sensors  → Prometheus (in-cluster)
      │                                         ├─ weather  → Open-Meteo
      │                                         ├─ plan     → deterministic formula + caps  (safety-critical)
      │                                         └─ water    → Tuya Cloud API (+ re-clamp, confirm-closed)
      │ posts proposal, saves pending plan to PVC
      ▼
Discord  ──(you reply ✅)──▶  OpenClaw agent reads pending plan ─▶ `garden water` ─▶ report
```

- **`garden` CLI**: one self-contained Python file (stdlib only — `urllib`, `hmac`,
  `hashlib` for Tuya request signing; no pip deps, no sidecar). Source of truth is
  this repo (`irrigation/garden.py`). OpenClaw fetches it into `~/.openclaw/.local/bin/garden`
  at startup, exactly as it already fetches `gh`/`opencode` (curl from the public
  repo raw URL; configmap mount is the fallback).
- **Agent = orchestrator + voice.** It runs `sensors`/`weather`/`plan`, writes the
  human-readable Discord proposal, and on approval runs `water`. The
  **safety-critical logic (formula, caps, valve command, confirm-closed) lives in
  the CLI**, not in prompts.
- **State** (pending plan, per-day watered totals, idempotency keys) is JSON on
  OpenClaw's existing PVC (`/home/node/.openclaw/garden/`).
- **Skill doc** (markdown) registered with OpenClaw defines the 3×/day workflow and
  the OpenClaw cron entries so the agent behaves consistently.

## Components — `garden` CLI

All subcommands emit JSON to stdout.

- `garden sensors --zone <z>` — Prometheus query for the zone's soil %, temp, and
  `garden_push_timestamp_seconds` (staleness). Returns `{soil_pct, temp_c, stale}`.
- `garden weather` — Open-Meteo for the configured lat/long: next-12h precip mm +
  probability, today's ET₀, forecast high. Returns those fields.
- `garden plan` — pure function over sensors+weather → `{zone, minutes, reason}[]`.
  Safety-critical; unit-tested. (See Formula.)
- `garden water --zone <z> --minutes <m> [--dry-run]` — re-clamps to caps, sends the
  Tuya on command (timed), waits, sends off, **reads device state back to confirm
  closed** (retry + alert if not). `--dry-run` logs and opens nothing.

**Config & secrets** (added to `openclaw-secrets` via Azure Key Vault CSI):
`TUYA_CLIENT_ID`, `TUYA_CLIENT_SECRET`, `TUYA_REGION` (e.g. `us`), per-zone
`TUYA_DEVICE_ID`, `GARDEN_LAT`, `GARDEN_LON`. Prometheus URL is in-cluster
(`http://prometheus.default.svc:9090`). Zone→device→probe mapping in a small config
file/env (zone1 → device A → `device_id=garden-node-1,probe=bed1`; zone2 later).

## Decision formula + caps (`garden plan`)

Deterministic, per zone, per run. **Constants are the gardener's to tune** — this
is the key contribution point; `plan()` ships scaffolded with these defaults and
clear TODOs.

1. **Rain skip** — next-12h ≥ 2 mm or ≥ 60% prob → `0 min`.
2. **Soil gate** — soil % ≥ target → `0 min`.
3. **Deficit → base** — `deficit = target% − soil%`; `base = deficit × min_per_pct`
   (per-zone flow calibration).
4. **ET₀ scaling** — `× (ET₀_today / ET₀_baseline)`.
5. **Midday = heat-wave only** — the 12:30 run waters only if `temp > heat_threshold`
   and soil below a lower stress line; a short cooling burst with a small cap.
   Morning is the main event; evening is a top-up.
6. **Caps (final guard)** — clamp to `[min_run, max_per_run]`; enforce `max_per_day`
   across the 3 runs (PVC state). `garden water` re-clamps independently.

**Starting defaults (tune per garden):** target **40%**, **min_per_pct** ~0.5 min/%,
**ET₀_baseline** 4 mm, caps **15 min/run, 30 min/day per zone**, **min_run** 1 min,
**heat_threshold 32 °C**, midday cap **5 min**.

## Data flow (one cycle)

1. OpenClaw cron fires → agent starts the irrigation-check run.
2. Agent runs `sensors` + `weather` for each zone, then `plan`.
3. For zones with `minutes > 0`, agent posts a Discord proposal (zone, minutes,
   plain-English reason) and saves the pending plan to PVC with a **2h TTL**.
4. You reply ✅ (per zone or all). Agent verifies the plan is un-expired, runs
   `garden water --zone <z> --minutes <m>`.
5. `water` re-clamps, opens the valve, waits, closes, **confirms closed**, updates
   the per-day total, and the agent reports the result in Discord.

## Safety & error handling

- **Caps enforced twice** (plan + water); valve logic never trusts the LLM number.
- **Approve-first** with a **2h pending-plan TTL** (no stale approvals).
- **Confirm-closed** read-back after watering; retry + loud Discord alert if a valve
  won't confirm closed (stuck-open is the worst case).
- **max_per_day watchdog** in PVC state; further runs auto-skip with a note.
- **Fail-safe skip** — if Prometheus is unreachable, the soil reading is stale, or
  weather/Tuya errors → **do not water**, notify. Never guess-and-water.
- **Idempotency** — each scheduled run carries a key; re-runs don't double-water.

## Testing

- Table-driven unit tests for `plan()` (rain-skip, soil-gate, deficit, ET scaling,
  heat-wave branch, cap clamping) — pure function, no I/O.
- Mock Tuya / Open-Meteo / Prometheus; test `water` cap re-clamp and confirm-closed
  retry.
- `--dry-run` for a safe end-to-end Discord rehearsal before the first real water.

## Repos touched

- **gardencontroller**: `irrigation/garden.py` (CLI), tests, the skill doc, README.
- **bigboy**: OpenClaw startup snippet to fetch `garden` into `.local/bin`; add Tuya/
  location secrets to the `openclaw-secrets` SecretProviderClass; register the
  OpenClaw cron + skill. (GitOps — pushed for Flux to apply.)

## Prerequisites (gardener / setup)

- Tuya IoT Platform developer project linked to the Smart Life account; obtain
  `client_id`/`secret`, region, and each timer's `device_id`; store in Key Vault.
- Confirm each timer's Tuya **DP (datapoint)** for switch on/off (and any countdown
  DP) — varies by model.
- Garden lat/long.

## Out of scope (for now)

- More than two zones (design scales; add zone config + probe + device).
- Fully autonomous (no-approval) mode — could be a later flag once trusted.
- Flow-rate auto-calibration; per-plant schedules.
