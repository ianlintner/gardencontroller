"""garden-ingest — accepts JSON readings from garden nodes and pushes them to
Prometheus Pushgateway.

Auth is handled at the edge: istio RequestAuthentication validates the OAuth2
JWT from roauth2 before the request ever reaches this service, so we trust the
request here. We push each board under its own Pushgateway group
(job=garden, instance=<device_id>) so multiple boards never overwrite each other.
"""
from __future__ import annotations

import logging
import os
from urllib.parse import quote

import httpx
from fastapi import FastAPI, Request, Response, status

from .mapping import ValidationError, render_exposition, to_samples

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("garden-ingest")

PUSHGATEWAY_URL = os.getenv("PUSHGATEWAY_URL", "http://pushgateway.default.svc.cluster.local:9091")
PUSH_JOB = os.getenv("PUSH_JOB", "garden")
HTTP_TIMEOUT = float(os.getenv("PUSH_TIMEOUT_SECONDS", "5"))

app = FastAPI(title="garden-ingest", version="0.1.0")
_client = httpx.AsyncClient(timeout=HTTP_TIMEOUT)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/ingest")
async def ingest(request: Request) -> Response:
    try:
        payload = await request.json()
    except Exception:
        return Response("invalid JSON", status_code=status.HTTP_400_BAD_REQUEST)

    try:
        samples = to_samples(payload)
    except ValidationError as exc:
        log.warning("rejected payload: %s", exc)
        return Response(str(exc), status_code=status.HTTP_422_UNPROCESSABLE_ENTITY)

    device_id = samples[0].labels.get("device_id") or payload.get("device_id", "unknown")
    push_url = f"{PUSHGATEWAY_URL}/metrics/job/{quote(PUSH_JOB)}/instance/{quote(str(device_id))}"
    body = render_exposition(samples)

    try:
        # PUT replaces the whole group for this instance (clears stale series).
        resp = await _client.put(push_url, content=body, headers={"Content-Type": "text/plain"})
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        log.error("pushgateway error for %s: %s", device_id, exc)
        return Response("upstream pushgateway error", status_code=status.HTTP_502_BAD_GATEWAY)

    log.info("pushed %d samples for device_id=%s", len(samples), device_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.on_event("shutdown")
async def _shutdown() -> None:
    await _client.aclose()
