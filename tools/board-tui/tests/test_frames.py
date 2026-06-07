import json
from frames import parse_frame

VALID = json.dumps({
    "t": 12345, "fw": "1.0.1", "dev": "garden-node-1",
    "pins": {"A0": 16021, "A1": 11002, "A2": 8190, "A3": 8175, "A4": 8201, "A5": 8188},
    "sensors": {"tempC": 22.4, "hum": 48.2, "dhtOk": True,
                "soil": [{"probe": "bed1", "pin": "A0", "raw": 16021, "pct": 12.5}]},
    "net": {"wifi": "up", "rssi": -58, "ip": "192.168.1.42", "push": "ok"},
    "board": {"up_s": 1234, "heap_b": 23456},
})


def test_parses_valid_frame():
    f = parse_frame(VALID)
    assert f is not None
    assert f["dev"] == "garden-node-1"
    assert f["pins"]["A0"] == 16021


def test_ignores_banner_line():
    assert parse_frame("garden-node booting: garden-node-1 @ raised-bed") is None


def test_ignores_ota_log_line():
    assert parse_frame("OTA: up to date") is None


def test_ignores_truncated_json():
    assert parse_frame('{"pins": {"A0": 1') is None


def test_ignores_json_without_telemetry_keys():
    # valid JSON, but not a telemetry frame
    assert parse_frame('{"hello": "world"}') is None


def test_ignores_blank_line():
    assert parse_frame("   ") is None
