# garden-ingest

FastAPI service that accepts JSON sensor readings from garden nodes and pushes
them to Prometheus Pushgateway. Auth (OAuth2 JWT from roauth2) is enforced at the
istio edge, so this service trusts the request and focuses on validation +
label mapping.

## Endpoints

- `POST /ingest` — body is the board's JSON reading (see schema below). Returns `204`.
- `GET /health` — liveness/readiness.

## Reading payload

```json
{
  "device_id": "garden-node-1",
  "location": "raised-bed",
  "readings": {
    "air_temperature_celsius": 21.3,
    "air_humidity_percent": 54,
    "soil": [{ "probe": "bed1", "raw": 640, "percent": 42.0 }],
    "rain_percent": 12.5,
    "rain_detected": false
  },
  "board": { "rssi_dbm": -58, "uptime_seconds": 1234 }
}
```

Each board is pushed under its own Pushgateway group
(`/metrics/job/garden/instance/<device_id>`) so boards never overwrite each other.

## Dev

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest tests/        # mapping/validation tests
.venv/bin/uvicorn app.main:app --reload  # PUSHGATEWAY_URL defaults to in-cluster DNS
```

## Config (env)

| Var | Default |
|---|---|
| `PUSHGATEWAY_URL` | `http://pushgateway.default.svc.cluster.local:9091` |
| `PUSH_JOB` | `garden` |
| `PUSH_TIMEOUT_SECONDS` | `5` |

## Label mapping

`app/mapping.py` is the contribution point for the label schema, range clamps,
and validation policy. Keep label cardinality sane — every distinct
`device_id`/`location`/`probe` combo is a new Prometheus series.
