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
    # tracker already shows the right kart; we're on KART_SELECT throughout and clip_done carries
    # no error (the flourish took), so the clip is kept.
    client = FakeScoreClient(selection={"kart": "Plushbuggy"}, scores={"KART_SELECT": [True]})
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
                             scores={"KART_SELECT": [True]})
    SweepRunner(g, ctrl, client, idle_seconds=0.0, settle_seconds=0.0,
                ground_timeout=0.0, screen_timeout=0.0).capture_kart("mario__base", "plushbuggy")
    assert len([b for (_, b) in ctrl.log if b == "DPAD_RIGHT"]) >= 1              # stepped toward target
    assert {"type": "at_record_clip_mark", "event": "flourish"} in client.sent    # reached capture


def test_park_on_char_navigates_to_target():
    """Closed-loop char nav steps toward the target cell and stops on it (overshoot/undershoot safe)."""
    g = grid.load_grid(YAML)
    ctrl = FakeController()
    # parked on Luigi, then on the target Mario after a step — it steps toward Mario and stops.
    client = FakeClient(selection=[{"character": "Luigi", "costume": "Base"},
                                   {"character": "Luigi", "costume": "Base"},
                                   {"character": "Mario", "costume": "Base"}])
    SweepRunner(g, ctrl, client, ground_timeout=0.0)._park_on_char("mario__base")
    assert any(b.startswith("DPAD") for (_, b) in ctrl.log)            # stepped toward the target
    assert not any(b in ("A", "B") for (_, b) in ctrl.log)            # nav only, never selects


def test_park_on_char_repolls_on_invalid_read_not_blind_nudge():
    """A transient invalid (char,costume) read (e.g. a costume LAGGED from the previous cell, like
    'baby_mario__touring' — baby_mario has no touring variant) must NOT trigger a blind DPAD_RIGHT;
    re-poll until a real cell. This was the drift/oscillation bug."""
    g = grid.load_grid(YAML)
    ctrl = FakeController()
    client = FakeClient(selection=[{"character": "Baby Mario", "costume": "Touring"},  # invalid lag
                                   {"character": "Baby Mario", "costume": "Base"}])      # settled, valid
    SweepRunner(g, ctrl, client, ground_timeout=0.0, nav_settle=0.0)._park_on_char("baby_mario__base")
    assert not any(b == "DPAD_RIGHT" for (_, b) in ctrl.log)   # re-polled to the valid cell; no nudge


def test_park_on_char_treats_empty_costume_as_base():
    """The tracker reports costume=None for a BASE character (base = no costume banner), but the grid
    cell is '<char>__base'. _park_on_char must read empty costume AS base, not get stuck on
    '<char>__' (the 'stuck reading an unrecognised cell mario__' failure seen on hardware)."""
    g = grid.load_grid(YAML)
    ctrl = FakeController()
    client = FakeClient(selection={"character": "Baby Mario", "costume": None})   # base char, costume None
    SweepRunner(g, ctrl, client, ground_timeout=0.0, nav_settle=0.0)._park_on_char("baby_mario__base")
    assert not any(b.startswith("DPAD") for (_, b) in ctrl.log)   # already on target (empty == base)


def test_park_on_char_is_costume_aware():
    """A different COSTUME of the same character is a different cell — keep navigating, don't stop."""
    g = grid.load_grid(YAML)
    ctrl = FakeController()
    # on Mario (Base), target Mario (Touring): same character, different costume cell.
    client = FakeClient(selection=[{"character": "Mario", "costume": "Base"},
                                   {"character": "Mario", "costume": "Base"},
                                   {"character": "Mario", "costume": "Touring"}])
    SweepRunner(g, ctrl, client, ground_timeout=0.0)._park_on_char("mario__touring")
    assert any(b.startswith("DPAD") for (_, b) in ctrl.log)            # didn't false-stop on mario__base


class GridNavSim:
    """Position-aware fake (controller + client in one): models a cursor on the grid so DPAD
    presses actually MOVE it, and at_current_selection reports the cell under the cursor. Lets a
    test prove _park_on can traverse a full-width row — the bug where a 30-step budget stranded the
    cursor mid-row (donkey_kong col 7 -> dolphin col 39 = 32 presses > 30)."""
    _D = {"DPAD_RIGHT": (0, 1), "DPAD_LEFT": (0, -1), "DPAD_DOWN": (1, 0), "DPAD_UP": (-1, 0)}

    def __init__(self, g, start_slug, category="characters"):
        self.cells = {c.coord: c for c in g.cells(category)}
        self.r, self.c = g.coord_of(start_slug)
        self.log = []

    # controller interface
    def press(self, b, duration=0.1):
        self.log.append(("press", b))
        dr, dc = self._D.get(b, (0, 0))
        if (self.r + dr, self.c + dc) in self.cells:        # stay on the grid (clamp at edges)
            self.r, self.c = self.r + dr, self.c + dc

    def hold(self, *a): pass
    def rstick_down(self, *a): pass

    def cur_slug(self):
        return self.cells[(self.r, self.c)].slug

    # client interface
    def send(self, msg):
        if msg["type"] == "at_current_selection":
            char, cost = self.cur_slug().split("__", 1)      # '<char>__<costume>'; base costume = None
            return {"type": "current_selection", "course": None, "character": char,
                    "costume": None if cost == "base" else cost, "kart": None}
        if msg["type"] == "at_screen_score":                 # nav happens on the expected screen
            return {"type": "screen_score", "screen": msg.get("screen"), "score": 1.0, "detected": True}
        return {"type": "ok"}

    def wait_for(self, type_):
        return {"type": "clip_done", "item": "x", "events": {}}


def test_park_on_char_crosses_full_width_row():
    """Regression: _park_on's step budget must exceed the widest grid row. donkey_kong (row 2,
    col 7) -> dolphin (row 2, col 39) is 32 DPAD_RIGHT presses; the old 30-step cap
    (MAX_VERIFY_ATTEMPTS) ran out on daisy__swimwear (col 36) and raised 'never reached'."""
    g = grid.load_grid(YAML)
    sim = GridNavSim(g, "donkey_kong__base")
    SweepRunner(g, sim, sim, ground_timeout=0.0, nav_settle=0.0)._park_on_char("dolphin__base")
    assert sim.cur_slug() == "dolphin__base"                # reached the far target, didn't strand


def test_sweep_karts_pauses_at_next_kart_and_returns_to_anchor():
    g = grid.load_grid(YAML)
    ctrl = FakeController()
    client = FakeScoreClient(scores={"KART_SELECT": [True], "CHARACTER_SELECT": [False, True]})
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
    ctrl = FakeController()
    client = FakeScoreClient(scores={"KART_SELECT": [True], "CHARACTER_SELECT": [False, True]})
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


def test_park_on_hard_fails_off_screen_instead_of_stampeding():
    """THE bug: a dropped flourish stranded the sweep on CHARACTER_SELECT, but the committed kart
    PERSISTS across screens, so _park_on_kart read the stale kart and spammed DPAD_RIGHT across the
    character roster (28 presses) until the step budget ran out. The screen guard must hard-fail the
    instant the expected screen's tell isn't scoring — pressing NO d-pad."""
    import pytest
    g = grid.load_grid(YAML)
    ctrl = FakeController()
    # tracker still reports a (stale, committed) kart, but the KART_SELECT tell is NOT scoring.
    client = FakeScoreClient(selection={"kart": "Funky Dorrie"}, scores={"KART_SELECT": [False]})
    with pytest.raises(RuntimeError, match="not scoring"):
        SweepRunner(g, ctrl, client, ground_timeout=0.0, nav_settle=0.0,
                    screen_timeout=0.0)._park_on_kart("junkyard_hog")
    assert not any(b.startswith("DPAD") for (_, b) in ctrl.log)   # no stampede — bailed before pressing


class FakeErrorClient(FakeClient):
    """clip_done carries an `error` (the recorder discarded a flourish-less clip) per the `errors`
    sequence (last value repeats). at_screen_score reports we're on KART_SELECT (True, via FakeClient),
    since an eaten flourish never leaves it."""
    def __init__(self, errors=None, **kw):
        super().__init__(**kw)
        self._errors = errors if errors is not None else [True]   # default: every clip_done errors
        self._ei = 0

    def wait_for(self, type_):
        err = self._errors[min(self._ei, len(self._errors) - 1)]
        self._ei += 1
        msg = {"type": "clip_done", "item": "x", "events": {"fps": 60}}
        if err:
            msg["error"] = "flourish not registered"
        return msg


def test_kart_flourish_failure_retries_then_hard_fails():
    """A persistently-eaten flourish (every clip_done carries an error — the recorder keeps
    discarding the flourish-less clip) re-records a BOUNDED number of times, then HARD-FAILS the run
    rather than skipping or stampeding (the chosen recovery policy)."""
    import pytest
    g = grid.load_grid(YAML)
    ctrl = FakeController()
    client = FakeErrorClient(selection={"kart": "Plushbuggy"})        # default: always errors
    with pytest.raises(RuntimeError, match="flourish never registered"):
        SweepRunner(g, ctrl, client, idle_seconds=0.0, settle_seconds=0.0,
                    ground_timeout=0.0, screen_timeout=0.0).capture_kart("mario__base", "plushbuggy")
    assert len([b for (_, b) in ctrl.log if b == "A"]) == SweepRunner.FLOURISH_MAX_ATTEMPTS  # bounded


def test_kart_flourish_recovers_after_one_eaten_A():
    """A single eaten flourish is recovered: the recorder discards the bad clip (clip_done.error
    once), capture_kart re-records, and the retry succeeds — clip kept, no hard-fail."""
    g = grid.load_grid(YAML)
    ctrl = FakeController()
    client = FakeErrorClient(selection={"kart": "Plushbuggy"}, errors=[True, False])
    SweepRunner(g, ctrl, client, idle_seconds=0.0, settle_seconds=0.0,
                ground_timeout=0.0, screen_timeout=0.0).capture_kart("mario__base", "plushbuggy")
    assert len([b for (_, b) in ctrl.log if b == "A"]) == 2          # one eaten + one that took
    begins = [m for m in client.sent if m.get("type") == "at_record_clip_begin"]
    assert len(begins) == 2                                          # re-recorded the same item


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


def test_sample_grid_accepts_explicit_slugs_including_costume():
    """--sample can name exact cells (incl a costume) instead of a random count."""
    g = grid.load_grid(YAML)
    chars, karts = sample_grid(g, "mario__touring,baby_mario__base", "standard_kart", seed=0)
    assert chars == ["mario__touring", "baby_mario__base"]   # explicit, order preserved, costume kept
    assert karts == ["standard_kart"]
    assert "mario__touring" in {c.slug for c in g.cells("characters")}   # a costume cell is valid


def test_sample_grid_rejects_unknown_slug():
    import pytest
    g = grid.load_grid(YAML)
    with pytest.raises(ValueError):
        sample_grid(g, "not_a_real_character__base", "3", seed=0)


def test_sample_grid_all_karts():
    """'all' expands to every kart cell (dark BD-base run = one char × every kart)."""
    g = grid.load_grid(YAML)
    chars, karts = sample_grid(g, "baby_daisy__base", "all", seed=0)
    assert chars == ["baby_daisy__base"]
    assert karts == [c.slug for c in g.cells("karts")] and len(karts) > 3


def test_sweep_karts_respects_kart_subset():
    """--sample mode records only the M sampled karts per character, in order."""
    g = grid.load_grid(YAML)
    ctrl, client = FakeController(), FakeClient()
    r = SweepRunner(g, ctrl, client, idle_seconds=0.0, ground_timeout=0.0, screen_timeout=0.0)
    captured = []
    r.capture_kart = lambda combo, kart: captured.append(kart)
    assert r.sweep_karts("mario__base", karts=["plushbuggy", "standard_kart"]) is False
    assert captured == ["plushbuggy", "standard_kart"]
