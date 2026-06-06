import json
import pytest


def _make_cfg(tmp_path):
    cfg = {"lat": 1, "lon": 2, "et0_baseline_mm": 4.0, "rain_skip_mm": 2.0,
           "rain_skip_prob_pct": 60.0, "heat_threshold_c": 32.0, "midday_cap_min": 5,
           "prometheus_url": "http://p",
           "zones": [{"name": "zone1", "tuya_device_id": "d", "prom_device_id": "garden-node-1",
                      "probe": "bed1", "target_pct": 40.0, "min_per_pct": 0.5,
                      "max_per_run": 15, "max_per_day": 30, "min_run": 1}]}
    p = tmp_path / "c.json"; p.write_text(json.dumps(cfg)); return str(p)


def test_cli_plan_runs_without_tuya_env(garden, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("TUYA_CLIENT_ID", raising=False)
    monkeypatch.delenv("TUYA_CLIENT_SECRET", raising=False)
    monkeypatch.setattr(garden, "read_sensors", lambda z, **k: {"zone": z["name"], "soil_pct": 20.0,
                        "temp_c": 25.0, "stale": False, "age_s": 5})
    monkeypatch.setattr(garden, "read_weather", lambda **k: {"precip_12h_mm": 0.0,
                        "precip_prob_pct": 0.0, "et0_mm": 4.0, "temp_high_c": 25.0})
    rc = garden.main(["plan", "--config", _make_cfg(tmp_path), "--phase", "morning"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)[0]["minutes"] == 10


def test_cli_has_no_water_or_status_subcommand(garden, tmp_path):
    # argparse exits (code 2) on an unknown subcommand
    with pytest.raises(SystemExit):
        garden.main(["water", "--zone", "zone1", "--minutes", "5",
                     "--config", _make_cfg(tmp_path)])
    with pytest.raises(SystemExit):
        garden.main(["status", "--zone", "zone1", "--config", _make_cfg(tmp_path)])


def test_garden_module_has_no_tuya_symbols(garden):
    # the agent CLI must not carry a Tuya code path
    assert not hasattr(garden, "Tuya")
    assert not hasattr(garden, "do_water")
    assert not hasattr(garden, "_tuya_from_env")
    assert not hasattr(garden, "cmd_water")
