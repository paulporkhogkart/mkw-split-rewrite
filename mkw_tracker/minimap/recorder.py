"""MinimapRecorder - records minimap trail points on the race clock."""
import time
from typing import Optional

from .tracker import MinimapState


class MinimapRecorder:
    """Records (race_ms, cx, cy, score, lap) during a race.

    Timestamps are the RaceTimer race clock (passed in by the main loop), so
    trails share the clock shown on the cards and t=0 is GO. Points seen
    before the timer's first anchor (countdown + ~0.5s) are buffered with
    their perf time and back-stamped when the anchor arrives; the countdown
    remainder (t < 0) is dropped. RaceTimer freezes during pauses, so the
    monotonic guard drops paused frames - no pause bookkeeping needed here.
    """

    def __init__(self):
        self._points:    list = []
        self._pending:   list = []   # (perf_now, cx, cy, score, lap) pre-anchor
        self._recording: bool = False
        self._last_t:    int  = -1

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def points(self) -> list:
        """Read-only copy of recorded points (list of (t_ms, cx, cy, score, lap))."""
        return list(self._points)

    def start(self):
        """Call when RACING begins (new race instance)."""
        self._points    = []
        self._pending   = []
        self._recording = True
        self._last_t    = -1

    def stop(self):
        self._recording = False
        self._points    = []
        self._pending   = []
        self._last_t    = -1

    def update(self, mm: MinimapState, lap: Optional[int] = None,
               race_ms: Optional[int] = None, now: Optional[float] = None):
        """Append a position point stamped with the race clock."""
        if not self._recording or not mm.tracking or mm.cx is None:
            return
        if now is None:
            now = time.perf_counter()
        if race_ms is None:
            self._pending.append((now, mm.cx_smooth, mm.cy_smooth,
                                  mm.last_score, lap))
            return
        if self._pending:
            for p_now, cx, cy, sc, lp in self._pending:
                t = int(round(race_ms - (now - p_now) * 1000.0))
                if 0 <= t and t > self._last_t:
                    self._points.append((t, cx, cy, sc, lp))
                    self._last_t = t
            self._pending = []
        t = int(race_ms)
        if t <= self._last_t:
            return
        self._points.append((t, mm.cx_smooth, mm.cy_smooth, mm.last_score, lap))
        self._last_t = t
