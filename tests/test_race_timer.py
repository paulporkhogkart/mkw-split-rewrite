import json

from mkw_tracker.race.timer import RaceTimer
from mkw_tracker.ipc.protocol import emit_race_time


def _t():
    # templates={} skips PNG loading; these tests drive step() with scripted reads.
    return RaceTimer(templates={}, resync_interval=0.5, tolerance_ms=300, forward_confirm=3)


def test_starts_on_first_nonzero_read():
    t = _t()
    assert t.step(None, 0.0, racing=True) is None     # nothing read yet
    assert t.step(0, 0.1, racing=True) is None         # still 0:00.000 -> not started
    assert t.step(1234, 0.2, racing=True) == 1234       # first >0 read anchors
    assert t.running is True


def test_counts_locally_between_reads():
    t = _t()
    t.step(1000, 10.0, racing=True)                     # anchor 1000ms @ 10.0s
    assert t.step(None, 10.5, racing=True) == 1500      # +0.5s
    assert t.step(None, 11.0, racing=True) == 2000


def test_backward_read_is_ignored_lap_flash():
    t = _t()
    t.step(1000, 10.0, racing=True)
    # 0.5s later the local estimate is ~1500; a lap-split flash reads far lower
    assert t.step(420, 10.5, racing=True) == 1500       # ignored, keeps counting
    assert t.step(None, 11.0, racing=True) == 2000


def test_within_tolerance_reanchors_drift():
    t = _t()
    t.step(1000, 10.0, racing=True)
    # estimate ~1500 @10.5; a read 1700 is within 300ms -> re-anchor up to 1700
    assert t.step(1700, 10.5, racing=True) == 1700
    assert t.step(None, 10.6, racing=True) == 1800      # counts from the new anchor


def test_forward_jump_needs_confirmation():
    t = _t()
    t.step(1000, 10.0, racing=True)                     # estimate stays 1000 @10.0
    assert t.step(9000, 10.0, racing=True) == 1000      # 1st big forward read ignored
    assert t.step(9000, 10.0, racing=True) == 1000      # 2nd, still ignored
    assert t.step(9000, 10.0, racing=True) == 9000      # 3rd confirms -> snap


def test_pause_freezes_and_resume_rebaselines():
    t = _t()
    t.step(1000, 10.0, racing=True)
    assert t.step(None, 12.0, racing=True) == 3000      # counting: +2s
    assert t.step(None, 12.0, racing=False) == 3000     # pause -> freeze
    assert t.step(None, 20.0, racing=False) == 3000     # 8s paused, no advance
    assert t.step(None, 20.0, racing=True) == 3000      # resume -> re-baseline, no jump
    assert t.step(None, 20.5, racing=True) == 3500      # counts from frozen value


def test_reset_clears_state():
    t = _t()
    t.step(1234, 10.0, racing=True)
    t.reset()
    assert t.running is False
    assert t.step(None, 11.0, racing=True) is None


def test_emit_race_time_json():
    assert json.loads(emit_race_time(5000)) == {"type": "race_time", "elapsed_ms": 5000}
    assert json.loads(emit_race_time(None)) == {"type": "race_time", "elapsed_ms": None}
