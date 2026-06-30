# tests/test_clip_capture.py
import json
import os
import itertools
import warnings
import pytest
from mkw_tracker.tools.clip_capture import ClipCaptureManager, kart_flourish_action


class FakePipe:
    def __init__(self, cmd, **kw): self.cmd = cmd
    def latest(self): return None
    def alive(self): return True
    def stop(self): pass


def test_events_written_with_marks(tmp_path):
    clock = itertools.count(0, 1)   # 0,1,2,3,... "seconds"
    m = ClipCaptureManager(str(tmp_path), "dev", "3840x2160", 60,
                           frame_ref=[None], _pipe_factory=FakePipe,
                           clock=lambda: next(clock))
    m.begin("mario__base__standard_kart")           # clock 0 at begin
    m.mark("swap")                                  # t=1
    m.mark("flourish")                              # t=2
    m.set_duration_end()                            # t=3 → flourish_end + duration
    ev = m.end()
    assert ev["swap_t"] == 1 and ev["flourish_t"] == 2
    assert ev["flourish_end_t"] == 3 and ev["fps"] == 60
    side = tmp_path / "mario__base__standard_kart.events.json"
    assert json.loads(side.read_text())["flourish_t"] == 2


def test_abort_deletes_clip(tmp_path):
    m = ClipCaptureManager(str(tmp_path), "dev", "3840x2160", 60,
                           frame_ref=[None], _pipe_factory=FakePipe,
                           clock=lambda: 0.0)
    m.begin("x__y")
    (tmp_path / "x__y.mkv").write_bytes(b"partial")   # simulate ffmpeg output
    m.abort()
    assert not (tmp_path / "x__y.mkv").exists()
    assert not (tmp_path / "x__y.events.json").exists()


def test_exists(tmp_path):
    m = ClipCaptureManager(str(tmp_path), "dev", "3840x2160", 60,
                           frame_ref=[None], _pipe_factory=FakePipe, clock=lambda: 0.0)
    assert not m.exists("a__b")
    (tmp_path / "a__b.mkv").write_bytes(b"x")
    assert m.exists("a__b")


def test_abort_warns_if_delete_fails(tmp_path, monkeypatch):
    import mkw_tracker.tools.clip_capture as cc
    m = ClipCaptureManager(str(tmp_path), "dev", "3840x2160", 60,
                           frame_ref=[None], _pipe_factory=FakePipe, clock=lambda: 0.0)
    m.begin("x__y")
    (tmp_path / "x__y.mkv").write_bytes(b"partial")
    def _boom(_p):
        raise OSError("file locked")
    monkeypatch.setattr(cc.os, "remove", _boom)
    with pytest.warns(UserWarning):
        m.abort()          # must not raise


def test_kart_flourish_action_waits_then_seals_when_tell_dropped():
    """Within the hold window -> keep recording ('wait'). After the hold, a DROPPED kart_select tell
    (score below the threshold) means the flourish played -> 'seal'."""
    assert kart_flourish_action(score=0.9, elapsed=1.0, hold_secs=3.0, drop=0.4) == "wait"
    assert kart_flourish_action(score=0.1, elapsed=3.0, hold_secs=3.0, drop=0.4) == "seal"


def test_kart_flourish_action_discards_when_tell_still_high_after_hold():
    """After the hold, kart_select STILL scoring (>= drop) == the A was eaten and no flourish
    played -> 'discard' (the flourish-less clip must not be kept; its events.json would look
    identical to a good one). Still within the hold -> 'wait' even at a high score."""
    assert kart_flourish_action(score=0.99, elapsed=3.0, hold_secs=3.0, drop=0.4) == "discard"
    assert kart_flourish_action(score=0.99, elapsed=2.9, hold_secs=3.0, drop=0.4) == "wait"


def test_mark_bogus_event_warns_and_does_not_raise(tmp_path):
    """mark() with an unknown event name must warn, not raise KeyError."""
    m = ClipCaptureManager(str(tmp_path), "dev", "3840x2160", 60,
                           frame_ref=[None], _pipe_factory=FakePipe, clock=lambda: 0.0)
    m.begin("a__b")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        m.mark("bogus")    # must not raise
    assert any("bogus" in str(w.message) for w in caught)
