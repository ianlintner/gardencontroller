def test_watered_today_roundtrip(garden, tmp_path):
    st = garden.State(tmp_path)
    assert st.watered_today("zone1", now=1000.0) == 0
    st.add_watered("zone1", 10, now=1000.0)
    assert st.watered_today("zone1", now=1500.0) == 10
    # a day later, resets
    assert st.watered_today("zone1", now=1000.0 + 90000) == 0

def test_pending_plan_ttl(garden, tmp_path):
    st = garden.State(tmp_path)
    st.set_pending([{"zone": "zone1", "minutes": 8, "reason": "x"}], now=1000.0, ttl_s=7200)
    assert st.get_pending(now=2000.0) == [{"zone": "zone1", "minutes": 8, "reason": "x"}]
    assert st.get_pending(now=1000.0 + 7201) is None      # expired

def test_pending_missing_expires_is_safe(garden, tmp_path):
    st = garden.State(tmp_path)
    # Simulate a malformed pending record with no 'expires' key
    import json
    (tmp_path / "state.json").write_text(json.dumps({"pending": {"plan": [{"zone": "z"}]}}))
    assert st.get_pending(now=1000.0) is None

def test_idempotency_key(garden, tmp_path):
    st = garden.State(tmp_path)
    assert st.seen_run("morning-2026-06-02") is False
    st.mark_run("morning-2026-06-02")
    assert st.seen_run("morning-2026-06-02") is True
