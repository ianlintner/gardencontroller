def test_read_sensors_parses_prom(garden):
    def fake_get(url):  # returns Prometheus instant-query JSON
        if "soil_moisture_percent" in url:
            return {"data": {"result": [{"value": [1000.0, "22.5"]}]}}
        if "push_timestamp" in url:
            return {"data": {"result": [{"value": [1000.0, "990.0"]}]}}  # 10s old
        if "temperature" in url:
            return {"data": {"result": [{"value": [1000.0, "31.0"]}]}}
        return {"data": {"result": []}}
    z = {"name": "zone1", "prom_device_id": "garden-node-1", "probe": "bed1"}
    r = garden.read_sensors(z, prom_url="http://p", now=1000.0, get_json=fake_get, max_age_s=300)
    assert r["soil_pct"] == 22.5 and r["temp_c"] == 31.0 and r["stale"] is False

def test_read_sensors_flags_stale(garden):
    def fake_get(url):
        if "soil_moisture_percent" in url:
            return {"data": {"result": [{"value": [5000.0, "22.5"]}]}}
        if "push_timestamp" in url:
            return {"data": {"result": [{"value": [5000.0, "1000.0"]}]}}  # 4000s old
        return {"data": {"result": []}}
    z = {"name": "zone1", "prom_device_id": "garden-node-1", "probe": "bed1"}
    r = garden.read_sensors(z, prom_url="http://p", now=5000.0, get_json=fake_get, max_age_s=300)
    assert r["stale"] is True

def test_read_weather_parses_open_meteo(garden):
    def fake_get(url):
        return {"hourly": {"precipitation": [0.0, 1.0, 0.5] + [0.0]*9,
                            "precipitation_probability": [10, 70, 20] + [0]*9},
                "daily": {"et0_fao_evapotranspiration": [5.2], "temperature_2m_max": [33.0]}}
    r = garden.read_weather(lat=1.0, lon=2.0, get_json=fake_get)
    assert round(r["precip_12h_mm"], 1) == 1.5
    assert r["precip_prob_pct"] == 70 and r["et0_mm"] == 5.2 and r["temp_high_c"] == 33.0


def test_read_weather_handles_null_et0(garden):
    """read_weather must return et0_mm=None (not crash) when Open-Meteo returns null."""
    def fake_get(url):
        return {"hourly": {"precipitation": [0.0] * 12,
                           "precipitation_probability": [0] * 12},
                "daily": {"et0_fao_evapotranspiration": [None],
                          "temperature_2m_max": [28.0]}}
    r = garden.read_weather(lat=1.0, lon=2.0, get_json=fake_get)
    assert r["et0_mm"] is None
    assert r["temp_high_c"] == 28.0


def test_read_sensors_missing_metric_returns_none(garden):
    """When the soil query returns no results, soil_pct must be None (not an error)."""
    def fake_get(url):
        if "soil_moisture_percent" in url:
            return {"data": {"result": []}}          # metric absent
        if "push_timestamp" in url:
            return {"data": {"result": [{"value": [1000.0, "990.0"]}]}}  # recent push
        if "temperature" in url:
            return {"data": {"result": [{"value": [1000.0, "25.0"]}]}}
        return {"data": {"result": []}}

    z = {"name": "zone1", "prom_device_id": "garden-node-1", "probe": "bed1"}
    r = garden.read_sensors(z, prom_url="http://p", now=1000.0, get_json=fake_get, max_age_s=300)
    assert r["soil_pct"] is None
    # Result must still be well-formed
    assert r["zone"] == "zone1"
    assert isinstance(r["stale"], bool)
    assert "age_s" in r
