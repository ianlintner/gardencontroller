class FakeTuya:
    def __init__(self): self.sent = []; self._status = {"switch": False}
    def send_commands(self, device_id, commands):
        self.sent.append((device_id, commands))
        for c in commands:
            if c["code"] in ("switch", "switch_1"): self._status["switch"] = c["value"]
            if c["code"].startswith("countdown"): self._status[c["code"]] = c["value"]
        return True
    def status(self, device_id): return dict(self._status)

def test_water_sends_countdown_and_reports(garden):
    z = {"name": "zone1", "tuya_device_id": "dev1", "max_per_run": 15,
         "max_per_day": 30, "min_run": 1, "switch_dp": "switch", "countdown_dp": "countdown_1"}
    t = FakeTuya()
    r = garden.do_water(t, zone=z, minutes=8, watered_today=0, dry_run=False)
    assert r["minutes"] == 8 and r["ok"] is True
    # set switch on + countdown 480s
    codes = {c["code"]: c["value"] for (_, cmds) in t.sent for c in cmds}
    assert codes["switch"] is True and codes["countdown_1"] == 480

def test_water_reclamps_over_cap(garden):
    z = {"name": "zone1", "tuya_device_id": "dev1", "max_per_run": 15,
         "max_per_day": 30, "min_run": 1, "switch_dp": "switch", "countdown_dp": "countdown_1"}
    t = FakeTuya()
    r = garden.do_water(t, zone=z, minutes=999, watered_today=0, dry_run=False)
    assert r["minutes"] == 15            # re-clamped even if asked for 999

def test_water_dry_run_sends_nothing(garden):
    z = {"name": "zone1", "tuya_device_id": "dev1", "max_per_run": 15,
         "max_per_day": 30, "min_run": 1, "switch_dp": "switch", "countdown_dp": "countdown_1"}
    t = FakeTuya()
    r = garden.do_water(t, zone=z, minutes=8, watered_today=0, dry_run=True)
    assert r["dry_run"] is True and t.sent == []
