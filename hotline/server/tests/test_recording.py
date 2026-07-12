from __future__ import annotations

import os
import time

from hotline import audio
from hotline.recording import CallRecorder, free_space_gib, sweep_retention


def test_records_both_legs_and_mix(tmp_path):
    rec = CallRecorder(tmp_path / "call1")
    tone = audio.tone_frames(440.0, 40)      # 2 frames
    for f in tone:
        rec.add_caller(f)
    rec.add_phone(audio.SILENCE_FRAME)       # phone leg shorter (1 frame)
    out = rec.close()
    caller = audio.wav_read_frames(out / "caller.wav")
    phone = audio.wav_read_frames(out / "phone.wav")
    mix = audio.wav_read_frames(out / "mix.wav")
    assert caller == tone and phone == [audio.SILENCE_FRAME]
    assert len(mix) == 2                     # padded to longer leg
    assert mix[0] == audio.mix_frames(tone[0], audio.SILENCE_FRAME)


def test_sweep_retention(tmp_path):
    old = tmp_path / "old_call"; old.mkdir()
    (old / "mix.wav").write_bytes(b"x")
    stale = time.time() - 91 * 86400
    os.utime(old, (stale, stale))
    fresh = tmp_path / "fresh_call"; fresh.mkdir()
    deleted = sweep_retention(tmp_path, max_age_days=90)
    assert deleted == 1
    assert not old.exists() and fresh.exists()


def test_free_space_positive(tmp_path):
    assert free_space_gib(tmp_path) > 0
