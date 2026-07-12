from __future__ import annotations

import math
import wave
from pathlib import Path
from typing import Iterable

import numpy as np

SAMPLE_RATE = 8000
FRAME_MS = 20
SAMPLES_PER_FRAME = SAMPLE_RATE * FRAME_MS // 1000  # 160
FRAME_BYTES = SAMPLES_PER_FRAME * 2  # 320
SILENCE_FRAME = b"\x00" * FRAME_BYTES


def tone_frames(freq_hz: float, ms: int, amplitude: float = 0.3) -> list[bytes]:
    n_frames = max(1, ms // FRAME_MS)
    total = n_frames * SAMPLES_PER_FRAME
    t = np.arange(total) / SAMPLE_RATE
    pcm = (np.sin(2 * math.pi * freq_hz * t) * amplitude * 32767).astype(np.int16)
    raw = pcm.tobytes()
    return [raw[i : i + FRAME_BYTES] for i in range(0, len(raw), FRAME_BYTES)]


def wav_write_frames(path: Path, frames: Iterable[bytes]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        for f in frames:
            w.writeframes(f)


def wav_read_frames(path: Path) -> list[bytes]:
    with wave.open(str(path), "rb") as r:
        assert r.getnchannels() == 1 and r.getsampwidth() == 2
        assert r.getframerate() == SAMPLE_RATE
        raw = r.readframes(r.getnframes())
    return [raw[i : i + FRAME_BYTES] for i in range(0, len(raw) - len(raw) % FRAME_BYTES, FRAME_BYTES)]


def mix_frames(a: bytes, b: bytes) -> bytes:
    pa = np.frombuffer(a, dtype=np.int16).astype(np.int32)
    pb = np.frombuffer(b, dtype=np.int16).astype(np.int32)
    n = max(len(pa), len(pb))
    pa = np.pad(pa, (0, n - len(pa)))
    pb = np.pad(pb, (0, n - len(pb)))
    return np.clip(pa + pb, -32768, 32767).astype(np.int16).tobytes()
