import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load_server():
    # ensure deps are importable under their bare module names
    for name, rel in [("garden_core", "garden_core.py"), ("gateway", "mcp/gateway.py")]:
        spec = importlib.util.spec_from_file_location(name, ROOT / rel)
        mod = importlib.util.module_from_spec(spec); sys.modules[name] = mod
        spec.loader.exec_module(mod)
    spec = importlib.util.spec_from_file_location("server", ROOT / "mcp" / "server.py")
    mod = importlib.util.module_from_spec(spec); sys.modules["server"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_build_service_from_config(tmp_path, monkeypatch):
    cfg = {"region": "us", "timezone": "UTC",
           "zones": [{"name": "zone1", "tuya_device_id": "d", "switch_dp": "switch",
                      "countdown_dp": "countdown_1", "max_per_run": 15,
                      "max_per_day": 30, "min_run": 1}]}
    cfg_path = tmp_path / "c.json"; cfg_path.write_text(json.dumps(cfg))
    monkeypatch.setenv("GARDEN_MCP_CONFIG", str(cfg_path))
    monkeypatch.setenv("GARDEN_MCP_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("TUYA_CLIENT_ID", "cid")
    monkeypatch.setenv("TUYA_CLIENT_SECRET", "sec")
    server = _load_server()
    svc = server.build_service()
    assert "zone1" in svc.zones
    assert svc.list_zones()[0]["remaining_today"] == 30


def test_build_service_requires_credentials(tmp_path, monkeypatch):
    cfg_path = tmp_path / "c.json"
    cfg_path.write_text(json.dumps({"region": "us", "timezone": "UTC", "zones": []}))
    monkeypatch.setenv("GARDEN_MCP_CONFIG", str(cfg_path))
    monkeypatch.setenv("GARDEN_MCP_STATE", str(tmp_path / "state"))
    monkeypatch.delenv("TUYA_CLIENT_ID", raising=False)
    monkeypatch.delenv("TUYA_CLIENT_SECRET", raising=False)
    server = _load_server()
    try:
        server.build_service(); assert False, "expected SystemExit"
    except SystemExit:
        pass
