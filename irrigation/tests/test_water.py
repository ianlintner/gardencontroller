class FakeTuya:
    def __init__(self): self.sent = []; self._status = {"switch": False}
    def send_commands(self, device_id, commands):
        self.sent.append((device_id, commands))
        for c in commands:
            if c["code"] in ("switch", "switch_1"): self._status["switch"] = c["value"]
            if c["code"].startswith("countdown"): self._status[c["code"]] = c["value"]
        return True
    def status(self, device_id): return dict(self._status)


def _zone():
    return {"name": "zone1", "tuya_device_id": "dev1", "max_per_run": 15,
            "max_per_day": 30, "min_run": 1, "switch_dp": "switch", "countdown_dp": "countdown_1"}


def test_water_sends_countdown_and_reports(core):
    z = _zone()
    t = FakeTuya()
    r = core.do_water(t, zone=z, minutes=8, watered_today=0, dry_run=False)
    assert r["minutes"] == 8 and r["ok"] is True
    assert r["switch_confirmed"] is True
    assert "auto-off armed" in r["note"]
    # set switch on + countdown 480s
    codes = {c["code"]: c["value"] for (_, cmds) in t.sent for c in cmds}
    assert codes["switch"] is True and codes["countdown_1"] == 480


def test_water_reclamps_over_cap(core):
    z = _zone()
    t = FakeTuya()
    r = core.do_water(t, zone=z, minutes=999, watered_today=0, dry_run=False)
    assert r["minutes"] == 15            # re-clamped even if asked for 999


def test_water_dry_run_sends_nothing(core):
    z = _zone()
    t = FakeTuya()
    r = core.do_water(t, zone=z, minutes=8, watered_today=0, dry_run=True)
    assert r["dry_run"] is True and t.sent == []


def test_water_failsafe_off_when_countdown_not_accepted(core):
    """A device model that ignores countdown_1 should trigger failsafe close."""
    class NoCountdownTuya:
        """send_commands stores commands but status never reflects a countdown."""
        def __init__(self): self.sent = []
        def send_commands(self, device_id, commands):
            self.sent.append((device_id, commands))
            return True
        def status(self, device_id):
            # Switch appears on but countdown is never set
            return {"switch": True}

    z = _zone()
    t = NoCountdownTuya()
    r = core.do_water(t, zone=z, minutes=8, watered_today=0, dry_run=False,
                        confirm_attempts=1, confirm_sleep_s=0)
    assert r["ok"] is False
    assert "failsafe" in r["note"].lower()
    # The last command batch sent must include a switch OFF
    all_commands = [c for (_, cmds) in t.sent for c in cmds]
    off_cmds = [c for c in all_commands if c["code"] == "switch" and c["value"] is False]
    assert off_cmds, "failsafe OFF command must have been sent"


def test_water_confirm_sleep_not_called_when_immediately_confirmed(core):
    """With FakeTuya (confirms on first read), time.sleep should never be called."""
    import unittest.mock as mock

    z = _zone()
    t = FakeTuya()
    with mock.patch.object(core.time, "sleep", side_effect=AssertionError("sleep called")) as _m:
        r = core.do_water(t, zone=z, minutes=8, watered_today=0, dry_run=False,
                            confirm_attempts=3, confirm_sleep_s=1.0)
    assert r["ok"] is True  # confirmed on first attempt, no sleep needed
