def test_budget_roundtrip(core, tmp_path):
    b = core.BudgetState(tmp_path, timezone="UTC")
    assert b.watered_today("zone1", now=1000.0) == 0
    b.add_watered("zone1", 10, now=1000.0)
    assert b.watered_today("zone1", now=1500.0) == 10


def test_budget_resets_at_local_midnight(core, tmp_path):
    # America/Chicago is UTC-5 (CDT) in June. Local midnight 2026-06-04 == 05:00Z.
    b = core.BudgetState(tmp_path, timezone="America/Chicago")
    before = 1780549140.0  # 2026-06-04T04:59:00Z == 23:59 CDT on 2026-06-03 (local)
    after = 1780549260.0   # 2026-06-04T05:01:00Z == 00:01 CDT on 2026-06-04 (new local day)
    b.add_watered("zone1", 20, now=before)
    assert b.watered_today("zone1", now=before) == 20
    assert b.watered_today("zone1", now=after) == 0  # rolled to a new local day


def test_budget_isolated_per_zone(core, tmp_path):
    b = core.BudgetState(tmp_path, timezone="UTC")
    b.add_watered("zone1", 5, now=1000.0)
    assert b.watered_today("zone2", now=1000.0) == 0
