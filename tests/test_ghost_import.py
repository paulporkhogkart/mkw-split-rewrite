from mkw_tracker.lifecycle.ghost import GhostImport, GhostState
from mkw_tracker.detection.screen import Screen


# ── Task 2: emit_ghost_import_state ────────────────────────────────────────

import json
from mkw_tracker.ipc.protocol import emit_ghost_import_state


def test_emit_ghost_import_state_shape():
    msg = json.loads(emit_ghost_import_state(True, False))
    assert msg == {"type": "ghost_import_state", "armed": True, "recording": False}


def test_starts_idle_disarmed():
    g = GhostImport()
    assert g.state == GhostState.IDLE
    assert not g.armed and not g.recording


def test_arm_then_disarm():
    g = GhostImport()
    g.arm()
    assert g.state == GhostState.ARMED and g.armed and not g.recording
    g.disarm()
    assert g.state == GhostState.IDLE and not g.armed


def test_enter_ghost_when_armed_starts_provisional_recording():
    g = GhostImport(); g.arm()
    started = g.on_ghost_enter(Screen.START_REPLAY)
    assert started is True
    assert g.state == GhostState.RECORDING and g.recording


def test_enter_ghost_when_idle_does_not_start():
    g = GhostImport()
    assert g.on_ghost_enter(Screen.GHOST_RESET) is False
    assert g.state == GhostState.IDLE


def test_fresh_origin_confirms_immediately_regardless_of_clock():
    # GHOST_RESET / START_REPLAY origin == a reload happened == fresh start.
    for origin in (Screen.GHOST_RESET, Screen.START_REPLAY):
        g = GhostImport(); g.arm(); g.on_ghost_enter(origin)
        # Even a large clock can't flip a fresh origin to a resume.
        assert g.validate(99999) is True
        assert g.recording


def test_replay_menu_origin_with_advanced_clock_is_resume():
    g = GhostImport(); g.arm(); g.on_ghost_enter(Screen.REPLAY_MENU)
    assert g.validate(8000) is False          # advanced clock => resume
    assert g.state == GhostState.ARMED and not g.recording


def test_replay_menu_origin_with_near_zero_clock_is_fresh():
    # A restart whose brief GHOST_RESET was missed: REPLAY_MENU origin but the
    # countdown is witnessed (clock <= START_ZERO_MS).
    g = GhostImport(); g.arm(); g.on_ghost_enter(Screen.REPLAY_MENU)
    assert g.validate(300) is True
    assert g.recording


def test_replay_menu_origin_countdown_then_window_elapses_is_fresh():
    g = GhostImport(); g.arm(); g.on_ghost_enter(Screen.REPLAY_MENU)
    # Clock not running yet (None) for the whole window => fresh start.
    res = None
    for _ in range(GhostImport.VALIDATE_FRAMES + 1):
        res = g.validate(None)
    assert res is True and g.recording


def test_leave_ghost_while_recording_returns_true_and_rearms():
    g = GhostImport(); g.arm(); g.on_ghost_enter(Screen.START_REPLAY)
    assert g.on_ghost_leave() is True
    assert g.state == GhostState.ARMED


def test_leave_ghost_when_not_recording_returns_false():
    g = GhostImport(); g.arm()
    assert g.on_ghost_leave() is False


def test_disarm_clears_recording():
    g = GhostImport(); g.arm(); g.on_ghost_enter(Screen.START_REPLAY)
    g.disarm()
    assert g.state == GhostState.IDLE and not g.recording
