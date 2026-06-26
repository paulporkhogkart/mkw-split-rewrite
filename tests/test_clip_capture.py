# tests/test_clip_capture.py
import json
import os
import itertools
import warnings
import pytest
from mkw_tracker.tools.clip_capture import ClipCaptureManager


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
