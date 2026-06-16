"""RaceLifecycle - screen-change callback that drives all race state transitions."""
from typing import Optional

from ..detection.screen import Screen
from ..detection.selection import SelectionTracker
from ..minimap.tracker import MinimapTracker, MINIMAP_ROI
from ..minimap.recorder import MinimapRecorder
from ..race.laps import LapTracker
from ..race.coins import CoinTracker
from ..race.timestamp import TimestampTracker
from ..race.finish import FinishStillDetector
from ..race.mushrooms import MushroomTracker
from ..database.replay_repo import get_minimap_roi, get_minimap_seed, get_minimap_threshold
from .ghost import GhostImport


_PAUSE_SCREENS = {Screen.RACE_MENU, Screen.HOME}


class RaceLifecycle:
    """
    Drives all race state transitions in response to screen changes.

    Attach to a ScreenDetector via:
        detector.on_screen_change = lifecycle.on_screen_change
    """

    def __init__(
        self,
        selection:  SelectionTracker,
        laps:       LapTracker,
        coins:      CoinTracker,
        ts:         TimestampTracker,
        finish:     FinishStillDetector,
        mush:       MushroomTracker,
        minimap:    MinimapTracker,
        mm_rec:     MinimapRecorder,
        timer=None,
        lapstats=None,
        transition_count: Optional[list] = None,
        ipc=None,
    ):
        self._selection  = selection
        self._laps       = laps
        self._coins      = coins
        self._ts         = ts
        self._finish     = finish
        self._mush       = mush
        self._lapstats   = lapstats
        self._minimap    = minimap
        self._mm_rec     = mm_rec
        self._timer      = timer
        self._transition_count = transition_count if transition_count is not None else [0]

        self._ipc = ipc

        self._paused_from_racing = False
        self._resuming_race      = False

        # Wall-clock ISO timestamp of the current race's fresh start (set by
        # _start_race, consumed by _finalize_recording as the run's started_at).
        self._race_started_at: Optional[str] = None

        # Guards a double finalize: the finished run is emitted the instant the final
        # time locks (finalize_on_finish), so the later screen-change finalize must not
        # re-emit. Reset on each new race / state clear.
        self._finalized = False

        # The most recent full frame (set externally each loop iteration)
        self.current_frame = None

        # One-shot "import the next ghost" state machine (see lifecycle/ghost.py).
        self._ghost = GhostImport()

    # ── Public callback ──────────────────────────────────────────────────────

    def on_screen_change(self, old: Screen, new: Screen):
        self._transition_count[0] += 1
        print(f"  {old.name:25s}  ->  {new.name}")
        if self._ipc is not None:
            from ..ipc.protocol import emit_screen_change
            self._ipc.emit(emit_screen_change(old.name, new.name))

        # ── From RACING ─────────────────────────────────────────────────────
        if old == Screen.RACING:
            if new in _PAUSE_SCREENS:
                self._pause()
            else:
                self._paused_from_racing = False
                self._resuming_race      = False
                completed = (new == Screen.POST_TIME_TRIAL) or self._finish.detected
                self._finalize_recording(completed)
                self._clear_race_state()

        # ── From a pause screen ──────────────────────────────────────────────
        elif self._paused_from_racing and old in _PAUSE_SCREENS:
            if new == Screen.RACING:
                self._resume()
            elif new in _PAUSE_SCREENS:
                pass  # HOME ↔ RACE_MENU - still paused, do nothing
            else:
                # Left the pause loop to a non-racing screen - race is over
                self._paused_from_racing = False
                self._resuming_race      = False
                self._finalize_recording(completed=False)
                self._clear_race_state()

        # ── Entering RACING fresh (not a resume) ────────────────────────────
        if new == Screen.RACING and old != Screen.RACING:
            if self._resuming_race:
                self._resuming_race = False   # consumed
            else:
                self._start_race(old)

        # ── Arriving at POST_TIME_TRIAL from anywhere but RACING ────────────
        if new == Screen.POST_TIME_TRIAL and old != Screen.RACING:
            self._clear_race_state()

        # ── RESET from non-racing contexts ───────────────────────────────────
        if new == Screen.RESET and old not in ({Screen.RACING} | _PAUSE_SCREENS):
            self._clear_race_state()

        # ── Ghost import: GHOST treated as a private RACING while recording ──
        if new == Screen.GHOST and old != Screen.GHOST:
            if self._ghost.on_ghost_enter(old):
                self._start_race(old)          # provisional; validated over next frames
                self._emit_ghost_state()
        elif old == Screen.GHOST and new != Screen.GHOST:
            if self._ghost.on_ghost_leave():   # was recording -> aborted before finish
                self._clear_race_state()       # discard, stay armed, no run emitted
                self._emit_ghost_state()
            elif self._finalized:              # a finished ghost left the screen
                self._clear_race_state()       # clear the lingering finished state

    # ── Ghost import surface (driven by main loop + IPC) ─────────────────────

    @property
    def ghost_armed(self) -> bool:
        return self._ghost.armed

    @property
    def ghost_recording(self) -> bool:
        return self._ghost.recording

    def arm_ghost(self) -> None:
        self._ghost.arm()
        self._emit_ghost_state()

    def disarm_ghost(self) -> None:
        if self._ghost.recording:
            self._clear_race_state()          # drop any in-progress capture
        self._ghost.disarm()
        self._emit_ghost_state()

    def effective_screen(self, real: "Screen") -> "Screen":
        """GHOST -> RACING only while a ghost is being recorded; else unchanged.
        Real RACING is always RACING regardless of arm state."""
        if real == Screen.GHOST and self._ghost.recording:
            return Screen.RACING
        return real

    def validate_ghost_start(self, race_elapsed_ms) -> None:
        """Per-frame restart-vs-resume check while recording. On a resume the
        provisional capture is discarded and we re-arm."""
        res = self._ghost.validate(race_elapsed_ms)
        if res is False:
            self._clear_race_state()
            self._emit_ghost_state()

    def _emit_ghost_state(self) -> None:
        if self._ipc is not None:
            from ..ipc.protocol import emit_ghost_import_state
            self._ipc.emit(emit_ghost_import_state(self._ghost.armed, self._ghost.recording))

    # ── Private helpers ─────────────────────────────────────────────────────

    def _pause(self):
        # Recorder needs no pause call: the RaceTimer clock freezes off-RACING,
        # so its monotonic guard drops paused frames.
        self._paused_from_racing = True
        print("  [Race] Paused (entering pause screen)")

    def _resume(self):
        self._resuming_race      = True
        self._paused_from_racing = False
        print("  [Race] Resumed")

    def _clear_race_state(self):
        self._laps.reset()
        self._coins.reset()
        self._ts.reset()
        self._finish.reset()
        self._mush.reset()
        if self._lapstats is not None:
            self._lapstats.reset()
        self._minimap.reset()
        if self._timer is not None:
            self._timer.reset()
        self._race_started_at = None
        self._finalized = False
        if self._ipc is not None:
            from ..ipc.protocol import emit_race_cleared
            self._ipc.emit(emit_race_cleared())
        print("  [reset] Race stats cleared")

    def finalize_on_finish(self):
        """Emit + save the finished run the instant the final time locks (called from
        the main loop when ts.total_time is set), so the server receives it at the
        timer-freeze rather than when the results screen later appears. Idempotent."""
        self._finalize_recording(completed=True)

    def _finalize_recording(self, completed: bool):
        """
        Emit the finalized attempt for the app to hold/upload.
        completed=True  → race finished; calibrate the minimap threshold.
        completed=False → aborted/reset; emit only, no calibration.

        Idempotent within a race: a no-op if already finalized (the finished run is
        emitted at the finish-lock, then the screen change would otherwise re-run this).
        """
        if self._finalized:
            return
        is_ghost  = self._ghost.recording
        sel       = self._selection.state
        course    = sel.course
        # A ghost replays the *recorded* loadout, not the live one we detected, so
        # null identity to force manual entry. Course is reliable (Course Select).
        character = None if is_ghost else sel.character
        costume   = None if is_ghost else sel.costume

        best_total_time: Optional[str] = None
        if completed:
            # Total time comes from the TimestampTracker; FinishStillDetector only
            # flags that the timer froze (it carries no time of its own).
            best_total_time = self._ts.total_time

        if completed and best_total_time and not is_ghost and not self._minimap._calibrated:
            new_threshold = self._minimap.calibrate_from_race()
            if course and character:
                from ..database.replay_repo import set_minimap_threshold
                set_minimap_threshold(course, character, costume or "", new_threshold)

        # Emit the full finalized-attempt payload for the Tauri app to upload.
        # (Pure detector: emit only; no network. Built before save() clears points.)
        # NOT gated on `course`: an incomplete run - even one where nothing was
        # detected - must still emit so the app can hold it for review and the user
        # can fill in the missing course/character/kart. The Rust side decides
        # ready-vs-held; unidentified runs are held (pending_review), never auto-uploaded.
        if self._ipc is not None:
            import uuid
            from datetime import datetime, timezone
            from ..ipc.protocol import emit_run_finalized
            from ..database.replay_repo import _to_ms
            per_lap = self._lapstats.per_lap if self._lapstats is not None else {}
            laps = []
            for lap, txt in sorted(self._ts.splits.items()):
                stats = per_lap.get(int(lap), {})
                laps.append({
                    "lap":     int(lap),
                    "time_ms": _to_ms(txt),
                    "time_str": txt,
                    "coins":   stats.get("coins"),
                    "shrooms": stats.get("shrooms"),
                })
            self._ipc.emit(emit_run_finalized({
                "attempt_id": uuid.uuid4().hex,
                "course":     course,
                "source":     "ghost" if is_ghost else None,
                "status":     "finished" if completed else "reset",
                "character":  character,
                "kart":       None if is_ghost else sel.kart,
                "costume":    costume,
                "total_laps": self._laps.state.total_laps,
                "started_at": self._race_started_at,
                "ended_at":   datetime.now(timezone.utc).isoformat(),
                "total_time": best_total_time,
                "laps":       laps,
                "points":     [list(p) for p in self._mm_rec.points],
                "coins_gained":   self._lapstats.coins_gained if self._lapstats is not None else None,
                "coins_lost":     self._lapstats.coins_lost if self._lapstats is not None else None,
                "mushrooms_used": self._lapstats.mushrooms_used if self._lapstats is not None else None,
            }))

        self._finalized = True
        if is_ghost:
            self._ghost.disarm()
            self._emit_ghost_state()

    def _start_race(self, old: Screen):
        from datetime import datetime, timezone
        self._race_started_at = datetime.now(timezone.utc).isoformat()
        self._finalized = False

        sel       = self._selection.state
        course    = sel.course
        character = sel.character
        costume   = sel.costume

        # Full reset of minimap tracker
        self._minimap.reset()

        # Apply per-course ROI if available from DB
        roi = get_minimap_roi(course) if course else None
        if roi:
            self._minimap.set_roi(roi["x"], roi["y"], roi["w"], roi["h"])
            print(f"  [MinimapTracker] Using course ROI for '{course}'")
        else:
            self._minimap.set_roi(MINIMAP_ROI[0], MINIMAP_ROI[1],
                                  MINIMAP_ROI[2], MINIMAP_ROI[3])

        # Start recording
        self._mm_rec.start()

        # Seed minimap position from DB
        if course:
            seed = get_minimap_seed(course)
            if seed:
                stored_conf: Optional[float] = None
                if character and not self._ghost.recording:
                    stored_conf = get_minimap_threshold(course, character, costume or "")
                    if stored_conf is not None:
                        print(f"  [ThresholdStore] Using stored thr={stored_conf:.3f} "
                              f"for '{course}' / '{character}' / '{costume or 'Base'}'")
                self._minimap.seed(
                    seed["cx"], seed["cy"], seed.get("radius", 0),
                    frame=self.current_frame,
                    confident_score=stored_conf if stored_conf is not None
                                    else seed.get("conf"),
                )
                if stored_conf is not None:
                    self._minimap._calibrated = True
