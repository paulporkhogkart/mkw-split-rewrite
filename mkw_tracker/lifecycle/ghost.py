"""GhostImport - one-shot 'record the next ghost replay' state machine.

cv2-free and free of any tracker references, so it is unit-testable in isolation.
Owned by RaceLifecycle, which drives it from screen changes + the race clock.

States:
  IDLE       - disarmed (default).
  ARMED      - waiting for a ghost to start from the beginning.
  RECORDING  - capturing a ghost (a 'validating' sub-window confirms it began at
               the start rather than resuming mid-replay).

Restart vs. resume (see the design spec):
  * A fresh ghost start is preceded by a reload, so old in {GHOST_RESET,
    START_REPLAY} is a decisive fresh-start signal.
  * A mid-replay resume is REPLAY_MENU -> GHOST with no reload; its race clock is
    already advanced. The clock is ground truth for the ambiguous REPLAY_MENU
    origin (and rescues a restart whose brief GHOST_RESET was missed).
"""
from enum import Enum, auto
from typing import Optional

from ..detection.screen import Screen


class GhostState(Enum):
    IDLE = auto()
    ARMED = auto()
    RECORDING = auto()


# Origins that mean a reload happened immediately before GHOST == a fresh start.
_FRESH_START_ORIGINS = {Screen.GHOST_RESET, Screen.START_REPLAY}


class GhostImport:
    # Race clock (ms) at/under which we count the start as "witnessed at zero"
    # (countdown / just-after-GO). Tuned against temp/ghostsample.mp4.
    START_ZERO_MS: int = 2000
    # Frames to wait for the clock to declare itself before defaulting to fresh
    # (~0.5s at 30fps; a real start shows its countdown well inside this).
    VALIDATE_FRAMES: int = 20

    def __init__(self):
        self.reset()

    def reset(self):
        self.state: GhostState = GhostState.IDLE
        self._validate_left: int = 0
        self._fresh_origin: bool = False

    @property
    def armed(self) -> bool:
        return self.state in (GhostState.ARMED, GhostState.RECORDING)

    @property
    def recording(self) -> bool:
        return self.state == GhostState.RECORDING

    def arm(self) -> None:
        if self.state == GhostState.IDLE:
            self.state = GhostState.ARMED

    def disarm(self) -> None:
        self.state = GhostState.IDLE
        self._validate_left = 0
        self._fresh_origin = False

    def on_ghost_enter(self, old: Screen) -> bool:
        """A transition into GHOST (old != GHOST). Begins a provisional recording
        iff ARMED. Returns True when a recording was started."""
        if self.state != GhostState.ARMED:
            return False
        self.state = GhostState.RECORDING
        # +1 so VALIDATE_FRAMES None-clock calls exhaust the countdown; the NEXT
        # (VALIDATE_FRAMES+1th) call finds _validate_left==0 and returns True.
        self._validate_left = self.VALIDATE_FRAMES + 1
        self._fresh_origin = old in _FRESH_START_ORIGINS
        return True

    def on_ghost_leave(self) -> bool:
        """Left GHOST. Returns True iff we were RECORDING (an abort before finish);
        the caller discards and we return to ARMED for the next start."""
        if self.state == GhostState.RECORDING:
            self.state = GhostState.ARMED
            self._validate_left = 0
            self._fresh_origin = False
            return True
        return False

    def validate(self, race_elapsed_ms: Optional[int]) -> Optional[bool]:
        """Feed the race-clock estimate each frame while RECORDING.
        Returns True (confirmed fresh start), False (resume -> back to ARMED), or
        None (still validating / not validating).

        _validate_left > 0  : still counting down (waiting for clock or window expiry)
        _validate_left == 0 : verdict already delivered; go silent
        _fresh_origin True  : decisive fresh-start pending; cleared on first call
        """
        if self.state != GhostState.RECORDING:
            return None
        if self._fresh_origin:
            # Fresh origin (GHOST_RESET / START_REPLAY) == decisive fresh-start signal.
            self._fresh_origin = False
            self._validate_left = 0
            return True
        # Window already consumed (clock or timeout resolved); go silent.
        if self._validate_left == 0:
            return None
        if race_elapsed_ms is not None:
            self._validate_left = 0
            if race_elapsed_ms <= self.START_ZERO_MS:
                return True
            self.state = GhostState.ARMED          # advanced clock => resume
            return False
        # Clock not running yet (countdown): wait out the window, then default fresh.
        self._validate_left -= 1
        if self._validate_left == 0:
            # Window exhausted with no clock signal => treat as fresh start.
            return True
        return None
