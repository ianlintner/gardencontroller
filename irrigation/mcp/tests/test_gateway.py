import json


class FakeTuya:
    """Records commands; reflects countdown into status (auto-off confirmed)."""
    def __init__(self):
        self.sent = []
        self._status = {"switch": False}
        self.token_calls = 0
    def token(self):
        self.token_calls += 1
        return "tok"
    def send_commands(self, device_id, commands):
        self.sent.append((device_id, commands))
        for c in commands:
            if c["code"] in ("switch", "switch_1"):
                self._status["switch"] = c["value"]
            if c["code"].startswith("countdown"):
                self._status[c["code"]] = c["value"]
        return True
    def status(self, device_id):
        return dict(self._status)


ZONES = [
    {"name": "zone1", "tuya_device_id": "dev1", "switch_dp": "switch",
     "countdown_dp": "countdown_1", "max_per_run": 15, "max_per_day": 30, "min_run": 1},
    {"name": "zone2", "tuya_device_id": "dev2", "switch_dp": "switch",
     "countdown_dp": "countdown_1", "max_per_run": 10, "max_per_day": 20, "min_run": 1},
]


def _svc(gateway, core, tmp_path, tuya=None, now=1000.0):
    budget = core.BudgetState(tmp_path, timezone="UTC")
    audit = []
    svc = gateway.GatewayService(
        zones=ZONES, tuya=tuya or FakeTuya(), budget=budget,
        now_fn=lambda: now, audit=audit.append, confirm_sleep_s=0,
    )
    return svc, audit


def test_list_zones_reports_remaining_budget(gateway, core, tmp_path):
    svc, _ = _svc(gateway, core, tmp_path)
    rows = {r["zone"]: r for r in svc.list_zones()}
    assert rows["zone1"]["remaining_today"] == 30
    assert rows["zone2"]["max_per_run"] == 10


def test_water_unknown_zone_rejected(gateway, core, tmp_path):
    t = FakeTuya()
    svc, audit = _svc(gateway, core, tmp_path, tuya=t)
    r = svc.water_zone("zoneX", 5)
    assert r["ok"] is False and r["reason"] == "unknown zone"
    assert t.sent == []  # nothing sent
    assert json.loads(json.dumps(audit[-1]))["decision"] == "unknown zone"


def test_water_clamps_to_max_per_run(gateway, core, tmp_path):
    svc, _ = _svc(gateway, core, tmp_path)
    r = svc.water_zone("zone1", 999)
    assert r["requested"] == 999 and r["granted"] == 15 and r["ok"] is True


def test_water_accumulates_toward_daily_budget(gateway, core, tmp_path):
    svc, _ = _svc(gateway, core, tmp_path)            # zone1 max_per_day=30
    assert svc.water_zone("zone1", 15)["granted"] == 15
    assert svc.water_zone("zone1", 15)["granted"] == 15  # total 30
    third = svc.water_zone("zone1", 15)
    assert third["granted"] == 0 and third["ok"] is True  # budget exhausted -> clamp to 0


def test_water_sends_token_and_commands_on_success(gateway, core, tmp_path):
    t = FakeTuya()
    svc, _ = _svc(gateway, core, tmp_path, tuya=t)
    r = svc.water_zone("zone1", 8)
    assert r["granted"] == 8 and r["ok"] is True
    assert t.token_calls == 1
    codes = {c["code"]: c["value"] for (_, cmds) in t.sent for c in cmds}
    assert codes["switch"] is True and codes["countdown_1"] == 480
