#!/usr/bin/env python3
"""Simulate one or more garden nodes pushing readings — no hardware needed.

Local (default): posts straight to the local ingest service (no auth).
    docker compose -f local/docker-compose.yml up -d --build
    python scripts/simulate.py --devices 3

Cloud: authenticate with roauth2 (from .env) and post to garden.cat-herding.net.
    python scripts/simulate.py --cloud

Each device keeps its own drifting state (soil slowly dries, temp has a daily
swing, occasional rain bumps soil + humidity) so the dashboard shows lifelike
moving data.
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def load_env() -> dict:
    env = {}
    p = Path(__file__).resolve().parent.parent / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def get_cloud_token(env: dict) -> str:
    token_url = env.get("OAUTH_TOKEN_URL", "https://roauth2.cat-herding.net/oauth/token")
    cid, csec = env["OAUTH_CLIENT_ID"], env["OAUTH_CLIENT_SECRET"]
    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "audience": env.get("OAUTH_AUDIENCE", "garden-ingest"),
        "scope": "garden:write",
    }).encode()
    basic = base64.b64encode(f"{cid}:{csec}".encode()).decode()
    req = urllib.request.Request(token_url, data=body, method="POST", headers={
        "Authorization": f"Basic {basic}",
        "Content-Type": "application/x-www-form-urlencoded",
    })
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)["access_token"]


class Node:
    """A simulated board with slowly-evolving sensor state."""
    def __init__(self, device_id: str, location: str):
        self.device_id = device_id
        self.location = location
        self.soil = random.uniform(45, 70)     # %
        self.t0 = time.time()

    def reading(self) -> dict:
        t = time.time() - self.t0
        raining = random.random() < 0.05
        # soil dries slowly; jumps up when it rains
        self.soil = min(100.0, self.soil + (8 if raining else -0.3) + random.uniform(-0.5, 0.5))
        self.soil = max(0.0, self.soil)
        temp = 21 + 4 * math.sin(t / 120) + random.uniform(-0.4, 0.4)   # gentle daily-ish swing
        humid = 55 + (20 if raining else 0) + random.uniform(-3, 3)
        rain_pct = random.uniform(20, 80) if raining else random.uniform(0, 8)
        # raw ADC values consistent with a capacitive probe (lower = wetter)
        soil_raw = int(13000 - (self.soil / 100) * 8000)
        return {
            "device_id": self.device_id,
            "location": self.location,
            "readings": {
                "air_temperature_celsius": round(temp, 1),
                "air_humidity_percent": round(min(100, max(0, humid)), 1),
                "soil": [{"probe": "bed1", "raw": soil_raw, "percent": round(self.soil, 1)}],
                "rain_percent": round(rain_pct, 1),
                "rain_detected": raining,
            },
            "board": {"rssi_dbm": random.randint(-70, -40),
                      "uptime_seconds": int(t)},
        }


def post(url: str, payload: dict, token: str | None) -> int:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def main() -> None:
    ap = argparse.ArgumentParser(description="Simulate garden nodes pushing readings.")
    ap.add_argument("--url", help="ingest URL (default: local, or cloud INGEST_URL with --cloud)")
    ap.add_argument("--devices", type=int, default=1, help="number of simulated boards")
    ap.add_argument("--interval", type=float, default=5.0, help="seconds between pushes")
    ap.add_argument("--location", default="raised-bed")
    ap.add_argument("--cloud", action="store_true", help="auth via roauth2 and post to the cloud")
    ap.add_argument("--once", action="store_true", help="push one round and exit")
    args = ap.parse_args()

    env = load_env()
    token = None
    if args.cloud:
        url = args.url or env.get("INGEST_URL", "https://garden.cat-herding.net/ingest")
        token = get_cloud_token(env)
        print(f"cloud mode: got token ({len(token)} chars)")
    else:
        url = args.url or "http://localhost:8080/ingest"

    nodes = [Node(f"garden-node-{i+1}", args.location) for i in range(args.devices)]
    print(f"posting to {url} | {len(nodes)} device(s) | every {args.interval}s | Ctrl-C to stop")
    try:
        while True:
            for n in nodes:
                rd = n.reading()
                code = post(url, rd, token)
                ok = "ok" if code in (200, 204) else f"FAIL({code})"
                print(f"  {n.device_id:14s} soil={rd['readings']['soil'][0]['percent']:5.1f}% "
                      f"temp={rd['readings']['air_temperature_celsius']:4.1f}C rain={rd['readings']['rain_detected']!s:5} -> {ok}",
                      flush=True)
            if args.once:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
