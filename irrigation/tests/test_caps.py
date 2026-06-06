def test_clamp_minutes_rounds_and_bounds(core):
    z = {"max_per_run": 15, "min_run": 1}
    assert core.clamp_minutes(8.4, z, watered_today=0) == 8
    assert core.clamp_minutes(99, z, watered_today=0) == 15      # max_per_run
    assert core.clamp_minutes(0.4, z, watered_today=0) == 0      # below min_run -> 0
    assert core.clamp_minutes(3, z, watered_today=0) == 3

def test_clamp_respects_daily_remaining(core):
    z = {"max_per_run": 15, "min_run": 1, "max_per_day": 30}
    assert core.clamp_minutes(15, z, watered_today=28) == 2      # only 2 left today
    assert core.clamp_minutes(15, z, watered_today=30) == 0      # nothing left
