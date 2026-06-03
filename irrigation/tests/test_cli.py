import json
import time
import pytest


def _make_cfg(tmp_path):
    cfg = {"lat": 1, "lon": 2, "et0_baseline_mm": 4.0, "rain_skip_mm": 2.0,
           "rain_skip_prob_pct": 60.0, "heat_threshold_c": 32.0, "midday_cap_min": 5,
           "prometheus_url": "http://p",
           "zones": [{"name": "zone1", "tuya_device_id": "d", "prom_device_id": "garden-node-1",
                      "probe": "bed1", "target_pct": 40.0, "min_per_pct": 0.5,
                      "max_per_run": 15, "max_per_day": 30, "min_run": 1}]}
    cfg_path = tmp_path / "c.json"
    cfg_path.write_text(json.dumps(cfg))
    return str(cfg_path)


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


class _DummyTuya:
    """Minimal Tuya stub that records token() calls but does no network I/O."""
    def __init__(self):
        self.token_called = False

    def token(self):
        self.token_called = True
        return "fake-token"


def test_cli_water_records_watered_on_success(garden, tmp_path, monkeypatch):
    cfg = _make_cfg(tmp_path)
    state_dir = str(tmp_path / "state")
    monkeypatch.setattr(garden, "_tuya_from_env", lambda: _DummyTuya())
    monkeypatch.setattr(garden, "do_water",
                        lambda tuya, **kw: {"zone": "zone1", "minutes": 5, "ok": True})
    rc = garden.main(["water", "--zone", "zone1", "--minutes", "5", "--force",
                      "--config", cfg, "--state", state_dir])
    assert rc == 0
    assert garden.State(state_dir).watered_today("zone1", now=time.time()) == 5.0


def test_cli_water_dry_run_does_not_record(garden, tmp_path, monkeypatch):
    cfg = _make_cfg(tmp_path)
    state_dir = str(tmp_path / "state")
    monkeypatch.setattr(garden, "_tuya_from_env", lambda: _DummyTuya())
    rc = garden.main(["water", "--zone", "zone1", "--minutes", "5",
                      "--config", cfg, "--state", state_dir, "--dry-run"])
    assert rc == 0
    assert garden.State(state_dir).watered_today("zone1", now=time.time()) == 0.0


def test_cli_water_failure_exits_1_no_record(garden, tmp_path, monkeypatch):
    cfg = _make_cfg(tmp_path)
    state_dir = str(tmp_path / "state")
    monkeypatch.setattr(garden, "_tuya_from_env", lambda: _DummyTuya())
    monkeypatch.setattr(garden, "do_water",
                        lambda tuya, **kw: {"zone": "zone1", "minutes": 5, "ok": False, "note": "x"})
    rc = garden.main(["water", "--zone", "zone1", "--minutes", "5", "--force",
                      "--config", cfg, "--state", state_dir])
    assert rc == 1
    assert garden.State(state_dir).watered_today("zone1", now=time.time()) == 0.0


def test_cli_unknown_zone_aborts(garden, tmp_path):
    cfg = _make_cfg(tmp_path)
    with pytest.raises(SystemExit):
        garden.main(["sensors", "--zone", "nope", "--config", cfg])


def test_cli_water_requires_approved_plan(garden, tmp_path, monkeypatch):
    """Without --force and no pending plan, water must abort."""
    cfg = _make_cfg(tmp_path)
    state_dir = str(tmp_path / "state")
    monkeypatch.setattr(garden, "_tuya_from_env", lambda: _DummyTuya())
    with pytest.raises(SystemExit):
        garden.main(["water", "--zone", "zone1", "--minutes", "5",
                     "--config", cfg, "--state", state_dir])


def test_cli_water_rejects_exceeding_approved(garden, tmp_path, monkeypatch):
    """Water must abort if requested minutes exceed the approved plan's minutes."""
    cfg = _make_cfg(tmp_path)
    state_dir = str(tmp_path / "state")
    garden.State(state_dir).set_pending(
        [{"zone": "zone1", "minutes": 5, "reason": "x"}], now=time.time()
    )
    monkeypatch.setattr(garden, "_tuya_from_env", lambda: _DummyTuya())
    with pytest.raises(SystemExit):
        garden.main(["water", "--zone", "zone1", "--minutes", "10",
                     "--config", cfg, "--state", state_dir])


def test_cli_water_consumes_pending_single_use(garden, tmp_path, monkeypatch):
    """A pending entry is consumed on success; a second identical call must abort."""
    cfg = _make_cfg(tmp_path)
    state_dir = str(tmp_path / "state")
    garden.State(state_dir).set_pending(
        [{"zone": "zone1", "minutes": 5, "reason": "x"}], now=time.time()
    )
    monkeypatch.setattr(garden, "_tuya_from_env", lambda: _DummyTuya())
    monkeypatch.setattr(garden, "do_water",
                        lambda tuya, **kw: {"zone": "zone1", "minutes": 5, "ok": True})
    # First call succeeds and consumes the pending entry.
    rc = garden.main(["water", "--zone", "zone1", "--minutes", "5",
                      "--config", cfg, "--state", state_dir])
    assert rc == 0
    # Second call must now fail (pending consumed).
    with pytest.raises(SystemExit):
        garden.main(["water", "--zone", "zone1", "--minutes", "5",
                     "--config", cfg, "--state", state_dir])
