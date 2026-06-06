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
