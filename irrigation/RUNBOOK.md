# OpenClaw Irrigation — Runtime Setup Runbook

The `garden` CLI install + secrets are GitOps (in the bigboy OpenClaw deployment +
Key Vault). The **cron jobs and Discord routing are OpenClaw gateway state** (live
on the PVC), so they're not in GitOps — recreate them with the commands below if
the gateway state is ever lost.

## Key Vault secrets (vault `openclaw-kv-301919`)
- `TUYA-CLIENT-ID`, `TUYA-CLIENT-SECRET` — Tuya IoT Platform project (data center: **US**)
- `TUYA-REGION` = `us`
- `GARDEN-CONFIG-JSON` — full `garden.config.json` (lat/lon, zones, device IDs, DP codes)

Mapped into the `openclaw-secrets` k8s secret by the SecretProviderClass; surfaced
to the pod as `TUYA_CLIENT_ID/SECRET/REGION` + `GARDEN_CONFIG_JSON`. The startup
writes `GARDEN_CONFIG_JSON` to `~/.openclaw/garden/garden.config.json`.

## Devices (Tuya, region us)
- zone1 → `eb56a2bc1193f3b8dd0r5y` (RF Water Timer), DPs `switch_1` / `countdown_1`
- zone2 → `eb094bad9780be6323u9gs` (RF Water Timer 2) — mapped to `garden-node-2` (inactive until a 2nd probe)
- Gateway `eb4633b346fc9ef275tton` (Sub-GHz bridge; not controlled directly)

## Discord
- Proposals delivered to channel **`1511685182040702996`** (server `1501373787172769873`).

## Cron jobs (recreate on the OpenClaw gateway)
Run inside the openclaw container (`kubectl -n bot exec deploy/openclaw -c openclaw -- ...`).
All use `--tz America/Chicago` (DST-safe), exec tools, and deliver to the channel above.
The message tells the agent to run `garden plan --phase <phase>`, post per-zone proposals,
and only run `garden water` after a human approves (the CLI also enforces the saved plan).

```sh
G=/home/node/.openclaw/.local/bin/garden
CHAN=1511685182040702996
MSG='Garden irrigation check (PHASE). Run: '"$G"' plan --phase PHASE then read its JSON. For each zone with minutes>0, post: "<zone>: water <minutes> min — <reason>. To approve, reply: approve <zone>". If all 0/skip, say no watering needed and why. Do NOT run garden water in this turn; only when a human approves a zone run: '"$G"' water --zone <zone> --minutes <minutes> and report. Never exceed caps; on error, report and do nothing.'
# substitute PHASE = morning|midday|evening per job
node dist/index.js cron add --cron "30 5 * * *"  --tz America/Chicago --name garden-morning --channel discord --to "$CHAN" --announce --best-effort-deliver --tools exec,read,write --timeout-seconds 180 --token proxy-authenticated --message "<morning MSG>"
node dist/index.js cron add --cron "30 12 * * *" --tz America/Chicago --name garden-midday  --channel discord --to "$CHAN" --announce --best-effort-deliver --tools exec,read,write --timeout-seconds 180 --token proxy-authenticated --message "<midday MSG>"
node dist/index.js cron add --cron "0 18 * * *"  --tz America/Chicago --name garden-evening --channel discord --to "$CHAN" --announce --best-effort-deliver --tools exec,read,write --timeout-seconds 180 --token proxy-authenticated --message "<evening MSG>"
```

Manage: `cron list` / `cron get <id>` / `cron disable <id>` / `cron rm <id>` / `cron run <id>` (debug) — all need `--token proxy-authenticated`.

## Manual ops (in-pod, via absolute path)
```sh
G=/home/node/.openclaw/.local/bin/garden
$G weather
$G sensors --zone zone1
$G status  --zone zone1            # live Tuya valve state
$G plan    --phase morning
$G water   --zone zone1 --minutes 1 --dry-run     # rehearsal, sends nothing
$G water   --zone zone1 --minutes 1 --force       # manual water (bypasses pending gate)
```

## Exec policy
`tools.exec` effective = `security=full, ask=off` — the agent runs `garden` without
per-command approval prompts. Watering safety is enforced by the CLI (caps, pending
plan, countdown auto-off), not by exec gating.
