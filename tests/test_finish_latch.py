"""FinishValueLatch streak/guard logic + FinishLatch combo seam."""
from mkw_tracker.race.finish import FinishValueLatch, FinishLatch


def make_latch():
    # templates aren't needed for feed()-level tests
    return FinishValueLatch(templates={})


def test_running_timer_never_latches():
    l = make_latch()
    for ms in range(50_000, 50_500, 50):       # advancing reads
        assert not l.feed(ms, lap_inc=False, estimate_ms=ms)
    assert not l.detected


def test_frozen_value_matching_estimate_latches_on_third_read():
    l = make_latch()
    assert not l.feed(96_713, lap_inc=False, estimate_ms=96_750)
    assert not l.feed(96_713, lap_inc=False, estimate_ms=96_800)
    assert l.feed(96_713, lap_inc=False, estimate_ms=96_850)
    assert l.detected and l.final_ms == 96_713


def test_lap_flash_rejected_by_estimate_guard():
    """Frozen lap split (32s) vs climbing cumulative estimate (~65s): never latches."""
    l = make_latch()
    est = 65_000
    for _ in range(10):
        assert not l.feed(32_456, lap_inc=False, estimate_ms=est)
        est += 50
    assert not l.detected


def test_lap_inc_arms_refractory():
    """A lap increment suppresses latching for LAP_REFRACTORY_S, not just one feed:
    at the lap-1 crossing the frozen split EQUALS the cumulative estimate for
    ~300ms (measured on real footage), outliving a one-shot streak reset."""
    l = make_latch()
    l.feed(19_440, lap_inc=False, estimate_ms=19_440, now=10.00)
    l.feed(19_440, lap_inc=True,  estimate_ms=19_490, now=10.05)   # crossing
    # frozen split still matches the estimate for a while - must stay suppressed
    assert not l.feed(19_440, lap_inc=False, estimate_ms=19_540, now=10.10)
    assert not l.feed(19_440, lap_inc=False, estimate_ms=19_590, now=10.15)
    assert not l.feed(19_440, lap_inc=False, estimate_ms=19_640, now=10.20)
    assert not l.detected
    # after the refractory, a genuine freeze (value == estimate) latches again
    assert not l.feed(96_713, lap_inc=False, estimate_ms=96_713, now=11.10)
    assert not l.feed(96_713, lap_inc=False, estimate_ms=96_763, now=11.15)
    assert l.feed(96_713, lap_inc=False, estimate_ms=96_813, now=11.20)
    assert l.detected and l.final_ms == 96_713


def test_none_read_resets_streak():
    l = make_latch()
    l.feed(96_713, lap_inc=False, estimate_ms=96_713)
    l.feed(None, lap_inc=False, estimate_ms=96_763)
    l.feed(96_713, lap_inc=False, estimate_ms=96_813)
    assert not l.feed(None, lap_inc=False, estimate_ms=96_863)
    assert not l.detected


def test_missing_estimate_never_latches():
    l = make_latch()
    for _ in range(5):
        assert not l.feed(96_713, lap_inc=False, estimate_ms=None)
    assert not l.detected


def test_combo_exposes_detected_and_reset():
    c = FinishLatch(templates={})
    assert not c.detected
    c.value.feed(10_000, lap_inc=False, estimate_ms=10_000)
    c.value.feed(10_000, lap_inc=False, estimate_ms=10_050)
    c.value.feed(10_000, lap_inc=False, estimate_ms=10_100)
    assert c.detected and c.final_ms == 10_000
    c.reset()
    assert not c.detected and c.final_ms is None
