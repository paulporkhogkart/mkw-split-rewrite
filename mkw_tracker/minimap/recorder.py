"""MinimapRecorder - records minimap positions and saves to DB."""
import time
from typing import Optional

from .tracker import MinimapState
from ..database.replay_repo import save_run


class MinimapRecorder:
    """
    Records raw minimap positions during a race and saves them to the DB
    on completion or abort.

    Points are stored as (t_ms, cx, cy, score).
    Supports pause/resume with continuous timestamps.
    """

    INTERP_SCORE = 0.0

    def __init__(self):
        self._points:       list           = []
        self._race_start:   float          = 0.0
        self._pause_start:  Optional[float] = None
        self._paused_total: float          = 0.0
        self._recording:    bool           = False

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def is_paused(self) -> bool:
        return self._pause_start is not None

    @property
    def points(self) -> list:
        """Read-only copy of the recorded points (list of (t_ms, cx, cy, score))."""
        return list(self._points)

    def start(self):
        """Call when RACING begins (new race instance)."""
        self._points       = []
        self._race_start   = time.perf_counter()
        self._pause_start  = None
        self._paused_total = 0.0
        self._recording    = True

    def pause(self):
        if self._recording and self._pause_start is None:
            self._pause_start = time.perf_counter()
            print("  [Replay] Recording paused")

    def resume(self):
        if self._recording and self._pause_start is not None:
            self._paused_total += (time.perf_counter() - self._pause_start) * 1000.0
            self._pause_start  = None
            print("  [Replay] Recording resumed")

    def stop(self):
        self._recording    = False
        self._points       = []
        self._pause_start  = None
        self._paused_total = 0.0

    def _elapsed_ms(self) -> int:
        raw    = (time.perf_counter() - self._race_start) * 1000.0
        paused = self._paused_total
        if self._pause_start is not None:
            paused += (time.perf_counter() - self._pause_start) * 1000.0
        return int(raw - paused)

    def update(self, mm: MinimapState):
        """Append a position point whenever the tracker has an active position."""
        if not self._recording or self._pause_start is not None:
            return
        if not mm.tracking or mm.cx is None:
            return
        t_ms = self._elapsed_ms()
        self._points.append((t_ms, mm.cx_smooth, mm.cy_smooth, mm.last_score))

    def save(
        self,
        course: str,
        total_time: Optional[str] = None,
        character: Optional[str] = None,
        costume:   Optional[str] = None,
        kart:      Optional[str] = None,
        lap_splits: Optional[dict] = None,
    ) -> Optional[int]:
        """
        Persist the recorded run to the DB.  Returns the replay_id, or None.
        total_time=None means an aborted/reset run.
        """
        self._recording   = False
        self._pause_start = None

        if not self._points or not course:
            self._points = []
            return None

        points_out = list(self._points)
        self._points = []

        replay_id = save_run(
            course=course,
            points=points_out,
            total_time=total_time,
            character=character,
            costume=costume,
            kart=kart,
            player="me",
            source="local",
            lap_splits=lap_splits,
        )
        status = f"time={total_time}" if total_time else "aborted"
        print(f"  [Replay] Saved to DB ({status}, {len(points_out)} pts) id={replay_id}")
        return replay_id

    def retroactive_filter(self, threshold: float):
        """
        Filter ALL recorded points using the calibrated threshold, then
        linearly interpolate over the removed gaps.
        """
        if not self._points:
            return

        good = [(t, cx, cy, sc) for t, cx, cy, sc in self._points if sc >= threshold]
        if len(good) < 2:
            print(f"  [Replay] retroactive_filter: too few good points ({len(good)}), keeping original")
            return

        good_ts  = [p[0] for p in good]
        good_cxs = [p[1] for p in good]
        good_cys = [p[2] for p in good]

        def _interp(ts_out, ts_in, vals_in):
            out = []
            for t in ts_out:
                if t <= ts_in[0]:
                    out.append(vals_in[0])
                elif t >= ts_in[-1]:
                    out.append(vals_in[-1])
                else:
                    lo, hi = 0, len(ts_in) - 1
                    while lo + 1 < hi:
                        mid = (lo + hi) // 2
                        if ts_in[mid] <= t:
                            lo = mid
                        else:
                            hi = mid
                    frac = (t - ts_in[lo]) / (ts_in[hi] - ts_in[lo])
                    out.append(vals_in[lo] + frac * (vals_in[hi] - vals_in[lo]))
            return out

        orig_ts = [p[0] for p in self._points]
        new_cxs = _interp(orig_ts, good_ts, good_cxs)
        new_cys = _interp(orig_ts, good_ts, good_cys)

        removed = sum(1 for p in self._points if p[3] < threshold)
        self._points = [(t, cx, cy, self._points[i][3])
                        for i, (t, cx, cy) in enumerate(zip(orig_ts, new_cxs, new_cys))]
        print(f"  [Replay] Retroactive filter: removed {removed}/{len(self._points)} "
              f"points below thr={threshold:.3f}")
