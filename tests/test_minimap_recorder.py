"""MinimapRecorder: race-clock stamping, GO backfill, monotonic guard."""
from mkw_tracker.minimap.recorder import MinimapRecorder
from mkw_tracker.minimap.tracker import MinimapState


def mm(cx=1700.0, cy=600.0, score=0.9, tracking=True):
    return MinimapState(cx=int(cx), cy=int(cy), cx_smooth=cx, cy_smooth=cy,
                        tracking=tracking, last_score=score)


def test_pending_points_backfill_to_go():
    r = MinimapRecorder()
    r.start()
    # countdown + pre-anchor: race_ms None, perf clock advancing
    r.update(mm(cx=100), lap=1, race_ms=None, now=10.00)   # t=-200 -> dropped
    r.update(mm(cx=101), lap=1, race_ms=None, now=10.15)   # t=-50  -> dropped
    r.update(mm(cx=102), lap=1, race_ms=None, now=10.30)   # t=100  -> kept
    r.update(mm(cx=103), lap=1, race_ms=500, now=10.70)    # first anchor
    ts = [p[0] for p in r.points]
    assert ts == [100, 500]
    assert r.points[0][1] == 102.0


def test_monotonic_guard_skips_frozen_clock():
    r = MinimapRecorder()
    r.start()
    r.update(mm(), lap=1, race_ms=1000, now=1.0)
    r.update(mm(), lap=1, race_ms=1000, now=1.016)   # frozen (pause) -> skipped
    r.update(mm(), lap=1, race_ms=1016, now=1.032)
    assert [p[0] for p in r.points] == [1000, 1016]


def test_not_tracking_or_stopped_records_nothing():
    r = MinimapRecorder()
    r.update(mm(), lap=1, race_ms=100, now=1.0)          # not started
    r.start()
    r.update(mm(tracking=False), lap=1, race_ms=200, now=1.1)
    assert r.points == []
    r.stop()
    r.update(mm(), lap=1, race_ms=300, now=1.2)
    assert r.points == []


def test_ring_only_band_points_are_kept():
    """No score-based filtering exists anymore (retroactive_filter deleted)."""
    r = MinimapRecorder()
    r.start()
    r.update(mm(score=0.50), lap=1, race_ms=100, now=1.0)   # sub-confident score
    assert len(r.points) == 1
    assert not hasattr(r, "retroactive_filter")
