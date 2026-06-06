"""Entry point  - wires all components; runs cv2 capture loop + asyncio IPC thread."""
import argparse
import os
import time
from collections import deque
from typing import Optional

import cv2
import numpy as np

from .database.connection import get_connection, close_connection
from .database.config_repo import get_config as _get_config_direct, delete_configs_like
from .database.migrations import apply_migrations
from .database.replay_repo import export_mkwreplay
from .config.settings import get_settings
from .config.defaults import Defaults
from .detection.screen import Screen, ScreenDetector

# Per-screen frame rate budgets.
# The main loop sleeps at the end of each iteration to enforce these caps,
# regardless of how fast the camera source or OS scheduler delivers frames.
_FULL_RATE_SCREENS    = {Screen.RACING, Screen.GHOST, Screen.UNKNOWN_RACE_ACTIVE}
_RACE_FRAME_INTERVAL  = 1.0 / 30.0   # 30 fps during races
_MENU_FRAME_INTERVAL  = 1.0 / 15.0   # 15 fps on menus / selection screens

# OpenCL (GPU) acceleration via OpenCV T-API.
# Enabled automatically if the driver supports it; falls back silently to CPU.
cv2.ocl.setUseOpenCL(True)
_ocl_available = cv2.ocl.haveOpenCL() and cv2.ocl.useOpenCL()
if _ocl_available:
    dev = cv2.ocl.Device.getDefault()
    print(f"[GPU] OpenCL enabled  - {dev.name()} ({dev.vendorName()})")
else:
    print("[GPU] OpenCL not available  - running on CPU")
from .detection.selection import SelectionTracker
from .race.laps import LapTracker
from .race.coins import CoinTracker
from .race.timestamp import TimestampTracker
from .race.finish import FinishDetector, FinishStillDetector, load_finish_templates
from .race.mushrooms import MushroomTracker, load_mushroom_templates, MUSHROOM_ROI, MUSHROOM_TEMPLATES
from .race.lapstats import LapStatsTracker
from .minimap.tracker import MinimapTracker, MINIMAP_ROI as _MINIMAP_ROI
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
                            emit_lap_update, emit_coin_update, emit_mush_update, emit_finish, emit_split_recorded,
                            emit_pb_export, emit_state, emit_devices_list,
                            emit_error, emit_heartbeat, emit_frame_data, emit_template_score,
                            emit_template_saved, emit_template_images, emit_tells_list, emit_rois_list,
                            emit_camera_paused, emit_camera_resumed, emit_camera_status,
                            emit_roi_preview, emit_asset_preview, emit_asset_saved,
                            emit_calibration_result, emit_calib_capture,
                            emit_minimap_update, minimap_update_payload,
                            emit_replay_paths, emit_minimap_sample, emit_screen_thumbs,
                            emit_option_lists)
from .utils.camera import build_camera_source
from .utils.normalize import Normalizer


_WINDOW = 60   # rolling-average window size (frames)

# All templates and ROI coordinates are in 1920×1080 space.
# Frames from the capture card are normalised to this size immediately
# after reading so every downstream component works at a fixed resolution.
_REF_W, _REF_H = 1920, 1080

# Capture-normalization LUT (per-channel gain+offset+gamma).  Set in run()
# once settings has loaded; _norm() reads it on every frame.  Pass-through
# until initialised, so import-time callers see no behaviour change.
_normalizer: Normalizer = None

# Calibration slot cache.  Maps slot (1..7) to a copied frame snapshot
# captured via capture_calib_frame IPC.  solve_calibration pairs each cached
# slot with its matching shipped reference and runs solve_transform on the
# pooled patches.  Cleared via clear_calib_frames or successful solve.
_calib_capture_slots: dict = {}

# Mirrors mkw_tracker.utils.calibrate.NUM_SLOTS.  Kept here as a literal so
# the IPC dispatch stays cheap (no per-message import) and aligned with the
# wizard's slot pills.
_CALIB_SLOTS: tuple = (1, 2, 3, 4, 5, 6, 7)


def _norm(frame):
    """Resize to 1920×1080 if needed.

    NOTE: the capture-normalization LUT (global gain/offset/gamma) is
    intentionally DISABLED for this patch - see the commented block below.
    """
    if frame is None:
        return None
    h, w = frame.shape[:2]
    if not (w == _REF_W and h == _REF_H):
        if _ocl_available:
            # Upload → resize on GPU → download.  Worth it for large source frames
            # (e.g. 2560×1440) where the resize dominates per-frame cost.
            frame = cv2.resize(cv2.UMat(frame), (_REF_W, _REF_H),
                               interpolation=cv2.INTER_LINEAR).get()
        else:
            frame = cv2.resize(frame, (_REF_W, _REF_H),
                               interpolation=cv2.INTER_LINEAR)
    # --- TEMP DISABLED (this patch): global capture-calibration offsets --------
    # The per-channel gain/offset/gamma LUT is intentionally NOT applied, so the
    # calibration sliders AND auto-calibration have zero effect on detection -
    # even if non-identity values are saved in the DB.  Left commented (not
    # deleted) for easy restore: just uncomment the two lines below.  The
    # Normalizer object, calibration IPC, and wizard are all left intact.
    # if _normalizer is not None:
    #     frame = _normalizer.apply(frame)
    return frame


def _handle_ipc_command(msg: dict, ipc: IpcServer, detector, settings,
                        minimap: MinimapTracker, lifecycle: RaceLifecycle,
                        show_debug: list, cap, current_frame: list,
                        setup_mode: list, tracker=None):
    """Dispatch a single inbound IPC command."""
    t = msg.get("type", "")

    if t == "update_config":
        key   = msg.get("key", "")
        value = msg.get("value")
        settings.update(key, value)
        settings.reload([key])
        if key == "switch2_language":
            _lang = str(value) if value else "en_uk"
            detector.reload_language(_lang)
            if tracker is not None:
                tracker.reload_language(_lang)
                ipc.emit(emit_option_lists(**tracker.option_lists()))
            load_finish_templates(switch2_language=_lang)
            load_mushroom_templates(switch2_language=_lang)
        elif key.startswith("calib_") and _normalizer is not None:
            _normalizer.mark_dirty()

    elif t == "get_state":
        # Emit a full snapshot of all tracker states immediately.
        sel   = tracker.state if tracker is not None else None
        laps  = lifecycle._laps.state
        coins = lifecycle._coins.state
        ts    = lifecycle._ts.state
        mush  = lifecycle._mush.state
        from .detection.screen import Screen as _Screen
        state_dict = {
            "screen":   detector.current_screen.name if detector.current_screen is not None else "UNKNOWN",
            # Selection
            "character":     sel.character     if sel else None,
            "character_conf": round(sel.character_conf, 4) if sel else 0.0,
            "costume":       sel.costume       if sel else None,
            "costume_conf":  round(sel.costume_conf, 4)  if sel else 0.0,
            "kart":          sel.kart          if sel else None,
            "kart_conf":     round(sel.kart_conf, 4)     if sel else 0.0,
            "course":        sel.course        if sel else None,
            "course_conf":   round(sel.course_conf, 4)   if sel else 0.0,
            # Race
            "current_lap": laps.current_lap,
            "total_laps":  laps.total_laps,
            "coins":       coins.coins,
            "mushrooms":   mush.count,
            # Ranked per-field selection candidates
            "candidates": tracker.score_maps if tracker is not None else {
                "char": [], "kart": [], "course": [], "costume": []
            },
        }
        ipc.emit(emit_state(state_dict))

    elif t == "force_screen":
        screen_name = msg.get("screen", "")
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

    elif t == "get_replay_paths":
        from .database.replay_repo import replay_paths as _replay_paths
        from .database.connection import get_connection as _get_conn
        from .ipc.protocol import emit_replay_paths as _emit_rp
        _course = msg.get("course", "")
        _paths = _replay_paths(_get_conn(), _course)
        ipc.emit(_emit_rp(_course, _paths))

    elif t == "get_pb_splits":
        from .database.replay_repo import get_pb_splits as _get_pb_splits
        from .database.connection import get_connection as _get_conn
        from .ipc.protocol import emit_pb_splits as _emit_pbs
        _course = msg.get("course", "")
        _row = _get_conn().execute(
            "SELECT total_time_ms FROM replays WHERE player='me' AND course=? AND is_pb=1",
            (_course,),
        ).fetchone()
        _total = _row["total_time_ms"] if _row else None
        ipc.emit(_emit_pbs(_course, _get_pb_splits(_course), _total))

    elif t == "get_screen_thumbs":
        # Downscaled per-screen reference shots for the edit-mode graph nodes.
        # Prefer the requested language, fall back to en_uk per file; user captures
        # (data_dir) take precedence over the bundled copies.
        import os as _os, base64 as _b64
        from .utils.paths import resource_path as _rp, data_dir as _dd
        from .detection.screen import GRAPH_NODE_SHOTS as _SHOTS
        _lang = msg.get("lang") or "en_uk"
        _thumbs = {}
        for _scr, _file in _SHOTS.items():
            _img = None
            for _cand in (
                _os.path.join(str(_dd()), "screenshots", _lang, _file),
                _rp(_os.path.join("screenshots", _lang, _file)),
                _os.path.join(str(_dd()), "screenshots", "en_uk", _file),
                _rp(_os.path.join("screenshots", "en_uk", _file)),
            ):
                if _os.path.exists(_cand):
                    _img = cv2.imread(_cand, cv2.IMREAD_COLOR)
                    if _img is not None:
                        break
            if _img is None:
                continue
            _h, _w = _img.shape[:2]
            _tw = 240
            _th = max(1, int(round(_h * _tw / _w)))
            _small = cv2.resize(_img, (_tw, _th), interpolation=cv2.INTER_AREA)
            _ok, _buf = cv2.imencode(".png", _small)
            if _ok:
                _thumbs[_scr.name] = _b64.b64encode(_buf.tobytes()).decode("ascii")
        ipc.emit(emit_screen_thumbs(_thumbs))

    elif t == "get_minimap_sample":
        import base64 as _b64
        from .database.replay_repo import get_minimap_seed as _get_seed
        from .ipc.protocol import emit_minimap_sample as _emit_ms
        _course = msg.get("course", "")
        _seed = _get_seed(_course)
        _png_b64 = None
        if _seed is not None:
            # The minimap_seeds table stores only (cx, cy, radius, conf) -
            # no image blob.  The in-memory character template (float32
            # HSV-CLAHE) is only available while a race is active.
            # Try the live tracker first; fall back to null when unavailable.
            _tmpl = getattr(minimap, "_char_template", None)
            if _tmpl is not None:
                try:
                    import cv2 as _cv2
                    import numpy as _np
                    # Convert float32 HSV-CLAHE channels back to uint8 BGR for display
                    _h = (_tmpl[:, :, 0] * 179.0).astype(_np.uint8)
                    _s = (_tmpl[:, :, 1] * 255.0).astype(_np.uint8)
                    _v = (_tmpl[:, :, 2] * 255.0).astype(_np.uint8)
                    _hsv = _np.stack([_h, _s, _v], axis=2)
                    _bgr = _cv2.cvtColor(_hsv, _cv2.COLOR_HSV2BGR)
                    _, _buf = _cv2.imencode(".png", _bgr)
                    _png_b64 = _b64.b64encode(_buf.tobytes()).decode("ascii")
                except Exception:
                    _png_b64 = None
        ipc.emit(_emit_ms(_course, _png_b64))

    elif t == "mark_setup_complete":
        settings.update("setup_complete", 1)
        setup_mode[0] = False

    elif t == "reset_to_defaults":
        # 1. Wipe all persisted tell overrides from the DB.
        for _pat in ("tell_roi_%", "tell_thresh_%", "tell_req_also_%",
                     "tell_alt_%", "tell_and_thresh_%", "tell_alt_thresh_%"):
            delete_configs_like(_pat)
        # 2. Reset all ROI and detection constants to Defaults (keep language /
        #    camera_device / setup_complete untouched).
        _d = Defaults()
        _roi_and_numeric_keys = [
            "lap_current_roi", "lap_total_roi",
            "coin_left_roi", "coin_right_roi",
            "finish_roi", "mushroom_roi", "minimap_roi",
            "char_name_roi", "costume_roi", "kart_name_roi", "course_name_roi",
            "selection_match_threshold", "char_confirm_frames", "costume_loss_frames",
            "lap_digit_threshold", "coin_digit_threshold",
            "timestamp_digit_threshold", "finish_match_threshold", "finish_confirm_frames",
            "mushroom_match_threshold", "mushroom_loss_frames", "mushroom_gain_frames",
            "confirm_loss_frames",
        ]
        _defaults_dict = _d.as_dict()
        for _k in _roi_and_numeric_keys:
            if _k in _defaults_dict:
                settings.update(_k, _defaults_dict[_k])
        settings.reload()
        # 3. Rebuild in-memory tells from hardcoded defaults.
        detector.reset_to_defaults()
        # 4. Emit updated state to the frontend.
        ipc.emit(emit_tells_list(detector.get_tells_config()))
        ipc.emit(emit_rois_list({
            "char_name":   settings.get("char_name_roi"),
            "costume":     settings.get("costume_roi"),
            "kart_name":   settings.get("kart_name_roi"),
            "course_name": settings.get("course_name_roi"),
            "lap_current": settings.get("lap_current_roi"),
            "lap_total":   settings.get("lap_total_roi"),
            "coin_left":   settings.get("coin_left_roi"),
            "coin_right":  settings.get("coin_right_roi"),
            "finish":      settings.get("finish_roi"),
            "mushroom":    settings.get("mushroom_roi"),
        }))

    elif t == "get_calibration":
        # Echo current calib_* values and any cached capture slots so the wizard
        # can populate its sliders and capture badges without guessing.
        if _normalizer is not None:
            _c = _normalizer.current()
            ipc.emit(emit_calibration_result(
                _c["gain_r"], _c["gain_g"], _c["gain_b"],
                _c["offset_r"], _c["offset_g"], _c["offset_b"],
                _c["gamma"], 0.0, ok=True, is_echo=True))
        # Always send every slot's status (captured or not) so the UI can hydrate.
        for _slot in _CALIB_SLOTS:
            ipc.emit(emit_calib_capture(_slot, _slot in _calib_capture_slots))

    elif t == "capture_calib_frame":
        _slot = int(msg.get("slot", 0))
        if _slot not in _CALIB_SLOTS:
            ipc.emit(emit_calib_capture(_slot, False,
                error=f"Invalid slot {_slot} (expected 1..{_CALIB_SLOTS[-1]})"))
            return
        frame = current_frame[0]
        if frame is None:
            ipc.emit(emit_calib_capture(_slot, False, error="No camera frame available"))
            return
        # Copy so the cached frame is not aliased to the rolling capture buffer.
        _calib_capture_slots[_slot] = frame.copy()
        ipc.emit(emit_calib_capture(_slot, True))

    elif t == "clear_calib_frames":
        _calib_capture_slots.clear()
        for _slot in _CALIB_SLOTS:
            ipc.emit(emit_calib_capture(_slot, False))

    elif t == "solve_calibration":
        from .utils.calibrate import solve_transform, load_reference_frames
        refs = load_reference_frames()
        if not refs:
            ipc.emit(emit_calibration_result(
                1.0, 1.0, 1.0, 0, 0, 0, 1.0, 0.0, ok=False,
                error="No reference images shipped. Use scripts/capture_calibration_ref.py "
                      "to save images/calibration/switch_hdr_test_1.png and ..._2.png."))
            return
        if not _calib_capture_slots:
            ipc.emit(emit_calibration_result(
                1.0, 1.0, 1.0, 0, 0, 0, 1.0, 0.0, ok=False,
                error="No frames captured. Use the Capture Test 1/2 buttons first."))
            return
        pairs = [(live, refs[slot]) for slot, live in _calib_capture_slots.items() if slot in refs]
        if not pairs:
            ipc.emit(emit_calibration_result(
                1.0, 1.0, 1.0, 0, 0, 0, 1.0, 0.0, ok=False,
                error=f"Captured slots {sorted(_calib_capture_slots)} don't match "
                      f"any shipped reference (have {sorted(refs)})."))
            return
        result = solve_transform(pairs)
        _apply_calibration_result(settings, detector, ipc, result,
                                  reset_tell_overrides=bool(msg.get("reset_tell_overrides", False)))
        # Captures consumed; clear so a fresh pass requires fresh captures.
        _calib_capture_slots.clear()
        for _slot in _CALIB_SLOTS:
            ipc.emit(emit_calib_capture(_slot, False))

    elif t == "calibrate_now":
        # Legacy single-shot path: grab current frame and solve against whichever
        # single reference is shipped.  The wizard uses solve_calibration instead.
        frame = current_frame[0]
        if frame is None:
            ipc.emit(emit_calibration_result(
                1.0, 1.0, 1.0, 0, 0, 0, 1.0, 0.0,
                ok=False, error="No camera frame available - open the camera first."))
            return
        from .utils.calibrate import solve_transform, load_reference_frames
        refs = load_reference_frames()
        if not refs:
            ipc.emit(emit_calibration_result(
                1.0, 1.0, 1.0, 0, 0, 0, 1.0, 0.0,
                ok=False,
                error="No reference images shipped. Use the two-step capture flow in the wizard, "
                      "or drop PNGs at images/calibration/switch_hdr_test_{1,2}.png."))
            return
        # Use whichever slot is available (prefer slot 1).
        _slot = 1 if 1 in refs else next(iter(refs))
        result = solve_transform([(frame, refs[_slot])])
        _apply_calibration_result(settings, detector, ipc, result,
                                  reset_tell_overrides=bool(msg.get("reset_tell_overrides", False)))

    elif t == "reset_calibration":
        _d  = Defaults().as_dict()
        _keys = ["calib_enabled", "calib_gain_r", "calib_gain_g", "calib_gain_b",
                 "calib_offset_r", "calib_offset_g", "calib_offset_b", "calib_gamma"]
        for _k in _keys:
            settings.update(_k, _d[_k])
        settings.reload(_keys)
        if _normalizer is not None:
            _normalizer.mark_dirty()
        ipc.emit(emit_calibration_result(
            _d["calib_gain_r"], _d["calib_gain_g"], _d["calib_gain_b"],
            _d["calib_offset_r"], _d["calib_offset_g"], _d["calib_offset_b"],
            _d["calib_gamma"], 0.0, ok=True))

    elif t == "list_devices":
        from .utils.camera import list_dshow_video_devices
        devices    = list_dshow_video_devices()
        configured   = settings.get("camera_device", "")
        active       = getattr(cap, "device_name", "")
        audio_label  = settings.get("audio_device_label", "")
        ipc.emit(emit_devices_list(devices, configured, active, audio_label))

    elif t == "capture_frame":
        import base64 as _b64
        frame = current_frame[0]
        if frame is None:
            return
        roi    = msg.get("roi")        # [x1, y1, x2, y2] or None
        draw   = msg.get("draw_roi", False)
        scale  = msg.get("scale", 0.333)
        label  = msg.get("label", "")
        work   = frame.copy() if draw and roi else frame
        if draw and roi:
            x1, y1, x2, y2 = [int(v) for v in roi]
            cv2.rectangle(work, (x1, y1), (x2, y2), (0, 220, 80), 4)
        h, w  = work.shape[:2]
        new_w = max(1, int(w * scale))
        new_h = max(1, int(h * scale))
        small = cv2.resize(work, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        _, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 72])
        ipc.emit(emit_frame_data(_b64.b64encode(buf.tobytes()).decode("ascii"),
                                  new_w, new_h, label))

    elif t == "update_region":
        sn = msg.get("screen", "")
        res = detector.update_region(
            sn, int(msg.get("group", 0)), int(msg.get("region", 0)),
            roi=msg.get("roi"), thresh=msg.get("thresh"),
            grayscale=msg.get("grayscale"), kind=msg.get("kind"),
            icon_roi=msg.get("icon_roi"))
        if res is not None:
            _persist_tell_tree(settings, sn, detector)
            ipc.emit(emit_tells_list(detector.get_tells_config()))

    elif t in ("add_region", "remove_region", "add_group", "remove_group"):
        sn = msg.get("screen", "")
        if t == "add_region":
            res = detector.add_region(sn, int(msg.get("group", 0)), roi=msg.get("roi"))
        elif t == "remove_region":
            res = detector.remove_region(sn, int(msg.get("group", 0)), int(msg.get("region", 0)))
        elif t == "add_group":
            res = detector.add_group(sn, roi=msg.get("roi"))
        else:
            res = detector.remove_group(sn, int(msg.get("group", 0)))
        if res is not None:
            _persist_tell_tree(settings, sn, detector)
            ipc.emit(emit_tells_list(detector.get_tells_config()))

    elif t == "reset_tell":
        sn = msg.get("screen", "")
        res = detector.reset_tell(sn)
        if res is not None:
            # Drop the persisted override so it stays default across restarts.
            from .detection.screen import Screen as _Scr, TELL_ALIAS_GROUPS as _TAG
            try:
                _canon = _Scr[sn]
                for _s in [sn] + [a.name for a in _TAG.get(_canon, [])]:
                    delete_configs_like(f"tell_tree_{_s}")
            except KeyError:
                pass
            ipc.emit(emit_tells_list(detector.get_tells_config()))

    elif t == "capture_region_template":
        frame = current_frame[0]
        if frame is not None:
            res = detector.capture_region_template(
                frame, msg.get("screen", ""),
                int(msg.get("group", 0)), int(msg.get("region", 0)))
            if res:
                _persist_tell_tree(settings, msg.get("screen", ""), detector)
                ipc.emit(emit_template_saved(**res))
            else:
                ipc.emit(emit_error(f"Failed to capture region for {msg.get('screen')!r}"))

    elif t == "test_region":
        frame = current_frame[0]
        res = detector.test_region(frame, msg.get("screen", ""),
                                   int(msg.get("group", 0)), int(msg.get("region", 0)))
        if res is not None:
            ipc.emit(emit_template_score(**res))

    elif t == "get_region_images":
        frame = current_frame[0]
        res = detector.get_region_images(frame, msg.get("screen", ""),
                                         int(msg.get("group", 0)), int(msg.get("region", 0)))
        if res is not None:
            ipc.emit(emit_template_images(**res))

    elif t == "list_tells":
        ipc.emit(emit_tells_list(detector.get_tells_config()))

    elif t == "list_rois":
        ipc.emit(emit_rois_list(_rois_payload(settings)))

    elif t == "reset_roi":
        # Restore one selection/HUD ROI config key to its packaged default.
        key = msg.get("key", "")
        from .config.defaults import Defaults
        _dflts = Defaults().as_dict()
        if key in _dflts:
            settings.update(key, _dflts[key])
            settings.reload([key])
            ipc.emit(emit_rois_list(_rois_payload(settings)))

    elif t == "get_roi_preview":
        frame = current_frame[0]
        if frame is None:
            return
        roi = msg.get("roi")
        binary_thresh = msg.get("binary_thresh", 170)
        use_edges = msg.get("use_edges", False)
        if not roi or len(roi) < 4:
            return
        x1, y1, x2, y2 = [int(v) for v in roi]
        fh, fw = frame.shape[:2]
        x1 = max(0, min(x1, fw - 1));  x2 = max(x1 + 1, min(x2, fw))
        y1 = max(0, min(y1, fh - 1));  y2 = max(y1 + 1, min(y2, fh))
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return
        import base64 as _b64
        if use_edges:
            from .detection.templates import prepare_text_edges as _pte
            processed = _pte(crop)
        else:
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if len(crop.shape) == 3 else crop.copy()
            if binary_thresh is not None:
                _, processed = cv2.threshold(gray, int(binary_thresh), 255, cv2.THRESH_BINARY)
            else:
                _, processed = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        _, buf = cv2.imencode(".png", processed)
        ipc.emit(emit_roi_preview(_b64.b64encode(buf.tobytes()).decode("ascii")))

    elif t == "get_asset_template":
        import base64 as _b64
        frame = current_frame[0]
        category  = msg.get("category", "")
        item_name = msg.get("item_name", "")
        _lang = settings.get("switch2_language", "en_uk") or "en_uk"
        _roi_map = {
            "characters": settings.get("char_name_roi"),
            "karts":      settings.get("kart_name_roi"),
            "courses":    settings.get("course_name_roi"),
            "costumes":   settings.get("costume_roi"),
            "mushrooms":  settings.get("mushroom_roi"),
        }
        _dir_map = {
            "characters": f"images/characters/{_lang}",
            "karts":      f"images/karts/{_lang}",
            "courses":    f"images/courses/{_lang}",
            "costumes":   f"images/costumes/{_lang}",
            "mushrooms":  f"images/mushrooms/{_lang}",
        }
        _roi = _roi_map.get(category)
        _img_dir = _dir_map.get(category)
        if not _roi or not _img_dir or not item_name:
            return
        from .utils.paths import resource_path as _rp, data_dir as _dd
        # Check user data dir first, then resource path
        _tmpl_path = str(_dd() / f"{_img_dir}/{item_name}.png")
        if not os.path.exists(_tmpl_path):
            _tmpl_path = _rp(f"{_img_dir}/{item_name}.png")
        _template_img = None
        if os.path.exists(_tmpl_path):
            _tmpl = cv2.imread(_tmpl_path)
            if _tmpl is not None:
                # Costumes are matched on Canny edges - show the edge-processed
                # template so the preview matches what the matcher actually sees.
                if category == "costumes":
                    from .detection.templates import prepare_text_edges as _pte
                    _tmpl = _pte(_tmpl)
                _, _buf = cv2.imencode(".png", _tmpl)
                _template_img = _b64.b64encode(_buf.tobytes()).decode("ascii")
        _live_crop = None
        if frame is not None:
            _x1, _y1, _x2, _y2 = [int(v) for v in _roi]
            _crop = frame[_y1:_y2, _x1:_x2]
            if _crop.size > 0:
                if category == "costumes":
                    from .detection.templates import prepare_text_edges as _pte
                    _processed = _pte(_crop)
                elif category == "mushrooms":
                    _processed = cv2.cvtColor(_crop, cv2.COLOR_BGR2GRAY) if len(_crop.shape) == 3 else _crop.copy()
                else:
                    _gray = cv2.cvtColor(_crop, cv2.COLOR_BGR2GRAY) if len(_crop.shape) == 3 else _crop.copy()
                    _, _processed = cv2.threshold(_gray, 170, 255, cv2.THRESH_BINARY)
                _, _buf = cv2.imencode(".png", _processed)
                _live_crop = _b64.b64encode(_buf.tobytes()).decode("ascii")
        ipc.emit(emit_asset_preview(category, item_name, _template_img, _live_crop))

    elif t == "capture_asset_template":
        import base64 as _b64
        frame = current_frame[0]
        category  = msg.get("category", "")
        item_name = msg.get("item_name", "")
        _lang = settings.get("switch2_language", "en_uk") or "en_uk"
        _roi_map = {
            "characters": settings.get("char_name_roi"),
            "karts":      settings.get("kart_name_roi"),
            "courses":    settings.get("course_name_roi"),
            "costumes":   settings.get("costume_roi"),
            "mushrooms":  settings.get("mushroom_roi"),
        }
        _dir_map = {
            "characters": f"images/characters/{_lang}",
            "karts":      f"images/karts/{_lang}",
            "courses":    f"images/courses/{_lang}",
            "costumes":   f"images/costumes/{_lang}",
            "mushrooms":  f"images/mushrooms/{_lang}",
        }
        _roi = _roi_map.get(category)
        _img_dir = _dir_map.get(category)
        if not _roi or not _img_dir or not item_name or frame is None:
            return
        _x1, _y1, _x2, _y2 = [int(v) for v in _roi]
        _crop = frame[_y1:_y2, _x1:_x2]
        if _crop.size == 0:
            return
        from .utils.paths import data_dir as _dd
        _save_path = str(_dd() / f"{_img_dir}/{item_name}.png")
        os.makedirs(os.path.dirname(_save_path), exist_ok=True)
        if category == "mushrooms":
            # Grayscale (continuous-tone) to match the grayscale mushroom matcher
            _gray = cv2.cvtColor(_crop, cv2.COLOR_BGR2GRAY) if len(_crop.shape) == 3 else _crop.copy()
            cv2.imwrite(_save_path, _gray)
        else:
            # Selection templates are grayscale crops; load_edge_template_groups applies
            # the Canny edges at load time (identically to the live crop).  Costumes also
            # get synthetic background variants so their variable banner background can't
            # collapse the score (mirrors scripts/gen_selection_templates.py).
            _gray = cv2.cvtColor(_crop, cv2.COLOR_BGR2GRAY) if len(_crop.shape) == 3 else _crop.copy()
            if category == "costumes":
                import glob as _glob
                from .detection.templates import synth_bg_variants
                _basepath = _save_path[:-4]
                for _stale in _glob.glob(f"{_basepath}__*.png"):
                    os.remove(_stale)
                for _suffix, _img in synth_bg_variants(_gray).items():
                    cv2.imwrite(_save_path if _suffix == "" else f"{_basepath}__{_suffix}.png", _img)
            else:
                cv2.imwrite(_save_path, _gray)
        ipc.emit(emit_asset_saved(category, item_name))


def _rois_payload(settings) -> dict:
    """Current selection + HUD ROIs (pixel space) for a rois_list emit."""
    return {
        "char_name":   settings.get("char_name_roi"),
        "costume":     settings.get("costume_roi"),
        "kart_name":   settings.get("kart_name_roi"),
        "course_name": settings.get("course_name_roi"),
        "lap_current": settings.get("lap_current_roi"),
        "lap_total":   settings.get("lap_total_roi"),
        "coin_left":   settings.get("coin_left_roi"),
        "coin_right":  settings.get("coin_right_roi"),
        "finish":      settings.get("finish_roi"),
        "mushroom":    settings.get("mushroom_roi"),
    }


def _persist_tell_tree(settings, screen_name: str, detector) -> None:
    """Persist a canonical screen's full groups tree (and its aliases)."""
    from .detection.screen import Screen as _Scr, TELL_ALIAS_GROUPS as _TAG
    from .database.tell_repo import serialize_groups
    try:
        canon = _Scr[screen_name]
    except KeyError:
        return
    tell = detector._tells_by_screen.get(canon)
    if tell is None:
        return
    blob = serialize_groups(tell)
    for sn in [screen_name] + [a.name for a in _TAG.get(canon, [])]:
        settings.update(f"tell_tree_{sn}", blob)


def _apply_calibration_result(settings, detector, ipc, result: dict,
                               reset_tell_overrides: bool) -> None:
    """Persist a solver result, hot-reload the normalizer, optionally wipe
    per-tell threshold overrides, and emit the result to the wizard."""
    for _k in ("gain_r", "gain_g", "gain_b"):
        settings.update(f"calib_{_k}", float(result[_k]))
    for _k in ("offset_r", "offset_g", "offset_b"):
        settings.update(f"calib_{_k}", int(result[_k]))
    settings.update("calib_gamma", float(result["gamma"]))
    settings.reload(["calib_gain_r", "calib_gain_g", "calib_gain_b",
                     "calib_offset_r", "calib_offset_g", "calib_offset_b",
                     "calib_gamma"])
    if _normalizer is not None:
        _normalizer.mark_dirty()
    if reset_tell_overrides:
        for _pat in ("tell_thresh_%", "tell_alt_thresh_%", "tell_and_thresh_%"):
            delete_configs_like(_pat)
        from .detection.screen import TELLS as _SPEC_TELLS
        _spec = {_t.screen: _t for _t in _SPEC_TELLS}
        for _se, _tell in detector._tells_by_screen.items():
            _sp = _spec.get(_se)
            if _sp is None:
                continue
            _tell.binary_thresh        = _sp.binary_thresh
            _tell.alt_binary_thresh    = _sp.alt_binary_thresh
            _tell.required_also_thresh = list(_sp.required_also_thresh)
        ipc.emit(emit_tells_list(detector.get_tells_config()))
    ipc.emit(emit_calibration_result(
        result["gain_r"], result["gain_g"], result["gain_b"],
        result["offset_r"], result["offset_g"], result["offset_b"],
        result["gamma"], result["fit_quality"], ok=True))


_cleanup_done = False


def _do_cleanup(ctypes_lib, cap, ipc):
    """Release all resources. Safe to call more than once."""
    global _cleanup_done
    if _cleanup_done:
        return
    _cleanup_done = True
    try:
        ctypes_lib.windll.winmm.timeEndPeriod(1)
    except Exception:
        pass
    try:
        if cap is not None:
            cap.release()
    except Exception:
        pass
    try:
        cv2.destroyAllWindows()
    except Exception:
        pass
    try:
        ipc.stop()          # cancels WS server task → releases port 8765
    except Exception:
        pass
    try:
        close_connection()
    except Exception:
        pass


def run(args):
    # ── Database setup ───────────────────────────────────────────────────────
    apply_migrations()
    settings = get_settings()
    display_enabled = not args.no_display

    # ── Capture normalization ───────────────────────────────────────────────
    # Initialised after settings so _norm() can apply the LUT on every frame.
    # Hot-reloaded via mark_dirty() from the update_config IPC handler below.
    global _normalizer
    _normalizer = Normalizer(settings)

    # ── Language ─────────────────────────────────────────────────────────────
    switch2_language = settings.get("switch2_language", "en_uk") or "en_uk"

    # ── Template loading ─────────────────────────────────────────────────────
    load_finish_templates(switch2_language=switch2_language)
    load_mushroom_templates(switch2_language=switch2_language)

    # ── Tracker construction ─────────────────────────────────────────────────
    transition_count = [0]

    detector  = ScreenDetector(on_screen_change=None, switch2_language=switch2_language)

    # Apply any persisted boolean-tree overrides from the editor.  Stored as one
    # tell_tree_<SCREEN> JSON blob per screen (see database/tell_repo.py).
    from .database.tell_repo import groups_from_blob
    for _screen_enum, _tell in detector._tells_by_screen.items():
        _blob = _get_config_direct(f"tell_tree_{_screen_enum.name}")
        if _blob:
            _tell.groups = groups_from_blob(_blob)
    for _tell in detector._tells_by_screen.values():
        _tell.load(switch2_language)

    tracker   = SelectionTracker(purge_tight=args.purge_tight,
                                  switch2_language=switch2_language)
    laps      = LapTracker()
    coins     = CoinTracker()
    ts        = TimestampTracker()
    finish    = FinishStillDetector()   # final-lap finish via frozen timer (position-ROI scan disabled)
    mush      = MushroomTracker()
    minimap   = MinimapTracker()
    mm_rec    = MinimapRecorder()
    mm_player = MinimapPlayer()
    lapstats  = LapStatsTracker()

    # ── IPC server ───────────────────────────────────────────────────────────
    from .ipc.broadcaster import EventBroadcaster
    ws_port = args.ws_port if args.ws_port is not None else 8765
    broadcaster = EventBroadcaster(port=ws_port)

    ipc = IpcServer(broadcaster=broadcaster)
    if not args.no_ipc:
        ipc.start()

    import ctypes as _ctypes
    import atexit as _atexit

    # Register cleanup so the WS port is released even on unexpected exits.
    # cap is None here but gets updated via _cap_ref before the loop runs.
    _cap_ref = [None]

    def _atexit_cleanup():
        _do_cleanup(_ctypes, _cap_ref[0], ipc)

    _atexit.register(_atexit_cleanup)

    lifecycle = RaceLifecycle(
        selection=tracker,
        laps=laps,
        coins=coins,
        ts=ts,
        finish=finish,
        mush=mush,
        lapstats=lapstats,
        minimap=minimap,
        mm_rec=mm_rec,
        mm_player=mm_player,
        history_mode=args.history,
        transition_count=transition_count,
        ipc=ipc,
    )
    detector.on_screen_change = lifecycle.on_screen_change

    # ── Setup mode ───────────────────────────────────────────────────────────
    setup_mode = [not bool(settings.get("setup_complete", 0))]
    if setup_mode[0]:
        print("[Setup] First-time setup required  - running in setup mode.")

    # ── Camera ───────────────────────────────────────────────────────────────
    # Set Windows timer resolution to 1ms for the lifetime of this process.
    # The default is 15.6ms, which makes cv2.waitKey(1) / Event.wait(0.001)
    # sleep for up to 15.6ms  - the primary cause of variable input lag.
    _ctypes.windll.winmm.timeBeginPeriod(1)

    # Camera is always deferred - the frontend picks the device after enumerating
    # both browser and Python sources, then sends open_camera with the matched name.
    # This guarantees both feeds always use the same physical device.
    cap = None
    _cap_ref[0] = cap

    # ── DEV test video source ────────────────────────────────────────────────
    # --video replaces the capture device with a (looping) video file so screen
    # detection / selection can be tested against recorded footage.  Skips the
    # setup wizard (no camera to pick) and camera-control IPC is ignored below
    # so the file source is never torn down by the frontend.
    _video_mode = bool(getattr(args, "video", None))
    if _video_mode:
        from .utils.camera import VideoFileSource
        try:
            cap = VideoFileSource(args.video, loop=not args.video_once,
                                  target_fps=args.video_fps)
            _cap_ref[0] = cap
            setup_mode[0] = False
        except Exception as _e:
            print(f"[Camera] --video failed: {_e}")
            return

    ipc.emit(emit_ready(
        version="dev" if args.no_ipc else "sidecar",
        setup_complete=not setup_mode[0],
        app_language=settings.get("app_language", "en_uk") or "en_uk",
        switch2_language=switch2_language,
    ))
    ipc.emit(emit_option_lists(**tracker.option_lists()))
    print("Screen detector running. Press 'q' to quit.\n")

    # ── Per-loop state ───────────────────────────────────────────────────────
    frame_times:   deque = deque(maxlen=_WINDOW)
    update_ms_buf: deque = deque(maxlen=_WINDOW)
    tells_buf:     deque = deque(maxlen=_WINDOW)

    show_debug    = [True]   # Tab toggles this
    current_frame = [None]
    cam_paused    = [False]  # True while setup wizard holds the camera

    from .utils.paths import resource_path as _rp
    broadcaster.enable_autotemplate(current_frame, settings, detector,
                                    os.path.dirname(_rp("images")))
    del _rp

    # Previous-state snapshots for on-change IPC emission
    _prev_sel    = (None, None, None, None)   # (character, costume, kart, course)
    _prev_lap    = (None, None)               # (current_lap, total_laps)
    _prev_coins  = None
    _prev_mush        = 0
    _prev_finish      = False
    _want_finish_emit = False   # True once finish detected; held until ts burst completes
    _finish_result    = None    # cached result string while waiting for ts burst
    _finish_wait      = 0       # frames waited (timeout fallback)
    _emitted_splits: dict = {}  # lap → time already sent via split_recorded

    _last_heartbeat = 0.0

    # Minimap stream: throttle to ~15 Hz and skip unchanged payloads
    _MM_EMIT_INTERVAL    = 1.0 / 15.0   # seconds between minimap_update emits
    _last_mm_emit        = 0.0
    _prev_mm_payload_key = None          # (cx, cy, radius, track_state) dedup tuple

    # ── Main capture loop ────────────────────────────────────────────────────
    frame = None
    while True:
        # ── Camera-paused state (setup wizard holds the device) ───────────────
        while cam_paused[0]:
            time.sleep(0.02)
            t_now = time.perf_counter()
            if t_now - _last_heartbeat >= 0.2:
                ipc.emit(emit_heartbeat(0.0, "PAUSED", False))
                _last_heartbeat = t_now
            while not ipc.inbound_queue.empty():
                _msg = ipc.inbound_queue.get_nowait()
                if _msg.get("type") in ("resume_camera", "open_camera"):
                    _dev = settings.get("camera_device", "") or None
                    if cap is not None:
                        cap.release()
                        cap = None
                    current_frame[0] = None
                    try:
                        cap = build_camera_source(device_name=_dev)
                        _ret, _frame = cap.read()
                        if _ret and _frame is not None:
                            current_frame[0] = _norm(_frame)
                            ipc.emit(emit_camera_status(ok=True,
                                                        width=_REF_W, height=_REF_H,
                                                        device=getattr(cap, "device_name", "")))
                        else:
                            cap.release()
                            cap = None
                            ipc.emit(emit_camera_status(ok=False,
                                                        error="Device opened but no frames received"))
                    except Exception as _e:
                        cap = None
                        ipc.emit(emit_camera_status(ok=False, error=str(_e)))
                    _cap_ref[0] = cap
                    cam_paused[0] = False
                    ipc.emit(emit_camera_resumed())
                else:
                    _handle_ipc_command(_msg, ipc, detector, settings,
                                         minimap, lifecycle, show_debug, cap,
                                         current_frame, setup_mode, tracker)

        # ── Read frame ───────────────────────────────────────────────────────
        if cap is not None:
            ret, frame = cap.read()
            if ret:
                frame = _norm(frame)
                t_frame = time.perf_counter()
                frame_times.append(t_frame)
                current_frame[0] = frame
                lifecycle.current_frame = frame
            elif setup_mode[0]:
                # In setup mode a missing frame is non-fatal
                time.sleep(0.033)
                t_frame = time.perf_counter()
            else:
                print("Failed to grab frame")
                break
        else:
            time.sleep(0.033)
            t_frame = time.perf_counter()

        # ── Drain IPC queue ──────────────────────────────────────────────────
        while not ipc.inbound_queue.empty():
            msg = ipc.inbound_queue.get_nowait()
            if msg.get("type") == "pause_camera" and not _video_mode:
                if cap is not None:
                    cap.release()
                    cap = None
                cam_paused[0] = True
                ipc.emit(emit_camera_paused())
            elif msg.get("type") == "open_camera" and not _video_mode:
                # Close any existing capture first (handles device changes)
                if cap is not None:
                    cap.release()
                    cap = None
                current_frame[0] = None
                try:
                    _dev = settings.get("camera_device", "") or None
                    cap = build_camera_source(device_name=_dev)
                    # Attempt a real frame read to confirm the device is actually
                    # delivering frames before reporting success to the frontend.
                    _ret, _frame = cap.read()
                    if _ret and _frame is not None:
                        current_frame[0] = _norm(_frame)
                        ipc.emit(emit_camera_status(ok=True,
                                                    width=_REF_W, height=_REF_H,
                                                    device=getattr(cap, "device_name", "")))
                        print(f"[Camera] Opened: {cap.device_name!r} {cap.width}x{cap.height} @ {cap.fps} fps"
                              + (f" (normalised to {_REF_W}x{_REF_H})"
                                 if cap.width != _REF_W or cap.height != _REF_H else ""))
                    else:
                        cap.release()
                        cap = None
                        ipc.emit(emit_camera_status(ok=False,
                                                    error="Device opened but no frames received"))
                except Exception as _e:
                    cap = None
                    ipc.emit(emit_camera_status(ok=False, error=str(_e)))
                    print(f"[Camera] open_camera failed: {_e}")
                _cap_ref[0] = cap
            else:
                _handle_ipc_command(msg, ipc, detector, settings,
                                     minimap, lifecycle, show_debug, cap,
                                     current_frame, setup_mode, tracker)

        # ── Setup mode: emit heartbeat and skip all tracking ─────────────────
        if setup_mode[0]:
            if t_frame - _last_heartbeat >= 0.2:
                ipc.emit(emit_heartbeat(0.0, "SETUP", False))
                _last_heartbeat = t_frame
            if display_enabled and frame is not None:
                display = cv2.resize(frame, (1280, 720), interpolation=cv2.INTER_LINEAR)
                cv2.imshow("MKW Tracker", display)
                key = cap.waitKey(1) & 0xFF if cap is not None else cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    break
            continue

        # Guard: skip tracking frames if camera hasn't delivered yet
        if frame is None:
            continue

        # ── Update all trackers ──────────────────────────────────────────────
        screen, perf  = detector.update(frame)
        selection     = tracker.update(frame, screen, perf.current_score)

        _race_complete = ts.total_time is not None

        if not _race_complete:
            lap_state, lap_inc = laps.update(frame, screen)
            coin_state         = coins.update(frame, screen)
            mush_state         = mush.update(frame, screen)
            lapstats.update(mush_state.count)
            mm_state           = minimap.update(frame, screen)
            mm_rec.update(mm_state)
        else:
            lap_state, lap_inc = laps.state, False
            coin_state         = coins.state
            mush_state         = mush.state
            mm_state           = minimap.state

        # Final-lap finish: the timer freezes (no gold/white flash) on the final
        # lap.  See FinishStillDetector.  (Position-ROI scan stays disabled.)
        _on_final_lap = (lap_state.current_lap is not None
                         and lap_state.total_laps
                         and lap_state.current_lap == lap_state.total_laps)
        finish_just_detected  = (finish.update(frame, screen, bool(_on_final_lap))
                                 and ts.total_time is None)

        # Stop replay playback the instant the timer is detected as stopped (final
        # time), not when the results graphic later appears / we leave RACING.
        # (Recording already halts here too: mm_rec.update is gated on _race_complete
        # = ts.total_time, which the finish burst populates.)
        if finish_just_detected:
            mm_player.stop()

        if finish_just_detected and lap_state.current_lap is not None:
            lapstats.record_lap(lap_state.current_lap, coin_state.coins)

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
            if lap_inc and _ts_lap is not None:
                lapstats.record_lap(_ts_lap, coin_state.coins)
        else:
            ts_state = ts.state

        # ── Finished run: emit + upload the instant the final time locks ──────
        # (the timer-freeze), rather than waiting for the POST_TIME_TRIAL results
        # screen. Idempotent: the lifecycle's _finalized guard fires this once.
        if ts.total_time is not None:
            lifecycle.finalize_on_finish()

        # ── Emit any newly-recorded splits ───────────────────────────────────
        # ts.splits is populated incrementally as burst scans complete.
        # Emit each new entry as soon as it appears so the UI updates in real time.
        for lap, split_time in ts.splits.items():
            if lap not in _emitted_splits:
                is_final = (ts.total_time is not None and lap == max(ts.splits))
                ipc.emit(emit_split_recorded(lap, split_time, is_final=is_final))
                _emitted_splits[lap] = split_time

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

        # ── Heartbeat (5 Hz) ─────────────────────────────────────────────────
        if t_frame - _last_heartbeat >= 0.2:
            ipc.emit(emit_heartbeat(
                avg_fps, screen.name, mm_state.tracking,
                current_score=perf.current_score,
                candidate_scores={k.name: v for k, v in perf.candidate_scores.items()},
                selection_candidates=tracker.score_maps if tracker is not None else {},
            ))
            _last_heartbeat = t_frame

        # ── IPC on-change events ─────────────────────────────────────────────
        sel_key = (selection.character, selection.costume,
                   selection.kart, selection.course)
        if sel_key != _prev_sel and any(sel_key):
            ipc.emit(emit_selection_update(
                *sel_key,
                char_conf=selection.character_conf,
                costume_conf=selection.costume_conf,
                kart_conf=selection.kart_conf,
                course_conf=selection.course_conf,
            ))
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

        # Minimap update: stream at ~15 Hz during RACING, skip unchanged state.
        # Only emits when screen == RACING (the tracker itself returns early on other
        # screens, so mm_state stays stale - no point forwarding it).
        if screen == Screen.RACING:
            if t_frame - _last_mm_emit >= _MM_EMIT_INTERVAL:
                _last_mm_emit = t_frame
                # Send the actual per-map ROI the tracker is using (not the default
                # constant) so the UI draws the correct box for the detected course.
                _active_roi = (
                    getattr(minimap, "_roi_x", _MINIMAP_ROI[0]),
                    getattr(minimap, "_roi_y", _MINIMAP_ROI[1]),
                    getattr(minimap, "_roi_w", _MINIMAP_ROI[2]),
                    getattr(minimap, "_roi_h", _MINIMAP_ROI[3]),
                )
                _mm_p = minimap_update_payload(mm_state, _active_roi)
                if _mm_p is not None:
                    _mm_key = (_mm_p["cx"], _mm_p["cy"], _mm_p["radius"],
                               _mm_p["track_state"], tuple(_mm_p["roi"]))
                    if _mm_key != _prev_mm_payload_key:
                        ipc.emit(emit_minimap_update(mm_state, _active_roi))
                        _prev_mm_payload_key = _mm_key
                elif _prev_mm_payload_key is not None:
                    # Lock lost / left RACING - clear dedup key; nothing emitted
                    # (frontend stops receiving updates).
                    _prev_mm_payload_key = None
        elif _prev_mm_payload_key is not None:
            # Left RACING - clear dedup so next race re-emits from scratch
            _prev_mm_payload_key = None

        # Arm the deferred finish emit when finish is first detected.
        # The timestamp burst starts on this same frame, so total_time / splits
        # won't be ready yet  - defer until ts.total_time is populated.
        if finish_just_detected and not _prev_finish and not _want_finish_emit:
            _want_finish_emit = True
            _finish_result    = None   # no position overlay (finish-ROI disabled)
            _finish_wait      = 0

        if _want_finish_emit:
            _finish_wait += 1
            if ts.total_time is not None or _finish_wait > 90:  # ~3 s timeout at 30 fps
                ipc.emit(emit_finish(_finish_result, ts.total_time, dict(ts.splits)))
                _want_finish_emit = False
                _prev_finish      = True

        # Reset race-specific prev-state when the lap tracker clears (new race)
        if lap_key == (None, None) and _prev_lap != (None, None):
            _prev_lap         = (None, None)
            _prev_coins       = None
            _prev_mush        = 0
            _prev_finish      = False
            _want_finish_emit = False
            _finish_result    = None
            _finish_wait      = 0
            _emitted_splits   = {}

        # ── Draw ─────────────────────────────────────────────────────────────
        if display_enabled:
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
                # draw_finish_roi(frame, None, screen, finish_state)   # finish disabled
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
                # draw_finish_roi(None, display, screen, finish_state)   # finish disabled
                draw_mushroom_roi(None, display, screen, mush_state)
                draw_minimap_crosshair(None, display, screen, mm_state, tracker=minimap)
                mm_player.draw(None, display, screen)

                draw_screen_badge(display, screen)
                draw_legend(display)
                draw_state_panel(
                    display, screen, perf, selection, lap_state, coin_state,
                    ts_state, finish, mush_state, mm_state,
                    avg_fps=avg_fps, avg_ms=avg_ms, avg_tells=avg_tells,
                    peak_ms=peak_ms, transition_count=transition_count[0],
                    lap_splits=ts.splits,
                    char_template=minimap._char_template,
                )

                cv2.imshow("MKW Tracker", display)

            key = (cap.waitKey(1) if cap is not None else cv2.waitKey(1)) & 0xFF
            if key == ord("q"):
                break
            if key == 9:   # Tab  - toggle debug overlay
                show_debug[0] = not show_debug[0]
            if key == ord("m"):
                minimap.debug_log = not minimap.debug_log
                print(f"  [mm debug] per-frame logging {'ON' if minimap.debug_log else 'OFF'}")
            if key == ord("d"):
                _debug_dump(frame, laps, coins, ts, mush)

        # ── Frame rate cap ────────────────────────────────────────────────────
        # Sleep for the remainder of the target frame budget so the loop never
        # spins faster than intended regardless of camera or OS delivery rate.
        _target = _RACE_FRAME_INTERVAL if screen in _FULL_RATE_SCREENS else _MENU_FRAME_INTERVAL
        _spare  = _target - (time.perf_counter() - t_frame)
        if _spare > 0:
            time.sleep(_spare)

    _do_cleanup(_ctypes, cap, ipc)


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
            print(f"  {count}mush: SKIP  - tmpl {tmpl.shape} > roi {processed.shape}")
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
    parser.add_argument("--no-display", action="store_true",
                        help="Suppress OpenCV window (headless mode for Tauri).")
    parser.add_argument("--video", metavar="PATH", default=None,
                        help="DEV TEST: read frames from a video file instead of the "
                             "capture device (skips the camera setup wizard).")
    parser.add_argument("--video-fps", type=float, default=None, metavar="FPS",
                        help="Playback rate for --video. Default: file fps capped at 60. "
                             "0 = as fast as possible (no real-time pacing).")
    parser.add_argument("--video-once", action="store_true",
                        help="With --video, stop at end of file instead of looping.")
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
