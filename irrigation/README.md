# garden — Weather-Aware Irrigation CLI

A self-contained, stdlib-only Python CLI for weather-aware irrigation of Tuya smart-timer zones.
Used by the OpenClaw agent for automated 3×/day watering decisions with human approval via Discord.

---

## Quick Start (local dev)

```bash
cd irrigation
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt   # pytest only; no runtime deps
.venv/bin/python -m pytest tests/ -q            # all tests should pass
```

The CLI requires no third-party packages at runtime — only Python 3.8+ stdlib.

---

## Config

Copy and fill in the example config:

```bash
cp garden.config.example.json garden.config.json
$EDITOR garden.config.json
```

Key fields:

| Field | Description |
|---|---|
| `lat`, `lon` | Garden location (decimal degrees) for Open-Meteo weather |
| `prometheus_url` | Prometheus base URL for soil/temp sensor scraping |
| `et0_baseline_mm` | Reference ET0 (mm/day) — scales watering up/down with actual ET0 |
| `rain_skip_mm` | Skip watering if 12h forecast precipitation exceeds this (mm) |
| `rain_skip_prob_pct` | Skip watering if max 12h precipitation probability exceeds this (%) |
| `heat_threshold_c` | Midday run fires only when the daily high exceeds this temperature (°C) |
| `midday_cap_min` | Max minutes for the midday heat-wave burst |
| `zones[].name` | Zone identifier used in CLI `--zone` flag |
| `zones[].tuya_device_id` | Tuya Cloud device ID for the smart timer |
| `zones[].prom_device_id` | `device_id` label value in Prometheus for the garden node |
| `zones[].probe` | `probe` label value in Prometheus for the soil sensor |
| `zones[].target_pct` | Target soil moisture % — watering stops when soil reaches this |
| `zones[].min_per_pct` | Minutes of watering per % of moisture deficit |
| `zones[].max_per_run` | Hard cap: maximum minutes per single watering run |
| `zones[].max_per_day` | Hard cap: maximum cumulative minutes per day across all runs |
| `zones[].min_run` | Minimum run duration — computed minutes below this are rounded to 0 |
| `zones[].switch_dp` | Tuya DP code for the switch (default: `switch`) |
| `zones[].countdown_dp` | Tuya DP code for the countdown timer (default: `countdown_1`) |

### DP Codes

DP (Data Point) codes vary by Tuya timer model. To find the correct codes for your device:
1. Log into [iot.tuya.com](https://iot.tuya.com), go to Cloud > Development > your project.
2. Open "Device Debugging" for your timer device.
3. Click "Instruction Set" — find the switch and countdown entries.
4. Common codes: `switch` or `switch_1` for the relay; `countdown_1` for seconds countdown.
5. Set `switch_dp` and `countdown_dp` in your `garden.config.json` per zone.

To verify without opening a valve:

```bash
garden water --zone zone1 --minutes 5 --dry-run
```

The `would_send` field shows exactly which DP codes and values would be sent.

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `TUYA_CLIENT_ID` | For `water`/`status` | Tuya Cloud project client ID |
| `TUYA_CLIENT_SECRET` | For `water`/`status` | Tuya Cloud project client secret |
| `TUYA_REGION` | No | Tuya region: `us` (default), `eu`, `cn`, `in` |
| `GARDEN_CONFIG` | No | Path to config JSON (default: `garden.config.json` in CWD) |
| `GARDEN_STATE` | No | State directory path (default: `~/.openclaw/garden`) |

Read-only subcommands (`sensors`, `weather`, `plan`) do not require Tuya credentials.

### Getting Tuya credentials

1. Register at [iot.tuya.com](https://iot.tuya.com) and create a Cloud project.
2. Link your Smart Life account devices to the project.
3. Copy the project's **Access ID** → `TUYA_CLIENT_ID` and **Access Secret** → `TUYA_CLIENT_SECRET`.
4. Set `TUYA_REGION` to match the data center region of your Smart Life account (`us` for Americas, `eu` for Europe, `cn` for China, `in` for India).

---

## Subcommands

```bash
# Read soil moisture + temperature from Prometheus (no Tuya):
garden sensors --zone zone1

# Fetch weather forecast from Open-Meteo (no Tuya):
garden weather

# Compute a watering plan and save as pending (no Tuya):
garden plan --phase morning          # or midday / evening

# Water a zone (requires Tuya env vars; touches hardware):
garden water --zone zone1 --minutes 8

# Dry-run: print what would be sent, send nothing:
garden water --zone zone1 --minutes 8 --dry-run

# Read current Tuya device status (valve on/off, countdown remaining):
garden status --zone zone1
```

Global flags (can appear before or after the subcommand):
- `--config PATH` — path to config JSON
- `--state DIR` — path to state directory

---

## State

The state directory (default `~/.openclaw/garden`) contains a single `state.json` file with:
- `watered` — per-zone daily totals (resets at UTC midnight)
- `pending` — the last generated plan with a 2-hour expiry timestamp
- `runs` — idempotency log of completed run keys

On OpenClaw the state directory is on a PVC so it survives pod restarts.

---

## Testing

```bash
cd irrigation
.venv/bin/python -m pytest tests/ -q
```

All tests use only stdlib + pytest. No Tuya or Prometheus connection required — HTTP clients are injected via parameters and monkeypatched in tests.

To add a zone to tests, copy the pattern from `tests/test_plan.py`: construct a `ZONE` dict with all required fields and pass it to `plan_zone()` or `do_water()` with a `FakeTuya`.

---

## Architecture

`irrigation/garden.py` is the single source of truth. It is:
- **Self-contained** — one file, zero runtime dependencies beyond Python stdlib.
- **Pure functions first** — `clamp_minutes`, `plan_zone`, `tuya_string_to_sign`, `tuya_sign`, `read_sensors`, `read_weather` are all testable without I/O.
- **Fail-safe** — stale sensors, rain forecasts, and daily budget exhaustion all produce `minutes=0`; the valve is never opened on ambiguous data.
- **Countdown-DP based** — the Tuya timer's hardware countdown auto-closes the valve; the process does not need to stay alive or send a second "off" command for normal operation.

On the OpenClaw pod, `garden.py` is fetched from this repo's `main` branch at container startup so it is always up to date without a pod redeploy.
