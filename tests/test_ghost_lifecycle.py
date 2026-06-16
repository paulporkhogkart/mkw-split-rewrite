import json
import pytest
from mkw_tracker.lifecycle.race import RaceLifecycle
from mkw_tracker.lifecycle.ghost import GhostState
from mkw_tracker.detection.screen import Screen


@pytest.fixture(autouse=True)
def _stub_minimap_db(monkeypatch):
    # _start_race calls these DB lookups; stub to None so a fresh start seeds nothing
    # and the lifecycle tests never touch the real SQLite store.
    import mkw_tracker.lifecycle.race as race_mod
    monkeypatch.setattr(race_mod, "get_minimap_roi", lambda *a, **k: None)
    monkeypatch.setattr(race_mod, "get_minimap_seed", lambda *a, **k: None)


class _Stub:
    """Minimal stand-in for every tracker the lifecycle resets/reads."""
    def __init__(self): self.reset_calls = 0
    def reset(self): self.reset_calls += 1
    def calibrate_from_race(self): return 0.5
    def set_roi(self, *a, **k): pass
    def start(self): pass


class _Sel(_Stub):
    class _S:
        course = "Choco Mountain"; character = "Mario"; kart = "K"; costume = "Base"
    state = _S()


class _Laps(_Stub):
    class _S:
        total_laps = 3; current_lap = 3
    state = _S()


class _Ts(_Stub):
    total_time = None
    splits = {}


class _Mm(_Stub):
    _calibrated = False
    _badge = None
    def seed(self, *a, **k): pass


class _Rec(_Stub):
    points = []


class _Ipc:
    def __init__(self): self.events = []
    def emit(self, e): self.events.append(e)


def _make(ipc=None):
    sel, laps, coins, ts = _Sel(), _Laps(), _Stub(), _Ts()
    finish, mush, mm, rec = _Stub(), _Stub(), _Mm(), _Rec()
    lc = RaceLifecycle(selection=sel, laps=laps, coins=coins, ts=ts, finish=finish,
                       mush=mush, minimap=mm, mm_rec=rec, ipc=ipc)
    return lc


def test_effective_screen_only_remaps_ghost_while_recording():
    lc = _make()
    assert lc.effective_screen(Screen.GHOST) == Screen.GHOST       # not recording
    lc.arm_ghost()
    assert lc.effective_screen(Screen.GHOST) == Screen.GHOST       # armed, not recording
    lc.on_screen_change(Screen.START_REPLAY, Screen.GHOST)         # provisional start
    assert lc.effective_screen(Screen.GHOST) == Screen.RACING      # recording
    assert lc.effective_screen(Screen.RACING) == Screen.RACING     # real racing untouched


def test_real_race_while_armed_is_not_a_ghost_recording():
    lc = _make()
    lc.arm_ghost()
    lc.on_screen_change(Screen.START_TIME_TRIAL, Screen.RACING)    # a REAL race
    assert lc.effective_screen(Screen.GHOST) == Screen.GHOST       # ghost not recording
    assert lc._ghost.state == GhostState.ARMED                     # still armed


def test_ghost_abort_discards_and_stays_armed_without_emitting_run():
    ipc = _Ipc(); lc = _make(ipc)
    lc.arm_ghost()
    lc.on_screen_change(Screen.GHOST_RESET, Screen.GHOST)          # start
    lc.on_screen_change(Screen.GHOST, Screen.REPLAY_MENU)          # abort before finish
    assert lc._ghost.state == GhostState.ARMED
    assert not any(json.loads(e).get("type") == "run_finalized" for e in ipc.events)


def test_validate_resume_discards_provisional_capture():
    lc = _make()
    lc.arm_ghost()
    lc.on_screen_change(Screen.REPLAY_MENU, Screen.GHOST)          # ambiguous origin
    lc.validate_ghost_start(8000)                                  # advanced clock => resume
    assert lc.effective_screen(Screen.GHOST) == Screen.GHOST       # reverted
    assert lc._ghost.state == GhostState.ARMED


def test_ghost_finalize_tags_source_nulls_identity_keeps_course_and_disarms():
    ipc = _Ipc(); lc = _make(ipc)
    lc.arm_ghost()
    lc.on_screen_change(Screen.GHOST_RESET, Screen.GHOST)
    lc.validate_ghost_start(None)
    lc._ts.total_time = "1:40.000"                                 # finish locked
    lc.finalize_on_finish()
    finals = [json.loads(e) for e in ipc.events if '"run_finalized"' in e]
    assert len(finals) == 1
    r = finals[0]
    assert r["source"] == "ghost"
    assert r["course"] == "Choco Mountain"
    assert r["character"] is None and r["kart"] is None and r["costume"] is None
    assert not lc.ghost_armed                                      # auto-disarmed
    states = [json.loads(e) for e in ipc.events if '"ghost_import_state"' in e]
    assert states[-1] == {"type": "ghost_import_state", "armed": False, "recording": False}


def test_arming_mid_ghost_does_not_record_until_a_fresh_start():
    # Spec: arming while already watching a ghost must NOT record - there was no
    # fresh into-GHOST transition to catch the start; wait for the next one.
    lc = _make()
    lc.arm_ghost()
    lc.on_screen_change(Screen.GHOST, Screen.REPLAY_MENU)          # leaving, never entered
    assert not lc.ghost_recording
    assert lc._ghost.state == GhostState.ARMED
    lc.on_screen_change(Screen.GHOST_RESET, Screen.GHOST)          # a genuine fresh start
    assert lc.ghost_recording
