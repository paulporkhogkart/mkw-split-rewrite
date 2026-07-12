from __future__ import annotations

from collections import deque

from .audio import FRAME_MS, SILENCE_FRAME


class JitterBuffer:
    """Absorbs network arrival jitter on the caller->phone path.

    push() on packet arrival; pull() on the 20 ms pump tick. Emits silence
    while pre-buffering (start and after any underrun) so the downstream
    clock never starves; drops oldest frames beyond max_ms so latency can
    never grow unbounded.
    """

    def __init__(self, target_ms: int = 100, max_ms: int = 400) -> None:
        self._frames: deque[bytes] = deque()
        self._target = max(1, target_ms // FRAME_MS)
        self._max = max(self._target, max_ms // FRAME_MS)
        self._playing = False

    def push(self, frame: bytes) -> None:
        self._frames.append(frame)
        while len(self._frames) > self._max:
            self._frames.popleft()

    def pull(self) -> bytes:
        if not self._playing:
            if len(self._frames) < self._target:
                return SILENCE_FRAME
            self._playing = True
        if self._frames:
            return self._frames.popleft()
        self._playing = False
        return SILENCE_FRAME
