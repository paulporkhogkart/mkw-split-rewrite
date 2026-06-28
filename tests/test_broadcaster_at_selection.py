"""at_current_selection must return the COMMITTED costume, not a stale score-map candidate.

On a no-costume character the tracker's costume-detection block is skipped, so _costume_scores
keeps the PREVIOUS character's costume high. The sweep's char nav keys on (char, costume); if the
read leaks that stale costume it builds a phantom '<char>__<old-costume>' that isn't a real grid
cell, and nav gets stuck (the hardware 'cheep_cheep__touring' failure)."""
from mkw_tracker.ipc.broadcaster import EventBroadcaster


class _State:
    character = "Mario"
    costume = None        # base / no-costume char: committed costume is None
    kart = None
    course = None


class _Tracker:
    def __init__(self):
        self.state = _State()
        # stale costume score lingering from a previous character (detection didn't run here):
        self.score_maps = {"costume": [{"name": "touring", "score": 0.9}]}


def test_at_current_selection_costume_is_committed_not_stale_score():
    b = EventBroadcaster(port=0)
    b._at_tracker = _Tracker()
    resp = b._handle_at_command({"type": "at_current_selection"})
    assert resp["type"] == "current_selection"
    assert resp["character"] == "Mario"
    assert resp["costume"] is None        # committed None — NOT the stale 'touring' from the score map
