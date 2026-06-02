import pytest

from app.mapping import ValidationError, render_exposition, to_samples


def _metrics(samples):
    return {s.metric for s in samples}


def test_full_payload_maps_all_metrics():
    payload = {
        "device_id": "garden-node-1",
        "location": "raised-bed",
        "readings": {
            "air_temperature_celsius": 21.3,
            "air_humidity_percent": 54,
            "soil": [{"probe": "bed1", "raw": 640, "percent": 42.0}],
            "rain_percent": 12.5,
            "rain_detected": False,
        },
        "board": {"rssi_dbm": -58, "uptime_seconds": 1234},
    }
    samples = to_samples(payload)
    assert {
        "garden_air_temperature_celsius",
        "garden_air_humidity_percent",
        "garden_soil_moisture_raw",
        "garden_soil_moisture_percent",
        "garden_rain_intensity_percent",
        "garden_rain_detected",
        "garden_board_rssi_dbm",
        "garden_board_uptime_seconds",
        "garden_push_timestamp_seconds",
    } <= _metrics(samples)


def test_label_values_are_sanitized():
    payload = {"device_id": "garden node/1!!", "location": "bed; drop", "readings": {"air_humidity_percent": 50}}
    samples = to_samples(payload)
    s = next(x for x in samples if x.metric == "garden_air_humidity_percent")
    assert s.labels["device_id"] == "garden_node/1"
    assert ";" not in s.labels["location"] and " " not in s.labels["location"]


def test_out_of_range_values_are_clamped():
    payload = {"device_id": "n1", "location": "b", "readings": {"air_humidity_percent": 150}}
    s = next(x for x in to_samples(payload) if x.metric == "garden_air_humidity_percent")
    assert s.value == 100


def test_empty_readings_rejected():
    with pytest.raises(ValidationError):
        to_samples({"device_id": "n1", "location": "b", "readings": {}})


def test_exposition_has_no_job_or_instance_labels():
    # job/instance come from the Pushgateway URL path, not the body.
    text = render_exposition(to_samples({"device_id": "n1", "location": "b",
                                         "readings": {"air_humidity_percent": 50}}))
    assert "job=" not in text and "instance=" not in text
    assert text.endswith("\n")
