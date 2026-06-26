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


# ── verify_on: costume-awareness tests ───────────────────────────────────────


class CostumeFakeClient:
    """FakeClient that returns name_score and optionally costume_score.

    ``costume_scores`` is a list of (name_score, costume_score|None) tuples
    returned in sequence on each at_check_asset_match call.  After the list
    is exhausted every subsequent call returns the last entry.
    """
    def __init__(self, scores):
        self.sent = []
        self._scores = list(scores)
        self._idx = 0

    def send(self, msg):
        self.sent.append(msg)
        t = msg.get("type", "")
        if t == "at_check_asset_match":
            entry = self._scores[min(self._idx, len(self._scores) - 1)]
            self._idx += 1
            name_score, costume_score = entry
            reply = {"type": "at_asset_score", "name_score": name_score}
            if costume_score is not None:
                reply["costume_score"] = costume_score
            return reply
        if t == "at_clip_exists":
            return {"type": "exists_result", "done": False}
        return {"type": {"at_record_clip_begin": "clip_begun",
                         "at_record_clip_mark": "marked",
                         "at_record_clip_abort": "clip_aborted"}.get(t, "ok")}

    def wait_for(self, type_):
        return {"type": "clip_done", "item": "x", "events": {"fps": 60}}


def test_verify_on_costume_retries_on_low_costume_score():
    """When name matches but costume_score is low, verify_on re-presses DPAD_RIGHT
    until costume_score also clears the 0.65 threshold, then returns."""
    g = grid.load_grid(YAML)
    ctrl = FakeController()
    # First check: name=0.95 OK, costume=0.30 (too low) → must re-press
    # Second check: name=0.95 OK, costume=0.80 OK → grounds
    client = CostumeFakeClient([
        (0.95, 0.30),
        (0.95, 0.80),
    ])
    r = SweepRunner(g, ctrl, client, idle_seconds=0.0, settle_seconds=0.0)
    r.verify_on("mario__touring", "characters")

    # One DPAD_RIGHT re-press must have happened before grounding
    dpad_presses = [b for (_, b) in ctrl.log if b == "DPAD_RIGHT"]
    assert len(dpad_presses) >= 1, "expected at least one DPAD_RIGHT re-press"

    # The second at_check_asset_match must carry the costume field
    check_msgs = [m for m in client.sent if m.get("type") == "at_check_asset_match"]
    assert len(check_msgs) == 2
    assert check_msgs[0].get("costume") == "touring"
    assert check_msgs[1].get("costume") == "touring"


def test_verify_on_base_costume_grounds_on_name_score_only():
    """For mario__base the costume is 'base' — no 'costume' key sent, grounds purely
    on name_score >= 0.85 with no costume_score required."""
    g = grid.load_grid(YAML)
    ctrl = FakeController()
    # Single check: name=0.92, no costume_score → should ground immediately
    client = CostumeFakeClient([(0.92, None)])
    r = SweepRunner(g, ctrl, client, idle_seconds=0.0, settle_seconds=0.0)
    r.verify_on("mario__base", "characters")

    # No DPAD_RIGHT re-presses — grounded on the first check
    dpad_presses = [b for (_, b) in ctrl.log if b == "DPAD_RIGHT"]
    assert len(dpad_presses) == 0

    # The at_check_asset_match must NOT carry a 'costume' key for base
    check_msgs = [m for m in client.sent if m.get("type") == "at_check_asset_match"]
    assert len(check_msgs) == 1
    assert "costume" not in check_msgs[0]
