"""Tests for SweepRunner core + character capture (Task 6)."""
import os
import grid
from sweep_runner import SweepRunner

YAML = os.path.join(os.path.dirname(__file__), "..",
                    "tools", "autotemplate", "scripts", "clip_sweep.yaml")


class FakeController:
    def __init__(self): self.log = []
    def press(self, b, duration=0.1): self.log.append(("press", b))
    def hold(self, b, dur): self.log.append(("hold", b, dur))
    def rstick_down(self, dur): self.log.append(("rstick", dur))


class FakeClient:
    def __init__(self, ground=None):
        self.sent = []
        self._ground = ground or {}
    def send(self, msg):
        self.sent.append(msg)
        t = msg["type"]
        if t == "at_check_asset_match":
            return {"type": "at_asset_score", "name_score": self._ground.get(msg["name"], 0.0)}
        if t == "at_clip_exists":
            return {"type": "exists_result", "done": False}
        return {"type": {"at_record_clip_begin": "clip_begun",
                         "at_record_clip_mark": "marked"}.get(t, "ok")}
    def wait_for(self, type_):
        return {"type": "clip_done", "item": "x", "events": {"fps": 60}}


def test_capture_char_emits_begin_idle_flourish(monkeypatch):
    g = grid.load_grid(YAML)
    ctrl, client = FakeController(), FakeClient()
    r = SweepRunner(g, ctrl, client, idle_seconds=0.0)
    r.capture_char("mario__base")
    types = [m["type"] for m in client.sent]
    assert types[0] == "at_clip_exists"
    assert "at_record_clip_begin" in types
    assert {"type": "at_record_clip_mark", "event": "flourish"} in client.sent
    assert ("press", "A") in ctrl.log         # flourish press
    # no swap mark for characters (no spawn-in)
    assert {"type": "at_record_clip_mark", "event": "swap"} not in client.sent
