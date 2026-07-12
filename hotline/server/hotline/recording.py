from __future__ import annotations

import shutil
import time
from pathlib import Path

from . import audio


class CallRecorder:
    """Buffers both legs in memory (one call at a time; 10 min ≈ 9.6 MB/leg)
    and writes caller.wav / phone.wav / mix.wav at close. Recordings are RAW
    (pre-dump) per spec §12 — the dump log lives in the strikes table."""

    def __init__(self, dir: Path) -> None:
        self._dir = dir
        self._caller: list[bytes] = []
        self._phone: list[bytes] = []
        self._closed = False

    def add_caller(self, frame: bytes) -> None:
        self._caller.append(frame)

    def add_phone(self, frame: bytes) -> None:
        self._phone.append(frame)

    def close(self) -> Path:
        if self._closed:
            return self._dir
        self._closed = True
        audio.wav_write_frames(self._dir / "caller.wav", self._caller)
        audio.wav_write_frames(self._dir / "phone.wav", self._phone)
        n = max(len(self._caller), len(self._phone))
        mix = [
            audio.mix_frames(
                self._caller[i] if i < len(self._caller) else audio.SILENCE_FRAME,
                self._phone[i] if i < len(self._phone) else audio.SILENCE_FRAME,
            )
            for i in range(n)
        ]
        audio.wav_write_frames(self._dir / "mix.wav", mix)
        return self._dir


def sweep_retention(recordings_root: Path, max_age_days: int = 90,
                    now: float | None = None) -> int:
    if not recordings_root.exists():
        return 0
    cutoff = (now if now is not None else time.time()) - max_age_days * 86400
    deleted = 0
    for child in recordings_root.iterdir():
        if child.is_dir() and child.stat().st_mtime < cutoff:
            shutil.rmtree(child)
            deleted += 1
    return deleted


def free_space_gib(path: Path) -> float:
    probe = path
    while not probe.exists():
        parent = probe.parent
        if parent == probe:
            break
        probe = parent
    return shutil.disk_usage(probe).free / (1024 ** 3)
