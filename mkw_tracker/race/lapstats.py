"""LapStatsTracker - per-lap coins (signed delta of the coin count between lap lines)
and mushrooms used (count of mushroom-count decrements within the lap)."""
from typing import Optional


class LapStatsTracker:
    # A real coin hit drops <=3; a bigger single-scan move is an OCR misread (ignored, and
    # it doesn't move the baseline, so a one-frame glitch like 18->5->18 nets to zero).
    MAX_LOSS_PER_SCAN: int = 3
    MAX_GAIN_PER_SCAN: int = 5

    def __init__(self):
        self.per_lap: dict = {}          # {lap_number: {"coins": int|None, "shrooms": int}}
        self._coin_baseline: int = 0     # coin count at the previous lap line (0 at race start)
        self._mush_used: int = 0         # mushroom uses accumulated in the current lap
        self._prev_mush: int = 0         # last seen mushroom count (to detect decrements)
        # Run-level totals (whole run, incl. resets; never cleared per lap).
        self.coins_gained: int = 0       # sum of upward coin-count moves
        self.coins_lost: int = 0         # sum of downward moves (getting hit)
        self.mushrooms_used: int = 0     # run-level mushroom uses
        self._prev_coin = None           # last accepted live coin count

    def reset(self):
        self.per_lap = {}
        self._coin_baseline = 0
        self._mush_used = 0
        self._prev_mush = 0
        self.coins_gained = 0
        self.coins_lost = 0
        self.mushrooms_used = 0
        self._prev_coin = None

    def update(self, mush_count: int):
        """Each RACING frame: a drop in the mushroom count is a use (a pickup/gain is
        ignored). A 3->0 triple-burst decrements by 3 - that's a legitimate three uses,
        accumulated in full (no cap)."""
        if mush_count < self._prev_mush:
            used = self._prev_mush - mush_count
            self._mush_used += used          # per-lap (cleared at record_lap)
            self.mushrooms_used += used      # run-level (kept for the whole run)
        self._prev_mush = mush_count

    def update_coins(self, coin_count):
        """Each RACING frame: accumulate run-level coins gained/lost from the live count.
        The first reading only sets the baseline (so the race-start 0 isn't a 'gain'); a
        single-scan move beyond the plausible caps is an OCR glitch - ignored, baseline left
        put."""
        if coin_count is None:
            return
        if self._prev_coin is None:
            self._prev_coin = coin_count
            return
        delta = coin_count - self._prev_coin
        if 0 < delta <= self.MAX_GAIN_PER_SCAN:
            self.coins_gained += delta
            self._prev_coin = coin_count
        elif -self.MAX_LOSS_PER_SCAN <= delta < 0:
            self.coins_lost += -delta
            self._prev_coin = coin_count
        # delta == 0 or beyond the caps: no change (the caps absorb misreads)

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
