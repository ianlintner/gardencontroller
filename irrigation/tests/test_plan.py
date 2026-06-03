import pytest

ZONE = {"name": "zone1", "target_pct": 40.0, "min_per_pct": 0.5,
        "max_per_run": 15, "max_per_day": 30, "min_run": 1}
CFG = {"et0_baseline_mm": 4.0, "rain_skip_mm": 2.0, "rain_skip_prob_pct": 60.0,
       "heat_threshold_c": 32.0, "midday_cap_min": 5}

def plan_one(garden, **kw):
    base = dict(zone=ZONE, cfg=CFG, soil_pct=20.0, temp_c=25.0, stale=False,
                precip_12h_mm=0.0, precip_prob_pct=0.0, et0_mm=4.0,
                run_phase="morning", watered_today=0.0)
    base.update(kw)
    return garden.plan_zone(**base)

def test_skip_when_rain_forecast(garden):
    r = plan_one(garden, precip_12h_mm=5.0)
    assert r["minutes"] == 0 and "rain" in r["reason"].lower()

def test_skip_when_soil_at_target(garden):
    r = plan_one(garden, soil_pct=45.0)
    assert r["minutes"] == 0

def test_skip_when_sensor_stale(garden):
    r = plan_one(garden, stale=True)
    assert r["minutes"] == 0 and "stale" in r["reason"].lower()

def test_deficit_drives_minutes(garden):
    # deficit 20% * 0.5 min/% * (et0 4/4) = 10 min
    r = plan_one(garden, soil_pct=20.0, et0_mm=4.0)
    assert r["minutes"] == 10

def test_et0_scales_up_on_hot_dry_day(garden):
    # deficit 20 * 0.5 * (8/4) = 20 -> capped at 15
    r = plan_one(garden, soil_pct=20.0, et0_mm=8.0)
    assert r["minutes"] == 15

def test_plan_neutral_when_et0_none(garden):
    # et0_mm=None -> factor 1.0 -> deficit 20 * 0.5 * 1.0 = 10 min
    r = plan_one(garden, et0_mm=None)
    assert r["minutes"] == 10


def test_midday_only_waters_in_heat(garden):
    cool = plan_one(garden, run_phase="midday", temp_c=25.0, soil_pct=20.0)
    assert cool["minutes"] == 0          # not hot enough -> midday skips
    hot = plan_one(garden, run_phase="midday", temp_c=35.0, soil_pct=20.0)
    assert 0 < hot["minutes"] <= CFG["midday_cap_min"]   # short burst, midday cap
