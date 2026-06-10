"""RaceLifecycle finalize path uses the FinishStillDetector API.

The lifecycle is given a FinishStillDetector (frozen-timer / final-time detector),
which exposes `.detected` and has NO `.state`. These tests cover the two screen
transitions that previously hit the stale `self._finish.state.*` references and
raised AttributeError. (Playback now also stops at final-time detection in the
main loop; recording append already halts there via the _race_complete gate.)
"""
from unittest.mock import MagicMock

from mkw_tracker.detection.screen import Screen
from mkw_tracker.race.finish import FinishStillDetector
from mkw_tracker.lifecycle.race import RaceLifecycle


def _make_lifecycle(ts_total_time=None):
    ts = MagicMock()
    ts.total_time = ts_total_time
    minimap = MagicMock()
    minimap._calibrated = True            # skip the calibrate-on-finish branch
    return RaceLifecycle(
        selection=MagicMock(), laps=MagicMock(), coins=MagicMock(), ts=ts,
        finish=FinishStillDetector(), mush=MagicMock(), minimap=minimap,
        mm_rec=MagicMock(),
    )


def test_leaving_racing_to_menu_finalizes_without_crash():
    # Previously raised on `self._finish.state.detected`.
    lc = _make_lifecycle()
    lc.on_screen_change(Screen.RACING, Screen.MAIN_MENU)
    lc._laps.reset.assert_called()   # finalize + clear ran without crashing


def test_finish_to_post_without_timestamp_does_not_crash():
    # Previously raised on `self._finish.state.total_time` when ts.total_time was None.
    lc = _make_lifecycle(ts_total_time=None)
    lc.on_screen_change(Screen.RACING, Screen.POST_TIME_TRIAL)
    lc._laps.reset.assert_called()   # finalize + clear ran without crashing


def test_clear_race_state_resets_the_race_timer():
    ts = MagicMock(); ts.total_time = None
    minimap = MagicMock(); minimap._calibrated = True
    timer = MagicMock()
    lc = RaceLifecycle(
        selection=MagicMock(), laps=MagicMock(), coins=MagicMock(), ts=ts,
        finish=FinishStillDetector(), mush=MagicMock(), minimap=minimap,
        mm_rec=MagicMock(), timer=timer,
    )
    lc.on_screen_change(Screen.RACING, Screen.MAIN_MENU)   # finalize + clear
    timer.reset.assert_called()
