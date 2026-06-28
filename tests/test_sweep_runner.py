"""Tests for SweepRunner core + character/kart capture.

Grounding reads the LIVE tracker via at_current_selection (returns the current
detected character/costume/kart display names), so the fake returns a selection
(static, or a sequence consumed one-per-check) instead of re-match scores.
"""
import os
import grid
from sweep_runner import SweepRunner, sample_grid

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
        if t == "at_current_screen":
            return {"type": "current_screen", "screen": "KART_SELECT"}
        if t == "at_clip_exists":
            return {"type": "exists_result", "done": False}
        if t == "at_screen_score":
            return {"type": "screen_score", "screen": msg.get("screen"), "score": 1.0, "detected": True}
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
    # tracker already shows the right kart; KART_SELECT departs after the flourish (False), then
    # scores again on the B return (True).
    client = FakeScoreClient(selection={"kart": "Plushbuggy"}, scores={"KART_SELECT": [False, True]})
    SweepRunner(g, ctrl, client, idle_seconds=0.0, settle_seconds=0.0,
                ground_timeout=0.0, screen_timeout=0.0).capture_kart("mario__base", "plushbuggy")
    assert {"type": "at_record_clip_mark", "event": "swap"} in client.sent
    assert {"type": "at_record_clip_mark", "event": "flourish"} in client.sent   # recorded through
    assert not any(m["type"] == "at_record_clip_abort" for m in client.sent)     # grounded, no discard


def test_kart_navigates_to_offset_target():
    g = grid.load_grid(YAML)
    ctrl = FakeController()
    # tracker parks on Standard Kart twice, then Plushbuggy — _park_on_kart steps RIGHT
    # one parked read at a time until it's on the target, then capture proceeds.
    client = FakeScoreClient(selection=[{"kart": "Standard Kart"},
                                        {"kart": "Standard Kart"},
                                        {"kart": "Plushbuggy"}],
                             scores={"KART_SELECT": [False, True]})
    SweepRunner(g, ctrl, client, idle_seconds=0.0, settle_seconds=0.0,
                ground_timeout=0.0, screen_timeout=0.0).capture_kart("mario__base", "plushbuggy")
    assert len([b for (_, b) in ctrl.log if b == "DPAD_RIGHT"]) >= 1              # stepped toward target
    assert {"type": "at_record_clip_mark", "event": "flourish"} in client.sent    # reached capture


def test_verify_on_costume_retries_until_right_costume():
    """name matches but the wrong costume is shown → re-press until the right costume lands."""
    g = grid.load_grid(YAML)
    ctrl = FakeController()
    client = FakeClient(selection=[{"character": "Mario", "costume": "Base"},      # wrong costume
                                   {"character": "Mario", "costume": "Touring"}])  # right
    SweepRunner(g, ctrl, client, idle_seconds=0.0, settle_seconds=0.0,
                ground_timeout=0.0).verify_on("mario__touring", "characters")
    assert len([b for (_, b) in ctrl.log if b == "DPAD_RIGHT"]) >= 1
    assert len(_selections(client)) == 2


def test_verify_on_base_grounds_on_character_only():
    """mario__base grounds as soon as the tracker reports character=Mario (costume ignored)."""
    g = grid.load_grid(YAML)
    ctrl = FakeController()
    client = FakeClient(selection={"character": "Mario", "costume": "Base"})
    SweepRunner(g, ctrl, client, idle_seconds=0.0, settle_seconds=0.0,
                ground_timeout=0.0).verify_on("mario__base", "characters")
    assert len([b for (_, b) in ctrl.log if b == "DPAD_RIGHT"]) == 0   # grounded on first check
    assert len(_selections(client)) == 1


class FakeScreenClient(FakeClient):
    """FakeClient whose at_current_screen reports a fixed screen (so _return_to lands)."""
    def __init__(self, screen="CHARACTER_SELECT", **kw):
        super().__init__(**kw)
        self._screen_name = screen
    def send(self, msg):
        if msg.get("type") == "at_current_screen":
            return {"type": "current_screen", "screen": self._screen_name}
        return super().send(msg)


def test_sweep_karts_pauses_at_next_kart_and_returns_to_anchor():
    g = grid.load_grid(YAML)
    ctrl, client = FakeController(), FakeScreenClient("CHARACTER_SELECT")
    calls = [0]
    def stop_check():
        calls[0] += 1
        return calls[0] > 1                       # allow one kart, pause before the second
    r = SweepRunner(g, ctrl, client, idle_seconds=0.0, ground_timeout=0.0, stop_check=stop_check)
    captured = []
    r.capture_kart = lambda combo, kart: captured.append(kart)   # stub hardware
    assert r.sweep_karts("mario__base") is True   # paused
    assert len(captured) == 1
    assert ("press", "B") in ctrl.log             # returned to CHARACTER_SELECT anchor


def test_sweep_karts_completes_when_not_paused():
    g = grid.load_grid(YAML)
    ctrl, client = FakeController(), FakeScreenClient("CHARACTER_SELECT")
    r = SweepRunner(g, ctrl, client, idle_seconds=0.0, ground_timeout=0.0, stop_check=lambda: False)
    captured = []
    r.capture_kart = lambda combo, kart: captured.append(kart)
    assert r.sweep_karts("mario__base") is False  # completed
    assert len(captured) == len(list(g.cells("karts")))
    assert ("press", "B") in ctrl.log             # anchor return at end of row


class FakeScoreClient(FakeClient):
    """Answers at_screen_score with a per-screen `detected` sequence (last value repeats),
    modelling the post-kart intermediary screen: the KART_SELECT tell scores ~0 (detected
    False) while sitting on it, then scores (True) once B returns to the real KART_SELECT."""
    def __init__(self, scores=None, **kw):
        super().__init__(**kw)
        self._scores = scores or {}
        self._idx = {}
    def send(self, msg):
        if msg.get("type") == "at_screen_score":
            self.sent.append(msg)
            name = msg.get("screen")
            seq = self._scores.get(name, [True])
            i = self._idx.get(name, 0)
            det = bool(seq[min(i, len(seq) - 1)])
            self._idx[name] = i + 1
            return {"type": "screen_score", "screen": name,
                    "score": 1.0 if det else 0.0, "detected": det}
        return super().send(msg)


def test_return_to_confirms_by_tell_score_not_held_screen():
    """The post-kart intermediary screen makes the detector HOLD KART_SELECT; _return_to must
    keep pressing B until the KART_SELECT tell actually SCORES, not stop at the held name.
    Modelled as detected=False for the first reads (intermediary), then True (real return)."""
    g = grid.load_grid(YAML)
    ctrl = FakeController()
    client = FakeScoreClient(scores={"KART_SELECT": [False, False, False, True]})
    SweepRunner(g, ctrl, client, idle_seconds=0.0, screen_timeout=0.02)._return_to("KART_SELECT", "test")
    assert len([b for (_, b) in ctrl.log if b == "B"]) >= 2   # didn't false-succeed on the held name


def test_return_to_raises_when_tell_never_scores():
    """If the target tell never scores (we never actually leave the intermediary), _return_to
    presses its full B budget then raises — vs the old silent false-success on the held name."""
    import pytest
    g = grid.load_grid(YAML)
    ctrl = FakeController()
    client = FakeScoreClient(scores={"KART_SELECT": [False]})
    with pytest.raises(RuntimeError, match="never reached"):
        SweepRunner(g, ctrl, client, idle_seconds=0.0, screen_timeout=0.02)._return_to("KART_SELECT", "x")
    assert len([b for (_, b) in ctrl.log if b == "B"]) == 6


def test_kart_flourish_refires_A_when_not_departed():
    """kart -> not-kart departure: if KART_SELECT still SCORES after the flourish (the A was
    eaten), capture_kart re-fires A until kart_select stops scoring, then returns."""
    g = grid.load_grid(YAML)
    ctrl = FakeController()
    # KART_SELECT: still present right after the flourish (eaten), then departs, then scores
    # again on the B return.
    client = FakeScoreClient(selection={"kart": "Plushbuggy"},
                             scores={"KART_SELECT": [True, False, True]})
    SweepRunner(g, ctrl, client, idle_seconds=0.0, settle_seconds=0.0,
                ground_timeout=0.0, screen_timeout=0.0).capture_kart("mario__base", "plushbuggy")
    assert len([b for (_, b) in ctrl.log if b == "A"]) >= 2   # flourish A + at least one re-fire


def test_sample_grid_reproducible_and_valid():
    """--sample mode: pick N distinct base-costume characters + M distinct karts, reproducibly."""
    g = grid.load_grid(YAML)
    chars1, karts1 = sample_grid(g, 5, 3, seed=0)
    chars2, karts2 = sample_grid(g, 5, 3, seed=0)
    assert (chars1, karts1) == (chars2, karts2)            # same seed -> same draw
    assert len(set(chars1)) == 5 and len(set(karts1)) == 3
    assert all(c.endswith("__base") for c in chars1)       # base-costume characters
    all_karts = {c.slug for c in g.cells("karts")}
    assert set(karts1) <= all_karts


def test_sweep_karts_respects_kart_subset():
    """--sample mode records only the M sampled karts per character, in order."""
    g = grid.load_grid(YAML)
    ctrl, client = FakeController(), FakeClient()
    r = SweepRunner(g, ctrl, client, idle_seconds=0.0, ground_timeout=0.0, screen_timeout=0.0)
    captured = []
    r.capture_kart = lambda combo, kart: captured.append(kart)
    assert r.sweep_karts("mario__base", karts=["plushbuggy", "standard_kart"]) is False
    assert captured == ["plushbuggy", "standard_kart"]
