from __future__ import annotations

from hotline.audio import SILENCE_FRAME
from hotline.jitter import JitterBuffer


def frame(i: int) -> bytes:
    return bytes([i % 256]) * 320


def test_prebuffers_to_target_then_plays():
    jb = JitterBuffer(target_ms=60, max_ms=400)  # target = 3 frames
    assert jb.pull() == SILENCE_FRAME
    jb.push(frame(1)); jb.push(frame(2))
    assert jb.pull() == SILENCE_FRAME          # only 2 < 3 buffered
    jb.push(frame(3))
    assert jb.pull() == frame(1)
    assert jb.pull() == frame(2)


def test_underrun_emits_silence_and_rebuffers():
    jb = JitterBuffer(target_ms=40, max_ms=400)  # target = 2
    jb.push(frame(1)); jb.push(frame(2))
    assert jb.pull() == frame(1) and jb.pull() == frame(2)
    assert jb.pull() == SILENCE_FRAME          # underrun
    jb.push(frame(3))
    assert jb.pull() == SILENCE_FRAME          # rebuffering: 1 < 2
    jb.push(frame(4))
    assert jb.pull() == frame(3)


def test_overrun_drops_oldest():
    jb = JitterBuffer(target_ms=20, max_ms=60)  # max = 3 frames
    for i in range(1, 6):
        jb.push(frame(i))
    assert jb.pull() == frame(3)               # 1 and 2 were dropped
