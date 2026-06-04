"""RaceLifecycle - screen-change callback that drives all race state transitions."""
from typing import Optional

from ..detection.screen import Screen
from ..detection.selection import SelectionTracker
from ..minimap.tracker import MinimapTracker, MINIMAP_ROI
from ..minimap.recorder import MinimapRecorder
from ..minimap.player import MinimapPlayer
from ..race.laps import LapTracker
from ..race.coins import CoinTracker
from ..race.timestamp import TimestampTracker
from ..race.finish import FinishStillDetector
from ..race.mushrooms import MushroomTracker
from ..database.replay_repo import get_minimap_roi, get_minimap_seed, get_minimap_threshold


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
        mm_player:  MinimapPlayer,
        history_mode: bool = False,
        transition_count: Optional[list] = None,
        ipc=None,
    ):
        self._selection  = selection
        self._laps       = laps
        self._coins      = coins
        self._ts         = ts
        self._finish     = finish
        self._mush       = mush
        self._minimap    = minimap
        self._mm_rec     = mm_rec
        self._mm_player  = mm_player
        self._history_mode = history_mode
        self._transition_count = transition_count if transition_count is not None else [0]

        self._ipc = ipc

        self._paused_from_racing = False
        self._resuming_race      = False

        # Set to (course, time_str) when a run sets a new PB; cleared by main loop.
        self.pending_pb_event = None

        # The most recent full frame (set externally each loop iteration)
        self.current_frame = None

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
                self._mm_player.stop()
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
                self._mm_player.stop()
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

    # ── Private helpers ─────────────────────────────────────────────────────

    def _pause(self):
        self._mm_rec.pause()
        self._mm_player.stop()
        self._paused_from_racing = True
        print("  [Race] Paused (entering pause screen)")

    def _resume(self):
        self._resuming_race      = True
        self._paused_from_racing = False
        self._mm_rec.resume()
        if self._mm_player._replays:
            self._mm_player.start(offset_ms=self._mm_rec._elapsed_ms())
        print("  [Race] Resumed")

    def _clear_race_state(self):
        self._laps.reset()
        self._coins.reset()
        self._ts.reset()
        self._finish.reset()
        self._mush.reset()
        self._minimap.reset()
        print("  [reset] Race stats cleared")

    def _finalize_recording(self, completed: bool):
        """
        Save the current recording.
        completed=True  → race finished; calibrate and save threshold; PB eligible.
        completed=False → aborted; save history only, no calibration.
        """
        sel       = self._selection.state
        course    = sel.course
        character = sel.character
        costume   = sel.costume

        best_total_time: Optional[str] = None
        if completed:
            # Total time comes from the TimestampTracker; FinishStillDetector only
            # flags that the timer froze (it carries no time of its own).
            best_total_time = self._ts.total_time

        if completed and best_total_time and not self._minimap._calibrated:
            new_threshold = self._minimap.calibrate_from_race()
            self._mm_rec.retroactive_filter(new_threshold)
            if course and character:
                from ..database.replay_repo import set_minimap_threshold
                set_minimap_threshold(course, character, costume or "", new_threshold)

        # Emit the full finalized-attempt payload for the Tauri app to upload.
        # (Pure detector: emit only; no network. Built before save() clears points.)
        if self._ipc is not None and course:
            import uuid
            from datetime import datetime, timezone
            from ..ipc.protocol import emit_run_finalized
            from ..database.replay_repo import _to_ms
            laps = [{"lap": int(lap), "time_ms": _to_ms(txt)}
                    for lap, txt in sorted(self._ts.splits.items())]
            self._ipc.emit(emit_run_finalized({
                "attempt_id": uuid.uuid4().hex,
                "course":     course,
                "status":     "finished" if completed else "reset",
                "character":  character,
                "kart":       sel.kart,
                "costume":    costume,
                "started_at": None,
                "ended_at":   datetime.now(timezone.utc).isoformat(),
                "total_time": best_total_time,
                "laps":       laps,
                "points":     [[t, cx, cy, sc] for (t, cx, cy, sc) in self._mm_rec.points],
            }))

        replay_id = self._mm_rec.save(course, character=character, costume=costume,
                                      kart=sel.kart, total_time=best_total_time,
                                      lap_splits=dict(self._ts.splits))
        if completed and best_total_time and course and replay_id is not None:
            from ..database.replay_repo import get_pb
            pb = get_pb(course)
            if pb and pb.get("id") == replay_id:
                self.pending_pb_event = (course, best_total_time)

    def _start_race(self, old: Screen):
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

        # Load and start replay
        replay_mode = "history" if self._history_mode else "others"
        if course and self._mm_player.load(course, mode=replay_mode):
            self._mm_player.start()

        # Seed minimap position from DB
        if course:
            seed = get_minimap_seed(course)
            if seed:
                stored_conf: Optional[float] = None
                if character:
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
