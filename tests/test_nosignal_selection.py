"""SelectionTracker.reset() clears all selections (used on NO_SIGNAL teardown)."""
from mkw_tracker.detection.selection import SelectionTracker


def test_selection_reset_clears_all_fields(memdb):
    t = SelectionTracker(switch2_language="en_uk")
    t.state.character = "Mario";  t.state.character_conf = 0.9
    t.state.costume = "Touring";  t.state.costume_conf = 0.8
    t.state.kart = "Tiny Titan";  t.state.kart_conf = 0.7
    t.state.course = "Rainbow Road"; t.state.course_conf = 0.6
    t._costume_loss_streak = 3

    t.reset()

    assert t.state.character is None and t.state.character_conf == 0.0
    assert t.state.costume is None and t.state.costume_conf == 0.0
    assert t.state.kart is None and t.state.kart_conf == 0.0
    assert t.state.course is None and t.state.course_conf == 0.0
    assert t._costume_loss_streak == 0
