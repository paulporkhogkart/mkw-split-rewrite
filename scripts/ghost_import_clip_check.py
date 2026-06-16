"""Manual integration check for ghost import against temp/ghostsample.mp4.

Runs the real engine over the clip with ghost import armed and asserts:
  * exactly ONE run_finalized with source == "ghost" (the full playthrough),
    course == Choco Mountain, with a non-null total_time;
  * the real race in the middle yields a NON-ghost run_finalized;
  * the second identical playthrough is NOT recorded (only one ghost run total).

Usage:  python scripts/ghost_import_clip_check.py [path-to-clip]
"""
import json
import os
import sys

# Allow running directly (`python scripts/ghost_import_clip_check.py`): put the repo
# root on sys.path so `mkw_tracker` imports without an editable install.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mkw_tracker.config.settings import get_settings
from mkw_tracker.database.migrations import apply_migrations


def main(clip: str) -> int:
    apply_migrations()
    settings = get_settings()
    lang = settings.get("switch2_language", "en_uk") or "en_uk"

    from mkw_tracker.detection.screen import Screen, ScreenDetector
    from mkw_tracker.detection.selection import SelectionTracker
    from mkw_tracker.race.laps import LapTracker
    from mkw_tracker.race.coins import CoinTracker
    from mkw_tracker.race.timestamp import TimestampTracker
    from mkw_tracker.race.finish import FinishLatch, load_finish_templates
    from mkw_tracker.race.mushrooms import MushroomTracker, load_mushroom_templates
    from mkw_tracker.race.lapstats import LapStatsTracker
    from mkw_tracker.race.timer import RaceTimer
    from mkw_tracker.minimap.tracker import MinimapTracker
    from mkw_tracker.minimap.recorder import MinimapRecorder
    from mkw_tracker.lifecycle.race import RaceLifecycle
    from mkw_tracker.utils.camera import VideoFileSource

    load_finish_templates(switch2_language=lang)
    load_mushroom_templates(switch2_language=lang)

    events = []

    class _Ipc:
        def emit(self, e): events.append(e)

    detector = ScreenDetector(switch2_language=lang)
    tracker = SelectionTracker(switch2_language=lang)
    laps, coins, ts = LapTracker(), CoinTracker(), TimestampTracker()
    timer = RaceTimer()
    finish = FinishLatch(templates=timer._templates)
    mush, minimap, rec = MushroomTracker(), MinimapTracker(), MinimapRecorder()
    lapstats = LapStatsTracker()
    lc = RaceLifecycle(selection=tracker, laps=laps, coins=coins, ts=ts, finish=finish,
                       mush=mush, lapstats=lapstats, minimap=minimap, mm_rec=rec,
                       timer=timer, ipc=_Ipc())
    detector.on_screen_change = lc.on_screen_change
    lc.arm_ghost()

    cap = VideoFileSource(clip, loop=False, target_fps=0)
    import numpy as np, cv2
    # The trackers throttle on wall-clock time.perf_counter() (selection 10Hz, timer
    # resync, finish scan). Processing the clip unpaced at max speed would starve those
    # throttles of scans on brief screens (e.g. COURSE_SELECT) and never commit a course.
    # Drive a frame-based monotonic clock so the throttles see video time at full speed.
    import time as _time
    _clk = [0.0]
    _time.perf_counter = lambda: _clk[0]
    while True:
        ok, frame = cap.read()
        if not ok or frame is None:
            break
        _clk[0] += 1.0 / 60.0                          # advance one 60fps frame
        if frame.shape[1] != 1920 or frame.shape[0] != 1080:
            frame = cv2.resize(frame, (1920, 1080), interpolation=cv2.INTER_LINEAR)
        lc.current_frame = frame
        screen, perf = detector.update(frame)
        eff = lc.effective_screen(screen)
        tracker.update(frame, screen, perf.current_score)   # real screen score gates selection
        if ts.total_time is None:
            ls, li = laps.update(frame, eff)
            coins.update(frame, eff); mush.update(frame, eff)
            ms = minimap.update(frame, eff)
            re = timer.update(frame, eff)
            lc.validate_ghost_start(re)
            if eff == Screen.RACING:
                rec.update(ms, ls.current_lap, re)
            on_final = ls.current_lap is not None and ls.total_laps and ls.current_lap == ls.total_laps
            fjd = finish.update(frame, eff, bool(on_final), lap_inc=li, estimate_ms=re) and ts.total_time is None
            tslap = (ls.current_lap - 1) if li and ls.current_lap is not None else ls.current_lap
            ts.update(frame, eff, capture_now=li or fjd, lap_number=tslap, is_finish=fjd)
        if ts.total_time is not None:
            lc.finalize_on_finish()

    finals = [json.loads(e) for e in events if '"run_finalized"' in e]
    ghosts = [r for r in finals if r.get("source") == "ghost"]
    non_ghost = [r for r in finals if r.get("source") != "ghost"]
    states = [json.loads(e) for e in events if '"ghost_import_state"' in e]
    print(f"run_finalized total={len(finals)}  ghost={len(ghosts)}  non_ghost={len(non_ghost)}")
    for r in finals:
        print(f"  {'GHOST' if r.get('source') == 'ghost' else 'run  '}: "
              f"status={r.get('status')!r} course={r.get('course')!r} total={r.get('total_time')!r} "
              f"char={r.get('character')!r} points={len(r.get('points', []))}")
    print(f"ghost_import_state events: {[(s.get('armed'), s.get('recording')) for s in states]}")
    ok = (len(ghosts) == 1
          and ghosts[0].get("course") == "Choco Mountain"
          and ghosts[0].get("total_time") is not None
          and ghosts[0].get("character") is None
          and len(non_ghost) >= 1                       # the real race in the middle
          and (states[-1] == {"type": "ghost_import_state", "armed": False, "recording": False}
               if states else False))                   # auto-disarmed at the end
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "temp/ghostsample.mp4"))
