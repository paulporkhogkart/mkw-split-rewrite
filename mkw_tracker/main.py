"""Entry point — wires all components; runs cv2 capture loop + asyncio IPC thread."""
import argparse
import os
import time
from collections import deque
from typing import Optional

import cv2
import numpy as np

from .database.connection import get_connection, close_connection
from .database.config_repo import get_config as _get_config_direct
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
                            emit_lap_update, emit_coin_update, emit_mush_update, emit_finish, emit_split_recorded,
                            emit_pb_achieved, emit_pb_export, emit_state, emit_devices_list,
                            emit_error, emit_heartbeat, emit_frame_data, emit_template_score,
                            emit_template_saved, emit_template_images, emit_tells_list, emit_rois_list,
                            emit_camera_paused, emit_camera_resumed, emit_camera_status,
                            emit_roi_preview, emit_asset_preview, emit_asset_saved)
from .utils.camera import build_camera_source


_WINDOW = 60   # rolling-average window size (frames)

# All templates and ROI coordinates are in 1920×1080 space.
# Frames from the capture card are normalised to this size immediately
# after reading so every downstream component works at a fixed resolution.
_REF_W, _REF_H = 1920, 1080


def _norm(frame):
    """Resize frame to the 1920×1080 reference resolution if needed."""
    if frame is None:
        return None
    h, w = frame.shape[:2]
    if w == _REF_W and h == _REF_H:
        return frame
    return cv2.resize(frame, (_REF_W, _REF_H), interpolation=cv2.INTER_LINEAR)


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
            load_mushroom_templates(switch2_language=_lang)

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

    elif t == "mark_setup_complete":
        settings.update("setup_complete", 1)
        setup_mode[0] = False

    elif t == "list_devices":
        from .utils.camera import list_dshow_video_devices
        devices    = list_dshow_video_devices()
        configured = settings.get("camera_device", "")
        active     = getattr(cap, "device_name", "")
        ipc.emit(emit_devices_list(devices, configured, active))

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

    elif t == "get_template_images":
        roi_key = msg.get("roi_key", "primary")
        result  = detector.get_template_images(current_frame[0], msg.get("screen", ""),
                                               roi_key=roi_key)
        if result:
            ipc.emit(emit_template_images(**result))
        else:
            ipc.emit(emit_error(f"Unknown screen: {msg.get('screen')!r}"))

    elif t == "test_template":
        frame = current_frame[0]
        if frame is not None:
            roi_key = msg.get("roi_key", "primary")
            result  = detector.test_tell_by_name(frame, msg.get("screen", ""),
                                                  roi_key=roi_key)
            if result:
                ipc.emit(emit_template_score(**result))
            else:
                ipc.emit(emit_error(f"Unknown screen: {msg.get('screen')!r}"))

    elif t == "capture_template":
        frame = current_frame[0]
        if frame is not None:
            roi_key = msg.get("roi_key", "primary")
            result  = detector.capture_and_save_template(frame, msg.get("screen", ""),
                                                          roi_key=roi_key)
            if result:
                ipc.emit(emit_template_saved(**result))
            else:
                ipc.emit(emit_error(f"Failed to capture template for: {msg.get('screen')!r}"))

    elif t == "add_required_also":
        sn  = msg.get("screen", "")
        roi = msg.get("roi")
        result = detector.add_required_also(sn, roi=roi)
        if result is not None:
            _persist_tell_structure(settings, sn, detector)
            ipc.emit(emit_tells_list(detector.get_tells_config()))

    elif t == "remove_required_also":
        sn    = msg.get("screen", "")
        index = int(msg.get("index", 0))
        result = detector.remove_required_also(sn, index=index)
        if result is not None:
            _persist_tell_structure(settings, sn, detector)
            ipc.emit(emit_tells_list(detector.get_tells_config()))

    elif t == "add_alt":
        sn  = msg.get("screen", "")
        roi = msg.get("roi")
        result = detector.add_alt(sn, roi=roi)
        if result is not None:
            _persist_tell_structure(settings, sn, detector)
            ipc.emit(emit_tells_list(detector.get_tells_config()))

    elif t == "remove_alt":
        sn = msg.get("screen", "")
        result = detector.remove_alt(sn)
        if result is not None:
            _persist_tell_structure(settings, sn, detector)
            ipc.emit(emit_tells_list(detector.get_tells_config()))

    elif t == "list_tells":
        ipc.emit(emit_tells_list(detector.get_tells_config()))

    elif t == "list_rois":
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

    elif t == "update_tell":
        screen_name          = msg.get("screen", "")
        roi                  = msg.get("roi")
        binary_thresh        = msg.get("binary_thresh")
        required_also_rois   = msg.get("required_also_rois")
        required_also_thresh = msg.get("required_also_thresh")
        alt_binary_thresh    = msg.get("alt_binary_thresh")
        alt_roi              = msg.get("alt_roi")
        detector.update_tell(screen_name, roi=roi, binary_thresh=binary_thresh,
                             required_also_rois=required_also_rois,
                             required_also_thresh=required_also_thresh,
                             alt_binary_thresh=alt_binary_thresh,
                             alt_roi=alt_roi)
        # Persist primary ROI and threshold (per screen including aliases)
        from .detection.screen import Screen as _Scr, TELL_ALIAS_GROUPS as _TAG
        try:
            _canon = _Scr[screen_name]
            for _sn in [screen_name] + [a.name for a in _TAG.get(_canon, [])]:
                if roi:
                    settings.update(f"tell_roi_{_sn}", roi)
                if binary_thresh is not None:
                    settings.update(f"tell_thresh_{_sn}", int(binary_thresh))
        except KeyError:
            pass
        # Persist structure changes (required_also rois/thresh, alt roi/thresh)
        if required_also_rois is not None or required_also_thresh is not None \
                or alt_binary_thresh is not None or alt_roi is not None:
            _persist_tell_structure(settings, screen_name, detector)

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
        if category == "costumes":
            # Save raw colour — load_template_dir applies edge processing at load time
            cv2.imwrite(_save_path, _crop)
        else:
            # Binarise before saving so the on-disk template matches the live crop space
            _gray = cv2.cvtColor(_crop, cv2.COLOR_BGR2GRAY) if len(_crop.shape) == 3 else _crop.copy()
            _, _binary = cv2.threshold(_gray, 170, 255, cv2.THRESH_BINARY)
            cv2.imwrite(_save_path, _binary)
        ipc.emit(emit_asset_saved(category, item_name))


def _persist_tell_structure(settings, screen_name: str, detector) -> None:
    """Persist full required_also + alt structure for a canonical screen and its aliases.

    req_also is saved as [[path, [x1,y1,x2,y2]], ...].
    alt is saved as [path, [x1,y1,x2,y2]] when present, or False when explicitly
    removed (so startup loading can distinguish "removed" from "never configured").
    """
    from .detection.screen import Screen as _Scr, TELL_ALIAS_GROUPS as _TAG
    try:
        canon = _Scr[screen_name]
    except KeyError:
        return
    tell = detector._tells_by_screen.get(canon)
    if tell is None:
        return
    req_also = [[p, list(r)] for p, r in tell.required_also]
    alt = ([tell.alt_image_path, list(tell.alt_roi)]
           if tell.alt_image_path and tell.alt_roi else False)
    req_also_thresh = list(tell.required_also_thresh)
    while len(req_also_thresh) < len(tell.required_also):
        req_also_thresh.append(170)
    alt_thresh = tell.alt_binary_thresh if tell.alt_image_path else None
    for sn in [screen_name] + [a.name for a in _TAG.get(canon, [])]:
        settings.update(f"tell_req_also_{sn}", req_also)
        settings.update(f"tell_alt_{sn}", alt)
        settings.update(f"tell_and_thresh_{sn}", req_also_thresh)
        settings.update(f"tell_alt_thresh_{sn}", alt_thresh)


def run(args):
    # ── Database setup ───────────────────────────────────────────────────────
    apply_migrations()
    settings = get_settings()
    display_enabled = not args.no_display

    # ── Language ─────────────────────────────────────────────────────────────
    switch2_language = settings.get("switch2_language", "en_uk") or "en_uk"

    # ── Template loading ─────────────────────────────────────────────────────
    load_finish_templates()
    load_mushroom_templates(switch2_language=switch2_language)

    # ── Tracker construction ─────────────────────────────────────────────────
    transition_count = [0]

    detector  = ScreenDetector(on_screen_change=None, switch2_language=switch2_language)

    # Apply any persisted tell overrides from the wizard.
    # These keys are not in Defaults so settings.get() would never find them
    # (Settings._load only caches keys present in Defaults).  Read directly
    # from the DB via get_config so the values survive app restarts.
    for _screen_enum, _tell in detector._tells_by_screen.items():
        _sn = _screen_enum.name
        _roi_ov        = _get_config_direct(f"tell_roi_{_sn}")
        _thresh_ov     = _get_config_direct(f"tell_thresh_{_sn}")
        _req_also_ov   = _get_config_direct(f"tell_req_also_{_sn}")   # [[path,[roi]], ...]
        _alt_ov        = _get_config_direct(f"tell_alt_{_sn}")        # [path,[roi]] | False
        _and_thresh_ov = _get_config_direct(f"tell_and_thresh_{_sn}") # [int, ...]
        _alt_thresh_ov = _get_config_direct(f"tell_alt_thresh_{_sn}") # int | None
        if _roi_ov and isinstance(_roi_ov, list) and len(_roi_ov) >= 4:
            _tell.roi = tuple(int(v) for v in _roi_ov)
        if _thresh_ov is not None:
            _tell.binary_thresh = int(_thresh_ov)
        if _req_also_ov and isinstance(_req_also_ov, list):
            _tell.required_also = [
                (_item[0], tuple(int(v) for v in _item[1]))
                for _item in _req_also_ov
                if isinstance(_item, list) and len(_item) >= 2 and len(_item[1]) >= 4
            ]
            _tell.required_also_templates = [None] * len(_tell.required_also)
        if _and_thresh_ov and isinstance(_and_thresh_ov, list):
            _tell.required_also_thresh = [int(t) for t in _and_thresh_ov]
            while len(_tell.required_also_thresh) < len(_tell.required_also):
                _tell.required_also_thresh.append(170)
        # alt: list → apply; False → explicitly removed; None → key not in DB, keep default
        if isinstance(_alt_ov, list) and len(_alt_ov) >= 2 and _alt_ov[0]:
            _tell.alt_image_path = _alt_ov[0]
            _tell.alt_roi        = tuple(int(v) for v in _alt_ov[1])
        elif _alt_ov is False:
            _tell.alt_image_path = None
            _tell.alt_roi        = None
        if _alt_thresh_ov is not None:
            _tell.alt_binary_thresh = int(_alt_thresh_ov)
    # Re-load templates now that required_also / alt paths may have changed
    for _tell in detector._tells_by_screen.values():
        _tell.load()

    tracker   = SelectionTracker(purge_tight=args.purge_tight,
                                  switch2_language=switch2_language)
    laps      = LapTracker()
    coins     = CoinTracker()
    ts        = TimestampTracker()
    finish    = FinishDetector(scan_interval=0.0)
    mush      = MushroomTracker()
    minimap   = MinimapTracker()
    mm_rec    = MinimapRecorder()
    mm_player = MinimapPlayer()

    # ── IPC server ───────────────────────────────────────────────────────────
    from .ipc.broadcaster import EventBroadcaster
    ws_port = args.ws_port if args.ws_port is not None else 8765
    broadcaster = EventBroadcaster(port=ws_port)

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

    # ── Setup mode ───────────────────────────────────────────────────────────
    setup_mode = [not bool(settings.get("setup_complete", 0))]
    if setup_mode[0]:
        print("[Setup] First-time setup required — running in setup mode.")

    # ── Camera ───────────────────────────────────────────────────────────────
    # Set Windows timer resolution to 1ms for the lifetime of this process.
    # The default is 15.6ms, which makes cv2.waitKey(1) / Event.wait(0.001)
    # sleep for up to 15.6ms — the primary cause of variable input lag.
    import ctypes as _ctypes
    _ctypes.windll.winmm.timeBeginPeriod(1)

    configured_device = settings.get("camera_device", "") or None

    if setup_mode[0]:
        # First-time setup: don't open camera yet. It will be opened on demand
        # when the frontend sends open_camera from the Camera wizard step.
        cap = None
        print("[Setup] Camera deferred — waiting for open_camera command.")
    else:
        try:
            cap = build_camera_source(device_name=configured_device)
            print(f"Camera: {cap.width}x{cap.height} @ {cap.fps} fps"
                  + (f" (normalised to {_REF_W}x{_REF_H})"
                     if cap.width != _REF_W or cap.height != _REF_H else ""))
            ipc.emit(emit_camera_status(ok=True, width=_REF_W, height=_REF_H,
                                         device=getattr(cap, "device_name", "")))
        except Exception as _e:
            cap = None
            ipc.emit(emit_camera_status(ok=False, error=str(_e)))
            print(f"[Camera] Failed to open: {_e}")

    ipc.emit(emit_ready(
        version="dev" if args.no_ipc else "sidecar",
        setup_complete=not setup_mode[0],
        app_language=settings.get("app_language", "en_uk") or "en_uk",
        switch2_language=switch2_language,
    ))
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
            if msg.get("type") == "pause_camera":
                if cap is not None:
                    cap.release()
                    cap = None
                cam_paused[0] = True
                ipc.emit(emit_camera_paused())
            elif msg.get("type") == "open_camera":
                # Close any existing capture first (handles device changes)
                if cap is not None:
                    cap.release()
                    cap = None
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

        # Arm the deferred finish emit when finish is first detected.
        # The timestamp burst starts on this same frame, so total_time / splits
        # won't be ready yet — defer until ts.total_time is populated.
        if finish_just_detected and not _prev_finish and not _want_finish_emit:
            _want_finish_emit = True
            _finish_result    = finish_state.result
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

            key = (cap.waitKey(1) if cap is not None else cv2.waitKey(1)) & 0xFF
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
    if cap is not None:
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
    parser.add_argument("--no-display", action="store_true",
                        help="Suppress OpenCV window (headless mode for Tauri).")
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
