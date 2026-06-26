"""Tests for SweepRunner core + character/kart capture.

Grounding reads the LIVE tracker via at_current_selection (returns the current
detected character/costume/kart display names), so the fake returns a selection
(static, or a sequence consumed one-per-check) instead of re-match scores.
"""
import os
import grid
from sweep_runner import SweepRunner

YAML = os.path.join(os.path.dirname(__file__), "..",
                    "tools", "autotemplate", "scripts", "clip_sweep.yaml")


class FakeController:
    def __init__(self): self.log = []
    def press(self, b, duration=0.1): self.log.append(("press", b))
    def hold(self, b, dur): self.log.append(("hold", b, dur))
    def rstick_down(self, *a): self.log.append(("rstick",))


class FakeClient:
    """`selection` is either a dict {character,costume,kart} (the static current
    selection) or a LIST of such dicts returned one per at_current_selection call
    (the last entry repeats)."""
    def __init__(self, selection=None):
        self.sent = []
        self._sel = selection
        self._i = 0

    def _current(self):
        if isinstance(self._sel, list):
            s = self._sel[min(self._i, len(self._sel) - 1)]
            self._i += 1
        else:
            s = self._sel or {}
        return {"type": "current_selection", "course": None,
                "character": s.get("character"), "costume": s.get("costume"),
                "kart": s.get("kart")}

    def send(self, msg):
        self.sent.append(msg)
        t = msg["type"]
        if t == "at_current_selection":
            return self._current()
        if t == "at_clip_exists":
            return {"type": "exists_result", "done": False}
        return {"type": {"at_record_clip_begin": "clip_begun",
                         "at_record_clip_mark": "marked",
                         "at_record_clip_abort": "clip_aborted"}.get(t, "ok")}

    def wait_for(self, type_):
        return {"type": "clip_done", "item": "x", "events": {"fps": 60}}


def _selections(client):
    return [m for m in client.sent if m.get("type") == "at_current_selection"]


def test_capture_char_emits_begin_idle_flourish():
    g = grid.load_grid(YAML)
    ctrl, client = FakeController(), FakeClient()
    SweepRunner(g, ctrl, client, idle_seconds=0.0).capture_char("mario__base")
    types = [m["type"] for m in client.sent]
    assert types[0] == "at_clip_exists"
    assert "at_record_clip_begin" in types
    assert {"type": "at_record_clip_mark", "event": "flourish"} in client.sent
    assert ("press", "A") in ctrl.log                                  # flourish press
    assert {"type": "at_record_clip_mark", "event": "swap"} not in client.sent   # no spawn-in for chars


def test_kart_keep_on_match():
    g = grid.load_grid(YAML)
    ctrl = FakeController()
    client = FakeClient(selection={"kart": "Plushbuggy"})              # tracker already shows the right kart
    SweepRunner(g, ctrl, client, idle_seconds=0.0, settle_seconds=0.0,
                ground_timeout=0.0).capture_kart("mario__base", "plushbuggy")
    assert {"type": "at_record_clip_mark", "event": "swap"} in client.sent
    assert not any(m["type"] == "at_record_clip_abort" for m in client.sent)
    assert ("press", "DPAD_RIGHT") in ctrl.log                         # the swap-on press


def test_kart_discard_and_retry_on_mismatch():
    g = grid.load_grid(YAML)
    ctrl = FakeController()
    # ground#1 -> still on Standard Kart (miss); recover reads Standard Kart and steps right;
    # ground#2 -> Plushbuggy (hit). Loop terminates.
    client = FakeClient(selection=[{"kart": "Standard Kart"},
                                   {"kart": "Standard Kart"},
                                   {"kart": "Plushbuggy"}])
    SweepRunner(g, ctrl, client, idle_seconds=0.0, settle_seconds=0.0,
                ground_timeout=0.0).capture_kart("mario__base", "plushbuggy")
    assert any(m["type"] == "at_record_clip_abort" for m in client.sent)
    assert {"type": "at_record_clip_mark", "event": "flourish"} in client.sent   # proves it terminated


def test_verify_on_costume_retries_until_right_costume():
    """name matches but the wrong costume is shown → re-press until the right costume lands."""
    g = grid.load_grid(YAML)
    ctrl = FakeController()
    client = FakeClient(selection=[{"character": "Mario", "costume": "Base"},      # wrong costume
                                   {"character": "Mario", "costume": "Touring"}])  # right
    SweepRunner(g, ctrl, client, idle_seconds=0.0, settle_seconds=0.0).verify_on("mario__touring", "characters")
    assert len([b for (_, b) in ctrl.log if b == "DPAD_RIGHT"]) >= 1
    assert len(_selections(client)) == 2


def test_verify_on_base_grounds_on_character_only():
    """mario__base grounds as soon as the tracker reports character=Mario (costume ignored)."""
    g = grid.load_grid(YAML)
    ctrl = FakeController()
    client = FakeClient(selection={"character": "Mario", "costume": "Base"})
    SweepRunner(g, ctrl, client, idle_seconds=0.0, settle_seconds=0.0).verify_on("mario__base", "characters")
    assert len([b for (_, b) in ctrl.log if b == "DPAD_RIGHT"]) == 0   # grounded on first check
    assert len(_selections(client)) == 1
