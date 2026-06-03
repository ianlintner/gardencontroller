import json


def test_cli_plan_outputs_json(garden, tmp_path, monkeypatch, capsys):
    cfg = {"lat": 1, "lon": 2, "et0_baseline_mm": 4.0, "rain_skip_mm": 2.0,
           "rain_skip_prob_pct": 60.0, "heat_threshold_c": 32.0, "midday_cap_min": 5,
           "prometheus_url": "http://p",
           "zones": [{"name": "zone1", "tuya_device_id": "d", "prom_device_id": "garden-node-1",
                      "probe": "bed1", "target_pct": 40.0, "min_per_pct": 0.5,
                      "max_per_run": 15, "max_per_day": 30, "min_run": 1}]}
    cfg_path = tmp_path / "c.json"; cfg_path.write_text(json.dumps(cfg))
    monkeypatch.setattr(garden, "read_sensors", lambda z, **k: {"zone": z["name"], "soil_pct": 20.0,
                        "temp_c": 25.0, "stale": False, "age_s": 5})
    monkeypatch.setattr(garden, "read_weather", lambda **k: {"precip_12h_mm": 0.0,
                        "precip_prob_pct": 0.0, "et0_mm": 4.0, "temp_high_c": 25.0})
    rc = garden.main(["plan", "--config", str(cfg_path), "--phase", "morning",
                      "--state", str(tmp_path)])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["zone"] == "zone1" and payload[0]["minutes"] == 10
