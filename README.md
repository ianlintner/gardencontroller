# gardencontroller

Garden environmental telemetry: an **Arduino UNO R4 WiFi** reads soil moisture,
air temp/humidity, and rain, then pushes readings to the existing
**Prometheus + Grafana** stack on the AKS cluster. MVP is one board; the design
scales to ~3 boards and leaves a seam for future actuation/triggers.

## Data path

```
UNO R4 WiFi ──OAuth2 client-credentials──▶ roauth2.cat-herding.net  (JWT)
           ──HTTPS POST JSON + Bearer───▶ garden.cat-herding.net/ingest
                                          │ istio validates JWT at the edge
                                          ▼
                              garden-ingest (FastAPI) ──▶ Pushgateway
                                          Prometheus scrapes (30s) ──▶ Grafana "Garden Overview"
```

See the full design in `~/.claude/plans/we-have-an-arduino-squishy-finch.md`.

## Layout

- `firmware/garden-node/` — Arduino sketch. Start at [its README](firmware/garden-node/README.md).
- `ingest/` — the FastAPI ingest service. See [ingest/README.md](ingest/README.md).
- `docs/calibration.md` — bench calibration procedure (Phase 0).

## Kubernetes manifests

Live in the Flux GitOps repo `~/projects/bigboy/k8s` (so Flux reconciles them):
- `apps/garden-ingest/` — service + istio VirtualService + JWT RequestAuthentication/AuthorizationPolicy.
- `infrastructure/observability/pushgateway-*.yaml` — Pushgateway (namespace `default`).
- `infrastructure/observability/assets/grafana/dashboards/garden-overview.json` — dashboard.
- `infrastructure/observability/assets/prometheus/rules/garden_alerts.yaml` — offline + dry-soil alerts.

## Before first deploy (prerequisites)

1. **Container image**: build `ingest/` and push to a registry Flux can pull
   (set `image:` in `apps/garden-ingest/overlays/prod/kustomization.yaml`).
2. **DNS + gateway TLS**: already handled — `cat-herding-gateway` serves
   `*.cat-herding.net` on 443 with the `cat-herding-wildcard-tls` cert (and
   wildcard DNS), so `garden.cat-herding.net` works as soon as the VirtualService
   (already authored) is applied. No gateway change needed.
3. **OAuth2 client**: register a client in roauth2 (client-credentials, audience
   `garden-ingest`) per board.
4. **Push** the bigboy repo — Flux applies automatically. (Not done here; this
   repo only authored the files.)

## Secrets & flashing the board

`.env` (gitignored) is the single source of truth. `arduino_secrets.h` is
generated from it and is also gitignored — only the `*.example` files are committed.

```bash
cp .env.example .env            # fill in WiFi + OAuth client creds
./scripts/gen-arduino-secrets.sh  # → firmware/garden-node/arduino_secrets.h
./scripts/flash.sh              # gen secrets + compile + upload (auto-detects port)
```

For Phase 2 uploads, set `ENABLE_UPLOAD 1` in `firmware/garden-node/config.h`
before flashing.

## Verify end-to-end

```bash
./scripts/verify.sh             # token → POST /ingest, using .env (no board needed)
```

Then check the **Garden Overview** dashboard in Grafana. Full path:
token → `/ingest` → Pushgateway `/metrics` → Prometheus → Grafana → board flash.
