"""Entering NO_SIGNAL is an app-restart-style teardown: discard the active run
WITHOUT finalizing it, clear selections, and disarm any ghost capture."""
import json
import pytest
from mkw_tracker.lifecycle.race import RaceLifecycle
from mkw_tracker.detection.screen import Screen


@pytest.fixture(autouse=True)
def _stub_minimap_db(monkeypatch):
    import mkw_tracker.lifecycle.race as race_mod
    monkeypatch.setattr(race_mod, "get_minimap_roi", lambda *a, **k: None)
    monkeypatch.setattr(race_mod, "get_minimap_seed", lambda *a, **k: None)


class _Stub:
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
        total_laps = 3; current_lap = 2
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
    return RaceLifecycle(selection=_Sel(), laps=_Laps(), coins=_Stub(), ts=_Ts(),
                         finish=_Stub(), mush=_Stub(), minimap=_Mm(), mm_rec=_Rec(), ipc=ipc)


def test_no_signal_from_racing_discards_without_finalizing():
    ipc = _Ipc(); lc = _make(ipc)
    lc.on_screen_change(Screen.START_TIME_TRIAL, Screen.RACING)   # start a race
    lc.on_screen_change(Screen.RACING, Screen.NO_SIGNAL)          # signal drops mid-race
    types = [json.loads(e).get("type") for e in ipc.events]
    assert "race_cleared" in types
    assert "run_finalized" not in types          # silently discarded, not queued for review
    assert lc._selection.reset_calls >= 1        # selections cleared


def test_no_signal_disarms_active_ghost():
    ipc = _Ipc(); lc = _make(ipc)
    lc.arm_ghost()
    lc.on_screen_change(Screen.GHOST_RESET, Screen.GHOST)         # recording
    assert lc.ghost_recording
    lc.on_screen_change(Screen.GHOST, Screen.NO_SIGNAL)
    assert not lc.ghost_armed and not lc.ghost_recording
