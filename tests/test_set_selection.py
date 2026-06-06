from unittest.mock import MagicMock

from mkw_tracker.main import _handle_ipc_command
from mkw_tracker.detection.selection import SelectionTracker, SelectionState


def _tracker(state=None):
    # __new__ skips __init__ (no template/image load); we only need .state.
    t = SelectionTracker.__new__(SelectionTracker)
    t.state = state or SelectionState()
    return t


def _dispatch(msg, tracker):
    # _handle_ipc_command only touches `tracker` for set_selection; the rest are stubs.
    _handle_ipc_command(msg, MagicMock(), MagicMock(), MagicMock(), MagicMock(),
                        MagicMock(), [True], None, [None], [False], tracker)


def test_set_selection_sets_all_fields_and_confidence():
    t = _tracker()
    _dispatch({"type": "set_selection", "course": "Rainbow Road",
               "character": "Mario", "kart": "Pipe Frame", "costume": "Aero"}, t)
    assert t.state.course == "Rainbow Road"
    assert t.state.character == "Mario"
    assert t.state.kart == "Pipe Frame"
    assert t.state.costume == "Aero"
    assert t.state.course_conf == 1.0
    assert t.state.character_conf == 1.0
    assert t.state.kart_conf == 1.0
    assert t.state.costume_conf == 1.0


def test_set_selection_ignores_missing_and_null_fields():
    t = _tracker(SelectionState(character="Luigi", character_conf=0.5))
    _dispatch({"type": "set_selection", "course": "DK Pass", "character": None}, t)
    assert t.state.course == "DK Pass"          # set
    assert t.state.character == "Luigi"         # null in msg -> left unchanged
    assert t.state.character_conf == 0.5
    assert t.state.kart is None                 # absent in msg -> untouched


def test_set_selection_no_tracker_is_noop():
    # Must not raise when tracker is None (e.g. very early startup).
    _dispatch({"type": "set_selection", "course": "X"}, None)
