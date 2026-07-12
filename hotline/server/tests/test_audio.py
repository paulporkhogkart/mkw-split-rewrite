from __future__ import annotations

import numpy as np

from hotline import audio


def test_constants():
    assert audio.FRAME_BYTES == 320 and audio.SAMPLES_PER_FRAME == 160
    assert audio.SILENCE_FRAME == b"\x00" * 320


def test_tone_frames_shape_and_energy():
    frames = audio.tone_frames(440.0, 100)  # 100 ms => 5 frames
    assert len(frames) == 5 and all(len(f) == 320 for f in frames)
    pcm = np.frombuffer(b"".join(frames), dtype=np.int16)
    assert np.abs(pcm).max() > 5000  # audible


def test_wav_roundtrip(tmp_path):
    frames = audio.tone_frames(300.0, 60)
    p = tmp_path / "t.wav"
    audio.wav_write_frames(p, frames)
    assert audio.wav_read_frames(p) == frames


def test_mix_clips_not_wraps():
    loud = (np.full(160, 30000, dtype=np.int16)).tobytes()
    mixed = audio.mix_frames(loud, loud)
    pcm = np.frombuffer(mixed, dtype=np.int16)
    assert pcm.max() == 32767  # saturated, not wrapped negative
