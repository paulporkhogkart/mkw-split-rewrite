"""LapStatsTracker - per-lap coins (signed delta of the coin count between lap lines)
and mushrooms used (count of mushroom-count decrements within the lap)."""
from typing import Optional


class LapStatsTracker:
    def __init__(self):
        self.per_lap: dict = {}          # {lap_number: {"coins": int|None, "shrooms": int}}
        self._coin_baseline: int = 0     # coin count at the previous lap line (0 at race start)
        self._mush_used: int = 0         # mushroom uses accumulated in the current lap
        self._prev_mush: int = 0         # last seen mushroom count (to detect decrements)

    def reset(self):
        self.per_lap = {}
        self._coin_baseline = 0
        self._mush_used = 0
        self._prev_mush = 0

    def update(self, mush_count: int):
        """Each RACING frame: a drop in the mushroom count is a use (a pickup/gain is
        ignored). Triple-mushroom bursts decrement by >1, so accumulate the difference."""
        if mush_count < self._prev_mush:
            self._mush_used += self._prev_mush - mush_count
        self._prev_mush = mush_count

    def record_lap(self, lap: int, coin_count: Optional[int]):
        """At a lap crossing (and the finish) for the just-completed lap: store its coins
        (signed delta since the previous lap line; None if the count wasn't read) and the
        mushrooms used. Idempotent per lap."""
        if lap is None or lap in self.per_lap:
            return
        if coin_count is None:
            coins = None
        else:
            coins = coin_count - self._coin_baseline
            self._coin_baseline = coin_count
        self.per_lap[lap] = {"coins": coins, "shrooms": self._mush_used}
        self._mush_used = 0
