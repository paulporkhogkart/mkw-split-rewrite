"""TimestampTracker.record_finish must not lock a total_time that is impossible.

Regression for the photo-mode PB exploit (MAJOR1): photo mode freezes the lap-split
flash in the timer ROI on the final lap; the burst reads that frozen split (e.g.
0:46.422) and calls record_finish. Since the split is *less* than the sum of the
prior lap splits, the final lap would be negative - an impossible finish. The tracker
detected this ("final lap negative") but still left total_time set, so the bogus time
was finalized as a PB. It must reject the read and leave total_time unset.
"""
from mkw_tracker.race.timestamp import TimestampTracker


def _set_state(ts, s):
    """Drive the six digit slots so state.formatted() == s (e.g. '0:46.422')."""
    mins, rest = s.split(":")
    secs, mil = rest.split(".")
    ts.state.A = int(mins)
    ts.state.B, ts.state.C = int(secs[0]), int(secs[1])
    ts.state.D, ts.state.E, ts.state.F = int(mil[0]), int(mil[1]), int(mil[2])


def test_finish_below_prior_splits_does_not_lock_total_time():
    """MAJOR1: total 0:46.422 < (lap1 45033 + lap2 46422) = 91455 -> negative final lap."""
    ts = TimestampTracker()
    ts.splits = {1: "0:45.033", 2: "0:46.422"}
    _set_state(ts, "0:46.422")
    ts.record_finish(lap=3)
    assert ts.total_time is None, f"bogus finish locked total_time={ts.total_time!r}"
    assert 3 not in ts.splits


def test_valid_finish_still_locks_total_time():
    """A real finish (total exceeds prior splits) must still lock + record the final lap."""
    ts = TimestampTracker()
    ts.splits = {1: "0:45.033", 2: "0:46.422"}     # sum 91455
    _set_state(ts, "2:19.818")                     # 139818 -> final lap 48363
    ts.record_finish(lap=3)
    assert ts.total_time == "2:19.818"
    assert ts.splits[3] == "0:48.363"


def test_finish_with_no_prior_splits_locks():
    """No splits captured (e.g. joined mid-race): any readable total is accepted here.
    (The race-clock guard, tested elsewhere, is what rejects a frozen mid-lap pause.)"""
    ts = TimestampTracker()
    ts.splits = {}
    _set_state(ts, "1:50.891")
    ts.record_finish(lap=3)
    assert ts.total_time == "1:50.891"
