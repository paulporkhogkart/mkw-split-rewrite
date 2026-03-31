"""Entry point — wires all components; runs cv2 capture loop + asyncio IPC thread."""
import argparse
import os
import time
from collections import deque
from typing import Optional

import cv2
import numpy as np

from .database.connection import get_connection, close_connection
from .database.migrations import apply_migrations
from .database.replay_repo import export_mkwreplay
from .config.settings import get_settings
from .detection.screen import ScreenDetector
from .detection.selection import SelectionTracker
from .race.laps import LapTracker
from .race.coins import CoinTracker
from .race.timestamp import TimestampTracker
from .race.finish import FinishDetector, load_finish_templates
from .race.mushrooms import MushroomTracker, load_mushroom_templates, MUSHROOM_ROI, MUSHROOM_TEMPLATES
from .minimap.tracker import MinimapTracker
from .minimap.recorder import MinimapRecorder
from .minimap.player import MinimapPlayer
from .overlay.debug import draw_debug_rois, draw_selection_rois
from .overlay.race_hud import (draw_lap_rois, draw_coin_rois, draw_timestamp_rois,
                                draw_finish_roi, draw_mushroom_roi)
from .overlay.minimap import draw_minimap_crosshair
from .overlay.panels import draw_screen_badge, draw_legend, draw_state_panel
from .lifecycle.race import RaceLifecycle
from .ipc.sidecar import IpcServer
from .ipc.protocol import (parse_inbound, emit_ready, emit_screen_change, emit_selection_update,
                            emit_lap_update, emit_coin_update, emit_mush_update, emit_finish,
                            emit_pb_achieved, emit_pb_export, emit_state, emit_devices_list,
                            emit_error)
from .utils.camera import build_camera_source


_WINDOW = 60   # rolling-average window size (frames)


def _handle_ipc_command(msg: dict, ipc: IpcServer, detector, settings,
                        minimap: MinimapTracker, lifecycle: RaceLifecycle,
                        show_debug: list, cap):
    """Dispatch a single inbound IPC command."""
    t = msg.get("type", "")

    if t == "update_config":
        key   = msg.get("key", "")
        value = msg.get("value")
        settings.update(key, value)
        settings.reload([key])

    elif t == "get_state":
        # Full snapshot emitted on next frame — just mark as needed
        pass

    elif t == "force_screen":
        screen_name = msg.get("screen", "")
        from .detection.screen import Screen
        try:
            detector.force_screen(Screen[screen_name])
        except KeyError:
            ipc.emit(emit_error(f"Unknown screen: {screen_name!r}"))

    elif t == "toggle_debug":
        show_debug[0] = bool(msg.get("enabled", not show_debug[0]))

    elif t == "export_pb":
        course = msg.get("course", "")
        payload = export_mkwreplay(course)
        if payload:
            ipc.emit(emit_pb_export(course, payload))
        else:
            ipc.emit(emit_error(f"No PB found for course {course!r}"))

    elif t == "set_seed":
        from .database.replay_repo import set_minimap_seed
        set_minimap_seed(
            msg.get("course", ""),
            msg.get("cx", 0),
            msg.get("cy", 0),
            msg.get("radius", 0),
        )

    elif t == "set_roi":
        from .database.replay_repo import set_minimap_roi
        set_minimap_roi(
            msg.get("course", ""),
            msg.get("x", 0),
            msg.get("y", 0),
            msg.get("w", 0),
            msg.get("h", 0),
        )

    elif t == "list_devices":
        from .utils.camera import list_dshow_video_devices
        devices    = list_dshow_video_devices()
        configured = settings.get("camera_device", "")
        active     = getattr(cap, "device_name", "")
        ipc.emit(emit_devices_list(devices, configured, active))


def run(args):
    # ── Database setup ───────────────────────────────────────────────────────
    apply_migrations()
    settings = get_settings()

    # ── Template loading ─────────────────────────────────────────────────────
    load_finish_templates()
    load_mushroom_templates()

    # ── Tracker construction ─────────────────────────────────────────────────
    transition_count = [0]

    detector  = ScreenDetector(on_screen_change=None)
    tracker   = SelectionTracker(purge_tight=args.purge_tight)
    laps      = LapTracker()
    coins     = CoinTracker()
    ts        = TimestampTracker()
    finish    = FinishDetector(scan_interval=0.0)
    mush      = MushroomTracker()
    minimap   = MinimapTracker()
    mm_rec    = MinimapRecorder()
    mm_player = MinimapPlayer()

    # ── IPC server ───────────────────────────────────────────────────────────
    broadcaster = None
    if args.ws_port is not None:
        from .ipc.broadcaster import EventBroadcaster
        broadcaster = EventBroadcaster(port=args.ws_port)

    ipc = IpcServer(broadcaster=broadcaster)
    if not args.no_ipc:
        ipc.start()

    lifecycle = RaceLifecycle(
        selection=tracker,
        laps=laps,
        coins=coins,
        ts=ts,
        finish=finish,
        mush=mush,
        minimap=minimap,
        mm_rec=mm_rec,
        mm_player=mm_player,
        history_mode=args.history,
        transition_count=transition_count,
        ipc=ipc,
    )
    detector.on_screen_change = lifecycle.on_screen_change

    # ── Camera ───────────────────────────────────────────────────────────────
    # Set Windows timer resolution to 1ms for the lifetime of this process.
    # The default is 15.6ms, which makes cv2.waitKey(1) / Event.wait(0.001)
    # sleep for up to 15.6ms — the primary cause of variable input lag.
    import ctypes as _ctypes
    _ctypes.windll.winmm.timeBeginPeriod(1)

    configured_device = settings.get("camera_device", "") or None
    cap = build_camera_source(width=1920, height=1080, fps=60,
                              device_name=configured_device)
    print(f"Camera: {cap.width}x{cap.height} @ {cap.fps} fps")
    print("Screen detector running. Press 'q' to quit.\n")

    ipc.emit(emit_ready(version="dev" if args.no_ipc else "sidecar"))

    # ── Per-loop state ───────────────────────────────────────────────────────
    frame_times:   deque = deque(maxlen=_WINDOW)
    update_ms_buf: deque = deque(maxlen=_WINDOW)
    tells_buf:     deque = deque(maxlen=_WINDOW)

    show_debug  = [True]    # Tab toggles this
    current_frame = [None]

    # Previous-state snapshots for on-change IPC emission
    _prev_sel    = (None, None, None, None)   # (character, costume, kart, course)
    _prev_lap    = (None, None)               # (current_lap, total_laps)
    _prev_coins  = None
    _prev_mush   = 0
    _prev_finish = False

    # ── Main capture loop ────────────────────────────────────────────────────
    while True:
        ret, frame = cap.read()
        if not ret:
            print("Failed to grab frame")
            break

        t_frame = time.perf_counter()
        frame_times.append(t_frame)

        # Expose current frame to lifecycle (for minimap seeding)
        current_frame[0]      = frame
        lifecycle.current_frame = frame

        # ── Drain IPC queue ──────────────────────────────────────────────────
        while not ipc.inbound_queue.empty():
            msg = ipc.inbound_queue.get_nowait()
            _handle_ipc_command(msg, ipc, detector, settings,
                                 minimap, lifecycle, show_debug, cap)

        # ── Update all trackers ──────────────────────────────────────────────
        screen, perf  = detector.update(frame)
        selection     = tracker.update(frame, screen, perf.current_score)

        _race_complete = ts.total_time is not None

        if not _race_complete:
            lap_state, lap_inc = laps.update(frame, screen)
            coin_state         = coins.update(frame, screen)
            mush_state         = mush.update(frame, screen)
            mm_state           = minimap.update(frame, screen)
            mm_rec.update(mm_state)
        else:
            lap_state, lap_inc = laps.state, False
            coin_state         = coins.state
            mush_state         = mush.state
            mm_state           = minimap.state

        finish_state          = finish.update(frame, screen)
        finish_just_detected  = finish_state.detected and ts.total_time is None

        # ── Calibrate on finish detection ────────────────────────────────────
        if finish_just_detected and not minimap._calibrated:
            sel = selection
            new_thr = minimap.calibrate_from_race()
            mm_rec.retroactive_filter(new_thr)
            if sel.course and sel.character:
                from .database.replay_repo import set_minimap_threshold
                set_minimap_threshold(sel.course, sel.character,
                                      sel.costume or "", new_thr)

        # ── Calibrate on final lap crossing ──────────────────────────────────
        if lap_inc and not minimap._calibrated:
            total_laps = lap_state.total_laps or 0
            if lap_state.current_lap == total_laps and total_laps > 0:
                sel     = selection
                new_thr = minimap.calibrate_from_race()
                mm_rec.retroactive_filter(new_thr)
                if sel.course and sel.character:
                    from .database.replay_repo import set_minimap_threshold
                    set_minimap_threshold(sel.course, sel.character,
                                          sel.costume or "", new_thr)

        # ── Timestamp update (with burst triggers) ───────────────────────────
        if not _race_complete:
            _ts_lap = (
                (lap_state.current_lap - 1)
                if lap_inc and lap_state.current_lap is not None
                else lap_state.current_lap
            )
            ts_state = ts.update(
                frame, screen,
                capture_now=lap_inc or finish_just_detected,
                lap_number=_ts_lap,
                is_finish=finish_just_detected,
            )
        else:
            ts_state = ts.state

        # ── Perf metrics ─────────────────────────────────────────────────────
        update_ms_buf.append(perf.update_ms)
        tells_buf.append(perf.tells_evaluated)

        if len(frame_times) >= 2:
            span    = frame_times[-1] - frame_times[0]
            avg_fps = (len(frame_times) - 1) / span if span > 0 else 0.0
        else:
            avg_fps = 0.0
        avg_ms    = sum(update_ms_buf) / len(update_ms_buf)
        avg_tells = sum(tells_buf)     / len(tells_buf)
        peak_ms   = max(update_ms_buf)

        # ── IPC on-change events ─────────────────────────────────────────────
        sel_key = (selection.character, selection.costume,
                   selection.kart, selection.course)
        if sel_key != _prev_sel and any(sel_key):
            ipc.emit(emit_selection_update(*sel_key))
            _prev_sel = sel_key

        lap_key = (lap_state.current_lap, lap_state.total_laps)
        if lap_key != _prev_lap and any(lap_key):
            completed_lap = (lap_state.current_lap or 0) - 1
            split = ts.splits.get(completed_lap) if lap_inc else None
            ipc.emit(emit_lap_update(lap_state.current_lap, lap_state.total_laps, split))
            _prev_lap = lap_key

        if coin_state.coins != _prev_coins and coin_state.coins is not None:
            ipc.emit(emit_coin_update(coin_state.coins))
            _prev_coins = coin_state.coins

        if mush_state.count != _prev_mush:
            ipc.emit(emit_mush_update(mush_state.count))
            _prev_mush = mush_state.count

        if finish_just_detected and not _prev_finish:
            ipc.emit(emit_finish(
                finish_state.result,
                ts_state.total_time,
                dict(ts.splits),
            ))
            _prev_finish = True

        # Reset race-specific prev-state when the lap tracker clears (new race)
        if lap_key == (None, None) and _prev_lap != (None, None):
            _prev_lap    = (None, None)
            _prev_coins  = None
            _prev_mush   = 0
            _prev_finish = False

        # ── Draw ─────────────────────────────────────────────────────────────
        if not show_debug[0]:
            display = cv2.resize(frame, (1280, 720), interpolation=cv2.INTER_LINEAR)
            mm_player.draw(None, display, screen)
            cv2.imshow("MKW Tracker", display)
        else:
            # Full-res ROI boxes
            draw_debug_rois(
                frame, None,
                current_screen=screen,
                current_score=perf.current_score,
                candidate_screens=detector._candidate_screens(),
                candidate_scores=perf.candidate_scores,
                tells_by_screen=detector._tells_by_screen,
            )
            draw_selection_rois(frame, None, screen, selection)
            draw_lap_rois(frame, None, screen, lap_state)
            draw_coin_rois(frame, None, screen, coin_state)
            draw_timestamp_rois(frame, None, screen, ts_state)
            draw_finish_roi(frame, None, screen, finish_state)
            draw_mushroom_roi(frame, None, screen, mush_state)
            draw_minimap_crosshair(frame, None, screen, mm_state, tracker=minimap)

            display = cv2.resize(frame, (1280, 720), interpolation=cv2.INTER_LINEAR)

            # 720p labels
            draw_debug_rois(
                None, display,
                current_screen=screen,
                current_score=perf.current_score,
                candidate_screens=detector._candidate_screens(),
                candidate_scores=perf.candidate_scores,
                tells_by_screen=detector._tells_by_screen,
            )
            draw_selection_rois(None, display, screen, selection)
            draw_lap_rois(None, display, screen, lap_state)
            draw_coin_rois(None, display, screen, coin_state)
            draw_timestamp_rois(None, display, screen, ts_state)
            draw_finish_roi(None, display, screen, finish_state)
            draw_mushroom_roi(None, display, screen, mush_state)
            draw_minimap_crosshair(None, display, screen, mm_state, tracker=minimap)
            mm_player.draw(None, display, screen)

            draw_screen_badge(display, screen)
            draw_legend(display)
            draw_state_panel(
                display, screen, perf, selection, lap_state, coin_state,
                ts_state, finish_state, mush_state, mm_state,
                avg_fps=avg_fps, avg_ms=avg_ms, avg_tells=avg_tells,
                peak_ms=peak_ms, transition_count=transition_count[0],
                lap_splits=ts.splits,
                char_template=minimap._char_template,
            )

            cv2.imshow("MKW Tracker", display)

        key = cap.waitKey(1) & 0xFF
        if key == ord("q"):
            break
        if key == 9:   # Tab — toggle debug overlay
            show_debug[0] = not show_debug[0]
        if key == ord("m"):
            minimap.debug_log = not minimap.debug_log
            print(f"  [mm debug] per-frame logging {'ON' if minimap.debug_log else 'OFF'}")
        if key == ord("d"):
            _debug_dump(frame, laps, coins, ts, mush)

    _ctypes.windll.winmm.timeEndPeriod(1)
    cap.release()
    cv2.destroyAllWindows()
    close_connection()
    import os as _os; _os._exit(0)


def _debug_dump(frame, laps, coins, ts, mush):
    """Dump diagnostic ROI crops to debug_laps/."""
    print("\n[debug] Dumping ROI diagnostics to debug_laps/ ...")
    laps.debug_frame(frame)
    coins.debug_frame(frame)
    ts.debug_frame(frame)
    os.makedirs("debug_laps", exist_ok=True)
    x1, y1, x2, y2 = MUSHROOM_ROI
    crop = frame[y1:y2, x1:x2]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if len(crop.shape) == 3 else crop
    _, processed = cv2.threshold(gray, 170, 255, cv2.THRESH_BINARY)
    cv2.imwrite("debug_laps/mush_roi_crop.png", processed)
    for count in (3, 2, 1):
        tmpl = MUSHROOM_TEMPLATES.get(count)
        if tmpl is None:
            print(f"  {count}mush: template not loaded")
            continue
        if tmpl.shape[0] > processed.shape[0] or tmpl.shape[1] > processed.shape[1]:
            print(f"  {count}mush: SKIP — tmpl {tmpl.shape} > roi {processed.shape}")
            continue
        result = cv2.matchTemplate(processed, tmpl, cv2.TM_CCOEFF_NORMED)
        score  = float(cv2.minMaxLoc(result)[1])
        print(f"  {count}mush: {score:.3f}  (tmpl {tmpl.shape})")
    print("[debug] Done.\n")


def main():
    parser = argparse.ArgumentParser(description="MKW split tracker")
    parser.add_argument("--purge-tight", action="store_true",
                        help="Delete all _tight.png files on startup.")
    parser.add_argument("--history", action="store_true",
                        help="Load 'last 100 runs' replay mode.")
    parser.add_argument("--no-ipc", action="store_true",
                        help="Disable stdin/stdout IPC (run as standalone).")
    parser.add_argument("--ws-port", type=int, metavar="PORT", default=None,
                        help="Broadcast all events on a local WebSocket (e.g. 8765).")
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
