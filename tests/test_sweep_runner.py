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


def test_kart_keep_on_match():
    g = grid.load_grid(YAML)
    ctrl = FakeController()
    client = FakeClient(ground={"plushbuggy": 0.95})   # lands correctly
    r = SweepRunner(g, ctrl, client, idle_seconds=0.0, settle_seconds=0.0)
    r.capture_kart("mario__base", "plushbuggy")
    assert {"type": "at_record_clip_mark", "event": "swap"} in client.sent
    assert not any(m["type"] == "at_record_clip_abort" for m in client.sent)
    assert ("press", "DPAD_RIGHT") in ctrl.log     # the swap-on press


def test_kart_discard_and_retry_on_mismatch():
    g = grid.load_grid(YAML)
    ctrl = FakeController()

    # First ground check for plushbuggy → miss (still on standard_kart).
    # _recover_to scans the row: standard_kart scores 0.95 so recovery finds us.
    # Second ground check for plushbuggy → hit. Loop terminates.
    class RetryClient(FakeClient):
        def __init__(self):
            super().__init__(ground={})
            self._plushbuggy_calls = 0

        def send(self, msg):
            if msg["type"] == "at_check_asset_match":
                self.sent.append(msg)
                name = msg["name"]
                if name == "plushbuggy":
                    self._plushbuggy_calls += 1
                    # first call → mismatch; subsequent calls → match
                    score = 0.0 if self._plushbuggy_calls == 1 else 0.95
                elif name == "standard_kart":
                    # so _recover_to can identify our current position
                    score = 0.95
                else:
                    score = 0.0
                return {"type": "at_asset_score", "name_score": score}
            return super().send(msg)

    client = RetryClient()
    r = SweepRunner(g, ctrl, client, idle_seconds=0.0, settle_seconds=0.0)
    r.capture_kart("mario__base", "plushbuggy")
    assert any(m["type"] == "at_record_clip_abort" for m in client.sent)
    # confirm the loop terminated (not hanging) by asserting the flourish was emitted
    assert {"type": "at_record_clip_mark", "event": "flourish"} in client.sent
